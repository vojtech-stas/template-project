"""
tests/test_release_freeze_rerun_1507.py

Regression test for PRD #1501 slice #1507 — criterion 11's second-run
clause for `tools/release.py freeze` (ADR-0090 D1: "a re-run with the same
list changes nothing"). Slice #1506 shipped the first-run clause in
tests/test_release_freeze_1506.py and deferred this one (A10).

The fake `gh` here is STATEFUL: it keeps issues, milestones and sub-issue
links in a JSON file under the test's temp dir (rule #21) and applies every
milestone PATCH, milestone POST and label edit to it. The second `freeze`
therefore reads exactly the state the first one left, with nothing else
changed in between, which is what "run twice over unchanged state" means for
an idempotence check. An unrouted gh call exits non-zero, so a stray read
would make `freeze` refuse rather than pass.

Covers PRD #1501 §2 criterion 11 (release_freeze_idempotent), second-run
clause: the second run with the same list makes no milestone-changing call.
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

args = sys.argv[1:]
log_path = os.environ.get("FAKE_GH_LOG_FILE")
if log_path:
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(" ".join(args) + "\n")
state_path = os.environ["FAKE_GH_STATE_FILE"]
with open(state_path, encoding="utf-8") as f:
    state = json.load(f)

def save():
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(state, f)

def fields():
    out = {}
    i = 0
    while i < len(args):
        if args[i] in ("-f", "-F") and i + 1 < len(args):
            key, _, value = args[i + 1].partition("=")
            out[key] = value
            i += 2
            continue
        i += 1
    return out

def issue(num):
    for it in state["issues"]:
        if it["number"] == int(num):
            return it
    return None

def unrouted():
    sys.stderr.write("fake gh: unrouted call: " + " ".join(args) + "\n")
    sys.exit(1)

if args and args[0] == "api":
    method = args[args.index("-X") + 1] if "-X" in args else "GET"
    rest = args[1].split("/")[3:]  # drop repos/<owner>/<repo>
    f = fields()
    if rest == ["issues"] and method == "GET":
        print(json.dumps([i for i in state["issues"] if i["state"] == "open"]))
    elif rest == ["milestones"] and method == "GET":
        print(json.dumps(state["milestones"]))
    elif rest == ["milestones"] and method == "POST":
        number = max([m["number"] for m in state["milestones"]] + [0]) + 1
        created = {"number": number, "title": f["title"], "state": "open"}
        state["milestones"].append(created)
        save()
        print(json.dumps(created))
    elif len(rest) == 3 and rest[0] == "issues" and rest[2] == "sub_issues" and method == "GET":
        subs = [issue(n) for n in state["sub_issues"].get(rest[1], [])]
        print(json.dumps([s for s in subs if s is not None]))
    elif len(rest) == 2 and rest[0] == "issues" and method == "PATCH" and "milestone" in f:
        target = int(f["milestone"])
        it = issue(rest[1])
        it["milestone"] = next(m for m in state["milestones"] if m["number"] == target)
        save()
        print(json.dumps(it))
    else:
        unrouted()
elif args[:2] == ["issue", "edit"] and "--add-label" in args:
    issue(args[2])["labels"].append({"name": args[args.index("--add-label") + 1]})
    save()
else:
    unrouted()
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


def _issue(number, labels, milestone=None, state="open"):
    return {
        "number": number,
        "state": state,
        "labels": [{"name": l} for l in labels],
        "milestone": milestone,
        "body": "",
        "title": f"issue {number}",
    }


def _milestone_changing(line):
    """A logged gh call that changes a milestone or an issue's milestone."""
    return ("milestones" in line and "-X POST" in line) or (
        "-X PATCH" in line and "milestone=" in line
    )


class TestReleaseFreezeIdempotentSecondRun(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="release_freeze_rerun_test_")
        self.work = os.path.join(self.tmp, "work")
        os.makedirs(self.work)
        subprocess.run(["git", "init", "-q"], cwd=self.work, check=True)
        subprocess.run(
            ["git", "remote", "add", "origin", "https://github.com/o/r.git"], cwd=self.work, check=True,
        )
        self.fake_gh_dir = _write_fake_gh(self.tmp)
        self.state_path = os.path.join(self.tmp, "gh-state.json")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _freeze(self, args, log_name):
        log_path = os.path.join(self.tmp, log_name)
        env = os.environ.copy()
        env["PATH"] = self.fake_gh_dir + os.pathsep + env.get("PATH", "")
        env["FAKE_GH_STATE_FILE"] = self.state_path
        env["FAKE_GH_LOG_FILE"] = log_path
        cmd = [sys.executable, str(RELEASE_PY), "freeze"] + args
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=self.work, env=env, timeout=60)
        log = Path(log_path).read_text(encoding="utf-8").splitlines() if os.path.exists(log_path) else []
        return result, log

    def test_release_freeze_idempotent_second_run_makes_no_milestone_call(self):
        w_ms = {"number": 2, "title": "v1.1", "state": "open"}
        other_ms = {"number": 9, "title": "some-other-thing", "state": "open"}
        state = {
            # <V> = v1.0 is missing, so the first run must create it.
            "milestones": [w_ms, other_ms],
            "issues": [
                _issue(101, ["bug"]),                              # unmilestoned bug -> <V>
                _issue(102, ["bug"], milestone=w_ms),              # bug in <W> -> <V>
                _issue(201, ["feature", "prd"]),                   # listed feature -> <V>
                _issue(202, ["feature", "slice"]),                 # its open slice -> <V>
                _issue(203, ["feature", "slice"], state="closed"), # its closed slice: untouched
                _issue(302, ["feature"]),                          # unlisted feature -> <W>
                _issue(401, ["needs-human-check"]),                # residual: never moved
                _issue(402, ["bug"], milestone=other_ms),          # another milestone stands
                _issue(403, []),                                   # classified by --classes -> <V>
            ],
            "sub_issues": {"201": [202, 203]},
        }
        with open(self.state_path, "w", encoding="utf-8") as f:
            json.dump(state, f)
        args = ["v1.0", "--next", "v1.1", "--features", "201", "--classes", '{"403": "bug"}']

        first, first_log = self._freeze(args, "gh-run1.log")
        self.assertEqual(first.returncode, 0, first.stderr)
        # The first run really moved things, so the second run's zero below
        # is not a vacuous pass.
        moved_first = sorted(
            l.split()[1].rsplit("/", 1)[1] for l in first_log if "-X PATCH" in l and "milestone=" in l
        )
        self.assertEqual(moved_first, ["101", "102", "201", "202", "302", "403"])
        self.assertEqual(len([l for l in first_log if "milestones" in l and "-X POST" in l]), 1)

        second, second_log = self._freeze(args, "gh-run2.log")
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual([l for l in second_log if _milestone_changing(l)], [])
        # Nothing else is written either: 403 already carries its class.
        self.assertEqual([l for l in second_log if l.startswith("issue edit")], [])
        self.assertIn("moved=0", second.stdout)
        self.assertTrue(second_log, "the second run made no gh call at all")


if __name__ == "__main__":
    unittest.main()
