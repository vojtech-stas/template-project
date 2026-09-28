#!/bin/bash
# PreToolUse(Edit|MultiEdit|Write) hook — extended per ADR-0028 with spec-gate;
# retrofitted per ADR-0057 D1/D2 to parse FULL stdin via python3 (no head -c
# truncation), gate on extracted tool_input.file_path field (not raw-stdin
# substring), emit attempt beacon BEFORE parse, and emit ERROR beacon with
# session_id on parser failure (fail-open + fail-loud).
#
# Layered behavior (in order):
#  1. Read full stdin once into variable (no head -c cap — ADR-0057 D1c).
#  2. Emit attempt beacon — pure bash, before any parsing (ADR-0057 D1a).
#  2b. Arm the single-terminal EXIT trap (ADR-0083 D1/D2): every one of the seven
#      exit sites below ends in exactly one terminal beacon; ASK/DENY are
#      completions (`status:"ok"` + additive `outcome`), never forged crashes.
#  3. Parse stdin via python3: extract tool_input.file_path + session_id + agent_id.
#     On parse failure: emit ERROR beacon w/ session_id, exit 0 (ADR-0057 D1b/D2).
#  4. Subagent context skip (payload `agent_id` non-empty, ADR-0091 D1) — exit 0;
#     subagents ARE the PR pipeline.
#  5. Allowlist on extracted file_path field (ADR-0057 D1c — field-based, not raw substring).
#     Paths under tool-results / .claude/projects / .claude/logs → exit 0.
#  6. jq-missing fallback — emit "ask" (cannot build JSON response without jq).
#  7. Tracked-file check; non-tracked files → exit 0.
#  8. Spec-gate (ADR-0028 D1+D2, PRESERVED UNCHANGED): for main-agent edits to tracked files,
#     parse branch and verify an in-flight PRD/slice issue exists. DENY when no matching
#     issue / no matching branch pattern / issue closed. Fall through to rule-#10 ask when
#     issue exists+open. Soft-degrades to ask if `gh` is unavailable OR returns a network
#     error (ADR-0028 D4).
#  9. Rule-#10 escalate-to-ask fallback (preserved from ADR-0023 D3).

set -uo pipefail

cd "${CLAUDE_PROJECT_DIR:-.}" 2>/dev/null || true

# Resolve main root + LOG_DIR via lib-root.sh (PRD #668 beacon unification).
SCRIPT_DIR="$(dirname "${BASH_SOURCE[0]}")"
# shellcheck source=lib-root.sh
source "$SCRIPT_DIR/lib-root.sh"

# Step 1: read FULL stdin once — no head -c truncation (ADR-0057 D1c).
STDIN_RAW=$(cat)

# Step 2: emit attempt beacon BEFORE any parsing — pure bash, no parse dependency
# (ADR-0057 D1a: attempt-before-parse ordering).
_BEACON_DIR="${WORKFLOW_LOG_DIR:-$LOG_DIR}"
mkdir -p "$_BEACON_DIR" 2>/dev/null || true
printf '{"hook":"pre-tool-edit","status":"attempt","ts":"%s"}\n' \
  "$(date -u -Iseconds 2>/dev/null)" \
  >> "$_BEACON_DIR/hook-fires.jsonl" 2>/dev/null || true

# Step 2b: terminal-beacon contract (ADR-0083 D1/D2).
# This script has seven exit sites. Rather than bolting an emit onto each one —
# which is how sites go silent again the next time one is added — a single EXIT
# trap covers all of them, guarded so exactly one terminal is ever written.
# ADR-0083 D2: `status` is the closed lifecycle set {attempt, ok, ERROR}. A
# deliberate policy decision (ASK/DENY) is a COMPLETION, not a crash, so it
# exits `status:"ok"` and records the decision additively in `outcome`.
_TERMINAL_EMITTED=0
_OUTCOME=""

_emit_terminal_beacon() {
  [ "$_TERMINAL_EMITTED" = "1" ] && return 0
  _TERMINAL_EMITTED=1
  local extra=""
  [ -n "$_OUTCOME" ] && extra=",\"outcome\":\"$_OUTCOME\""
  printf '{"hook":"pre-tool-edit","status":"ok","ts":"%s","session_id":"%s"%s}\n' \
    "$(date -u -Iseconds 2>/dev/null)" "${_SID:-}" "$extra" \
    >> "$_BEACON_DIR/hook-fires.jsonl" 2>/dev/null || true
}
trap _emit_terminal_beacon EXIT

REASON='Main-agent write to tracked file — CLAUDE.md rule #10 says flow through PR pipeline. Confirm if this is an I3 trivial-lane edit (≤10 LoC, `trivial` label, branch `hotfix/<issue#>-…`); cancel and use /to-prd or /ship otherwise.'

emit_ask() {
  _OUTCOME="ask"
  if command -v jq >/dev/null 2>&1; then
    jq -cn --arg r "$REASON" '{hookSpecificOutput: {hookEventName: "PreToolUse", permissionDecision: "ask", permissionDecisionReason: $r}}'
  else
    ESC=$(printf '%s' "$REASON" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g')
    printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"ask","permissionDecisionReason":"%s"}}\n' "$ESC"
  fi
  exit 0
}

# ADR-0028 D2: spec-gate denies tracked-file edits when branch lacks an in-flight PRD/slice issue.
emit_deny() {
  local reason="$1"
  _OUTCOME="deny"
  if command -v jq >/dev/null 2>&1; then
    jq -cn --arg r "$reason" '{hookSpecificOutput: {hookEventName: "PreToolUse", permissionDecision: "deny", permissionDecisionReason: $r}}'
  else
    ESC=$(printf '%s' "$reason" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g')
    printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"%s"}}\n' "$ESC"
  fi
  exit 0
}

# Step 3: parse FULL stdin with python3 — extract file_path + session_id + subagent flag.
# ADR-0057 D1c: gating decisions match extracted JSON fields, never raw-stdin text.
# ADR-0057 D1b/D2: on parser failure emit ERROR beacon with session_id and exit 0 (fail-open).
_PY_OUT=$(export _PTE_STDIN="$STDIN_RAW"; python3 - <<'PYEOF' 2>/dev/null
import sys, os, json, re
raw = os.environ.get("_PTE_STDIN", "")
try:
    payload = json.loads(raw)
    fp  = (payload.get("tool_input") or {}).get("file_path") or ""
    sid = payload.get("session_id") or ""
    # ADR-0091 D1: a non-empty `agent_id` marks a hook fired inside a subagent.
    sub = bool(str(payload.get("agent_id") or "").strip())
    print(json.dumps({"ok": True, "file_path": fp, "session_id": sid, "subagent": sub}))
except Exception as exc:
    # Best-effort session_id extraction even on corrupt JSON.
    m = re.search(r'"session_id"\s*:\s*"([^"]*)"', raw)
    sid = m.group(1) if m else ""
    print(json.dumps({"ok": False, "file_path": "", "session_id": sid, "error": str(exc)[:200]}))
PYEOF
)

_PY_OK=$(printf '%s' "$_PY_OUT" | python3 -c "import sys,json; d=json.load(sys.stdin); print('1' if d.get('ok') else '0')" 2>/dev/null || echo "0")
FP=$(printf '%s' "$_PY_OUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('file_path',''))" 2>/dev/null || echo "")
_SID=$(printf '%s' "$_PY_OUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('session_id',''))" 2>/dev/null || echo "")
_SUBAGENT=$(printf '%s' "$_PY_OUT" | python3 -c "import sys,json; print('1' if json.load(sys.stdin).get('subagent') else '')" 2>/dev/null || echo "")
# Bash-level session_id fallback: if python3 failed entirely _SID is empty; extract via sed.
if [ -z "$_SID" ]; then
  _SID=$(printf '%s' "$STDIN_RAW" | sed -n 's/.*"session_id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1 2>/dev/null || echo "")
fi

if [ "$_PY_OK" != "1" ]; then
  # ADR-0057 D1b: parser failure → ERROR beacon with session_id, then fail-open.
  _ERR_MSG=$(printf '%s' "$_PY_OUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('error','parse-failed'))" 2>/dev/null || echo "parse-failed")
  # This IS the terminal for this path — claim it so the EXIT trap does not
  # append a second one (ADR-0083 D1: exactly one terminal per fire).
  _TERMINAL_EMITTED=1
  printf '{"hook":"pre-tool-edit","status":"ERROR","ts":"%s","session_id":"%s","reason":"%s"}\n' \
    "$(date -u -Iseconds 2>/dev/null)" "$_SID" "$_ERR_MSG" \
    >> "$_BEACON_DIR/hook-fires.jsonl" 2>/dev/null || true
  exit 0
fi

# Step 4: subagent context — allow (no emit). Subagents ARE the PR pipeline per ADR-0023 D3 step 1.
# The discriminator is the payload's `agent_id` (ADR-0091 D1); no environment variable carries it.
if [ "$_SUBAGENT" = "1" ]; then
  exit 0
fi

# Step 5: allowlist on EXTRACTED file_path (ADR-0057 D1c — field-based, not raw substring).
# Handles both POSIX (/) and Windows (\) path separators.
case "$FP" in
  *tool-results*|*.claude/projects*|*.claude/logs*|*.claude\\projects*|*.claude\\logs*)
    exit 0
    ;;
esac

# Step 6: jq-missing fallback — cannot build JSON response without jq.
if ! command -v jq >/dev/null 2>&1; then
  emit_ask
fi

[ -z "$FP" ] && exit 0

# Step 7: tracked-file check; if NOT tracked → exit cleanly (no spec-gate, no ask).
REL="${FP#"$PWD"/}"
if ! git ls-files --error-unmatch -- "$REL" >/dev/null 2>&1 && ! git ls-files --error-unmatch -- "$FP" >/dev/null 2>&1; then
  exit 0
fi

# Step 8: ADR-0028 D1+D2: spec-gate runs BEFORE rule-#10 ask fallback for tracked-file edits.
# Soft-degrades per D4: if `gh` missing OR `gh issue view` returns a network error, fall through to ask.
if command -v gh >/dev/null 2>&1; then
  BRANCH="${BRANCH:-$(git rev-parse --abbrev-ref HEAD 2>/dev/null)}"
  N=""
  if printf '%s' "$BRANCH" | grep -qE '^(feat|fix|docs|chore|refactor|test|perf|style|build|ci)/[0-9]+-'; then
    N=$(printf '%s' "$BRANCH" | sed -E 's|^[a-z]+/([0-9]+)-.*|\1|')
  elif printf '%s' "$BRANCH" | grep -qE '^hotfix/[0-9]+-'; then
    # ADR-0028 D3 trivial-lane (I3) carveout — hotfix branches require an audit-trail issue
    # but skip the slice/prd label requirement (reviewer enforces `trivial` label at PR time).
    N=$(printf '%s' "$BRANCH" | sed -E 's|^hotfix/([0-9]+)-.*|\1|')
  else
    emit_deny "Branch '$BRANCH' does not match <type>/<issue#>-... or hotfix/<issue#>-... pattern. Run /grill-me + /ship to create a PRD/slice before editing tracked files."
  fi

  # Run gh issue view, capturing stderr to distinguish network errors from missing-issue errors.
  GH_OUT=$(gh issue view "$N" --json state --jq .state 2>/tmp/gh-spec-gate-err.$$ )
  GH_RC=$?
  GH_ERR=$(cat /tmp/gh-spec-gate-err.$$ 2>/dev/null)
  rm -f /tmp/gh-spec-gate-err.$$ 2>/dev/null

  if [ $GH_RC -ne 0 ]; then
    # Distinguish missing-issue (deny) from network error / auth error (soft-degrade to ask).
    if printf '%s' "$GH_ERR" | grep -qiE 'could not resolve|not found|no such issue|graphql.*resolve'; then
      emit_deny "Issue #$N doesn't exist; branch references a stale or invalid issue number. Run /grill-me + /ship for a fresh PRD/slice."
    fi
    # ADR-0028 D4: network / auth / rate-limit errors → soft-degrade to ask (defense-in-depth).
    emit_ask
  fi

  if [ "$GH_OUT" = "CLOSED" ]; then
    emit_deny "Issue #$N is closed; current branch references a closed issue. Run /grill-me + /ship for a fresh PRD/slice."
  fi
  # Issue exists + open — fall through to existing rule-#10 ask.
fi

# Step 9: Rule-#10 escalate-to-ask fallback (ADR-0023 D3) — preserved for tracked-file edits when
# spec-gate either passed (issue open) or soft-degraded (gh unavailable).
emit_ask
