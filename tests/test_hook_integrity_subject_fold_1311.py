"""Regression tests for slice #1311 (PRD #1266 §2 criteria 5 + 14) — ADR-0083 D3.

ADR-0083 D3's corollary states the invariant these tests pin: *a check's subject
set is defined, not assumed, and every registered subject is scored under exactly
one named label.*  For `dashboard/health.py::check_hook_integrity` that means:

  - the subject set is the hook scripts registered in `.claude/settings.json`;
  - `auto` is `log-tool-event.sh`'s stream identity, and the runtime-derived
    terminal keys `dashboard/discovery.py::_auto_mode_derived_keys` returns for a
    registered auto-mode entry (agent_start, bash_complete, ...) fold onto it, so
    a paired attempt+terminal is NOT drift;
  - a record whose `hook` label is neither a registered hook nor a registered
    hook's declared stream identity is not scored at all — it may not enter the
    ratio list, the drift list, `error_count`, or the verdict.

Against the pre-fix code (raw `obj["hook"]` grouping) the first three test
classes FAIL: `auto` is drift on a correctly-paired two-record log, an
out-of-subject `ERROR` record FAILs the whole row through the unfiltered
`error_count` term, and the three registered hooks that beacon under their own
script stems are absent from `ratios:` entirely.

Rule #21: every synthetic beacon here is written to a `tempfile.mkdtemp()` tree
and the check is redirected there by patching `health._HEALTH_REPO_ROOT` (the
documented test seam — `_telemetry_log_root()` falls back to it when the patched
directory is not a git repo).  Zero lines reach `.claude/logs/*`; `setUp` asserts
the redirection actually took effect rather than trusting it.

Runner: stdlib unittest + pytest compatible.
  python -m unittest tests.test_hook_integrity_subject_fold_1311 -v
"""

import json
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "dashboard"))

import health  # noqa: E402

REAL_SETTINGS = REPO_ROOT / ".claude" / "settings.json"


def _now_ts() -> str:
    """An in-window timestamp in the shape the hook layer emits."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class _ScratchLogCase(unittest.TestCase):
    """Base: a scratch tree holding a synthetic hook-fires.jsonl + the REAL
    settings.json (so the subject set under test is the real registration set,
    not a fixture that could drift away from it)."""

    def setUp(self):
        self.scratch = Path(tempfile.mkdtemp(prefix="hookintegrity1311_"))
        self.addCleanup(shutil.rmtree, self.scratch, True)
        (self.scratch / ".claude" / "logs").mkdir(parents=True)
        shutil.copyfile(REAL_SETTINGS, self.scratch / ".claude" / "settings.json")
        self._real_root = health._HEALTH_REPO_ROOT
        health._HEALTH_REPO_ROOT = self.scratch
        self.addCleanup(setattr, health, "_HEALTH_REPO_ROOT", self._real_root)
        # Fixture integrity (rule #21): the check must read the scratch tree.
        self.assertNotEqual(self.scratch, self._real_root)
        self.assertEqual(
            health._telemetry_log_root(),
            self.scratch,
            "test seam broken: the check would read the production log",
        )

    def run_check(self, records) -> dict:
        log = self.scratch / ".claude" / "logs" / "hook-fires.jsonl"
        with log.open("w", encoding="utf-8") as fh:
            for rec in records:
                rec = dict(rec)
                rec.setdefault("ts", _now_ts())
                fh.write(json.dumps(rec) + "\n")
        return health.check_hook_integrity()


class TestAutoFoldIsNotDrift(_ScratchLogCase):
    """Criterion 5: an `auto` attempt plus a matching derived terminal is a
    complete pair, not drift."""

    def test_auto_attempt_plus_derived_terminal_is_not_drift(self):
        res = self.run_check([
            {"hook": "auto", "status": "attempt"},
            {"hook": "bash_complete", "status": "ok"},
        ])
        self.assertNotIn("drift", res["detail"], res["detail"])
        self.assertIn("auto:1/1", res["detail"], res["detail"])
        self.assertEqual(res["result"], "PASS", res["detail"])

    def test_derived_terminal_is_not_scored_under_its_own_label(self):
        """`bash_complete` is a derived key, never a subject of its own."""
        res = self.run_check([
            {"hook": "auto", "status": "attempt"},
            {"hook": "bash_complete", "status": "ok"},
        ])
        self.assertNotIn("bash_complete", res["detail"], res["detail"])

    def test_unpaired_auto_attempt_still_drifts(self):
        """The fold must not become a blanket exemption: an `auto` attempt with
        no derived terminal is still genuine drift."""
        res = self.run_check([{"hook": "auto", "status": "attempt"}])
        self.assertIn("auto(0/1)", res["detail"], res["detail"])
        self.assertEqual(res["result"], "FAIL", res["detail"])


class TestOutOfSubjectRecordsAreNotScored(_ScratchLogCase):
    """Criterion 14's `error_count` leg: a label outside the subject set may not
    redden the row, and may not appear in the ratio list."""

    def _conforming_pair(self):
        return [
            {"hook": "session-start", "status": "attempt"},
            {"hook": "session-start", "status": "ok"},
        ]

    def test_out_of_subject_error_does_not_fail_the_row(self):
        for spelling in ("ERROR", "error"):
            with self.subTest(status=spelling):
                res = self.run_check(self._conforming_pair() + [
                    {"hook": "not-a-registered-hook", "status": spelling},
                ])
                self.assertNotIn("ERROR beacons", res["detail"], res["detail"])
                self.assertNotIn("not-a-registered-hook", res["detail"], res["detail"])
                self.assertEqual(res["result"], "PASS", res["detail"])

    def test_out_of_subject_attempt_is_not_scored(self):
        res = self.run_check(self._conforming_pair() + [
            {"hook": "not-a-registered-hook", "status": "attempt"},
        ])
        self.assertNotIn("not-a-registered-hook", res["detail"], res["detail"])
        self.assertEqual(res["result"], "PASS", res["detail"])

    def test_in_subject_error_is_attributed_to_its_stream_identity(self):
        """An ERROR under a derived key counts against its registrar's stream
        identity, and the detail names the label it was attributed to."""
        res = self.run_check(self._conforming_pair() + [
            {"hook": "agent_start", "status": "error"},
        ])
        self.assertIn("ERROR beacons: auto:1", res["detail"], res["detail"])
        self.assertEqual(res["result"], "FAIL", res["detail"])


class TestGenuineDriftStaysVisible(_ScratchLogCase):
    """Criterion 5's whole point: with the structural false reds folded away, a
    real registered-hook drift entry (the live example is `session-start`) is
    still listed."""

    def test_registered_hook_drift_survives_the_fold(self):
        res = self.run_check([
            {"hook": "session-start", "status": "attempt"},
            {"hook": "session-start", "status": "attempt"},
            {"hook": "session-start", "status": "attempt"},
            {"hook": "session-start", "status": "ok"},
            {"hook": "auto", "status": "attempt"},
            {"hook": "bash_complete", "status": "ok"},
            {"hook": "not-a-registered-hook", "status": "ERROR"},
        ])
        self.assertIn("drift: session-start(1/3)", res["detail"], res["detail"])
        self.assertNotIn("auto(", res["detail"], res["detail"])
        self.assertEqual(res["result"], "FAIL", res["detail"])


class TestRegisteredHooksAreCountable(_ScratchLogCase):
    """Criterion 14: the three hooks that beacon under their own script stems
    each carry a ratio entry when the log holds their records."""

    def test_three_previously_invisible_streams_get_ratio_entries(self):
        # "dashboard-autostart" was one of three example subjects at slice
        # #1311's writing; its SessionStart registration is deleted per
        # ADR-0088 D1 (slice #1481), so "session-start" — the surviving
        # SessionStart-registered, non-auto hook — stands in as the third
        # example. The invariant under test (three hooks beaconing under
        # their own script stems each get a ratio entry) is unchanged.
        res = self.run_check([
            {"hook": "pre-tool-bash", "status": "attempt"},
            {"hook": "pre-tool-bash", "status": "ok"},
            {"hook": "user-prompt-submit", "status": "attempt"},
            {"hook": "user-prompt-submit", "status": "ok"},
            {"hook": "session-start", "status": "attempt"},
            {"hook": "session-start", "status": "ok"},
        ])
        for name in ("pre-tool-bash", "user-prompt-submit", "session-start"):
            with self.subTest(hook=name):
                self.assertIn(f"{name}:1/1", res["detail"], res["detail"])


class TestSubjectSetIsDefinedNotAssumed(unittest.TestCase):
    """ADR-0083 D3 corollary: every scored label is a registered hook or a
    registered hook's declared stream identity, and `log-tool-event`'s stream is
    scored under `auto` rather than under its own script name."""

    def setUp(self):
        self.subjects = health._hook_integrity_subjects(REAL_SETTINGS)
        self.assertTrue(self.subjects, "subject set must be derivable from settings.json")

    def test_scored_labels_are_registered_hooks_or_stream_identities(self):
        text = REAL_SETTINGS.read_text(encoding="utf-8")
        for label in set(self.subjects.values()):
            with self.subTest(label=label):
                self.assertTrue(
                    label == "auto" or f"hooks/{label}.sh" in text,
                    f"{label} is neither a registered hook nor a declared stream identity",
                )

    def test_log_tool_event_is_scored_under_auto(self):
        self.assertNotIn("log-tool-event", set(self.subjects.values()))
        self.assertEqual(self.subjects.get("auto"), "auto")
        self.assertEqual(self.subjects.get("bash_complete"), "auto")


if __name__ == "__main__":
    unittest.main()
