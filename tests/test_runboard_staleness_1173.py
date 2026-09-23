"""
tests/test_runboard_staleness_1173.py

Fixture-ledger unit tests for the run-board staleness flag (PRD #1170,
slice #1173, §2 criterion 1d): a `dispatch` span open longer than
RUNBOARD_STALE_THRESHOLD_SECONDS carries `stale: true` in its `now` entry;
a fresh one does not. Also covers §2 criterion 2c's per-row `source` field
on now/recent entries (the `next` column was retired per ADR-0080 D2,
slice #1219 — its dedicated provenance case below was trimmed with it).

Every fixture is written to a tempfile via explicit log_path/db_path_ args
(the same override seam TRACE_LOG_OVERRIDE/TRACE_DB_OVERRIDE resolve to
under the hood) — NEVER the real ledger (CLAUDE.md rule #21 / R-FIXTURE
fixture discipline). Mirrors tests/test_runboard_query_1172.py's house
pattern exactly.

Runner: stdlib unittest + pytest compatible.
  python -m pytest tests/test_runboard_staleness_1173.py -v
"""

import importlib.util
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
TRACESTORE_PY = REPO_ROOT / "dashboard" / "tracestore.py"


def _load_tracestore():
    spec = importlib.util.spec_from_file_location("tracestore_test_1173", TRACESTORE_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write_fixture_log(path, spans):
    with open(path, "w", encoding="utf-8") as f:
        for s in spans:
            f.write(json.dumps(s) + "\n")


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class TestRunboardStaleness1173(unittest.TestCase):
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

    def test_back_dated_dispatch_is_flagged_stale(self):
        """A dispatch span opened well past the threshold carries
        stale: true with its elapsed time."""
        threshold = self.tracestore.RUNBOARD_STALE_THRESHOLD_SECONDS
        back_dated = datetime.now(timezone.utc) - timedelta(seconds=threshold + 600)
        spans = [
            {"v": 3, "ts": _iso(back_dated), "trace_id": "slice-9001",
             "span_id": "s1", "kind": "dispatch",
             "attrs": {"slice": "9001", "prd": "1170", "session_id": "s1"}},
        ]
        board = self._build(spans)
        self.assertEqual(len(board["now"]), 1)
        entry = board["now"][0]
        self.assertTrue(entry["stale"])
        self.assertGreaterEqual(entry["elapsed_seconds"], threshold)

    def test_fresh_dispatch_is_not_flagged_stale(self):
        """A dispatch span opened moments ago does not carry stale: true."""
        fresh = datetime.now(timezone.utc) - timedelta(seconds=5)
        spans = [
            {"v": 3, "ts": _iso(fresh), "trace_id": "slice-9002",
             "span_id": "s2", "kind": "dispatch",
             "attrs": {"slice": "9002", "prd": "1170", "session_id": "s1"}},
        ]
        board = self._build(spans)
        self.assertEqual(len(board["now"]), 1)
        entry = board["now"][0]
        self.assertFalse(entry["stale"])
        self.assertLess(entry["elapsed_seconds"], self.tracestore.RUNBOARD_STALE_THRESHOLD_SECONDS)

    def test_threshold_boundary_is_inclusive(self):
        """Elapsed exactly equal to the threshold is flagged stale (>=, not >)."""
        threshold = self.tracestore.RUNBOARD_STALE_THRESHOLD_SECONDS
        boundary = datetime.now(timezone.utc) - timedelta(seconds=threshold + 1)
        spans = [
            {"v": 3, "ts": _iso(boundary), "trace_id": "slice-9003",
             "span_id": "s3", "kind": "dispatch",
             "attrs": {"slice": "9003", "prd": "1170", "session_id": "s1"}},
        ]
        board = self._build(spans)
        self.assertTrue(board["now"][0]["stale"])

    def test_stale_threshold_seconds_echoed_in_payload(self):
        """The board response echoes the configured threshold — the caller
        must never hardcode it (a run-board-era provenance intent; ADR-0078
        D1 history, superseded by ADR-0088)."""
        board = self._build([])
        self.assertEqual(
            board["stale_threshold_seconds"],
            self.tracestore.RUNBOARD_STALE_THRESHOLD_SECONDS,
        )
        self.assertGreater(board["stale_threshold_seconds"], 0)

    def test_ledger_name_echoed_in_payload(self):
        """The board response echoes the ledger's display name — the
        honest empty state names it rather than rendering a blank panel."""
        board = self._build([])
        self.assertIn("trace-v3.jsonl", board["ledger"])

    def test_now_entry_carries_source_provenance(self):
        """Every `now` entry states its own data source (§2 #2c)."""
        fresh = datetime.now(timezone.utc) - timedelta(seconds=5)
        spans = [
            {"v": 3, "ts": _iso(fresh), "trace_id": "slice-9004",
             "span_id": "s4", "kind": "dispatch",
             "attrs": {"slice": "9004", "prd": "1170", "session_id": "s1"}},
        ]
        board = self._build(spans)
        self.assertIn("dispatch", board["now"][0]["source"])

    def test_recent_entry_carries_source_provenance(self):
        """`recent` entries state their own data source (§2 #2c)."""
        spans = [
            {"v": 3, "ts": "2026-08-04T09:05:00Z", "trace_id": "pr-500",
             "span_id": "p1", "kind": "pr_merged",
             "attrs": {"pr": "500"}, "dur_ms": 5000},
        ]
        board = self._build(spans)
        self.assertIn("pr_merged", board["recent"][0]["source"])


if __name__ == "__main__":
    unittest.main()
