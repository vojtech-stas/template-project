"""
tests/test_gh_provenance_class_sweep_1496.py

Regression tests for PRD #1496 slice #1499 (ADR-0087 D3) — the class sweep:
`check_branch_topology()` step 5, `check_capture_shape()`, and
`check_residual_ratio()` stop asserting a GitHub-derived state they never
observed, plus a registry-wide invariant that forces every `CHECK_REGISTRY`
subject consulting the seam through an unconfirmed answer and asserts none
of them reads PASS.

Per ADR-0067 D2 / rule #13's regression rider: this file is committed
BEFORE the fix commit and fails against pre-fix `dashboard/health.py`:
  - step 5 silently defaults `pr_base_ok = True` on a failed/unconfirmed
    fetch instead of reporting WARN (#1448);
  - `check_capture_shape()`'s `_fetch_issues` and `check_residual_ratio()`'s
    `_fetch_closed_prds` / `_fetch_comments` collapse an unconfirmed fetch
    into an empty list and print "no ... found" as though the absence had
    been observed;
  - the registry-wide `no_pass_on_unconfirmed` invariant does not exist yet.

Scope of THIS file (slice 3 of 3, ADR-0003 D8):
  - `check_branch_topology()` step 5 (PRD #1496 §2 criterion 20, closes #1448);
  - `check_capture_shape()`'s `_fetch_issues` (criterion 21);
  - `check_residual_ratio()`'s `_fetch_closed_prds` / `_fetch_comments`
    (criterion 22);
  - the registry-wide `no_pass_on_unconfirmed` invariant (criterion 23).

The seam's own closed provenance vocabulary (`with_source=True`, the four
`GhResult` sources) is tests/test_gh_fetch_provenance_1497.py's scope.
QUERY-HONESTY's own PASS/FAIL/WARN legs and the seam's `--label` attestation
gate are tests/test_query_honesty_1498.py's scope. Neither is re-tested
here. All BRANCH-TOPOLOGY / CAPTURE-SHAPE / RESIDUAL-RATIO tests below stub
the seam (`health._health_gh_fetch`) directly, bypassing the QUERY-HONESTY
attestation's own internals — only the registry-wide invariant patches the
lower `_gh_fetch_impl` layer, so a single patch forces EVERY caller
(including the attestation's own two internal queries) to a genuinely
unconfirmed answer without touching the network.

Runner: pytest (also discoverable via `python -m pytest tests/`).
"""

import importlib
import inspect
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
_DASHBOARD_DIR = str(REPO_ROOT / "dashboard")
if _DASHBOARD_DIR not in sys.path:
    sys.path.insert(0, _DASHBOARD_DIR)


def _reimport_health():
    """Force a fresh import of the health module and return it (mirrors
    the pattern in tests/test_gh_fetch_provenance_1497.py and
    tests/test_query_honesty_1498.py — required whenever the full suite
    runs in one process and other files also re-bind sys.modules['health'])."""
    if "health" in sys.modules:
        del sys.modules["health"]
    return importlib.import_module("health")


class _FakeGhResult:
    """Minimal duck-typed stand-in for gh_cache.GhResult (a NamedTuple)."""

    def __init__(self, value, source, fetched_at="2026-01-01T00:00:00+00:00"):
        self.value = value
        self.fetched_at = fetched_at
        self.source = source


def _stub_fixed(rc, out, source):
    """A seam stub that ignores its call args and always returns the same
    (rc, out, source) — or its 2-tuple prefix when with_source is False."""

    def _stub(args, ttl=0, timeout=0, with_source=False):
        return (rc, out, source) if with_source else (rc, out)

    return _stub


def _stub_router(route):
    """A seam stub that dispatches on `args` via `route(args) -> (rc, out,
    source)`, returning the 2-tuple prefix when with_source is False."""

    def _stub(args, ttl=0, timeout=0, with_source=False):
        rc, out, source = route(args)
        return (rc, out, source) if with_source else (rc, out)

    return _stub


# ===========================================================================
# BRANCH-TOPOLOGY step 5 (PRD #1496 §2 criterion 20 — closes #1448)
# ===========================================================================

def test_branch_topology_step5_unconfirmed_warns_named_source():
    """Criterion 20: an unconfirmed PR-list fetch reports WARN naming the
    unconfirmed source, instead of silently defaulting pr_base_ok=True."""
    health = _reimport_health()
    health._health_gh_fetch = _stub_fixed(1, "", "computing")
    r = health.check_branch_topology()
    assert r["result"] == "WARN", r
    assert "recent-PR base check unconfirmed" in r["detail"], r["detail"]
    assert "source=computing" in r["detail"], r["detail"]


def test_branch_topology_step5_unverified_source_named():
    """The QUERY-HONESTY-produced 'unverified' source is equally caught."""
    health = _reimport_health()
    health._health_gh_fetch = _stub_fixed(1, "", "unverified")
    r = health.check_branch_topology()
    assert r["result"] == "WARN", r
    assert "recent-PR base check unconfirmed" in r["detail"], r["detail"]
    assert "source=unverified" in r["detail"], r["detail"]


def test_branch_topology_step5_confirmed_empty_payload_holds():
    """A confirmed source (rc=0) paired with an EMPTY payload must not be
    read as a confirmed empty PR list — ADR-0087 D3's 'a confirmed source
    with an empty or unparsable payload is unconfirmed, never zero' rule,
    applied here the same way slice #1497 applied it to condition (e)'s
    legs."""
    health = _reimport_health()
    health._health_gh_fetch = _stub_fixed(0, "", "live")
    r = health.check_branch_topology()
    assert r["result"] == "WARN", r
    assert "recent-PR base check unconfirmed" in r["detail"], r["detail"]
    assert "source=live" in r["detail"], r["detail"]


def test_branch_topology_step5_confirmed_unparsable_payload_holds():
    health = _reimport_health()
    health._health_gh_fetch = _stub_fixed(0, "not-a-json-array", "live")
    r = health.check_branch_topology()
    assert r["result"] == "WARN", r
    assert "recent-PR base check unconfirmed" in r["detail"], r["detail"]
    assert "source=live" in r["detail"], r["detail"]


def test_branch_topology_step5_confirmed_empty_list_still_passes():
    """Regression guard: a genuinely confirmed empty PR list is NOT
    unconfirmed — pr_base_ok stays True and the check reaches its normal
    (real-git-state) verdict, unchanged from before this slice."""
    health = _reimport_health()
    health._health_gh_fetch = _stub_fixed(0, "[]", "live")
    r = health.check_branch_topology()
    assert "unconfirmed" not in r["detail"], r["detail"]
    assert r["result"] in ("PASS", "WARN"), r


def test_branch_topology_step5_main_based_pr_still_warns():
    """Regression guard: a confirmed PR list naming a main-based PR still
    reports the pre-existing 'recent PRs targeting main' WARN, unchanged by
    this slice's fix."""
    health = _reimport_health()
    payload = json.dumps([{"number": 42, "baseRefName": "main"}])
    health._health_gh_fetch = _stub_fixed(0, payload, "live")
    r = health.check_branch_topology()
    assert r["result"] == "WARN", r
    assert "recent PRs targeting main" in r["detail"], r["detail"]
    assert "unconfirmed" not in r["detail"], r["detail"]


# ===========================================================================
# CAPTURE-SHAPE (PRD #1496 §2 criterion 21)
# ===========================================================================

def test_capture_shape_unconfirmed_root_cause_fetch_warns():
    """An unconfirmed root-cause-labeled fetch reports WARN naming the
    unconfirmed source, instead of the confirmed-empty 'no
    root-cause-labeled issues found' wording."""
    health = _reimport_health()
    health._health_gh_fetch = _stub_fixed(1, "", "computing")
    r = health.check_capture_shape()
    assert r["result"] == "WARN", r
    assert "unconfirmed (source=computing)" in r["detail"], r["detail"]
    assert "no root-cause-labeled issues found" not in r["detail"], r["detail"]


def test_capture_shape_unconfirmed_captured_fetch_warns():
    """The second _fetch_issues call (label=captured) is equally
    unconfirmed-aware, even when the first (root-cause) call is confirmed
    empty."""
    health = _reimport_health()

    def _route(args):
        li = args.index("--label")
        if args[li + 1] == "root-cause":
            return (0, "[]", "live")
        return (1, "", "computing")

    health._health_gh_fetch = _stub_router(_route)
    r = health.check_capture_shape()
    assert r["result"] == "WARN", r
    assert "unconfirmed (source=computing)" in r["detail"], r["detail"]


def test_capture_shape_confirmed_empty_keeps_existing_wording():
    """Regression guard: a genuinely confirmed empty result for BOTH labels
    keeps the pre-existing 'no root-cause-labeled issues found' wording —
    that absence was observed (ADR-0087 D3)."""
    health = _reimport_health()
    health._health_gh_fetch = _stub_fixed(0, "[]", "live")
    r = health.check_capture_shape()
    assert r["result"] == "WARN", r
    assert "no root-cause-labeled issues found" in r["detail"], r["detail"]
    assert "unconfirmed" not in r["detail"], r["detail"]


def test_capture_shape_confirmed_unparsable_payload_is_unconfirmed():
    health = _reimport_health()
    health._health_gh_fetch = _stub_fixed(0, "not-json", "live")
    r = health.check_capture_shape()
    assert r["result"] == "WARN", r
    assert "unconfirmed (source=live" in r["detail"], r["detail"]


# ===========================================================================
# RESIDUAL-RATIO (PRD #1496 §2 criterion 22)
# ===========================================================================

def test_residual_ratio_unconfirmed_closed_prds_fetch_warns():
    """An unconfirmed closed-PRD fetch reports WARN naming the unconfirmed
    source, instead of the confirmed-empty 'no closed PRDs found' wording."""
    health = _reimport_health()
    health._health_gh_fetch = _stub_fixed(1, "", "computing")
    r = health.check_residual_ratio()
    assert r["result"] == "WARN", r
    assert "unconfirmed (source=computing)" in r["detail"], r["detail"]
    assert "no closed PRDs found" not in r["detail"], r["detail"]


def test_residual_ratio_confirmed_empty_closed_prds_keeps_existing_wording():
    """Regression guard: a genuinely confirmed empty closed-PRD list keeps
    the pre-existing 'no closed PRDs found' wording — that absence was
    observed (ADR-0087 D3)."""
    health = _reimport_health()
    health._health_gh_fetch = _stub_fixed(0, "[]", "live")
    r = health.check_residual_ratio()
    assert r["result"] == "WARN", r
    assert "no closed PRDs found" in r["detail"], r["detail"]
    assert "unconfirmed" not in r["detail"], r["detail"]


def test_residual_ratio_unconfirmed_comment_fetch_warns():
    """The PRD-list leg confirms with one PRD, but that PRD's comment fetch
    is unconfirmed — ADR-0087 D3: RESIDUAL-RATIO must not compute a ratio
    (or a 'low-sample' verdict) from partial comment data."""
    health = _reimport_health()

    def _route(args):
        if args[:2] == ["issue", "list"]:
            return (0, json.dumps([{"number": 111}]), "live")
        return (1, "", "computing")  # issue view (comments) unconfirmed

    health._health_gh_fetch = _stub_router(_route)
    r = health.check_residual_ratio()
    assert r["result"] == "WARN", r
    assert "unconfirmed (source=computing)" in r["detail"], r["detail"]


def test_residual_ratio_confirmed_unparsable_comments_payload_is_unconfirmed():
    health = _reimport_health()

    def _route(args):
        if args[:2] == ["issue", "list"]:
            return (0, json.dumps([{"number": 222}]), "live")
        return (0, "not-json", "live")

    health._health_gh_fetch = _stub_router(_route)
    r = health.check_residual_ratio()
    assert r["result"] == "WARN", r
    assert "unconfirmed (source=live" in r["detail"], r["detail"]


# ===========================================================================
# Registry-wide invariant (PRD #1496 §2 criterion 23 — ADR-0087 D3)
#
# Named so `pytest -k no_pass_on_unconfirmed` collects and passes it.
# ===========================================================================

_MIN_SUBJECTS = 12

# Pins RELEASE-READY conditions (a)-(d) and (f) per the check's documented
# test seams, so a forced-computing seam exercises ONLY condition (e) via
# the seam, instead of falling back to a local tools/ci-checks.sh / pytest
# re-run that would recurse inside this very suite (PRD #1496 §6
# rabbit-hole: "the invariant test must set ISO for RELEASE-READY").
_ISO_ENV = {
    "_RELEASE_READY_CI_RESULT": "PASS",
    "_RELEASE_READY_TESTS_RESULT": "PASS",
    "_RELEASE_READY_PROOF_INTEGRITY_RESULT": "PASS",
    "_RELEASE_READY_STREAK_RESULT": "PASS",
    "_META_TRIPWIRE_RESULT_OVERRIDE": "PASS",
}
_NH_COUNT_VAR = "_RELEASE_READY_NEEDS_HUMAN_COUNT"


def _discover_seam_subjects(health):
    """Discover CHECK_REGISTRY subjects by source inspection: every
    registered check whose OWN function body (nested helpers included —
    inspect.getsource(fn) covers a function's full text) contains a direct
    call to the gated seam `_health_gh_fetch(`.

    Deliberately a literal-substring check, not `_health_gh_fetch_raw(`:
    the `_raw` infix sits between the base name and the opening paren, so
    a raw-only caller never matches this substring.

    QUERY-HONESTY is deliberately excluded: `check_query_honesty()` calls
    only `_query_honesty_attest()`, which — to avoid recursing into its own
    attestation gate — calls `_health_gh_fetch_raw()` directly, by design
    (ADR-0087 D2). It is not a `_health_gh_fetch()` consumer, so it is
    correctly absent from this discovery, not silently missed.
    """
    subjects = []
    for check_id, fn in health.CHECK_REGISTRY.items():
        try:
            src = inspect.getsource(fn)
        except (OSError, TypeError):
            continue
        if "_health_gh_fetch(" in src:
            subjects.append(check_id)
    return subjects


def test_no_pass_on_unconfirmed_subject_discovery_floor():
    """The invariant's own subject-discovery must find at least the 12
    independently-confirmed seam consumers (SPEC-COVERAGE, RESIDUAL-RATIO,
    CAPTURE-SHAPE, RECORD-VS-GH, SLICE-VS-PR, MERGED-WITHOUT-VERDICT,
    CLOSED-PRD-VS-QA, SILENT-DRIFT, TEST-ORDERING, REQUIRED-LABELS,
    BRANCH-TOPOLOGY, RELEASE-READY), and must explicitly exclude
    QUERY-HONESTY."""
    health = _reimport_health()
    subjects = _discover_seam_subjects(health)
    assert len(subjects) >= _MIN_SUBJECTS, subjects
    assert "QUERY-HONESTY" not in subjects, (
        "QUERY-HONESTY consults _health_gh_fetch_raw directly (ADR-0087 D2 "
        "no-recursion design), not the gated seam — it must not be a "
        f"discovered subject: {subjects}"
    )


def test_no_pass_on_unconfirmed_registry_wide_invariant():
    """Criterion 23: with every seam answer forced to computing, every
    registered check that consults the seam returns a non-PASS verdict
    whose detail names unavailable/unconfirmed/unverifiable. The test
    fails itself below 12 subjects (never passes on a filtered-away
    subject set), and collects every offender before failing once,
    naming each."""
    health = _reimport_health()
    subjects = _discover_seam_subjects(health)
    assert len(subjects) >= _MIN_SUBJECTS, (
        f"invariant found only {len(subjects)} subjects (<{_MIN_SUBJECTS}); "
        f"refusing to run on a filtered-away subject set: {subjects}"
    )

    def _computing_impl(args, *, ttl, timeout):
        # Forces the underlying gh_cache layer itself to computing, so
        # EVERY consumer of the seam — including QUERY-HONESTY's own two
        # internal _health_gh_fetch_raw() calls — sees an unconfirmed
        # answer, without any real network access.
        return _FakeGhResult(None, "computing")

    health._gh_fetch_impl = _computing_impl
    health._GH_CACHE_AVAILABLE = True

    tracked_keys = set(_ISO_ENV) | {_NH_COUNT_VAR}
    saved_env = {k: os.environ.get(k) for k in tracked_keys}
    os.environ.update(_ISO_ENV)
    os.environ.pop(_NH_COUNT_VAR, None)

    offenders = []
    try:
        for check_id in subjects:
            result = health.CHECK_REGISTRY[check_id]()
            verdict = result.get("result")
            detail = result.get("detail", "") or ""
            observed = verdict != "PASS" and any(
                token in detail
                for token in ("unavailable", "unconfirmed", "unverifiable")
            )
            if not observed:
                offenders.append((check_id, verdict, detail))
    finally:
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _reimport_health()

    assert not offenders, (
        "seam consumer(s) reported an unobserved state under a "
        f"forced-computing seam: {offenders}"
    )


if __name__ == "__main__":
    import pytest as _pytest

    raise SystemExit(_pytest.main([__file__, "-v"]))
