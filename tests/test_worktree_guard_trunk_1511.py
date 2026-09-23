"""
tests/test_worktree_guard_trunk_1511.py

Regression / new-feature test for slice #1511 (PRD #1500 walking skeleton,
ADR-0089 D1) — tools/worktree-guard.sh resolves both branch roles via
tools/pipeline_config.py rather than hardcoding develop/main, and correctly
drives branch-restore's ff-sync path against a configured `trunk` role
(mirrors PRD #1500's Sandbox S: integration_branch=trunk).

Also covers S1-c: worktree-guard.sh locates tools/pipeline_config.py
relative to its own script path, so it resolves correctly even when run by
absolute path with the cwd in a foreign repo that has no tools/ directory
of its own.

Fixture pattern follows tests/test_branch_restore_hard_align_950.py (never
the live worktree/branch set — synthetic bare-origin + clone only, per the
PR #543/#545 shared-git-fixture discipline).

Runner: stdlib unittest + pytest compatible.
  python -m pytest tests/test_worktree_guard_trunk_1511.py -v
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
GUARD_SH = REPO_ROOT / "tools" / "worktree-guard.sh"


def _git_list(args, cwd, check=True):
    return subprocess.run(
        ["git"] + args, cwd=str(cwd), check=check, capture_output=True, text=True,
    )


class TestWorktreeGuardTrunkRole(unittest.TestCase):
    """branch-restore resolves the configured integration role ('trunk' in
    this fixture, mirroring PRD #1500 Sandbox S) via tools/pipeline_config.py,
    never a hardcoded 'develop' (criterion 9's shape)."""

    def setUp(self):
        result = subprocess.run(["bash", "--version"], capture_output=True, text=True)
        if result.returncode != 0:
            self.skipTest("bash not available — skipping behavioral test")
        if not GUARD_SH.exists():
            self.fail(f"Guard script not found at {GUARD_SH}")

        self._tmpdir = tempfile.mkdtemp(prefix="guard-trunk-test-1511-")
        self.addCleanup(lambda: shutil.rmtree(self._tmpdir, ignore_errors=True))

        seed_path = os.path.join(self._tmpdir, "seed")
        os.makedirs(seed_path)
        _git_list(["init", "-q", "-b", "release"], seed_path)
        _git_list(["config", "user.email", "test@example.com"], seed_path)
        _git_list(["config", "user.name", "Test"], seed_path)
        with open(os.path.join(seed_path, "README.txt"), "w") as f:
            f.write("initial\n")
        _git_list(["add", "README.txt"], seed_path)
        _git_list(["commit", "-q", "-m", "initial commit"], seed_path)

        # trunk/release configured conf (mirrors PRD #1500 Sandbox S).
        os.makedirs(os.path.join(seed_path, ".claude"))
        with open(os.path.join(seed_path, ".claude", "pipeline.conf"), "w") as f:
            f.write("integration_branch=trunk\nrelease_branch=release\n")
        _git_list(["add", ".claude/pipeline.conf"], seed_path)
        _git_list(["commit", "-q", "-m", "add pipeline.conf"], seed_path)

        # Bare "origin".
        origin_path = os.path.join(self._tmpdir, "origin")
        os.makedirs(origin_path)
        _git_list(["clone", "-q", "--bare", seed_path, origin_path], self._tmpdir)

        # Clone into "local", create trunk at release's tip on origin.
        local_path = os.path.join(self._tmpdir, "local")
        _git_list(["clone", "-q", origin_path, local_path], self._tmpdir)
        _git_list(["config", "user.email", "test@example.com"], local_path)
        _git_list(["config", "user.name", "Test"], local_path)
        _git_list(
            ["push", "-q", "origin", "refs/remotes/origin/release:refs/heads/trunk"],
            local_path,
        )
        _git_list(["fetch", "-q", "origin"], local_path)

        self._local_path = local_path
        self._origin_trunk_sha = _git_list(
            ["rev-parse", "origin/trunk"], local_path
        ).stdout.strip()

    def test_branch_restore_from_detached_head_lands_on_origin_trunk(self):
        """Criterion 9's shape: from a detached HEAD at origin/trunk,
        branch-restore feat/2-canary leaves feat/2-canary at origin/trunk."""
        _git_list(["checkout", "-q", "--detach", "origin/trunk"], self._local_path)

        result = subprocess.run(
            ["bash", str(GUARD_SH.resolve()), "branch-restore", "feat/2-canary"],
            cwd=self._local_path,
            capture_output=True, text=True,
        )
        self.assertEqual(
            result.returncode, 0,
            msg=f"branch-restore exited {result.returncode}\nstdout={result.stdout}\nstderr={result.stderr}",
        )
        feat_sha = _git_list(["rev-parse", "feat/2-canary"], self._local_path).stdout.strip()
        self.assertEqual(
            feat_sha, self._origin_trunk_sha,
            msg="branch-restore must land feat/2-canary at origin/trunk (resolved integration role), not origin/develop",
        )

    def test_no_hardcoded_develop_literal_used(self):
        """Sanity: the resolved role really is 'trunk', not the hardcoded
        default 'develop' silently winning."""
        result = _git_list(["rev-parse", "origin/develop"], self._local_path, check=False)
        self.assertNotEqual(
            result.returncode, 0,
            msg="fixture invariant broken: origin/develop should not exist in this trunk/release sandbox",
        )


class TestWorktreeGuardForeignRepoNoToolsDir(unittest.TestCase):
    """S1-c: worktree-guard.sh locates tools/pipeline_config.py relative to
    its OWN script path — running it by absolute path with the cwd in a
    foreign repo that has a trunk conf and NO tools/ directory of its own
    must still act on trunk."""

    def setUp(self):
        result = subprocess.run(["bash", "--version"], capture_output=True, text=True)
        if result.returncode != 0:
            self.skipTest("bash not available — skipping behavioral test")
        if not GUARD_SH.exists():
            self.fail(f"Guard script not found at {GUARD_SH}")
        self._tmpdir = tempfile.mkdtemp(prefix="guard-foreign-test-1511-")
        self.addCleanup(lambda: shutil.rmtree(self._tmpdir, ignore_errors=True))

    def test_foreign_repo_no_tools_dir_still_resolves_trunk(self):
        foreign = os.path.join(self._tmpdir, "foreign")
        os.makedirs(foreign)
        _git_list(["init", "-q", "-b", "trunk"], foreign)
        _git_list(["config", "user.email", "test@example.com"], foreign)
        _git_list(["config", "user.name", "Test"], foreign)
        with open(os.path.join(foreign, "README.txt"), "w") as f:
            f.write("x\n")
        _git_list(["add", "README.txt"], foreign)
        _git_list(["commit", "-q", "-m", "init"], foreign)
        os.makedirs(os.path.join(foreign, ".claude"))
        with open(os.path.join(foreign, ".claude", "pipeline.conf"), "w") as f:
            f.write("integration_branch=trunk\n")
        # No tools/ directory anywhere in `foreign` — pipeline_config.py
        # must resolve via GUARD_SH's own BASH_SOURCE-relative path, never
        # $REPO_ROOT/tools/... or show-toplevel-of-cwd/tools/... (S1-c).
        self.assertFalse((Path(foreign) / "tools").exists())

        # No remote origin needed — branch-restore's early exit (EXPECTED
        # equals CURRENT branch) still requires resolving the roles first;
        # confirm that resolution alone doesn't crash absent a tools/ dir.
        result = subprocess.run(
            ["bash", str(GUARD_SH.resolve()), "branch-restore", "trunk"],
            cwd=foreign,
            capture_output=True, text=True,
        )
        self.assertEqual(
            result.returncode, 0,
            msg=(
                f"branch-restore must resolve roles via its own script path "
                f"even with no tools/ dir in the foreign repo.\n"
                f"stdout={result.stdout}\nstderr={result.stderr}"
            ),
        )


if __name__ == "__main__":
    unittest.main()
