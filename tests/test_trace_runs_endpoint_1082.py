"""
tests/test_trace_runs_endpoint_1082.py

Test-first regressions for slice #1082 (PRD #1075 criterion 9) — the
read-model that formerly fed the dashboard's PRIMARY-render `/api/trace-runs`
endpoint (now retired, ADR-0088 D1; the functions below survive as
library shims, captured: #1491):
`dashboard/tracestore.py`'s `_build_recorded_runs()` (blocking builder) and
`serve_trace_runs()` (background-warmed, stale-while-revalidate serve path,
mirroring `prd_firing.py`'s serve_prd_firing() house pattern per issue
#962).

Test-first discipline: this commit lands BEFORE `serve_trace_runs()` /
`_build_recorded_runs()` exist on `dashboard/tracestore.py`. FAILS before
impl: every test below raises AttributeError. PASSES after impl: the next
commit adds both functions and this suite goes green.

Covers:
  (a) _build_recorded_runs groups spans by trace_id into ordered PR-shaped
      chains (opened_ts/merged_ts/dur_ms/spans), newest-opened-first.
  (b) serve_trace_runs cold-start: first call returns {"status":
      "computing"} immediately (no blocking gh/sqlite work on the request
      thread) and kicks a background thread.
  (c) serve_trace_runs warm: once the background thread completes, a
      subsequent call returns the real payload (real data — no fixtures
      in a production path; this test's fixture log lives entirely in a
      tmpdir, never `.claude/logs/*`, per rule #21).
  (d) serve_trace_runs never re-kicks a second background thread while one
      is already in flight for the same cache key.

Runner: stdlib unittest + pytest compatible.
  python -m pytest tests/test_trace_runs_endpoint_1082.py -v
"""

import importlib.util
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "dashboard"
TRACESTORE_PY = DASHBOARD_DIR / "tracestore.py"


def _load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_tracestore():
    return _load_module(TRACESTORE_PY, "tracestore_test_1082_endpoint")


# Two PR chains (opened->merged) + one dispatch-only sibling span (no
# attrs.pr) — mirrors real pr-open/pr-merge wrapper output.
_FIXTURE_SPANS = [
    {"v": 3, "ts": "2026-08-02T10:00:00Z", "trace_id": "pr-1095",
     "span_id": "s0", "kind": "agent_dispatch", "attrs": {"actor": "implementer"}},
    {"v": 3, "ts": "2026-08-02T10:00:05Z", "trace_id": "pr-1095",
     "span_id": "s1", "kind": "pr_opened",
     "attrs": {"pr": "1095", "branch": "feat/x"}},
    {"v": 3, "ts": "2026-08-02T10:05:00Z", "trace_id": "pr-1095",
     "span_id": "s2", "kind": "pr_merged", "dur_ms": 867,
     "attrs": {"pr": "1095", "sha": "abc123"}},
    {"v": 3, "ts": "2026-08-02T11:00:00Z", "trace_id": "pr-1096",
     "span_id": "s3", "kind": "pr_opened",
     "attrs": {"pr": "1096", "branch": "feat/y"}},
    {"v": 3, "ts": "2026-08-02T11:10:00Z", "trace_id": "pr-1096",
     "span_id": "s4", "kind": "pr_merged", "dur_ms": 9573,
     "attrs": {"pr": "1096", "sha": "def456"}},
]


def _write_fixture_log(path, spans=None):
    spans = spans if spans is not None else _FIXTURE_SPANS
    with open(path, "w", encoding="utf-8") as f:
        for s in spans:
            f.write(json.dumps(s) + "\n")


class TestBuildRecordedRuns(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.log_path = os.path.join(self.tmpdir.name, "trace-v3.jsonl")
        self.db_path = os.path.join(self.tmpdir.name, "trace.db")
        _write_fixture_log(self.log_path)
        self.tracestore = _load_tracestore()

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_groups_spans_into_ordered_pr_chains(self):
        runs = self.tracestore._build_recorded_runs(
            limit=30, log_path=self.log_path, db_path_=self.db_path
        )
        self.assertEqual(len(runs), 2)
        # newest-opened-first: pr-1096 opened at 11:00 > pr-1095 at 10:00:05
        self.assertEqual(runs[0]["pr"], "1096")
        self.assertEqual(runs[1]["pr"], "1095")

        run_1095 = runs[1]
        self.assertEqual(run_1095["opened_ts"], "2026-08-02T10:00:05Z")
        self.assertEqual(run_1095["merged_ts"], "2026-08-02T10:05:00Z")
        self.assertEqual(run_1095["dur_ms"], 867)
        self.assertEqual(
            [s["span_id"] for s in run_1095["spans"]], ["s0", "s1", "s2"]
        )

    def test_limit_caps_the_result(self):
        runs = self.tracestore._build_recorded_runs(
            limit=1, log_path=self.log_path, db_path_=self.db_path
        )
        self.assertEqual(len(runs), 1)

    def test_limit_below_total_signals_capped_response(self):
        """(review round 1, B2) Data-layer proof of the "capped" scenario the
        client's _recordedRunsCapped heuristic depends on: 25 real recorded
        chains, limit=20 -> exactly 20 returned (run_count == limit), so a
        client sees `runs.length >= requestedLimit` and must NOT assume the
        5 chains outside the window (e.g. a hypothetical PR #21) are
        genuinely absent from the trace store — only that they're outside
        THIS fetch's window."""
        many_spans = []
        for i in range(25):
            pr = str(9200 + i)
            many_spans.append({
                "v": 3, "ts": f"2026-08-02T{10 + (i // 60):02d}:{i % 60:02d}:00Z",
                "trace_id": f"pr-{pr}", "span_id": f"open-{pr}",
                "kind": "pr_opened", "attrs": {"pr": pr},
            })
            many_spans.append({
                "v": 3, "ts": f"2026-08-02T{11 + (i // 60):02d}:{i % 60:02d}:00Z",
                "trace_id": f"pr-{pr}", "span_id": f"merge-{pr}",
                "kind": "pr_merged", "dur_ms": 100, "attrs": {"pr": pr},
            })
        big_log = os.path.join(self.tmpdir.name, "big25.jsonl")
        _write_fixture_log(big_log, spans=many_spans)

        runs = self.tracestore._build_recorded_runs(
            limit=20, log_path=big_log, db_path_=self.db_path
        )
        self.assertEqual(
            len(runs), 20,
            "25 real recorded chains with limit=20 must return exactly 20 "
            "(the capped case) — proves the >limit scenario is representable",
        )

    def test_no_spans_returns_empty_list(self):
        empty_log = os.path.join(self.tmpdir.name, "empty.jsonl")
        _write_fixture_log(empty_log, spans=[])
        runs = self.tracestore._build_recorded_runs(
            limit=30, log_path=empty_log, db_path_=self.db_path
        )
        self.assertEqual(runs, [])


class TestServeTraceRunsBackgroundWarm(unittest.TestCase):
    """Mirrors prd_firing.py's stale-while-revalidate cache exactly — each
    test resets the module-level cache dict/lock via a fresh module load
    (no shared global state leaks across tests)."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.log_path = os.path.join(self.tmpdir.name, "trace-v3.jsonl")
        self.db_path = os.path.join(self.tmpdir.name, "trace.db")
        _write_fixture_log(self.log_path)
        self.tracestore = _load_tracestore()

    def tearDown(self):
        # Wait for any daemon background thread this test kicked off to
        # finish BEFORE deleting the tmpdir — otherwise a still-running
        # thread's open sqlite handle races tmpdir.cleanup() on Windows'
        # stricter mandatory file locking (PermissionError/OSError).
        deadline = time.time() + 5
        while getattr(self.tracestore, "_runs_computing", False) and time.time() < deadline:
            time.sleep(0.02)
        self.tmpdir.cleanup()

    def test_cold_start_returns_computing_immediately(self):
        result = self.tracestore.serve_trace_runs(
            limit=30, log_path=self.log_path, db_path_=self.db_path
        )
        self.assertEqual(result, {"status": "computing"})

    def test_warm_after_background_completes_returns_real_data(self):
        self.tracestore.serve_trace_runs(
            limit=30, log_path=self.log_path, db_path_=self.db_path
        )
        # Poll until the background thread finishes (bounded wait).
        deadline = time.time() + 5
        result = None
        while time.time() < deadline:
            result = self.tracestore.serve_trace_runs(
                limit=30, log_path=self.log_path, db_path_=self.db_path
            )
            if result.get("status") != "computing":
                break
            time.sleep(0.02)
        self.assertIsNotNone(result)
        self.assertNotEqual(result.get("status"), "computing")
        self.assertEqual(result.get("run_count"), 2)
        self.assertEqual(len(result.get("runs", [])), 2)
        self.assertIn("fetched_at", result)

    def test_second_cold_call_does_not_duplicate_background_thread(self):
        first = self.tracestore.serve_trace_runs(
            limit=30, log_path=self.log_path, db_path_=self.db_path
        )
        second = self.tracestore.serve_trace_runs(
            limit=30, log_path=self.log_path, db_path_=self.db_path
        )
        self.assertEqual(first, {"status": "computing"})
        self.assertEqual(second, {"status": "computing"})


if __name__ == "__main__":
    unittest.main()
