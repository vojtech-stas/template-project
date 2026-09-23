"""
tests/test_lane_mode_prompts_r3_1506.py

PR #1528 round-2 BLOCK recommendations 2 and 3 (slice #1506 / PRD #1501,
ADR-0090 D4). Static reads of the lane-mode prompt surfaces, the only
enforcement a prompt clause has.

  Rec 2  a Check that "fails" at the packet sha only because the test it
         names does not exist there (pytest exit 4, "no tests ran") proves
         nothing. Lane mode requires the R-PROVE fails-before run of the
         Check at the test-only commit, where it must fail on a real test
         failure; the reviewer's Check-line leg re-derives that run.
  Rec 3  until #1529's mechanical fix lands in slice #1507, QD11 runs only
         orchestrator-supervised lanes.
"""
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
IMPLEMENTER = REPO_ROOT / ".claude" / "agents" / "implementer.md"
REVIEWER = REPO_ROOT / ".claude" / "agents" / "reviewer.md"
SHIP = REPO_ROOT / ".claude" / "skills" / "ship" / "SKILL.md"


def _section(path, heading_re, level):
    """Text from the first line matching `heading_re` up to (not including)
    the next heading of `level` or shallower."""
    lines = path.read_text(encoding="utf-8").splitlines()
    stop = re.compile(r"^#{1,%d} " % level)
    out, inside = [], False
    for line in lines:
        if inside and stop.match(line):
            break
        if not inside and re.match(heading_re, line):
            inside = True
        if inside:
            out.append(line)
    return "\n".join(out)


def _lines_with_all(text, *needles):
    return [l for l in text.splitlines() if all(n.lower() in l.lower() for n in needles)]


class TestRec2CheckFailsAtTheTestOnlyCommit(unittest.TestCase):
    def test_rec2_lane_mode_requires_the_fails_before_run_at_the_test_only_commit(self):
        hits = _lines_with_all(
            _section(IMPLEMENTER, r"^## Lane mode", 2),
            "test-only commit", "R-PROVE", "non-zero", "## Verification",
        )
        self.assertTrue(hits, "Lane mode must require the Check's fails-before run at the test-only commit")
        text = " ".join(hits).lower()
        self.assertIn("exit 4", text)
        self.assertIn("no tests ran", text)

    def test_rec2_reviewer_check_leg_re_derives_the_test_only_commit_run(self):
        hits = _lines_with_all(
            _section(REVIEWER, r"^### R-PR-BODY ", 3),
            "Check #<n>:", "test-only commit", "non-zero",
        )
        self.assertTrue(hits, "the reviewer's Check-line lane leg must run the Check at the test-only commit")
        text = " ".join(hits).lower()
        self.assertIn("exit 4", text)
        self.assertIn("does not fail at test-only commit", text)


class TestRec3Qd11InterimSupervision(unittest.TestCase):
    def test_rec3_qd11_runs_only_orchestrator_supervised_lanes_until_1529(self):
        hits = _lines_with_all(
            _section(SHIP, r"^### QD11\. Release mode", 3),
            "#1529", "#1507", "orchestrator-supervised",
        )
        self.assertTrue(hits, "QD11 must carry the interim line: only orchestrator-supervised lanes until #1529 lands")


if __name__ == "__main__":
    unittest.main()
