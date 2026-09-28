"""
tests/test_record_vs_gh_1081.py

Regression / new-feature tests for slice #1081 (PRD #1075 criterion 3) — the
RECORD-VS-GH health check: reconcile recorded pr_merged v3 spans against gh's
merged-PR ground truth on develop.

Test-first discipline (rule #13 rider / ADR-0067 D3 shape, applied here to a
new-feature slice per the slice's own instruction): this commit lands BEFORE
`check_record_vs_gh()` exists in dashboard/health.py.

FAILS before impl: every test below fails (AttributeError / KeyError) because
health.check_record_vs_gh does not exist yet.
PASSES after impl: dashboard/health.py gains check_record_vs_gh() + a
'RECORD-VS-GH' CHECK_REGISTRY entry satisfying every assertion here.

Covers (per slice #1081 body):
  (a) full coverage -> PASS with counts
  (b) inject one post-window unwrapped merge -> FAIL naming the exact PR
  (c) pre-window merges ignored (grandfather)
  (d) gh unavailable -> honest unverifiable WARN, never false-PASS/FAIL

Stubbing seam (mirrors tests/test_gh_cache_health_firing_996.py): gh is
stubbed at the health._gh_fetch_impl layer (the imported gh_cache.gh_fetch
reference) — no real subprocess/gh calls. The trace-v3.jsonl fixture log is
supplied via TRACE_LOG_OVERRIDE (tools/trace.py's own test seam) so
check_record_vs_gh's read of recorded pr_merged spans is fully controlled.

Bind-forward window anchor (updated slice #1514 / ADR-0089 D4): the window
is now the fixed `GRANDFATHER_UNTIL['RECORD-VS-GH']` instant in
dashboard/_constants.py (2026-08-02T02:11:00Z, PR #1089's own mergedAt) —
there is no override seam anymore (the `_RECORD_VS_GH_ANCHOR_TS_OVERRIDE`
test seam was retired with the sha-resolution code it protected). Fixture
PRs below use dates safely either side of that fixed instant instead of an
injected anchor.

Runner: stdlib unittest + pytest compatible.
  python -m pytest tests/test_record_vs_gh_1081.py -v
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
# Shared gh-stub helpers (mirrors test_gh_cache_health_firing_996.py's _GhResult)
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


# The fixed GRANDFATHER_UNTIL['RECORD-VS-GH'] instant (dashboard/_constants.py,
# ADR-0089 D4): 2026-08-02T02:11:00Z. Every "post-window" fixture PR below
# merges strictly after it; every "pre-window" fixture PR merges before it.
_ANCHOR_TS = "2026-08-02T02:11:00+00:00"


class _RecordVsGhTestBase(unittest.TestCase):
    """Shared setUp/tearDown: env-var + module-attribute isolation, temp trace log."""

    def setUp(self):
        self._old_env = {}
        for k in ("TRACE_LOG_OVERRIDE",):
            self._old_env[k] = os.environ.get(k)
        self._tmpdir = tempfile.mkdtemp(prefix="record_vs_gh_test_")
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

    def _write_spans(self, spans):
        with open(self.log_path, "w", encoding="utf-8") as f:
            for s in spans:
                f.write(json.dumps(s) + "\n")

    def _patch_gh(self, health_mod, gh_result):
        health_mod._gh_fetch_impl = lambda args, ttl, timeout: gh_result
        health_mod._GH_CACHE_AVAILABLE = True

    def _pr_merged_span(self, pr_num, sha="deadbeef"):
        return {
            "v": 3, "ts": "2026-08-01T12:00:00Z", "trace_id": f"pr-{pr_num}",
            "span_id": f"s-{pr_num}", "kind": "pr_merged",
            "attrs": {"pr": str(pr_num), "sha": sha},
        }


# ---------------------------------------------------------------------------
# (a) full coverage -> PASS with counts
# ---------------------------------------------------------------------------

class TestFullCoveragePass(_RecordVsGhTestBase):
    def test_full_coverage_pass_with_counts(self):
        health = _reimport("health")
        prs = [
            {"number": 2001, "mergedAt": "2026-08-03T10:00:00Z",
             "mergeCommit": {"oid": "sha2001"}},
            {"number": 2002, "mergedAt": "2026-08-03T11:00:00Z",
             "mergeCommit": {"oid": "sha2002"}},
        ]
        self._patch_gh(health, _live(json.dumps(prs)))
        self._write_spans([
            self._pr_merged_span(2001, "sha2001"),
            self._pr_merged_span(2002, "sha2002"),
        ])

        result = health.check_record_vs_gh()

        self.assertEqual(result.get("id"), "RECORD-VS-GH")
        self.assertEqual(
            result.get("result"), "PASS",
            f"expected PASS with full span coverage; got: {result}",
        )
        detail = result.get("detail", "")
        self.assertIn("2/2", detail, f"detail must show N/N counts; got: {detail!r}")
        self.assertEqual(result.get("missing"), [])


# ---------------------------------------------------------------------------
# (b) inject one post-window unwrapped merge -> FAIL naming the exact PR
# ---------------------------------------------------------------------------

class TestUnwrappedMergeFails(_RecordVsGhTestBase):
    def test_one_missing_span_fails_naming_exact_pr(self):
        health = _reimport("health")
        prs = [
            {"number": 3001, "mergedAt": "2026-08-03T10:00:00Z",
             "mergeCommit": {"oid": "sha3001"}},
            {"number": 3002, "mergedAt": "2026-08-03T11:00:00Z",
             "mergeCommit": {"oid": "sha3002"}},
        ]
        self._patch_gh(health, _live(json.dumps(prs)))
        # PR #3002 merged but its span was never emitted (unwrapped raw-gh merge).
        self._write_spans([
            self._pr_merged_span(3001, "sha3001"),
        ])

        result = health.check_record_vs_gh()

        self.assertEqual(
            result.get("result"), "FAIL",
            f"expected FAIL when a post-window merge lacks a span; got: {result}",
        )
        detail = result.get("detail", "")
        self.assertIn(
            "#3002", detail,
            f"FAIL detail must name the exact PR lacking a span; got: {detail!r}",
        )
        self.assertNotIn(
            "#3001", detail.replace("#3002", ""),
            f"FAIL detail must not falsely implicate the covered PR; got: {detail!r}",
        )
        self.assertEqual(result.get("missing"), ["3002"])


# ---------------------------------------------------------------------------
# (c) pre-window merges ignored (grandfather)
# ---------------------------------------------------------------------------

class TestPreWindowGrandfathered(_RecordVsGhTestBase):
    def test_pre_window_merge_without_span_is_grandfathered_not_failed(self):
        health = _reimport("health")
        prs = [
            # Merged BEFORE the anchor timestamp -> must be grandfathered,
            # even though it has no recorded span.
            {"number": 1000, "mergedAt": "2026-07-01T09:00:00Z",
             "mergeCommit": {"oid": "sha1000"}},
        ]
        self._patch_gh(health, _live(json.dumps(prs)))
        self._write_spans([])  # no spans at all

        result = health.check_record_vs_gh()

        self.assertNotEqual(
            result.get("result"), "FAIL",
            f"a pre-window merge lacking a span must be grandfathered, not FAIL; got: {result}",
        )
        self.assertEqual(result.get("missing"), [])
        self.assertGreaterEqual(
            result.get("grandfathered", 0), 1,
            f"expected grandfathered count >= 1; got: {result}",
        )
        detail = result.get("detail", "")
        self.assertIn("grandfathered", detail.lower())


# ---------------------------------------------------------------------------
# (d) gh unavailable -> honest unverifiable WARN
# ---------------------------------------------------------------------------

class TestGhUnavailableHonestWarn(_RecordVsGhTestBase):
    def test_gh_unavailable_returns_unverifiable_warn(self):
        health = _reimport("health")
        self._patch_gh(health, _computing())
        self._write_spans([])

        result = health.check_record_vs_gh()

        self.assertEqual(
            result.get("result"), "WARN",
            f"gh unavailable must degrade to WARN, never FAIL/PASS; got: {result}",
        )
        detail = result.get("detail", "")
        self.assertIn(
            "unverifiable", detail.lower(),
            f"WARN detail must say 'unverifiable' (honest degrade); got: {detail!r}",
        )
        self.assertIn("gh", detail.lower())


# ---------------------------------------------------------------------------
# Registry + CLI wiring
# ---------------------------------------------------------------------------

class TestRecordVsGhRegistered(unittest.TestCase):
    def test_registered_in_check_registry(self):
        health = _reimport("health")
        self.assertIn(
            "RECORD-VS-GH", health.CHECK_REGISTRY,
            "CHECK_REGISTRY must include 'RECORD-VS-GH' (PRD #1075 criterion 3 / slice #1081).",
        )

    def test_function_exists_and_callable(self):
        health = _reimport("health")
        self.assertTrue(
            callable(getattr(health, "check_record_vs_gh", None)),
            "health.check_record_vs_gh must exist and be callable.",
        )


if __name__ == "__main__":
    unittest.main()
