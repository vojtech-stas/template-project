"""
tests/test_release_packet_1506.py

Regression tests for PRD #1501 slice #1506 — `tools/release.py`'s
`build_packet()` library function, the packet `tools/pipe/dispatch --lane`
prints to stdout (ADR-0090 D3). Fakes `gh` on PATH; the excerpt read is a
REAL `git show <sha>:<path>` against a throwaway git repo built under
pytest's tmp_path (rule #21) — never against the checkout under test.

Covers PRD #1501 §2 criteria 21, 23 (criterion 22, freshness across two
shas, is deferred to slice 2 per the SPIDR fallback, A10):
  21 (lane_packet_shape): the sha, then per bug its path:line ref, a
     ±20-line excerpt at that sha, and its Check: line or CHECK: MISSING.
  23 (lane_packet_untrusted): a comment whose author association is not
     OWNER/MEMBER/COLLABORATOR contributes no ref and no check.
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
RELEASE_PY = REPO_ROOT / "tools" / "release.py"


def _load_release():
    spec = importlib.util.spec_from_file_location("release_packet_test", RELEASE_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class PacketTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="release_packet_test_"))
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)
        subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=self.repo, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=self.repo, check=True)
        target = self.repo / "widget.py"
        lines = [f"line {n}" for n in range(1, 61)]
        target.write_text("\n".join(lines) + "\n", encoding="utf-8")
        subprocess.run(["git", "add", "widget.py"], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=self.repo, check=True)
        self.sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.repo, capture_output=True, text=True, check=True,
        ).stdout.strip()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)


def _comment(body, association):
    return {"body": body, "author_association": association}


class TestLanePacketShape(PacketTestBase):
    def test_lane_packet_shape_sha_ref_excerpt_check(self):
        release = _load_release()
        release._fetch_issue_json = lambda owner, repo, num: {
            "number": int(num),
            "body": "Broken at `widget.py:30`.\nCheck: python3 -c \"1\"",
        }
        release._fetch_comments = lambda owner, repo, num: []

        packet = release.build_packet("o", "r", ["101"], self.sha, repo_root=str(self.repo))

        self.assertIn(f"SHA: {self.sha}", packet)
        self.assertIn("#### Bug #101", packet)
        self.assertIn("widget.py:30", packet)
        # ±20-line excerpt around line 30 -> lines 10..50 inclusive.
        self.assertIn("line 10", packet)
        self.assertIn("line 50", packet)
        self.assertNotIn("line 9\n", packet)
        self.assertIn('Check: python3 -c "1"', packet)
        self.assertNotIn("CHECK: MISSING", packet)

    def test_lane_packet_shape_missing_check(self):
        release = _load_release()
        release._fetch_issue_json = lambda owner, repo, num: {
            "number": int(num),
            "body": "Broken at `widget.py:5`. No check line here.",
        }
        release._fetch_comments = lambda owner, repo, num: []

        packet = release.build_packet("o", "r", ["102"], self.sha, repo_root=str(self.repo))

        self.assertIn("CHECK: MISSING", packet)


class TestLanePacketUntrusted(PacketTestBase):
    def test_lane_packet_untrusted_comment_excluded(self):
        release = _load_release()
        release._fetch_issue_json = lambda owner, repo, num: {
            "number": int(num),
            "body": "No ref in the body itself.",
        }
        release._fetch_comments = lambda owner, repo, num: [
            _comment("Untrusted ref at `widget.py:1`.\nCheck: echo untrusted", "NONE"),
            _comment("Trusted ref at `widget.py:40`.\nCheck: echo trusted", "COLLABORATOR"),
        ]

        packet = release.build_packet("o", "r", ["103"], self.sha, repo_root=str(self.repo))

        self.assertIn("widget.py:40", packet)
        self.assertIn("Check: echo trusted", packet)
        self.assertNotIn("widget.py:1`", packet)
        self.assertNotIn("echo untrusted", packet)


if __name__ == "__main__":
    unittest.main()
