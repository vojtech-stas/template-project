"""
tests/test_test_ordering_root_cause_1509.py — slice #1509 (PRD #1501 §2
criterion 40) regression test.

Root cause (ADR-0090 D5): a defect found in a run can now be fixed by a
lane PR whose branch is not `fix/*` (a bug's own lane branch is named after
whichever bug's work surfaced it), so TEST-ORDERING's `fix/*`-only branch
filter silently drops a fix-type PR that instead carries the `root-cause`
label. This file is committed BEFORE the implementation commit (rule #13's
regression rider / R-PROVE) and fails against pre-change `health.py`,
which counts only `fix/*` branches as fix-type — a `root-cause`-labeled PR
off a non-`fix/*` branch is invisible to `total`.

Stubs the seam (`health._health_gh_fetch`) directly (no --label filter on
this call, so the QUERY-HONESTY attestation gate is not in play), mirroring
tests/test_gh_provenance_class_sweep_1496.py's established pattern.

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


def test_test_ordering_root_cause_label():
    """A merged PR labeled `root-cause` whose branch is not `fix/*` counts
    as fix-type (post-activation), even with no mergeCommit to walk."""
    health = _reimport_health()
    prs = [{
        "number": 9301,
        "headRefName": "feat/9301-not-a-fix-branch",
        "mergeCommit": None,
        "closingIssuesReferences": [],
        "labels": [{"name": "root-cause"}],
    }]

    def _stub(args, ttl=0, timeout=0, with_source=False):
        rc, out, source = 0, json.dumps(prs), "live"
        return (rc, out, source) if with_source else (rc, out)

    health._health_gh_fetch = _stub
    r = health.check_test_ordering()
    assert r["total"] == 1, r
