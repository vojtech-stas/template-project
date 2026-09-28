"""
tests/test_capture_shape_pr_leg_1509.py — slice #1509 (PRD #1501 §2 criteria
38/39, constraint 2, Truncation A8) regression tests.

Root cause (ADR-0090 D5): rule #13's record now rides the fixing PR's body
in the same run — it becomes a `captured`+`root-cause` issue only when the
mistake is unfixable in the run. CAPTURE-SHAPE's issue-only leg (ADR-0063)
can no longer see most root-cause records, so it gains a PR leg: a
`pr-records: <conforming>/<total>` detail part over merged PRs after T0
(the committer time of BASE, `2026-09-23T14:58:59Z`).

This file is committed BEFORE the implementation commit (rule #13's
regression rider / R-PROVE) and fails against pre-change `health.py`, which
has no PR leg at all — the tests below assert on `pr-records:` appearing in
`detail`, which does not exist yet.

All tests stub the seam (`health._health_gh_fetch`) directly, mirroring
tests/test_gh_provenance_class_sweep_1496.py's established pattern — never
`.claude/logs/*` (rule #21).

Runner: pytest (also discoverable via `python -m pytest tests/`).
"""

import importlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
_DASHBOARD_DIR = str(REPO_ROOT / "dashboard")
if _DASHBOARD_DIR not in sys.path:
    sys.path.insert(0, _DASHBOARD_DIR)


def _reimport_health():
    if "health" in sys.modules:
        del sys.modules["health"]
    return importlib.import_module("health")


def _stub_router(route):
    """A seam stub that dispatches on `args` via `route(args) -> (rc, out,
    source)`, returning the 2-tuple prefix when with_source is False."""

    def _stub(args, ttl=0, timeout=0, with_source=False):
        rc, out, source = route(args)
        return (rc, out, source) if with_source else (rc, out)

    return _stub


_EMPTY_ISSUES_ROUTE_PREFIX = ("issue", "list")

_CONFORMING_BODY = (
    "**Symptom:** it broke.\n\n**Root cause:** a bad assumption.\n\n"
    "**Proposed:** fix the assumption."
)

_MISSING_PROPOSED_BODY = (
    "**Symptom:** it broke.\n\n**Root cause:** a bad assumption.\n"
)


def _route_issues_empty_pr_list(pr_payload):
    def _route(args):
        if args[:2] == list(_EMPTY_ISSUES_ROUTE_PREFIX):
            return (0, "[]", "live")
        if args[:2] == ["pr", "list"]:
            return (0, json.dumps(pr_payload), "live")
        raise AssertionError(f"unexpected gh call: {args}")

    return _route


def test_capture_shape_pr_records():
    """Criterion 38: a merged PR after T0 with all three headings and the
    `root-cause` label counts as conforming, reported `pr-records: 1/1`."""
    health = _reimport_health()
    prs = [{
        "number": 9101,
        "body": _CONFORMING_BODY,
        "labels": [{"name": "root-cause"}],
        "mergedAt": "2026-09-24T10:00:00Z",
    }]
    health._health_gh_fetch = _stub_router(_route_issues_empty_pr_list(prs))
    r = health.check_capture_shape()
    assert "pr-records: 1/1" in r["detail"], r["detail"]
    assert r["pr_non_conformers"] == [], r


def test_capture_shape_pr_nonconformers():
    """Criterion 39: a merged PR after T0 with `**Root cause:**` but missing
    `**Proposed:**` (and the label) is named as a non-conformer, and still
    counted in <total>."""
    health = _reimport_health()
    prs = [{
        "number": 9102,
        "body": _MISSING_PROPOSED_BODY,
        "labels": [],
        "mergedAt": "2026-09-24T10:00:00Z",
    }]
    health._health_gh_fetch = _stub_router(_route_issues_empty_pr_list(prs))
    r = health.check_capture_shape()
    assert "pr-records: 0/1" in r["detail"], r["detail"]
    assert 9102 in r["pr_non_conformers"], r
    assert "#9102" in r["detail"], r["detail"]
    assert r["result"] == "WARN", r


def test_capture_shape_pr_truncated():
    """Truncation (A8): the read hits the explicit --limit and every
    returned PR merged after T0 — older PRs may be hiding past the cap, so
    the leg reads unconfirmed (truncated) and the row is non-PASS."""
    health = _reimport_health()
    limit = health._CAPTURE_SHAPE_PR_LEG_LIMIT
    prs = [{
        "number": 9200 + i,
        "body": _CONFORMING_BODY,
        "labels": [{"name": "root-cause"}],
        "mergedAt": "2026-09-24T10:00:00Z",
    } for i in range(limit)]
    health._health_gh_fetch = _stub_router(_route_issues_empty_pr_list(prs))
    r = health.check_capture_shape()
    assert "pr-records: unconfirmed (truncated)" in r["detail"], r["detail"]
    assert r["result"] == "WARN", r
