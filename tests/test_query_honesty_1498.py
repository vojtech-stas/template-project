"""
tests/test_query_honesty_1498.py

Regression tests for PRD #1496 slice #1498 (ADR-0087 D2) — the QUERY-HONESTY
REST-attested canary, and the seam-level gate that requires it to PASS
before any `--label`-bearing call through `_health_gh_fetch()` may be read
as confirmed.

Per ADR-0067 D2 / rule #13's regression rider: this file is committed
BEFORE the fix commit and fails against pre-fix `dashboard/health.py`
(check_query_honesty / CHECK_REGISTRY["QUERY-HONESTY"] / the seam's
"--label" gate do not exist yet).

Scope of THIS file (slice 2 of 3, ADR-0003 D8):
  - check_query_honesty()'s own PASS/FAIL/WARN verdicts (PRD §2 criteria
    5, 6, 7, 8);
  - the REST-paginate parsing helper, including the "empty/unparsable
    payload is unconfirmed, never zero" rule slice #1497 established for
    condition (e)'s legs, applied here to both of QUERY-HONESTY's legs;
  - the seam's "--label" gate (criteria 9, 10) and its no-recursion /
    memoization contract.

RELEASE-READY condition (e)'s own consumption of the "unverified" source
is already covered by tests/test_gh_fetch_provenance_1497.py (slice #1497)
via a stubbed seam — not re-tested here.

All tests monkeypatch at the `_gh_fetch_impl` layer (never the network),
per PRD #1496 §4 Appetite.

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
    """Force a fresh import of the health module (fresh module-level state,
    including the QUERY-HONESTY memoization cache) and return it."""
    if "health" in sys.modules:
        del sys.modules["health"]
    return importlib.import_module("health")


class _FakeGhResult:
    """Minimal duck-typed stand-in for gh_cache.GhResult (a NamedTuple)."""

    def __init__(self, value, source, fetched_at="2026-01-01T00:00:00+00:00"):
        self.value = value
        self.fetched_at = fetched_at
        self.source = source


def _rest_page(numbers, pr_numbers=()):
    """Build a REST-canary-shaped JSON array: `numbers` are plain issues,
    `pr_numbers` are issues that additionally carry a `pull_request` key
    (and so must be excluded from the count)."""
    items = [{"number": n} for n in numbers]
    items += [{"number": n, "pull_request": {}} for n in pr_numbers]
    return json.dumps(items)


def _router(label_result=None, api_result=None, other=None):
    """Route gh calls by sub-command: args[0] == 'issue' -> label_result,
    args[0] == 'api' -> api_result. Anything else (or an unset route) ->
    `other` if given, else a computing GhResult. Also returns a call
    counter dict {'issue': n, 'api': n, 'other': n}."""
    calls = {"issue": 0, "api": 0, "other": 0}

    def _fetch(args, ttl, timeout):
        if args and args[0] == "issue":
            calls["issue"] += 1
            if label_result is not None:
                return label_result
        elif args and args[0] == "api":
            calls["api"] += 1
            if api_result is not None:
                return api_result
        else:
            calls["other"] += 1
            if other is not None:
                return other
        return _FakeGhResult(None, "computing")

    return _fetch, calls


# ===========================================================================
# check_query_honesty() verdicts (PRD #1496 §2 criteria 5, 6, 7, 8)
# ===========================================================================

def test_registered():
    health = _reimport_health()
    assert "QUERY-HONESTY" in health.CHECK_REGISTRY
    assert callable(getattr(health, "check_query_honesty", None))


def test_query_honesty_pass_when_counts_agree():
    """Criterion 5 shape: both legs confirmed, equal non-zero counts -> PASS."""
    health = _reimport_health()
    fetch, _ = _router(
        label_result=_FakeGhResult(json.dumps([{"number": 1}, {"number": 2}]), "live"),
        api_result=_FakeGhResult(_rest_page([10, 11]), "live"),
    )
    health._gh_fetch_impl = fetch
    health._GH_CACHE_AVAILABLE = True

    r = health.check_query_honesty()
    assert r["id"] == "QUERY-HONESTY"
    assert r["result"] == "PASS", r
    assert "label=2" in r["detail"] and "rest=2" in r["detail"], r["detail"]


def test_query_honesty_fail_when_counts_disagree():
    """Criterion 6 shape: both legs confirmed, counts differ -> FAIL naming
    both counts (matches the `label=<n> rest=<n>` detail token)."""
    health = _reimport_health()
    fetch, _ = _router(
        label_result=_FakeGhResult(json.dumps([]), "live"),
        api_result=_FakeGhResult(_rest_page([1, 2, 3]), "live"),
    )
    health._gh_fetch_impl = fetch
    health._GH_CACHE_AVAILABLE = True

    r = health.check_query_honesty()
    assert r["result"] == "FAIL", r
    assert "label=0" in r["detail"] and "rest=3" in r["detail"], r["detail"]


def test_query_honesty_empty_canary():
    """Criterion 8: both legs confirmed but the REST count is 0 -> WARN
    (agreement on an empty set proves nothing). Named so `pytest -k
    query_honesty_empty_canary` collects and passes."""
    health = _reimport_health()
    fetch, _ = _router(
        label_result=_FakeGhResult(json.dumps([]), "live"),
        api_result=_FakeGhResult(_rest_page([]), "live"),
    )
    health._gh_fetch_impl = fetch
    health._GH_CACHE_AVAILABLE = True

    r = health.check_query_honesty()
    assert r["result"] == "WARN", r


def test_query_honesty_warn_when_both_unavailable():
    """Criterion 7 shape: both legs unconfirmed -> WARN naming 'unconfirmed'."""
    health = _reimport_health()
    fetch, _ = _router(
        label_result=_FakeGhResult(None, "computing"),
        api_result=_FakeGhResult(None, "computing"),
    )
    health._gh_fetch_impl = fetch
    health._GH_CACHE_AVAILABLE = True

    r = health.check_query_honesty()
    assert r["result"] == "WARN", r
    assert "unconfirmed" in r["detail"], r["detail"]


def test_query_honesty_warn_when_only_label_leg_unconfirmed():
    health = _reimport_health()
    fetch, _ = _router(
        label_result=_FakeGhResult(None, "stale"),
        api_result=_FakeGhResult(_rest_page([1]), "live"),
    )
    health._gh_fetch_impl = fetch
    health._GH_CACHE_AVAILABLE = True

    r = health.check_query_honesty()
    assert r["result"] == "WARN", r
    assert "label path unconfirmed" in r["detail"], r["detail"]


def test_query_honesty_warn_when_only_rest_leg_unconfirmed():
    health = _reimport_health()
    fetch, _ = _router(
        label_result=_FakeGhResult(json.dumps([{"number": 1}]), "live"),
        api_result=_FakeGhResult(None, "computing"),
    )
    health._gh_fetch_impl = fetch
    health._GH_CACHE_AVAILABLE = True

    r = health.check_query_honesty()
    assert r["result"] == "WARN", r
    assert "REST canary unconfirmed" in r["detail"], r["detail"]


def test_query_honesty_warn_when_label_path_at_limit():
    """D2: 'the label path returned its full --limit' is a WARN, not a
    PASS, even when the (truncated) counts happen to agree."""
    health = _reimport_health()
    limit = health._QUERY_HONESTY_LABEL_LIMIT
    label_items = json.dumps([{"number": i} for i in range(limit)])
    health_fetch, _ = _router(
        label_result=_FakeGhResult(label_items, "live"),
        api_result=_FakeGhResult(_rest_page(list(range(limit))), "live"),
    )
    health._gh_fetch_impl = health_fetch
    health._GH_CACHE_AVAILABLE = True

    r = health.check_query_honesty()
    assert r["result"] == "WARN", r
    assert "--limit" in r["detail"], r["detail"]


def test_query_honesty_never_passes_on_unconfirmed_data():
    """The new check must never PASS on unconfirmed data (slice #1498
    instruction): sweep every combination where at least one leg is
    unconfirmed and assert the result is never PASS."""
    health = _reimport_health()
    unconfirmed = _FakeGhResult(None, "computing")
    confirmed = _FakeGhResult(json.dumps([{"number": 1}]), "live")
    confirmed_api = _FakeGhResult(_rest_page([1]), "live")

    for label_r, api_r in (
        (unconfirmed, confirmed_api),
        (confirmed, unconfirmed),
        (unconfirmed, unconfirmed),
    ):
        health = _reimport_health()
        fetch, _ = _router(label_result=label_r, api_result=api_r)
        health._gh_fetch_impl = fetch
        health._GH_CACHE_AVAILABLE = True
        r = health.check_query_honesty()
        assert r["result"] != "PASS", r


def test_query_honesty_empty_payload_confirmed_source_is_unconfirmed_not_zero():
    """Consistency with slice #1497's fix (PR #1502 review): a CONFIRMED
    source (rc=0, source=live) with an EMPTY payload must NOT be read as a
    confirmed zero on either leg -- it must hold as unconfirmed."""
    health = _reimport_health()
    # Empty label-path payload, confirmed source.
    fetch, _ = _router(
        label_result=_FakeGhResult("", "live"),
        api_result=_FakeGhResult(_rest_page([1]), "live"),
    )
    health._gh_fetch_impl = fetch
    health._GH_CACHE_AVAILABLE = True
    r = health.check_query_honesty()
    assert r["result"] == "WARN", r
    assert r["result"] != "PASS", r

    # Empty REST payload, confirmed source.
    health = _reimport_health()
    fetch, _ = _router(
        label_result=_FakeGhResult(json.dumps([{"number": 1}]), "live"),
        api_result=_FakeGhResult("", "live"),
    )
    health._gh_fetch_impl = fetch
    health._GH_CACHE_AVAILABLE = True
    r = health.check_query_honesty()
    assert r["result"] == "WARN", r
    assert r["result"] != "PASS", r


# ===========================================================================
# The pagination-parsing helper (ADR-0087 D2: "one JSON document per page")
# ===========================================================================

def test_sum_paginated_rest_issue_count_single_array():
    health = _reimport_health()
    payload = _rest_page([1, 2, 3], pr_numbers=[4])
    assert health._sum_paginated_rest_issue_count(payload) == 3


def test_sum_paginated_rest_issue_count_multi_document():
    """Two concatenated JSON array 'pages' (no separator between them, the
    shape gh api --paginate can legally produce) are summed together."""
    health = _reimport_health()
    page1 = _rest_page([1, 2], pr_numbers=[3])
    page2 = _rest_page([4, 5, 6])
    assert health._sum_paginated_rest_issue_count(page1 + page2) == 5


def test_sum_paginated_rest_issue_count_raises_on_empty():
    health = _reimport_health()
    try:
        health._sum_paginated_rest_issue_count("")
        assert False, "expected ValueError on empty payload"
    except ValueError:
        pass


def test_sum_paginated_rest_issue_count_raises_on_garbage():
    health = _reimport_health()
    try:
        health._sum_paginated_rest_issue_count("not json")
        assert False, "expected ValueError on unparsable payload"
    except ValueError:
        pass


# ===========================================================================
# The seam's "--label" gate (PRD #1496 §2 criteria 9, 10)
# ===========================================================================

def test_seam_gates_label_calls_unverified_on_attestation_fail():
    """Criterion 9 shape: attestation FAILs (desynced counts) -> a --label
    call through the seam whose raw fetch came back confirmed returns
    (1, '', 'unverified')."""
    health = _reimport_health()
    fetch, _ = _router(
        label_result=_FakeGhResult(json.dumps([]), "live"),
        api_result=_FakeGhResult(_rest_page([1, 2]), "live"),
    )
    health._gh_fetch_impl = fetch
    health._GH_CACHE_AVAILABLE = True

    r = health._health_gh_fetch(
        ["issue", "list", "--label", "needs-human", "--state", "open",
         "--json", "number"],
        with_source=True,
    )
    assert r == (1, "", "unverified"), r


def test_seam_lets_label_calls_through_on_attestation_pass():
    """Criterion 10 shape: attestation PASSes -> the --label call's
    confirmed raw answer is returned as-is (here, a live, confirmed leg)."""
    health = _reimport_health()
    fetch, _ = _router(
        label_result=_FakeGhResult(json.dumps([{"number": 1}]), "live"),
        api_result=_FakeGhResult(_rest_page([1]), "live"),
    )
    health._gh_fetch_impl = fetch
    health._GH_CACHE_AVAILABLE = True

    r = health._health_gh_fetch(
        ["issue", "list", "--label", "prd", "--state", "all",
         "--limit", "5", "--json", "number"],
        with_source=True,
    )
    assert r[0] == 0, r
    assert r[2] == "live", r


def test_seam_non_label_calls_bypass_attestation_entirely():
    """A call whose args carry no '--label' token is unaffected even when
    the attestation would FAIL -- e.g. `pr list --state merged`."""
    health = _reimport_health()
    fetch, _ = _router(
        label_result=_FakeGhResult(json.dumps([]), "live"),   # would FAIL attestation
        api_result=_FakeGhResult(_rest_page([1, 2]), "live"),
        other=_FakeGhResult(json.dumps([{"number": 99}]), "live"),
    )
    health._gh_fetch_impl = fetch
    health._GH_CACHE_AVAILABLE = True

    r = health._health_gh_fetch(["pr", "list", "--state", "merged"], with_source=True)
    assert r == (0, json.dumps([{"number": 99}]), "live"), r


# ---------------------------------------------------------------------------
# Round-2 fix (reviewer R2): the gate must catch every gh flag spelling
# that applies a label filter, not just the exact two-token "--label X"
# form. `gh issue list --help` / `gh pr list --help` document
# `-l, --label strings`; live `gh issue list -l=<x>` and `gh issue list
# -l<x>` (run against vojtech-stas/template-project, 2026-09-23) both
# returned the same label-filtered result as `--label <x>`, confirming
# gh (cobra/pflag) accepts all of these spellings.
# ---------------------------------------------------------------------------

def test_seam_gates_label_equals_form():
    """`--label=<x>` (single argv token) must be gated exactly like the
    two-token `--label <x>` form."""
    health = _reimport_health()
    fetch, _ = _router(
        label_result=_FakeGhResult(json.dumps([]), "live"),
        api_result=_FakeGhResult(_rest_page([1, 2]), "live"),
    )
    health._gh_fetch_impl = fetch
    health._GH_CACHE_AVAILABLE = True

    r = health._health_gh_fetch(
        ["issue", "list", "--label=needs-human", "--state", "open", "--json", "number"],
        with_source=True,
    )
    assert r == (1, "", "unverified"), r


def test_seam_gates_label_short_form_separate_token():
    """`-l <x>` (two argv tokens, gh's documented shorthand) must be
    gated the same as the long form."""
    health = _reimport_health()
    fetch, _ = _router(
        label_result=_FakeGhResult(json.dumps([]), "live"),
        api_result=_FakeGhResult(_rest_page([1, 2]), "live"),
    )
    health._gh_fetch_impl = fetch
    health._GH_CACHE_AVAILABLE = True

    r = health._health_gh_fetch(
        ["issue", "list", "-l", "needs-human", "--state", "open", "--json", "number"],
        with_source=True,
    )
    assert r == (1, "", "unverified"), r


def test_seam_gates_label_short_form_equals():
    """`-l=<x>` (single argv token) -- verified live-accepted by `gh
    issue list`."""
    health = _reimport_health()
    fetch, _ = _router(
        label_result=_FakeGhResult(json.dumps([]), "live"),
        api_result=_FakeGhResult(_rest_page([1, 2]), "live"),
    )
    health._gh_fetch_impl = fetch
    health._GH_CACHE_AVAILABLE = True

    r = health._health_gh_fetch(
        ["issue", "list", "-l=needs-human", "--state", "open", "--json", "number"],
        with_source=True,
    )
    assert r == (1, "", "unverified"), r


def test_seam_gates_label_short_form_concatenated():
    """`-l<x>` (single argv token, no separator) -- verified
    live-accepted by `gh issue list`."""
    health = _reimport_health()
    fetch, _ = _router(
        label_result=_FakeGhResult(json.dumps([]), "live"),
        api_result=_FakeGhResult(_rest_page([1, 2]), "live"),
    )
    health._gh_fetch_impl = fetch
    health._GH_CACHE_AVAILABLE = True

    r = health._health_gh_fetch(
        ["issue", "list", "-lneeds-human", "--state", "open", "--json", "number"],
        with_source=True,
    )
    assert r == (1, "", "unverified"), r


def test_seam_does_not_false_positive_on_uppercase_limit_shorthand():
    """`-L100` is the unrelated `--limit` shorthand (case-sensitive) --
    must NOT be mistaken for a label flag and gated. Uses a `pr list`
    call (routes to `other`, distinct from the `issue`/`api` legs the
    attestation itself queries) so a wrongly-gated call is visible as a
    changed answer, not masked by the router's own `issue` route."""
    health = _reimport_health()
    fetch, _ = _router(
        label_result=_FakeGhResult(json.dumps([]), "live"),   # would FAIL attestation
        api_result=_FakeGhResult(_rest_page([1, 2]), "live"),
        other=_FakeGhResult(json.dumps([{"number": 99}]), "live"),
    )
    health._gh_fetch_impl = fetch
    health._GH_CACHE_AVAILABLE = True

    r = health._health_gh_fetch(
        ["pr", "list", "-L100", "--json", "number"], with_source=True,
    )
    assert r == (0, json.dumps([{"number": 99}]), "live"), r


def test_attestation_own_label_query_bypasses_gate_no_recursion():
    """ADR-0087 D2: the canary's own label-path query must bypass its own
    attestation (else it recurses whenever that query's raw fetch comes
    back confirmed, as it does here). Proven by breaking the seam
    (`_health_gh_fetch`) and showing the attestation still completes."""
    health = _reimport_health()
    fetch, _ = _router(
        label_result=_FakeGhResult(json.dumps([{"number": 1}]), "live"),
        api_result=_FakeGhResult(_rest_page([1]), "live"),
    )
    health._gh_fetch_impl = fetch
    health._GH_CACHE_AVAILABLE = True

    def _boom(*a, **k):
        raise AssertionError("the seam must not be called by the attestation itself")

    health._health_gh_fetch = _boom
    passed, verdict, detail = health._query_honesty_attest()
    assert passed is True and verdict == "PASS", (passed, verdict, detail)


def test_attestation_memoized_within_ttl():
    """ADR-0087 D2: 'memoized per process for its own queries' cache
    lifetime' -- two attestations back to back must not double the
    underlying gh call count."""
    health = _reimport_health()
    fetch, calls = _router(
        label_result=_FakeGhResult(json.dumps([{"number": 1}]), "live"),
        api_result=_FakeGhResult(_rest_page([1]), "live"),
    )
    health._gh_fetch_impl = fetch
    health._GH_CACHE_AVAILABLE = True

    health._query_honesty_attest()
    first_calls = dict(calls)
    health._query_honesty_attest()
    assert calls == first_calls, (first_calls, calls)
