"""
tests/test_pr_merge_lane_closing_comment_1507.py

Regression tests for PRD #1501 slice #1507 — criterion 27, the closing
comment `tools/pipe/pr-merge` posts on each issue it closes after a lane
merge (ADR-0090 D4 merge leg 3). Slice #1506 shipped the close itself
(criterion 26, tests/test_pr_merge_lane_1506.py) and deferred the comment
(A10).

Same harness as tests/test_pr_merge_lane_1506.py: gh is faked on PATH and
logs every call to a marker file; the lane head branch lives in a REAL
throwaway origin under the test's temp dir (rule #21); the trace log is
redirected there too. Every run sets PR_MERGE_BUDGET_S=0, so pr-merge
confirms the merge on its first poll and never chains record-green (each
test asserts that skip): these tests are about the closing comment only.

Covers PRD #1501 §2 criterion 27 (pr_merge_lane_closing_comment): when
pr-merge closes an issue after a lane merge, it posts on that issue a
comment giving the PR number and the merge sha. An issue whose close fails
was not closed, so it gets no such comment; a non-lane PR gets none.
"""
import json
import os
import platform
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
PR_MERGE = REPO_ROOT / "tools" / "pipe" / "pr-merge"


_FAKE_GH_BODY = r'''
import sys, os, json

args = sys.argv[1:]
p = os.environ.get("FAKE_GH_MARKER_FILE")
if p:
    with open(p, "a", encoding="utf-8") as f:
        f.write(" ".join(args) + "\n")
sub0 = args[0] if len(args) > 0 else ""
sub1 = args[1] if len(args) > 1 else ""

if sub0 == "pr" and sub1 == "view":
    print(os.environ.get("FAKE_GH_VIEW_JSON", json.dumps({"comments": []})))
    sys.exit(0)
elif sub0 == "pr" and sub1 == "merge":
    sys.exit(0)
elif sub0 == "api":
    print(os.environ.get("FAKE_GH_API_JSON", "{}"))
    sys.exit(0)
elif sub0 == "issue" and sub1 == "close":
    failing = os.environ.get("FAKE_GH_CLOSE_FAIL", "").split(",")
    sys.exit(1 if len(args) > 2 and args[2] in failing else 0)
elif sub0 == "issue" and sub1 == "comment":
    sys.exit(0)
else:
    sys.exit(0)
'''


def _write_fake_gh(dirpath):
    if platform.system() == "Windows":
        impl_path = os.path.join(dirpath, "_fake_gh_impl.py")
        with open(impl_path, "w", encoding="utf-8") as f:
            f.write(_FAKE_GH_BODY)
        bat_path = os.path.join(dirpath, "gh.bat")
        with open(bat_path, "w", encoding="utf-8", newline="\r\n") as f:
            f.write(f'@echo off\r\n"{sys.executable}" "{impl_path}" %*\r\n')
    else:
        sh_path = os.path.join(dirpath, "gh")
        with open(sh_path, "w", encoding="utf-8") as f:
            f.write("#!/usr/bin/env python3\n")
            f.write(_FAKE_GH_BODY)
        os.chmod(sh_path, 0o755)
    return dirpath


def _read_marker(path):
    p = Path(path)
    if not p.exists():
        return []
    return [l.strip() for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def _read_jsonl(path):
    p = Path(path)
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


APPROVE = "VERDICT: APPROVE\nMODEL: opus-5.5\nROUND: 1\nCRITIC: reviewer"


def _view(labels, head_ref, closes):
    return {
        "comments": [{"body": APPROVE}],
        "labels": [{"name": l} for l in labels],
        "body": "## Scope\nfixes\n" + "\n".join(f"Closes #{n}" for n in closes),
        "headRefName": head_ref,
    }


class ClosingCommentTestBase(unittest.TestCase):
    """Real throwaway origin ('o/r.git') with a 'develop' branch
    (pipeline_config's default here) and a work clone pr-merge runs from."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pr_merge_closing_comment_test_")
        origin = self.tmp.replace("\\", "/") + "/o/r.git"
        subprocess.run(["git", "init", "--bare", "-q", "-b", "develop", origin], check=True)
        seed = os.path.join(self.tmp, "seed")
        subprocess.run(["git", "init", "-q", "-b", "develop", seed], check=True)
        subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=seed, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=seed, check=True)
        Path(seed, "README.md").write_text("seed\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=seed, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=seed, check=True)
        subprocess.run(["git", "push", "-q", origin, "develop"], cwd=seed, check=True)
        self.work = os.path.join(self.tmp, "work")
        subprocess.run(["git", "clone", "-q", origin, self.work], check=True)
        self.marker = os.path.join(self.tmp, "gh_calls.marker")
        self.trace = os.path.join(self.tmp, "trace-v3.jsonl")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _push_head_branch(self, name):
        """A diff-empty head branch: zero lane commits, so the dispatch-window
        leg passes with no recorded window."""
        subprocess.run(["git", "checkout", "-q", "-b", name, "develop"], cwd=self.work, check=True)
        subprocess.run(["git", "push", "-q", "origin", name], cwd=self.work, check=True)
        subprocess.run(["git", "checkout", "-q", "develop"], cwd=self.work, check=True)

    def _merge(self, args, view, api=None, extra_env=None):
        env = os.environ.copy()
        env["PATH"] = _write_fake_gh(self.tmp) + os.pathsep + env.get("PATH", "")
        env.update({
            "TRACE_LOG_OVERRIDE": self.trace,
            "FAKE_GH_MARKER_FILE": self.marker,
            "FAKE_GH_VIEW_JSON": json.dumps(view),
            "FAKE_GH_API_JSON": json.dumps(api if api is not None else {"merged": True, "merge_commit_sha": "d00dad"}),
            "PR_MERGE_BUDGET_S": "0",
        })
        env.update(extra_env or {})
        cmd = [sys.executable, str(PR_MERGE)] + args
        result = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=self.work, env=env, timeout=60,
        )
        self.assertIn("record-green NOT chained", result.stderr, "record-green must not run in these tests")
        return result, _read_marker(self.marker)


def _comments_on(calls, num):
    return [c for c in calls if c.startswith(f"issue comment {num} ")]


class TestPrMergeLaneClosingComment(ClosingCommentTestBase):
    def test_pr_merge_lane_closing_comment_on_each_closed_issue(self):
        self._push_head_branch("fix/910-lane-f")
        result, calls = self._merge(["901"], _view(["lane"], "fix/910-lane-f", (910, 911)))
        self.assertEqual(result.returncode, 0, result.stderr)
        for num in ("910", "911"):
            comments = _comments_on(calls, num)
            self.assertEqual(len(comments), 1, calls)
            self.assertIn("--body", comments[0])
            self.assertIn("#901", comments[0])
            self.assertIn("d00dad", comments[0])
            # The comment follows that issue's close.
            self.assertLess(calls.index(f"issue close {num}"), calls.index(comments[0]))
        self.assertEqual(len([s for s in _read_jsonl(self.trace) if s["kind"] == "pr_merged"]), 1)

    def test_pr_merge_lane_closing_comment_confirm_mode(self):
        self._push_head_branch("fix/920-lane-g")
        result, calls = self._merge(
            ["--confirm", "902"], _view(["lane"], "fix/920-lane-g", (920,)),
            api={"merged": True, "merge_commit_sha": "e11e0f"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        comments = _comments_on(calls, "920")
        self.assertEqual(len(comments), 1, calls)
        self.assertIn("#902", comments[0])
        self.assertIn("e11e0f", comments[0])

    def test_pr_merge_lane_closing_comment_skipped_when_the_close_fails(self):
        self._push_head_branch("fix/930-lane-h")
        result, calls = self._merge(
            ["903"], _view(["lane"], "fix/930-lane-h", (930, 931)),
            extra_env={"FAKE_GH_CLOSE_FAIL": "931"},
        )
        # The merge is already confirmed and recorded: a failed close never
        # turns it into a failed merge. The unclosed issue stays open and
        # gets no "closed by" comment; stderr names it.
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(_comments_on(calls, "930")), 1, calls)
        self.assertEqual(_comments_on(calls, "931"), [], calls)
        self.assertIn("#931", result.stderr)

    def test_pr_merge_lane_closing_comment_names_a_missing_merge_sha(self):
        self._push_head_branch("fix/940-lane-i")
        result, calls = self._merge(
            ["904"], _view(["lane"], "fix/940-lane-i", (940,)), api={"merged": True},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        comments = _comments_on(calls, "940")
        self.assertEqual(len(comments), 1, calls)
        self.assertIn("#904", comments[0])
        self.assertIn("merge sha not reported", comments[0])
        self.assertNotIn("None", comments[0])

    def test_pr_merge_lane_closing_comment_never_on_a_non_lane_pr(self):
        result, calls = self._merge(["999"], _view(["slice"], "feat/999-ordinary", (999,)))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([c for c in calls if c.startswith("issue ")], [], calls)


if __name__ == "__main__":
    unittest.main()
