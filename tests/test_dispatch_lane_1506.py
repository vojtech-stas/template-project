"""
tests/test_dispatch_lane_1506.py

Regression tests for PRD #1501 slice #1506 — `tools/pipe/dispatch --lane`
and `--end --lane` (ADR-0090 D3). Reached only via `argv[0]=="--lane"` or
`rest[0]=="--lane"` (new branches in `main()`); `dispatch <slice>` and
`--end <slice>` are C2-protected and keep their exact behavior, verified
unedited by tests/test_dispatch_verb_1129.py. Fakes `gh` on PATH; the
integration-branch sha comes from a REAL throwaway origin built under
pytest's tmp_path (rule #21) — never a network call.

Covers PRD #1501 §2 criteria 19-20:
  19 (dispatch_lane_refuses, 4 collected ids: closed, not_bug, slice_or_prd,
     outside_milestone): a named issue that is closed, lacks `bug`, carries
     `slice`/`prd`, or lies outside `<V>` -> exit non-zero, no span.
  20 (dispatch_lane_span): on success, exactly one `dispatch` span with
     attrs lane/issues/milestone/model (+ the packet sha).
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


def _read_jsonl(path):
    p = Path(path)
    if not p.exists():
        return []
    lines = [l.strip() for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    return [json.loads(l) for l in lines]


def _bug_issue(number, state="open", labels=("bug",), milestone_title="v1.0", body=""):
    return {
        "number": number, "state": state,
        "labels": [{"name": l} for l in labels],
        "milestone": {"number": 1, "title": milestone_title} if milestone_title else None,
        "body": body,
    }


class DispatchLaneTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dispatch_lane_test_")
        # Real throwaway origin with a "develop" branch (pipeline_config's
        # own default, since no .claude/pipeline.conf exists here). Nested
        # under o/r.git (forward slashes) so `_remote_owner_repo()`'s
        # trailing-segment regex resolves owner=o repo=r from the plain
        # local path, same as it would from a real github.com URL.
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

    def _run(self, args, issues, extra_env=None):
        fake_gh_dir = _write_fake_gh(self.tmp)
        log_path = os.path.join(self.tmp, "gh-calls.log")
        routes = {}
        for num, issue in issues.items():
            routes[f"API GET repos/o/r/issues/{num}"] = [json.dumps(issue), 0]
            routes[f"API GET repos/o/r/issues/{num}/comments per_page=100"] = ["[]", 0]
        routes_path = os.path.join(self.tmp, "routes.json")
        with open(routes_path, "w", encoding="utf-8") as f:
            json.dump(routes, f)

        trace_path = os.path.join(self.tmp, "trace-v3.jsonl")
        env = os.environ.copy()
        env["PATH"] = fake_gh_dir + os.pathsep + env.get("PATH", "")
        env["FAKE_GH_LOG_FILE"] = log_path
        env["FAKE_GH_ROUTES_FILE"] = routes_path
        env["TRACE_LOG_OVERRIDE"] = trace_path
        if extra_env:
            env.update(extra_env)

        cmd = [sys.executable, str(DISPATCH)] + args
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=self.work, env=env, timeout=30)
        return result, _read_jsonl(trace_path)


class TestDispatchLaneRefuses(DispatchLaneTestBase):
    def test_dispatch_lane_refuses_closed(self):
        issues = {"501": _bug_issue(501, state="closed")}
        result, spans = self._run(
            ["--lane", "fix/501-lane-x", "--milestone", "v1.0", "--model", "sonnet", "501"], issues,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(spans, [])

    def test_dispatch_lane_refuses_not_bug(self):
        issues = {"502": _bug_issue(502, labels=("feature",))}
        result, spans = self._run(
            ["--lane", "fix/502-lane-x", "--milestone", "v1.0", "--model", "sonnet", "502"], issues,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(spans, [])

    def test_dispatch_lane_refuses_slice_or_prd(self):
        issues = {"503": _bug_issue(503, labels=("bug", "slice"))}
        result, spans = self._run(
            ["--lane", "fix/503-lane-x", "--milestone", "v1.0", "--model", "sonnet", "503"], issues,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(spans, [])

    def test_dispatch_lane_refuses_outside_milestone(self):
        issues = {"504": _bug_issue(504, milestone_title="v1.1")}
        result, spans = self._run(
            ["--lane", "fix/504-lane-x", "--milestone", "v1.0", "--model", "sonnet", "504"], issues,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(spans, [])


class TestDispatchLaneSpan(DispatchLaneTestBase):
    def test_dispatch_lane_span_attrs(self):
        issues = {
            "601": _bug_issue(601, body="Broken at `README.md:1`.\nCheck: python3 -c \"1\""),
        }
        result, spans = self._run(
            ["--lane", "fix/601-lane-readme", "--milestone", "v1.0", "--model", "sonnet", "601"], issues,
        )
        self.assertEqual(result.returncode, 0, f"stdout={result.stdout!r} stderr={result.stderr!r}")
        self.assertEqual(len(spans), 1)
        span = spans[0]
        self.assertEqual(span["kind"], "dispatch")
        self.assertEqual(span["trace_id"], "lane-fix/601-lane-readme")
        self.assertEqual(span["attrs"]["lane"], "fix/601-lane-readme")
        self.assertEqual(span["attrs"]["issues"], "601")
        self.assertEqual(span["attrs"]["milestone"], "v1.0")
        self.assertEqual(span["attrs"]["model"], "sonnet")
        self.assertIn("sha", span["attrs"])
        self.assertIn("SHA:", result.stdout)

    def test_dispatch_end_lane_span(self):
        result, spans = self._run(
            ["--end", "--lane", "fix/601-lane-readme", "--result", "SUCCESS"], {},
        )
        self.assertEqual(result.returncode, 0, f"stdout={result.stdout!r} stderr={result.stderr!r}")
        self.assertEqual(len(spans), 1)
        span = spans[0]
        self.assertEqual(span["kind"], "dispatch_end")
        self.assertEqual(span["trace_id"], "lane-fix/601-lane-readme")
        self.assertEqual(span["attrs"]["lane"], "fix/601-lane-readme")
        self.assertEqual(span["attrs"]["result"], "SUCCESS")


if __name__ == "__main__":
    unittest.main()
