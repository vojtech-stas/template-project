"""
Regression tests for issue #848 — R-SENSITIVE must be advisory, not blocking.

ADR-0070 D4 retired R-SENSITIVE as a per-PR blocking gate in favour of a
promotion-time meta-tripwire.  These tests assert that reviewer.md's
R-SENSITIVE rule section no longer contains BLOCK/human-ack-required directives
and that it carries the "advisory" marker plus an ADR-0070 reference.

These tests FAIL before the fix (reviewer.md still has the old BLOCK text)
and PASS after (reviewer.md updated to advisory per this slice).

All assertions are offline grep-based (deterministic, no network required).

Runner: stdlib unittest + pytest compatible.
  python -m pytest tests/test_rsensitive_advisory_848.py -v
"""

import re
import unittest
from pathlib import Path

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

REVIEWER_MD = Path(__file__).parent.parent / ".claude" / "pipeline" / "agents" / "reviewer.md"


def _reviewer_text() -> str:
    """Return the full text of reviewer.md."""
    return REVIEWER_MD.read_text(encoding="utf-8")


def _rsensitive_section(text: str) -> str:
    """Extract the R-SENSITIVE section body from reviewer.md (excluding heading line).

    Returns the text from the line AFTER the '### R-SENSITIVE' heading until
    the next '### R-' heading (or end of file), so tests target only the body
    of the rule — not the heading itself.
    """
    # Find the start of the R-SENSITIVE block
    start = text.find("### R-SENSITIVE")
    if start == -1:
        return ""
    # Skip past the heading line to examine the body only
    newline_after_heading = text.find("\n", start)
    body_start = newline_after_heading + 1 if newline_after_heading != -1 else start
    # Find the next ### R- heading after the body start
    next_rule = text.find("### R-", body_start)
    if next_rule == -1:
        return text[body_start:]
    return text[body_start:next_rule]


# ---------------------------------------------------------------------------
# Tests — these all FAIL before the fix
# ---------------------------------------------------------------------------


class TestRSensitiveIsAdvisory(unittest.TestCase):
    """R-SENSITIVE must be advisory (not a blocking gate) per ADR-0070 D4."""

    def setUp(self):
        self.text = _reviewer_text()
        self.section = _rsensitive_section(self.text)

    def test_rsensitive_section_exists(self):
        """R-SENSITIVE section must still exist in reviewer.md (slot preserved)."""
        self.assertNotEqual(
            self.section,
            "",
            "reviewer.md must still contain a '### R-SENSITIVE' section "
            "(the rule slot is preserved, its verdict changes to advisory).",
        )

    def test_no_block_directive_in_rsensitive(self):
        """R-SENSITIVE section must NOT contain a BLOCK directive.

        The old text had 'BLOCK' as the verdict and 'requires human ack'.
        After the fix, the section must not instruct the reviewer to BLOCK.
        """
        # The section should NOT contain a BLOCK-verdict instruction.
        # Accept upper and mixed case to catch any variant.
        block_pattern = re.compile(
            r'\bBLOCK\b',
            re.IGNORECASE,
        )
        # Allow 'BLOCK' in the old-rule name references only if accompanied
        # by advisory framing — but the simplest assertion is that the section
        # should not contain bare 'BLOCK' as an action directive.
        # The heading "### R-SENSITIVE" itself can stay; we just need no BLOCK action.
        # Narrow: look for "BLOCK" as a verdict/action word.
        # The pattern "BLOCK" appears in old text like "BLOCK with `R-SENSITIVE:`"
        self.assertNotRegex(
            self.section,
            r'(?i)\bBLOCK\s+with\b',
            "R-SENSITIVE section must NOT instruct reviewer to BLOCK; "
            "per ADR-0070 D4 the rule is retired as a blocking gate.",
        )

    def test_no_human_ack_required_in_rsensitive(self):
        """R-SENSITIVE section must NOT require human-ack label before APPROVE.

        The old text: '... require human ack before APPROVE; BLOCK without it.'
        After the fix, no such requirement.
        """
        # Check for the specific phrasing patterns of the old blocking requirement.
        self.assertNotRegex(
            self.section,
            r'(?i)require[s]?\s+human\s+ack\b',
            "R-SENSITIVE section must NOT contain 'requires human ack'; "
            "per ADR-0070 D4 this blocking gate is retired.",
        )
        self.assertNotRegex(
            self.section,
            r'(?i)BLOCK\s+without\s+it',
            "R-SENSITIVE section must NOT contain 'BLOCK without it'; "
            "per ADR-0070 D4 the blocking gate is retired.",
        )

    def test_advisory_marker_present(self):
        """R-SENSITIVE section MUST contain the word 'advisory'.

        The rescoped rule is advisory, not blocking.  The word 'advisory'
        must appear in the section to make this explicit.
        """
        self.assertIn(
            "advisory",
            self.section.lower(),
            "R-SENSITIVE section must contain 'advisory' to state its non-blocking nature "
            "per ADR-0070 D4.",
        )

    def test_adr_0070_reference_present(self):
        """R-SENSITIVE section MUST reference ADR-0070.

        ADR-0070 D4 is the decision that retires the blocking gate; the section
        must cite it so the rationale is traceable.
        """
        self.assertRegex(
            self.section,
            r'ADR-0070',
            "R-SENSITIVE section must reference ADR-0070 (the ADR that retired the "
            "per-PR blocking gate per D4).",
        )

    def test_active_block_annotation_removed(self):
        """The 'ACTIVE' / 'BLOCK' activation annotation must be gone or changed.

        The old text started: 'ACTIVE — activated at PRD #813 closing slice per
        ADR-0064 D4. PRs touching the declared enforcement-path set require human
        ack before APPROVE; BLOCK without it.'

        After the fix, that annotation must be gone (or replaced with advisory framing).
        """
        self.assertNotIn(
            "ACTIVE — activated at PRD #813",
            self.section,
            "The old 'ACTIVE — activated at PRD #813' annotation must be replaced "
            "with advisory framing per ADR-0070 D4.",
        )


if __name__ == "__main__":
    unittest.main()
