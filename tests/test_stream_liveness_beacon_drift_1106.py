"""
tests/test_stream_liveness_beacon_drift_1106.py

Drift-guard test for STREAM-LIVENESS's filename-stem fallback (captured issue
#1106, folded into slice #1086 / PRD #1075 closing pass).

STREAM-LIVENESS (dashboard/health.py::check_stream_liveness) enumerates
"direct-beacon" hooks (those NOT routed through log-tool-event.sh's AUTO-MODE)
by taking the hook SCRIPT's filename stem (dashboard/discovery.py::
_read_hook_name, regex `hooks/([a-z0-9_-]+)\\.sh`) as the registered stream
NAME, then looks up that name's last-fired timestamp from hook-fires.jsonl's
"hook" field. If a hook script is ever renamed WITHOUT updating its own
`"hook":"<name>"` beacon literal (or vice versa), the two silently diverge:
STREAM-LIVENESS registers a stream under the NEW filename stem, but the
script keeps beaconing under the OLD literal — the stream looks permanently
"never-fired" (a false FAIL) with no test catching the drift before this one.

Two assertions:
  1. Real-tree regression guard: every direct-beacon hook registered in the
     real .claude/settings.json has a script whose body contains a literal
     `"hook":"<its own filename stem>"` at least once.
  2. Non-vacuous proof: the same comparison helper, run against a
     deliberately-mismatched synthetic hook script, DOES flag the mismatch —
     proving assertion 1 is a real guard, not a tautology that would pass no
     matter what.

Runner: stdlib unittest + pytest compatible.
    python -m pytest tests/test_stream_liveness_beacon_drift_1106.py -v
"""
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "dashboard"
SETTINGS_PATH = REPO_ROOT / ".claude" / "settings.json"
HOOKS_DIR = REPO_ROOT / ".claude" / "pipeline" / "hooks"

sys.path.insert(0, str(DASHBOARD_DIR))
from discovery import _event_type_from_cmd, _read_hook_name  # noqa: E402

_HOOK_PATH_RE = re.compile(r'hooks/([a-z0-9_-]+)\.sh')


def _direct_beacon_stems_from_settings(settings_path: Path) -> set:
    """Return the set of filename-stem-fallback stream names STREAM-LIVENESS
    would register from settings.json — mirrors
    health.py::_stream_liveness_registered_streams' else-branch exactly."""
    stems = set()
    data = json.loads(settings_path.read_text(encoding="utf-8"))
    for _event, entries in data.get("hooks", {}).items():
        for entry in entries:
            for hook in entry.get("hooks", []):
                cmd = hook.get("command", "")
                event_type_arg = _event_type_from_cmd(cmd)
                if event_type_arg:
                    continue  # "auto" or a literal event-type arg — not a stem fallback
                clean_name = _read_hook_name(cmd)
                m = _HOOK_PATH_RE.search(cmd)
                if clean_name and m:
                    stems.add(clean_name)
    return stems


def _beacon_literal_mismatches(hooks_dir: Path, stems: set) -> list:
    """For each stem, assert its own script contains `"hook":"<stem>"`
    literally. Returns a list of mismatch descriptions (empty = all consistent)."""
    mismatches = []
    for stem in sorted(stems):
        script_path = hooks_dir / f"{stem}.sh"
        if not script_path.exists():
            mismatches.append(f"{stem}: script {script_path} does not exist")
            continue
        body = script_path.read_text(encoding="utf-8", errors="replace")
        if f'"hook":"{stem}"' not in body:
            mismatches.append(
                f"{stem}: {script_path} has no literal \"hook\":\"{stem}\" beacon"
            )
    return mismatches


class TestRealHooksBeaconStemConsistency(unittest.TestCase):
    """Assertion 1: the real tree's direct-beacon hooks are drift-free today —
    and stay that way, since this test fails the instant they diverge."""

    def test_every_direct_beacon_hook_literal_matches_its_filename_stem(self):
        stems = _direct_beacon_stems_from_settings(SETTINGS_PATH)
        self.assertTrue(
            stems, "expected at least one direct-beacon hook stem from settings.json"
        )
        mismatches = _beacon_literal_mismatches(HOOKS_DIR, stems)
        self.assertEqual(
            mismatches, [],
            msg=(
                "hook-name/beacon-literal drift detected (STREAM-LIVENESS would "
                f"silently mis-track these streams): {mismatches}"
            ),
        )


class TestGuardIsNonVacuous(unittest.TestCase):
    """Assertion 2: prove the comparison helper actually catches a real
    mismatch — a synthetic hook script whose beacon literal does NOT match
    its own filename stem must be flagged, not silently pass."""

    def test_mismatched_synthetic_hook_is_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            hooks_dir = Path(tmp) / "hooks"
            hooks_dir.mkdir()
            # Filename stem is "right-name" but the beacon literal says
            # "wrong-name" — the exact drift class this guard exists to catch.
            (hooks_dir / "right-name.sh").write_text(
                '#!/bin/bash\n'
                'printf \'{"hook":"wrong-name","ts":"%s"}\\n\' "$(date -u -Iseconds)"\n',
                encoding="utf-8",
            )
            mismatches = _beacon_literal_mismatches(hooks_dir, {"right-name"})
            self.assertEqual(len(mismatches), 1)
            self.assertIn("right-name", mismatches[0])

    def test_matched_synthetic_hook_is_not_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            hooks_dir = Path(tmp) / "hooks"
            hooks_dir.mkdir()
            (hooks_dir / "consistent-name.sh").write_text(
                '#!/bin/bash\n'
                'printf \'{"hook":"consistent-name","ts":"%s"}\\n\' "$(date -u -Iseconds)"\n',
                encoding="utf-8",
            )
            mismatches = _beacon_literal_mismatches(hooks_dir, {"consistent-name"})
            self.assertEqual(mismatches, [])


if __name__ == "__main__":
    unittest.main()
