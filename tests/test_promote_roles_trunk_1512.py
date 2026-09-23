"""
tests/test_promote_roles_trunk_1512.py

New-feature test for slice #1512 (PRD #1500 §2 criterion S2-promote,
ADR-0089 D1): tools/promote.sh's v2 promotion event (`from`/`to`) and its v3
promotion span (`--attr from=... --attr to=...`) must both resolve the
configured branch roles instead of hardcoding 'develop'/'main'.

Why: promote.sh:131 (the v2 promotion event) and :152/:165 (the v3 span's
`--attr from=`/`to=`) match no SCAN29 or CHECK 29 token shape. The six L2
promote fixtures and 1083 all run on this checkout's own default roles
(develop/main), so today nothing tests these two sites under OTHER role
names. This is a `trunk`/`release` variant of
tests/test_promote_v3_span_1083.py's own fixture -- same isolated-repo
approach (bare origin + working clone as REPO_ROOT, `_PROMOTE_SH_SKIP_PUSH=1`,
`TRACE_LOG_OVERRIDE`), except the isolated repo's `.claude/pipeline.conf`
holds `integration_branch=trunk` / `release_branch=release` instead of the
tracked defaults.

tests/test_promote_v3_span_1083.py itself is left byte-unchanged by this
slice (`git diff --quiet origin/develop -- tests/test_promote_v3_span_1083.py`
exits 0 on this PR's branch) -- it already covers the default-role path.

Runner: stdlib unittest + pytest compatible.
  python -m pytest tests/test_promote_roles_trunk_1512.py -v
"""

import json
import os
import platform
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

try:
    import pytest  # noqa: F401
except ImportError:
    pytest = None

REPO_ROOT = Path(__file__).parent.parent
PROMOTE_SH = REPO_ROOT / "tools" / "promote.sh"
TRACE_PY = REPO_ROOT / "tools" / "trace.py"
PIPELINE_CONFIG_PY = REPO_ROOT / "tools" / "pipeline_config.py"


def _to_bash_path(win_path: str) -> str:
    """Convert a Windows path to a bash-compatible POSIX path for Git Bash
    (mirrors tests/test_promote_v3_span_1083.py's own helper) -- needed
    because PROMOTE_HEALTH_CMD is `eval`'d inside promote.sh, and raw
    Windows backslashes are stripped by shell parsing otherwise."""
    if platform.system() != "Windows":
        return win_path
    try:
        result = subprocess.run(
            ["cygpath", "-u", win_path], capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    p = win_path.replace("\\", "/")
    p = re.sub(r"^([A-Za-z]):/", lambda m: f"/{m.group(1).lower()}/", p)
    return p


def _git(*args, cwd=None, check=True):
    return subprocess.run(
        ["git"] + list(args), cwd=cwd, check=check, capture_output=True, text=True,
    )


def _make_isolated_trunk_release_repo(parent_tmp: str):
    """Bare origin + working clone with branches `trunk` (integration) and
    `release` (release), and a tracked `.claude/pipeline.conf` naming them —
    mirrors test_promote_v3_span_1083.py's `_make_isolated_repo`, plus the
    conf file and the non-default branch names."""
    bare = os.path.join(parent_tmp, "bare.git")
    os.makedirs(bare)
    _git("init", "--bare", "-b", "release", bare)

    work = os.path.join(parent_tmp, "work")
    _git("clone", bare, work)
    _git("-C", work, "config", "user.email", "test@example.com")
    _git("-C", work, "config", "user.name", "Test")

    import shutil as _shutil
    os.makedirs(os.path.join(work, "tools"), exist_ok=True)
    _shutil.copy(str(TRACE_PY), os.path.join(work, "tools", "trace.py"))
    _shutil.copy(str(PIPELINE_CONFIG_PY), os.path.join(work, "tools", "pipeline_config.py"))

    current = subprocess.run(
        ["git", "-C", work, "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True,
    ).stdout.strip()
    if current != "release":
        _git("-C", work, "checkout", "-b", "release")

    readme = os.path.join(work, "README.md")
    with open(readme, "w") as f:
        f.write("hello")
    _git("-C", work, "add", "README.md")
    _git("-C", work, "commit", "-m", "init")
    _git("-C", work, "push", "-u", "origin", "HEAD:release")

    _git("-C", work, "checkout", "-b", "trunk")
    os.makedirs(os.path.join(work, ".claude"), exist_ok=True)
    conf_path = os.path.join(work, ".claude", "pipeline.conf")
    with open(conf_path, "w") as f:
        f.write("integration_branch=trunk\nrelease_branch=release\n")
    _git("-C", work, "add", ".claude/pipeline.conf")
    _git("-C", work, "commit", "-m", "trunk change + role conf")
    _git("-C", work, "push", "-u", "origin", "trunk")

    trunk_sha = subprocess.run(
        ["git", "-C", work, "rev-parse", "trunk"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    return work, trunk_sha


def _make_stub_health(tmpdir: str, output_line: str) -> str:
    stub_path = os.path.join(tmpdir, "stub_health.sh")
    safe_line = output_line.replace('"', '\\"')
    with open(stub_path, "w", newline="\n") as f:
        f.write("#!/bin/bash\n")
        f.write(f'printf "%s\\n" "{safe_line}"\n')
        f.write("exit 0\n")
    import stat
    mode = os.stat(stub_path).st_mode
    os.chmod(stub_path, mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return _to_bash_path(stub_path)


class TestPromoteRolesTrunkRelease(unittest.TestCase):
    """promote.sh's v2 event + v3 span carry the CONFIGURED role names
    (trunk/release), never the hardcoded develop/main literals."""

    def setUp(self):
        if not PROMOTE_SH.exists():
            self.fail(f"promote.sh not found at {PROMOTE_SH}")
        self.tmp = tempfile.mkdtemp(prefix="promote_roles_trunk_1512_")
        self.addCleanup(self._cleanup)
        self.work, self.trunk_sha = _make_isolated_trunk_release_repo(self.tmp)
        self.trace_log = os.path.join(self.tmp, "trace-v3.jsonl")

        self.env = os.environ.copy()
        # NOTE: unlike test_promote_v3_span_1083.py, MSYS_NO_PATHCONV is
        # deliberately NOT set here -- it disables MSYS's automatic
        # POSIX-to-Windows path conversion for arguments passed to native
        # (non-MSYS) executables, which breaks promote.sh's new
        # `$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)` + `python3
        # "$_DIR/pipeline_config.py"` resolver call (S1-c) -- `pwd` returns a
        # POSIX-style path that native python3.exe needs auto-converted.
        # Matches tests/test_worktree_guard_trunk_1511.py's own env, which
        # never sets this var for the same reason.
        self.env["_PROMOTE_SH_SKIP_PUSH"] = "1"
        self.env["TRACE_LOG_OVERRIDE"] = self.trace_log

        sentinel = os.path.join(self.work, ".claude", "PROMOTE_OK")
        with open(sentinel, "w") as f:
            f.write("")

        health_path = _make_stub_health(
            self.tmp, "PASS: RELEASE-READY - gate open: stub for test_promote_roles_trunk_1512"
        )
        self.env["PROMOTE_HEALTH_CMD"] = f"bash {health_path}"

    def _cleanup(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run_promote(self):
        return subprocess.run(
            ["bash", str(PROMOTE_SH)], cwd=self.work, env=self.env,
            capture_output=True, text=True, timeout=30,
        )

    def test_v3_span_attrs_use_configured_roles(self):
        result = self._run_promote()
        self.assertEqual(
            result.returncode, 0,
            msg=f"promote.sh must exit 0.\nstdout={result.stdout!r}\nstderr={result.stderr!r}",
        )
        self.assertTrue(os.path.exists(self.trace_log), msg="v3 trace log was not written")
        spans = []
        with open(self.trace_log, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    spans.append(json.loads(line))
        promotion_spans = [s for s in spans if s.get("kind") == "promotion"]
        self.assertEqual(len(promotion_spans), 1, msg=f"spans={spans!r}")
        attrs = promotion_spans[0].get("attrs", {})
        self.assertEqual(attrs.get("from"), "trunk")
        self.assertEqual(attrs.get("to"), "release")
        self.assertEqual(attrs.get("sha"), self.trunk_sha)

    def test_v2_event_from_to_use_configured_roles(self):
        result = self._run_promote()
        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
        events_log = os.path.join(self.work, ".claude", "logs", "workflow-events.jsonl")
        self.assertTrue(os.path.exists(events_log))
        with open(events_log, "r", encoding="utf-8") as f:
            v2_lines = [json.loads(line) for line in f if line.strip()]
        promotions = [e for e in v2_lines if e.get("event") == "promotion"]
        self.assertEqual(len(promotions), 1, msg=f"events={v2_lines!r}")
        self.assertEqual(promotions[0]["from"], "trunk")
        self.assertEqual(promotions[0]["to"], "release")
        self.assertEqual(promotions[0]["sha"], self.trunk_sha)


if __name__ == "__main__":
    unittest.main()
