"""
Regression test for PRD #1075 criterion 10d — reviewer.md's stated hard-block
rule count must match the ACTUAL count of active `### R-` rule headings.

Root cause: reviewer.md's Output-format section stated "the Rubric line items
map 1:1 to the 12 hard-block rules above" — a number that predates several
R-* rules added since (R-FIXTURE, R-TRAILER, R-RULE-CHECK, R-PROVE, ...) and
never got bumped. The 2026-08-01 forensic audit (PRD #1075 §1) named this as
one of the "2 audit-found integrity defects" this slice fixes.

Method (mechanical, not read from memory — CLAUDE.md rule #18's discipline
extended to self-referential counts): count every `### R-<NAME> — <desc>`
heading in reviewer.md; a heading is RETIRED (excluded from the active count)
when its description clause starts with "retired" (case-insensitive) — this
is reviewer.md's own established convention (see R-SENSITIVE's heading:
"### R-SENSITIVE — retired; see promotion meta-tripwire (ADR-0070 D4)").
The stated count in the "map 1:1 to the N hard-block rules" sentence must
equal len(active headings).

This test FAILS on develop before the fix (stated=12, actual active=16) and
PASSES after (stated=16).

Runner: stdlib unittest + pytest compatible.
  python -m pytest tests/test_reviewer_rule_count_1085.py -v
"""

import re
import unittest
from pathlib import Path

REVIEWER_MD = Path(__file__).parent.parent / ".claude" / "pipeline" / "agents" / "reviewer.md"

_R_HEADING_RE = re.compile(r'^###\s+(R-[A-Z-]+)\s+—\s+(.+)$', re.MULTILINE)
_RULE_COUNT_CLAIM_RE = re.compile(r'map 1:1 to the (\d+) hard-block rules')


def _reviewer_text() -> str:
    return REVIEWER_MD.read_text(encoding="utf-8")


def _all_r_headings(text: str) -> list:
    """Return [(name, description), ...] for every '### R-NAME — desc' heading."""
    return _R_HEADING_RE.findall(text)


def _active_r_headings(text: str) -> list:
    """Active headings = all headings EXCEPT those whose description starts
    with 'retired' (case-insensitive) — reviewer.md's own convention."""
    return [
        name for name, desc in _all_r_headings(text)
        if not desc.strip().lower().startswith("retired")
    ]


class TestReviewerRuleCountMatchesHeadingCount(unittest.TestCase):
    """reviewer.md's stated hard-block rule count must equal the actual count
    of active (non-retired) '### R-' headings."""

    def setUp(self):
        self.text = _reviewer_text()

    def test_at_least_one_r_heading_found(self):
        """Sanity: the parser must find R- headings at all (guards against a
        regex/heading-format drift silently making this test vacuous)."""
        headings = _all_r_headings(self.text)
        self.assertGreater(len(headings), 0, "no '### R-NAME — desc' headings found in reviewer.md")

    def test_stated_count_claim_is_present(self):
        """The 'map 1:1 to the N hard-block rules' sentence must exist."""
        m = _RULE_COUNT_CLAIM_RE.search(self.text)
        self.assertIsNotNone(
            m,
            "reviewer.md must state 'map 1:1 to the N hard-block rules' in its "
            "Output format section",
        )

    def test_stated_count_equals_active_heading_count(self):
        """The stated N must equal the actual count of active R- headings."""
        m = _RULE_COUNT_CLAIM_RE.search(self.text)
        self.assertIsNotNone(m, "rule-count claim sentence not found")
        stated = int(m.group(1))
        active = _active_r_headings(self.text)
        self.assertEqual(
            stated,
            len(active),
            msg=(
                f"reviewer.md claims {stated} hard-block rules but {len(active)} "
                f"active '### R-' headings were found: {active}. "
                "Update the 'map 1:1 to the N hard-block rules' sentence."
            ),
        )

    def test_retired_rule_excluded_from_active_count(self):
        """R-SENSITIVE (explicitly retired) must NOT be counted as active."""
        active = _active_r_headings(self.text)
        self.assertNotIn(
            "R-SENSITIVE",
            active,
            "R-SENSITIVE is retired (per ADR-0070 D4) and must be excluded "
            "from the active hard-block rule count",
        )


if __name__ == "__main__":
    unittest.main()
