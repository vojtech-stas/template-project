"""
tests/test_browser_opt_in.py

Regression tests for slice #1434 (PRD #1432): make the dashboard's browser
auto-open opt-in.

Per ADR-0067 D2 + D3 (regression rider): this test file is committed BEFORE
the fix commit so it fails on the pre-fix codebase (the pure decision
helper does not exist yet, and the pre-fix default opens a browser whenever
nothing suppresses it). After the fix it passes.

Asserts:
  (a) _should_open_browser(env) truth table: unset -> False, empty ->
      False, a non-empty value -> True, including a value that reads as
      falsy text ("0") -> True, since any non-empty setting opts in.
  (b) the real server.main() call site drives webbrowser.open to a call
      count of 0 when the flag is absent and exactly 1 -- with its own
      bound-port URL -- when the flag is set. ThreadingHTTPServer is
      mocked out so main() never binds a socket; port 8799 is used
      throughout (never 8765/8766) per PRD #1432's port constraint.

Per PRD #1432 SS6: this file asserts the new flag's behavior only. It does
not name the retired flag anywhere -- the whole-repo sweep (criterion 5)
greps the tracked tree for that literal string, so naming it here would
reintroduce it on the very change meant to remove it.
"""

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DASHBOARD_DIR = _REPO_ROOT / "dashboard"
if str(_DASHBOARD_DIR) not in sys.path:
    sys.path.insert(0, str(_DASHBOARD_DIR))

import server  # noqa: E402 -- needs sys.path set above


class TestShouldOpenBrowserTruthTable(unittest.TestCase):
    """_should_open_browser(env) is a pure function of its environment mapping."""

    def test_unset_is_false(self):
        self.assertFalse(server._should_open_browser({}))

    def test_empty_value_is_false(self):
        self.assertFalse(server._should_open_browser({"DASH_OPEN_BROWSER": ""}))

    def test_nonempty_value_is_true(self):
        self.assertTrue(server._should_open_browser({"DASH_OPEN_BROWSER": "1"}))

    def test_falsy_looking_nonempty_value_is_true(self):
        # "0" is a non-empty string: bool("0") is True. Any explicit,
        # non-empty setting opts in, regardless of its textual content.
        self.assertTrue(server._should_open_browser({"DASH_OPEN_BROWSER": "0"}))


class TestMainBrowserOpenPath(unittest.TestCase):
    """Exercises the real server.main() call site. ThreadingHTTPServer is
    mocked so no socket is ever bound -- port 8799 is used throughout,
    never 8765/8766, per PRD #1432's port constraint."""

    def _run_main_with_env(self, env):
        """Run server.main() with ThreadingHTTPServer + webbrowser.open
        mocked out and the environment replaced, so main() returns instead
        of blocking in serve_forever(). Returns the webbrowser.open double."""
        mock_server_instance = mock.MagicMock()
        with mock.patch.object(
            server, "ThreadingHTTPServer", return_value=mock_server_instance
        ), mock.patch.object(server.webbrowser, "open") as mock_open, \
                mock.patch.dict(os.environ, env, clear=True):
            server.main()
        return mock_open

    def test_flag_absent_makes_zero_browser_calls(self):
        mock_open = self._run_main_with_env({"DASH_PORT": "8799"})
        self.assertEqual(mock_open.call_count, 0)

    def test_flag_set_makes_exactly_one_call_with_bound_port_url(self):
        mock_open = self._run_main_with_env(
            {"DASH_PORT": "8799", "DASH_OPEN_BROWSER": "1"}
        )
        mock_open.assert_called_once_with("http://localhost:8799")


if __name__ == "__main__":
    unittest.main()
