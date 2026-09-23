"""
tests/test_pr_merge_lane_1506.py

Regression tests for PRD #1501 slice #1506 — `tools/pipe/pr-merge`'s lane
legs (ADR-0090 D3/D4), gated on the `lane` label carried in the SAME
`gh pr view` call `_fetch_pr_view` widened (labels,body,headRefName) — no
new gh call on a non-lane PR (named risk, PRD #1501 constraint 5). Fakes
`gh` on PATH; the head-branch commit history for the window leg comes from
a REAL throwaway origin+develop-branch git repo built under pytest's
tmp_path (rule #21), mirroring tests/test_dispatch_lane_1506.py's harness —
never a network call.

Covers PRD #1501 §2 criteria 24-26 (criterion 27's closing comment is
deferred to slice 2 per the SPIDR note / A10):
  24 (pr_merge_lane_model): a lane PR whose latest APPROVE comment lacks a
     `MODEL:` line, or names Sonnet/Haiku, is refused before any merge call.
  25 (pr_merge_lane_windows): a lane PR with a non-merge commit authored
     outside every `dispatch`->`dispatch_end` window on trace_id
     `lane-<headRefName>` is refused before any merge call.
  26 (pr_merge_lane_closes): on a confirmed lane-PR merge, every
     `Closes #<n>` issue in the body is closed (no comment) — after merge
     confirmation, before the span append, never on the already-recorded
     idempotence shortcut.

Plus the named risk itself: an ordinary (non-lane) PR takes none of these
three legs — no extra gh call, no MODEL: demand, no window check.
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


# ---------------------------------------------------------------------------
# Fake-gh fixture: pr view / pr merge / api pulls / issue close, all logged
# to a marker file so tests can assert exactly which calls fired.
# ---------------------------------------------------------------------------

_FAKE_GH_BODY = r'''
import sys, os, json

def _log():
    p = os.environ.get("FAKE_GH_MARKER_FILE")
    if p:
        with open(p, "a", encoding="utf-8") as f:
            f.write(" ".join(sys.argv[1:]) + "\n")

_log()
args = sys.argv[1:]
sub0 = args[0] if len(args) > 0 else ""
sub1 = args[1] if len(args) > 1 else ""

if sub0 == "pr" and sub1 == "view":
    out = os.environ.get("FAKE_GH_VIEW_JSON", json.dumps({"comments": []}))
    exit_code = int(os.environ.get("FAKE_GH_VIEW_EXIT", "0"))
    print(out)
    sys.exit(exit_code)
elif sub0 == "pr" and sub1 == "merge":
    exit_code = int(os.environ.get("FAKE_GH_MERGE_EXIT", "0"))
    err = os.environ.get("FAKE_GH_MERGE_STDERR", "")
    if err:
        print(err, file=sys.stderr)
    sys.exit(exit_code)
elif sub0 == "pr" and sub1 == "update-branch":
    sys.exit(int(os.environ.get("FAKE_GH_UPDATE_BRANCH_EXIT", "0")))
elif sub0 == "pr" and sub1 == "checks":
    exit_code = int(os.environ.get("FAKE_GH_CHECKS_EXIT", "0"))
    out = os.environ.get("FAKE_GH_CHECKS_STDOUT", "")
    if out:
        print(out)
    sys.exit(exit_code)
elif sub0 == "api":
    out = os.environ.get("FAKE_GH_API_JSON", "{}")
    print(out)
    sys.exit(int(os.environ.get("FAKE_GH_API_EXIT", "0")))
elif sub0 == "issue" and sub1 == "close":
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


def _read_jsonl(path):
    p = Path(path)
    if not p.exists():
        return []
    lines = [l.strip() for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    return [json.loads(l) for l in lines]


def _read_marker(path):
    p = Path(path)
    if not p.exists():
        return []
    return [l.strip() for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


class PrMergeLaneTestBase(unittest.TestCase):
    """Real throwaway origin ('o/r.git', forward-slash-normalized so
    `_remote_owner_repo()`'s trailing-segment regex resolves owner=o
    repo=r) with a 'develop' branch — pipeline_config's own default, since
    no .claude/pipeline.conf exists in this throwaway repo — plus a work
    clone pr-merge runs from. Mirrors DispatchLaneTestBase exactly."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pr_merge_lane_test_")
        self.origin = self.tmp.replace("\\", "/") + "/o/r.git"
        subprocess.run(["git", "init", "--bare", "-q", "-b", "develop", self.origin], check=True)
        seed = os.path.join(self.tmp, "seed")
        subprocess.run(["git", "init", "-q", "-b", "develop", seed], check=True)
        subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=seed, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=seed, check=True)
        Path(seed, "README.md").write_text("seed\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=seed, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=seed, check=True)
        subprocess.run(["git", "push", "-q", self.origin, "develop"], cwd=seed, check=True)

        self.work = os.path.join(self.tmp, "work")
        subprocess.run(["git", "clone", "-q", self.origin, self.work], check=True)
        subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=self.work, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=self.work, check=True)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _push_head_branch(self, name, extra_commit_author_epoch=None):
        """Create `name` off develop's tip in `self.work` and push it. When
        `extra_commit_author_epoch` is given, adds one commit authored at
        that epoch (GIT_AUTHOR_DATE) before pushing — otherwise the branch
        stays diff-empty against develop (zero lane commits)."""
        subprocess.run(["git", "checkout", "-q", "-b", name, "develop"], cwd=self.work, check=True)
        if extra_commit_author_epoch is not None:
            Path(self.work, "widget.py").write_text("x = 1\n", encoding="utf-8")
            subprocess.run(["git", "add", "widget.py"], cwd=self.work, check=True)
            env = os.environ.copy()
            env["GIT_AUTHOR_DATE"] = f"{extra_commit_author_epoch} +0000"
            env["GIT_COMMITTER_DATE"] = f"{extra_commit_author_epoch} +0000"
            subprocess.run(["git", "commit", "-q", "-m", "lane work"], cwd=self.work, env=env, check=True)
        subprocess.run(["git", "push", "-q", "origin", name], cwd=self.work, check=True)
        subprocess.run(["git", "checkout", "-q", "develop"], cwd=self.work, check=True)

    def _run(self, args, env_updates):
        fake_gh_dir = _write_fake_gh(self.tmp)
        env = os.environ.copy()
        env["PATH"] = fake_gh_dir + os.pathsep + env.get("PATH", "")
        env.update(env_updates)
        cmd = [sys.executable, str(PR_MERGE)] + args
        return subprocess.run(cmd, capture_output=True, text=True, cwd=self.work, env=env, timeout=30)


def _lane_view(labels, head_ref, closes=(101,), approve_body=None):
    body = "\n".join(f"Closes #{n}" for n in closes)
    return {
        "comments": [{"body": approve_body}] if approve_body else [],
        "labels": [{"name": l} for l in labels],
        "body": body,
        "headRefName": head_ref,
    }


# ---------------------------------------------------------------------------
# 24: pr_merge_lane_model
# ---------------------------------------------------------------------------

class TestPrMergeLaneModel(PrMergeLaneTestBase):
    def test_pr_merge_lane_model_refuses_missing_model_line(self):
        self._push_head_branch("fix/701-lane-a")
        log_path = os.path.join(self.tmp, "trace-v3.jsonl")
        marker = os.path.join(self.tmp, "gh_calls.marker")
        view = _lane_view(["lane"], "fix/701-lane-a",
                           approve_body="VERDICT: APPROVE\nREASON: ok\nROUND: 1\nCRITIC: reviewer")
        result = self._run(["701"], {
            "TRACE_LOG_OVERRIDE": log_path,
            "FAKE_GH_VIEW_JSON": json.dumps(view),
            "FAKE_GH_MARKER_FILE": marker,
            "PR_MERGE_BUDGET_S": "5",
        })
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("MODEL", result.stderr)
        self.assertEqual(_read_jsonl(log_path), [])
        calls = _read_marker(marker)
        self.assertEqual([c for c in calls if c.startswith("pr merge")], [])

    def test_pr_merge_lane_model_refuses_sonnet(self):
        self._push_head_branch("fix/702-lane-b")
        log_path = os.path.join(self.tmp, "trace-v3.jsonl")
        view = _lane_view(["lane"], "fix/702-lane-b",
                           approve_body="VERDICT: APPROVE\nMODEL: sonnet-5\nROUND: 1\nCRITIC: reviewer")
        result = self._run(["702"], {
            "TRACE_LOG_OVERRIDE": log_path,
            "FAKE_GH_VIEW_JSON": json.dumps(view),
            "PR_MERGE_BUDGET_S": "5",
        })
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(_read_jsonl(log_path), [])

    def test_pr_merge_lane_model_admits_non_weak_model(self):
        # Diff-empty head branch -> zero lane commits -> window leg passes
        # trivially regardless of recorded dispatch windows.
        self._push_head_branch("fix/703-lane-c")
        log_path = os.path.join(self.tmp, "trace-v3.jsonl")
        marker = os.path.join(self.tmp, "gh_calls.marker")
        view = _lane_view(["lane"], "fix/703-lane-c",
                           approve_body="VERDICT: APPROVE\nMODEL: opus-5.5\nROUND: 1\nCRITIC: reviewer")
        result = self._run(["703"], {
            "TRACE_LOG_OVERRIDE": log_path,
            "FAKE_GH_VIEW_JSON": json.dumps(view),
            "FAKE_GH_MERGE_EXIT": "0",
            "FAKE_GH_API_JSON": json.dumps({"merged": True, "merge_commit_sha": "feedface"}),
            "FAKE_GH_MARKER_FILE": marker,
            "PR_MERGE_BUDGET_S": "10",
            "RECORD_GREEN_CI_STATUS": "fail",
            "RECORD_GREEN_TEST_LOG_PATH": os.path.join(self.tmp, "workflow-events.jsonl"),
        })
        self.assertEqual(result.returncode, 0, f"stdout={result.stdout!r} stderr={result.stderr!r}")
        lines = _read_jsonl(log_path)
        self.assertEqual(len([l for l in lines if l["kind"] == "pr_merged"]), 1)


# ---------------------------------------------------------------------------
# 25: pr_merge_lane_windows
# ---------------------------------------------------------------------------

class TestPrMergeLaneWindows(PrMergeLaneTestBase):
    def test_pr_merge_lane_windows_refuses_commit_outside_every_window(self):
        self._push_head_branch("fix/801-lane-d", extra_commit_author_epoch=1700000000)
        log_path = os.path.join(self.tmp, "trace-v3.jsonl")
        marker = os.path.join(self.tmp, "gh_calls.marker")
        # No dispatch/dispatch_end spans recorded at all for this lane ->
        # the commit falls outside every (empty) window.
        view = _lane_view(["lane"], "fix/801-lane-d",
                           approve_body="VERDICT: APPROVE\nMODEL: opus-5.5\nROUND: 1\nCRITIC: reviewer")
        result = self._run(["801"], {
            "TRACE_LOG_OVERRIDE": log_path,
            "FAKE_GH_VIEW_JSON": json.dumps(view),
            "FAKE_GH_MARKER_FILE": marker,
            "PR_MERGE_BUDGET_S": "10",
        })
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("window", result.stderr.lower())
        self.assertEqual(_read_jsonl(log_path), [])
        calls = _read_marker(marker)
        self.assertEqual([c for c in calls if c.startswith("pr merge")], [])

    def test_pr_merge_lane_windows_admits_commit_inside_recorded_window(self):
        self._push_head_branch("fix/802-lane-e", extra_commit_author_epoch=1700000000)
        log_path = os.path.join(self.tmp, "trace-v3.jsonl")
        # A dispatch/dispatch_end pair on trace_id lane-<head_ref> spanning
        # the commit's authored timestamp (2023-11-14T22:13:20Z).
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(json.dumps({
                "v": 3, "ts": "2023-11-14T22:00:00Z", "trace_id": "lane-fix/802-lane-e",
                "span_id": "s1", "kind": "dispatch", "attrs": {"lane": "fix/802-lane-e"},
            }) + "\n")
            f.write(json.dumps({
                "v": 3, "ts": "2023-11-14T23:00:00Z", "trace_id": "lane-fix/802-lane-e",
                "span_id": "s2", "kind": "dispatch_end", "attrs": {"lane": "fix/802-lane-e"},
            }) + "\n")
        view = _lane_view(["lane"], "fix/802-lane-e",
                           approve_body="VERDICT: APPROVE\nMODEL: opus-5.5\nROUND: 1\nCRITIC: reviewer")
        result = self._run(["802"], {
            "TRACE_LOG_OVERRIDE": log_path,
            "FAKE_GH_VIEW_JSON": json.dumps(view),
            "FAKE_GH_MERGE_EXIT": "0",
            "FAKE_GH_API_JSON": json.dumps({"merged": True, "merge_commit_sha": "abc999"}),
            "PR_MERGE_BUDGET_S": "10",
            "RECORD_GREEN_CI_STATUS": "fail",
            "RECORD_GREEN_TEST_LOG_PATH": os.path.join(self.tmp, "workflow-events.jsonl"),
        })
        self.assertEqual(result.returncode, 0, f"stdout={result.stdout!r} stderr={result.stderr!r}")
        lines = _read_jsonl(log_path)
        self.assertEqual(len([l for l in lines if l["kind"] == "pr_merged"]), 1)


# ---------------------------------------------------------------------------
# 26: pr_merge_lane_closes
# ---------------------------------------------------------------------------

class TestPrMergeLaneCloses(PrMergeLaneTestBase):
    def test_pr_merge_lane_closes_issues_on_confirmed_merge_no_comment(self):
        self._push_head_branch("fix/901-lane-f")
        log_path = os.path.join(self.tmp, "trace-v3.jsonl")
        marker = os.path.join(self.tmp, "gh_calls.marker")
        view = _lane_view(["lane"], "fix/901-lane-f", closes=(910, 911),
                           approve_body="VERDICT: APPROVE\nMODEL: opus-5.5\nROUND: 1\nCRITIC: reviewer")
        result = self._run(["901"], {
            "TRACE_LOG_OVERRIDE": log_path,
            "FAKE_GH_VIEW_JSON": json.dumps(view),
            "FAKE_GH_MERGE_EXIT": "0",
            "FAKE_GH_API_JSON": json.dumps({"merged": True, "merge_commit_sha": "d00dad"}),
            "FAKE_GH_MARKER_FILE": marker,
            "PR_MERGE_BUDGET_S": "10",
            "RECORD_GREEN_CI_STATUS": "fail",
            "RECORD_GREEN_TEST_LOG_PATH": os.path.join(self.tmp, "workflow-events.jsonl"),
        })
        self.assertEqual(result.returncode, 0, f"stdout={result.stdout!r} stderr={result.stderr!r}")
        calls = _read_marker(marker)
        close_calls = [c for c in calls if c.startswith("issue close")]
        self.assertEqual(sorted(close_calls), ["issue close 910", "issue close 911"])
        comment_calls = [c for c in calls if c.startswith("issue comment")]
        self.assertEqual(comment_calls, [], "criterion 27's closing comment is deferred to slice 2")

    def test_pr_merge_lane_closes_confirm_mode(self):
        self._push_head_branch("fix/902-lane-g")
        log_path = os.path.join(self.tmp, "trace-v3.jsonl")
        marker = os.path.join(self.tmp, "gh_calls.marker")
        view = _lane_view(["lane"], "fix/902-lane-g", closes=(920,),
                           approve_body="VERDICT: APPROVE\nMODEL: opus-5.5\nROUND: 1\nCRITIC: reviewer")
        result = self._run(["--confirm", "902"], {
            "TRACE_LOG_OVERRIDE": log_path,
            "FAKE_GH_VIEW_JSON": json.dumps(view),
            "FAKE_GH_API_JSON": json.dumps({"merged": True, "merge_commit_sha": "e11e"}),
            "FAKE_GH_MARKER_FILE": marker,
            "PR_MERGE_BUDGET_S": "10",
            "RECORD_GREEN_CI_STATUS": "fail",
            "RECORD_GREEN_TEST_LOG_PATH": os.path.join(self.tmp, "workflow-events.jsonl"),
        })
        self.assertEqual(result.returncode, 0, f"stdout={result.stdout!r} stderr={result.stderr!r}")
        calls = _read_marker(marker)
        self.assertIn("issue close 920", calls)

    def test_pr_merge_lane_closes_never_fires_on_already_recorded_shortcut(self):
        """The idempotence shortcut (a pr_merged span already exists) makes
        ZERO gh calls at all -- close-on-merge must not run on that path."""
        self._push_head_branch("fix/903-lane-h")
        log_path = os.path.join(self.tmp, "trace-v3.jsonl")
        marker = os.path.join(self.tmp, "gh_calls.marker")
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(json.dumps({
                "v": 3, "ts": "2026-08-02T09:00:00Z", "trace_id": "pr-903",
                "span_id": "seed1", "kind": "pr_merged",
                "attrs": {"pr": "903", "sha": "already"},
            }) + "\n")
        result = self._run(["--confirm", "903"], {
            "TRACE_LOG_OVERRIDE": log_path,
            "FAKE_GH_MARKER_FILE": marker,
        })
        self.assertEqual(result.returncode, 0, f"stdout={result.stdout!r} stderr={result.stderr!r}")
        self.assertEqual(_read_marker(marker), [])


# ---------------------------------------------------------------------------
# Named risk (PRD #1501 constraint 5): no lane leg leaks into a non-lane PR.
# ---------------------------------------------------------------------------

class TestPrMergeNonLaneUnaffected(PrMergeLaneTestBase):
    def test_non_lane_pr_no_model_demand_no_window_check_no_close_call(self):
        """A PR with NO `lane` label: an APPROVE comment with no MODEL: line
        still succeeds (no MODEL demand), no git-log window check runs (no
        head-branch git plumbing needed at all -- this repo's 'develop'
        checkout has no such branch, so a leaked window check would error),
        and no `issue close` call fires even though the body has a
        `Closes #<n>` line (ordinary R-CLOSES semantics are unaffected --
        closing is GitHub's own PR-merge behavior, not this leg's)."""
        log_path = os.path.join(self.tmp, "trace-v3.jsonl")
        marker = os.path.join(self.tmp, "gh_calls.marker")
        view = {
            "comments": [{"body": "VERDICT: APPROVE\nREASON: ok\nROUND: 1\nCRITIC: reviewer"}],
            "labels": [{"name": "slice"}],
            "body": "Closes #999",
            "headRefName": "feat/999-ordinary",
        }
        result = self._run(["999"], {
            "TRACE_LOG_OVERRIDE": log_path,
            "FAKE_GH_VIEW_JSON": json.dumps(view),
            "FAKE_GH_MERGE_EXIT": "0",
            "FAKE_GH_API_JSON": json.dumps({"merged": True, "merge_commit_sha": "0ff1ce"}),
            "FAKE_GH_MARKER_FILE": marker,
            "PR_MERGE_BUDGET_S": "10",
            "RECORD_GREEN_CI_STATUS": "fail",
            "RECORD_GREEN_TEST_LOG_PATH": os.path.join(self.tmp, "workflow-events.jsonl"),
        })
        self.assertEqual(result.returncode, 0, f"stdout={result.stdout!r} stderr={result.stderr!r}")
        calls = _read_marker(marker)
        self.assertEqual([c for c in calls if c.startswith("issue close")], [])
        view_calls = [c for c in calls if c.startswith("pr view")]
        self.assertEqual(len(view_calls), 1, f"exactly one pr view call expected; calls={calls}")


if __name__ == "__main__":
    unittest.main()
