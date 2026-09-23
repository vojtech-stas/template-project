"""
Regression test for slice #1021 — telemetry log root resolves via git-common-dir.

Scenario:
  - Dashboard code runs FROM a worktree (code path "A", no .claude/logs/).
  - git rev-parse --git-common-dir resolves to canonical root "B" (has .claude/logs/).
  - _telemetry_log_root() MUST return B, not A.
  - When run from the canonical root (git-common-dir parent == REPO_ROOT),
    the result is unchanged (no regression).

Runner: stdlib unittest + pytest compatible.
  python -m pytest tests/test_telemetry_log_root_1021.py -q
"""

import importlib
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "dashboard"

# Ensure dashboard/ is importable.
if str(DASHBOARD_DIR) not in sys.path:
    sys.path.insert(0, str(DASHBOARD_DIR))


# ---------------------------------------------------------------------------
# Helper: import telemetry_root module fresh so monkeypatching works cleanly.
# ---------------------------------------------------------------------------

def _import_telemetry_root():
    """Import (or re-import) the telemetry_root module from dashboard/."""
    mod_name = "telemetry_root"
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(
        mod_name, DASHBOARD_DIR / "telemetry_root.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestTelemetryLogRootWorktreeScenario(unittest.TestCase):
    """
    Core regression: when code runs from worktree A (no logs),
    git-common-dir points to root B (has logs) — helper must return B.
    """

    def setUp(self):
        self._orig_path = sys.path[:]

    def tearDown(self):
        sys.path[:] = self._orig_path
        # Clean up cached module.
        sys.modules.pop("telemetry_root", None)

    def test_worktree_resolves_to_canonical_root(self):
        """
        Simulate worktree: code root A (no logs), git-common-dir -> root B (has logs).
        _telemetry_log_root() must return B.

        FAILS before the fix because the helper doesn't exist yet (code returns A
        via bare REPO_ROOT).
        """
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            # "A" = worktree code root — NO .claude/logs
            root_a = tmp_path / "worktree_a"
            root_a.mkdir()
            # "B" = canonical root — HAS .claude/logs/hook-fires.jsonl
            root_b = tmp_path / "canonical_b"
            (root_b / ".claude" / "logs").mkdir(parents=True)
            fires = root_b / ".claude" / "logs" / "hook-fires.jsonl"
            fires.write_text(
                json.dumps({"ts": "2026-06-23T00:00:00+00:00", "hook": "test"}) + "\n",
                encoding="utf-8",
            )
            events = root_b / ".claude" / "logs" / "workflow-events.jsonl"
            events.write_text(
                json.dumps({"ts": "2026-06-23T00:00:00+00:00", "v": 2}) + "\n",
                encoding="utf-8",
            )

            # Fake git-common-dir: worktrees store .git as a FILE containing
            # "gitdir: <canonical_root>/.git/worktrees/<name>" —
            # `git rev-parse --git-common-dir` returns "<canonical_root>/.git".
            fake_git_dir = str(root_b / ".git")

            def fake_run(cmd, **kwargs):
                result = MagicMock()
                if "git" in cmd and "--git-common-dir" in cmd:
                    result.returncode = 0
                    result.stdout = fake_git_dir + "\n"
                else:
                    result.returncode = 1
                    result.stdout = ""
                return result

            mod = _import_telemetry_root()
            with patch.object(mod, "_TELEMETRY_CODE_ROOT", root_a), \
                 patch("subprocess.run", side_effect=fake_run):
                # Clear any cached value.
                mod._telemetry_log_root.cache_clear() if hasattr(
                    mod._telemetry_log_root, "cache_clear"
                ) else None
                mod._TELEMETRY_ROOT_CACHE = None

                result = mod._telemetry_log_root()

            self.assertEqual(
                result,
                root_b,
                msg=(
                    f"Expected _telemetry_log_root() == {root_b} (canonical root B) "
                    f"but got {result}. "
                    "Fix: resolve git-common-dir parent in _telemetry_log_root()."
                ),
            )

    def test_hook_fires_path_points_to_canonical_root(self):
        """
        The hook-fires path derived from _telemetry_log_root() must point into
        the canonical root's .claude/logs/, not into the worktree.
        """
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root_a = tmp_path / "worktree_a"
            root_a.mkdir()
            root_b = tmp_path / "canonical_b"
            (root_b / ".claude" / "logs").mkdir(parents=True)

            fake_git_dir = str(root_b / ".git")

            def fake_run(cmd, **kwargs):
                result = MagicMock()
                if "git" in cmd and "--git-common-dir" in cmd:
                    result.returncode = 0
                    result.stdout = fake_git_dir + "\n"
                else:
                    result.returncode = 1
                    result.stdout = ""
                return result

            mod = _import_telemetry_root()
            with patch.object(mod, "_TELEMETRY_CODE_ROOT", root_a), \
                 patch("subprocess.run", side_effect=fake_run):
                mod._TELEMETRY_ROOT_CACHE = None
                telemetry_root = mod._telemetry_log_root()

            fires_path = telemetry_root / ".claude" / "logs" / "hook-fires.jsonl"
            # Must be rooted under B, not A.
            self.assertTrue(
                str(fires_path).startswith(str(root_b)),
                msg=(
                    f"hook-fires path {fires_path} is not under canonical root {root_b}. "
                    "Telemetry reads should resolve to the canonical root via git-common-dir."
                ),
            )
            # Explicitly NOT under A.
            self.assertFalse(
                str(fires_path).startswith(str(root_a)),
                msg=f"hook-fires path {fires_path} is incorrectly rooted under worktree {root_a}.",
            )


class TestTelemetryLogRootFallback(unittest.TestCase):
    """
    When git is unavailable or not a worktree, fall back to code root (REPO_ROOT).
    """

    def setUp(self):
        self._orig_path = sys.path[:]

    def tearDown(self):
        sys.path[:] = self._orig_path
        sys.modules.pop("telemetry_root", None)

    def test_fallback_when_git_fails(self):
        """If git rev-parse fails (non-zero exit), return _TELEMETRY_CODE_ROOT."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root_a = tmp_path / "some_root"
            root_a.mkdir()

            def fake_run_fail(cmd, **kwargs):
                result = MagicMock()
                result.returncode = 128
                result.stdout = ""
                return result

            mod = _import_telemetry_root()
            with patch.object(mod, "_TELEMETRY_CODE_ROOT", root_a), \
                 patch("subprocess.run", side_effect=fake_run_fail):
                mod._TELEMETRY_ROOT_CACHE = None
                result = mod._telemetry_log_root()

            self.assertEqual(
                result,
                root_a,
                msg=(
                    f"Expected fallback to code root {root_a} when git fails, "
                    f"got {result}"
                ),
            )

    def test_no_regression_when_run_from_canonical_root(self):
        """
        When git-common-dir parent == code root (running from canonical root,
        not a worktree), result must equal the code root — no regression.
        """
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root = tmp_path / "canonical_root"
            root.mkdir()
            # git-common-dir == root/.git → parent == root == code root.
            fake_git_dir = str(root / ".git")

            def fake_run(cmd, **kwargs):
                result = MagicMock()
                if "git" in cmd and "--git-common-dir" in cmd:
                    result.returncode = 0
                    result.stdout = fake_git_dir + "\n"
                else:
                    result.returncode = 1
                    result.stdout = ""
                return result

            mod = _import_telemetry_root()
            with patch.object(mod, "_TELEMETRY_CODE_ROOT", root), \
                 patch("subprocess.run", side_effect=fake_run):
                mod._TELEMETRY_ROOT_CACHE = None
                result = mod._telemetry_log_root()

            self.assertEqual(
                result,
                root,
                msg=(
                    f"Expected no-op for canonical root: {root} == {result}. "
                    "Root-run path must be unaffected."
                ),
            )

    def test_fallback_when_git_raises_exception(self):
        """If subprocess.run raises (git not on PATH), fall back to code root."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root_a = tmp_path / "some_root"
            root_a.mkdir()

            def fake_run_raises(cmd, **kwargs):
                raise FileNotFoundError("git not found")

            mod = _import_telemetry_root()
            with patch.object(mod, "_TELEMETRY_CODE_ROOT", root_a), \
                 patch("subprocess.run", side_effect=fake_run_raises):
                mod._TELEMETRY_ROOT_CACHE = None
                result = mod._telemetry_log_root()

            self.assertEqual(
                result,
                root_a,
                msg=(
                    f"Expected fallback to code root {root_a} when git raises, "
                    f"got {result}"
                ),
            )


class TestTelemetryLogRootServerUsage(unittest.TestCase):
    """
    Verify that health.py imports and uses _telemetry_log_root for the
    hook/event log paths (structural check — not a live-server test).

    (The parallel HTTP-server-module sub-check was retired along with that
    module per ADR-0088 D1 — slice #1481.)
    """

    def test_health_py_imports_telemetry_root(self):
        """health.py must import from telemetry_root (or define equivalent)."""
        health_src = (DASHBOARD_DIR / "health.py").read_text(encoding="utf-8")
        self.assertTrue(
            "telemetry_root" in health_src or "_telemetry_log_root" in health_src,
            msg=(
                "health.py must import or call _telemetry_log_root to resolve "
                "hook/event log paths via git-common-dir. Not found in source."
            ),
        )


if __name__ == "__main__":
    unittest.main()
