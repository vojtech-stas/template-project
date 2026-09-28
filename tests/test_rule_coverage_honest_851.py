"""
Regression tests for issue #851 — RULE-COVERAGE honesty.

Two defects fixed in this slice:
  1. check_rule_coverage() only counted inline signals in CLAUDE.md text;
     it ignored enforcement living in reviewer.md R- rules or CHECK_REGISTRY.
  2. The rule→enforcer map cannot drift: every mapped enforcer must ACTUALLY
     exist (R-XXX in reviewer.md; check ID in CHECK_REGISTRY).

These tests FAIL before the fix and PASS after (ADR-0067 D2 test-first ordering).
All assertions are offline grep-based (deterministic, no network).

Runner: stdlib unittest + pytest compatible.
  python -m pytest tests/test_rule_coverage_honest_851.py -v
"""

import sys
import unittest
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parent.parent
REVIEWER_MD = REPO_ROOT / ".claude" / "pipeline" / "agents" / "reviewer.md"
HEALTH_PY = REPO_ROOT / "dashboard" / "health.py"


def _reviewer_text() -> str:
    return REVIEWER_MD.read_text(encoding="utf-8")


def _health_text() -> str:
    return HEALTH_PY.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Import the check function + CHECK_REGISTRY at runtime
# ---------------------------------------------------------------------------

def _import_health():
    """Import health module, injecting repo root into sys.path if needed."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("health", HEALTH_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Test 1: map-validity — every enforcer in the rule→enforcer map actually exists
# ---------------------------------------------------------------------------

class TestRuleEnforcerMapValidity(unittest.TestCase):
    """Every R-XXX in RULE_ENFORCER_MAP must be in reviewer.md;
    every check ID must be in CHECK_REGISTRY.

    This prevents the map from becoming its own theater (the exact concern
    stated in the slice #851 spec).
    """

    def test_rule_enforcer_map_exists_in_health_py(self):
        """health.py must define RULE_ENFORCER_MAP."""
        text = _health_text()
        self.assertIn(
            "RULE_ENFORCER_MAP",
            text,
            "health.py must define RULE_ENFORCER_MAP (rule→enforcer dict).",
        )

    def test_every_r_rule_in_map_exists_in_reviewer_md(self):
        """Every R-XXX enforcer listed in RULE_ENFORCER_MAP must appear in reviewer.md."""
        health = _import_health()
        self.assertTrue(
            hasattr(health, "RULE_ENFORCER_MAP"),
            "health module must export RULE_ENFORCER_MAP",
        )
        reviewer_text = _reviewer_text()
        missing = []
        for rule_num, enforcers in health.RULE_ENFORCER_MAP.items():
            for enforcer in enforcers:
                if enforcer.startswith("R-") and enforcer not in reviewer_text:
                    missing.append(f"rule #{rule_num}: {enforcer} not found in reviewer.md")
        self.assertEqual(
            [],
            missing,
            "These R-XXX enforcers in RULE_ENFORCER_MAP are missing from reviewer.md:\n"
            + "\n".join(missing),
        )

    def test_every_check_id_in_map_exists_in_registry(self):
        """Every check-ID enforcer (non-R-, non-SC-, non-advisory) in RULE_ENFORCER_MAP
        must be a key in CHECK_REGISTRY."""
        health = _import_health()
        self.assertTrue(
            hasattr(health, "RULE_ENFORCER_MAP"),
            "health module must export RULE_ENFORCER_MAP",
        )
        self.assertTrue(
            hasattr(health, "CHECK_REGISTRY"),
            "health module must export CHECK_REGISTRY",
        )
        registry = health.CHECK_REGISTRY
        missing = []
        for rule_num, enforcers in health.RULE_ENFORCER_MAP.items():
            for enforcer in enforcers:
                # Skip R- prefix (reviewer.md rules), SC-/PC-/AC- (critic rubric),
                # and "advisory" (explicit tag)
                if enforcer.startswith(("R-", "SC-", "PC-", "AC-")):
                    continue
                if enforcer == "advisory":
                    continue
                # Must be a registered check ID
                if enforcer not in registry:
                    missing.append(
                        f"rule #{rule_num}: '{enforcer}' not found in CHECK_REGISTRY"
                    )
        self.assertEqual(
            [],
            missing,
            "These check-ID enforcers in RULE_ENFORCER_MAP are missing from CHECK_REGISTRY:\n"
            + "\n".join(missing),
        )

    def test_sc_rules_in_map_exist_in_slicer_critic(self):
        """Every SC-XXX enforcer in RULE_ENFORCER_MAP must appear in slicer-critic.md."""
        health = _import_health()
        if not hasattr(health, "RULE_ENFORCER_MAP"):
            self.skipTest("RULE_ENFORCER_MAP not yet defined")
        slicer_path = REPO_ROOT / ".claude" / "pipeline" / "agents" / "slicer-critic.md"
        if not slicer_path.exists():
            self.skipTest("slicer-critic.md not found")
        slicer_text = slicer_path.read_text(encoding="utf-8")
        missing = []
        for rule_num, enforcers in health.RULE_ENFORCER_MAP.items():
            for enforcer in enforcers:
                if enforcer.startswith("SC-") and enforcer not in slicer_text:
                    missing.append(f"rule #{rule_num}: {enforcer} not found in slicer-critic.md")
        self.assertEqual(
            [],
            missing,
            "These SC-XXX enforcers in RULE_ENFORCER_MAP are missing from slicer-critic.md:\n"
            + "\n".join(missing),
        )


# ---------------------------------------------------------------------------
# Test 2: coverage-counts-load-bearing — after the fix, key rules are covered
# ---------------------------------------------------------------------------

class TestCoverageCountsLoadBearing(unittest.TestCase):
    """After the fix, load-bearing rules must be reported as covered with
    a named enforcer in the detail string.

    These tests fail before the fix because check_rule_coverage() only looked
    at inline signals in CLAUDE.md text (ignoring reviewer.md R- rules and
    CHECK_REGISTRY membership).
    """

    def _run_check(self):
        health = _import_health()
        return health.check_rule_coverage()

    def test_result_is_pass_or_warn_not_fail(self):
        """check_rule_coverage must return PASS or WARN (never FAIL)."""
        result = self._run_check()
        self.assertIn(
            result["result"],
            ("PASS", "WARN"),
            f"RULE-COVERAGE must be PASS or WARN, got {result['result']}: {result['detail']}",
        )

    def test_rule_1_yagni_covered(self):
        """Rule #1 (YAGNI) must be reported covered (enforced by R-YAGNI in reviewer.md)."""
        result = self._run_check()
        detail = result.get("detail", "")
        # Rule #1 must NOT appear in the grandfathered-unchecked list
        self.assertNotIn(
            "1",
            self._extract_unchecked(detail),
            f"Rule #1 (YAGNI) must be covered (R-YAGNI). detail={detail}",
        )

    def test_rule_4_no_main_covered(self):
        """Rule #4 (never push to main) must be reported covered (enforced by R-NO-MAIN)."""
        result = self._run_check()
        detail = result.get("detail", "")
        self.assertNotIn(
            "4",
            self._extract_unchecked(detail),
            f"Rule #4 (never push to main) must be covered (R-NO-MAIN). detail={detail}",
        )

    def test_rule_5_conv_commits_covered(self):
        """Rule #5 (Conventional Commits) must be reported covered (enforced by R-CONV-COMMITS)."""
        result = self._run_check()
        detail = result.get("detail", "")
        self.assertNotIn(
            "5",
            self._extract_unchecked(detail),
            f"Rule #5 (Conventional Commits) must be covered (R-CONV-COMMITS). detail={detail}",
        )

    def test_rule_11_capture_covered(self):
        """Rule #11 (capture discipline) must be reported covered (CAPTURE-SHAPE)."""
        result = self._run_check()
        detail = result.get("detail", "")
        self.assertNotIn(
            "11",
            self._extract_unchecked(detail),
            f"Rule #11 (capture discipline) must be covered (CAPTURE-SHAPE). detail={detail}",
        )

    def test_rule_15_prod_verify_covered(self):
        """Rule #15 (production verify) must be reported covered (PROOF-PRESENCE)."""
        result = self._run_check()
        detail = result.get("detail", "")
        self.assertNotIn(
            "15",
            self._extract_unchecked(detail),
            f"Rule #15 (production verify) must be covered (PROOF-PRESENCE). detail={detail}",
        )

    def test_rule_23_no_rule_without_check_covered(self):
        """Rule #23 (no rule without check) must be reported covered (R-RULE-CHECK)."""
        result = self._run_check()
        detail = result.get("detail", "")
        self.assertNotIn(
            "23",
            self._extract_unchecked(detail),
            f"Rule #23 (no rule without check) must be covered (R-RULE-CHECK). detail={detail}",
        )

    def test_coverage_fraction_above_baseline(self):
        """After the fix, coverage must exceed 50% (was 23% / 5-of-21 before fix)."""
        result = self._run_check()
        detail = result.get("detail", "")
        # Extract N/total covered fraction from "N/M covered (P%)"
        import re
        m = re.search(r'(\d+)/(\d+)\s+covered', detail)
        self.assertIsNotNone(m, f"Could not parse coverage fraction from: {detail}")
        covered = int(m.group(1))
        total = int(m.group(2))
        pct = covered * 100 // total
        self.assertGreater(
            pct,
            50,
            f"Coverage must exceed 50% after fix; got {covered}/{total} ({pct}%). detail={detail}",
        )

    def test_enforcer_names_in_detail(self):
        """The detail string must name at least one enforcer (R-YAGNI, CAPTURE-SHAPE, etc.)."""
        result = self._run_check()
        detail = result.get("detail", "")
        enforcer_signals = ["R-YAGNI", "R-NO-MAIN", "R-CONV-COMMITS", "CAPTURE-SHAPE",
                            "PROOF-PRESENCE", "R-RULE-CHECK", "R-FIXTURE"]
        found = [sig for sig in enforcer_signals if sig in detail]
        self.assertGreater(
            len(found),
            0,
            f"detail must name at least one enforcer; got: {detail}",
        )

    # ---------------------------------------------------------------------------
    # Helper
    # ---------------------------------------------------------------------------

    def _extract_unchecked(self, detail: str):
        """Return a set of rule-number strings found in grandfathered-unchecked or
        NEW unchecked-untagged sections of the detail string.

        E.g. detail = "5/21 covered (23%) | grandfathered-unchecked: [1, 2, 3]"
        returns {"1", "2", "3"}.
        """
        import re
        unchecked = set()
        # Scan for any list like [1, 2, 3] that follows "unchecked"
        for m in re.finditer(r'unchecked[^:]*:\s*\[([^\]]*)\]', detail):
            for num in re.findall(r'\d+', m.group(1)):
                unchecked.add(num)
        return unchecked


if __name__ == "__main__":
    unittest.main()
