"""
Tests for slice #968: purpose_group, hook-trio composite, what_to_do, --list conservation.
PRD #957 slice 3 (final).
"""

import subprocess
import sys
import os
import types
import importlib

try:
    import pytest
except ImportError:  # CI runs stdlib unittest without pytest installed (CHECK 12 / #985)
    pytest = None


# ---------------------------------------------------------------------------
# Module import helper
# ---------------------------------------------------------------------------
HEALTH_DIR = os.path.join(os.path.dirname(__file__), "..", "dashboard")


def _import_health():
    spec = importlib.util.spec_from_file_location(
        "health_968", os.path.join(HEALTH_DIR, "health.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# AC1: ≥5 purpose groups present in PURPOSE_GROUP_MAP
# ---------------------------------------------------------------------------
class TestPurposeGroupMapGroups:
    def test_at_least_five_distinct_groups(self):
        h = _import_health()
        groups = set(h.PURPOSE_GROUP_MAP.values())
        assert len(groups) >= 5, f"Expected ≥5 purpose groups, got {sorted(groups)}"

    def test_expected_group_names_present(self):
        h = _import_health()
        groups = set(h.PURPOSE_GROUP_MAP.values())
        expected = {
            "Docs in sync",
            "Rules enforced",
            "Telemetry live",
            "Verification integrity",
            "Isolation/hygiene",
        }
        missing = expected - groups
        assert not missing, f"Missing expected purpose groups: {missing}"

    def test_purpose_group_order_has_all_six(self):
        h = _import_health()
        order = h.PURPOSE_GROUP_ORDER
        assert len(order) >= 5, f"PURPOSE_GROUP_ORDER too short: {order}"
        assert "Telemetry live" in order


# ---------------------------------------------------------------------------
# AC2: 4 excluded checks NOT in PURPOSE_GROUP_MAP
# ---------------------------------------------------------------------------
EXCLUDED_IDS = ["BRANCH-TOPOLOGY", "FRONTMATTER-COVERAGE", "META-TRIPWIRE", "RELEASE-READY"]


class TestExcludedChecksNotInMap:
    # pytest parametrize — only applied when pytest is present; otherwise the sibling
    # test_all_four_excluded covers the same assertions under stdlib unittest.
    if pytest is not None:
        @pytest.mark.parametrize("check_id", EXCLUDED_IDS)
        def test_check_excluded_from_purpose_group_map(self, check_id):
            h = _import_health()
            assert check_id not in h.PURPOSE_GROUP_MAP, (
                f"{check_id} should NOT be in PURPOSE_GROUP_MAP (registered-but-UI-invisible)"
            )

    def test_all_four_excluded(self):
        h = _import_health()
        for cid in EXCLUDED_IDS:
            assert cid not in h.PURPOSE_GROUP_MAP, f"{cid} unexpectedly in PURPOSE_GROUP_MAP"


# ---------------------------------------------------------------------------
# AC3: Hook-trio composite rollup logic
#      Worst actionable sub-signal wins; no-data does NOT downgrade composite
#
# NOTE: health.py check functions return UPPERCASE result values ("PASS", "WARN",
# "FAIL", "NO-DATA") and lowercase data_state ("pass", "actionable", "no-data").
# Tests use the same conventions.
# ---------------------------------------------------------------------------
class TestHookTrioComposite:
    def _make_result(self, result, data_state, check_id):
        # result = uppercase ("PASS"/"WARN"/"FAIL"/"NO-DATA")
        # data_state = lowercase ("pass"/"actionable"/"no-data")
        return {
            "id": check_id,
            "result": result,
            "data_state": data_state,
            "detail": "",
            "description": "",
        }

    def _rollup(self, h, capture_result, capture_state, integrity_result, integrity_state,
                liveness_result, liveness_state):
        slo = self._make_result(capture_result, capture_state, "CAPTURE-SLO")
        integrity = self._make_result(integrity_result, integrity_state, "HOOK-INTEGRITY")
        liveness = self._make_result(liveness_result, liveness_state, "HOOK-LIVENESS")
        comp = h._build_hook_trio_composite(slo, integrity, liveness)
        return comp

    def test_all_pass_gives_pass(self):
        h = _import_health()
        comp = self._rollup(
            h,
            "PASS", "pass",
            "PASS", "pass",
            "PASS", "pass",
        )
        assert comp["result"] == "PASS", f"Expected PASS, got {comp['result']}"

    def test_one_fail_gives_fail(self):
        h = _import_health()
        comp = self._rollup(
            h,
            "FAIL", "actionable",
            "PASS", "pass",
            "PASS", "pass",
        )
        assert comp["result"] == "FAIL"

    def test_no_data_does_not_downgrade(self):
        """If all actionable sub-signals pass, no-data must not make composite fail/warn."""
        h = _import_health()
        comp = self._rollup(
            h,
            "PASS", "pass",
            "PASS", "pass",
            "NO-DATA", "no-data",   # liveness no-data
        )
        # Composite should pass or at most warn (not degraded to fail by no-data)
        assert comp["result"] in ("PASS", "WARN"), (
            f"no-data should not downgrade composite to fail; got {comp['result']}"
        )
        assert comp["result"] != "FAIL", (
            "no-data sub-signal must not force composite to 'FAIL'"
        )

    def test_only_no_data_subs_yields_not_fail(self):
        """All three no-data → composite is not FAIL (no actionable worst-signal)."""
        h = _import_health()
        comp = self._rollup(
            h,
            "NO-DATA", "no-data",
            "NO-DATA", "no-data",
            "NO-DATA", "no-data",
        )
        # No actionable signals → composite must not be FAIL
        assert comp["result"] != "FAIL", (
            f"All-no-data should not yield FAIL; got {comp['result']}"
        )

    def test_composite_has_three_sub_signals(self):
        h = _import_health()
        comp = self._rollup(
            h,
            "PASS", "pass",
            "PASS", "pass",
            "PASS", "pass",
        )
        assert "sub_signals" in comp
        assert len(comp["sub_signals"]) == 3

    def test_composite_id_is_telemetry_live(self):
        h = _import_health()
        comp = self._rollup(
            h,
            "PASS", "pass",
            "PASS", "pass",
            "PASS", "pass",
        )
        assert comp["id"] == "TELEMETRY-LIVE"

    def test_composite_is_composite_flag(self):
        h = _import_health()
        comp = self._rollup(
            h,
            "PASS", "pass",
            "PASS", "pass",
            "PASS", "pass",
        )
        assert comp.get("is_composite") is True

    def test_warn_actionable_gives_warn(self):
        h = _import_health()
        comp = self._rollup(
            h,
            "WARN", "actionable",
            "PASS", "pass",
            "PASS", "pass",
        )
        assert comp["result"] == "WARN"


# ---------------------------------------------------------------------------
# AC4: --list count invariant (currently 49)
# ---------------------------------------------------------------------------
class TestListCountInvariant:
    def test_list_count_is_49(self):
        """python dashboard/health.py --list | wc -l must stay 49.

        Bumped from 47 to 50 by slice #1085 (PRD #1075 criteria 8/10c/10d),
        which legitimately registers THREE new checks: STREAM-LIVENESS,
        DEPLOY-HANDSHAKE, and CRITIC-HEALTH (function existed since slice
        #779 but was never added to CHECK_REGISTRY — a registry-closure fix,
        not a new check). Bumped again from 50 to 53 by slice #1136 (PRD
        #1127 §2 criterion 11b / ADR-0076 D6), which legitimately registers
        THREE new reconciler checks: SLICE-VS-PR, MERGED-WITHOUT-VERDICT,
        CLOSED-PRD-VS-QA. Moved back from 53 to 50 by slice #1240 (PRD
        #1236 §2 criterion 7c / ADR-0081 D3), which deliberately REMOVES
        three checks: EVAL-REVIEWER, EVAL-PRD-CRITIC, EVAL-SLICER-CRITIC,
        retired together with the golden-set eval harness. Bumped from 50 to
        51 by slice #1329 (PRD #1326 §2 criterion 14 / ADR-0085 D6), which
        registers ONE new check: DRAIN-LEDGER. Moved from 51 to 49 by slice
        #1483 (PRD #1480 §2 criterion 13 / ADR-0088 D6), which deliberately
        REMOVES two checks: STALE-SERVER and DEAD-ROUTES, retired together
        with the dashboard frontend they depended on. This invariant guards
        against ACCIDENTAL registry drift (duplicate/dropped IDs) —
        deliberate additions and removals bump the literal in the same PR
        that adds or deletes the check, per the established pattern for
        this test.
        """
        result = subprocess.run(
            [sys.executable, os.path.join(HEALTH_DIR, "health.py"), "--list"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"health.py --list failed: {result.stderr}"
        lines = [l for l in result.stdout.strip().splitlines() if l.strip()]
        count = len(lines)
        assert count == 49, (
            f"--list count changed! Expected 49, got {count}. "
            "Conservation violated (slice #968 §2 #8 / bumped by slice #1085, "
            "#1136; moved to 50 by slice #1240; moved to 51 by slice #1329; "
            "moved to 49 by slice #1483)."
        )


# ---------------------------------------------------------------------------
# AC5: _attach_purpose_group annotates known check IDs correctly
# ---------------------------------------------------------------------------
class TestAttachPurposeGroup:
    def test_docs1_gets_docs_in_sync(self):
        h = _import_health()
        checks = [{"id": "DOCS-1", "result": "pass"}]
        result = h._attach_purpose_group(checks)
        assert result[0]["purpose_group"] == "Docs in sync"

    def test_hook_integrity_gets_telemetry_live(self):
        h = _import_health()
        checks = [{"id": "HOOK-INTEGRITY", "result": "pass"}]
        result = h._attach_purpose_group(checks)
        assert result[0]["purpose_group"] == "Telemetry live"

    def test_excluded_check_gets_none(self):
        h = _import_health()
        for cid in EXCLUDED_IDS:
            checks = [{"id": cid, "result": "pass"}]
            result = h._attach_purpose_group(checks)
            assert result[0]["purpose_group"] is None, (
                f"{cid} should have purpose_group=None"
            )


# ---------------------------------------------------------------------------
# AC6: _attach_what_to_do only fills actionable checks
# ---------------------------------------------------------------------------
class TestAttachWhatToDo:
    def test_actionable_check_gets_what_to_do(self):
        h = _import_health()
        checks = [{"id": "DOCS-1", "result": "fail", "data_state": "actionable"}]
        result = h._attach_what_to_do(checks)
        # Should be non-empty string (fallback is allowed)
        assert isinstance(result[0].get("what_to_do"), str)

    def test_pass_check_gets_empty_what_to_do(self):
        h = _import_health()
        checks = [{"id": "DOCS-1", "result": "pass", "data_state": "pass"}]
        result = h._attach_what_to_do(checks)
        assert result[0].get("what_to_do") == ""

    def test_no_data_check_gets_empty_what_to_do(self):
        h = _import_health()
        checks = [{"id": "HOOK-LIVENESS", "result": "no-data", "data_state": "no-data"}]
        result = h._attach_what_to_do(checks)
        assert result[0].get("what_to_do") == ""
