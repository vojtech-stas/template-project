#!/bin/bash
# log-tool-event.sh — parameterized python3-based hook logger (PRD #668 slice #669).
#                     Consolidated per PRD #876 slice #877: now the ONE canonical logger;
#                     log-event.sh deleted; lifecycle events folded into calling scripts.
#
# CONTRACT:
#   Called with exactly one argument: the event_type string
#   (e.g. session_start, session_stop, agent_start, agent_complete,
#   bash_complete, skill_invoke, grill_qa, session_context_injected).
#   Pass "auto" to derive event_type from the payload's hook_event_name + tool_name.
#   Hook stdin is a JSON object from Claude Code containing session_id,
#   hook_event_name, and event-specific fields.
#
# ORDERING GUARANTEE (per PRD #668 §2 / rabbit-holes):
#   1. Read stdin once into a bash variable.
#   2. BEACON attempt → hook-fires.jsonl  (pure bash — no parse dependency).
#   3. Parse + validate with python3.
#   4. Route + write v2 event to workflow-events[.test].jsonl OR rejects.
#   5. BEACON ok|error.
#   6. Exit 0 always (never break the session).
#
# ROUTING:
#   - If WORKFLOW_LOG_DIR env is set → write under that dir (test harness sandbox).
#   - fixture session_id (matches FIXTURE_PATTERN) → workflow-events.test.jsonl.
#   - else → workflow-events.jsonl (production).
#   The two routing dimensions are independent: sandbox dir + fixture file name
#   can both apply simultaneously.
#
# Schema v2: {"v":2, "ts", "session_id" (required/non-empty), "src":"hook",
#              "wt":<basename of git toplevel>, "event", ...payload}
#
# Per-event payload (selected fields only — never the full raw input/response):
#   agent_start:    subagent_type, input (first 300 chars of description), model (if found)
#   agent_complete: subagent_type, input (first 300 chars), tail (last 2000 chars
#                   of tool_response — trailer capture for verdict parsing), model (if found)
#   bash_complete:  command (first 200 chars)
#   skill_invoke:   skill (name extracted from tool_input), source="skill_tool"
#   grill_qa:       question (first 300 chars), answer (first 300 chars)
#   user_prompt:    prompt (first 500 chars of .prompt)
#   session_start:  no extra payload
#   session_stop:   assistant_tail (last 700 chars of last assistant message, via
#                   ≤256 KB transcript tail-read; present only when found);
#                   tail_error (short reason string; present only on failure);
#                   fail-soft: event always writes even if transcript read fails
#   subagent_edit:  file_path (written to subagent-edits.log, NOT workflow-events.jsonl)
#
# NO jq IN THIS FILE — python3 only; jq ENOEXEC hazard is structurally irrelevant.
#
# Invoke style (settings.json registration):
#   bash "$CLAUDE_PROJECT_DIR/.claude/pipeline/hooks/log-tool-event.sh" session_start
#   bash "$CLAUDE_PROJECT_DIR/.claude/pipeline/hooks/log-tool-event.sh" auto

EVENT_TYPE="${1:-unknown}"

# Step 1: read stdin once into a bash variable.
LTE_STDIN=$(cat)

# Step 2: beacon ATTEMPT before any parsing — pure bash, no parse dependency.
# Source lib-root.sh to get LOG_DIR (and MAIN_ROOT).
SCRIPT_DIR="$(dirname "${BASH_SOURCE[0]}")"
# shellcheck source=lib-root.sh
source "$SCRIPT_DIR/lib-root.sh"

# Route attempt beacon through WORKFLOW_LOG_DIR if set (fix: reviewer follow-up
# from PR #672 — bash-level beacon was bypassing WORKFLOW_LOG_DIR, causing
# attempt/ok pairs to land in different directories during sandbox tests).
_BEACON_DIR="${WORKFLOW_LOG_DIR:-$LOG_DIR}"
mkdir -p "$_BEACON_DIR" 2>/dev/null || true

# Write attempt beacon (pure bash printf — no jq, no python).
printf '{"hook":"%s","status":"attempt","ts":"%s"}\n' \
  "$EVENT_TYPE" "$(date -u -Iseconds 2>/dev/null)" \
  >> "$_BEACON_DIR/hook-fires.jsonl" 2>/dev/null || true

# Steps 3-5: parse, validate, route, write, beacon result — via python3.
# Pass stdin content via env var (stdin is already consumed above).
export LTE_STDIN
export LTE_EVENT_TYPE="$EVENT_TYPE"
export LTE_LOG_DIR="$LOG_DIR"

python3 - <<'PYEOF'
import sys, os, json, datetime, re, subprocess

event_type = os.environ["LTE_EVENT_TYPE"]
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

def is_fixture_sid(sid):
    return bool(FIXTURE_PATTERN.match(sid))

try:
    raw = stdin_data.strip()
    if not raw:
        raise ValueError("empty stdin")

    payload = json.loads(raw)

    # AUTO-MODE: derive event_type from hook_event_name + tool_name.
    if event_type == "auto":
        _hev = str(payload.get("hook_event_name", ""))
        _tn  = str(payload.get("tool_name", ""))
        _POST = {"Agent": "agent_complete", "Bash": "bash_complete", "AskUserQuestion": "grill_qa"}
        _PRE  = {"Agent": "agent_start", "Skill": "skill_invoke"}
        event_type = _POST.get(_tn, "post_tool") if _hev == "PostToolUse" else \
                     _PRE.get(_tn, "pre_tool")  if _hev == "PreToolUse"  else "unknown"

    # Validate required field.
    session_id = payload.get("session_id", "")
    if not session_id:
        raise ValueError("missing or empty session_id")

    # Derive worktree name from git toplevel basename.
    try:
        toplevel = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"],
            stderr=subprocess.DEVNULL,
            timeout=3
        ).decode().strip()
        wt = os.path.basename(toplevel) if toplevel else "unknown"
    except Exception:
        wt = "unknown"

    # Per-event payload selection (selected fields only — never full raw input/response).
    tool_input    = payload.get("tool_input", {}) if isinstance(payload.get("tool_input"), dict) else {}
    tool_response = payload.get("tool_response", "")
    # Normalise tool_response to a string for slicing.
    if not isinstance(tool_response, str):
        tool_response = json.dumps(tool_response, separators=(",", ":"))

    # SUBAGENT-EDIT NUDGE: PostToolUse Edit|MultiEdit|Write on .claude/agents/*.md
    # (host-owned/shims) or .claude/pipeline/agents/*.md (package sources,
    # ADR-0092 D1, slice #1604) → write to subagent-edits.log; NOT a
    # workflow-events.jsonl event.
    if event_type == "post_tool":
        _fp = str(tool_input.get("file_path", ""))
        _tn2 = str(payload.get("tool_name", ""))
        if _tn2 in ("Edit", "MultiEdit", "Write") and (
            re.search(r'\.claude[/\\]agents[/\\].*\.md$', _fp)
            or re.search(r'\.claude[/\\]pipeline[/\\]agents[/\\].*\.md$', _fp)
        ):
            _ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
            _nudge = "{} {} (edit detected; AS-AUDIT runs in CI CHECK 18)\n".format(_ts, _fp)
            _wdir = os.environ.get("WORKFLOW_LOG_DIR", "") or log_dir
            os.makedirs(_wdir, exist_ok=True)
            try:
                with open(os.path.join(_wdir, "subagent-edits.log"), "a", encoding="utf-8") as f:
                    f.write(_nudge)
            except Exception:
                pass
        beacon("ok")
        sys.exit(0)

    # Build v2 event.
    event = {
        "v": 2,
        "ts": ts_now(),
        "session_id": session_id,
        "src": "hook",
        "wt": wt,
        "event": event_type,
    }

    if event_type == "agent_start":
        event["subagent_type"] = str(tool_input.get("subagent_type", ""))[:200]
        _desc = str(tool_input.get("description", ""))
        event["input"] = _desc[:300]
        # model: subagent model where derivable from description (ADR-0069 D3).
        _model_m = re.search(r'\bmodel\s*:\s*(\S+)', _desc, re.IGNORECASE)
        if _model_m:
            event["model"] = str(_model_m.group(1))[:100]

    elif event_type == "agent_complete":
        event["subagent_type"] = str(tool_input.get("subagent_type", ""))[:200]
        _desc_c = str(tool_input.get("description", ""))
        event["input"] = _desc_c[:300]
        # tail = last 2000 chars of tool_response (trailer capture for verdict parsing).
        event["tail"]          = tool_response[-2000:] if len(tool_response) > 2000 else tool_response
        # model: carry through on completion events where derivable (ADR-0069 D3).
        _model_mc = re.search(r'\bmodel\s*:\s*(\S+)', _desc_c, re.IGNORECASE)
        if _model_mc:
            event["model"] = str(_model_mc.group(1))[:100]

    elif event_type == "bash_complete":
        event["command"] = str(tool_input.get("command", ""))[:200]

    elif event_type == "skill_invoke":
        # Skill tool_input carries the skill name under various keys.
        skill_name = (
            tool_input.get("skill")
            or tool_input.get("command")
            or tool_input.get("name")
            or ""
        )
        event["skill"]  = str(skill_name)[:200]
        event["source"] = "skill_tool"

    elif event_type == "grill_qa":
        q_raw = tool_input.get("question") or tool_input
        if isinstance(q_raw, dict):
            q_raw = json.dumps(q_raw, separators=(",", ":"))
        a_raw = tool_response
        event["question"] = str(q_raw)[:300]
        event["answer"]   = str(a_raw)[:300]

    elif event_type == "user_prompt":
        # Capture first 500 chars of the user's prompt (python3 only — no jq).
        prompt_text = str(payload.get("prompt", ""))
        event["prompt"] = prompt_text[:500]

    elif event_type == "session_stop":
        # Attempt to capture assistant_tail from the transcript file.
        # Fail-soft: ANY failure → omit the field, set optional tail_error,
        # event still writes, beacon ok, exit 0.
        transcript_path = payload.get("transcript_path", "")
        if transcript_path:
            try:
                import os as _os
                _MAX_BYTES = 256 * 1024  # 256 KB tail bound
                tsize = _os.path.getsize(transcript_path)
                with open(transcript_path, "rb") as _tf:
                    if tsize > _MAX_BYTES:
                        _tf.seek(tsize - _MAX_BYTES)
                    raw_tail = _tf.read().decode("utf-8", errors="replace")
                # Discard first partial line (may be cut mid-line by seek).
                if tsize > _MAX_BYTES:
                    first_nl = raw_tail.find("\n")
                    if first_nl != -1:
                        raw_tail = raw_tail[first_nl + 1:]
                # Scan for the last assistant message and concatenate text blocks.
                last_text = None
                for _line in raw_tail.splitlines():
                    _line = _line.strip()
                    if not _line:
                        continue
                    try:
                        _obj = json.loads(_line)
                        if _obj.get("type") == "assistant":
                            _msg = _obj.get("message", {})
                            _content = _msg.get("content", []) if isinstance(_msg, dict) else []
                            _text = "".join(
                                _block.get("text", "")
                                for _block in _content
                                if isinstance(_block, dict) and _block.get("type") == "text"
                            )
                            if _text:
                                last_text = _text
                    except Exception:
                        continue
                if last_text is not None:
                    event["assistant_tail"] = last_text[-700:]
                else:
                    event["tail_error"] = "no_assistant_message_found"
            except Exception as _te:
                event["tail_error"] = str(_te)[:200]

    # session_start carries no extra payload (session_stop handled above).

    line = json.dumps(event, separators=(",", ":"))

    # Routing (two independent dimensions):
    # - WORKFLOW_LOG_DIR overrides the base directory (test harness sandbox).
    # - fixture session_id always routes to workflow-events.test.jsonl.
    sandbox = os.environ.get("WORKFLOW_LOG_DIR", "")
    write_dir = sandbox if sandbox else log_dir
    target_file = "workflow-events.test.jsonl" if is_fixture_sid(session_id) else "workflow-events.jsonl"

    os.makedirs(write_dir, exist_ok=True)

    # Size-capped archive-aside rotation (ADR-0068 D1).
    # Cap: 5 MB per workflow-events.jsonl (production file only).
    # Archive-aside: move to workflow-events.YYYYMMDDTHHMMSS.jsonl — NEVER delete.
    # Only rotates the production file (not .test.jsonl or .rejects.jsonl).
    _ROTATION_CAP_BYTES = 5 * 1024 * 1024  # 5 MB; documented here per ADR-0068 D1
    target_path = os.path.join(write_dir, target_file)
    if (target_file == "workflow-events.jsonl"
            and os.path.exists(target_path)
            and os.path.getsize(target_path) >= _ROTATION_CAP_BYTES):
        try:
            archive_ts = datetime.datetime.now(datetime.timezone.utc).strftime(
                "%Y%m%dT%H%M%S"
            )
            archive_name = "workflow-events.{}.jsonl".format(archive_ts)
            archive_path = os.path.join(write_dir, archive_name)
            import shutil as _shutil
            _shutil.move(target_path, archive_path)
            rot_obj = {
                "ts": ts_now(), "hook": "log-tool-event", "status": "ok",
                "event": "rotation",
                "archived": archive_name, "cap_bytes": _ROTATION_CAP_BYTES,
            }
            with open(os.path.join(write_dir, "hook-fires.jsonl"),
                      "a", encoding="utf-8") as _rf:
                _rf.write(json.dumps(rot_obj, separators=(",", ":")) + "\n")
        except Exception:
            pass  # rotation failure is non-fatal; write proceeds to new file

    with open(os.path.join(write_dir, target_file), "a", encoding="utf-8") as f:
        f.write(line + "\n")

    beacon("ok")

except Exception as exc:
    reason = str(exc)[:200]
    # Preserve raw stdin in rejects file (lossless).
    reject_dir = os.environ.get("WORKFLOW_LOG_DIR", log_dir)
    os.makedirs(reject_dir, exist_ok=True)
    reject_obj = {
        "ts": ts_now(),
        "hook": event_type,
        "reason": reason,
        "raw": stdin_data[:4096],
    }
    try:
        with open(os.path.join(reject_dir, "workflow-events.rejects.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(reject_obj, separators=(",", ":")) + "\n")
    except Exception:
        pass
    beacon("ERROR", reason)
PYEOF

exit 0
