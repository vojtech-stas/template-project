"""
Regression test for issues #942 and #891 — reviewer.md must have a PROMINENT,
EARLY retired-rules guard that prevents the reviewer from blocking on retired rules.

Root cause: the reviewer applied a remembered/stale version of R-SENSITIVE
(ADR-0070 D4 retired it) without reading the current rubric text. The retirement
notice existed only at ~line 326 (buried inside the rubric), making it easy to
miss. Two false BLOCKs occurred (PR #890, PR #941) before this was fixed.

Fix: add a clearly-headed section BEFORE the numbered R-rule rubric that:
  (a) Names the retired rules (R-SENSITIVE, R-BOY-SCOUT) and says never-block.
  (b) Cites the retirement ADRs (ADR-0070 D4, ADR-0046 D5).
  (c) Instructs the reviewer to confirm a rule is ACTIVE (read its current text)
      before blocking on it.

These tests FAIL on develop before the fix (only a buried retirement notice
exists, not a prominent early guard) and PASS after the fix.

Runner: stdlib unittest — no top-level pytest import.
  python -m unittest tests.test_reviewer_retired_rules_guard_942 -v
"""

import re
import unittest
from pathlib import Path

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

REVIEWER_MD = Path(__file__).parent.parent / ".claude" / "pipeline" / "agents" / "reviewer.md"


def _reviewer_text() -> str:
    return REVIEWER_MD.read_text(encoding="utf-8")


def _section_before_first_r_rule(text: str) -> str:
    """Return the text that appears BEFORE the first '### R-' rule heading.

    The prominent guard must appear before '### R-SCOPE' (the first hard-block
    rule), so this function returns the prefix portion of the document that a
    reviewer would read before reaching any individual rule.
    """
    first_r_rule = text.find("### R-SCOPE")
    if first_r_rule == -1:
        return text  # No R-SCOPE found; return full text so assertions can fail clearly
    return text[:first_r_rule]


def _retired_rules_guard_section(text: str) -> str:
    """Extract the body of the 'Retired rules' guard section in the preamble.

    Returns text from the 'Retired rules' heading until the next '##' heading
    (or end of preamble), scoped to the pre-rubric portion of the document.
    This is the section the fix must add.  Returns '' if no such section exists.
    """
    preamble = _section_before_first_r_rule(text)
    match = re.search(r'##\s+Retired\s+rules', preamble, re.IGNORECASE)
    if not match:
        return ""
    body_start = match.start()
    # Find the next ## heading after our section
    next_section = re.search(r'\n##\s+', preamble[match.end():])
    if next_section:
        body_end = match.end() + next_section.start()
        return preamble[body_start:body_end]
    return preamble[body_start:]


# ---------------------------------------------------------------------------
# Tests — all FAIL before fix, PASS after fix
# ---------------------------------------------------------------------------


class TestReviewerRetiredRulesGuard(unittest.TestCase):
    """Reviewer.md must have a prominent early guard for retired rules (#942/#891)."""

    def setUp(self):
        self.text = _reviewer_text()
        self.preamble = _section_before_first_r_rule(self.text)
        self.guard = _retired_rules_guard_section(self.text)

    # (a) A clearly-headed section appears BEFORE the numbered R-rule rubric.
    def test_retired_rules_section_exists_before_rubric(self):
        """A 'Retired rules' section heading must appear before R-SCOPE."""
        # Accept case-insensitive variants like "RETIRED RULES", "Retired rules"
        pattern = re.compile(r'##\s+Retired\s+rules', re.IGNORECASE)
        self.assertRegex(
            self.preamble,
            pattern,
            "reviewer.md must have a '## Retired rules' (or similar) section heading "
            "that appears BEFORE '### R-SCOPE' (the first hard-block rule). "
            "Currently the retirement notice is buried at ~line 326 inside the rubric, "
            "making it easy for the reviewer to miss — issues #942 and #891."
        )

    # (b) The guard names R-SENSITIVE as retired.
    def test_r_sensitive_named_in_early_guard(self):
        """The early guard must name R-SENSITIVE as retired/never-block."""
        self.assertIn(
            "R-SENSITIVE",
            self.preamble,
            "The early retired-rules guard section must explicitly name 'R-SENSITIVE' "
            "so a reviewer reading top-to-bottom cannot miss it before reaching the rubric."
        )

    # (b) The guard names ADR-0070 D4 as the retirement authority.
    def test_adr_0070_cited_in_early_guard(self):
        """The early guard must cite ADR-0070 D4 as the retirement authority."""
        self.assertIn(
            "ADR-0070",
            self.preamble,
            "The early retired-rules guard must cite ADR-0070 (the ADR that retired "
            "R-SENSITIVE per D4) so the rationale is traceable."
        )

    # (c) An explicit instruction to confirm a rule is ACTIVE before blocking.
    def test_confirm_active_instruction_in_early_guard(self):
        """The early guard section must instruct: confirm a rule is ACTIVE before blocking."""
        # The instruction must live INSIDE the guard section, not just anywhere in preamble.
        # guard == '' when the '## Retired rules' section does not yet exist.
        self.assertNotEqual(
            self.guard,
            "",
            "A '## Retired rules' section must exist in the preamble before the rubric "
            "(guard section not found; test_retired_rules_section_exists_before_rubric "
            "would also fail). Cannot check active-confirmation instruction."
        )
        has_confirm_instruction = (
            re.search(r'\bactive\b', self.guard, re.IGNORECASE) is not None
            or re.search(r'confirm.{0,80}(active|current)', self.guard, re.IGNORECASE) is not None
            or re.search(r'(read|check|verify).{0,80}current.{0,40}text', self.guard, re.IGNORECASE) is not None
        )
        self.assertTrue(
            has_confirm_instruction,
            "The '## Retired rules' guard section must include an instruction to confirm "
            "a rule is ACTIVE (or to read its current text in the file) before blocking "
            "on it — guards against the memory/stale-rule anti-pattern (issues #942/#891)."
        )

    # Safety: the existing buried retirement notice at ~line 326 must still exist.
    def test_existing_r_sensitive_retirement_still_present(self):
        """The existing retirement notice inside the rubric must still be there."""
        self.assertIn(
            "Do NOT BLOCK on this rule",
            self.text,
            "The existing 'Do NOT BLOCK on this rule' retirement notice in the "
            "R-SENSITIVE section must be preserved (belt-and-suspenders)."
        )


if __name__ == "__main__":
    unittest.main()
