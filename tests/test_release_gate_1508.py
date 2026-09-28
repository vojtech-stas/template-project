"""
tests/test_release_gate_1508.py

Regression tests for PRD #1501 slice #1508 — the `RELEASE-GATE` health
check (ADR-0090 D1) and the `--check` printer's opt-in `subject` key.

GitHub is faked at the health seam (`_health_gh_fetch`) or, for the
no-stall and forced-unconfirmed cases, at the gh_cache layer beneath it.
Nothing here touches the network or `.claude/logs/` (rule #21).

Covers PRD #1501 §2:
  12 (release_gate_holds): an open non-residual issue in the lowest open
     version milestone, an open non-residual unmilestoned `bug`, or an
     open non-residual issue without exactly one class label holds the
     gate: first line `^WARN: RELEASE-GATE .*hold`, stating the count.
  13 (release_gate_pass): with none of those and GitHub answering, the
     first line is `^PASS: RELEASE-GATE <V>` and names every residual it
     excluded.
  14 (with slice constraint 2/3): an unconfirmed read, or a listing that
     reaches its limit (release_gate_truncated), reads `unconfirmed`,
     never PASS.
Slice constraint 1: a result carrying `subject` prints
`<VERDICT>: <ID> <subject> — <detail>`; a result without it prints
byte-identically to before.
"""
import importlib
import json
import os
import re
import sys
import time
import unittest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_TESTS_DIR)
_DASHBOARD_DIR = os.path.join(_REPO_ROOT, "dashboard")
if _DASHBOARD_DIR not in sys.path:
    sys.path.insert(0, _DASHBOARD_DIR)

_ENV_VAR = "RELEASE_GATE_VERSION"


def _reimport_health():
    if "health" in sys.modules:
        del sys.modules["health"]
    return importlib.import_module("health")


class _FakeGhResult:
    def __init__(self, value, source, fetched_at="2026-01-01T00:00:00+00:00"):
        self.value = value
        self.fetched_at = fetched_at
        self.source = source


def _issue(num, labels=(), milestone=None):
    return {
        "number": num,
        "labels": [{"name": l} for l in labels],
        "milestone": ({"title": milestone} if milestone else None),
    }


_MILESTONES = [
    {"number": 2, "title": "v1.1", "state": "open"},
    {"number": 1, "title": "v1.0", "state": "open"},
    {"number": 7, "title": "Backlog grooming", "state": "open"},
]


class _Seam:
    """Records every seam call and answers the milestone read and the
    issue listing from fixed tables."""

    def __init__(self, issues, milestones=_MILESTONES, issues_answer=None,
                 milestones_answer=None):
        self.calls = []
        self.issues_answer = issues_answer or (0, json.dumps(issues), "live")
        self.milestones_answer = milestones_answer or (0, json.dumps(milestones), "live")

    def __call__(self, args, ttl=0, timeout=0, with_source=False):
        args = list(args)
        self.calls.append(args)
        if args[:2] == ["issue", "list"]:
            rc, out, src = self.issues_answer
        elif args and args[0] == "api" and any("milestones" in a for a in args):
            rc, out, src = self.milestones_answer
        else:
            rc, out, src = (1, "", "computing")
        return (rc, out, src) if with_source else (rc, out)


class _GateCase(unittest.TestCase):
    def setUp(self):
        self._saved = os.environ.pop(_ENV_VAR, None)
        self.health = _reimport_health()

    def tearDown(self):
        os.environ.pop(_ENV_VAR, None)
        if self._saved is not None:
            os.environ[_ENV_VAR] = self._saved
        _reimport_health()

    def gate(self, seam):
        self.health._health_gh_fetch = seam
        result = self.health.check_release_gate()
        return result, self.health._format_cli_line("RELEASE-GATE", result)


class TestReleaseGateHolds(_GateCase):
    """Criterion 12: five hold cases, each alone in an otherwise clean state."""

    def _assert_holds(self, issues, count):
        result, line = self.gate(_Seam(issues))
        self.assertEqual(result["result"], "WARN", line)
        self.assertRegex(line, r"^WARN: RELEASE-GATE .*hold")
        self.assertIn(f"hold: {count} ", line)
        return line

    def test_release_gate_holds_bug_in_milestone(self):
        line = self._assert_holds([_issue(11, ["bug"], "v1.0")], 1)
        self.assertRegex(line, r"^WARN: RELEASE-GATE v1\.0 ")
        self.assertIn("#11", line)

    def test_release_gate_holds_admitted_feature_in_milestone(self):
        self._assert_holds([_issue(12, ["feature", "prd"], "v1.0")], 1)

    def test_release_gate_holds_unmilestoned_bug(self):
        line = self._assert_holds([_issue(13, ["bug"])], 1)
        self.assertIn("#13", line)

    def test_release_gate_holds_unclassified_issue(self):
        result, line = self.gate(_Seam([_issue(14, ["captured"], "v1.1")]))
        self.assertEqual(result["result"], "WARN", line)
        self.assertRegex(line, r"^WARN: RELEASE-GATE .*hold")
        self.assertIn("1 unclassified", line)
        self.assertIn("#14", line)

    def test_release_gate_holds_both_class_labels_is_unclassified(self):
        result, line = self.gate(_Seam([_issue(15, ["bug", "feature"], "v1.1")]))
        self.assertEqual(result["result"], "WARN", line)
        self.assertIn("1 unclassified", line)

    def test_release_gate_holds_bug_with_needs_human_check(self):
        """A bug escalated to the owner keeps `bug`, so it is not a
        residual and holds the gate."""
        line = self._assert_holds([_issue(16, ["bug", "needs-human-check"])], 1)
        self.assertIn("#16", line)

    def test_release_gate_holds_counts_every_holding_issue(self):
        issues = [
            _issue(21, ["bug"], "v1.0"),
            _issue(22, ["feature"], "v1.0"),
            _issue(23, ["bug"]),
            _issue(24, ["feature"], "v1.1"),  # the next version: never holds v1.0
        ]
        line = self._assert_holds(issues, 3)
        self.assertNotIn("#24", line)


class TestReleaseGatePass(_GateCase):
    def test_release_gate_pass_names_every_excluded_residual(self):
        issues = [
            _issue(31, ["needs-human-check"]),                       # QA residual
            _issue(32, ["feature", "needs-human-check"], "v1.0"),    # policy question
            _issue(33, ["feature"], "v1.1"),
            _issue(34, ["bug"], "v0.9-archive"),
        ]
        # #34 is a bug in a milestone other than v1.0: it is neither in
        # the gated milestone nor unmilestoned, so it does not hold.
        result, line = self.gate(_Seam(issues))
        self.assertEqual(result["result"], "PASS", line)
        self.assertRegex(line, r"^PASS: RELEASE-GATE v1\.0 ")
        self.assertIn("#31", line)
        self.assertIn("#32", line)

    def test_release_gate_pass_picks_the_lowest_version_numerically(self):
        milestones = [
            {"number": 3, "title": "v1.10", "state": "open"},
            {"number": 4, "title": "v1.2", "state": "open"},
        ]
        issues = [_issue(41, ["bug"], "v1.10")]
        result, line = self.gate(_Seam(issues, milestones=milestones))
        self.assertRegex(line, r"^PASS: RELEASE-GATE v1\.2 ")

    def test_release_gate_pass_env_override_selects_the_version(self):
        os.environ[_ENV_VAR] = "v1.1"
        result, line = self.gate(_Seam([_issue(51, ["feature"], "v1.1")]))
        self.assertRegex(line, r"^WARN: RELEASE-GATE v1\.1 .*hold")

    def test_release_gate_pass_reads_are_unfiltered_and_bounded(self):
        seam = _Seam([])
        self.gate(seam)
        issue_calls = [c for c in seam.calls if c[:2] == ["issue", "list"]]
        ms_calls = [c for c in seam.calls if c and c[0] == "api"]
        self.assertEqual(len(issue_calls), 1, seam.calls)
        self.assertEqual(len(ms_calls), 1, seam.calls)
        self.assertFalse(self.health._args_apply_label_filter(issue_calls[0]))
        self.assertIn("--limit", issue_calls[0])
        self.assertEqual(issue_calls[0][issue_calls[0].index("--limit") + 1], "1000")
        self.assertIn("--paginate", ms_calls[0])


class TestReleaseGateUnconfirmed(_GateCase):
    def test_release_gate_unconfirmed_milestone_read(self):
        seam = _Seam([], milestones_answer=(1, "", "computing"))
        result, line = self.gate(seam)
        self.assertEqual(result["result"], "WARN")
        self.assertEqual(line.split(" — ")[0], "WARN: RELEASE-GATE")
        self.assertIn("unconfirmed (source=computing)", line)

    def test_release_gate_unconfirmed_issue_read(self):
        seam = _Seam([], issues_answer=(1, "", "stale"))
        result, line = self.gate(seam)
        self.assertEqual(result["result"], "WARN")
        self.assertRegex(line, r"^WARN: RELEASE-GATE .*unconfirmed \(source=stale\)")

    def test_release_gate_unconfirmed_unparsable_payload(self):
        seam = _Seam([], issues_answer=(0, "<html>rate limited</html>", "live"))
        result, line = self.gate(seam)
        self.assertEqual(result["result"], "WARN")
        self.assertIn("unconfirmed (source=live", line)

    def test_release_gate_no_version_milestone_is_not_pass(self):
        seam = _Seam([], milestones=[{"number": 7, "title": "Backlog grooming"}])
        result, line = self.gate(seam)
        self.assertEqual(result["result"], "WARN", line)
        self.assertNotIn("PASS", line)

    def test_release_gate_no_stall_when_gh_cache_is_computing(self):
        self.health._gh_fetch_impl = lambda args, *, ttl, timeout: _FakeGhResult(None, "computing")
        self.health._GH_CACHE_AVAILABLE = True
        start = time.monotonic()
        result = self.health.check_release_gate()
        self.assertLess(time.monotonic() - start, 10.0)
        self.assertEqual(result["result"], "WARN")
        self.assertIn("unconfirmed (source=computing)", result["detail"])


class TestReleaseGateTruncated(_GateCase):
    def test_release_gate_truncated_listing_is_unconfirmed_never_pass(self):
        issues = [_issue(n, ["feature"], "v1.1") for n in range(1, 1001)]
        result, line = self.gate(_Seam(issues))
        self.assertEqual(result["result"], "WARN", line[:200])
        self.assertIn("unconfirmed (truncated at 1000)", line)
        self.assertNotRegex(line, r"^PASS")


class TestCliSubjectPrinting(unittest.TestCase):
    def setUp(self):
        self.health = _reimport_health()

    def test_release_gate_cli_line_without_subject_is_byte_identical(self):
        f = self.health._format_cli_line
        self.assertEqual(
            f("RELEASE-READY", {"result": "PASS", "detail": "all six conditions hold"}),
            "PASS: RELEASE-READY — all six conditions hold",
        )
        self.assertEqual(f("X", {"result": "WARN", "detail": ""}), "WARN: X")
        self.assertEqual(f("X", {}), "UNKNOWN: X")

    def test_release_gate_cli_line_with_subject(self):
        f = self.health._format_cli_line
        self.assertEqual(
            f("RELEASE-GATE", {"result": "PASS", "subject": "v1.0", "detail": "d"}),
            "PASS: RELEASE-GATE v1.0 — d",
        )

    def test_release_gate_registered_and_grouped(self):
        h = self.health
        self.assertIn("RELEASE-GATE", h.CHECK_REGISTRY)
        self.assertEqual(h._CHECK_GROUP_MAP.get("RELEASE-GATE"), "Release gates")
        self.assertEqual(h.PURPOSE_GROUP_MAP.get("RELEASE-GATE"), "Release gates")
        self.assertTrue(h._description_from_docstring(h.check_release_gate).strip())
        self.assertIn("check_release_gate()", h.__doc__)


if __name__ == "__main__":
    unittest.main()
