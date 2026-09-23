"""
tests/test_pr_open_default_base_1511.py

Regression / new-feature test for slice #1511 (PRD #1500 walking skeleton,
ADR-0089 D3) — tools/pipe/pr-open appends `--base <integration>` when the
caller passes neither `--base` nor `-B`; an explicit base wins.

Mirrors the fake-gh pattern from tests/test_trace_skeleton_1078.py.

TestPrOpenForeignRepoNoToolsDir below covers S1-c's `pr-open` leg (reviewer
round-1 F1): pr-open, run by absolute path from THIS checkout with cwd in a
FOREIGN repo that has a `trunk` conf and NO tools/ directory of its own,
must resolve the foreign repo's configured role — never this checkout's
'develop'. TestPrOpenDefaultBase above cannot catch a hard-coded `develop`
literal, because this checkout's own tracked conf also resolves to
`develop`; only a foreign, differently-configured cwd can prove the
resolver — not a literal — produced the base.

Runner: stdlib unittest + pytest compatible.
  python -m pytest tests/test_pr_open_default_base_1511.py -v
"""

import os
import platform
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PR_OPEN = REPO_ROOT / "tools" / "pipe" / "pr-open"

_FAKE_GH_BODY = '''
import os
import sys

args = sys.argv[1:]
log_path = os.environ.get("FAKE_GH_ARGV_LOG")
if log_path:
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(" ".join(args) + "\\n")

if "create" in args and "pr" in args:
    exit_code = int(os.environ.get("FAKE_GH_CREATE_EXIT", "0"))
    stdout = os.environ.get("FAKE_GH_CREATE_STDOUT", "https://github.com/o/r/pull/1")
    sys.stdout.write(stdout + "\\n")
    sys.exit(exit_code)

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


class TestPrOpenDefaultBase(unittest.TestCase):
    """tools/pipe/pr-open appends --base <integration> when the caller
    passes no --base/-B; an explicit base always wins (ADR-0089 D3)."""

    def setUp(self):
        if not PR_OPEN.exists():
            self.skipTest(f"tools/pipe/pr-open not found at {PR_OPEN}")
        self.tmp = tempfile.mkdtemp(prefix="pr_open_base_test_")
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, args, cwd=None):
        fake_gh_dir = _write_fake_gh(self.tmp)
        argv_log = os.path.join(self.tmp, "gh_argv.log")
        log_path = os.path.join(self.tmp, "trace-v3.jsonl")
        body_file = os.path.join(self.tmp, "body.md")
        with open(body_file, "w", encoding="utf-8") as f:
            f.write("Some PR body.\n")

        env = os.environ.copy()
        env.update({
            "PATH": fake_gh_dir + os.pathsep + env.get("PATH", ""),
            "TRACE_LOG_OVERRIDE": log_path,
            "FAKE_GH_ARGV_LOG": argv_log,
            "FAKE_GH_CREATE_EXIT": "0",
            "FAKE_GH_CREATE_STDOUT": "https://github.com/o/r/pull/9999",
        })
        cmd = [sys.executable, str(PR_OPEN), "--title", "t", "--body-file", body_file] + args
        result = subprocess.run(
            cmd, cwd=cwd or str(REPO_ROOT), env=env, capture_output=True, text=True, timeout=30,
        )
        argv_text = ""
        if os.path.exists(argv_log):
            argv_text = Path(argv_log).read_text(encoding="utf-8")
        return result, argv_text

    def test_no_base_appends_configured_integration(self):
        """This checkout's .claude/pipeline.conf resolves integration to
        'develop' — no --base/-B passed means pr-open must append it."""
        result, argv_text = self._run([])
        self.assertEqual(result.returncode, 0, f"stdout={result.stdout!r} stderr={result.stderr!r}")
        create_lines = [l for l in argv_text.splitlines() if "create" in l]
        self.assertEqual(len(create_lines), 1, f"expected exactly one gh pr create call, got: {argv_text!r}")
        self.assertIn("--base develop", create_lines[0])

    def test_explicit_base_wins_over_default(self):
        result, argv_text = self._run(["--base", "other-target"])
        self.assertEqual(result.returncode, 0, f"stdout={result.stdout!r} stderr={result.stderr!r}")
        create_lines = [l for l in argv_text.splitlines() if "create" in l]
        self.assertEqual(len(create_lines), 1)
        self.assertIn("--base other-target", create_lines[0])
        # Only ONE --base flag must be present (no double-append).
        self.assertEqual(create_lines[0].count("--base "), 1)

    def test_explicit_short_flag_b_wins_over_default(self):
        result, argv_text = self._run(["-B", "another-target"])
        self.assertEqual(result.returncode, 0, f"stdout={result.stdout!r} stderr={result.stderr!r}")
        create_lines = [l for l in argv_text.splitlines() if "create" in l]
        self.assertEqual(len(create_lines), 1)
        self.assertIn("-B another-target", create_lines[0])
        self.assertNotIn("--base", create_lines[0])


def _git(args, cwd, check=True):
    return subprocess.run(["git"] + args, cwd=str(cwd), check=check, capture_output=True, text=True)


class TestPrOpenForeignRepoNoToolsDir(unittest.TestCase):
    """S1-c (reviewer round-1 F1): tools/pipe/pr-open locates
    tools/pipeline_config.py relative to ITS OWN script path — never
    `$REPO_ROOT/...` or `git rev-parse --show-toplevel` of the cwd repo — so
    running it by absolute path with the cwd in a foreign repo that has a
    `trunk` conf and NO tools/ directory of its own must still resolve that
    foreign repo's configured integration branch, 'trunk'."""

    def setUp(self):
        if not PR_OPEN.exists():
            self.skipTest(f"tools/pipe/pr-open not found at {PR_OPEN}")
        result = subprocess.run(["git", "--version"], capture_output=True, text=True)
        if result.returncode != 0:
            self.skipTest("git not available — skipping behavioral test")

        self.tmp = tempfile.mkdtemp(prefix="pr_open_foreign_test_1511_")
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))

        self._foreign = os.path.join(self.tmp, "foreign")
        os.makedirs(self._foreign)
        _git(["init", "-q", "-b", "trunk"], self._foreign)
        _git(["config", "user.email", "test@example.com"], self._foreign)
        _git(["config", "user.name", "Test"], self._foreign)
        with open(os.path.join(self._foreign, "README.txt"), "w") as f:
            f.write("x\n")
        _git(["add", "README.txt"], self._foreign)
        _git(["commit", "-q", "-m", "init"], self._foreign)
        os.makedirs(os.path.join(self._foreign, ".claude"))
        with open(os.path.join(self._foreign, ".claude", "pipeline.conf"), "w") as f:
            f.write("integration_branch=trunk\n")
        # No tools/ directory anywhere in `foreign` — pipeline_config.py must
        # resolve via pr-open's own file-relative path (S1-c), never
        # $REPO_ROOT/tools/... or show-toplevel-of-cwd/tools/....
        self.assertFalse((Path(self._foreign) / "tools").exists())

    def _run_in_foreign(self, args):
        fake_gh_dir = _write_fake_gh(self.tmp)
        argv_log = os.path.join(self.tmp, "gh_argv.log")
        log_path = os.path.join(self.tmp, "trace-v3.jsonl")
        body_file = os.path.join(self.tmp, "body.md")
        with open(body_file, "w", encoding="utf-8") as f:
            f.write("Some PR body.\n")

        env = os.environ.copy()
        env.update({
            "PATH": fake_gh_dir + os.pathsep + env.get("PATH", ""),
            "TRACE_LOG_OVERRIDE": log_path,
            "FAKE_GH_ARGV_LOG": argv_log,
            "FAKE_GH_CREATE_EXIT": "0",
            "FAKE_GH_CREATE_STDOUT": "https://github.com/o/r/pull/9999",
        })
        cmd = [sys.executable, str(PR_OPEN), "--title", "t", "--body-file", body_file] + args
        result = subprocess.run(
            cmd, cwd=self._foreign, env=env, capture_output=True, text=True, timeout=30,
        )
        argv_text = ""
        if os.path.exists(argv_log):
            argv_text = Path(argv_log).read_text(encoding="utf-8")
        return result, argv_text

    def test_no_base_resolves_foreign_repo_trunk(self):
        """cwd is a foreign repo (trunk conf, no tools/ dir): pr-open, run by
        absolute path from THIS checkout, must append --base trunk — never
        this checkout's own 'develop' (S1-c)."""
        result, argv_text = self._run_in_foreign([])
        self.assertEqual(result.returncode, 0, f"stdout={result.stdout!r} stderr={result.stderr!r}")
        create_lines = [l for l in argv_text.splitlines() if "create" in l]
        self.assertEqual(len(create_lines), 1, f"expected exactly one gh pr create call, got: {argv_text!r}")
        self.assertIn("--base trunk", create_lines[0])

    def test_explicit_base_passes_through_unchanged_in_foreign_repo(self):
        """An explicit --base always wins, even run from a foreign repo whose
        conf would otherwise resolve to 'trunk'."""
        result, argv_text = self._run_in_foreign(["--base", "other-target"])
        self.assertEqual(result.returncode, 0, f"stdout={result.stdout!r} stderr={result.stderr!r}")
        create_lines = [l for l in argv_text.splitlines() if "create" in l]
        self.assertEqual(len(create_lines), 1)
        self.assertIn("--base other-target", create_lines[0])
        self.assertEqual(create_lines[0].count("--base "), 1)


if __name__ == "__main__":
    unittest.main()
