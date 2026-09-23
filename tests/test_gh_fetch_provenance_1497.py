"""
tests/test_gh_fetch_provenance_1497.py

Regression tests for PRD #1496 slice #1497 (ADR-0087 D1, D3, D4) — the
health seam `_health_gh_fetch()` gains a closed provenance vocabulary, and
`RELEASE-READY` condition (e) is rewritten to fail closed on any unconfirmed
needs-human count instead of defaulting to zero.

Per ADR-0067 D2 / rule #13's regression rider: this file is committed BEFORE
the fix commit and fails against pre-fix `health.py` (stale reads as
success, condition (e) defaults an unconfirmed count to 0).

Scope of THIS file (slice 1 of 3, ADR-0003 D8 walking skeleton):
  - the seam's `with_source=True` contract and its closed vocabulary
    (PRD #1496 §2 criteria 1, 2, 3);
  - condition (e) holding on every unconfirmed shape, and counting both
    open `needs-human` issues and pull requests (criteria 13, 15, 16, 17,
    19).
Everything QUERY-HONESTY / the seven-caller canary sweep (criteria 4-11,
14) and the class-sweep + registry-wide invariant (criteria 20-23) belong
to slices 2 and 3 and are deliberately NOT exercised here.

All tests monkeypatch at the seam layer (`health._health_gh_fetch` or,
for the two source-vocabulary tests, `health._gh_fetch_impl`) — never the
network, per PRD #1496 §4 Appetite.

Runner: pytest (also discoverable via `python -m pytest tests/`).
"""

import importlib
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).parent.parent
_DASHBOARD_DIR = str(REPO_ROOT / "dashboard")
if _DASHBOARD_DIR not in sys.path:
    sys.path.insert(0, _DASHBOARD_DIR)

import health  # noqa: E402  (path bootstrap must precede the import; also
                             # guarantees "health" is importable at all)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

def _reimport_health():
    """Force a fresh import of the health module and return it.

    Several other files in this suite (e.g. test_gh_cache_health_firing_996's
    own ``_reimport``) delete ``health`` from ``sys.modules`` and re-import
    it, which replaces the module OBJECT. When the full suite runs in one
    process, a module-level ``import health`` binding captured at this
    file's collection time can go stale — ``importlib.reload()`` requires
    identity with ``sys.modules['health']`` and raises otherwise. Always
    fetch a fresh reference instead of relying on reload-in-place.
    """
    if "health" in sys.modules:
        del sys.modules["health"]
    return importlib.import_module("health")

class _FakeGhResult:
    """Minimal duck-typed stand-in for gh_cache.GhResult (a NamedTuple)."""

    def __init__(self, value, source, fetched_at="2026-01-01T00:00:00+00:00"):
        self.value = value
        self.fetched_at = fetched_at
        self.source = source


_ALL_SOURCES = ("live", "cache", "stale", "computing")

ISO_ENV = {
    # Pins conditions (a)-(d) and (f) per the check's documented seams
    # (health.py:5431-5438 at ADR-0087's baseline). Condition (e) is
    # deliberately never injected here unless a test sets it explicitly.
    "_RELEASE_READY_CI_RESULT": "PASS",
    "_RELEASE_READY_TESTS_RESULT": "PASS",
    "_RELEASE_READY_PROOF_INTEGRITY_RESULT": "PASS",
    "_RELEASE_READY_STREAK_RESULT": "PASS",
    "_META_TRIPWIRE_RESULT_OVERRIDE": "PASS",
}

_NH_COUNT_VAR = "_RELEASE_READY_NEEDS_HUMAN_COUNT"


def _stub_fixed(rc, out, source):
    """A seam stub that ignores its arguments and always returns the same
    (rc, out, source) — or its 2-tuple prefix when with_source is False."""

    def _stub(args, ttl=0, timeout=0, with_source=False):
        return (rc, out, source) if with_source else (rc, out)

    return _stub


def _stub_raises(exc):
    def _stub(args, ttl=0, timeout=0, with_source=False):
        raise exc

    return _stub


def _release_ready_with_seam(seam_stub, extra_env=None):
    """Reload health, apply ISO overrides (+ extra_env), stub the seam, call
    check_release_ready(). Restores module state and environment afterward.
    """
    env = dict(ISO_ENV)
    if extra_env:
        env.update(extra_env)
    tracked_keys = set(env) | {_NH_COUNT_VAR}
    old = {k: os.environ.get(k) for k in tracked_keys}
    if _NH_COUNT_VAR not in env:
        # A leftover override from another test/process must not mask the
        # seam call this test wants to exercise.
        os.environ.pop(_NH_COUNT_VAR, None)
    for k, v in env.items():
        os.environ[k] = v
    try:
        health_mod = _reimport_health()
        health_mod._health_gh_fetch = seam_stub
        return health_mod.check_release_ready()
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _reimport_health()


# ===========================================================================
# Seam provenance (PRD #1496 §2 criteria 1, 2, 3 — ADR-0087 D1)
# ===========================================================================

def test_seam_default_two_tuple_confirmed_vs_unconfirmed():
    """Criterion 1: default (rc, out) shape — confirmed sources rc=0 with
    the payload, unconfirmed sources rc=1 with an empty payload."""
    health = _reimport_health()
    results = []
    for source in _ALL_SOURCES:
        health._gh_fetch_impl = (
            lambda args, *, ttl, timeout, _s=source: _FakeGhResult("[]", _s)
        )
        health._GH_CACHE_AVAILABLE = True
        results.append(health._health_gh_fetch(["issue", "list"]))
    assert results == [(0, "[]"), (0, "[]"), (1, ""), (1, "")], results


def test_seam_with_source_three_tuple_labels_every_source():
    """Criterion 2: with_source=True always returns a 3-tuple naming the
    exact source, for all four GhResult sources."""
    health = _reimport_health()
    labels = []
    lengths = set()
    for source in _ALL_SOURCES:
        health._gh_fetch_impl = (
            lambda args, *, ttl, timeout, _s=source: _FakeGhResult("[]", _s)
        )
        health._GH_CACHE_AVAILABLE = True
        r = health._health_gh_fetch(["issue", "list"], with_source=True)
        labels.append(r[2])
        lengths.add(len(r))
    assert labels == list(_ALL_SOURCES), labels
    assert lengths == {3}, lengths


def test_seam_fallback_labels_missing_gh_as_computing():
    """Criterion 3: gh_cache import unavailable + gh missing from PATH →
    _health_gh_fetch(with_source=True) returns (1, '', 'computing')."""
    health = _reimport_health()
    health._GH_CACHE_AVAILABLE = False
    with patch.object(
        health.subprocess, "run",
        side_effect=FileNotFoundError("gh: command not found"),
    ):
        r = health._health_gh_fetch(["--version"], with_source=True)
    assert r == (1, "", "computing"), r


def test_seam_fallback_labels_successful_run_as_live():
    """F2(a): import-fallback branch, success leg (health.py:122-125). gh_cache
    import unavailable but the direct subprocess call succeeds (rc=0, no
    exception) → _health_gh_fetch(with_source=True) returns (0, stdout, 'live')."""
    health = _reimport_health()
    health._GH_CACHE_AVAILABLE = False
    completed = MagicMock(returncode=0, stdout="[]")
    with patch.object(health.subprocess, "run", return_value=completed):
        r = health._health_gh_fetch(["issue", "list"], with_source=True)
    assert r == (0, "[]", "live"), r


def test_seam_fallback_labels_failed_run_without_exception_as_computing():
    """F2(a): import-fallback branch, failed-without-exception leg
    (health.py:122-125). gh_cache import unavailable and the direct
    subprocess call returns a non-zero exit WITHOUT raising →
    _health_gh_fetch(with_source=True) returns (1, '', 'computing'), the
    same shape as the exception path."""
    health = _reimport_health()
    health._GH_CACHE_AVAILABLE = False
    completed = MagicMock(returncode=1, stdout="")
    with patch.object(health.subprocess, "run", return_value=completed):
        r = health._health_gh_fetch(["issue", "list"], with_source=True)
    assert r == (1, "", "computing"), r


# ===========================================================================
# RELEASE-READY condition (e) fails closed (ADR-0087 D3, D4)
# ===========================================================================

def test_release_ready_e_unconfirmed_source_computing():
    """Criterion 13: gh unavailable → WARN naming condition (e), unconfirmed,
    source=computing (matches promote.sh's own grep target)."""
    r = _release_ready_with_seam(_stub_fixed(1, "", "computing"))
    assert r["result"] == "WARN", r
    assert r["first_failing_condition"] == "e", r
    detail = r["detail"]
    assert "condition (e)" in detail, detail
    assert "unconfirmed" in detail, detail
    assert "source=computing" in detail, detail


def test_release_ready_e_non_integer_injection_holds():
    """Criterion 16: a non-integer _RELEASE_READY_NEEDS_HUMAN_COUNT now
    holds the gate instead of silently defaulting the count to 0."""
    r = _release_ready_with_seam(
        _stub_raises(AssertionError("seam must not be called on a set override")),
        extra_env={_NH_COUNT_VAR: "abc"},
    )
    assert r["result"] == "WARN", r
    assert r["first_failing_condition"] == "e", r


def test_release_ready_e_integer_override_bypasses_seam_entirely():
    """Criterion 15: a valid integer override (0) still PASSes, and per
    ADR-0087 D3/D4 it bypasses BOTH queries — the seam must never be
    called at all when the override parses."""

    def _seam(*a, **k):
        raise AssertionError("seam must not be called when the count is overridden")

    r = _release_ready_with_seam(_seam, extra_env={_NH_COUNT_VAR: "0"})
    assert r["result"] == "PASS", r
    assert r["first_failing_condition"] == "", r


def test_release_ready_e_confirmed_empty_still_passes():
    """Regression guard for the Appetite promise 'verdicts with GitHub
    healthy are unchanged': both legs confirmed and empty → PASS."""

    def _seam(a, ttl=0, timeout=0, with_source=False):
        return (0, "[]", "live") if with_source else (0, "[]")

    r = _release_ready_with_seam(_seam)
    assert r["result"] == "PASS", r
    assert r["first_failing_condition"] == "", r


def test_release_ready_e_empty_payload_confirmed_source_holds():
    """F1 regression (round-1 reviewer repro): a CONFIRMED source (rc=0,
    source=live) with an EMPTY payload on both legs must NOT be read as a
    confirmed empty list. ADR-0087 D3: confirmed means a confirmed source
    AND a payload that parses as a JSON list; an empty string does not
    parse as one. Pre-fix, `_json.loads(_leg_out) if _leg_out.strip() else
    []` turned this into a confirmed zero and opened the gate."""
    r = _release_ready_with_seam(_stub_fixed(0, "", "live"))
    assert r["result"] == "WARN", r
    assert r["first_failing_condition"] == "e", r
    assert "unconfirmed" in r["detail"], r["detail"]
    assert "source=live" in r["detail"], r["detail"]


def test_release_ready_e_empty_payload_holds_even_if_other_leg_confirmed():
    """F1 sweep: the empty-payload defect must be caught leg-by-leg — a
    confirmed-but-empty issues leg must hold the gate even when the PRs
    leg is confirmed with a real (parseable) empty list, and vice versa."""

    def _issues_empty(a, ttl=0, timeout=0, with_source=False):
        if a[:2] == ["issue", "list"]:
            return (0, "", "live") if with_source else (0, "")
        return (0, "[]", "live") if with_source else (0, "[]")

    r = _release_ready_with_seam(_issues_empty)
    assert r["result"] == "WARN", r
    assert r["first_failing_condition"] == "e", r
    assert "issues unconfirmed" in r["detail"], r["detail"]

    def _prs_empty(a, ttl=0, timeout=0, with_source=False):
        if a[:2] == ["pr", "list"]:
            return (0, "", "live") if with_source else (0, "")
        return (0, "[]", "live") if with_source else (0, "[]")

    r = _release_ready_with_seam(_prs_empty)
    assert r["result"] == "WARN", r
    assert r["first_failing_condition"] == "e", r
    assert "PRs unconfirmed" in r["detail"], r["detail"]


def test_release_ready_e_counts_issues_and_prs():
    """Criterion 19: one open needs-human PR and zero open needs-human
    issues holds the gate — condition (e) must count both queries' sum."""

    def _seam(a, ttl=0, timeout=0, with_source=False):
        value = (
            json.dumps([{"number": 7}])
            if a[:2] == ["pr", "list"] and "needs-human" in a
            else "[]"
        )
        return (0, value, "live") if with_source else (0, value)

    r = _release_ready_with_seam(_seam)
    assert r["result"] == "WARN", r
    assert r["first_failing_condition"] == "e", r
    assert "1 open" in r["detail"], r["detail"]


# ---------------------------------------------------------------------------
# Criterion 17: exactly five fail-closed shapes, one test per shape, named
# so `pytest -k release_ready_e_fail_closed` collects and passes exactly 5.
# ---------------------------------------------------------------------------

def test_release_ready_e_fail_closed_stale():
    r = _release_ready_with_seam(_stub_fixed(1, "", "stale"))
    assert r["result"] == "WARN", r
    assert r["first_failing_condition"] == "e", r
    assert "unconfirmed" in r["detail"], r["detail"]
    assert "source=stale" in r["detail"], r["detail"]


def test_release_ready_e_fail_closed_computing():
    r = _release_ready_with_seam(_stub_fixed(1, "", "computing"))
    assert r["result"] == "WARN", r
    assert r["first_failing_condition"] == "e", r
    assert "unconfirmed" in r["detail"], r["detail"]
    assert "source=computing" in r["detail"], r["detail"]


def test_release_ready_e_fail_closed_unverified():
    # "unverified" is D2's (QUERY-HONESTY, slice 2) label — condition (e)
    # must treat it as unconfirmed generically, with no slice-2 code yet.
    r = _release_ready_with_seam(_stub_fixed(1, "", "unverified"))
    assert r["result"] == "WARN", r
    assert r["first_failing_condition"] == "e", r
    assert "unconfirmed" in r["detail"], r["detail"]
    assert "source=unverified" in r["detail"], r["detail"]


def test_release_ready_e_fail_closed_unparsable():
    # Confirmed source (rc=0, source=live) but a payload that is not a
    # parseable JSON list — condition (e) must not treat rc=0 as confirmed
    # on its own.
    r = _release_ready_with_seam(_stub_fixed(0, "not-a-json-array", "live"))
    assert r["result"] == "WARN", r
    assert r["first_failing_condition"] == "e", r
    assert "unconfirmed" in r["detail"], r["detail"]
    assert "source=live" in r["detail"], r["detail"]


def test_release_ready_e_fail_closed_exception():
    r = _release_ready_with_seam(_stub_raises(RuntimeError("boom")))
    assert r["result"] == "WARN", r
    assert r["first_failing_condition"] == "e", r
