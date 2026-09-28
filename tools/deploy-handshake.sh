#!/bin/bash
# tools/deploy-handshake.sh — deploy-gap immunity handshake (PRD #1075 slice #1079).
#
# PROBLEM (forensic audit, 2026-08-01): Claude Code hooks are invoked via a
# FIXED path resolved from git-common-dir's parent (see .claude/settings.json:
# "${CLAUDE_PROJECT_DIR:-$(dirname "$(git rev-parse --git-common-dir)")}") —
# i.e. the ROOT checkout, regardless of which worktree a session lives in.
# If that ROOT checkout is stale (detached HEAD, wrong branch, or its working
# tree has drifted from what's committed), EVERY session silently runs stale
# hook code. The #879 hook consolidation shipped six weeks ago and never
# executed once for exactly this reason.
#
# WHAT THIS SCRIPT DOES: compares the RUNNING content (the literal bytes on
# disk at the ROOT checkout right now — what hooks actually execute) against
# the DEPLOYED content (what is committed on the branch the ROOT checkout's
# HEAD currently tracks). MATCH -> exit 0. MISMATCH, DETACHED HEAD, or a
# core.hooksPath other than .claude/pipeline/githooks -> LOUD banner + exit 1.
#
# MECHANISM (git-plumbing only, never mutates the ROOT's real index/refs):
#   - DEPLOYED hooks-dir hash:    git rev-parse <branch>:.claude/pipeline/hooks       (tree oid)
#   - DEPLOYED settings.json hash: git rev-parse <branch>:.claude/settings.json (blob oid)
#   - RUNNING settings.json hash: git hash-object -- .claude/settings.json  (blob oid,
#     literal on-disk bytes, independent of the real index)
#   - RUNNING hooks-dir hash:     built via a THROWAWAY index (GIT_INDEX_FILE
#     pointed at a scratch file) + `git write-tree --prefix=.claude/pipeline/hooks/` —
#     this stages the literal on-disk directory contents into a private index
#     and asks git for the tree oid of just that subdirectory, without ever
#     touching the ROOT repo's real .git/index or any ref. (Loose objects may
#     be written to the shared object database — content-addressed storage,
#     not a working-tree/ref mutation — the same side effect any `git show`/
#     `git diff` invocation can trigger.)
#
# USAGE:
#   bash tools/deploy-handshake.sh [start-dir]
#     start-dir defaults to ${CLAUDE_PROJECT_DIR:-$(pwd)} — mirrors the exact
#     resolution order used by .claude/settings.json's hook commands. Tests
#     pass a synthetic temp-repo path here to avoid depending on cwd.
#
#   bash tools/deploy-handshake.sh --self-test [start-dir]
#     CI-safe internal-consistency mode (see SELF-TEST CONTRACT below).
#
#   bash tools/deploy-handshake.sh --check-only [start-dir]
#     NON-MUTATING, NEVER-BLOCKING leg (PRD #1075 slice #1085): re-runs the
#     exact same LOCAL LEG comparison below (DRY — zero duplicated hash
#     logic) but always exits 0 and prints a machine-parseable two-line
#     result instead of the loud banner + exit 1:
#       STATUS: PASS|FAIL
#       detail: <one-line summary>
#     Consumed by dashboard/health.py's check_deploy_handshake() (DEPLOY-
#     HANDSHAKE health row) — a health-check subprocess call must never be
#     interpreted as "the shell command itself failed" just because the
#     underlying comparison found a mismatch.
#
# SELF-TEST CONTRACT (what CI can/cannot assert):
#   GitHub Actions checks out the PR ref in DETACHED HEAD by design — the
#   branch-vs-HEAD comparison this script performs for the "local leg" would
#   ALWAYS report a false "deploy gap" in CI (there is no meaningful "branch
#   the checkout tracks" concept for an ephemeral CI runner). CI can assert:
#   the script parses, .claude/pipeline/hooks/ exists and is non-empty, and
#   .claude/settings.json exists and is valid JSON. CI CANNOT assert the real
#   deploy-gap invariant (running vs deployed-branch content, or attached-vs-
#   detached HEAD) — that is exclusively the local/operator leg's job, run
#   against the real ROOT checkout (see tools/repair-topology.md).
set -uo pipefail

SELF_TEST=0
CHECK_ONLY=0
if [ "${1:-}" = "--self-test" ]; then
  SELF_TEST=1
  shift
elif [ "${1:-}" = "--check-only" ]; then
  CHECK_ONLY=1
  shift
fi

START_DIR="${1:-${CLAUDE_PROJECT_DIR:-$(pwd)}}"

# ---------------------------------------------------------------------------
# Resolve ROOT: the git-common-dir parent, i.e. the checkout hooks actually
# execute from (mirrors .claude/pipeline/hooks/lib-root.sh + settings.json's own
# resolution order).
#
# NOTE (MSYS_NO_PATHCONV trap, #1091): START_DIR may be MSYS-form (e.g.
# "/f/project_claude", from bash's own `pwd`). Passing that string as a
# `git -C <path>` ARGUMENT relies on Git Bash's MSYS runtime auto-converting
# it to a real Windows path before spawning git.exe — a conversion that
# MSYS_NO_PATHCONV=1 (this repo's own mandated env for every git invocation)
# explicitly disables, so git.exe receives the literal "/f/..." and fails.
# `cd` is a bash BUILTIN, not a spawned child process, so it is never subject
# to that argv conversion step; it resolves MSYS-form and native-form paths
# alike via bash's own internal path handling, then git runs with the
# process's real (already-chdir'd) cwd -- no path argument involved at all.
# ---------------------------------------------------------------------------
COMMON=$(cd "$START_DIR" 2>/dev/null && git rev-parse --path-format=absolute --git-common-dir 2>/dev/null)
if [ -z "$COMMON" ]; then
  echo "ERROR: deploy-handshake: '$START_DIR' is not inside a git repository" >&2
  exit 1
fi
ROOT=$(dirname "$COMMON")

# ---------------------------------------------------------------------------
# SELF-TEST MODE (CI-safe internal consistency only — see contract above).
# ---------------------------------------------------------------------------
if [ "$SELF_TEST" -eq 1 ]; then
  FAIL=0
  if [ ! -d "$ROOT/.claude/pipeline/hooks" ] || [ -z "$(ls -A "$ROOT/.claude/pipeline/hooks" 2>/dev/null)" ]; then
    echo "FAIL: deploy-handshake --self-test: .claude/pipeline/hooks/ missing or empty at $ROOT" >&2
    FAIL=1
  fi
  if [ ! -f "$ROOT/.claude/settings.json" ]; then
    echo "FAIL: deploy-handshake --self-test: .claude/settings.json missing at $ROOT" >&2
    FAIL=1
  elif command -v python3 >/dev/null 2>&1; then
    if ! python3 -m json.tool "$ROOT/.claude/settings.json" >/dev/null 2>&1; then
      echo "FAIL: deploy-handshake --self-test: .claude/settings.json is not valid JSON" >&2
      FAIL=1
    fi
  fi
  if [ ! -d "$ROOT/.claude/pipeline/githooks" ]; then
    echo "FAIL: deploy-handshake --self-test: .claude/pipeline/githooks/ directory missing at $ROOT" >&2
    FAIL=1
  fi
  if [ "$FAIL" -eq 0 ]; then
    echo "PASS: deploy-handshake --self-test: internal consistency OK (branch/HEAD-topology checks are the local-leg's job, not CI's — see script header)"
    exit 0
  fi
  exit 1
fi

# ---------------------------------------------------------------------------
# LOCAL LEG: full deploy-gap comparison.
# ---------------------------------------------------------------------------
BANNER_LINES=()

BRANCH=$(git -C "$ROOT" symbolic-ref --short HEAD 2>/dev/null)
if [ -z "$BRANCH" ]; then
  BANNER_LINES+=("Root checkout HEAD is DETACHED — this IS the deploy-gap state.")
  BANNER_LINES+=("There is no branch to compare running content against.")
fi

HOOKS_PATH=$(git -C "$ROOT" config --get core.hooksPath 2>/dev/null || echo "__unset__")
# Compare PATH IDENTITY, not string equality (#1093 flapper): .claude/pipeline/githooks/install.sh
# may write an ABSOLUTE hooksPath (e.g. "F:\project_claude\.claude/pipeline/githooks" or
# "/f/project_claude/.claude/pipeline/githooks") that resolves to the SAME directory as the
# documented relative ".claude/pipeline/githooks" — a bare string compare false-flags that as
# a deploy-gap even though the content and the resolved directory match.
# Resolve both sides to a real, symlink-free absolute path via `cd ... && pwd -P`
# before comparing; only report a genuine mismatch (unset OR resolves elsewhere).
HOOKS_PATH_RESOLVED=""
if [ "$HOOKS_PATH" != "__unset__" ]; then
  case "$HOOKS_PATH" in
    /*|[A-Za-z]:*) HOOKS_PATH_CANDIDATE="$HOOKS_PATH" ;;   # already absolute
    *)             HOOKS_PATH_CANDIDATE="$ROOT/$HOOKS_PATH" ;;  # relative -> resolve vs ROOT
  esac
  HOOKS_PATH_RESOLVED=$(cd "$HOOKS_PATH_CANDIDATE" 2>/dev/null && pwd -P)
fi
EXPECTED_HOOKS_PATH_RESOLVED=$(cd "$ROOT/.claude/pipeline/githooks" 2>/dev/null && pwd -P)
if [ -z "$HOOKS_PATH_RESOLVED" ] || [ -z "$EXPECTED_HOOKS_PATH_RESOLVED" ] || \
   [ "$HOOKS_PATH_RESOLVED" != "$EXPECTED_HOOKS_PATH_RESOLVED" ]; then
  BANNER_LINES+=("core.hooksPath is '$HOOKS_PATH' (resolved: '${HOOKS_PATH_RESOLVED:-<unresolvable>}'), expected to resolve to '$ROOT/.claude/pipeline/githooks' (resolved: '${EXPECTED_HOOKS_PATH_RESOLVED:-<unresolvable>}').")
fi

if [ -n "$BRANCH" ]; then
  DEPLOYED_HOOKS_HASH=$(git -C "$ROOT" rev-parse "${BRANCH}:.claude/pipeline/hooks" 2>/dev/null)
  DEPLOYED_SETTINGS_HASH=$(git -C "$ROOT" rev-parse "${BRANCH}:.claude/settings.json" 2>/dev/null)

  RUNNING_SETTINGS_HASH=$(git -C "$ROOT" hash-object -- ".claude/settings.json" 2>/dev/null)

  TMP_INDEX="$(mktemp -u)"
  GIT_INDEX_FILE="$TMP_INDEX" git -C "$ROOT" add -A -- .claude/pipeline/hooks 2>/dev/null
  RUNNING_HOOKS_HASH=$(GIT_INDEX_FILE="$TMP_INDEX" git -C "$ROOT" write-tree --prefix=.claude/pipeline/hooks/ 2>/dev/null)
  rm -f "$TMP_INDEX"

  if [ -z "$DEPLOYED_HOOKS_HASH" ] || [ -z "$RUNNING_HOOKS_HASH" ] || \
     [ "$DEPLOYED_HOOKS_HASH" != "$RUNNING_HOOKS_HASH" ]; then
    BANNER_LINES+=(".claude/pipeline/hooks/ content-hash MISMATCH — running=${RUNNING_HOOKS_HASH:-<none>} deployed(${BRANCH})=${DEPLOYED_HOOKS_HASH:-<none>}")
  fi

  if [ -z "$DEPLOYED_SETTINGS_HASH" ] || [ -z "$RUNNING_SETTINGS_HASH" ] || \
     [ "$DEPLOYED_SETTINGS_HASH" != "$RUNNING_SETTINGS_HASH" ]; then
    BANNER_LINES+=(".claude/settings.json content-hash MISMATCH — running=${RUNNING_SETTINGS_HASH:-<none>} deployed(${BRANCH})=${DEPLOYED_SETTINGS_HASH:-<none>}")
  fi
fi

# ---------------------------------------------------------------------------
# --check-only: NON-MUTATING, NEVER-BLOCKING leg (PRD #1075 slice #1085).
# Reuses the exact BANNER_LINES computed above (DRY) but always exits 0 and
# prints a machine-parseable STATUS:/detail: pair instead of the loud banner
# + exit 1 — dashboard/health.py's check_deploy_handshake() shells to this.
# ---------------------------------------------------------------------------
if [ "$CHECK_ONLY" -eq 1 ]; then
  if [ "${#BANNER_LINES[@]}" -gt 0 ]; then
    echo "STATUS: FAIL"
    (IFS='; '; echo "detail: ${BANNER_LINES[*]}")
  else
    echo "STATUS: PASS"
    echo "detail: branch=$BRANCH hooksPath=$HOOKS_PATH root=$ROOT"
  fi
  exit 0
fi

if [ "${#BANNER_LINES[@]}" -gt 0 ]; then
  echo "================================================================" >&2
  echo "                    DEPLOY-GAP DETECTED" >&2
  echo "================================================================" >&2
  echo "The hooks/settings that ACTUALLY EXECUTE (resolved from" >&2
  echo "git-common-dir: $ROOT) do not match what this repo's topology" >&2
  echo "promises. Every Claude Code session may be running STALE hook" >&2
  echo "code right now." >&2
  echo "" >&2
  for line in "${BANNER_LINES[@]}"; do
    echo "  - $line" >&2
  done
  echo "" >&2
  echo "Repair procedure: tools/repair-topology.md (operator-executed only;" >&2
  echo "no dispatched agent may run it — it mutates the root repo)." >&2
  echo "================================================================" >&2
  exit 1
fi

echo "deploy-handshake: OK (branch=$BRANCH hooksPath=$HOOKS_PATH root=$ROOT)"
exit 0
