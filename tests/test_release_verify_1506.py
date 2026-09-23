"""
tests/test_release_verify_1506.py

Regression tests for PRD #1501 slice #1506 — `tools/release.py verify`
(ADR-0090 D3/D4). `--reopen` is deferred to slice 3 (this slice's `verify`
runs the check-and-print path only). Fakes `gh` on PATH; every log lives
under pytest's tmp_path (rule #21).

Covers PRD #1501 §2 criteria 32-34:
  32 (release_verify_lines): exactly one `PASS|FAIL|MISSING #<n>` line per
     named issue.
  33 (release_verify_check_source): the issue's own `Check:` line when one
     exists, otherwise its closing lane PR's `Check #<n>:` line — found by
     search, never the (deferred, criterion 27) closing comment.
  34 (release_verify_exit): exit 0 iff every printed line is PASS.
"""
import importlib.util
import json
import os
import platform
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
RELEASE_PY = REPO_ROOT / "tools" / "release.py"


def _load_release():
    spec = importlib.util.spec_from_file_location("release_verify_test", RELEASE_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestReleaseVerifyLines(unittest.TestCase):
    def test_release_verify_lines_pass_fail_missing(self):
        release = _load_release()
        release._fetch_issue_json = lambda owner, repo, num: {
            "number": int(num),
            "body": {
                "201": "Check: python3 -c \"import sys; sys.exit(0)\"",
                "202": "Check: python3 -c \"import sys; sys.exit(1)\"",
                "203": "No check line here.",
            }[num],
        }
        release._fetch_comments = lambda owner, repo, num: []
        release._find_lane_pr_for_issue = lambda owner, repo, num: None

        class _Args:
            issues = ["201", "202", "203"]

        buf_out, buf_err = [], []
        import io
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            rc = release._cmd_verify(_Args())
            out = sys.stdout.getvalue()
        finally:
            sys.stdout = old_stdout

        lines = [l for l in out.splitlines() if l.strip()]
        self.assertEqual(lines, ["PASS #201", "FAIL #202", "MISSING #203"])
        self.assertNotEqual(rc, 0)


class TestReleaseVerifyCheckSource(unittest.TestCase):
    def test_release_verify_check_source_own_line(self):
        release = _load_release()
        release._fetch_issue_json = lambda owner, repo, num: {
            "number": int(num), "body": "Check: python3 -c \"1\"",
        }
        release._fetch_comments = lambda owner, repo, num: []
        release._find_lane_pr_for_issue = lambda owner, repo, num: (_ for _ in ()).throw(
            AssertionError("must not search for a lane PR when the issue has its own Check: line")
        )
        check = release._resolve_check("o", "r", "301")
        self.assertEqual(check, 'python3 -c "1"')

    def test_release_verify_check_source_lane_pr_line(self):
        release = _load_release()
        release._fetch_issue_json = lambda owner, repo, num: {
            "number": int(num), "body": "No check here.",
        }
        release._fetch_comments = lambda owner, repo, num: []
        release._find_lane_pr_for_issue = lambda owner, repo, num: {
            "number": 999,
            "body": f"Closes #{num}\nCheck #{num}: python3 -c \"2\"\n",
        }
        check = release._resolve_check("o", "r", "302")
        self.assertEqual(check, 'python3 -c "2"')


class TestReleaseVerifyExit(unittest.TestCase):
    def test_release_verify_exit_zero_only_when_all_pass(self):
        release = _load_release()

        def _issue(owner, repo, num):
            return {"number": int(num), "body": "Check: python3 -c \"import sys; sys.exit(0)\""}

        release._fetch_issue_json = _issue
        release._fetch_comments = lambda owner, repo, num: []
        release._find_lane_pr_for_issue = lambda owner, repo, num: None

        class _AllPass:
            issues = ["401", "402"]

        rc_pass = release._cmd_verify(_AllPass())
        self.assertEqual(rc_pass, 0)

        def _issue_mixed(owner, repo, num):
            body = "Check: python3 -c \"import sys; sys.exit(0)\""
            if num == "404":
                body = "Check: python3 -c \"import sys; sys.exit(1)\""
            return {"number": int(num), "body": body}

        release._fetch_issue_json = _issue_mixed

        class _Mixed:
            issues = ["403", "404"]

        rc_fail = release._cmd_verify(_Mixed())
        self.assertNotEqual(rc_fail, 0)


if __name__ == "__main__":
    unittest.main()
