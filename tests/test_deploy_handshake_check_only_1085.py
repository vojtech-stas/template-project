"""
Regression tests for PRD #1075 slice #1085 — tools/deploy-handshake.sh
--check-only flag + hooksPath path-identity comparison (the #1093 flapper fix).

Two changes under test:
  1. --check-only: a NON-MUTATING, NEVER-BLOCKING leg (always exit 0) that
     prints a machine-parseable "STATUS: PASS|FAIL" + "detail: ..." pair —
     dashboard/health.py's check_deploy_handshake() shells to this so a real
     content mismatch surfaces as a named FAIL health row, not a raw
     subprocess exit-1 the health check would have to special-case.
  2. hooksPath comparison now compares PATH IDENTITY, not string equality:
     an ABSOLUTE core.hooksPath that resolves to the same directory as the
     documented relative ".githooks" must PASS (the #1093 root-cause capture
     — .githooks/install.sh sometimes writes an absolute path).

All fixtures are synthetic temp git repos (via Python's tempfile module) —
NEVER the live worktree set (shared-git fixture discipline per the #543/#545
incident).

This test file FAILS on develop before slice #1085 (no --check-only flag
exists at all — argparse falls through to the normal blocking leg with
"--check-only" treated as [start-dir], which is not a git repo, so it exits 1
with a path-resolution ERROR rather than "STATUS: ...") and PASSES after.

Runner: stdlib unittest + pytest compatible.
  python -m pytest tests/test_deploy_handshake_check_only_1085.py -v
"""

import re
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
    return subprocess.run(
        ["git"] + list(args),
        cwd=str(cwd) if cwd else None,
        check=check,
        capture_output=True,
        text=True,
    )


def _run_handshake(repo: Path, *extra_args):
    return subprocess.run(
        ["bash", str(HANDSHAKE_SH), *extra_args, str(repo)],
        capture_output=True, text=True, timeout=60,
    )


def _build_fixture(tmp_path: Path, hooks_path_value: str = ".claude/pipeline/githooks") -> Path:
    """Synthetic repo: .claude/hooks/session-start.sh + .claude/settings.json
    + .githooks/, committed on 'main'. hooks_path_value is written verbatim
    to core.hooksPath (mirrors test_deploy_handshake_1079.py's fixture, with
    an added knob for identity-equivalent absolute forms)."""
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

    if hooks_path_value is not None:
        _git("-C", str(repo), "config", "core.hooksPath", hooks_path_value)

    return repo


class TestCheckOnlyNeverBlocks(unittest.TestCase):
    """--check-only always exits 0, whether the underlying comparison PASSes
    or FAILs — the whole point of the flag."""

    def setUp(self):
        if not _bash_available():
            self.skipTest("bash not available in this environment")

    def test_check_only_exits_zero_on_matching_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _build_fixture(Path(tmp))
            result = _run_handshake(repo, "--check-only")
        self.assertEqual(
            0, result.returncode,
            msg=f"--check-only must never block; stdout={result.stdout} stderr={result.stderr}",
        )
        self.assertIn("STATUS: PASS", result.stdout)

    def test_check_only_exits_zero_even_on_injected_mismatch(self):
        """The core acceptance case: a real content mismatch must still exit 0,
        reported as STATUS: FAIL (data), never as a non-zero process exit."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = _build_fixture(Path(tmp))
            (repo / ".claude" / "pipeline" / "hooks" / "session-start.sh").write_text(
                "#!/bin/bash\necho MUTATED\n"
            )
            result = _run_handshake(repo, "--check-only")
        self.assertEqual(
            0, result.returncode,
            msg=(
                "--check-only must NEVER block, even on a real mismatch; "
                f"stdout={result.stdout} stderr={result.stderr}"
            ),
        )
        self.assertIn("STATUS: FAIL", result.stdout)
        self.assertIn("detail:", result.stdout)
        self.assertIn("MISMATCH", result.stdout)

    def test_check_only_exits_zero_on_detached_head(self):
        """Even the detached-HEAD deploy-gap case must not block under --check-only."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = _build_fixture(Path(tmp))
            sha = _git("-C", str(repo), "rev-parse", "HEAD").stdout.strip()
            _git("-C", str(repo), "checkout", "--detach", sha)
            result = _run_handshake(repo, "--check-only")
        self.assertEqual(0, result.returncode)
        self.assertIn("STATUS: FAIL", result.stdout)

    def test_check_only_output_is_structured_status_and_detail(self):
        """Output must contain a parseable STATUS: line and a detail: line
        (the exact contract dashboard/health.py's check_deploy_handshake()
        regex-parses)."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = _build_fixture(Path(tmp))
            result = _run_handshake(repo, "--check-only")
        self.assertRegex(result.stdout, re.compile(r'^STATUS:\s*(PASS|FAIL)', re.MULTILINE))
        self.assertRegex(result.stdout, re.compile(r'^detail:\s*.+', re.MULTILINE))


class TestHooksPathIdentityComparison(unittest.TestCase):
    """core.hooksPath identity-equivalent forms (relative vs absolute) must
    both PASS — the #1093 flapper (.githooks/install.sh sometimes writes an
    absolute path that resolves to the same directory as the documented
    relative '.githooks')."""

    def setUp(self):
        if not _bash_available():
            self.skipTest("bash not available in this environment")

    def test_relative_hooks_path_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _build_fixture(Path(tmp), hooks_path_value=".claude/pipeline/githooks")
            result = _run_handshake(repo, "--check-only")
        self.assertIn("STATUS: PASS", result.stdout, msg=result.stdout)

    def test_absolute_hooks_path_resolving_to_same_dir_passes(self):
        """core.hooksPath set to the repo's OWN absolute .githooks path (the
        #1093 shape) must PASS, not false-flag a deploy-gap."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = _build_fixture(Path(tmp), hooks_path_value=None)
            abs_githooks = str((repo / ".claude" / "pipeline" / "githooks").resolve())
            _git("-C", str(repo), "config", "core.hooksPath", abs_githooks)
            result = _run_handshake(repo, "--check-only")
        self.assertIn(
            "STATUS: PASS", result.stdout,
            msg=(
                "an absolute core.hooksPath resolving to the same directory as "
                f"'.githooks' must not false-flag a deploy-gap (#1093); stdout={result.stdout}"
            ),
        )

    def test_genuinely_wrong_hooks_path_still_fails(self):
        """A hooksPath pointing somewhere ELSE entirely must still FAIL —
        the identity-comparison fix must not become a blanket pass-through."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = _build_fixture(Path(tmp), hooks_path_value="some/other/dir")
            result = _run_handshake(repo, "--check-only")
        self.assertIn("STATUS: FAIL", result.stdout, msg=result.stdout)
        self.assertIn("core.hooksPath", result.stdout)

    def test_unset_hooks_path_still_fails_under_check_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _build_fixture(Path(tmp), hooks_path_value=None)
            result = _run_handshake(repo, "--check-only")
        self.assertIn("STATUS: FAIL", result.stdout, msg=result.stdout)
        self.assertIn("core.hooksPath", result.stdout)


if __name__ == "__main__":
    unittest.main()
