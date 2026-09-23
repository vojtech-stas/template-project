"""
tests/test_lane_packet_fresh_1507.py

Regression tests for PRD #1501 slice #1507 — criterion 22, lane packet
freshness (ADR-0090 D3: "`tools/pipe/dispatch --lane` ... fetches
`origin/develop`" and the packet "is fresh at dispatch"). Slice #1506
deferred this criterion (A10).

Driven end to end through `tools/pipe/dispatch --lane`, never
`build_packet()` alone: the packet is fresh only if dispatch itself fetches
the integration branch and excerpts at the sha it fetched. The integration
branch lives in a REAL throwaway origin built under the test's temp dir
(rule #21), the same harness as tests/test_dispatch_lane_1506.py; gh is
faked on PATH; the trace log is redirected into the temp dir.

Covers PRD #1501 §2 criterion 22 (lane_packet_fresh): when a cited file
changes between two integration-branch shas, the packet built after the
change carries the new text. When the fetch fails, dispatch refuses with no
packet and no span rather than excerpt a stale local ref.
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
DISPATCH = REPO_ROOT / "tools" / "pipe" / "dispatch"


_FAKE_GH_BODY = r'''
import sys, os, json

def _routes():
    p = os.environ.get("FAKE_GH_ROUTES_FILE")
    if not p or not os.path.exists(p):
        return {}
    with open(p, encoding="utf-8") as f:
        return json.load(f)

def _key():
    a = sys.argv[1:]
    if not a:
        return ""
    if a[0] == "api":
        path = a[1] if len(a) > 1 else ""
        method = "GET"
        if "-X" in a:
            method = a[a.index("-X") + 1]
        fields = []
        i = 0
        while i < len(a):
            if a[i] in ("-f", "-F") and i + 1 < len(a):
                fields.append(a[i + 1])
            i += 1
        fields_str = " ".join(sorted(fields))
        return ("API " + method + " " + path + " " + fields_str).strip()
    return " ".join(a)

routes = _routes()
key = _key()
if key in routes:
    out, code = routes[key]
    sys.stdout.write(out)
    sys.exit(code)
sys.stderr.write("fake gh: unrouted call: " + key + "\n")
sys.exit(1)
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
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def _git(args, cwd):
    return subprocess.run(
        ["git"] + args, cwd=cwd, check=True, capture_output=True, text=True,
    ).stdout.strip()


LANE = "fix/701-lane-widget-py"
ISSUE = {
    "number": 701,
    "state": "open",
    "labels": [{"name": "bug"}],
    "milestone": {"number": 1, "title": "v1.0"},
    "body": "Broken at `widget.py:30`.\nCheck: python3 -c \"1\"",
}


class LanePacketFreshTestBase(unittest.TestCase):
    """A throwaway origin ('o/r.git', so `_remote_owner_repo()` resolves
    owner=o repo=r) whose 'develop' branch (pipeline_config's default, as no
    .claude/pipeline.conf exists here) carries a 60-line widget.py; a seed
    clone that moves 'develop' on; and a work clone dispatch runs from."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="lane_packet_fresh_test_")
        base = self.tmp.replace("\\", "/")
        self.origin = base + "/o/r.git"
        _git(["init", "--bare", "-q", "-b", "develop", self.origin], self.tmp)
        self.seed = os.path.join(self.tmp, "seed")
        _git(["init", "-q", "-b", "develop", self.seed], self.tmp)
        _git(["config", "user.email", "t@example.com"], self.seed)
        _git(["config", "user.name", "t"], self.seed)
        self._write_widget(30, "line 30")
        _git(["add", "widget.py"], self.seed)
        _git(["commit", "-q", "-m", "seed"], self.seed)
        _git(["push", "-q", self.origin, "develop"], self.seed)
        self.work = os.path.join(self.tmp, "work")
        _git(["clone", "-q", self.origin, self.work], self.tmp)

        fake_gh_dir = _write_fake_gh(self.tmp)
        routes = {
            "API GET repos/o/r/issues/701": [json.dumps(ISSUE), 0],
            "API GET repos/o/r/issues/701/comments per_page=100": ["[]", 0],
        }
        routes_path = os.path.join(self.tmp, "routes.json")
        with open(routes_path, "w", encoding="utf-8") as f:
            json.dump(routes, f)
        self.trace_path = os.path.join(self.tmp, "trace-v3.jsonl")
        self.env = os.environ.copy()
        self.env["PATH"] = fake_gh_dir + os.pathsep + self.env.get("PATH", "")
        self.env["FAKE_GH_ROUTES_FILE"] = routes_path
        self.env["TRACE_LOG_OVERRIDE"] = self.trace_path

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_widget(self, changed_line, changed_text):
        lines = [f"line {n}" for n in range(1, 61)]
        lines[changed_line - 1] = changed_text
        Path(self.seed, "widget.py").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _move_develop(self, text):
        """Change widget.py's line 30 on origin's develop; return the new sha.
        The work clone is NOT fetched here: only dispatch may fetch it."""
        self._write_widget(30, text)
        _git(["commit", "-q", "-am", "change line 30"], self.seed)
        _git(["push", "-q", self.origin, "develop"], self.seed)
        return _git(["rev-parse", "HEAD"], self.seed)

    def _dispatch(self, args):
        cmd = [sys.executable, str(DISPATCH)] + args
        return subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", cwd=self.work, env=self.env, timeout=60,
        )

    def _dispatch_lane(self):
        return self._dispatch(["--lane", LANE, "--milestone", "v1.0", "--model", "sonnet", "701"])


class TestLanePacketFresh(LanePacketFreshTestBase):
    def test_lane_packet_fresh_after_the_cited_file_changes(self):
        sha1 = _git(["rev-parse", "HEAD"], self.seed)
        first = self._dispatch_lane()
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn(f"SHA: {sha1}", first.stdout)
        self.assertIn("line 30\n", first.stdout)
        end = self._dispatch(["--end", "--lane", LANE, "--result", "BLOCKED"])
        self.assertEqual(end.returncode, 0, end.stderr)

        sha2 = self._move_develop("fresh text at thirty")
        self.assertNotEqual(sha1, sha2)
        second = self._dispatch_lane()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn(f"SHA: {sha2}", second.stdout)
        self.assertIn("fresh text at thirty", second.stdout)
        self.assertNotIn("line 30\n", second.stdout)

        dispatch_spans = [s for s in _read_jsonl(self.trace_path) if s["kind"] == "dispatch"]
        self.assertEqual([s["attrs"]["sha"] for s in dispatch_spans], [sha1, sha2])

    def test_lane_packet_fresh_refuses_when_the_fetch_fails(self):
        # The work clone's origin/develop still names the old sha, but the
        # remote has moved on and can no longer be fetched: excerpting the
        # stale local ref would brief the builder with text that is gone.
        stale = _git(["rev-parse", "origin/develop"], self.work)
        self._move_develop("fresh text at thirty")
        gone = self.tmp.replace("\\", "/") + "/gone/o/r.git"
        _git(["remote", "set-url", "origin", gone], self.work)

        result = self._dispatch_lane()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertNotIn("SHA:", result.stdout)
        self.assertNotIn(stale, result.stdout)
        self.assertIn("fetch", result.stderr)
        self.assertEqual(_read_jsonl(self.trace_path), [])


if __name__ == "__main__":
    unittest.main()
