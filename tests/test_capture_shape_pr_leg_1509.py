"""
tests/test_capture_shape_pr_leg_1509.py — slice #1509 (PRD #1501 §2 criteria
38/39, constraint 2, Truncation A8) regression tests.

Root cause (ADR-0090 D5): rule #13's record now rides the fixing PR's body
in the same run — it becomes a `captured`+`root-cause` issue only when the
mistake is unfixable in the run. CAPTURE-SHAPE's issue-only leg (ADR-0063)
can no longer see most root-cause records, so it gains a PR leg: a
`pr-records: <conforming>/<total>` detail part over merged PRs after T0
(the committer time of BASE, `2026-09-23T14:58:59Z`).

Round-3 fix (reviewer BLOCK round 2, R-TESTS): every PR-leg test used to
route `issue list` to `[]`, so `total_rc == 0` forced the overall row to
WARN regardless of what the PR leg did — `result != "PASS"` / `== "WARN"`
assertions could never fail no matter how broken the PR leg was. Every test
below now shares `_route_pr_leg()`, a fixture where the issue leg alone is
PASS-worthy (one conforming `root-cause` issue, an empty `captured` list),
so a WARN or non-PASS result is driven by the PR leg under test, not by an
empty issue leg. A positive control (`test_capture_shape_pr_records`)
proves the fixture itself yields PASS when the PR leg also conforms.

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
    source)` (or `route(args)` may raise, to simulate `_health_gh_fetch`
    itself raising), returning the 2-tuple prefix when with_source is
    False."""

    def _stub(args, ttl=0, timeout=0, with_source=False):
        rc, out, source = route(args)
        return (rc, out, source) if with_source else (rc, out)

    return _stub


_CONFORMING_BODY = (
    "**Symptom:** it broke.\n\n**Root cause:** a bad assumption.\n\n"
    "**Proposed:** fix the assumption."
)

_MISSING_PROPOSED_BODY = (
    "**Symptom:** it broke.\n\n**Root cause:** a bad assumption.\n"
)

# The issue leg alone is PASS-worthy: one conforming root-cause issue, no
# unlabeled captured candidates. Every test below varies only the PR-list
# response against this fixed, non-vacuous issue leg (round-3 fix).
_PASSING_RC_ISSUES = [{
    "number": 9400,
    "body": _CONFORMING_BODY,
    "labels": [{"name": "root-cause"}],
}]


def _route_pr_leg(pr_response):
    """Issue leg: one conforming `root-cause` issue + an empty `captured`
    list (PASS-worthy on its own). `pr_response` is a zero-arg callable
    returning `(rc, out, source)` for the `pr list` call — or it may raise,
    to simulate `_health_gh_fetch` raising outright for that call."""

    def _route(args):
        if args[:2] == ["issue", "list"]:
            label = args[3] if len(args) > 3 else None
            if label == "root-cause":
                return (0, json.dumps(_PASSING_RC_ISSUES), "live")
            if label == "captured":
                return (0, "[]", "live")
            raise AssertionError(f"unexpected issue-list label: {args}")
        if args[:2] == ["pr", "list"]:
            return pr_response()
        raise AssertionError(f"unexpected gh call: {args}")

    return _route


def _pr_ok(prs):
    return lambda: (0, json.dumps(prs), "live")


def test_capture_shape_pr_records():
    """Criterion 38 + positive control: a merged PR after T0 with all three
    headings and the `root-cause` label counts as conforming, reported
    `pr-records: 1/1` — and, against a PASS-worthy issue leg, the overall
    row is PASS (proving the fixture itself is not the reason other tests
    below see WARN)."""
    health = _reimport_health()
    prs = [{
        "number": 9101,
        "body": _CONFORMING_BODY,
        "labels": [{"name": "root-cause"}],
        "mergedAt": "2026-09-24T10:00:00Z",
    }]
    health._health_gh_fetch = _stub_router(_route_pr_leg(_pr_ok(prs)))
    r = health.check_capture_shape()
    assert "pr-records: 1/1" in r["detail"], r["detail"]
    assert r["pr_non_conformers"] == [], r
    assert r["result"] == "PASS", r


def test_capture_shape_pr_nonconformers():
    """Criterion 39: a merged PR after T0 with `**Root cause:**` but missing
    `**Proposed:**` (and the label) is named as a non-conformer, still
    counted in <total>, and — against a PASS-worthy issue leg — the PR
    leg's own non-conformance is what drives the row to WARN."""
    health = _reimport_health()
    prs = [{
        "number": 9102,
        "body": _MISSING_PROPOSED_BODY,
        "labels": [],
        "mergedAt": "2026-09-24T10:00:00Z",
    }]
    health._health_gh_fetch = _stub_router(_route_pr_leg(_pr_ok(prs)))
    r = health.check_capture_shape()
    assert "pr-records: 0/1" in r["detail"], r["detail"]
    assert 9102 in r["pr_non_conformers"], r
    assert "#9102" in r["detail"], r["detail"]
    assert r["result"] == "WARN", r


def test_capture_shape_pr_truncated():
    """Truncation (A8): the read hits the explicit --limit and every
    returned PR merged after T0 — older PRs may be hiding past the cap, so
    the leg reads unconfirmed (truncated) and — against a PASS-worthy issue
    leg — that truncation alone is what drives the row to WARN."""
    health = _reimport_health()
    limit = health._CAPTURE_SHAPE_PR_LEG_LIMIT
    prs = [{
        "number": 9200 + i,
        "body": _CONFORMING_BODY,
        "labels": [{"name": "root-cause"}],
        "mergedAt": "2026-09-24T10:00:00Z",
    } for i in range(limit)]
    health._health_gh_fetch = _stub_router(_route_pr_leg(_pr_ok(prs)))
    r = health.check_capture_shape()
    assert "pr-records: unconfirmed (truncated)" in r["detail"], r["detail"]
    assert r["result"] == "WARN", r


def test_capture_shape_pr_read_failed():
    """PR-leg gh read failure (rc != 0) is reported unconfirmed by source,
    never collapsed into a confirmed empty/zero count — and, against a
    PASS-worthy issue leg, that failure alone drives the row non-PASS
    (C4)."""
    health = _reimport_health()
    health._health_gh_fetch = _stub_router(
        _route_pr_leg(lambda: (1, "", "live"))
    )
    r = health.check_capture_shape()
    assert "pr-records: unconfirmed (source=live)" in r["detail"], r["detail"]
    assert r["result"] != "PASS", r


def test_capture_shape_pr_unparsable_payload():
    """PR-leg payload that fails to parse as JSON (a syntax error) is
    reported unconfirmed, never collapsed into a confirmed count — and,
    against a PASS-worthy issue leg, that failure alone drives the row
    non-PASS (C4)."""
    health = _reimport_health()
    health._health_gh_fetch = _stub_router(
        _route_pr_leg(lambda: (0, "not-json{", "live"))
    )
    r = health.check_capture_shape()
    assert "pr-records: unconfirmed" in r["detail"], r["detail"]
    assert "unparsable payload" in r["detail"], r["detail"]
    assert r["result"] != "PASS", r


def test_capture_shape_pr_non_list_payload():
    """PR-leg payload that parses as valid JSON but is not a list (e.g. a
    JSON object) is reported unconfirmed, never silently treated as an
    empty/confirmed `0/0` count — and, against a PASS-worthy issue leg, that
    failure alone drives the row non-PASS (C4; guards the non-list check at
    the top of the PR-leg parse)."""
    health = _reimport_health()
    health._health_gh_fetch = _stub_router(
        _route_pr_leg(lambda: (0, "{}", "live"))
    )
    r = health.check_capture_shape()
    assert "pr-records: unconfirmed" in r["detail"], r["detail"]
    assert "unparsable payload" in r["detail"], r["detail"]
    assert r["result"] != "PASS", r


def test_capture_shape_pr_fetch_raises():
    """`_health_gh_fetch` itself raising for the `pr list` call (not just
    returning rc != 0) is caught and reported unconfirmed with a
    `computing:` source, never collapsed into a confirmed empty count — and,
    against a PASS-worthy issue leg, that failure alone drives the row
    non-PASS (guards the fetch-raises except arm, distinct from a plain
    rc != 0 return)."""
    health = _reimport_health()

    def _raise():
        raise RuntimeError("boom")

    health._health_gh_fetch = _stub_router(_route_pr_leg(_raise))
    r = health.check_capture_shape()
    assert "pr-records: unconfirmed (source=computing:" in r["detail"], r["detail"]
    assert "boom" in r["detail"], r["detail"]
    assert r["result"] != "PASS", r


def test_capture_shape_pr_before_t0_excluded():
    """A PR merged at or before T0 contributes nothing to the count: with no
    other PRs in the read, the leg reports pr-records: 0/0 rather than
    counting it (ADR-0090 D5 — the PR leg only judges PRs merged after T0);
    a legitimately empty count is not itself a failure, so the row is still
    PASS against the PASS-worthy issue leg."""
    health = _reimport_health()
    t0 = health._CAPTURE_SHAPE_PR_GRANDFATHER_UNTIL
    prs = [{
        "number": 9310,
        "body": _CONFORMING_BODY,
        "labels": [{"name": "root-cause"}],
        "mergedAt": t0,
    }]
    health._health_gh_fetch = _stub_router(_route_pr_leg(_pr_ok(prs)))
    r = health.check_capture_shape()
    assert "pr-records: 0/0" in r["detail"], r["detail"]
    assert r["result"] == "PASS", r


def test_capture_shape_pr_after_t0_counted():
    """A PR merged after T0 is counted; a PR merged at T0 in the same read
    is excluded, so only the after-T0 one contributes to the total — and
    the row is PASS against the PASS-worthy issue leg."""
    health = _reimport_health()
    t0 = health._CAPTURE_SHAPE_PR_GRANDFATHER_UNTIL
    prs = [
        {
            "number": 9311,
            "body": _CONFORMING_BODY,
            "labels": [{"name": "root-cause"}],
            "mergedAt": t0,
        },
        {
            "number": 9312,
            "body": _CONFORMING_BODY,
            "labels": [{"name": "root-cause"}],
            "mergedAt": "2026-09-24T10:00:00Z",
        },
    ]
    health._health_gh_fetch = _stub_router(_route_pr_leg(_pr_ok(prs)))
    r = health.check_capture_shape()
    assert "pr-records: 1/1" in r["detail"], r["detail"]
    assert 9311 not in r["pr_non_conformers"], r
    assert r["result"] == "PASS", r
