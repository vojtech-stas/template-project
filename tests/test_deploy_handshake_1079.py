"""
Regression tests for slice #1079 — deploy-gap immunity handshake.

tools/deploy-handshake.sh compares the RUNNING content of .claude/pipeline/hooks/ +
.claude/settings.json (resolved from git-common-dir parent — the checkout
hooks actually execute from) against the DEPLOYED branch's committed content
(the branch the root checkout's HEAD tracks). Per PRD #1075 criterion 4:
MATCH -> exit 0; MISMATCH, DETACHED HEAD, or hooksPath != .claude/pipeline/githooks -> LOUD
banner + exit 1.

All fixtures are synthetic temp git repos (via Python's tempfile module) —
NEVER the live worktree set (shared-git fixture discipline per the #543/#545
incident: isolation:"worktree" shares one .git, so any destructive op against
the real root/session tree would affect every worktree).

This test file FAILS before #1079 (tools/deploy-handshake.sh does not exist)
and PASSES after.

Runner: stdlib unittest + pytest compatible.
  python -m pytest tests/test_deploy_handshake_1079.py -v
"""

import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
HANDSHAKE_SH = REPO_ROOT / "tools" / "deploy-handshake.sh"


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


def _run_handshake(repo: Path, *extra_args):
    """Invoke tools/deploy-handshake.sh [extra_args...] <repo> as the
    resolution start-dir (mirrors the script's [start-dir] CLI contract)."""
    return subprocess.run(
        ["bash", str(HANDSHAKE_SH), *extra_args, str(repo)],
        capture_output=True, text=True, timeout=60,
    )


class TestDeployHandshake(unittest.TestCase):
    """tools/deploy-handshake.sh — content-hash handshake + topology checks."""

    def setUp(self):
        if not _bash_available():
            self.skipTest("bash not available in this environment")
        # NOTE: deliberately NOT skipping when HANDSHAKE_SH is absent (pre-#1079
        # state) — the assertions below must FAIL (not skip) in that state, per
        # the test-before-impl acceptance criterion ("fails before, passes after").

    def _build_fixture(self, tmp_path: Path, set_hooks_path: bool = True) -> Path:
        """Synthetic repo: .claude/pipeline/hooks/session-start.sh + .claude/settings.json
        + .claude/pipeline/githooks/, committed on 'main', with core.hooksPath
        configured (unless set_hooks_path=False)."""
        repo = tmp_path / "repo"
        repo.mkdir()
        r = _git("init", "-b", "main", str(repo), check=False)
        if r.returncode != 0:
            # Fallback for git versions without `init -b`.
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

    # (a) matching content -> exit 0
    def test_matching_content_exits_zero(self):
        """Clean checkout, hooksPath set, HEAD attached -> exit 0."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._build_fixture(Path(tmp))
            result = _run_handshake(repo)
        self.assertEqual(
            0, result.returncode,
            msg=(
                f"expected exit 0 on matching content; got {result.returncode}\n"
                f"stdout: {result.stdout}\nstderr: {result.stderr}"
            ),
        )

    # (b) mutate a hook file -> exit 1 + banner text
    def test_mutated_hook_file_exits_nonzero_with_banner(self):
        """A hook file mutated on disk (uncommitted) -> exit 1 + banner."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._build_fixture(Path(tmp))
            (repo / ".claude" / "pipeline" / "hooks" / "session-start.sh").write_text(
                "#!/bin/bash\necho MUTATED\n"
            )
            result = _run_handshake(repo)
        combined = result.stdout + result.stderr
        self.assertEqual(
            1, result.returncode,
            msg=f"expected exit 1 on mutated hook file; stdout/stderr:\n{combined}",
        )
        self.assertIn("DEPLOY-GAP DETECTED", combined)
        self.assertIn("MISMATCH", combined)

    # (c) detached HEAD fixture -> exit 1 + deploy-gap banner
    def test_detached_head_exits_nonzero_with_banner(self):
        """Detached HEAD at the root checkout IS the deploy-gap state -> exit 1."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._build_fixture(Path(tmp))
            sha = _git("-C", str(repo), "rev-parse", "HEAD").stdout.strip()
            _git("-C", str(repo), "checkout", "--detach", sha)
            result = _run_handshake(repo)
        combined = result.stdout + result.stderr
        self.assertEqual(
            1, result.returncode,
            msg=f"expected exit 1 on detached HEAD; stdout/stderr:\n{combined}",
        )
        self.assertIn("DEPLOY-GAP DETECTED", combined)
        self.assertIn("DETACHED", combined)

    # (d) hooksPath unset -> exit 1
    def test_hooks_path_unset_exits_nonzero(self):
        """core.hooksPath unset (.claude/pipeline/githooks/install.sh never run) -> exit 1."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._build_fixture(Path(tmp), set_hooks_path=False)
            result = _run_handshake(repo)
        combined = result.stdout + result.stderr
        self.assertEqual(
            1, result.returncode,
            msg=f"expected exit 1 on unset hooksPath; stdout/stderr:\n{combined}",
        )
        self.assertIn("DEPLOY-GAP DETECTED", combined)
        self.assertIn("core.hooksPath", combined)

    # --self-test mode: the CI-safe internal-consistency leg (documented split
    # between what CI can and cannot assert — see script header).
    def test_self_test_mode_passes_on_well_formed_repo(self):
        """--self-test mode: CI-safe internal consistency check passes."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._build_fixture(Path(tmp))
            result = _run_handshake(repo, "--self-test")
        self.assertEqual(
            0, result.returncode,
            msg=f"--self-test expected to pass; stdout={result.stdout} stderr={result.stderr}",
        )

    def test_self_test_mode_does_not_require_attached_head(self):
        """--self-test must NOT fail on detached HEAD (CI checkouts are
        always detached by GitHub Actions design — this is the documented
        CI-vs-local-leg split)."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._build_fixture(Path(tmp))
            sha = _git("-C", str(repo), "rev-parse", "HEAD").stdout.strip()
            _git("-C", str(repo), "checkout", "--detach", sha)
            result = _run_handshake(repo, "--self-test")
        self.assertEqual(
            0, result.returncode,
            msg=(
                "--self-test must be detached-HEAD-safe; "
                f"stdout={result.stdout} stderr={result.stderr}"
            ),
        )


if __name__ == "__main__":
    unittest.main()
