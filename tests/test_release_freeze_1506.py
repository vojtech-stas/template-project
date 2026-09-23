"""
tests/test_release_freeze_1506.py

Regression tests for PRD #1501 slice #1506 — `tools/release.py freeze`
(ADR-0090 D1). Fakes `gh` on PATH (subprocess-level, mirrors the pattern
established by tests/test_dispatch_verb_1129.py) so no real GitHub call is
ever made; every ledger/log lives under pytest's tmp_path (rule #21).

Covers PRD #1501 §2 criteria 6-11:
  6 (release_freeze_refuses): missing --features, an unclassified open
    non-residual issue, or a listed number naming no non-residual feature
    issue -> refuse, non-zero, before any GitHub mutation.
  7 (release_freeze_admits_bugs): an unmilestoned bug and a bug sitting in
    <W> both move to <V>.
  8 (release_freeze_admits_features): a listed feature's open sub-issue
    moves to <V> alongside the feature itself.
  9 (release_freeze_defers_features): an unlisted feature in <V> and an
    unmilestoned unlisted feature both move to <W>.
  10 (release_freeze_creates_milestones): a missing <V> or <W> is created.
  11 (release_freeze_idempotent): a residual, an issue in a milestone
    other than <V>/<W>, and an issue already correctly placed each get no
    milestone-changing call (the "run freeze twice" clause is deferred to
    slice 2 per the SPIDR fallback, A10).
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
    if a[0] == "issue" and len(a) > 1 and a[1] == "edit":
        num = a[2] if len(a) > 2 else ""
        label = ""
        if "--add-label" in a:
            label = a[a.index("--add-label") + 1]
        return "ISSUE-EDIT " + num + " " + label
    if a[0] == "issue" and len(a) > 1 and a[1] == "close":
        num = a[2] if len(a) > 2 else ""
        return "ISSUE-CLOSE " + num
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


def _issue(number, labels=None, milestone=None, body="", state="open"):
    return {
        "number": number,
        "state": state,
        "labels": [{"name": l} for l in (labels or [])],
        "milestone": milestone,
        "body": body,
        "title": f"issue {number}",
    }


def _milestone(number, title, state="open"):
    return {"number": number, "title": title, "state": state}


class FreezeTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="release_freeze_test_")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, args, issues=None, milestones=None, sub_issues=None, extra_routes=None):
        fake_gh_dir = _write_fake_gh(self.tmp)
        log_path = os.path.join(self.tmp, "gh-calls.log")
        routes = {}
        if issues is not None:
            routes["API GET repos/o/r/issues per_page=100 state=open"] = [json.dumps(issues), 0]
        if milestones is not None:
            routes["API GET repos/o/r/milestones per_page=100 state=all"] = [json.dumps(milestones), 0]
        for num, subs in (sub_issues or {}).items():
            routes[f"API GET repos/o/r/issues/{num}/sub_issues"] = [json.dumps(subs), 0]
        if extra_routes:
            routes.update(extra_routes)
        routes_path = os.path.join(self.tmp, "routes.json")
        with open(routes_path, "w", encoding="utf-8") as f:
            json.dump(routes, f)

        env = os.environ.copy()
        env["PATH"] = fake_gh_dir + os.pathsep + env.get("PATH", "")
        env["FAKE_GH_LOG_FILE"] = log_path
        env["FAKE_GH_ROUTES_FILE"] = routes_path
        # Fake origin so _remote_owner_repo() resolves to o/r.
        origin_repo = os.path.join(self.tmp, "origin.git")
        subprocess.run(["git", "init", "--bare", "-q", origin_repo], check=True)
        cwd = os.path.join(self.tmp, "work")
        os.makedirs(cwd, exist_ok=True)
        subprocess.run(["git", "init", "-q"], cwd=cwd, check=True)
        subprocess.run(["git", "remote", "add", "origin", "https://github.com/o/r.git"], cwd=cwd, check=True)

        cmd = [sys.executable, str(RELEASE_PY)] + args
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd, env=env, timeout=30)
        log_lines = []
        if os.path.exists(log_path):
            log_lines = Path(log_path).read_text(encoding="utf-8").splitlines()
        return result, log_lines


# ---------------------------------------------------------------------------
# criterion 6: release_freeze_refuses
# ---------------------------------------------------------------------------

class TestReleaseFreezeRefuses(FreezeTestBase):
    def test_release_freeze_refuses_missing_features(self):
        result, log = self._run(
            ["freeze", "v1.0", "--next", "v1.1"],
            issues=[_issue(1, labels=["bug"])],
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--features", result.stderr)
        self.assertFalse(any("milestones" in l and "-X POST" in l for l in log))

    def test_release_freeze_refuses_unclassified_issue(self):
        result, log = self._run(
            ["freeze", "v1.0", "--next", "v1.1", "--features", "none"],
            issues=[_issue(1, labels=[])],
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("#1", result.stderr)
        self.assertFalse(any("milestones" in l and "-X POST" in l for l in log))

    def test_release_freeze_refuses_bad_feature_number(self):
        result, log = self._run(
            ["freeze", "v1.0", "--next", "v1.1", "--features", "1"],
            issues=[_issue(1, labels=["bug"])],
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("#1", result.stderr)
        self.assertFalse(any("milestones" in l and "-X POST" in l for l in log))


# ---------------------------------------------------------------------------
# criterion 7: release_freeze_admits_bugs
# ---------------------------------------------------------------------------

class TestReleaseFreezeAdmitsBugs(FreezeTestBase):
    def test_release_freeze_admits_bugs_unmilestoned_and_w(self):
        w_ms = _milestone(2, "v1.1")
        result, log = self._run(
            ["freeze", "v1.0", "--next", "v1.1", "--features", "none"],
            issues=[
                _issue(101, labels=["bug"], milestone=None),
                _issue(102, labels=["bug"], milestone=w_ms),
            ],
            milestones=[_milestone(1, "v1.0"), w_ms],
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(any("issues/101 -X PATCH" in l and "milestone=1" in l for l in log))
        self.assertTrue(any("issues/102 -X PATCH" in l and "milestone=1" in l for l in log))


# ---------------------------------------------------------------------------
# criterion 8: release_freeze_admits_features
# ---------------------------------------------------------------------------

class TestReleaseFreezeAdmitsFeatures(FreezeTestBase):
    def test_release_freeze_admits_features_listed_and_sub_issue(self):
        result, log = self._run(
            ["freeze", "v1.0", "--next", "v1.1", "--features", "201"],
            issues=[
                _issue(201, labels=["feature"], milestone=None),
                _issue(202, labels=["feature"], milestone=None),
            ],
            milestones=[_milestone(1, "v1.0"), _milestone(2, "v1.1")],
            sub_issues={"201": [
                {"number": 202, "state": "open"},
                {"number": 203, "state": "closed"},
            ]},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(any("issues/201 -X PATCH" in l and "milestone=1" in l for l in log))
        self.assertTrue(any("issues/202 -X PATCH" in l and "milestone=1" in l for l in log))
        self.assertFalse(any("issues/203 -X PATCH" in l for l in log))


# ---------------------------------------------------------------------------
# criterion 9: release_freeze_defers_features
# ---------------------------------------------------------------------------

class TestReleaseFreezeDefersFeatures(FreezeTestBase):
    def test_release_freeze_defers_features_unlisted(self):
        v_ms = _milestone(1, "v1.0")
        result, log = self._run(
            ["freeze", "v1.0", "--next", "v1.1", "--features", "none"],
            issues=[
                _issue(301, labels=["feature"], milestone=v_ms),
                _issue(302, labels=["feature"], milestone=None),
            ],
            milestones=[v_ms, _milestone(2, "v1.1")],
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(any("issues/301 -X PATCH" in l and "milestone=2" in l for l in log))
        self.assertTrue(any("issues/302 -X PATCH" in l and "milestone=2" in l for l in log))


# ---------------------------------------------------------------------------
# criterion 10: release_freeze_creates_milestones
# ---------------------------------------------------------------------------

class TestReleaseFreezeCreatesMilestones(FreezeTestBase):
    def test_release_freeze_creates_milestones_missing_v_and_w(self):
        result, log = self._run(
            ["freeze", "v1.0", "--next", "v1.1", "--features", "none"],
            issues=[],
            milestones=[],
            extra_routes={
                "API POST repos/o/r/milestones title=v1.0": [json.dumps({"number": 10, "title": "v1.0"}), 0],
                "API POST repos/o/r/milestones title=v1.1": [json.dumps({"number": 11, "title": "v1.1"}), 0],
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(any("milestones -X POST" in l and "title=v1.0" in l for l in log))
        self.assertTrue(any("milestones -X POST" in l and "title=v1.1" in l for l in log))


# ---------------------------------------------------------------------------
# criterion 11: release_freeze_idempotent
# ---------------------------------------------------------------------------

class TestReleaseFreezeIdempotent(FreezeTestBase):
    def test_release_freeze_idempotent_no_call_for_residual_other_or_placed(self):
        v_ms = _milestone(1, "v1.0")
        other_ms = _milestone(9, "some-other-thing")
        result, log = self._run(
            ["freeze", "v1.0", "--next", "v1.1", "--features", "none"],
            issues=[
                _issue(401, labels=["needs-human-check"], milestone=None),   # residual
                _issue(402, labels=["bug"], milestone=other_ms),             # other milestone stands
                _issue(403, labels=["bug"], milestone=v_ms),                 # already placed
            ],
            milestones=[v_ms, _milestone(2, "v1.1"), other_ms],
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any("issues/401 -X PATCH" in l for l in log))
        self.assertFalse(any("issues/402 -X PATCH" in l for l in log))
        self.assertFalse(any("issues/403 -X PATCH" in l for l in log))


if __name__ == "__main__":
    unittest.main()
