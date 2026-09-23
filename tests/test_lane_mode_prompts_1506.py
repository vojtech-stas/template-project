"""
tests/test_lane_mode_prompts_1506.py

PR #1528 fix round (slice #1506 / PRD #1501, ADR-0090 D4): the prompt-side
findings of the orchestrator-supervised lane run (lane PR #1539). Static
reads of `.claude/agents/implementer.md` `## Lane mode` and
`.claude/agents/reviewer.md`, the only enforcement a prompt clause has
(its mechanical twins are the code tests in
`test_release_check_resolver_1506.py` and
`test_pr_merge_lane_closes_anchor_1506.py`).

  R2  Lane mode says a Check line is plain text: no backticks.
  R3  Lane mode makes the builder run the exact Check at the packet sha,
      show it exits non-zero, and paste that run under `## Verification`
      (a Check that already passes there cannot tell fix from bug).
  R4  reviewer.md R-PR-BODY carries the lane leg for the `Check #<n>:`
      line: exactly one plain-text line per bug without an issue `Check:`,
      failing at the packet sha and passing at head, re-derived.
  R7  both lane-mode surfaces carry an ISO-003 clause: temporary worktrees
      only under the agent's own worktree or the system temp dir, uniquely
      named, and an agent removes only worktrees it created.
  R8  reviewer.md's R-CLOSES lane leg names the same anchored closing form
      `tools/pipe/pr-merge` closes on merge, and Lane mode tells the
      builder to write each `Closes #<n>` alone on its own line.
"""
import importlib.machinery
import importlib.util
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
IMPLEMENTER = REPO_ROOT / ".claude" / "agents" / "implementer.md"
REVIEWER = REPO_ROOT / ".claude" / "agents" / "reviewer.md"
PR_MERGE = REPO_ROOT / "tools" / "pipe" / "pr-merge"


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


def _lane_mode():
    return _section(IMPLEMENTER, r"^## Lane mode", 2)


def _reviewer_rule(rule):
    return _section(REVIEWER, r"^### %s " % re.escape(rule), 3)


def _lines_with_all(text, *needles):
    return [l for l in text.splitlines() if all(n.lower() in l.lower() for n in needles)]


class TestLaneModeImplementer(unittest.TestCase):
    def test_lane_mode_section_exists(self):
        self.assertTrue(_lane_mode().startswith("## Lane mode"))

    def test_r2_check_line_is_plain_text(self):
        self.assertTrue(
            _lines_with_all(_lane_mode(), "Check #<n>:", "plain text", "backtick"),
            "Lane mode must state the Check line is plain text, with no backticks",
        )

    def test_r3_check_fails_at_packet_sha_in_verification(self):
        self.assertTrue(
            _lines_with_all(_lane_mode(), "packet", "sha", "non-zero", "## Verification"),
            "Lane mode must require the exact Check run at the packet sha, exiting "
            "non-zero, pasted under ## Verification",
        )

    def test_r7_iso003_clause(self):
        hits = _lines_with_all(_lane_mode(), "ISO-003", "worktree")
        self.assertTrue(hits, "Lane mode must carry an ISO-003 worktree clause")
        clause = " ".join(hits).lower()
        self.assertIn("system temp", clause)
        self.assertIn("unique", clause)
        self.assertIn("created", clause)

    def test_r8_closes_alone_on_its_own_line(self):
        self.assertTrue(
            _lines_with_all(_lane_mode(), "Closes #<n>", "own line"),
            "Lane mode must tell the builder to write each Closes #<n> on its own line",
        )


class TestReviewerLaneLegs(unittest.TestCase):
    def test_r4_pr_body_check_line_lane_leg(self):
        leg = _lines_with_all(_reviewer_rule("R-PR-BODY"), "lane", "Check #<n>:", "packet", "non-zero")
        self.assertTrue(leg, "R-PR-BODY must carry the lane leg for Check #<n>: lines")
        text = " ".join(leg).lower()
        self.assertIn("plain-text", text)
        self.assertIn("exit 0", text)

    def test_r7_reviewer_iso003_clause(self):
        hits = _lines_with_all(_reviewer_rule("R-PR-BODY"), "ISO-003", "worktree")
        self.assertTrue(hits, "the reviewer's Check-line lane leg must carry an ISO-003 worktree clause")
        clause = " ".join(hits).lower()
        self.assertIn("system temp", clause)
        self.assertIn("unique", clause)
        self.assertIn("created", clause)

    def test_r8_r_closes_lane_leg_names_pr_merges_anchored_form(self):
        loader = importlib.machinery.SourceFileLoader("pr_merge_prompt_test", str(PR_MERGE))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        mod = importlib.util.module_from_spec(spec)
        loader.exec_module(mod)
        pattern = mod._LANE_CLOSES_RE.pattern
        leg = _lines_with_all(_reviewer_rule("R-CLOSES"), "lane", "pr-merge")
        self.assertTrue(leg, "R-CLOSES must carry its lane leg")
        self.assertIn("`%s`" % pattern, " ".join(leg))


if __name__ == "__main__":
    unittest.main()
