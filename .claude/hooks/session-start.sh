#!/bin/bash
# session-start.sh — deterministic read-only session context injection.
#
# Implements ADR-0068 D3 (session-start context injection) + ADR-0057 D4.
# NEVER invokes skills or subagents (rule #12's hard line).
#
# What it injects:
#   - Branch name + divergence vs origin/develop
#   - Recent commits (last 5)
#   - Open needs-human PRs/issues (I5 escalation surface)
#   - In-flight assigned slices
#   - Open PRs (recent 3)
#   - Open captured-queue depth
#   - jq / hooks warnings
#
# Graceful degradation: missing gh → one-line warning, never block.
# Emits session_context_injected event via the canonical logger pattern.
#
# Fail-loud beacon contract per ADR-0057 D1: beacons attempt/ok/error.
# Output capped: 60 lines / 6KB.
set -uo pipefail

cd "${CLAUDE_PROJECT_DIR:-.}" 2>/dev/null || true

# Resolve main root + LOG_DIR via lib-root.sh (beacon unification).
SCRIPT_DIR="$(dirname "${BASH_SOURCE[0]}")"
# shellcheck source=lib-root.sh
source "$SCRIPT_DIR/lib-root.sh"

# Read stdin ONCE before any work (SessionStart passes JSON on stdin).
SESSION_STDIN=$(cat)

# Beacon ATTEMPT before any work (fail-loud contract per ADR-0057 D1).
printf '{"hook":"session-start","status":"attempt","ts":"%s"}\n' \
  "$(date -u -Iseconds 2>/dev/null)" \
  >> "$LOG_DIR/hook-fires.jsonl" 2>/dev/null || true

# Python3 in-hook self-test: beacon result so interpreter liveness is explicit.
_PY3_STATUS="ok"
python3 -c "import json,sys" 2>/dev/null || _PY3_STATUS="error"
printf '{"hook":"session-start","status":"python3_selftest","result":"%s","ts":"%s"}\n' \
  "$_PY3_STATUS" "$(date -u -Iseconds 2>/dev/null)" \
  >> "$LOG_DIR/hook-fires.jsonl" 2>/dev/null || true

# ---- git state (always available) ------------------------------------------
BR=$(git symbolic-ref --short HEAD 2>/dev/null || echo "(detached)")
DIV="(fetch failed)"
git fetch origin develop 2>/dev/null && DIV=$(git rev-list --count HEAD..origin/develop 2>/dev/null || echo "?")
LOG=$(git log --oneline -5 2>/dev/null || echo "(no log)")

# ---- deploy-gap handshake (PRD #1075 criterion 4 / slice #1079) --------------
# NOTE (#1282 root-cause fix): this block is now the ONLY core.hooksPath
# check in this script. A standalone "hooks path warning" block used to
# live here too, bare-string-comparing `git config --get core.hooksPath`
# against the literal ".githooks" -- the exact bug #1093 already fixed
# in deploy-handshake.sh's own comparison below (path-identity via
# `cd ... && pwd -P`, not string equality), but never ported to this
# second call site. Since deploy-handshake.sh's hooksPath check already
# runs as part of the handshake below and reports any GENUINE mismatch
# via DEPLOY_WARN, the standalone block was a duplicate implementation of
# the same invariant -- and the only one of the two still wrong. Deleting
# it (one invariant, one implementation) rather than fixing it twice.
# SPIDR-R choice (documented per the slice's own risk callout): this is the
# highest-stakes slice in the PRD (a session-start hard block that could brick
# every session bootstrap on a false positive, and this dispatch cannot
# dogfood-test real session UX). We take the SAFER fallback explicitly
# authorized by the slice body: land the handshake script (exit 1 + LOUD
# banner) and the CI backstop as the actual BLOCKING legs now; session-start
# stays LOUD-WARN (upgrade from the prior one-line HOOKS_WARN to the full
# multi-line deploy-gap banner surfaced in context) rather than causing this
# hook to exit non-zero. TODO-slice: graduate session-start to a true hard
# block after one proven session cycle with the loud-warn behavior observed
# live — see tools/repair-topology.md + PR body for the full rationale.
DEPLOY_WARN=""
HANDSHAKE_SH="$SCRIPT_DIR/../../tools/deploy-handshake.sh"
if [ -f "$HANDSHAKE_SH" ]; then
  DEPLOY_OUTPUT=$(bash "$HANDSHAKE_SH" 2>&1)
  DEPLOY_EXIT=$?
  if [ "$DEPLOY_EXIT" -ne 0 ]; then
    DEPLOY_WARN=$(printf "\n%s\n" "$DEPLOY_OUTPUT")
  fi
fi

# ---- gh/jq availability -------------------------------------------------------
JQ_OK=0; GH_OK=0
command -v jq >/dev/null 2>&1 && JQ_OK=1
[ "$JQ_OK" -eq 1 ] && command -v gh >/dev/null 2>&1 \
  && gh auth status >/dev/null 2>&1 && GH_OK=1

# ---- jq warning ---------------------------------------------------------------
JQ_WARN=""
if [ "$JQ_OK" -ne 1 ]; then
  JQ_WARN=$(printf "\nWARNING: jq missing. PreToolUse Edit/Write hook degrades to rule-#10 ask. Install: bootstrap.sh or winget/brew/apt jq.\n")
fi

# ---- gh-unavailable warning ---------------------------------------------------
GH_WARN=""
if [ "$GH_OK" -ne 1 ]; then
  GH_WARN=$(printf "\nWARNING: gh CLI unavailable or not authenticated. Issue/PR state unavailable at session start.\n")
fi

# ---- GitHub live state (gh + jq required) ------------------------------------
# Formatting helpers: byte-identical to the original inline jq filters (only
# their INPUT changed -- a local tempfile instead of a live gh|jq pipe -- so
# additionalContext stays field/format-compatible, per AC 3c4). The `-s`
# (file exists AND non-empty) guard is required in addition to jq's own
# `|| echo "(query failed)"`: jq on a genuinely EMPTY file (a failed gh call
# writes zero bytes -- its own error text goes to the stderr we discard)
# exits 0 with no output, not an error, so jq's `||` alone would silently
# degrade to an EMPTY field rather than "(query failed)" -- the ORIGINAL
# live `gh | jq` pipe caught this via `pipefail` propagating gh's own
# non-zero exit through the pipeline; that signal doesn't exist once gh's
# capture and jq's formatting are split across `wait`, so the emptiness
# check restores the same fallback guarantee explicitly.
_fmt_open() {
  [ -s "$1" ] || { echo "(query failed)"; return; }
  jq -r 'if length==0 then "0 open"
         else "\(length)+ open; recent: \([.[] | "#\(.number) \(.title)"] | join(" | "))"
         end' < "$1" 2>/dev/null || echo "(query failed)"
}
_fmt_nhprs() {
  [ -s "$1" ] || { echo "(query failed)"; return; }
  jq -r 'if length==0 then "0 needs-human PRs"
         else "\(length)+ needs-human PRs: \([.[] | "#\(.number) \(.title)"] | join(" | "))"
         end' < "$1" 2>/dev/null || echo "(query failed)"
}

NH_ISSUES="(gh/jq unavailable)"
NH_PRS="(gh/jq unavailable)"
SL="(gh/jq unavailable)"
PR="(gh/jq unavailable)"
CAP="(gh/jq unavailable)"

if [ "$GH_OK" -eq 1 ]; then
  # Five independent gh queries run CONCURRENTLY as backgrounded jobs, each
  # writing its RAW JSON to a PRIVATE tempfile (per-query output isolation,
  # the slicer's named risk: one query's timeout/rate-limit can never
  # corrupt a sibling's captured text). jq formatting runs AFTER `wait`,
  # sequentially, against the already-captured local files -- measured
  # faster than piping gh|jq live inside each backgrounded job (5 concurrent
  # jq spawns contending with the network-bound gh calls cost ~2s in
  # profiling; concurrent-gh + sequential-local-jq cost ~1.1s for the same
  # fixture -- see PR body). Each formatting call keeps the query's own
  # `|| echo "(query failed)"` fallback (also degrades to that text when a
  # job's tempfile was never written at all -- e.g. gh killed mid-run).
  # Primitive: bash background jobs + `wait` (chosen over a python3
  # orchestrator per PRD #1193 §6 OQ2 -- see PR body for the measured
  # before/after medians). The serial `git fetch origin develop` +
  # divergence count above is untouched -- explicit rabbit-hole guard
  # (PRD #1193 §6 / slice #1199 out-of-bounds); the #1191 dashboard-liveness
  # probe below is untouched too. ADR-0079 D3 / slice #1199.
  T_NH=$(mktemp 2>/dev/null) || T_NH="${TMPDIR:-${TEMP:-${TMP:-/tmp}}}/session-start-gh-nh-$$-$RANDOM"
  T_SL=$(mktemp 2>/dev/null) || T_SL="${TMPDIR:-${TEMP:-${TMP:-/tmp}}}/session-start-gh-sl-$$-$RANDOM"
  T_CAP=$(mktemp 2>/dev/null) || T_CAP="${TMPDIR:-${TEMP:-${TMP:-/tmp}}}/session-start-gh-cap-$$-$RANDOM"
  T_PR=$(mktemp 2>/dev/null) || T_PR="${TMPDIR:-${TEMP:-${TMP:-/tmp}}}/session-start-gh-pr-$$-$RANDOM"
  T_NHPR=$(mktemp 2>/dev/null) || T_NHPR="${TMPDIR:-${TEMP:-${TMP:-/tmp}}}/session-start-gh-nhpr-$$-$RANDOM"

  gh issue list --label needs-human --state open --json number,title --limit 3 > "$T_NH" 2>/dev/null &
  PID_NH=$!
  gh issue list --label slice --state open --json number,title --limit 3 > "$T_SL" 2>/dev/null &
  PID_SL=$!
  gh issue list --label captured --state open --json number,title --limit 3 > "$T_CAP" 2>/dev/null &
  PID_CAP=$!
  gh pr list --state open --json number,title --limit 3 > "$T_PR" 2>/dev/null &
  PID_PR=$!
  gh pr list --state open --label needs-human --json number,title --limit 3 > "$T_NHPR" 2>/dev/null &
  PID_NHPR=$!

  wait "$PID_NH" "$PID_SL" "$PID_CAP" "$PID_PR" "$PID_NHPR" 2>/dev/null

  NH_ISSUES=$(_fmt_open "$T_NH")
  SL=$(_fmt_open "$T_SL")
  CAP=$(_fmt_open "$T_CAP")
  PR=$(_fmt_open "$T_PR")
  NH_PRS=$(_fmt_nhprs "$T_NHPR")

  rm -f "$T_NH" "$T_SL" "$T_CAP" "$T_PR" "$T_NHPR" 2>/dev/null
fi

# ---- Build context string ---------------------------------------------------
CTX=$(printf "Branch: %s | %s commit(s) behind origin/develop\n\nRecent commits:\n%s\n\nNeeds-human issues: %s\nNeeds-human PRs: %s\nOpen slices: %s\nOpen PRs: %s\nOpen captured: %s%s%s%s\n" \
  "$BR" "$DIV" "$LOG" \
  "$NH_ISSUES" "$NH_PRS" "$SL" "$PR" "$CAP" \
  "$JQ_WARN" "$GH_WARN" "$DEPLOY_WARN" \
  | head -c 6144 | head -n 60)

# ---- Emit session_start event (PRD #876 consolidation) ----------------------
# Replaces the standalone settings.json SessionStart log-tool-event.sh entry.
# Pass SESSION_STDIN (already captured above) as stdin to the logger.
printf '%s' "$SESSION_STDIN" | bash "$SCRIPT_DIR/log-tool-event.sh" session_start 2>/dev/null || true

# ---- Emit session_context_injected event via canonical logger pattern --------
export LTE_STDIN="$SESSION_STDIN"
export LTE_EVENT_TYPE="session_context_injected"
export LTE_LOG_DIR="$LOG_DIR"

python3 - <<'PYEOF'
import sys, os, json, datetime, re, subprocess

event_type = "session_context_injected"
log_dir    = os.environ["LTE_LOG_DIR"]
stdin_data = os.environ.get("LTE_STDIN", "")

def ts_now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def beacon(status, reason=None):
    obj = {"hook": event_type, "status": status, "ts": ts_now()}
    if reason:
        obj["reason"] = reason
    line = json.dumps(obj, separators=(",", ":"))
    target = os.environ.get("WORKFLOW_LOG_DIR", log_dir)
    os.makedirs(target, exist_ok=True)
    try:
        with open(os.path.join(target, "hook-fires.jsonl"), "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass  # never crash

FIXTURE_PATTERN = re.compile(
    r"^(demo|test|verify|fixture|manual|sess-|sample-session-id$)",
    re.IGNORECASE
)

try:
    raw = stdin_data.strip()
    if not raw:
        raise ValueError("empty stdin")
    payload = json.loads(raw)
    session_id = payload.get("session_id", "")
    if not session_id:
        raise ValueError("missing or empty session_id")

    # Derive worktree name from git toplevel basename.
    try:
        toplevel = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"],
            stderr=subprocess.DEVNULL, timeout=3
        ).decode().strip()
        wt = os.path.basename(toplevel) if toplevel else "unknown"
    except Exception:
        wt = "unknown"

    event = {
        "v": 2,
        "ts": ts_now(),
        "session_id": session_id,
        "src": "hook",
        "wt": wt,
        "event": event_type,
    }
    line = json.dumps(event, separators=(",", ":"))

    sandbox = os.environ.get("WORKFLOW_LOG_DIR", "")
    write_dir = sandbox if sandbox else log_dir
    is_fixture = bool(FIXTURE_PATTERN.match(session_id))
    target_file = "workflow-events.test.jsonl" if is_fixture else "workflow-events.jsonl"

    os.makedirs(write_dir, exist_ok=True)
    with open(os.path.join(write_dir, target_file), "a", encoding="utf-8") as f:
        f.write(line + "\n")

    beacon("ok")

except Exception as exc:
    reason = str(exc)[:200]
    reject_dir = os.environ.get("WORKFLOW_LOG_DIR", log_dir)
    os.makedirs(reject_dir, exist_ok=True)
    reject_obj = {
        "ts": ts_now(),
        "hook": event_type,
        "reason": reason,
        "raw": stdin_data[:4096],
    }
    try:
        with open(os.path.join(reject_dir, "workflow-events.rejects.jsonl"),
                  "a", encoding="utf-8") as f:
            f.write(json.dumps(reject_obj, separators=(",", ":")) + "\n")
    except Exception:
        pass
    beacon("ERROR", reason)
PYEOF

# ---- Emit hookSpecificOutput to stdout ---------------------------------------
if [ "$JQ_OK" -eq 1 ]; then
  jq -cn --arg ctx "$CTX" \
    '{hookSpecificOutput: {hookEventName: "SessionStart", additionalContext: $ctx}}'
else
  ESC=$(printf '%s' "$CTX" \
        | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' \
        | awk 'BEGIN{ORS="\\n"}{print}')
  printf '{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"%s"}}\n' "$ESC"
fi

# Beacon OK at the end.
printf '{"hook":"session-start","status":"ok","ts":"%s"}\n' \
  "$(date -u -Iseconds 2>/dev/null)" \
  >> "$LOG_DIR/hook-fires.jsonl" 2>/dev/null || true

exit 0
