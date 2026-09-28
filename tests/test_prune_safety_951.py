"""
Regression tests for issue #951 — worktree-guard prune silently removes
worktrees with in-flight background work.

Two test groups:
  1. Prune-safety: a worktree with a .gate-running marker is NOT removed
     by `tools/worktree-guard.sh prune`, and prune logs a skip message,
     even when all other reclaim conditions (no-PR, clean, 0-ahead, aged)
     are satisfied.
  2. qa-tester contract: .claude/agents/qa-tester.md contains the
     synchronous-long-command rule and .gate-running marker contract.

All assertions use a synthetic git repo fixture (never the live worktree
set) — the shared-.git isolation discipline per the #543/#545 incident.

Runner: stdlib unittest + pytest compatible (no top-level pytest).
  python -m pytest tests/test_prune_safety_951.py -v
"""

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
GUARD_SH = REPO_ROOT / "tools" / "worktree-guard.sh"
QA_TESTER_MD = REPO_ROOT / ".claude" / "pipeline" / "agents" / "qa-tester.md"


def _bash_available() -> bool:
    try:
        r = subprocess.run(["bash", "--version"], capture_output=True, timeout=5)
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _git(*args, cwd=None, check=True):
    """Run a git command, suppressing output."""
    return subprocess.run(
        ["git"] + list(args),
        cwd=str(cwd) if cwd else None,
        check=check,
        capture_output=True,
        text=True,
    )


class TestPruneSafetyGateRunning(unittest.TestCase):
    """prune must SKIP a worktree that has a .gate-running marker file."""

    def setUp(self):
        if not _bash_available():
            self.skipTest("bash not available in this environment")
        if not GUARD_SH.exists():
            self.skipTest(f"worktree-guard.sh not found at {GUARD_SH}")

    def _build_fixture(self, tmp_path: Path, marker_present: bool):
        """
        Build a synthetic git environment:

          tmp_path/
            bare/            <- bare repo acting as 'origin' (branch: main)
            repo/            <- main worktree (checks out main)
            agent-zzztest/   <- dispatch worktree (agent- prefix → eligible)

        The agent worktree satisfies all no-PR reclamation conditions:
          - basename starts with "agent-" (PRIMARY safety guard pass-through)
          - stub gh reports 0 open + 0 merged PRs
          - clean working tree (no changes)
          - 0 commits ahead of origin/main
          - mtime forced to >24h ago

        A stub `gh` is placed first on PATH so worktree-guard.sh sees it.

        Returns:
            (repo, agent_wt, stub_dir) as Path objects.
        """
        # --- Bare repo = origin ---
        bare = tmp_path / "bare"
        bare.mkdir()
        _git("init", "--bare", "-b", "main", str(bare))

        # --- Working repo ---
        repo = tmp_path / "repo"
        repo.mkdir()
        _git("init", "-b", "main", str(repo))
        _git("-C", str(repo), "config", "user.email", "test@example.com")
        _git("-C", str(repo), "config", "user.name", "Test")
        (repo / "README.md").write_text("test")
        _git("-C", str(repo), "add", ".")
        _git("-C", str(repo), "commit", "-m", "init")
        _git("-C", str(repo), "remote", "add", "origin", str(bare))
        _git("-C", str(repo), "push", "origin", "main")

        # Create the agent branch from main
        _git("-C", str(repo), "checkout", "-b", "fix/agent-zzztest-branch")
        _git("-C", str(repo), "checkout", "main")

        # Add the agent worktree
        agent_wt = tmp_path / "agent-zzztest"
        _git("-C", str(repo), "worktree", "add",
             str(agent_wt), "fix/agent-zzztest-branch")

        # Fetch origin/main into the agent worktree
        _git("-C", str(agent_wt), "fetch", "origin", check=False)

        # Force mtime > 24h ago (satisfies the aged condition)
        old_time = time.time() - 90000  # 25 hours ago
        os.utime(str(agent_wt), (old_time, old_time))

        # Optionally place the .gate-running marker
        if marker_present:
            (agent_wt / ".gate-running").write_text("gate in progress")

        # Stub gh: always reports 0 PRs (simulates no-PR reclamation eligibility)
        stub_dir = tmp_path / "stub-bin"
        stub_dir.mkdir()
        stub_gh = stub_dir / "gh"
        stub_gh.write_text(
            "#!/bin/bash\n"
            "# Stub gh: always reports 0 PRs\n"
            "echo '0'\n"
            "exit 0\n"
        )
        stub_gh.chmod(0o755)

        return repo, agent_wt, stub_dir

    def _run_prune(self, repo: Path, stub_dir: Path) -> subprocess.CompletedProcess:
        """Run worktree-guard.sh prune from the repo directory."""
        env = os.environ.copy()
        env["PATH"] = str(stub_dir) + os.pathsep + env.get("PATH", "")
        env["CLAUDE_PROJECT_DIR"] = str(repo)
        return subprocess.run(
            ["bash", str(GUARD_SH), "prune"],
            capture_output=True, text=True,
            env=env,
            cwd=str(repo),
            timeout=60,
        )

    def test_prune_skips_worktree_with_gate_running_marker(self):
        """
        A worktree with .gate-running present must NOT be removed by prune
        and prune must log a skip message, even when the worktree would
        otherwise qualify for no-PR reclamation.

        This test FAILS on develop (before fix) because prune has no
        marker-skip logic and never logs 'skipped: gate-running'.
        """
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo, agent_wt, stub_dir = self._build_fixture(
                tmp_path, marker_present=True)
            prune_result = self._run_prune(repo, stub_dir)

        # Worktree must still exist after prune
        # (agent_wt is inside the TemporaryDirectory which is now deleted, so
        # we can't check existence — instead we check the log output which is
        # the contractual signal)
        combined = prune_result.stdout + prune_result.stderr

        self.assertTrue(
            "gate-running" in combined or "skipped" in combined,
            msg=(
                "REGRESSION: prune did not log a skip message for the "
                ".gate-running worktree. Expected 'skipped: gate-running' "
                "or similar in stdout/stderr.\n"
                f"Exit code: {prune_result.returncode}\n"
                f"stdout: {prune_result.stdout[:600]}\n"
                f"stderr: {prune_result.stderr[:600]}"
            ),
        )

    def test_prune_skips_gate_running_worktree_not_removed(self):
        """
        Belt-and-suspenders: run prune against a LIVE tmpdir (not cleaned up
        between prune and stat) to confirm the worktree directory still exists.
        """
        tmp = tempfile.mkdtemp()
        try:
            tmp_path = Path(tmp)
            repo, agent_wt, stub_dir = self._build_fixture(
                tmp_path, marker_present=True)
            prune_result = self._run_prune(repo, stub_dir)

            self.assertTrue(
                agent_wt.exists(),
                msg=(
                    "REGRESSION: prune removed an agent worktree that had a "
                    ".gate-running marker. This is the #951 bug — in-flight gate "
                    "processes are silently killed.\n"
                    f"prune exit: {prune_result.returncode}\n"
                    f"prune stdout: {prune_result.stdout[:500]}\n"
                    f"prune stderr: {prune_result.stderr[:500]}"
                ),
            )
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_prune_no_error_on_clean_repo(self):
        """
        Smoke test: prune must exit 0 (or 1 max) on a clean repo with no
        agent worktrees. Verifies the marker-skip code path doesn't break
        prune for repos without any eligible worktrees.
        """
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo = tmp_path / "repo"
            repo.mkdir()
            _git("init", "-b", "main", str(repo), check=False)
            # Fallback for older git that doesn't support -b
            _git("init", str(repo), check=False)
            _git("-C", str(repo), "config", "user.email", "test@example.com", check=False)
            _git("-C", str(repo), "config", "user.name", "Test", check=False)
            (repo / "README.md").write_text("test")
            _git("-C", str(repo), "add", ".", check=False)
            _git("-C", str(repo), "commit", "-m", "init", check=False)

            stub_dir = tmp_path / "stub-bin"
            stub_dir.mkdir()
            stub_gh = stub_dir / "gh"
            stub_gh.write_text("#!/bin/bash\necho '0'\nexit 0\n")
            stub_gh.chmod(0o755)

            result = self._run_prune(repo, stub_dir)

        self.assertIn(
            result.returncode, (0, 1),
            msg=(
                f"prune crashed (exit {result.returncode}) on a clean repo. "
                f"stderr: {result.stderr[:300]}"
            ),
        )


class TestQaTesterContract(unittest.TestCase):
    """qa-tester.md must contain the synchronous-long-command rule and
    the .gate-running marker contract.

    This test FAILS on develop (before fix) because qa-tester.md lacks
    the contract text.
    """

    def test_qa_tester_contains_sync_long_cmd_rule(self):
        """qa-tester.md must declare that long-running commands MUST run
        synchronously (NEVER run_in_background)."""
        text = QA_TESTER_MD.read_text(encoding="utf-8")
        sync_phrases = [
            "NEVER run_in_background",
            "never run_in_background",
            "synchronously",
            "SYNCHRONOUSLY",
        ]
        found = any(phrase in text for phrase in sync_phrases)
        self.assertTrue(
            found,
            msg=(
                "qa-tester.md does not contain the synchronous-long-command rule. "
                "Long-running commands must run synchronously (NEVER run_in_background) "
                "so the gate completes within the agent's lifetime. "
                "This is the qa-tester half of the #951 contract.\n"
                f"Checked phrases: {sync_phrases}\n"
                f"File: {QA_TESTER_MD}"
            ),
        )

    def test_qa_tester_contains_gate_running_marker_contract(self):
        """qa-tester.md must describe the .gate-running marker contract."""
        text = QA_TESTER_MD.read_text(encoding="utf-8")
        self.assertIn(
            ".gate-running",
            text,
            msg=(
                "qa-tester.md does not mention the .gate-running marker contract. "
                "When a qa-tester runs a long command, it must create .gate-running "
                "at its worktree root so prune skips it, and remove it when done. "
                f"File: {QA_TESTER_MD}"
            ),
        )


if __name__ == "__main__":
    unittest.main()
