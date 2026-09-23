#!/bin/bash
# PreToolUse(Bash) hook — deny-guard for dangerous git ops and incident-backed pipeline bypasses.
# Covers ADR-0023 D4 (push-to-main) and the ADR-0076 D4 deny-guard funnel
# (PRD #1127 criteria 7a-7c, slice #1133): three raw forms below, each with
# a working sanctioned alternative; plus criterion 7d (slice #1135): an
# advisory-only warn (never deny) for `gh issue create --label slice|prd` --
# no sanctioned posting verb exists yet, so /to-issues and /to-prd legitimately
# invoke this raw form and the call must proceed. Criterion 7e (also slice
# #1135, no code here) is a pass-through demo, covered by
# tests/test_deny_guard_advisory_1135.py: every sanctioned `tools/pipe/*` verb
# form (plus the already-covered orchestrator-context tools/promote.sh) is
# untouched by any check below because none of their clause-head tokens are
# `gh`/`git`/a `tools/promote.sh` invocation -- their Bash-tool command line
# is `python tools/pipe/<verb> ...`, so no new allowlist code was needed.
#
# SINGLE-SPAWN CONSOLIDATION + OUTCOME BEACON (ADR-0079 D3, slice #1198):
# the eight sequential jq spawns the pre-consolidation script made on every
# allow-path fire (one tool_input.command extraction, two classifier-JSON
# shape-validity checks, five per-flag `jq -r` reads) are replaced by ONE
# python3 invocation (pre-tool-bash-classify.py, alongside this script) that
# reads the raw stdin payload directly, extracts the command, classifies it,
# decides deny/warn/allow, and prints at most two lines: the outcome marker
# and (for deny/warn) the compact JSON this hook relays to Claude Code. The
# classifier ALSO appends a second beacon line to hook-fires.jsonl carrying
# the additive `outcome` field (deny|warn|allow), making real deny history
# measurable for the first time. Decision logic, message text, and clause-
# anchoring behavior are byte-identical to the pre-consolidation version --
# only the spawn count changed. Target: allow-path median wall ≤350 ms (from
# measured ~652 ms).
#
# Reads tool-call JSON on stdin; inspects tool_input.command.
#
# INVOCATION-ANCHORED, NOT SUBSTRING (reviewer round-1 BLOCK on PR #1141,
# fixed here): every check below classifies the command via a python3 clause
# tokenizer (`classify()`, in pre-tool-bash-classify.py) rather than a
# whole-string grep. The raw command is split into "clauses" on unquoted
# shell separators (`;`, `&`, `&&`, `|`, `||`, newline) using `shlex.shlex(...,
# posix=True, punctuation_chars=True)` (quotes are honored — a quoted phrase
# collapses into ONE token, so it can never masquerade as an adjacent command
# word). Each clause is checked independently by its OWN first token(s)
# (skipping leading `VAR=val` env-prefixes) — a mention of a dangerous phrase
# inside an unrelated command's argument (a commit message, an echo, a grep
# pattern) never matches, and a dangerous clause in a compound command is
# never rescued by an unrelated mention in another clause. Full parsing of
# command-substitution subshells (`$(...)`, backticks) is intentionally out
# of scope (ADR-0076 D4 rabbit-hole discipline — see PR body); each clause is
# examined independently, so under-splitting only ever makes detection MORE
# conservative, never less.
# Denies (permissionDecision: "deny"):
#   - `git push` targeting main via ANY refspec form: plain `origin main`,
#     `HEAD:main`/`<src>:main`, `:main` (delete-remote-main), and
#     `refs/heads/main` (bare, or as a colon-form destination) — ADR-0023 D4 +
#     ADR-0076 D4 7c, closing the refspec evasion of the original
#     origin-main-only regex. Refspec matching is scoped to tokens WITHIN
#     the same `git push` clause only.
#   - raw `gh pr merge` (any flags, as the actual invocation — clause's first
#     three tokens literally `gh`, `pr`, `merge`) — ADR-0076 D4 7a, upgraded
#     from the advisory warn added in PRD #1075 slice #1086. Names
#     `tools/pipe/pr-merge` as the sanctioned wrapper. The wrapper's own
#     internal `subprocess.run(["gh","pr","merge",...])` call is a
#     Python-internal subprocess invisible to this hook — Claude Code's
#     PreToolUse(Bash) hook only ever sees the literal Bash-tool command line
#     (`python tools/pipe/pr-merge <pr>`), whose first clause-token is
#     `python`, never `gh`, so the sanctioned wrapper invocation is never
#     mis-denied.
#   - `tools/promote.sh` invoked (as the clause's actual command, not a
#     mention) from a SUBAGENT context — ADR-0076 D4 7b, using the same
#     discriminator `pre-tool-edit.sh` uses for its subagent-context skip:
#     a non-empty `agent_id` in the hook's stdin payload (ADR-0091 D1); the
#     orchestrator's own (main-thread, no `agent_id`) invocation passes
#     through un-denied.
# Warns (systemMessage, NOT denied):
#   - `git commit ... -m ... WIP` (convention nudge; same clause-anchoring
#     applied per rule #19 class-sweep).
#   - `gh issue create --label slice` or `--label prd` (as the actual
#     invocation, clause-anchored) -- ADR-0076 D4 7d advisory nudge naming
#     /to-issues and /to-prd. Deliberately NOT a deny: no sanctioned posting
#     verb exists yet and /to-issues/to-prd legitimately run this exact raw
#     form in main-agent context (a hard deny would break the sanctioned
#     pipeline itself -- the #1038 untested-gate class). The #918
#     hand-created-slice class keeps its deterministic lane via CI CHECK 19
#     (slicer-provenance) + the SLICE-VS-PR reconciler.
# Soft-degrades if `python3` missing → ERROR beacon + exit 0 (cannot
# classify; let Claude's built-in classifier handle). `jq` is no longer a
# hard dependency for the allow/deny/warn decision path (slice #1198) --
# only the fail-open contract below relies on plain bash + python3.
set -uo pipefail

# Resolve main root + LOG_DIR via lib-root.sh (PRD #668 beacon unification).
SCRIPT_DIR="$(dirname "${BASH_SOURCE[0]}")"
# shellcheck source=lib-root.sh
source "$SCRIPT_DIR/lib-root.sh"

# WORKFLOW_LOG_DIR sandbox seam (mirrors pre-tool-edit.sh / stop-reviewer-gate.sh
# / log-tool-event.sh): only mkdir when a test harness actually overrides the
# beacon directory -- the production LOG_DIR is already created by lib-root.sh,
# so the hot path never pays for a redundant mkdir spawn.
if [ -n "${WORKFLOW_LOG_DIR:-}" ] && [ ! -d "${WORKFLOW_LOG_DIR}" ]; then
  mkdir -p "$WORKFLOW_LOG_DIR" 2>/dev/null || true
fi
_BEACON_DIR="${WORKFLOW_LOG_DIR:-$LOG_DIR}"

# ATTEMPT beacon FIRST — before ANY parsing (HOK-008).
printf '{"hook":"pre-tool-bash","status":"attempt","ts":"%s"}\n' "$(date -u -Iseconds 2>/dev/null)" >> "$_BEACON_DIR/hook-fires.jsonl" 2>/dev/null || true

emit_error_beacon() {
  local reason="$1"
  printf '{"hook":"pre-tool-bash","status":"ERROR","ts":"%s","reason":"%s"}\n' \
    "$(date -u -Iseconds 2>/dev/null)" "$reason" \
    >> "$_BEACON_DIR/hook-fires.jsonl" 2>/dev/null || true
}

if ! command -v python3 >/dev/null 2>&1; then
  emit_error_beacon 'python3 unavailable -- single-spawn classification skipped, fail-open'
  exit 0
fi

# Single python3 invocation: reads the ORIGINAL stdin payload directly (no
# pre-read into a bash variable, no heredoc — the classifier is a companion
# file, so real stdin is never consumed by anything else), extracts
# tool_input.command, classifies it, decides deny/warn/allow, writes the
# outcome beacon, and prints the outcome marker (+decision JSON for
# deny/warn) — ADR-0079 D3 / slice #1198.
_PTB_OUT=$(_PTB_BEACON_DIR="$_BEACON_DIR" python3 "$SCRIPT_DIR/pre-tool-bash-classify.py" 2>/dev/null)
_PTB_RC=$?

if [ $_PTB_RC -ne 0 ]; then
  emit_error_beacon "classifier failed (rc=$_PTB_RC) -- fail-open"
  exit 0
fi

# Pure bash string split on the first newline — no head/tail subprocess
# spawn on the hot path. `${_PTB_OUT%%$'\n'*}` removes the first newline
# onward (yields line 1); `${_PTB_OUT#*$'\n'}` removes up through the first
# newline (yields everything after line 1). For a single-line "allow"
# payload the second form is unused (only referenced in the deny/warn arm).
# Trailing `\r` stripped defensively (Python's classifier reconfigures LF-only
# stdout, but a foreign python3 build could still emit CRLF on Windows).
_PTB_OUTCOME="${_PTB_OUT%%$'\n'*}"
_PTB_OUTCOME="${_PTB_OUTCOME%$'\r'}"

case "$_PTB_OUTCOME" in
  deny|warn)
    printf '%s\n' "${_PTB_OUT#*$'\n'}"
    ;;
  allow)
    : # allow decisions produce no stdout — call proceeds untouched.
    ;;
  *)
    emit_error_beacon "classifier produced unexpected outcome marker '$_PTB_OUTCOME' -- fail-open"
    ;;
esac

exit 0
