"""
tests/test_release_verify_reopen_1508.py

Regression tests for PRD #1501 slice #1508 — `tools/release.py verify
--reopen` (ADR-0090 D4; PRD #1501 AMENDMENT 2, criteria 35 and 36).

A recording fake replaces `_run_gh`, so every gh call the command makes
is captured and no real issue is ever touched. Nothing is written under
`.claude/logs/` (rule #21).

  35 (release_verify_reopens): `verify --reopen` reopens a closed issue
     exactly when its line is FAIL or MISSING. A closed issue whose gh
     lookup fails prints UNCONFIRMED and gets neither a reopen nor a
     comment; a closed issue whose line is PASS gets no mutating call.
  36 (release_verify_reopen_comment): each reopened issue gets a comment
     carrying its `verify` output.
"""
import argparse
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import re
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
RELEASE_PY = REPO_ROOT / "tools" / "release.py"
REPO_URL = "https://api.github.com/repos/o/r"

PASS_CMD = f'"{sys.executable}" -c "import sys; sys.exit(0)"'
FAIL_CMD = f'"{sys.executable}" -c "import sys; print(\'boom-1508\'); sys.exit(3)"'

_COUNTER = [0]


def _load():
    _COUNTER[0] += 1
    loader = importlib.machinery.SourceFileLoader(f"release_reopen_{_COUNTER[0]}", str(RELEASE_PY))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class _Res:
    def __init__(self, stdout="", returncode=0):
        self.stdout, self.stderr, self.returncode = stdout, "", returncode


def _ok(obj):
    return (json.dumps(obj), 0)


_ERR = ("", 1)


class _RecordingGh:
    """A recording gh shim. Reads (`api repos/o/r/issues/<n>[/comments|
    /timeline]`) answer from tables; every other call is recorded as a
    mutation and answers `mutation_rc`. A `--body-file` is read at call
    time, so the comment text is recorded with the call."""

    def __init__(self, issues, comments=None, timeline=None, mutation_rc=0):
        self.issues, self.comments, self.timeline = issues, comments or {}, timeline or {}
        self.mutation_rc = mutation_rc
        self.calls, self.mutations, self.bodies = [], [], []

    def __call__(self, args):
        args = list(args)
        self.calls.append(args)
        if args[:1] == ["api"] and ("-X" not in args or args[args.index("-X") + 1] == "GET"):
            m = re.fullmatch(r"repos/o/r/issues/(\d+)(/comments|/timeline)?", args[1])
            if m:
                table = {None: self.issues, "/comments": self.comments,
                         "/timeline": self.timeline}[m.group(2)]
                return _Res(*table.get(m.group(1), _ERR))
            return _Res(*_ERR)
        self.mutations.append(args)
        if "--body-file" in args:
            self.bodies.append(Path(args[args.index("--body-file") + 1]).read_text(encoding="utf-8"))
        return _Res("", self.mutation_rc)


def _issue(num, state="closed", body="No check here."):
    return {"number": int(num), "state": state, "body": body, "repository_url": REPO_URL,
            "labels": [{"name": "bug"}]}


def _run(fake, nums, reopen=True):
    release = _load()
    release._run_gh = fake
    release._remote_owner_repo = lambda: ("o", "r")
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = release._cmd_verify(argparse.Namespace(issues=[str(n) for n in nums], reopen=reopen))
    return rc, out.getvalue(), err.getvalue()


def _lines(text):
    return [l for l in text.splitlines() if l.strip()]


def _four_way_gh():
    """#1 closed FAIL, #2 closed MISSING, #3 closed UNCONFIRMED (its
    timeline read fails), #4 closed PASS."""
    return _RecordingGh(
        issues={
            "1": _ok(_issue(1, body=f"Check: {FAIL_CMD}")),
            "2": _ok(_issue(2)),
            "3": _ok(_issue(3)),
            "4": _ok(_issue(4, body=f"Check: {PASS_CMD}")),
        },
        comments={n: _ok([]) for n in "1234"},
        timeline={"1": _ok([]), "2": _ok([{"event": "labeled"}]), "3": _ERR, "4": _ok([])},
    )


class TestReleaseVerifyReopens(unittest.TestCase):
    def test_release_verify_reopens_only_fail_and_missing(self):
        fake = _four_way_gh()
        rc, out, _err = _run(fake, [1, 2, 3, 4])
        self.assertEqual(_lines(out), ["FAIL #1", "MISSING #2", "UNCONFIRMED #3", "PASS #4"])
        self.assertNotEqual(rc, 0)
        reopened = sorted(a[2] for a in fake.mutations if a[:2] == ["issue", "reopen"])
        self.assertEqual(reopened, ["1", "2"])

    def test_release_verify_reopens_never_on_unconfirmed(self):
        fake = _RecordingGh(issues={"3": _ok(_issue(3))}, comments={"3": _ok([])},
                            timeline={"3": _ERR})
        rc, out, _err = _run(fake, [3])
        self.assertEqual(_lines(out), ["UNCONFIRMED #3"])
        self.assertNotEqual(rc, 0)
        self.assertEqual(fake.mutations, [], "UNCONFIRMED must neither reopen nor comment")

    def test_release_verify_reopens_never_on_pass(self):
        fake = _RecordingGh(issues={"4": _ok(_issue(4, body=f"Check: {PASS_CMD}"))},
                            comments={"4": _ok([])})
        rc, out, _err = _run(fake, [4])
        self.assertEqual(_lines(out), ["PASS #4"])
        self.assertEqual(rc, 0)
        self.assertEqual(fake.mutations, [], "PASS must make no mutating call")

    def test_release_verify_reopens_only_a_closed_issue(self):
        fake = _RecordingGh(issues={"5": _ok(_issue(5, state="open", body=f"Check: {FAIL_CMD}"))},
                            comments={"5": _ok([])})
        rc, out, _err = _run(fake, [5])
        self.assertEqual(_lines(out), ["FAIL #5"])
        self.assertNotEqual(rc, 0)
        self.assertEqual(fake.mutations, [], "an open issue is never reopened or commented on")

    def test_release_verify_reopens_nothing_without_the_flag(self):
        fake = _four_way_gh()
        rc, out, _err = _run(fake, [1, 2, 3, 4], reopen=False)
        self.assertEqual(_lines(out), ["FAIL #1", "MISSING #2", "UNCONFIRMED #3", "PASS #4"])
        self.assertEqual(fake.mutations, [])

    def test_release_verify_reopens_cli_flag_parses(self):
        release = _load()
        seen = {}
        release._cmd_verify = lambda args: seen.setdefault("args", args) and 0
        release.main(["verify", "--reopen", "7", "8"])
        self.assertTrue(seen["args"].reopen)
        self.assertEqual(seen["args"].issues, ["7", "8"])


class TestReleaseVerifyReopenComment(unittest.TestCase):
    def test_release_verify_reopen_comment_carries_the_verify_output(self):
        fake = _four_way_gh()
        _run(fake, [1, 2, 3, 4])
        comments = [a for a in fake.mutations if a[:2] == ["issue", "comment"]]
        self.assertEqual(sorted(a[2] for a in comments), ["1", "2"])
        by_issue = dict(zip((a[2] for a in comments), fake.bodies))
        self.assertIn("FAIL #1", by_issue["1"])
        self.assertIn("boom-1508", by_issue["1"])
        self.assertIn("MISSING #2", by_issue["2"])

    def test_release_verify_reopen_comment_follows_the_reopen(self):
        fake = _RecordingGh(issues={"1": _ok(_issue(1, body=f"Check: {FAIL_CMD}"))},
                            comments={"1": _ok([])})
        _run(fake, [1])
        self.assertEqual([a[:2] for a in fake.mutations], [["issue", "reopen"], ["issue", "comment"]])

    def test_release_verify_reopen_comment_skipped_when_the_reopen_fails(self):
        fake = _RecordingGh(issues={"1": _ok(_issue(1, body=f"Check: {FAIL_CMD}"))},
                            comments={"1": _ok([])}, mutation_rc=1)
        rc, out, err = _run(fake, [1])
        self.assertEqual(_lines(out), ["FAIL #1"])
        self.assertNotEqual(rc, 0)
        self.assertEqual([a[:2] for a in fake.mutations], [["issue", "reopen"]])
        self.assertIn("#1", err)


if __name__ == "__main__":
    unittest.main()
