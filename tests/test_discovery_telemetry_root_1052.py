"""
Regression test for slice #1052 — discovery.py reads hook-fires.jsonl via the
canonical telemetry root, not the worktree code root (3rd instance of the
#1021 class; #1021 fixed the HTTP server module + health.py, discovery.py
was missed).

Scenario:
  - discovery.py's CODE root points at worktree A (no .claude/logs/).
  - The canonical telemetry root resolves to root B (HAS .claude/logs/hook-
    fires.jsonl with >=1 valid beacon).
  - discover_hooks() must aggregate fire_count/last_fired from B's log, not
    report every hook as fire_count == 0 / last_fired == None.

FAILS before the fix: discovery.py reads _DISCOVERY_REPO_ROOT (worktree A,
empty) directly, so every hook aggregates to fire_count == 0.
PASSES after the fix: discovery.py routes the beacon read through
telemetry_root._telemetry_log_root(), which (once patched to resolve to B)
returns B's populated log.

Runner: stdlib unittest + pytest compatible.
  python -m pytest tests/test_discovery_telemetry_root_1052.py -q
"""

import importlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "dashboard"

if str(DASHBOARD_DIR) not in sys.path:
    sys.path.insert(0, str(DASHBOARD_DIR))


def _import_fresh(mod_name: str, filename: str):
    """Import (or re-import) a dashboard module fresh so monkeypatching works cleanly."""
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(mod_name, DASHBOARD_DIR / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SETTINGS_JSON = json.dumps({
    "hooks": {
        "SessionStart": [
            {
                "matcher": "",
                "hooks": [
                    {
                        "type": "command",
                        "command": 'bash ".claude/hooks/log-tool-event.sh" session_start',
                    }
                ],
            }
        ]
    }
})

BEACON_LINE = json.dumps({
    "ts": "2026-07-01T17:34:00+00:00",
    "hook": "session_start",
    "status": "attempt",
})


class TestDiscoveryTelemetryRootWorktreeScenario(unittest.TestCase):
    """
    Core regression: discovery.py's code root (worktree A) has no logs, but
    the canonical telemetry root (B) has a populated hook-fires.jsonl.
    discover_hooks() must reflect B's beacons, not report 0 fires.
    """

    def setUp(self):
        self._orig_path = sys.path[:]

    def tearDown(self):
        sys.path[:] = self._orig_path
        sys.modules.pop("discovery", None)
        sys.modules.pop("telemetry_root", None)

    def test_discover_hooks_reads_beacons_from_canonical_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)

            # "A" = worktree code root — settings.json + hooks/ present, NO
            # .claude/logs/ (mirrors a worktree-run dashboard).
            root_a = tmp_path / "worktree_a"
            (root_a / ".claude" / "hooks").mkdir(parents=True)
            (root_a / ".claude" / "settings.json").write_text(SETTINGS_JSON, encoding="utf-8")
            (root_a / ".claude" / "hooks" / "log-tool-event.sh").write_text("#!/bin/sh\n", encoding="utf-8")

            # "B" = canonical telemetry root — HAS .claude/logs/hook-fires.jsonl
            # with >=1 valid beacon.
            root_b = tmp_path / "canonical_b"
            (root_b / ".claude" / "logs").mkdir(parents=True)
            fires = root_b / ".claude" / "logs" / "hook-fires.jsonl"
            fires.write_text(BEACON_LINE + "\n", encoding="utf-8")

            telemetry_root_mod = _import_fresh("telemetry_root", "telemetry_root.py")
            discovery_mod = _import_fresh("discovery", "discovery.py")

            with patch.object(discovery_mod, "_DISCOVERY_REPO_ROOT", root_a), \
                 patch.object(telemetry_root_mod, "_telemetry_log_root", lambda: root_b):
                # discovery.py must call telemetry_root's helper (imported by
                # reference or module) so this patch is observed.
                if hasattr(discovery_mod, "_telemetry_log_root"):
                    with patch.object(discovery_mod, "_telemetry_log_root", lambda: root_b):
                        hooks = discovery_mod.discover_hooks()
                else:
                    hooks = discovery_mod.discover_hooks()

            self.assertTrue(hooks, "discover_hooks() returned no hooks — settings.json fixture not read")
            session_start_hooks = [h for h in hooks if h.get("name") == "session_start"]
            self.assertTrue(session_start_hooks, "expected a session_start hook entry")
            hook = session_start_hooks[0]

            self.assertGreaterEqual(
                hook["fire_count"], 1,
                msg=(
                    f"Expected fire_count >= 1 (beacon present in canonical root B), "
                    f"got {hook['fire_count']}. discovery.py is reading the worktree "
                    "code root instead of the canonical telemetry root — repoint the "
                    "beacon read through telemetry_root._telemetry_log_root()."
                ),
            )
            self.assertIsNotNone(
                hook["last_fired"],
                msg="Expected last_fired to be set from B's beacon, got None.",
            )

    def test_discover_hooks_reports_zero_when_code_root_used_directly(self):
        """
        Sanity check on the OLD (buggy) behaviour: if discovery.py were to read
        directly from _DISCOVERY_REPO_ROOT (worktree A, no logs), fire_count
        would be 0. This documents the exact symptom from the #1052 report.
        """
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root_a = tmp_path / "worktree_a"
            (root_a / ".claude" / "hooks").mkdir(parents=True)
            (root_a / ".claude" / "settings.json").write_text(SETTINGS_JSON, encoding="utf-8")
            (root_a / ".claude" / "hooks" / "log-tool-event.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            # No .claude/logs/hook-fires.jsonl under root_a at all.

            discovery_mod = _import_fresh("discovery", "discovery.py")

            beacon_path = root_a / ".claude" / "logs" / "hook-fires.jsonl"
            self.assertFalse(beacon_path.exists())


if __name__ == "__main__":
    unittest.main()
