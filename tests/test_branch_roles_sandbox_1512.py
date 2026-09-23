"""
tests/test_branch_roles_sandbox_1512.py

New-feature tests for slice #1512 (PRD #1500 §2 criteria 12-15, ADR-0089
D1/D3) in a `trunk`/`release` sandbox (the PRD's own Sandbox S shape,
narrowed to what these four criteria need: a tracked `.claude/pipeline.conf`
naming non-default roles, and — for criterion 15 only — both role branches
present on origin).

Covers:
  12 — .githooks/pre-commit rejects a direct commit on the release branch,
       naming it by its CONFIGURED name ('release'), not the literal 'main'.
  13 — the PreToolUse(Bash) hook denies `git push origin release`.
  14 (= S2-14) — a malformed .claude/pipeline.conf makes the classifier
       raise (PipelineConfigError, uncaught), which pre-tool-bash.sh's
       existing `_PTB_RC -ne 0` path turns into exactly ONE
       `"status":"ERROR"` beacon, fails open (exit 0), and prints no
       decision JSON — HOK-008 / CI CHECK 27's closed beacon-status set.
  15 — dashboard/health.py's BRANCH-TOPOLOGY describes 'trunk', never a
       missing 'develop', when origin holds both configured role branches.
  S2-d — .claude/hooks/session-start.sh, run by absolute path against a
       sandbox whose .claude/pipeline.conf is malformed, degrades instead of
       exiting: exit 0, a visible resolver-failure placeholder in the
       divergence slot, all five GitHub fields still emitted (from a fake
       `gh` on PATH), and exactly one terminal beacon.

Fixture discipline (rule #21): every sandbox lives under a fresh
tempfile.mkdtemp(); no test ever touches this checkout's own
`.claude/logs/*` (beacon paths use WORKFLOW_LOG_DIR, matching
test_deny_guard_mechanical_1133.py's own seam) or `.claude/pipeline.conf`.

Runner: stdlib unittest + pytest compatible.
  python -m pytest tests/test_branch_roles_sandbox_1512.py -v
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

try:
    import pytest  # noqa: F401
except ImportError:
    pytest = None

REPO_ROOT = Path(__file__).parent.parent
PRE_COMMIT = REPO_ROOT / ".githooks" / "pre-commit"
PRE_TOOL_BASH = REPO_ROOT / ".claude" / "hooks" / "pre-tool-bash.sh"
SESSION_START_SH = REPO_ROOT / ".claude" / "hooks" / "session-start.sh"
DASHBOARD_DIR = REPO_ROOT / "dashboard"


def _git(*args, cwd=None, check=True):
    return subprocess.run(
        ["git"] + list(args), cwd=cwd, check=check, capture_output=True, text=True,
    )


def _make_trunk_release_sandbox(parent_tmp: str, both_branches_on_origin: bool = True):
    """Bare origin + working clone with a tracked .claude/pipeline.conf
    naming integration_branch=trunk / release_branch=release. When
    `both_branches_on_origin`, origin also holds `release`'s tip (mirrors
    Sandbox S post-bootstrap: criterion 5 already created `trunk` there)."""
    bare = os.path.join(parent_tmp, "origin.git")
    os.makedirs(bare)
    _git("init", "-q", "--bare", "-b", "release", bare)

    work = os.path.join(parent_tmp, "work")
    _git("clone", "-q", bare, work)
    _git("-C", work, "config", "user.email", "test@example.com")
    _git("-C", work, "config", "user.name", "Test")

    os.makedirs(os.path.join(work, ".claude"), exist_ok=True)
    with open(os.path.join(work, ".claude", "pipeline.conf"), "w") as f:
        f.write("integration_branch=trunk\nrelease_branch=release\n")
    with open(os.path.join(work, "README.md"), "w") as f:
        f.write("hello\n")
    _git("-C", work, "add", ".")
    _git("-C", work, "commit", "-q", "-m", "init")
    _git("-C", work, "push", "-q", "-u", "origin", "HEAD:release")

    _git("-C", work, "checkout", "-q", "-b", "trunk")
    with open(os.path.join(work, "CHANGES.md"), "w") as f:
        f.write("trunk change\n")
    _git("-C", work, "add", "CHANGES.md")
    _git("-C", work, "commit", "-q", "-m", "trunk change")
    if both_branches_on_origin:
        _git("-C", work, "push", "-q", "-u", "origin", "trunk")
        _git("-C", work, "fetch", "-q", "origin")

    return work


class TestCriterion12PreCommitRejectsReleaseBranch(unittest.TestCase):
    """PRD #1500 §2 criterion 12: .githooks/pre-commit rejects a commit on
    the configured RELEASE branch, naming it by its configured name."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="branch_roles_sandbox_c12_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.work = _make_trunk_release_sandbox(self.tmp, both_branches_on_origin=False)
        _git("-C", self.work, "checkout", "-q", "release")

    def test_rejects_direct_commit_on_release_branch(self):
        result = subprocess.run(
            ["sh", str(PRE_COMMIT)], cwd=self.work, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(
            result.returncode, 1,
            msg=f"pre-commit must exit 1 on 'release'.\nstdout={result.stdout!r}\nstderr={result.stderr!r}",
        )
        self.assertIn("direct commits to 'release'", result.stderr)
        self.assertNotIn("direct commits to 'main'", result.stderr)


class TestCriterion13PreToolBashDeniesPushRelease(unittest.TestCase):
    """PRD #1500 §2 criterion 13: the PreToolUse(Bash) hook denies
    `git push origin release` when the sandbox's conf names 'release' as
    the release role — never a hardcoded 'main' literal."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="branch_roles_sandbox_c13_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.work = _make_trunk_release_sandbox(self.tmp, both_branches_on_origin=False)
        self.beacon_dir = os.path.join(self.tmp, "logs")
        os.makedirs(self.beacon_dir, exist_ok=True)

    def _run_hook(self, command: str):
        payload = json.dumps({"tool_input": {"command": command}})
        env = os.environ.copy()
        env["WORKFLOW_LOG_DIR"] = self.beacon_dir
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            f.write(payload)
            tmp_path = f.name
        try:
            with open(tmp_path, "r", encoding="utf-8") as stdin_f:
                return subprocess.run(
                    ["bash", str(PRE_TOOL_BASH)], stdin=stdin_f, cwd=self.work,
                    capture_output=True, text=True, timeout=15, env=env,
                )
        finally:
            os.unlink(tmp_path)

    def test_denies_push_to_configured_release_branch(self):
        result = self._run_hook("git push origin release")
        self.assertEqual(result.returncode, 0)
        out = json.loads(result.stdout) if result.stdout.strip() else {}
        decision = out.get("hookSpecificOutput", {}).get("permissionDecision")
        self.assertEqual(decision, "deny", msg=f"expected deny, got: {out}")
        self.assertIn("release", out["hookSpecificOutput"]["permissionDecisionReason"])

    def test_push_to_trunk_not_denied(self):
        """The integration branch ('trunk' here) must never be denied — only
        the configured RELEASE role is."""
        result = self._run_hook("git push origin trunk")
        self.assertEqual(result.returncode, 0)
        out = json.loads(result.stdout) if result.stdout.strip() else {}
        self.assertNotIn("hookSpecificOutput", out, msg=f"push to trunk must not be denied: {out}")


class TestCriterion14MalformedConfErrorBeacon(unittest.TestCase):
    """PRD #1500 §2 criterion 14 (= S2-14): a malformed .claude/pipeline.conf
    makes the PreToolUse(Bash) hook append exactly ONE 'status':'ERROR'
    beacon and fail open (exit 0, no decision JSON) — pre-tool-bash.sh's
    EXISTING classifier-failed path, no new hook policy."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="branch_roles_sandbox_c14_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.work = _make_trunk_release_sandbox(self.tmp, both_branches_on_origin=False)
        # Malform the conf AFTER the sandbox is built (both roles equal —
        # one of pipeline_config.py's four documented malformed shapes).
        with open(os.path.join(self.work, ".claude", "pipeline.conf"), "w") as f:
            f.write("integration_branch=x\nrelease_branch=x\n")
        self.beacon_dir = os.path.join(self.tmp, "logs")
        os.makedirs(self.beacon_dir, exist_ok=True)

    def _run_hook(self, command: str):
        payload = json.dumps({"tool_input": {"command": command}})
        env = os.environ.copy()
        env["WORKFLOW_LOG_DIR"] = self.beacon_dir
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            f.write(payload)
            tmp_path = f.name
        try:
            with open(tmp_path, "r", encoding="utf-8") as stdin_f:
                return subprocess.run(
                    ["bash", str(PRE_TOOL_BASH)], stdin=stdin_f, cwd=self.work,
                    capture_output=True, text=True, timeout=15, env=env,
                )
        finally:
            os.unlink(tmp_path)

    def test_malformed_conf_yields_exactly_one_error_beacon_fail_open(self):
        # PRD #1500 §2 criterion 14's own verification recipe uses a
        # generic, non-push command ('git status') -- classify() resolves
        # (and thereby validates) the release role unconditionally on every
        # call, not only for a push-shaped command, so this generic command
        # must raise on the malformed conf too.
        result = self._run_hook("git status")
        self.assertEqual(
            result.returncode, 0,
            msg=f"hook must fail OPEN (exit 0) on a resolver failure.\nstdout={result.stdout!r}\nstderr={result.stderr!r}",
        )
        self.assertEqual(
            result.stdout.strip(), "",
            msg=f"hook must print no decision JSON on the classifier-failed path: {result.stdout!r}",
        )
        beacon_path = os.path.join(self.beacon_dir, "hook-fires.jsonl")
        self.assertTrue(os.path.exists(beacon_path), msg="no hook-fires.jsonl written at all")
        with open(beacon_path, "r", encoding="utf-8") as f:
            lines = [json.loads(l) for l in f if l.strip()]
        error_lines = [l for l in lines if l.get("status") == "ERROR"]
        self.assertEqual(
            len(error_lines), 1,
            msg=f"expected exactly 1 ERROR beacon, got {len(error_lines)}: {lines!r}",
        )
        # HOK-008 / CI CHECK 27: only {attempt, ok, ERROR} statuses allowed.
        for l in lines:
            self.assertIn(l.get("status"), ("attempt", "ok", "ERROR"), msg=f"unexpected status: {l!r}")

    def test_malformed_conf_also_errors_on_a_push_command(self):
        """Regression guard: a push-shaped command must still hit the same
        classifier-failed path (it did even before the eager-resolve fix,
        via _refspec_release_re() -- this proves the fix did not narrow that
        existing coverage)."""
        result = self._run_hook("git push origin somewhere")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "")
        beacon_path = os.path.join(self.beacon_dir, "hook-fires.jsonl")
        with open(beacon_path, "r", encoding="utf-8") as f:
            lines = [json.loads(l) for l in f if l.strip()]
        error_lines = [l for l in lines if l.get("status") == "ERROR"]
        self.assertEqual(len(error_lines), 1, msg=f"lines={lines!r}")


class TestCriterion15BranchTopologyDescribesTrunk(unittest.TestCase):
    """PRD #1500 §2 criterion 15: BRANCH-TOPOLOGY describes 'trunk', never a
    missing 'develop', once origin holds both configured role branches
    (mirrors Sandbox S post-bootstrap, ADR-0089 D1)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="branch_roles_sandbox_c15_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.work = _make_trunk_release_sandbox(self.tmp, both_branches_on_origin=True)

    def _run_branch_topology(self) -> dict:
        script = (
            "import sys, json\n"
            "from pathlib import Path\n"
            f"sys.path.insert(0, r'{DASHBOARD_DIR}')\n"
            "import health as h\n"
            f"h._HEALTH_REPO_ROOT = Path(r'{self.work}')\n"
            "result = h.check_branch_topology()\n"
            "print(json.dumps(result))\n"
        )
        proc = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True, text=True, cwd=str(DASHBOARD_DIR), timeout=30,
        )
        if proc.returncode != 0:
            self.fail(f"check_branch_topology subprocess failed (exit={proc.returncode}): {proc.stderr[:800]}")
        return json.loads(proc.stdout.strip())

    def test_describes_trunk_not_missing_develop(self):
        result = self._run_branch_topology()
        detail = result.get("detail", "")
        self.assertIn("trunk", detail, msg=f"BRANCH-TOPOLOGY must describe 'trunk': {result!r}")
        self.assertNotIn(
            "origin/develop does not exist", detail,
            msg=f"BRANCH-TOPOLOGY must never report a missing 'develop' under a trunk/release conf: {result!r}",
        )
        self.assertNotEqual(result.get("result"), "FAIL", msg=f"unexpected FAIL: {result!r}")


# A fake `gh` for the S2-d case. It answers `gh auth status` and the five
# list queries in session-start.sh's GH_OK block with one distinctive item
# each, so a real GH_OK-block run is distinguishable from the
# "(gh/jq unavailable)" fallback. It is a bare python3-shebang `gh` with no
# extension because the hook finds it through bash's own PATH lookup, which
# applies no PATHEXT (same reasoning as test_session_start_concurrent_1199.py).
_FAKE_GH_BODY = '''import json, sys
argv = sys.argv[1:]
if argv[:2] == ["auth", "status"]:
    sys.exit(0)
if len(argv) >= 2 and argv[0] in ("issue", "pr") and argv[1] == "list":
    label = argv[argv.index("--label") + 1] if "--label" in argv else "open"
    print(json.dumps([{"number": 1, "title": "fake-%s-%s" % (argv[0], label)}]))
    sys.exit(0)
sys.stderr.write("fake-gh: unexpected invocation %r\\n" % (argv,))
sys.exit(2)
'''


class TestS2dSessionStartDegradesOnMalformedConf(unittest.TestCase):
    """Slice #1512 S2-d: when the resolver refuses a malformed
    .claude/pipeline.conf, session-start.sh shows a visible placeholder in
    the divergence slot (no fallback branch literal), still runs the GH_OK
    block so all five GitHub fields are emitted, and ends in exactly one
    terminal beacon. A hook that exits on the resolver failure fails here:
    non-zero exit, no context JSON, no fields, no terminal beacon."""

    FIELDS = {
        "Needs-human issues": "1+ open; recent: #1 fake-issue-needs-human",
        "Needs-human PRs": "1+ needs-human PRs: #1 fake-pr-needs-human",
        "Open slices": "1+ open; recent: #1 fake-issue-slice",
        "Open PRs": "1+ open; recent: #1 fake-pr-open",
        "Open captured": "1+ open; recent: #1 fake-issue-captured",
    }

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="branch_roles_sandbox_s2d_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.work = os.path.join(self.tmp, "work")
        _git("init", "-q", self.work)
        os.makedirs(os.path.join(self.work, ".claude"), exist_ok=True)
        # Both roles equal: one of pipeline_config.py's malformed shapes.
        with open(os.path.join(self.work, ".claude", "pipeline.conf"), "w") as f:
            f.write("integration_branch=x\nrelease_branch=x\n")
        # The sandbox is its own git repo, so lib-root.sh puts LOG_DIR at
        # <sandbox>/.claude/logs; WORKFLOW_LOG_DIR points the python
        # emitters at the same dir, so every beacon stays in the sandbox.
        self.log_dir = os.path.join(self.work, ".claude", "logs")
        self.gh_dir = os.path.join(self.tmp, "fake-gh")
        os.makedirs(self.gh_dir)
        gh_path = os.path.join(self.gh_dir, "gh")
        with open(gh_path, "w", encoding="utf-8", newline="\n") as f:
            f.write("#!/usr/bin/env python3\n" + _FAKE_GH_BODY)
        os.chmod(gh_path, 0o755)

    def _run_session_start(self):
        env = os.environ.copy()
        env["PATH"] = self.gh_dir + os.pathsep + env.get("PATH", "")
        env["CLAUDE_PROJECT_DIR"] = self.work.replace(os.sep, "/")
        env["WORKFLOW_LOG_DIR"] = self.log_dir
        return subprocess.run(
            ["bash", SESSION_START_SH.as_posix()],
            input=json.dumps({"session_id": "fixture-s2d-1512"}),
            cwd=self.work, capture_output=True, text=True, timeout=60, env=env,
        )

    def test_malformed_conf_degrades_and_still_emits_gh_fields(self):
        result = self._run_session_start()
        self.assertEqual(
            result.returncode, 0,
            msg=f"session-start.sh must not exit on a resolver failure.\nstdout={result.stdout!r}\nstderr={result.stderr!r}",
        )
        self.assertTrue(result.stdout.strip(), msg=f"no context JSON emitted; stderr={result.stderr!r}")
        ctx = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]

        # A visible placeholder naming the failure, never a fallback literal.
        self.assertIn("(resolver failed:", ctx, msg=ctx)
        self.assertIn("malformed pipeline.conf", ctx, msg=ctx)
        self.assertNotIn("origin/develop", ctx, msg=ctx)
        self.assertNotIn("origin/main", ctx, msg=ctx)

        # All five GitHub fields carry the fake gh's data, so the GH_OK
        # block ran after the resolver failure.
        for label, value in self.FIELDS.items():
            self.assertIn(f"{label}: {value}", ctx, msg=f"missing GH field {label!r} in:\n{ctx}")

        # Exactly one attempt and exactly one terminal beacon, which is `ok`.
        with open(os.path.join(self.log_dir, "hook-fires.jsonl"), encoding="utf-8") as f:
            beacons = [json.loads(line) for line in f if line.strip()]
        mine = [b for b in beacons if b.get("hook") == "session-start"]
        terminal = [b["status"] for b in mine if b.get("status") in ("ok", "ERROR")]
        self.assertEqual(terminal, ["ok"], msg=f"beacons={mine!r}")
        attempts = [b for b in mine if b.get("status") == "attempt"]
        self.assertEqual(len(attempts), 1, msg=f"beacons={mine!r}")


if __name__ == "__main__":
    unittest.main()
