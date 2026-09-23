"""
Regression tests for issue #1107 — STREAM-LIVENESS per-stream cadence classes.

Root cause (see issue #1107's diagnosis comment): STREAM-LIVENESS applied a
uniform 60-minute window to every registered stream regardless of its natural
cadence. A once-per-session stream (session-start, session-scoped-b) or a
trigger-driven stream (grill_qa) mathematically FAILs any uniform window
shorter than the time since its last (legitimate) trigger — a false-alarm
class, not a real outage. This is exactly what forced PRD #1075's
production-verify to FAIL on 2026-08-02: all three streams had beaconed at
their most recent real trigger; the window model was wrong for sparse streams.

Fix: per-stream cadence classes in the STREAM-LIVENESS registry —
  - always-on: unchanged 60m uniform window.
  - session-scoped (session-start, session-scoped-b, ...): alive iff the
    stream's own last beacon is within a small skew tolerance of the NEWEST
    beacon among all session-scoped streams (never against wall-clock "now").
    FAILs only when a newer session-scoped beacon exists elsewhere and this
    stream missed it — the real hooks-go-dark outage class stays detected.
  - on-demand (grill_qa, ...): silence alone is informational ("idle
    (on-demand)"), never a FAIL.

This test file FAILS against develop before the #1107 fix (uniform-window
check_stream_liveness has no cadence classes: fixture (a) — a session-scoped
stream idle >60m with no newer session evidence — incorrectly FAILs) and
PASSES after.

Runner: stdlib unittest + pytest compatible.
  python -m pytest tests/test_stream_liveness_1107.py -v
"""

import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "dashboard"

_STREAM_LIVENESS_DARK_MINUTES = 60


def _iso(ts: float) -> str:
    import datetime
    return (
        datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )


def _write_jsonl(path: Path, lines: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for obj in lines:
            fh.write(json.dumps(obj) + "\n")


def _write_settings(path: Path, hook_entries: dict) -> None:
    """hook_entries: {event_name: [{"matcher": ..., "hooks": [{"command": ...}]}]}"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"hooks": hook_entries}), encoding="utf-8")


# session-start.sh is the one real SessionStart hook registered in
# .claude/settings.json; session-scoped-b is a synthetic sibling
# (the former dashboard-spawn hook's registration was retired per
# ADR-0088 D1 / slice #1483) that exercises the same filename-stem-fallback
# shape so the two-session-scoped-stream skew-tolerance logic below still
# has a second stream to test against.
_SESSION_SCOPED_SETTINGS = {
    "SessionStart": [
        {"matcher": "", "hooks": [{"type": "command",
         "command": 'bash "$X/.claude/hooks/session-start.sh"'}]},
        {"matcher": "", "hooks": [{"type": "command",
         "command": 'bash "$X/.claude/hooks/session-scoped-b.sh"'}]},
    ],
    "PreToolUse": [
        {"matcher": "Bash", "hooks": [{"type": "command",
         "command": 'bash "$X/.claude/hooks/pre-tool-bash.sh"'}]},
        # Skill routed through log-tool-event.sh's AUTO-MODE -> derives the
        # "skill_invoke" on-demand stream (mirrors real settings.json's
        # PreToolUse "Agent|Skill" matcher).
        {"matcher": "Skill", "hooks": [{"type": "command",
         "command": 'bash "$X/.claude/hooks/log-tool-event.sh" auto'}]},
        # AskUserQuestion routed through log-tool-event.sh's AUTO-MODE ->
        # derives the "grill_qa" on-demand stream (mirrors real settings.json).
    ],
    "PostToolUse": [
        {"matcher": "AskUserQuestion", "hooks": [{"type": "command",
         "command": 'bash "$X/.claude/hooks/log-tool-event.sh" auto'}]},
    ],
}


def _run_check(tmp_dir: Path, settings: dict, fires_lines=None, trace_lines=None,
               now_override: str = None) -> dict:
    """Invoke check_stream_liveness() via subprocess with env-var overrides
    (mirrors tests/test_stream_liveness_1085.py's _run_check pattern)."""
    settings_path = tmp_dir / "settings.json"
    _write_settings(settings_path, settings)

    fires_path = tmp_dir / "hook-fires.jsonl"
    if fires_lines is not None:
        _write_jsonl(fires_path, fires_lines)

    trace_path = tmp_dir / "trace-v3.jsonl"
    if trace_lines is not None:
        _write_jsonl(trace_path, trace_lines)

    script = f"""
import sys
sys.path.insert(0, r'{DASHBOARD_DIR}')
import os
os.environ['_STREAM_LIVENESS_SETTINGS_OVERRIDE'] = r'{settings_path}'
os.environ['_STREAM_LIVENESS_FIRES_OVERRIDE'] = r'{fires_path}'
os.environ['_STREAM_LIVENESS_TRACE_OVERRIDE'] = r'{trace_path}'
{"os.environ['_STREAM_LIVENESS_NOW_OVERRIDE'] = " + repr(now_override) if now_override else ""}
from health import check_stream_liveness
import json
print(json.dumps(check_stream_liveness()))
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True, cwd=str(DASHBOARD_DIR),
    )
    if result.returncode != 0:
        raise AssertionError(
            f"check_stream_liveness() subprocess failed:\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}"
        )
    return json.loads(result.stdout.strip())


class TestFixtureA_SessionScopedOldButSynced(unittest.TestCase):
    """(a) False-FAIL repro: session-scoped streams whose last beacon
    coincides with the newest session start, >60m ago, must NOT FAIL — this
    is exactly the PRD #1075 production-verify incident (session-start and
    the real incident's second session-scoped hook — now the synthetic
    session-scoped-b sibling, since that hook's registration was retired per
    ADR-0088 D1 — both beaconed at ~05:26Z, 145m before the check ran, with
    no newer session having started since)."""

    def test_synced_session_scoped_streams_old_but_not_failed(self):
        now = time.time()
        now_iso = _iso(now)
        old_seconds = 145 * 60  # 145m ago — matches the real incident's evidence
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            result = _run_check(
                tmp_dir,
                _SESSION_SCOPED_SETTINGS,
                fires_lines=[
                    {"hook": "session-start", "ts": _iso(now - old_seconds)},
                    {"hook": "session-scoped-b", "ts": _iso(now - old_seconds)},
                    {"hook": "pre-tool-bash", "ts": _iso(now - 60)},  # fresh always-on
                    # grill_qa (on-demand): never fired — must not FAIL either.
                ],
                # Post-explosion (slice #1136): every always-on v3 kind needs
                # a fresh span too, or this "should PASS" fixture would pick
                # up unrelated v3-kind never-fired FAILs.
                trace_lines=[
                    {"v": 3, "ts": _iso(now - 30), "kind": "pr_opened"},
                    {"v": 3, "ts": _iso(now - 30), "kind": "pr_merged"},
                    {"v": 3, "ts": _iso(now - 30), "kind": "verdict"},
                    {"v": 3, "ts": _iso(now - 30), "kind": "dispatch"},
                    {"v": 3, "ts": _iso(now - 30), "kind": "dispatch_end"},
                ],
                now_override=now_iso,
            )
        self.assertEqual(
            "PASS", result["result"],
            msg=(
                "session-scoped streams synced to the newest session-start "
                f"cluster must not FAIL merely for being old: {result}"
            ),
        )
        fail_names = " ".join(result.get("fail_streams", []))
        self.assertNotIn("session-start", fail_names, msg=result)
        self.assertNotIn("session-scoped-b", fail_names, msg=result)
        self.assertNotIn("grill_qa", fail_names, msg=result)
        # honesty requirement: the class + age must still be nameable in output
        self.assertIn("session-start", result["detail"], msg=result)
        self.assertIn("session-scoped-b", result["detail"], msg=result)


class TestFixtureB_NewerSessionWithoutBeacon(unittest.TestCase):
    """(b) Real outage class stays detected: a newer session-scoped beacon
    exists (session-scoped-b just fired) but a sibling session-scoped
    stream (session-start) has NOT fired since a much older session — that
    sibling MUST FAIL (it demonstrably missed the newest session start)."""

    def test_stream_missing_from_newest_session_fails(self):
        now = time.time()
        now_iso = _iso(now)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            result = _run_check(
                tmp_dir,
                _SESSION_SCOPED_SETTINGS,
                fires_lines=[
                    # session-scoped-b just fired -- a new session started.
                    {"hook": "session-scoped-b", "ts": _iso(now - 120)},
                    # session-start's last beacon is from a MUCH older session
                    # (well beyond the skew tolerance) -- it missed this one.
                    {"hook": "session-start", "ts": _iso(now - 200 * 60)},
                    {"hook": "pre-tool-bash", "ts": _iso(now - 60)},
                ],
                trace_lines=[
                    {"v": 3, "ts": _iso(now - 30), "kind": "pr_opened"},
                ],
                now_override=now_iso,
            )
        self.assertEqual("FAIL", result["result"], msg=result)
        fail_names = " ".join(result.get("fail_streams", []))
        self.assertIn(
            "session-start", fail_names,
            msg=f"session-start missed the newest session start and must FAIL: {result}",
        )
        self.assertNotIn(
            "session-scoped-b(", fail_names,
            msg=f"session-scoped-b (fired at the newest session start) must not FAIL: {result}",
        )


class TestFixtureC_OnDemandSilentIsIdleNotFail(unittest.TestCase):
    """(c) An on-demand stream (grill_qa, skill_invoke — both named in
    #1107's proposed design) silent for days, or never fired, is
    informational idle, never a FAIL — the check has no independent proof a
    trigger happened without its beacon."""

    def test_grill_qa_silent_for_days_is_idle_not_fail(self):
        now = time.time()
        now_iso = _iso(now)
        days_seconds = 6 * 24 * 60 * 60  # 6 days
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            result = _run_check(
                tmp_dir,
                _SESSION_SCOPED_SETTINGS,
                fires_lines=[
                    {"hook": "session-start", "ts": _iso(now - 60)},
                    {"hook": "session-scoped-b", "ts": _iso(now - 60)},
                    {"hook": "pre-tool-bash", "ts": _iso(now - 60)},
                    {"hook": "grill_qa", "ts": _iso(now - days_seconds)},
                ],
                # Post-explosion (slice #1136): full always-on v3 coverage so
                # this fixture isolates the grill_qa on-demand behavior.
                trace_lines=[
                    {"v": 3, "ts": _iso(now - 30), "kind": "pr_opened"},
                    {"v": 3, "ts": _iso(now - 30), "kind": "pr_merged"},
                    {"v": 3, "ts": _iso(now - 30), "kind": "verdict"},
                    {"v": 3, "ts": _iso(now - 30), "kind": "dispatch"},
                    {"v": 3, "ts": _iso(now - 30), "kind": "dispatch_end"},
                ],
                now_override=now_iso,
            )
        self.assertEqual(
            "PASS", result["result"],
            msg=f"a stale on-demand stream must not sink the overall verdict: {result}",
        )
        fail_names = " ".join(result.get("fail_streams", []))
        self.assertNotIn("grill_qa", fail_names, msg=result)
        idle_names = " ".join(result.get("idle_streams", []))
        self.assertIn(
            "grill_qa", idle_names,
            msg=f"grill_qa's silence must be reported as idle (on-demand): {result}",
        )
        self.assertIn("idle (on-demand)", result["detail"], msg=result)

    def test_grill_qa_never_fired_is_idle_not_fail(self):
        """Never having fired at all is the most extreme silence — still no
        provable trigger-without-beacon, so it stays idle, not FAIL."""
        now = time.time()
        now_iso = _iso(now)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            result = _run_check(
                tmp_dir,
                _SESSION_SCOPED_SETTINGS,
                fires_lines=[
                    {"hook": "session-start", "ts": _iso(now - 60)},
                    {"hook": "session-scoped-b", "ts": _iso(now - 60)},
                    {"hook": "pre-tool-bash", "ts": _iso(now - 60)},
                    # grill_qa: no entry at all -- never fired.
                ],
                # Post-explosion (slice #1136): full always-on v3 coverage so
                # this fixture isolates the grill_qa on-demand behavior.
                trace_lines=[
                    {"v": 3, "ts": _iso(now - 30), "kind": "pr_opened"},
                    {"v": 3, "ts": _iso(now - 30), "kind": "pr_merged"},
                    {"v": 3, "ts": _iso(now - 30), "kind": "verdict"},
                    {"v": 3, "ts": _iso(now - 30), "kind": "dispatch"},
                    {"v": 3, "ts": _iso(now - 30), "kind": "dispatch_end"},
                ],
                now_override=now_iso,
            )
        self.assertEqual("PASS", result["result"], msg=result)
        fail_names = " ".join(result.get("fail_streams", []))
        self.assertNotIn("grill_qa", fail_names, msg=result)
        idle_names = " ".join(result.get("idle_streams", []))
        self.assertIn("grill_qa", idle_names, msg=result)

    def test_skill_invoke_silent_for_days_is_idle_not_fail(self):
        """Sibling on-demand stream skill_invoke (Skill-tool invocations) —
        same idle-not-FAIL treatment as grill_qa."""
        now = time.time()
        now_iso = _iso(now)
        days_seconds = 6 * 24 * 60 * 60  # 6 days
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            result = _run_check(
                tmp_dir,
                _SESSION_SCOPED_SETTINGS,
                fires_lines=[
                    {"hook": "session-start", "ts": _iso(now - 60)},
                    {"hook": "session-scoped-b", "ts": _iso(now - 60)},
                    {"hook": "pre-tool-bash", "ts": _iso(now - 60)},
                    {"hook": "skill_invoke", "ts": _iso(now - days_seconds)},
                ],
                # Post-explosion (slice #1136): full always-on v3 coverage so
                # this fixture isolates the skill_invoke on-demand behavior.
                trace_lines=[
                    {"v": 3, "ts": _iso(now - 30), "kind": "pr_opened"},
                    {"v": 3, "ts": _iso(now - 30), "kind": "pr_merged"},
                    {"v": 3, "ts": _iso(now - 30), "kind": "verdict"},
                    {"v": 3, "ts": _iso(now - 30), "kind": "dispatch"},
                    {"v": 3, "ts": _iso(now - 30), "kind": "dispatch_end"},
                ],
                now_override=now_iso,
            )
        self.assertEqual(
            "PASS", result["result"],
            msg=f"a stale on-demand stream must not sink the overall verdict: {result}",
        )
        fail_names = " ".join(result.get("fail_streams", []))
        self.assertNotIn("skill_invoke", fail_names, msg=result)
        idle_names = " ".join(result.get("idle_streams", []))
        self.assertIn(
            "skill_invoke", idle_names,
            msg=f"skill_invoke's silence must be reported as idle (on-demand): {result}",
        )
        self.assertIn("idle (on-demand)", result["detail"], msg=result)

    def test_skill_invoke_never_fired_is_idle_not_fail(self):
        now = time.time()
        now_iso = _iso(now)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            result = _run_check(
                tmp_dir,
                _SESSION_SCOPED_SETTINGS,
                fires_lines=[
                    {"hook": "session-start", "ts": _iso(now - 60)},
                    {"hook": "session-scoped-b", "ts": _iso(now - 60)},
                    {"hook": "pre-tool-bash", "ts": _iso(now - 60)},
                    # skill_invoke: no entry at all -- never fired.
                ],
                # Post-explosion (slice #1136): full always-on v3 coverage.
                trace_lines=[
                    {"v": 3, "ts": _iso(now - 30), "kind": "pr_opened"},
                    {"v": 3, "ts": _iso(now - 30), "kind": "pr_merged"},
                    {"v": 3, "ts": _iso(now - 30), "kind": "verdict"},
                    {"v": 3, "ts": _iso(now - 30), "kind": "dispatch"},
                    {"v": 3, "ts": _iso(now - 30), "kind": "dispatch_end"},
                ],
                now_override=now_iso,
            )
        self.assertEqual("PASS", result["result"], msg=result)
        fail_names = " ".join(result.get("fail_streams", []))
        self.assertNotIn("skill_invoke", fail_names, msg=result)
        idle_names = " ".join(result.get("idle_streams", []))
        self.assertIn("skill_invoke", idle_names, msg=result)


if __name__ == "__main__":
    unittest.main()
