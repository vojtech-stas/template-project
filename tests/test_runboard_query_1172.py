"""
tests/test_runboard_query_1172.py

Fixture-ledger unit tests for the run-board query (PRD #1170 walking
skeleton, slice #1172) — dashboard/tracestore.py's now/recent additions,
formerly consumed by the served run-board's `/api/runboard` endpoint,
retired per ADR-0088 D1 (this test exercises the underlying query
directly; the `next` query was retired earlier, per ADR-0080 D2, slice
#1219 — its two dedicated cases below were deleted with it). One
test per surviving criterion's own Verify clause:

  1a. 2 open + 1 closed dispatch fixture -> `now` has exactly the 2 open.
  1c. 25 terminated chains fixture -> `recent` has exactly the 20 newest,
      newest first, and the 5 oldest are absent.
  1e. empty ledger -> `now` and `recent` are both [].

Every fixture is written to a tempfile via TRACE_LOG_OVERRIDE/
TRACE_DB_OVERRIDE-equivalent explicit log_path/db_path_ args — NEVER the
real ledger (CLAUDE.md rule #21 / R-FIXTURE fixture discipline).

Runner: stdlib unittest + pytest compatible.
  python -m pytest tests/test_runboard_query_1172.py -v
"""

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
TRACESTORE_PY = REPO_ROOT / "dashboard" / "tracestore.py"


def _load_tracestore():
    spec = importlib.util.spec_from_file_location("tracestore_test_1172", TRACESTORE_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write_fixture_log(path, spans):
    with open(path, "w", encoding="utf-8") as f:
        for s in spans:
            f.write(json.dumps(s) + "\n")


class TestRunboardQuery1172(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.log_path = os.path.join(self.tmpdir.name, "trace-v3.jsonl")
        self.db_path = os.path.join(self.tmpdir.name, "trace.db")
        self.tracestore = _load_tracestore()

    def tearDown(self):
        self.tmpdir.cleanup()

    def _build(self, spans):
        _write_fixture_log(self.log_path, spans)
        return self.tracestore._build_runboard(
            log_path=self.log_path, db_path_=self.db_path
        )

    def test_1a_now_has_exactly_open_dispatches(self):
        """2 open + 1 closed dispatch -> `now` has exactly the 2 open ones,
        each carrying slice/prd/session_id/elapsed_seconds."""
        spans = [
            {"v": 3, "ts": "2026-08-04T10:00:00Z", "trace_id": "slice-1",
             "span_id": "a1", "kind": "dispatch",
             "attrs": {"slice": "1", "prd": "900", "session_id": "s1"}},
            {"v": 3, "ts": "2026-08-04T10:01:00Z", "trace_id": "slice-2",
             "span_id": "a2", "kind": "dispatch",
             "attrs": {"slice": "2", "prd": "900", "session_id": "s1"}},
            {"v": 3, "ts": "2026-08-04T10:02:00Z", "trace_id": "slice-3",
             "span_id": "a3", "kind": "dispatch",
             "attrs": {"slice": "3", "prd": "900", "session_id": "s1"}},
            {"v": 3, "ts": "2026-08-04T10:03:00Z", "trace_id": "slice-3",
             "span_id": "a4", "kind": "dispatch_end",
             "attrs": {"slice": "3", "result": "SUCCESS"}},
        ]
        board = self._build(spans)
        self.assertEqual(len(board["now"]), 2)
        slices = {e["slice"] for e in board["now"]}
        self.assertEqual(slices, {"1", "2"})
        for e in board["now"]:
            self.assertEqual(e["prd"], "900")
            self.assertEqual(e["session_id"], "s1")
            self.assertIsNotNone(e["elapsed_seconds"])

    def test_1c_recent_caps_at_20_newest_first(self):
        """25 terminated pr_merged chains -> `recent` has exactly the 20
        newest, newest-first, and the 5 oldest are absent."""
        spans = [
            {"v": 3, "ts": f"2026-08-04T{10 + i // 60:02d}:{i % 60:02d}:00Z",
             "trace_id": f"pr-{i}", "span_id": f"p{i}", "kind": "pr_merged",
             "attrs": {"pr": str(i)}, "dur_ms": 1000 + i}
            for i in range(25)
        ]
        board = self._build(spans)
        recent_prs = [e["pr"] for e in board["recent"]]
        self.assertEqual(len(recent_prs), 20)
        self.assertEqual(recent_prs[0], "24", "must be newest-first")
        self.assertEqual(recent_prs[-1], "5", "20th-newest is pr #5")
        for stale_pr in ("0", "1", "2", "3", "4"):
            self.assertNotIn(stale_pr, recent_prs, "5 oldest must be absent")

    def test_1e_empty_ledger_returns_empty_now_and_recent(self):
        """No spans at all (empty ledger) -> `now` and `recent` are both []."""
        board = self._build([])
        self.assertEqual(board["now"], [])
        self.assertEqual(board["recent"], [])

    def test_dispatch_end_termination_has_outcome_and_duration(self):
        """A dispatch/dispatch_end pair appears in `recent` with the
        recorded outcome and a computed duration (end - start)."""
        spans = [
            {"v": 3, "ts": "2026-08-04T10:00:00Z", "trace_id": "slice-5",
             "span_id": "a1", "kind": "dispatch",
             "attrs": {"slice": "5", "prd": "900", "session_id": "s1"}},
            {"v": 3, "ts": "2026-08-04T10:04:00Z", "trace_id": "slice-5",
             "span_id": "a2", "kind": "dispatch_end",
             "attrs": {"slice": "5", "result": "BLOCKED"}},
        ]
        board = self._build(spans)
        self.assertEqual(len(board["recent"]), 1)
        entry = board["recent"][0]
        self.assertEqual(entry["slice"], "5")
        self.assertEqual(entry["outcome"], "BLOCKED")
        self.assertEqual(entry["dur_ms"], 240000)


if __name__ == "__main__":
    unittest.main()
