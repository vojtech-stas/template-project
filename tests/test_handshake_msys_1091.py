"""
Regression tests for root-cause capture #1091 — deploy-handshake.sh fails
under MSYS_NO_PATHCONV=1.

tools/deploy-handshake.sh derived START_DIR from MSYS-form `pwd` (e.g.
"/f/project_claude") and passed it straight to `git -C "$START_DIR"`. Git
Bash's MSYS runtime normally auto-converts POSIX-look-alike arguments like
"/f/..." into a real Windows path before spawning a native child process
(git.exe); MSYS_NO_PATHCONV=1 disables exactly that conversion. Since the
repo's own isolation protocol mandates MSYS_NO_PATHCONV=1 for every git
invocation, deploy-handshake.sh received a literal, unresolvable "/f/..."
argument on EVERY run, from any cwd, and failed with:

    ERROR: deploy-handshake: '/f/...' is not inside a git repository

This test file FAILS before #1091's fix (exit 1 with the path-resolution
ERROR line above) and PASSES after (matching-content case exits 0; the
mismatch case still exits 1, but via the DEPLOY-GAP banner, never the path
error).

All fixtures are synthetic temp git repos (via Python's tempfile module) —
NEVER the live worktree set (shared-git fixture discipline per the #543/#545
incident: isolation:"worktree" shares one .git, so any destructive op against
the real root/session tree would affect every worktree).

Runner: stdlib unittest + pytest compatible.
  python -m pytest tests/test_handshake_msys_1091.py -v
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
HANDSHAKE_SH = REPO_ROOT / "tools" / "deploy-handshake.sh"

PATH_RESOLUTION_ERROR = "is not inside a git repository"


def _bash_available() -> bool:
    try:
        r = subprocess.run(["bash", "--version"], capture_output=True, timeout=5)
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _git(*args, cwd=None, check=True):
    """Run a git command, capturing output."""
    return subprocess.run(
        ["git"] + list(args),
        cwd=str(cwd) if cwd else None,
        check=check,
        capture_output=True,
        text=True,
    )


def _build_fixture(tmp_path: Path, set_hooks_path: bool = True) -> Path:
    """Synthetic repo: .claude/hooks/session-start.sh + .claude/settings.json
    + .githooks/, committed on 'main', with core.hooksPath configured
    (unless set_hooks_path=False). Mirrors tests/test_deploy_handshake_1079.py."""
    repo = tmp_path / "repo"
    repo.mkdir()
    r = _git("init", "-b", "main", str(repo), check=False)
    if r.returncode != 0:
        _git("init", str(repo), check=True)
        _git("-C", str(repo), "checkout", "-b", "main", check=False)
    _git("-C", str(repo), "config", "user.email", "test@example.com")
    _git("-C", str(repo), "config", "user.name", "Test")

    hooks_dir = repo / ".claude" / "pipeline" / "hooks"
    hooks_dir.mkdir(parents=True)
    (hooks_dir / "session-start.sh").write_text("#!/bin/bash\necho hi\n")
    (repo / ".claude" / "settings.json").write_text('{"hooks": {}}\n')
    githooks_dir = repo / ".claude" / "pipeline" / "githooks"
    githooks_dir.mkdir(parents=True)
    (githooks_dir / "pre-commit").write_text("#!/bin/bash\nexit 0\n")

    _git("-C", str(repo), "add", ".")
    _git("-C", str(repo), "commit", "-m", "init")

    if set_hooks_path:
        _git("-C", str(repo), "config", "core.hooksPath", ".claude/pipeline/githooks")

    return repo


def _run_handshake_no_pathconv(repo: Path, *extra_args):
    """Invoke tools/deploy-handshake.sh [extra_args...] with NO start-dir CLI
    argument and NO CLAUDE_PROJECT_DIR set — forcing the script's own
    ${CLAUDE_PROJECT_DIR:-$(pwd)} fallback resolution path — with cwd set to
    the fixture repo AND MSYS_NO_PATHCONV=1 explicitly exported. This is the
    exact reproduction shape from the #1091 symptom (`bash
    tools/deploy-handshake.sh` from cwd, MSYS_NO_PATHCONV=1 exported)."""
    env = os.environ.copy()
    env["MSYS_NO_PATHCONV"] = "1"
    env.pop("CLAUDE_PROJECT_DIR", None)
    return subprocess.run(
        ["bash", str(HANDSHAKE_SH), *extra_args],
        cwd=str(repo),
        env=env,
        capture_output=True, text=True, timeout=60,
    )


class TestHandshakeMsysNoPathconv(unittest.TestCase):
    """tools/deploy-handshake.sh under MSYS_NO_PATHCONV=1 (#1091)."""

    def setUp(self):
        if not _bash_available():
            self.skipTest("bash not available in this environment")

    def test_matching_content_exits_zero_under_no_pathconv(self):
        """(a) Clean checkout, hooksPath set, HEAD attached, cwd-derived
        START_DIR, MSYS_NO_PATHCONV=1 -> exit 0 (fails before: exit 1 with
        the path-resolution ERROR, never reaching the deploy-gap banner)."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = _build_fixture(Path(tmp))
            result = _run_handshake_no_pathconv(repo)
        combined = result.stdout + result.stderr
        self.assertNotIn(
            PATH_RESOLUTION_ERROR, combined,
            msg=(
                "deploy-handshake.sh hit the MSYS path-resolution trap "
                f"(#1091) instead of resolving the repo; stdout/stderr:\n{combined}"
            ),
        )
        self.assertEqual(
            0, result.returncode,
            msg=(
                f"expected exit 0 on matching content under MSYS_NO_PATHCONV=1; "
                f"got {result.returncode}\nstdout: {result.stdout}\nstderr: {result.stderr}"
            ),
        )

    def test_mismatch_exits_nonzero_with_banner_not_path_error_under_no_pathconv(self):
        """(b) A mutated hook file, cwd-derived START_DIR, MSYS_NO_PATHCONV=1
        -> exit 1 WITH the DEPLOY-GAP banner (not the path-resolution error)."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = _build_fixture(Path(tmp))
            (repo / ".claude" / "pipeline" / "hooks" / "session-start.sh").write_text(
                "#!/bin/bash\necho MUTATED\n"
            )
            result = _run_handshake_no_pathconv(repo)
        combined = result.stdout + result.stderr
        self.assertNotIn(
            PATH_RESOLUTION_ERROR, combined,
            msg=(
                "deploy-handshake.sh hit the MSYS path-resolution trap "
                f"(#1091) instead of reaching the deploy-gap comparison; "
                f"stdout/stderr:\n{combined}"
            ),
        )
        self.assertEqual(
            1, result.returncode,
            msg=f"expected exit 1 on mutated hook file; stdout/stderr:\n{combined}",
        )
        self.assertIn("DEPLOY-GAP DETECTED", combined)
        self.assertIn("MISMATCH", combined)


if __name__ == "__main__":
    unittest.main()
