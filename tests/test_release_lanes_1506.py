"""
tests/test_release_lanes_1506.py

Regression tests for PRD #1501 slice #1506 — `tools/release.py lanes`
(ADR-0090 D3). Fakes `gh` on PATH; no `--split` in slice 1 (that lands in
slice 2 — SPIDR fallback A10). Every log lives under pytest's tmp_path
(rule #21).

Covers PRD #1501 §2 criteria 16-17:
  16 (release_lanes_shared_path): bugs citing a common path, directly or
     through a chain, share one `group` id.
  17 (release_lanes_no_path): a bug with no cited path gets `exclusive`.
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
RELEASE_PY = REPO_ROOT / "tools" / "release.py"


_FAKE_GH_BODY = r'''
import sys, os, json

def _log():
    p = os.environ.get("FAKE_GH_LOG_FILE")
    if p:
        with open(p, "a", encoding="utf-8") as f:
            f.write(" ".join(sys.argv[1:]) + "\n")

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

_log()
routes = _routes()
key = _key()
if key in routes:
    out, code = routes[key]
    sys.stdout.write(out)
    sys.exit(code)
sys.stdout.write("")
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


def _issue(number, body="", milestone_title="v1.0"):
    return {
        "number": number,
        "state": "open",
        "labels": [{"name": "bug"}],
        "milestone": {"number": 1, "title": milestone_title},
        "body": body,
        "title": f"bug {number}",
    }


class LanesTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="release_lanes_test_")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, args, issues, evidence=None):
        fake_gh_dir = _write_fake_gh(self.tmp)
        log_path = os.path.join(self.tmp, "gh-calls.log")
        routes = {
            "API GET repos/o/r/issues per_page=100 state=open": [json.dumps(issues), 0],
        }
        # No trusted-comment fetch needed when --evidence supplies paths;
        # canned empty-comments response otherwise (issue bodies alone
        # carry the refs in these fixtures).
        for i in issues:
            routes[f"API GET repos/o/r/issues/{i['number']}/comments per_page=100"] = ["[]", 0]
        routes_path = os.path.join(self.tmp, "routes.json")
        with open(routes_path, "w", encoding="utf-8") as f:
            json.dump(routes, f)

        env = os.environ.copy()
        env["PATH"] = fake_gh_dir + os.pathsep + env.get("PATH", "")
        env["FAKE_GH_LOG_FILE"] = log_path
        env["FAKE_GH_ROUTES_FILE"] = routes_path

        extra_args = list(args)
        if evidence is not None:
            evidence_path = os.path.join(self.tmp, "evidence.json")
            with open(evidence_path, "w", encoding="utf-8") as f:
                json.dump(evidence, f)
            extra_args += ["--evidence", evidence_path]

        origin_repo = os.path.join(self.tmp, "origin.git")
        subprocess.run(["git", "init", "--bare", "-q", origin_repo], check=True)
        cwd = os.path.join(self.tmp, "work")
        os.makedirs(cwd, exist_ok=True)
        subprocess.run(["git", "init", "-q"], cwd=cwd, check=True)
        subprocess.run(["git", "remote", "add", "origin", "https://github.com/o/r.git"], cwd=cwd, check=True)

        cmd = [sys.executable, str(RELEASE_PY), "lanes"] + extra_args
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd, env=env, timeout=30)
        lanes = json.loads(result.stdout) if result.returncode == 0 and result.stdout.strip() else None
        return result, lanes


class TestReleaseLanesSharedPath(LanesTestBase):
    def test_release_lanes_shared_path_direct(self):
        issues = [
            _issue(101, body="Seen at `dashboard/health.py:100`."),
            _issue(102, body="Also broken at `dashboard/health.py:200`."),
        ]
        result, lanes = self._run(["v1.0"], issues)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(lanes), 1, lanes)
        self.assertEqual(sorted(lanes[0]["issues"]), ["101", "102"])
        self.assertFalse(lanes[0]["exclusive"])
        self.assertIsNotNone(lanes[0]["group"])

    def test_release_lanes_shared_path_chain(self):
        # 201<->A, 202 shares A and B, 203<->B: a 3-bug chain via 2 files.
        issues = [
            _issue(201, body="`tools/release.py:10`."),
            _issue(202, body="`tools/release.py:20` and `tools/pipe/dispatch:30`."),
            _issue(203, body="`tools/pipe/dispatch:40`."),
        ]
        result, lanes = self._run(["v1.0"], issues)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(lanes), 1, lanes)
        self.assertEqual(sorted(lanes[0]["issues"]), ["201", "202", "203"])


class TestReleaseLanesNoPath(LanesTestBase):
    def test_release_lanes_no_path_exclusive(self):
        issues = [_issue(301, body="Something is wrong, no file cited.")]
        result, lanes = self._run(["v1.0"], issues)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(lanes), 1, lanes)
        self.assertTrue(lanes[0]["exclusive"])
        self.assertIsNone(lanes[0]["group"])
        self.assertEqual(lanes[0]["issues"], ["301"])
        self.assertEqual(lanes[0]["paths"], [])


if __name__ == "__main__":
    unittest.main()
