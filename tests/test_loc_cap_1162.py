"""
Regression tests for slice #1162 (root-cause capture #1162, ADR-0077) —
R-LOC cap raised 300 -> 600 runtime-artifact LoC.

Verifies:
1. reviewer.md's canonical R-LOC mechanic states the 600 cap (and no longer
   states the old 300 cap in its own operative mechanic/BLOCK-message text).
2. CLAUDE.md's I4 convention + glossary (R-LOC, slice) state 600, not 300.
3. slicer.md carries the new slice-count guidance (ADR-0077 D1).
4. slicer-critic.md's operative mechanics (INVEST "Small", SC-SLICE-COUNT-LOC,
   SC-DUAL-CAP-MATH) state 600; every remaining literal "300" substring in the
   file is accompanied by an explicit historical/rescaling marker (the
   PR #267/slice #258 incident predates ADR-0077 and is preserved verbatim
   with a note, per CLAUDE.md rule #6 "git log is the changelog" spirit for
   agent-prompt history) rather than silently misdescribing the current cap.
5. implementer.md, ship/SKILL.md, README.template.md, and the regenerated
   README.md state 600 in their respective LoC-cap references.
6. tools/gen_rules.py's rule_id conservation baseline was bumped to 82 and
   carries statements for the two new ADR-0077 rule_ids (PIP-020/021).
7. decisions/0077-ceremony-overhead-reduction.md exists with gen_rules.py
   -compatible frontmatter naming exactly PIP-020 and PIP-021.

This is a docs/prompt-surface slice — the "test" is a set of precise,
targeted substring assertions (not a blanket "300" grep) so that legitimate,
intentionally-preserved historical incident text is not misclassified as a
live cap violation.

Runner: stdlib unittest + pytest compatible.
  python -m pytest tests/test_loc_cap_1162.py -v
  python -m unittest tests.test_loc_cap_1162 -v
"""

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _read(rel_path: str) -> str:
    return (REPO_ROOT / rel_path).read_text(encoding="utf-8", errors="replace")


class TestReviewerCanonicalCap(unittest.TestCase):
    """reviewer.md is the canonical R-LOC definition site (CLAUDE.md I4 points
    here rather than restating the number)."""

    def setUp(self):
        self.text = _read(".claude/agents/reviewer.md")

    def test_states_600_cap(self):
        self.assertIn("600 LoC of runtime-artifact code", self.text)
        self.assertIn("cap is 600", self.text)

    def test_no_longer_states_300_cap(self):
        self.assertNotIn("300 LoC of runtime-artifact code", self.text)
        self.assertNotIn("cap is 300", self.text)

    def test_cites_adr_0077(self):
        self.assertIn("ADR-0077", self.text)

    def test_pre_merge_ci_terminal_gate_present(self):
        # ADR-0077 D2: the concurrent-dispatch format-fail intercept now
        # lives in reviewer.md's own pre-merge gate, not ship/SKILL.md's
        # former pre-dispatch gate.
        self.assertIn("Pre-merge CI-terminal gate", self.text)
        self.assertIn("ci-failure-kind.sh", self.text)


class TestClaudeMdCap(unittest.TestCase):
    def setUp(self):
        self.text = _read("CLAUDE.md")

    def test_i4_states_600(self):
        self.assertIn("≤600 LoC of runtime-artifact diff", self.text)
        self.assertNotIn("≤300 LoC of runtime-artifact diff", self.text)

    def test_glossary_r_loc_states_600(self):
        self.assertIn("≤600 LoC of runtime-artifact changes", self.text)
        self.assertNotIn("≤300 LoC of runtime-artifact changes", self.text)

    def test_glossary_slice_states_600(self):
        self.assertIn("≤600 runtime LoC", self.text)
        self.assertNotIn("≤300 runtime LoC", self.text)

    def test_check_22_named_in_ci_check_script_row(self):
        # Rider #1157, folded into this PR.
        self.assertIn("CHECK 22", self.text)
        self.assertIn("RECORD-VS-GH", self.text)


class TestSlicerGuidance(unittest.TestCase):
    def test_slicer_md_has_count_guidance(self):
        text = _read(".claude/agents/slicer.md")
        self.assertIn("ADR-0077 D1", text)
        self.assertIn("3", text)  # sanity: guidance mentions the new range
        self.assertRegex(text, r"3.5 slices per PRD")


class TestSlicerCriticCap(unittest.TestCase):
    def setUp(self):
        self.text = _read(".claude/agents/slicer-critic.md")

    def test_invest_small_states_600(self):
        self.assertIn("≤600 runtime-artifact LoC", self.text)

    def test_sc_slice_count_loc_states_600(self):
        self.assertIn("≤ 600 runtime-artifact LoC", self.text)

    def test_sc_dual_cap_math_states_600(self):
        self.assertIn("R-LOC ≤600 absolute-diff cap", self.text)
        self.assertIn("exceeds 600, BLOCK", self.text)

    def test_every_remaining_300_is_a_marked_exception(self):
        """Every line still containing the literal substring '300' must be
        an explicitly-marked historical/rescaling reference, never a bare
        current-cap restatement."""
        allowed_markers = ("raised from 300", "the cap in effect at the time was 300")
        offending = []
        for lineno, line in enumerate(self.text.splitlines(), start=1):
            if "300" not in line:
                continue
            if any(marker in line for marker in allowed_markers):
                continue
            # The rescaled dual-cap-math example legitimately produces the
            # coincidental line-count "840 -> 300 lines" (not a cap
            # statement) as part of its illustrative arithmetic.
            if "840" in line and "300 lines" in line:
                continue
            offending.append((lineno, line))
        self.assertEqual(
            offending, [],
            f"Unmarked '300' reference(s) found in slicer-critic.md: {offending}",
        )


class TestOtherLiveReferences(unittest.TestCase):
    def test_implementer_md_states_600(self):
        text = _read(".claude/agents/implementer.md")
        self.assertIn("R-LOC 600 cap", text)
        self.assertNotIn("R-LOC 300 cap", text)

    def test_ship_skill_states_600(self):
        text = _read(".claude/skills/ship/SKILL.md")
        self.assertIn("≤600 LoC slices", text)
        self.assertNotIn("≤300 LoC slices", text)

    def test_readme_template_states_600(self):
        text = _read("README.template.md")
        self.assertIn("≤600 LoC diff", text)
        self.assertNotIn("≤300 LoC diff", text)

    def test_readme_generated_states_600(self):
        # README.md is a build artifact (ADR-0034 D4) — regenerated from
        # README.template.md via dashboard/readme_gen.py (ADR-0088 D3).
        text = _read("README.md")
        self.assertIn("≤600 LoC diff", text)
        self.assertNotIn("≤300 LoC diff", text)


class TestGenRulesBaseline(unittest.TestCase):
    def setUp(self):
        self.text = _read("tools/gen_rules.py")

    def test_baseline_bumped_to_82(self):
        # Baseline is a moving conservation counter — every later ADR that
        # adds new rule_ids bumps it further (e.g. ADR-0078's PIP-022 moved
        # it 82 -> 83, slice #1172; ADR-0079's PIP-023 moved it 83 -> 84,
        # slice #1197; ADR-0080's PIP-024 moved it 84 -> 85, slice #1217;
        # ADR-0081's PIP-025 moved it 85 -> 86, slice #1238; ADR-0083's
        # VER-009/VER-010 moved it 86 -> 88, slice #1310; ADR-0085's
        # PIP-026..PIP-029 moved it 88 -> 92, slice #1329; ADR-0084's
        # VER-011/VER-012 moved it 92 -> 94, slice #1402; issue #1402's
        # RULE_IDS_BASELINE was then corrected 94 -> 92 by issue #1464 —
        # ADR-0013's stale `superseded_by: []` frontmatter was populated with
        # `["ADR-0044"]`, which retires ADR-0013's SLI-004/SLI-005 from the
        # active rule set; ADR-0088 then moved it 92 -> 91, slice #1481 —
        # ADR-0088 fully supersedes ADR-0078, so PIP-022 leaves the active
        # set and ADR-0088 itself adds no new rule_ids; ADR-0089's
        # PIP-031/PIP-032 then moved it 91 -> 93, slice #1511 — ADR-0089's
        # four supersessions (ADR-0070 D1, ADR-0004 D3, ADR-0041 D2,
        # ADR-0045 D3) are each PARTIAL, so no existing rule_id drops out of
        # the active set; the delta is a pure +2; ADR-0090's PIP-033/PIP-034
        # then moved it 93 -> 95, slice #1506 — ADR-0090's eight
        # supersessions (ADR-0003 D1, ADR-0024 D1/D2, ADR-0063 D1, ADR-0085
        # D1/D3/D5/D6) are each PARTIAL, so no existing rule_id drops out of
        # the active set; the delta is a pure +2; ADR-0091's HOK-010 then
        # moved it 95 -> 96, bug #1546's lane PR — ADR-0091's two
        # supersessions (ADR-0023 D3, ADR-0029 D3) are each PARTIAL, so no
        # existing rule_id drops out of the active set; the delta is a pure
        # +1). This test's job is
        # unchanged: confirm slice #1162's own PIP-020/021 rule_ids are
        # still represented in the live baseline, not that the literal
        # number stays frozen at 82.
        self.assertIn("RULE_IDS_BASELINE: int = 96", self.text)

    def test_new_rule_statements_present(self):
        self.assertIn('"PIP-020"', self.text)
        self.assertIn('"PIP-021"', self.text)
        self.assertIn("ADR-0077 D1", self.text)
        self.assertIn("ADR-0077 D2", self.text)

    def test_adr_0084_rule_statements_present(self):
        self.assertIn('"VER-011"', self.text)
        self.assertIn('"VER-012"', self.text)


class TestAdr0077FileShape(unittest.TestCase):
    def setUp(self):
        self.text = _read("decisions/0077-ceremony-overhead-reduction.md")

    def test_frontmatter_declares_new_rule_ids(self):
        self.assertRegex(self.text, r'rule_ids:\s*\n\s*-\s*"PIP-020"\s*\n\s*-\s*"PIP-021"')

    def test_frontmatter_scope_is_pipeline(self):
        self.assertRegex(self.text, r'scope:\s*"pipeline"')

    def test_status_accepted(self):
        self.assertRegex(self.text, r'status:\s*"accepted"')


if __name__ == "__main__":
    unittest.main()
