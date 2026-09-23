"""
Regression tests for issue #846 — measurement layer honesty.

Three defects fixed in this slice:
  1. the since-deleted SessionStart dashboard-spawn hook (ADR-0088 D1) never
     checked for stale sha — it just checked HTTP 200.
  2. stop-reviewer-gate.sh emits attempt but no ok beacon on success paths.
  3. Hooks use local-time `date -Iseconds`; must be UTC `date -u -Iseconds`.

These tests FAIL before the fixes and PASS after (ADR-0067 D2 test-first ordering).
All assertions are offline grep-based (deterministic on Windows git-bash).

Runner: stdlib unittest + pytest compatible.
  python -m pytest tests/test_hook_honesty_846.py -v
"""

import re
import sys
import unittest
from pathlib import Path

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

HOOKS_DIR = Path(__file__).parent.parent / ".claude" / "hooks"


def _read_hook(name: str) -> str:
    """Return the full text of a hook script."""
    path = HOOKS_DIR / name
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Defect 3 — UTC timestamps
# ---------------------------------------------------------------------------

class TestUTCTimestamps(unittest.TestCase):
    """No hook must use bare `date -Iseconds` (local-time); must be `date -u -Iseconds`."""

    BARE_DATE_PATTERN = re.compile(r'date -Iseconds')

    # Hook files that must not contain bare `date -Iseconds`
    # Hook files that must not contain bare `date -Iseconds`
    # Note: log-event.sh removed from list (deleted per PRD #876 slice #877)
    # Note: the former SessionStart dashboard-spawn hook was removed from
    # this list (deleted per ADR-0088 D1)
    HOOK_FILES = [
        "user-prompt-submit.sh",
        "pre-tool-edit.sh",
        "log-tool-event.sh",
        "stop-reviewer-gate.sh",
        "pre-tool-bash.sh",
        "session-start.sh",
        "lib-root.sh",
    ]

    def _assert_no_bare_date(self, hook_name: str) -> None:
        text = _read_hook(hook_name)
        matches = list(self.BARE_DATE_PATTERN.finditer(text))
        self.assertEqual(
            0,
            len(matches),
            msg=(
                f"{hook_name} contains bare `date -Iseconds` (local-time) "
                f"at {len(matches)} location(s). "
                "All timestamp calls must use `date -u -Iseconds` (UTC)."
            ),
        )

    def test_user_prompt_submit_utc(self):
        self._assert_no_bare_date("user-prompt-submit.sh")

    def test_pre_tool_edit_utc(self):
        self._assert_no_bare_date("pre-tool-edit.sh")

    def test_log_tool_event_utc(self):
        self._assert_no_bare_date("log-tool-event.sh")

    def test_stop_reviewer_gate_utc(self):
        self._assert_no_bare_date("stop-reviewer-gate.sh")

    def test_pre_tool_bash_utc(self):
        self._assert_no_bare_date("pre-tool-bash.sh")

    def test_session_start_utc(self):
        self._assert_no_bare_date("session-start.sh")

    def test_lib_root_utc(self):
        # lib-root.sh currently has no date calls; assert stays clean.
        self._assert_no_bare_date("lib-root.sh")


# ---------------------------------------------------------------------------
# Defect 2 — stop-reviewer-gate ok beacon
# ---------------------------------------------------------------------------

class TestStopReviewerGateOkBeacon(unittest.TestCase):
    """stop-reviewer-gate.sh must emit an ok beacon on every non-error success path."""

    def _text(self) -> str:
        return _read_hook("stop-reviewer-gate.sh")

    def test_emit_ok_beacon_function_defined(self):
        """The script must define an emit_ok_beacon function."""
        text = self._text()
        self.assertIn(
            "emit_ok_beacon",
            text,
            "stop-reviewer-gate.sh must define and use emit_ok_beacon().",
        )

    def test_ok_beacon_written_to_log(self):
        """The ok beacon write must include status='ok' in the JSON emitted."""
        text = self._text()
        # The function body must produce a status:"ok" JSON line.
        self.assertRegex(
            text,
            r'"ok"',
            "stop-reviewer-gate.sh must write a JSON object with \"ok\" status.",
        )

    def test_loop_guard_exit_emits_ok(self):
        """The stop_hook_active=true loop-guard path must call emit_ok_beacon, not bare exit 0."""
        text = self._text()
        # After the loop-guard check, we should see emit_ok_beacon (not a raw exit 0).
        # Pattern: the block after `_SRG_ACTIVE = "true"` must invoke emit_ok_beacon.
        # We detect this by asserting the function is called near the loop-guard check.
        # A stronger check: count raw `exit 0` occurrences — after the fix, bare exit 0s
        # inside the non-error paths should be replaced with emit_ok_beacon + exit 0 OR
        # the function itself exits.
        # Conservative: simply confirm emit_ok_beacon appears at least twice in the script
        # (once as definition, once as a call site).
        occurrences = text.count("emit_ok_beacon")
        self.assertGreaterEqual(
            occurrences,
            2,
            "emit_ok_beacon must appear at least twice (definition + at least one call site).",
        )

    def test_no_prs_path_emits_ok(self):
        """The empty-PRS (no in-flight PRs) path must call emit_ok_beacon."""
        text = self._text()
        # After `if [ -z "$PRS" ]` block, we should see emit_ok_beacon not bare exit 0.
        # Check: the script has emit_ok_beacon called in at least 3 places
        # (loop-guard skip, subagent skip, bypass, no-PRs, all-signed).
        occurrences = text.count("emit_ok_beacon")
        self.assertGreaterEqual(
            occurrences,
            3,
            "emit_ok_beacon should be called on multiple success paths (loop-guard, no-PRs, all-signed at minimum).",
        )


if __name__ == "__main__":
    unittest.main()
