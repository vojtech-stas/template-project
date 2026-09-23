#!/bin/bash
# record-green.sh — verify develop is genuinely green, then record develop_green.
#
# USAGE:
#   bash tools/record-green.sh [--dry-run] [<sha>]
#
# What it does (slice #1032 / PRD #1031; ci-trust fast path per #1161;
# explicit-sha certification per #1188 / root-cause #1183):
#   1. Resolves the sha to certify:
#      - EXPLICIT <sha> argument given (e.g. the confirmed merge oid a caller
#        like tools/pipe/pr-merge already holds from the GitHub API) -> use it
#        directly, no git call at all. This is the primary path: it kills the
#        stale-local-ref class at the root rather than papering over it.
#      - NO <sha> argument given (standalone/manual invocation) -> fetch
#        `origin develop` FIRST, then resolve `git rev-parse origin/develop`
#        (falls back to local `develop` if origin resolution fails). A stale
#        local remote-tracking ref can never be certified on this path again.
#   2. Fetches the GitHub 'ci' check conclusion for the EXACT sha being certified.
#      - conclusion == 'pass'        -> the recorded CI run (tools/ci-checks.sh,
#        which itself runs `pytest tests/` as a required sub-check — REG-001)
#        ALREADY proves the suite green for this sha. Trust it: skip the local
#        pytest re-run entirely (kills the #1122 duplication class at the root).
#      - conclusion == 'unavailable' (no recorded ci run for this sha — fresh
#        clone / un-pushed sha) -> fresh-clone honesty preserved: fall back to
#        a REAL local `python -m pytest tests/` run.
#      - conclusion == 'fail' | 'pending' | anything else -> refuse immediately
#        (no pytest fallback rescues an explicit fail/incomplete CI run).
#   3. On green (either the ci-trust fast path or the local-pytest fallback):
#      appends exactly one {"v":2,"event":"develop_green",...} line to the
#      CANONICAL telemetry log (resolved via git-common-dir, not repo root)
#      so worktree runs write the shared root log (#1021 lesson).
#   4. On refusal: prints reason to stderr, writes NOTHING, exits 1.
#      This is the core safety property: NEVER write a false green.
#
# --dry-run flag:
#   Runs the same verification + prints the event it WOULD write (or the
#   failure reason), but does NOT append to the log. Exits 0 if green, 1 if not.
#
# Test injection (env vars, consumed only when set):
#   RECORD_GREEN_CI_STATUS   — when set, treat this value as the CI status
#                              instead of calling _fetch_github_ci_conclusion.
#                              Accepted: pass | fail | pending | unavailable
#                              (takes priority over RECORD_GREEN_GH_CMD).
#   RECORD_GREEN_GH_CMD      — LEGACY: command run instead of 'gh'; must print
#                              the ci conclusion string to stdout ("success").
#                              Only used when RECORD_GREEN_CI_STATUS is unset.
#   RECORD_GREEN_PYTEST_CMD  — command run instead of 'python -m pytest tests/ -q';
#                              must exit 0 for pass, non-zero for fail. Only
#                              invoked on the no-recorded-ci-run (unavailable)
#                              fallback path — NEVER on the ci=pass fast path.
#   RECORD_GREEN_TEST_LOG_PATH — when set, write to this path instead of the
#                                canonical git-common-dir log (test isolation).
#
# Run by the ORCHESTRATOR after develop PRs are merged and before promoting.
# Requires: git, gh (GitHub CLI), python -m pytest (only on the fallback path).

set -euo pipefail

# Resolve the configured integration branch (ADR-0089 D1), located relative
# to THIS script's own path (S1-c) — never via $REPO_ROOT or
# `git rev-parse --show-toplevel` of the cwd repo. This script only ever
# certifies the integration branch (never the release branch), so only one
# role is resolved here. `pwd -W` (falls back to plain `pwd` where
# unsupported) gives a native-form path so the python3 call below resolves
# correctly even under MSYS_NO_PATHCONV=1.
_RG_TOOLS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -W 2>/dev/null || pwd)"
INTEGRATION_BRANCH="$(python3 "$_RG_TOOLS_DIR/pipeline_config.py" integration)" || {
  echo "ERROR: record-green.sh: cannot resolve the integration branch (tools/pipeline_config.py failed)" >&2
  exit 1
}

DRY_RUN=0
EXPLICIT_SHA=""
for arg in "$@"; do
  case "$arg" in
    --dry-run)
      DRY_RUN=1
      ;;
    --*)
      echo "ERROR: unknown argument: $arg" >&2
      exit 1
      ;;
    *)
      if [ -n "$EXPLICIT_SHA" ]; then
        echo "ERROR: unexpected extra positional argument: $arg (sha already set to $EXPLICIT_SHA)" >&2
        exit 1
      fi
      EXPLICIT_SHA="$arg"
      ;;
  esac
done

# --- 1. Resolve the sha to certify (#1188: explicit sha wins outright; the
# no-arg fallback fetches origin develop FIRST so a stale local
# remote-tracking ref can never be certified) ---
if [ -n "$EXPLICIT_SHA" ]; then
  DEV_SHA="$EXPLICIT_SHA"
  echo "INFO: certifying caller-provided sha = $DEV_SHA (e.g. a confirmed merge oid)"
else
  echo "INFO: no explicit sha provided — fetching origin $INTEGRATION_BRANCH before resolving fallback ref"
  if git fetch origin "$INTEGRATION_BRANCH" --quiet 2>/dev/null; then
    echo "INFO: fetched origin $INTEGRATION_BRANCH"
  else
    echo "WARN: git fetch origin $INTEGRATION_BRANCH failed — falling back to existing local ref (may be stale)" >&2
  fi
  DEV_SHA="$(git rev-parse "origin/$INTEGRATION_BRANCH" 2>/dev/null || git rev-parse "$INTEGRATION_BRANCH" 2>/dev/null)" || {
    echo "ERROR: cannot resolve $INTEGRATION_BRANCH HEAD — fetch origin first" >&2
    exit 1
  }
  echo "INFO: $INTEGRATION_BRANCH HEAD (post-fetch) = $DEV_SHA"
fi

# --- 2a. Verify GitHub ci conclusion via PR-mergeCommit lookup ---
# Squash-merge commits (every develop HEAD in the two-tier workflow) have NO
# check-runs attached to them — GitHub CI fires on the PR head, not the squash
# result.  We must find the PR whose mergeCommit.oid == develop HEAD and read
# THAT PR's ci check (the same strategy dashboard/health.py uses).
echo "INFO: checking GitHub ci conclusion for $DEV_SHA ..."
CI_DETAIL=""
if [ -n "${RECORD_GREEN_CI_STATUS+x}" ]; then
  # Test injection (highest priority): RECORD_GREEN_CI_STATUS overrides everything
  # when the variable is SET (even to empty — empty means refuse, not skip).
  CI_STATUS="${RECORD_GREEN_CI_STATUS:-}"
  CI_DETAIL="RECORD_GREEN_CI_STATUS (test injection) = '$CI_STATUS'"
  echo "INFO: CI status from RECORD_GREEN_CI_STATUS (test injection) = '$CI_STATUS'"
elif [ -n "${RECORD_GREEN_GH_CMD:-}" ]; then
  # Legacy test injection: RECORD_GREEN_GH_CMD echoes a raw conclusion string.
  # Map "success" → "pass" for consistency; anything else → refuse.
  RAW_CONCLUSION="$(eval "$RECORD_GREEN_GH_CMD" 2>/dev/null || true)"
  if [ "$RAW_CONCLUSION" = "success" ]; then
    CI_STATUS="pass"
  else
    CI_STATUS="$RAW_CONCLUSION"
  fi
  CI_DETAIL="RECORD_GREEN_GH_CMD (legacy injection) raw='$RAW_CONCLUSION'"
  echo "INFO: CI status from RECORD_GREEN_GH_CMD (legacy injection) = '$CI_STATUS'"
else
  # Real path: delegate to dashboard/health.py::_fetch_github_ci_conclusion().
  # It finds the merged PR whose mergeCommit.oid == the sha being certified
  # and reads THAT PR's ci check — works correctly for squash-merge commits.
  # Use git show-toplevel (not git-common-dir) for the dashboard import so
  # worktree runs load the worktree's own health.py (which has the function),
  # not the root repo's potentially-older copy.
  #
  # sha=$DEV_SHA (ADR-0079 D2 / #1192 fix): DEV_SHA was ALREADY resolved
  # above (step 1) — either the caller's explicit sha or this script's own
  # fetch-then-rev-parse fallback. Passing it through here means
  # _fetch_github_ci_conclusion() uses it DIRECTLY instead of re-deriving
  # its own sha internally via a SECOND `git rev-parse origin/develop`
  # call. Without this, the two independent derivations could disagree if
  # origin/develop moves between them (e.g. a different PR merges in the
  # interim) — the exact live incident (#1192) where record-green certified
  # the correct sha but the CI-evidence line cited an unrelated PR's run.
  SCRIPT_REPO_ROOT="$(git rev-parse --show-toplevel)"
  COMMON_LOGROOT="$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)")"
  CI_PY_OUT="$(python3 -c "
import sys
sys.path.insert(0, '$SCRIPT_REPO_ROOT/dashboard')
from health import _fetch_github_ci_conclusion
status, detail = _fetch_github_ci_conclusion('$COMMON_LOGROOT', sha='$DEV_SHA')
print(status)
print(detail)
" 2>/dev/null || echo "unavailable")"
  CI_STATUS="$(printf '%s\n' "$CI_PY_OUT" | sed -n '1p')"
  CI_DETAIL="$(printf '%s\n' "$CI_PY_OUT" | sed -n '2p')"
  [ -z "$CI_STATUS" ] && CI_STATUS="unavailable"
  echo "INFO: CI status from PR-mergeCommit lookup = '$CI_STATUS' ($CI_DETAIL)"
fi

# --- 2b. ci-trust gate (#1161): trust a recorded GitHub ci=pass for this EXACT
# sha instead of re-running the full pytest suite locally.  tools/ci-checks.sh
# (what GitHub Actions runs as the `ci` check) already runs `pytest tests/` as
# a required sub-check (REG-001) — re-verifying it locally after GitHub already
# proved it is pure duplication (~25-35 min/slice measured, #1161).
#
#   pass        -> recorded CI run already proves the suite green; SKIP local
#                  pytest entirely.
#   unavailable -> no recorded ci run exists for this sha (fresh clone /
#                  un-pushed sha); fall back to a REAL local pytest run
#                  (fresh-clone honesty preserved).
#   fail | pending | anything else -> refuse immediately; NEVER rescue an
#                  explicit fail/incomplete CI run with a local pytest pass.
case "$CI_STATUS" in
  pass)
    echo "INFO: GitHub ci = pass for sha $DEV_SHA — proven by recorded CI run ($CI_DETAIL); skipping local pytest re-run (#1161)"
    TESTS_EVIDENCE="recorded GitHub ci=pass for sha $DEV_SHA ($CI_DETAIL) — tools/ci-checks.sh already ran pytest as a required check (REG-001); no local pytest re-run"
    ;;
  unavailable)
    echo "INFO: no recorded GitHub ci run for sha $DEV_SHA ('$CI_STATUS': $CI_DETAIL) — falling back to local pytest verification"
    echo "INFO: running pytest to verify tests are green ..."
    if [ -n "${RECORD_GREEN_PYTEST_CMD:-}" ]; then
      # Test injection: run the stub command.
      if ! eval "$RECORD_GREEN_PYTEST_CMD" >/dev/null 2>&1; then
        echo "ERROR: pytest (stub) exited non-zero — $INTEGRATION_BRANCH tests are NOT green" >&2
        echo "ERROR: refusing to record develop_green (no false-green)" >&2
        exit 1
      fi
    else
      # Real path: run the full test suite.
      REPO_ROOT="$(git rev-parse --show-toplevel)"
      if ! python -m pytest "$REPO_ROOT/tests/" -q --no-header --tb=short 2>&1; then
        echo "ERROR: pytest exited non-zero — $INTEGRATION_BRANCH tests are NOT green" >&2
        echo "ERROR: refusing to record develop_green (no false-green)" >&2
        exit 1
      fi
    fi
    echo "INFO: pytest green — OK (local fallback verification; no recorded ci run for this sha)"
    TESTS_EVIDENCE="local pytest fallback (no recorded GitHub ci run for sha $DEV_SHA: $CI_DETAIL)"
    ;;
  *)
    echo "ERROR: GitHub ci conclusion for $INTEGRATION_BRANCH HEAD is '$CI_STATUS' (need 'pass', or 'unavailable' for local fallback)" >&2
    echo "ERROR: $INTEGRATION_BRANCH is NOT green — refusing to record develop_green (no false-green)" >&2
    exit 1
    ;;
esac
echo "INFO: test-suite proof — $TESTS_EVIDENCE"

# --- 3. Build the event line ---
TS="$(date -u +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null \
  || python3 -c "from datetime import datetime,timezone; print(datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'))")"
SESSION_ID="${CLAUDE_SESSION_ID:-orchestrator}"
EVENT="{\"v\":2,\"ts\":\"$TS\",\"session_id\":\"$SESSION_ID\",\"src\":\"orchestrator\",\"event\":\"develop_green\",\"sha\":\"$DEV_SHA\"}"

if [ "$DRY_RUN" -eq 1 ]; then
  echo "INFO: --dry-run — would write:"
  echo "$EVENT"
  echo "INFO: --dry-run — nothing written"
  exit 0
fi

# --- 4. Resolve canonical log path via git-common-dir ---
# git-common-dir is the shared .git dir even in worktrees, so this always
# writes to the root repo's .claude/logs/ (not a worktree-local copy).
if [ -n "${RECORD_GREEN_TEST_LOG_PATH:-}" ]; then
  # Test isolation: write to the caller-specified path.
  EVENTS_LOG="$RECORD_GREEN_TEST_LOG_PATH"
else
  LOGROOT="$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)")"
  EVENTS_LOG="$LOGROOT/.claude/logs/workflow-events.jsonl"
fi

mkdir -p "$(dirname "$EVENTS_LOG")"

# --- 5. Append event ---
echo "$EVENT" >> "$EVENTS_LOG"
echo "INFO: develop_green event appended — sha=$DEV_SHA ts=$TS"
echo "INFO: log = $EVENTS_LOG"
