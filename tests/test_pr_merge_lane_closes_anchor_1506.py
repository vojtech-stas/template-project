"""
tests/test_pr_merge_lane_closes_anchor_1506.py

PR #1528 fix round, finding R8 (slice #1506 / PRD #1501 criterion 26,
ADR-0090 D4): `tools/pipe/pr-merge`'s close-on-merge leg read closing
references with the unanchored, case-insensitive `closes\\s*#(\\d+)` over
the whole PR body, so prose such as "step 5 (closes #1448)" would close
#1448 on merge (round-2 BLOCK on lane PR #1539, issuecomment-5796935363).

The leg now closes exactly the issues named on whole `Closes #<n>` lines
(`^Closes #(\\d+)\\s*$`, multiline). The in-process test stubs `_run_gh`,
so no gh call and no issue close ever leaves the test.
"""
import importlib.machinery
import importlib.util
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
PR_MERGE = REPO_ROOT / "tools" / "pipe" / "pr-merge"


def _load_pr_merge(name="pr_merge_closes_anchor_test"):
    loader = importlib.machinery.SourceFileLoader(name, str(PR_MERGE))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class _Res:
    returncode, stdout, stderr = 0, "", ""


def _closed_numbers(body):
    mod = _load_pr_merge()
    calls = []

    def _fake_run_gh(args):
        calls.append(list(args))
        return _Res()

    mod._run_gh = _fake_run_gh
    mod._close_lane_issues({"body": body})
    assert all(c[:2] == ["issue", "close"] for c in calls), calls
    return [c[2] for c in calls]


class TestLaneClosesAnchored(unittest.TestCase):
    def test_r8_prose_closes_mention_closes_nothing(self):
        body = (
            "Closes #1525\n"
            "\n"
            "## Scope\n"
            "- step 5 (closes #1448) is untouched here.\n"
            "This also closes #77 in passing.\n"
        )
        self.assertEqual(_closed_numbers(body), ["1525"])

    def test_r8_only_whole_closes_lines_count(self):
        body = (
            "Closes #910\r\n"
            "closes #911\n"
            "  Closes #912\n"
            "Closes #913 and #914\n"
            "Closes: #915\n"
            "Closes #916   \n"
            "Closes #917"
        )
        self.assertEqual(_closed_numbers(body), ["910", "916", "917"])

    def test_r8_multi_bug_lane_closes_each_line(self):
        self.assertEqual(_closed_numbers("Closes #101\nCloses #102\nCloses #103\n"), ["101", "102", "103"])

    def test_r8_pattern_is_the_documented_anchored_form(self):
        mod = _load_pr_merge("pr_merge_closes_anchor_pattern")
        self.assertEqual(mod._LANE_CLOSES_RE.pattern, r"^Closes #(\d+)\s*$")


if __name__ == "__main__":
    unittest.main()
