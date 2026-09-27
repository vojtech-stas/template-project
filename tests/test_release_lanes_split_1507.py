"""
tests/test_release_lanes_split_1507.py

Regression tests for PRD #1501 slice #1507 — `tools/release.py lanes`
splits an oversized file group into sub-lanes (ADR-0090 D3: "A group larger
than the per-lane bug limit splits into sub-lanes. They share its `group` id
and run one after another."). Fakes `gh` on PATH; every file lives under a
per-test temp dir (rule #21). Slice #1506's own `release_lanes_*` tests in
tests/test_release_lanes_1506.py stay unedited: a group at or under the
limit is not split.

Covers PRD #1501 §2 criterion 18 (release_lanes_split): a file group holding
more bugs than the per-lane limit (default 10, `--max-per-lane <n>`) splits
into sub-lanes of at most that many bugs, every sub-lane carrying the
group's `group` id, and the same input always yields the same membership.
"""
import json
import os
import platform
import random
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
RELEASE_PY = REPO_ROOT / "tools" / "release.py"


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


def _issue(number, body):
    return {
        "number": number,
        "state": "open",
        "labels": [{"name": "bug"}],
        "milestone": {"number": 1, "title": "v1.0"},
        "body": body,
        "title": f"bug {number}",
    }


class SplitTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="release_lanes_split_test_")
        cwd = os.path.join(self.tmp, "work")
        os.makedirs(cwd, exist_ok=True)
        subprocess.run(["git", "init", "-q"], cwd=cwd, check=True)
        subprocess.run(
            ["git", "remote", "add", "origin", "https://github.com/o/r.git"], cwd=cwd, check=True,
        )
        self.cwd = cwd

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, args, issues):
        """Run `release.py lanes v1.0 <args>` against a fake gh that lists
        `issues` in the given order. Returns (result, parsed plan or None)."""
        fake_gh_dir = _write_fake_gh(self.tmp)
        routes = {
            "API GET repos/o/r/issues per_page=100 state=open": [json.dumps(issues), 0],
        }
        for i in issues:
            routes[f"API GET repos/o/r/issues/{i['number']}/comments per_page=100"] = ["[]", 0]
        routes_path = os.path.join(self.tmp, "routes.json")
        with open(routes_path, "w", encoding="utf-8") as f:
            json.dump(routes, f)
        env = os.environ.copy()
        env["PATH"] = fake_gh_dir + os.pathsep + env.get("PATH", "")
        env["FAKE_GH_ROUTES_FILE"] = routes_path
        cmd = [sys.executable, str(RELEASE_PY), "lanes", "v1.0"] + list(args)
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=self.cwd, env=env, timeout=30)
        plan = json.loads(result.stdout) if result.returncode == 0 and result.stdout.strip() else None
        return result, plan


def _same_file_bugs(first, count, path="dashboard/health.py"):
    return [_issue(n, f"Broken at `{path}:{n}`.") for n in range(first, first + count)]


class TestReleaseLanesSplit(SplitTestBase):
    def test_release_lanes_split_default_limit_is_ten(self):
        # 12 bugs in one file group -> 10 + 2, both carrying the group's id.
        result, plan = self._run([], _same_file_bugs(1001, 12))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(plan), 2, plan)
        first, second = plan
        self.assertEqual(first["issues"], [str(n) for n in range(1001, 1011)])
        self.assertEqual(second["issues"], ["1011", "1012"])
        self.assertEqual(first["group"], "fix/1001-lane-health-py")
        self.assertEqual(second["group"], "fix/1001-lane-health-py")
        self.assertEqual(first["lane"], "fix/1001-lane-health-py")
        self.assertEqual(second["lane"], "fix/1011-lane-health-py")
        self.assertFalse(first["exclusive"])
        self.assertFalse(second["exclusive"])
        self.assertEqual(first["paths"], ["dashboard/health.py"])
        self.assertEqual(second["paths"], ["dashboard/health.py"])

    def test_release_lanes_split_custom_limit_over_a_chain(self):
        # 7 bugs chained through three files -> one group; limit 3 -> 3+3+1.
        issues = [
            _issue(2001, "`a/one.py:1`"),
            _issue(2002, "`a/one.py:2` and `b/two.py:1`"),
            _issue(2003, "`b/two.py:2`"),
            _issue(2004, "`b/two.py:3` and `c/three.py:1`"),
            _issue(2005, "`c/three.py:2`"),
            _issue(2006, "`c/three.py:3`"),
            _issue(2007, "`a/one.py:9`"),
        ]
        result, plan = self._run(["--max-per-lane", "3"], issues)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([lane["issues"] for lane in plan],
                         [["2001", "2002", "2003"], ["2004", "2005", "2006"], ["2007"]])
        self.assertEqual({lane["group"] for lane in plan}, {"fix/2001-lane-one-py"})
        self.assertEqual([lane["lane"] for lane in plan],
                         ["fix/2001-lane-one-py", "fix/2004-lane-one-py", "fix/2007-lane-one-py"])
        # Each sub-lane lists the paths its own bugs cite.
        self.assertEqual(plan[0]["paths"], ["a/one.py", "b/two.py"])
        self.assertEqual(plan[2]["paths"], ["a/one.py"])

    def test_release_lanes_split_deterministic_across_listing_order(self):
        # The same bugs listed in a different order give the same membership:
        # a re-plan must never move a bug under a second branch name.
        issues = _same_file_bugs(3001, 23) + _same_file_bugs(3101, 2, path="tools/pipe/dispatch")
        shuffled = list(issues)
        random.Random(1507).shuffle(shuffled)
        self.assertNotEqual([i["number"] for i in shuffled], [i["number"] for i in issues])
        result_a, plan_a = self._run([], issues)
        result_b, plan_b = self._run([], shuffled)
        self.assertEqual(result_a.returncode, 0, result_a.stderr)
        self.assertEqual(result_b.returncode, 0, result_b.stderr)
        self.assertEqual(plan_a, plan_b)
        members = [n for lane in plan_a for n in lane["issues"]]
        self.assertEqual(len(members), len(set(members)), "a bug sits in two sub-lanes")
        self.assertEqual(sorted(members, key=int), sorted((str(i["number"]) for i in issues), key=int))

    def test_release_lanes_split_leaves_groups_at_the_limit_whole(self):
        # Exactly the limit -> one lane; an exclusive (no-path) bug stays a
        # singleton with no group, as in slice #1506.
        issues = _same_file_bugs(4001, 10) + [_issue(4099, "No file cited.")]
        result, plan = self._run([], issues)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(plan), 2, plan)
        whole = [lane for lane in plan if not lane["exclusive"]][0]
        self.assertEqual(whole["issues"], [str(n) for n in range(4001, 4011)])
        self.assertEqual(whole["lane"], whole["group"])
        alone = [lane for lane in plan if lane["exclusive"]][0]
        self.assertIsNone(alone["group"])
        self.assertEqual(alone["issues"], ["4099"])

    def test_release_lanes_split_limit_must_be_at_least_one(self):
        # The smallest limit is accepted: one bug per sub-lane, one group.
        result, plan = self._run(["--max-per-lane", "1"], _same_file_bugs(5001, 3))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([lane["issues"] for lane in plan], [["5001"], ["5002"], ["5003"]])
        self.assertEqual({lane["group"] for lane in plan}, {"fix/5001-lane-health-py"})
        # Anything below it is refused, naming the flag, with no plan printed.
        for bad in ("0", "-2"):
            result, plan = self._run(["--max-per-lane", bad], _same_file_bugs(5001, 3))
            self.assertNotEqual(result.returncode, 0, f"--max-per-lane {bad} was accepted")
            self.assertIsNone(plan)
            self.assertIn("--max-per-lane", result.stderr)


if __name__ == "__main__":
    unittest.main()
