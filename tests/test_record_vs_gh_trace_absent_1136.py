"""
tests/test_record_vs_gh_trace_absent_1136.py

Root-cause regression test for a defect discovered while shipping slice
#1136 (PRD #1127 §2 criterion 11a — wiring RECORD-VS-GH into CI as CHECK
22).

**Symptom:** the very first CI run of PR #1149 (this slice) FAILed CHECK 22
with `RECORD-VS-GH — PR #1095 merged ... has no pr_merged span (0/14
post-window PRs covered ...)` — naming EVERY post-window PR merged on
develop as missing a span, not just one real gap.

**Root cause:** `tools/trace.py`'s canonical trace log
(`.claude/logs/trace-v3.jsonl`) is a gitignored LOCAL runtime artifact.
GitHub Actions checks out the PR into a fresh clone that structurally
never has this file (same class as a genuinely fresh repo before the
first span is ever emitted). `check_record_vs_gh` (and the three new
reconcilers this slice ships — check_slice_vs_pr,
check_merged_without_verdict, check_closed_prd_vs_qa) read the log via
`trace_mod.read_spans()`, which degrades an ABSENT file to an empty list
— indistinguishable from "every single artifact's span was individually
dropped". The check then (correctly, given its inputs) reports 0/N
covered — but that reports an ENVIRONMENTAL gap as if it were N separate
verb-bypass incidents, which would red-out CI on every future PR forever.

**Proposed fix:** a shared `_v3_trace_log_exists()` helper, checked BEFORE
each reconciler's gh-fetch-and-reconcile logic; when the trace log file
does not exist at all, degrade honestly to WARN (mirroring the existing
gh-unavailable WARN posture) rather than fabricate a FAIL against every
historical artifact at once.

This test file FAILS against the pre-fix code (health.py's RECORD-VS-GH
lacked the trace-log-existence guard entirely — the whole trace log was
simply routed to TRACE_LOG_OVERRIDE pointing at a non-existent path, which
produced a FAIL naming an arbitrary PR rather than an honest WARN) and
PASSES after the fix, across all four RECORD-VS-GH-pattern checks.

Runner: stdlib unittest + pytest compatible.
  python -m pytest tests/test_record_vs_gh_trace_absent_1136.py -v
"""

import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
_DASHBOARD_DIR = str(REPO_ROOT / "dashboard")
if _DASHBOARD_DIR not in sys.path:
    sys.path.insert(0, _DASHBOARD_DIR)


class _GhResult:
    def __init__(self, value, fetched_at, source):
        self.value = value
        self.fetched_at = fetched_at
        self.source = source


def _live(value: str) -> _GhResult:
    return _GhResult(value=value, fetched_at="2026-08-02T00:00:00+00:00", source="live")


def _reimport(module_name: str):
    if module_name in sys.modules:
        del sys.modules[module_name]
    return importlib.import_module(module_name)


# Bind-forward anchors are now the fixed GRANDFATHER_UNTIL[...] instants in
# dashboard/_constants.py (ADR-0089 D4) -- no override seam exists anymore.
# This file's own assertions never reach the anchor comparison at all (the
# trace-log-existence guard returns WARN before any window logic runs), so
# no fixture date changes are needed here.


class _TraceAbsentTestBase(unittest.TestCase):
    """Points TRACE_LOG_OVERRIDE at a path INSIDE a real temp directory
    that is never created -- simulating a CI fresh checkout, where the
    parent directory tree exists (the repo checkout) but the gitignored
    log file itself was never written."""

    def setUp(self):
        self._old_env = {}
        for k in ("TRACE_LOG_OVERRIDE",):
            self._old_env[k] = os.environ.get(k)
        self._tmpdir = tempfile.mkdtemp(prefix="trace_absent_test_")
        # Deliberately never write this file -- the CI fresh-checkout case.
        self.log_path = os.path.join(self._tmpdir, "trace-v3.jsonl")
        os.environ["TRACE_LOG_OVERRIDE"] = self.log_path

    def tearDown(self):
        for k, v in self._old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _patch_gh(self, health_mod, result):
        health_mod._gh_fetch_impl = lambda args, ttl, timeout: result
        health_mod._GH_CACHE_AVAILABLE = True


class TestRecordVsGhTraceAbsentWarnsHonestly(_TraceAbsentTestBase):
    def test_absent_trace_log_warns_not_fail(self):
        health = _reimport("health")
        prs = [
            {"number": 9001, "mergedAt": "2026-08-01T10:00:00Z",
             "mergeCommit": {"oid": "sha9001"}},
            {"number": 9002, "mergedAt": "2026-08-01T11:00:00Z",
             "mergeCommit": {"oid": "sha9002"}},
        ]
        self._patch_gh(health, _live(json.dumps(prs)))
        self.assertFalse(
            os.path.exists(self.log_path),
            "fixture precondition: trace log must NOT exist (CI-checkout simulation)",
        )

        result = health.check_record_vs_gh()

        self.assertEqual(
            result.get("result"), "WARN",
            f"absent trace log must WARN honestly, never fabricate a FAIL "
            f"against every historical PR: {result}",
        )
        detail = result.get("detail", "").lower()
        self.assertIn("not found", detail, msg=result)
        self.assertNotIn("#9001", result.get("detail", ""), msg=result)
        self.assertNotIn("#9002", result.get("detail", ""), msg=result)


class TestSliceVsPrTraceAbsentWarnsHonestly(_TraceAbsentTestBase):
    def test_absent_trace_log_warns_not_fail(self):
        health = _reimport("health")
        prs = [
            {"number": 9101, "mergedAt": "2026-08-01T10:00:00Z",
             "body": "Closes #9201"},
        ]
        slice_issues = [{"number": 9201}]

        def _router(args, ttl, timeout):
            if args and args[0] == "pr":
                return _live(json.dumps(prs))
            if args and args[0] == "issue":
                return _live(json.dumps(slice_issues))
            return _GhResult(None, "2026-08-02T00:00:00+00:00", "computing")

        health._gh_fetch_impl = _router
        health._GH_CACHE_AVAILABLE = True

        result = health.check_slice_vs_pr()

        self.assertEqual(result.get("result"), "WARN", msg=result)
        self.assertIn("not found", result.get("detail", "").lower(), msg=result)


class TestMergedWithoutVerdictTraceAbsentWarnsHonestly(_TraceAbsentTestBase):
    def test_absent_trace_log_warns_not_fail(self):
        health = _reimport("health")
        prs = [{"number": 9301, "mergedAt": "2026-08-01T10:00:00Z"}]
        self._patch_gh(health, _live(json.dumps(prs)))

        result = health.check_merged_without_verdict()

        self.assertEqual(result.get("result"), "WARN", msg=result)
        self.assertIn("not found", result.get("detail", "").lower(), msg=result)


class TestClosedPrdVsQaTraceAbsentWarnsHonestly(_TraceAbsentTestBase):
    def test_absent_trace_log_warns_not_fail(self):
        health = _reimport("health")
        prds = [{"number": 9401, "closedAt": "2026-08-01T10:00:00Z"}]
        self._patch_gh(health, _live(json.dumps(prds)))

        result = health.check_closed_prd_vs_qa()

        self.assertEqual(result.get("result"), "WARN", msg=result)
        self.assertIn("not found", result.get("detail", "").lower(), msg=result)


if __name__ == "__main__":
    unittest.main()
