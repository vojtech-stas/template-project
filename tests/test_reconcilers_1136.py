"""
tests/test_reconcilers_1136.py

Regression / new-feature tests for slice #1136 (PRD #1127 §2 criterion 11b
/ ADR-0076 D1 enforcement item (c)) — the three new reconciler health checks
on the RECORD-VS-GH pattern:

  - SLICE-VS-PR:            merged slice-closing PR without matching
                             dispatch+pr_opened spans -> named FAIL
  - MERGED-WITHOUT-VERDICT: pr_merged'd PR without a verdict span ->
                             named FAIL (bind-forward from the ADR-0076
                             anchor)
  - CLOSED-PRD-VS-QA:       closed prd-labeled issue without a qa_verified
                             PASS span -> named FAIL (bind-forward)

Test-first discipline (rule #13 rider / ADR-0067 D3 shape, applied here to
a new-feature slice per the slice's own instruction): this commit lands
BEFORE the three check functions exist in dashboard/health.py.

FAILS before impl: every test below fails (AttributeError) because
health.check_slice_vs_pr / check_merged_without_verdict /
check_closed_prd_vs_qa do not exist yet.
PASSES after impl: dashboard/health.py gains all three functions + matching
CHECK_REGISTRY entries satisfying every assertion here.

Stubbing seam (mirrors tests/test_record_vs_gh_1081.py): gh is stubbed at
the health._gh_fetch_impl layer via a small router keyed on the gh
sub-command (args[0] == "pr" or "issue") — no real subprocess/gh calls. The
trace-v3.jsonl fixture log is supplied via TRACE_LOG_OVERRIDE (tools/
trace.py's own test seam). The shared ADR-0076 bind-forward anchor
timestamp is supplied via _ADR_0076_ANCHOR_TS_OVERRIDE — ONE override name
for all three reconcilers (they share exactly one anchor instant, threaded
from ADR-0076's binding paragraph, never re-derived per-check).

Runner: stdlib unittest + pytest compatible.
  python -m pytest tests/test_reconcilers_1136.py -v
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


# ---------------------------------------------------------------------------
# Shared gh-stub helpers (mirrors test_record_vs_gh_1081.py's _GhResult)
# ---------------------------------------------------------------------------

class _GhResult:
    """Minimal stand-in for gh_cache.GhResult (a NamedTuple)."""
    def __init__(self, value, fetched_at, source):
        self.value = value
        self.fetched_at = fetched_at
        self.source = source


def _live(value: str) -> _GhResult:
    return _GhResult(value=value, fetched_at="2026-08-02T00:00:00+00:00", source="live")


def _computing() -> _GhResult:
    return _GhResult(value=None, fetched_at="2026-08-02T00:00:00+00:00", source="computing")


def _reimport(module_name: str):
    if module_name in sys.modules:
        del sys.modules[module_name]
    return importlib.import_module(module_name)


# Fixed anchor for every test: the ADR-0076 walking-skeleton merge instant.
_ANCHOR_TS = "2026-08-01T00:00:00+00:00"


class _ReconcilerTestBase(unittest.TestCase):
    """Shared setUp/tearDown: env-var + module-attribute isolation, temp
    trace log, and a gh router keyed on sub-command (args[0])."""

    def setUp(self):
        self._old_env = {}
        for k in ("TRACE_LOG_OVERRIDE", "_ADR_0076_ANCHOR_TS_OVERRIDE"):
            self._old_env[k] = os.environ.get(k)
        self._tmpdir = tempfile.mkdtemp(prefix="reconciler_test_")
        self.log_path = os.path.join(self._tmpdir, "trace-v3.jsonl")
        os.environ["TRACE_LOG_OVERRIDE"] = self.log_path
        os.environ["_ADR_0076_ANCHOR_TS_OVERRIDE"] = _ANCHOR_TS

    def tearDown(self):
        for k, v in self._old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _write_spans(self, spans):
        with open(self.log_path, "w", encoding="utf-8") as f:
            for s in spans:
                f.write(json.dumps(s) + "\n")

    def _patch_gh(self, health_mod, pr_list_result=None, issue_list_result=None,
                  api_result=None):
        """Route gh calls by sub-command: args[0] == 'pr' -> pr_list_result,
        args[0] == 'issue' -> issue_list_result, args[0] == 'api' ->
        api_result. Unrouted calls degrade to 'computing' (never a silent
        real subprocess call).

        The 'api' route answers the QUERY-HONESTY REST canary (ADR-0087 D2
        / slice #1498) every --label-bearing call is now gated behind (see
        health._health_gh_fetch). When api_result is not given explicitly
        but issue_list_result is a confirmed ('live'/'cache') result, a
        REST-canary-matching payload is auto-derived from its item count so
        the attestation PASSes and every pre-existing --label assertion in
        this file keeps reading exactly as it did before the canary
        existed -- no per-test changes needed."""
        def _fetch(args, ttl, timeout):
            if args and args[0] == "pr" and pr_list_result is not None:
                return pr_list_result
            if args and args[0] == "issue" and issue_list_result is not None:
                return issue_list_result
            if args and args[0] == "api":
                if api_result is not None:
                    return api_result
                if issue_list_result is not None and issue_list_result.source in ("live", "cache"):
                    try:
                        _n = len(json.loads(issue_list_result.value))
                    except Exception:
                        _n = 0
                    return _live(json.dumps([{"number": 900000 + i} for i in range(_n)]))
            return _computing()
        health_mod._gh_fetch_impl = _fetch
        health_mod._GH_CACHE_AVAILABLE = True

    def _dispatch_span(self, slice_num, session_id="orchestrator"):
        return {
            "v": 3, "ts": "2026-08-01T12:00:00Z", "trace_id": f"slice-{slice_num}",
            "span_id": f"d-{slice_num}", "kind": "dispatch",
            "attrs": {"slice": str(slice_num), "session_id": session_id},
        }

    def _pr_opened_span(self, pr_num, slice_num=None):
        attrs = {"pr": str(pr_num), "branch": f"feat/{slice_num}-x"}
        if slice_num:
            attrs["slice"] = str(slice_num)
        return {
            "v": 3, "ts": "2026-08-01T12:05:00Z", "trace_id": f"pr-{pr_num}",
            "span_id": f"o-{pr_num}", "kind": "pr_opened", "attrs": attrs,
        }

    def _verdict_span(self, pr_num):
        return {
            "v": 3, "ts": "2026-08-01T13:00:00Z", "trace_id": f"pr-{pr_num}",
            "span_id": f"v-{pr_num}", "kind": "verdict",
            "attrs": {"pr": str(pr_num), "critic": "reviewer", "round": "1", "verdict": "APPROVE"},
        }

    def _qa_verified_span(self, prd_num, verdict="PASS"):
        return {
            "v": 3, "ts": "2026-08-01T14:00:00Z", "trace_id": f"qa-{prd_num}",
            "span_id": f"q-{prd_num}", "kind": "qa_verified",
            "attrs": {"prd": str(prd_num), "verdict": verdict, "route": "command"},
        }


# ---------------------------------------------------------------------------
# SLICE-VS-PR
# ---------------------------------------------------------------------------

class TestSliceVsPr(_ReconcilerTestBase):
    def test_full_coverage_pass(self):
        health = _reimport("health")
        prs = [
            {"number": 4001, "mergedAt": "2026-08-01T10:00:00Z",
             "body": "Closes #5001\n\nsome body"},
        ]
        slice_issues = [{"number": 5001}]
        self._patch_gh(health, pr_list_result=_live(json.dumps(prs)),
                        issue_list_result=_live(json.dumps(slice_issues)))
        self._write_spans([
            self._dispatch_span(5001),
            self._pr_opened_span(4001, 5001),
        ])

        result = health.check_slice_vs_pr()

        self.assertEqual(result.get("id"), "SLICE-VS-PR")
        self.assertEqual(result.get("result"), "PASS", msg=result)
        self.assertIn("1/1", result.get("detail", ""))
        self.assertEqual(result.get("missing"), [])

    def test_missing_dispatch_span_fails_naming_exact_pr_and_slice(self):
        health = _reimport("health")
        prs = [
            {"number": 4002, "mergedAt": "2026-08-01T10:00:00Z",
             "body": "Closes #5002"},
        ]
        slice_issues = [{"number": 5002}]
        self._patch_gh(health, pr_list_result=_live(json.dumps(prs)),
                        issue_list_result=_live(json.dumps(slice_issues)))
        # dispatch span for #5002 was never emitted (the #918 bypass class).
        self._write_spans([
            self._pr_opened_span(4002, 5002),
        ])

        result = health.check_slice_vs_pr()

        self.assertEqual(result.get("result"), "FAIL", msg=result)
        detail = result.get("detail", "")
        self.assertIn("#4002", detail)
        self.assertIn("dispatch", detail.lower())
        self.assertIn("5002", detail)

    def test_missing_pr_opened_span_fails(self):
        health = _reimport("health")
        prs = [
            {"number": 4003, "mergedAt": "2026-08-01T10:00:00Z",
             "body": "Closes #5003"},
        ]
        slice_issues = [{"number": 5003}]
        self._patch_gh(health, pr_list_result=_live(json.dumps(prs)),
                        issue_list_result=_live(json.dumps(slice_issues)))
        # PR opened via raw `gh pr create`, never through tools/pipe/pr-open.
        self._write_spans([
            self._dispatch_span(5003),
        ])

        result = health.check_slice_vs_pr()

        self.assertEqual(result.get("result"), "FAIL", msg=result)
        detail = result.get("detail", "")
        self.assertIn("#4003", detail)
        self.assertIn("pr_opened", detail.lower())

    def test_non_slice_pr_out_of_scope(self):
        """A merged PR closing a non-slice-labeled issue (PRD-tier/trivial)
        is not a slice PR -- not evaluated by this reconciler at all."""
        health = _reimport("health")
        prs = [
            {"number": 4004, "mergedAt": "2026-08-01T10:00:00Z",
             "body": "Closes #6001"},  # 6001 is NOT slice-labeled
        ]
        slice_issues = [{"number": 5002}]  # different issue is the slice set
        self._patch_gh(health, pr_list_result=_live(json.dumps(prs)),
                        issue_list_result=_live(json.dumps(slice_issues)))
        self._write_spans([])

        result = health.check_slice_vs_pr()

        self.assertEqual(result.get("result"), "PASS", msg=result)
        self.assertEqual(result.get("checked"), 0)

    def test_pre_anchor_grandfathered(self):
        health = _reimport("health")
        prs = [
            {"number": 4005, "mergedAt": "2026-07-01T10:00:00Z",  # before anchor
             "body": "Closes #5005"},
        ]
        slice_issues = [{"number": 5005}]
        self._patch_gh(health, pr_list_result=_live(json.dumps(prs)),
                        issue_list_result=_live(json.dumps(slice_issues)))
        self._write_spans([])  # no spans at all -- still grandfathered

        result = health.check_slice_vs_pr()

        self.assertNotEqual(result.get("result"), "FAIL", msg=result)
        self.assertGreaterEqual(result.get("grandfathered", 0), 1)

    def test_gh_unavailable_warns(self):
        health = _reimport("health")
        self._patch_gh(health, pr_list_result=_computing())
        self._write_spans([])

        result = health.check_slice_vs_pr()

        self.assertEqual(result.get("result"), "WARN", msg=result)
        self.assertIn("unverifiable", result.get("detail", "").lower())

    def test_registered(self):
        health = _reimport("health")
        self.assertIn("SLICE-VS-PR", health.CHECK_REGISTRY)
        self.assertTrue(callable(getattr(health, "check_slice_vs_pr", None)))


# ---------------------------------------------------------------------------
# MERGED-WITHOUT-VERDICT
# ---------------------------------------------------------------------------

class TestMergedWithoutVerdict(_ReconcilerTestBase):
    def test_full_coverage_pass(self):
        health = _reimport("health")
        prs = [
            {"number": 7001, "mergedAt": "2026-08-01T10:00:00Z"},
            {"number": 7002, "mergedAt": "2026-08-01T11:00:00Z"},
        ]
        self._patch_gh(health, pr_list_result=_live(json.dumps(prs)))
        self._write_spans([
            self._verdict_span(7001),
            self._verdict_span(7002),
        ])

        result = health.check_merged_without_verdict()

        self.assertEqual(result.get("id"), "MERGED-WITHOUT-VERDICT")
        self.assertEqual(result.get("result"), "PASS", msg=result)
        self.assertIn("2/2", result.get("detail", ""))
        self.assertEqual(result.get("missing"), [])

    def test_missing_verdict_span_fails_naming_exact_pr(self):
        health = _reimport("health")
        prs = [
            {"number": 7003, "mergedAt": "2026-08-01T10:00:00Z"},
            {"number": 7004, "mergedAt": "2026-08-01T11:00:00Z"},
        ]
        self._patch_gh(health, pr_list_result=_live(json.dumps(prs)))
        # PR #7004 merged before the pr-merge verdict-floor extension landed.
        self._write_spans([
            self._verdict_span(7003),
        ])

        result = health.check_merged_without_verdict()

        self.assertEqual(result.get("result"), "FAIL", msg=result)
        detail = result.get("detail", "")
        self.assertIn("#7004", detail)
        self.assertNotIn("#7003", detail.replace("#7004", ""))
        self.assertEqual(result.get("missing"), ["7004"])

    def test_pre_anchor_grandfathered(self):
        health = _reimport("health")
        prs = [
            {"number": 7005, "mergedAt": "2026-07-01T10:00:00Z"},  # before anchor
        ]
        self._patch_gh(health, pr_list_result=_live(json.dumps(prs)))
        self._write_spans([])

        result = health.check_merged_without_verdict()

        self.assertNotEqual(result.get("result"), "FAIL", msg=result)
        self.assertGreaterEqual(result.get("grandfathered", 0), 1)
        self.assertIn("grandfathered", result.get("detail", "").lower())

    def test_gh_unavailable_warns(self):
        health = _reimport("health")
        self._patch_gh(health, pr_list_result=_computing())
        self._write_spans([])

        result = health.check_merged_without_verdict()

        self.assertEqual(result.get("result"), "WARN", msg=result)
        self.assertIn("unverifiable", result.get("detail", "").lower())

    def test_registered(self):
        health = _reimport("health")
        self.assertIn("MERGED-WITHOUT-VERDICT", health.CHECK_REGISTRY)
        self.assertTrue(callable(getattr(health, "check_merged_without_verdict", None)))


# ---------------------------------------------------------------------------
# CLOSED-PRD-VS-QA
# ---------------------------------------------------------------------------

class TestClosedPrdVsQa(_ReconcilerTestBase):
    def test_full_coverage_pass(self):
        health = _reimport("health")
        prds = [
            {"number": 8001, "closedAt": "2026-08-01T10:00:00Z"},
        ]
        self._patch_gh(health, issue_list_result=_live(json.dumps(prds)))
        self._write_spans([
            self._qa_verified_span(8001, "PASS"),
        ])

        result = health.check_closed_prd_vs_qa()

        self.assertEqual(result.get("id"), "CLOSED-PRD-VS-QA")
        self.assertEqual(result.get("result"), "PASS", msg=result)
        self.assertIn("1/1", result.get("detail", ""))

    def test_missing_qa_verified_pass_fails_naming_exact_prd(self):
        health = _reimport("health")
        prds = [
            {"number": 8002, "closedAt": "2026-08-01T10:00:00Z"},
        ]
        self._patch_gh(health, issue_list_result=_live(json.dumps(prds)))
        self._write_spans([])  # no qa_verified span at all

        result = health.check_closed_prd_vs_qa()

        self.assertEqual(result.get("result"), "FAIL", msg=result)
        detail = result.get("detail", "")
        self.assertIn("#8002", detail)
        self.assertEqual(result.get("missing"), ["8002"])

    def test_qa_verified_fail_verdict_does_not_count_as_covered(self):
        """A qa_verified span with verdict=FAIL (not PASS) must not satisfy
        the precondition -- mirrors tools/pipe/prd-close's own strict
        verdict=='PASS' predicate exactly."""
        health = _reimport("health")
        prds = [
            {"number": 8003, "closedAt": "2026-08-01T10:00:00Z"},
        ]
        self._patch_gh(health, issue_list_result=_live(json.dumps(prds)))
        self._write_spans([
            self._qa_verified_span(8003, "FAIL"),
        ])

        result = health.check_closed_prd_vs_qa()

        self.assertEqual(result.get("result"), "FAIL", msg=result)
        self.assertIn("#8003", result.get("detail", ""))

    def test_pre_anchor_grandfathered(self):
        health = _reimport("health")
        prds = [
            {"number": 8004, "closedAt": "2026-07-01T10:00:00Z"},  # before anchor
        ]
        self._patch_gh(health, issue_list_result=_live(json.dumps(prds)))
        self._write_spans([])

        result = health.check_closed_prd_vs_qa()

        self.assertNotEqual(result.get("result"), "FAIL", msg=result)
        self.assertGreaterEqual(result.get("grandfathered", 0), 1)

    def test_gh_unavailable_warns(self):
        health = _reimport("health")
        self._patch_gh(health, issue_list_result=_computing())
        self._write_spans([])

        result = health.check_closed_prd_vs_qa()

        self.assertEqual(result.get("result"), "WARN", msg=result)
        self.assertIn("unverifiable", result.get("detail", "").lower())

    def test_registered(self):
        health = _reimport("health")
        self.assertIn("CLOSED-PRD-VS-QA", health.CHECK_REGISTRY)
        self.assertTrue(callable(getattr(health, "check_closed_prd_vs_qa", None)))


if __name__ == "__main__":
    unittest.main()
