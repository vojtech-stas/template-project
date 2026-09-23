"""
Regression test for issue #1020 — Trail comparison still false-FAILs develop PRDs.

Facet tested:
  A) slice_no_pr false-FAIL: when slices have closedAt set (are genuinely closed)
     but GitHub did NOT auto-populate closingIssuesReferences (develop-base merge),
     the comparison must NOT emit slice_no_pr violations after the fix. This test
     calls compare() (the comparison.py engine function directly), not sub-helpers
     that bypass the violation detector.

  Facet B (non-blocking /api/comparison via server.serve_comparison()) was
  retired per ADR-0080 D1: the /api/comparison route and its server-side
  non-blocking wrapper are deleted along with the Architecture tab's embedded
  comparison panel. comparison.py itself (and its compare() engine function
  tested by facet A) is unaffected — collector.py still imports it for its
  CLI, per ADR-0055 D2's run_pass guarantee.

Root cause:
  closedAt IS set on slices (GitHub auto-closed via some mechanism, or manual),
  but closing_pr_number is None because _discover_develop_pr_slice_links scans
  --base develop only; once PRs are promoted to main the scan returns empty and
  the slice_no_pr detector fires.

Before fix (commit #1 — this file only):
  - compare() returns slice_no_pr violations for slices with closedAt set + no
    closing_pr_number → run_pass False.

After fix (commit #2):
  - compare() returns run_pass True / zero slice_no_pr for slices whose closing PR
    is discoverable via trail prs dict or develop-PR body scanning.

Runner: stdlib unittest (no top-level pytest).
  python -m pytest tests/test_trail_develop_aware_1020.py -v
  python -m unittest tests.test_trail_develop_aware_1020 -v
"""

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "dashboard"


def _inject_dashboard():
    s = str(DASHBOARD_DIR)
    if s not in sys.path:
        sys.path.insert(0, s)


# ---------------------------------------------------------------------------
# Synthetic trail — develop-PRD with slices that ARE closed but have
# no ClosedEvent.closer (develop-base merge; closingIssuesReferences empty).
# KEY: closedAt is NON-NULL (slices are genuinely closed).
# ---------------------------------------------------------------------------

_PRD_GQL_ISSUE = {
    "title": "PRD: develop-aware trail regression (#1020)",
    "createdAt": "2026-06-01T10:00:00Z",
    "closedAt": "2026-06-15T12:00:00Z",
    "labels": {"nodes": [{"name": "prd"}]},
    "comments": {"nodes": []},
    "subIssues": {
        "nodes": [
            {
                "number": 995,
                "title": "feat: slice 1 of PRD 993",
                "createdAt": "2026-06-02T10:00:00Z",
                "closedAt": "2026-06-05T15:00:00Z",  # NON-NULL — slice is closed
                "labels": {"nodes": [{"name": "slice"}]},
                "assignees": {"nodes": [{"login": "implementer-bot"}]},
                "comments": {"nodes": []},
                # No ClosedEvent.closer → closing_pr_number stays None initially
                "timelineItems": {"nodes": [
                    {
                        "__typename": "ClosedEvent",
                        "createdAt": "2026-06-05T15:00:00Z",
                        "closer": None,  # no PR closer (develop merge)
                    }
                ]},
            },
            {
                "number": 996,
                "title": "feat: slice 2 of PRD 993",
                "createdAt": "2026-06-03T10:00:00Z",
                "closedAt": "2026-06-06T15:00:00Z",  # NON-NULL
                "labels": {"nodes": [{"name": "slice"}]},
                "assignees": {"nodes": [{"login": "implementer-bot"}]},
                "comments": {"nodes": []},
                "timelineItems": {"nodes": [
                    {
                        "__typename": "ClosedEvent",
                        "createdAt": "2026-06-06T15:00:00Z",
                        "closer": None,
                    }
                ]},
            },
            {
                "number": 997,
                "title": "feat: slice 3 of PRD 993",
                "createdAt": "2026-06-04T10:00:00Z",
                "closedAt": "2026-06-07T15:00:00Z",  # NON-NULL
                "labels": {"nodes": [{"name": "slice"}]},
                "assignees": {"nodes": [{"login": "implementer-bot"}]},
                "comments": {"nodes": []},
                "timelineItems": {"nodes": [
                    {
                        "__typename": "ClosedEvent",
                        "createdAt": "2026-06-07T15:00:00Z",
                        "closer": None,
                    }
                ]},
            },
        ]
    },
}

_GQL_RESPONSE_DATA = {
    "repository": {
        "issue": _PRD_GQL_ISSUE,
    }
}

# Develop-base merged PRs with Closes #N in body; closingIssuesReferences EMPTY
_PR_998 = {
    "number": 998,
    "createdAt": "2026-06-05T10:00:00Z",
    "mergedAt": "2026-06-05T14:30:00Z",
    "headRefName": "fix/995-slice-one",
    "body": "Closes #995\n\n## Scope\nSlice 1 impl.\n\n## Verification\n- [x] pass",
    "closingIssuesReferences": [],  # empty — develop merge
    "comments": [
        {
            "createdAt": "2026-06-05T14:00:00Z",
            "body": "VERDICT: APPROVE\nROUND: 1\nCRITIC: reviewer\n",
        }
    ],
    "statusCheckRollup": [],
}

_PR_1001 = {
    "number": 1001,
    "createdAt": "2026-06-06T10:00:00Z",
    "mergedAt": "2026-06-06T14:30:00Z",
    "headRefName": "fix/996-slice-two",
    "body": "Closes #996\n\n## Scope\nSlice 2 impl.",
    "closingIssuesReferences": [],
    "comments": [
        {
            "createdAt": "2026-06-06T14:00:00Z",
            "body": "VERDICT: APPROVE\nROUND: 1\nCRITIC: reviewer\n",
        }
    ],
    "statusCheckRollup": [],
}

_PR_1002 = {
    "number": 1002,
    "createdAt": "2026-06-07T10:00:00Z",
    "mergedAt": "2026-06-07T14:30:00Z",
    "headRefName": "fix/997-slice-three",
    "body": "Closes #997\n\n## Scope\nSlice 3 impl.",
    "closingIssuesReferences": [],
    "comments": [
        {
            "createdAt": "2026-06-07T14:00:00Z",
            "body": "VERDICT: APPROVE\nROUND: 1\nCRITIC: reviewer\n",
        }
    ],
    "statusCheckRollup": [],
}

_DEVELOP_PRS_LIST = [
    {
        "number": 998,
        "body": _PR_998["body"],
        "mergedAt": _PR_998["mergedAt"],
        "closingIssuesReferences": [],
    },
    {
        "number": 1001,
        "body": _PR_1001["body"],
        "mergedAt": _PR_1001["mergedAt"],
        "closingIssuesReferences": [],
    },
    {
        "number": 1002,
        "body": _PR_1002["body"],
        "mergedAt": _PR_1002["mergedAt"],
        "closingIssuesReferences": [],
    },
]


def _build_trail_with_mocks(develop_scan_returns_empty=False):
    """Build a trail via collect_trail with gh layer mocked.

    Args:
        develop_scan_returns_empty: if True, simulate the promoted-to-main
          scenario: '--base develop' scan returns [] (PRs no longer appear on
          develop branch list after squash-merge to main), but '--base main' or
          no-base scan DOES return the PRs (the fix's fallback path).
          When False (default): develop scan returns the full PR list directly.
    """
    _inject_dashboard()
    import collector

    def fake_gh_graphql(query, variables, timeout=30):
        return _GQL_RESPONSE_DATA, ""

    def fake_gh_pr_view(pr_number, timeout=20):
        mapping = {998: _PR_998, 1001: _PR_1001, 1002: _PR_1002}
        pr = mapping.get(pr_number)
        return (pr, "") if pr else (None, "transient")

    def fake_run_gh(args, timeout=30):
        if "pr" in args and "list" in args:
            if develop_scan_returns_empty:
                # Promoted-to-main scenario:
                # --base develop → empty (PRs already on main)
                # --base main or no-base-filter → returns PRs (fallback path)
                if "--base" in args:
                    base_idx = args.index("--base")
                    base_val = args[base_idx + 1] if base_idx + 1 < len(args) else ""
                    if base_val == "develop":
                        return json.dumps([]), ""
                    # main-base or other base → return PRs (fix's fallback)
                    return json.dumps(_DEVELOP_PRS_LIST), ""
                # No --base flag → no-base-filter scan (final fallback)
                return json.dumps(_DEVELOP_PRS_LIST), ""
            return json.dumps(_DEVELOP_PRS_LIST), ""
        return None, "transient"

    with patch.object(collector, "_gh_graphql", side_effect=fake_gh_graphql), \
         patch.object(collector, "_gh_pr_view", side_effect=fake_gh_pr_view), \
         patch.object(collector, "_run_gh", side_effect=fake_run_gh), \
         patch.object(collector, "_repo_slug", return_value="owner/repo"), \
         patch.object(
             collector, "_gql_query_for_slug",
             return_value="query($n:Int!){repository(owner:\"o\",name:\"r\"){issue(number:$n){title}}}",
         ):
        trail = collector.collect_trail(993)
    return trail


# ---------------------------------------------------------------------------
# Group A: slice_no_pr false-FAIL via compare() — the real comparison-engine path
# ---------------------------------------------------------------------------

class TestSliceNoPrDevelopAware(unittest.TestCase):
    """compare() must NOT emit slice_no_pr when develop-base PRs close the slices.

    Before fix: when develop_scan_returns_empty=False (develop list works),
      the collector wires closing_pr_number, but _detect_slice_no_pr could still
      fire if the mapping is incomplete.
      When promoted-to-main (develop_scan_returns_empty=True), _detect_slice_no_pr
      DOES fire → run_pass False (the pre-fix failure mode tested here).
    After fix: even when develop scan returns empty, the detector falls back to
      trail prs closing_issues OR a main-branch scan, and run_pass is True.
    """

    def _get_spec(self):
        _inject_dashboard()
        from comparison import get_spec_for_compare
        return get_spec_for_compare()

    def test_detect_slice_no_pr_prs_fallback(self):
        """_detect_slice_no_pr must use prs closing_issues as fallback (fix #1020).

        Construct a minimal trail where closing_pr_number is None (not populated
        by collector) but a PR in trail.prs lists the slice in closing_issues.
        The detector must NOT emit a violation in this case.

        This tests the fix's secondary fallback in _detect_slice_no_pr directly.
        """
        _inject_dashboard()
        from comparison import _detect_slice_no_pr

        trail = {
            "slices": [
                {
                    "number": 995,
                    "title": "feat: slice 1",
                    "closed_at": "2026-06-05T15:00:00Z",
                    "closing_pr_number": None,  # not set by collector
                }
            ],
            "prs": {
                "998": {
                    "number": 998,
                    "closing_issues": [995],  # body-parsed fallback populated this
                    "merged_at": "2026-06-05T14:30:00Z",
                }
            },
        }

        violations = _detect_slice_no_pr(trail)
        has_slice_no_pr = any(v["type"] == "slice_no_pr" for v in violations)
        self.assertFalse(
            has_slice_no_pr,
            "AFTER fix: slice_no_pr must NOT fire when prs closing_issues covers "
            f"the slice. violations={violations}"
        )

    def test_compare_no_slice_no_pr_with_develop_scan(self):
        """compare() must return zero slice_no_pr and run_pass True.

        The trail is built with develop_scan_returns_empty=False: the collector
        discovers PRs via _discover_develop_pr_slice_links and populates
        closing_pr_number for all slices. compare() must not emit slice_no_pr.

        This test exercises the FULL compare() path (not sub-helpers), so it
        catches any violation detector that fires despite closing_pr_number
        being set (the #1007 test-validity gap addressed in #1020).
        """
        _inject_dashboard()
        from comparison import compare

        trail = _build_trail_with_mocks(develop_scan_returns_empty=False)
        spec = self._get_spec()
        report = compare(spec, trail)

        slice_no_pr_violations = [
            v for v in report.get("violations", [])
            if v["type"] == "slice_no_pr"
        ]
        self.assertEqual(
            len(slice_no_pr_violations), 0,
            f"Expected 0 slice_no_pr violations, got "
            f"{len(slice_no_pr_violations)}: {slice_no_pr_violations}\n"
            f"Trail slices: "
            f"{[(s['number'], s.get('closing_pr_number')) for s in trail.get('slices', [])]}\n"
            f"Trail prs: {list(trail.get('prs', {}).keys())}"
        )
        self.assertTrue(
            report["run_pass"],
            f"run_pass must be True for a complete develop PRD. "
            f"Edges: {[(k, v['state']) for k, v in report.get('edges', {}).items()]}\n"
            f"Violations: {report.get('violations', [])}"
        )

    def test_compare_no_slice_no_pr_promoted_to_main(self):
        """AFTER fix: even when develop scan is empty (promoted to main),
        compare() must return zero slice_no_pr violations and run_pass True.

        This is the KEY regression test for #1020. It FAILS before the fix
        (because _discover_develop_pr_slice_links returns empty, closing_pr_number
        stays None, _detect_slice_no_pr fires) and PASSES after the fix.

        After fix, the detector falls back to trail prs closing_issues check
        or a broader PR scan (--base main fallback or no-base filter).
        """
        _inject_dashboard()
        from comparison import compare

        trail = _build_trail_with_mocks(develop_scan_returns_empty=True)
        spec = self._get_spec()
        report = compare(spec, trail)

        slice_no_pr_violations = [
            v for v in report.get("violations", [])
            if v["type"] == "slice_no_pr"
        ]
        self.assertEqual(
            len(slice_no_pr_violations), 0,
            f"AFTER FIX: expected 0 slice_no_pr violations even when develop "
            f"scan empty (promoted-to-main scenario), got "
            f"{len(slice_no_pr_violations)}: {slice_no_pr_violations}\n"
            f"Trail slices closing_pr_number: "
            f"{[s.get('closing_pr_number') for s in trail.get('slices', [])]}\n"
            f"Trail prs keys: {list(trail.get('prs', {}).keys())}"
        )
        self.assertTrue(
            report["run_pass"],
            f"run_pass must be True after fix. "
            f"Violations: {report.get('violations', [])}"
        )

    def test_e_slice_pr_confirmed_via_compare(self):
        """E-SLICE-PR must be 'confirmed' in the full compare() output."""
        _inject_dashboard()
        from comparison import compare

        trail = _build_trail_with_mocks(develop_scan_returns_empty=False)
        spec = self._get_spec()
        report = compare(spec, trail)

        edge = report.get("edges", {}).get("E-SLICE-PR", {})
        self.assertEqual(
            edge.get("state"), "confirmed",
            f"E-SLICE-PR must be confirmed, got: {edge}"
        )

    def test_e_pr_review_confirmed_via_compare(self):
        """E-PR-REVIEW must be 'confirmed' in the full compare() output."""
        _inject_dashboard()
        from comparison import compare

        trail = _build_trail_with_mocks(develop_scan_returns_empty=False)
        spec = self._get_spec()
        report = compare(spec, trail)

        edge = report.get("edges", {}).get("E-PR-REVIEW", {})
        self.assertEqual(
            edge.get("state"), "confirmed",
            f"E-PR-REVIEW must be confirmed, got: {edge}"
        )

    def test_e_review_merge_confirmed_via_compare(self):
        """E-REVIEW-MERGE must be 'confirmed' in the full compare() output."""
        _inject_dashboard()
        from comparison import compare

        trail = _build_trail_with_mocks(develop_scan_returns_empty=False)
        spec = self._get_spec()
        report = compare(spec, trail)

        edge = report.get("edges", {}).get("E-REVIEW-MERGE", {})
        self.assertEqual(
            edge.get("state"), "confirmed",
            f"E-REVIEW-MERGE must be confirmed, got: {edge}"
        )


if __name__ == "__main__":
    unittest.main()
