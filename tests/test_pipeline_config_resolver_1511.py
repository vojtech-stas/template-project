"""
tests/test_pipeline_config_resolver_1511.py

Regression / new-feature tests for slice #1511 (PRD #1500 walking skeleton,
ADR-0089 D1) — the sole branch-role config parser, tools/pipeline_config.py.

Covers slice criteria:
  S1-a: no subprocess on the import path (conf present / absent / outside a
        git repo, INCLUDING the malformed-conf refusal), plus the MUST
        ref-name differential against real `git check-ref-format --branch`.
  S1-b: config is read from the caller's repo, found from the cwd (including
        a subdirectory).
  S1-d: outside any git repo, the resolver answers the defaults with exit 0.

Also covers the four malformed-conf shapes (unknown key, empty value,
invalid branch name, both roles equal) and the absent-file / present-file
default-resolution contract (PRD #1500 §2 criteria 2-4).

Runner: stdlib unittest + pytest compatible.
  python -m pytest tests/test_pipeline_config_resolver_1511.py -v
"""

import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
PIPELINE_CONFIG_PY = REPO_ROOT / "tools" / "pipeline_config.py"


def _load_pipeline_config():
    """Import tools/pipeline_config.py under a distinct module name (never
    the CLI-facing "pipeline_config" other tools use, to avoid sys.modules
    collisions across test files run in the same session)."""
    spec = importlib.util.spec_from_file_location(
        "pipeline_config_test_1511", PIPELINE_CONFIG_PY
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _git(args, cwd, check=True):
    return subprocess.run(
        ["git"] + args, cwd=str(cwd), check=check, capture_output=True, text=True,
    )


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _git(["init", "-q"], path)
    _git(["config", "user.email", "test@example.com"], path)
    _git(["config", "user.name", "Test"], path)
    (path / "README.txt").write_text("seed\n")
    _git(["add", "README.txt"], path)
    _git(["commit", "-q", "-m", "seed"], path)


# ---------------------------------------------------------------------------
# S1-a: no subprocess on the import path
# ---------------------------------------------------------------------------

class TestNoSubprocessOnImportPath(unittest.TestCase):
    """integration_branch()/release_branch() must never start a subprocess
    -- not for conf-present, conf-absent, outside-any-repo, or the
    malformed-conf refusal (S1-a)."""

    def setUp(self):
        self.mod = _load_pipeline_config()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _patched(self):
        """Context manager: Popen and os.system raise if called."""
        return (
            patch("subprocess.Popen", side_effect=AssertionError("subprocess.Popen called")),
            patch("os.system", side_effect=AssertionError("os.system called")),
        )

    def test_conf_present_no_subprocess(self):
        repo = Path(self._tmp.name) / "repo_present"
        _init_repo(repo)
        (repo / ".claude").mkdir()
        (repo / ".claude" / "pipeline.conf").write_text(
            "integration_branch=trunk\nrelease_branch=release\n"
        )
        p1, p2 = self._patched()
        with p1, p2:
            self.assertEqual(self.mod.integration_branch(start_dir=repo), "trunk")
            self.assertEqual(self.mod.release_branch(start_dir=repo), "release")

    def test_conf_absent_no_subprocess(self):
        repo = Path(self._tmp.name) / "repo_absent"
        _init_repo(repo)
        p1, p2 = self._patched()
        with p1, p2:
            self.assertEqual(self.mod.integration_branch(start_dir=repo), "develop")
            self.assertEqual(self.mod.release_branch(start_dir=repo), "main")

    def test_outside_git_repo_no_subprocess(self):
        outside = Path(self._tmp.name) / "no_repo_here"
        outside.mkdir()
        # Sanity: confirm no .git exists anywhere up this path's ancestry
        # within the tempdir tree (the tempdir root itself is never a repo).
        cur = outside.resolve()
        while cur != cur.parent:
            self.assertFalse(
                (cur / ".git").exists(),
                f"test setup invalid: found .git at {cur}",
            )
            cur = cur.parent
        p1, p2 = self._patched()
        with p1, p2:
            self.assertEqual(self.mod.integration_branch(start_dir=outside), "develop")
            self.assertEqual(self.mod.release_branch(start_dir=outside), "main")

    def test_malformed_conf_refusal_no_subprocess(self):
        """The malformed-conf refusal itself must raise WITHOUT spawning —
        the ref-name check is spawn-free too."""
        repo = Path(self._tmp.name) / "repo_malformed"
        _init_repo(repo)
        (repo / ".claude").mkdir()
        (repo / ".claude" / "pipeline.conf").write_text("bogus line with no equals\n")
        p1, p2 = self._patched()
        with p1, p2:
            with self.assertRaises(self.mod.PipelineConfigError):
                self.mod.integration_branch(start_dir=repo)


# ---------------------------------------------------------------------------
# S1-a MUST: ref-name differential against real git check-ref-format --branch
# ---------------------------------------------------------------------------

# Each entry was verified once against git 2.54.0 at 19c212e while writing
# this list (matches the slice's own verified table exactly, including the
# round-3 addendum). `@` is deliberately excluded — see the slice's note.
_MUST_ACCEPT = ["develop", "main", "trunk", "release/1.0", "feat/x-y"]
_MUST_REJECT = [
    "-lead", "a..b", "a b", "a~b", "a^b", "a:b", "a?b", "a*b", "a[b",
    "a\\b", "x.lock", "a/.b", "a//b", "a/", "a@{b", ".a", "",
    "a.", "a\x01b", "/a",
]


class TestRefNameDifferential(unittest.TestCase):
    """MUST: the pure-Python check and `git check-ref-format --branch <name>`
    (run OUTSIDE any subprocess-patched window) must agree on every name in
    the slice's required valid/invalid lists, including the round-3
    addendum (S1-a)."""

    def setUp(self):
        self.mod = _load_pipeline_config()
        r = subprocess.run(["bash", "--version"], capture_output=True)
        if r.returncode != 0:
            self.skipTest("bash/git not available")

    def _git_accepts(self, name: str) -> bool:
        r = subprocess.run(
            ["git", "check-ref-format", "--branch", name],
            capture_output=True, text=True,
        )
        return r.returncode == 0

    def test_must_accept_list_agrees_with_git(self):
        for name in _MUST_ACCEPT:
            with self.subTest(name=name):
                git_ok = self._git_accepts(name)
                py_ok = self.mod._is_valid_ref_name(name)
                self.assertTrue(git_ok, f"test-list error: git rejected {name!r}")
                self.assertTrue(
                    py_ok,
                    f"pure-Python check rejected {name!r}, but git accepts it",
                )
                self.assertEqual(py_ok, git_ok, f"mismatch for {name!r}")

    def test_must_reject_list_agrees_with_git(self):
        for name in _MUST_REJECT:
            with self.subTest(name=name):
                git_ok = self._git_accepts(name)
                py_ok = self.mod._is_valid_ref_name(name)
                self.assertFalse(git_ok, f"test-list error: git accepted {name!r}")
                self.assertFalse(
                    py_ok,
                    f"pure-Python check accepted {name!r}, but git rejects it",
                )
                self.assertEqual(py_ok, git_ok, f"mismatch for {name!r}")


# ---------------------------------------------------------------------------
# S1-b: config is read from the caller's repo, found from the cwd
# ---------------------------------------------------------------------------

class TestForeignRepoConfig(unittest.TestCase):
    """The resolver reads .claude/pipeline.conf from the repository the
    caller operates in (found from the cwd), including a subdirectory
    (S1-b)."""

    def setUp(self):
        self.mod = _load_pipeline_config()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def test_foreign_repo_with_conf_prints_trunk(self):
        repo = Path(self._tmp.name) / "foreign"
        _init_repo(repo)
        (repo / ".claude").mkdir()
        (repo / ".claude" / "pipeline.conf").write_text("integration_branch=trunk\n")
        self.assertEqual(self.mod.integration_branch(start_dir=repo), "trunk")

    def test_foreign_repo_without_conf_prints_develop(self):
        repo = Path(self._tmp.name) / "foreign_no_conf"
        _init_repo(repo)
        self.assertEqual(self.mod.integration_branch(start_dir=repo), "develop")

    def test_foreign_repo_subdirectory_same_answer(self):
        repo = Path(self._tmp.name) / "foreign_subdir"
        _init_repo(repo)
        (repo / ".claude").mkdir()
        (repo / ".claude" / "pipeline.conf").write_text("integration_branch=trunk\n")
        subdir = repo / "a" / "b" / "c"
        subdir.mkdir(parents=True)
        self.assertEqual(self.mod.integration_branch(start_dir=subdir), "trunk")

    def test_cli_by_absolute_path_in_foreign_repo(self):
        """The CLI (invoked by absolute path, cwd = the foreign repo) must
        resolve from that repo's config, not this checkout's."""
        repo = Path(self._tmp.name) / "foreign_cli"
        _init_repo(repo)
        (repo / ".claude").mkdir()
        (repo / ".claude" / "pipeline.conf").write_text("integration_branch=trunk\n")
        result = subprocess.run(
            [sys.executable, str(PIPELINE_CONFIG_PY), "integration"],
            cwd=str(repo), capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "trunk")


# ---------------------------------------------------------------------------
# S1-d: outside any git repo, the resolver answers the defaults, exit 0
# ---------------------------------------------------------------------------

class TestOutsideGitRepoDefaults(unittest.TestCase):
    """Outside any git repo, the CLI prints the defaults and exits 0 (S1-d)."""

    def test_cli_outside_repo_prints_defaults_exit_0(self):
        with tempfile.TemporaryDirectory() as tmp:
            outside = Path(tmp) / "no_repo"
            outside.mkdir()
            cur = outside.resolve()
            while cur != cur.parent:
                assert not (cur / ".git").exists(), f"test setup invalid: .git at {cur}"
                cur = cur.parent

            result_i = subprocess.run(
                [sys.executable, str(PIPELINE_CONFIG_PY), "integration"],
                cwd=str(outside), capture_output=True, text=True,
            )
            result_r = subprocess.run(
                [sys.executable, str(PIPELINE_CONFIG_PY), "release"],
                cwd=str(outside), capture_output=True, text=True,
            )
        self.assertEqual(result_i.returncode, 0, result_i.stderr)
        self.assertEqual(result_i.stdout.strip(), "develop")
        self.assertEqual(result_r.returncode, 0, result_r.stderr)
        self.assertEqual(result_r.stdout.strip(), "main")


# ---------------------------------------------------------------------------
# Malformed-conf shapes (PRD #1500 §2 criterion 4) + defaults (criteria 2-3)
# ---------------------------------------------------------------------------

class TestDefaultsAndCli(unittest.TestCase):
    """PRD #1500 §2 criteria 2-3: absent file resolves to develop/main;
    a present file's values are printed verbatim."""

    def setUp(self):
        self.mod = _load_pipeline_config()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def test_this_checkout_resolves_tracked_conf(self):
        # Criterion 2: this repo's own .claude/pipeline.conf holds develop/main.
        self.assertEqual(self.mod.integration_branch(start_dir=REPO_ROOT), "develop")
        self.assertEqual(self.mod.release_branch(start_dir=REPO_ROOT), "main")

    def test_absent_file_resolves_defaults(self):
        repo = Path(self._tmp.name) / "absent"
        _init_repo(repo)
        self.assertEqual(self.mod.integration_branch(start_dir=repo), "develop")
        self.assertEqual(self.mod.release_branch(start_dir=repo), "main")


class TestMalformedConf(unittest.TestCase):
    """PRD #1500 §2 criterion 4 + slice What-ships: malformed conf (unknown
    key, empty value, invalid branch name, both roles equal) refuses with a
    non-zero exit and a file:line message naming pipeline.conf."""

    def setUp(self):
        self.mod = _load_pipeline_config()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _write_conf(self, repo: Path, text: str) -> None:
        (repo / ".claude").mkdir(exist_ok=True)
        (repo / ".claude" / "pipeline.conf").write_text(text)

    def test_unknown_key_refused(self):
        repo = Path(self._tmp.name) / "unknown_key"
        _init_repo(repo)
        self._write_conf(repo, "bogus_key=develop\n")
        with self.assertRaises(self.mod.PipelineConfigError) as ctx:
            self.mod.integration_branch(start_dir=repo)
        self.assertIn("pipeline.conf", str(ctx.exception))

    def test_empty_value_refused(self):
        repo = Path(self._tmp.name) / "empty_value"
        _init_repo(repo)
        self._write_conf(repo, "integration_branch=\n")
        with self.assertRaises(self.mod.PipelineConfigError) as ctx:
            self.mod.integration_branch(start_dir=repo)
        self.assertIn("pipeline.conf", str(ctx.exception))

    def test_invalid_branch_name_refused(self):
        repo = Path(self._tmp.name) / "invalid_name"
        _init_repo(repo)
        self._write_conf(repo, "integration_branch=a..b\n")
        with self.assertRaises(self.mod.PipelineConfigError) as ctx:
            self.mod.integration_branch(start_dir=repo)
        self.assertIn("pipeline.conf", str(ctx.exception))

    def test_both_roles_equal_refused(self):
        repo = Path(self._tmp.name) / "both_equal"
        _init_repo(repo)
        self._write_conf(repo, "integration_branch=x\nrelease_branch=x\n")
        with self.assertRaises(self.mod.PipelineConfigError) as ctx:
            self.mod.integration_branch(start_dir=repo)
        self.assertIn("pipeline.conf", str(ctx.exception))

    def test_cli_malformed_conf_nonzero_exit_and_message(self):
        """Mirrors PRD #1500 §2 criterion 4's exact shell recipe."""
        repo = Path(self._tmp.name) / "cli_malformed"
        _init_repo(repo)
        self._write_conf(repo, "integration_branch=x\nrelease_branch=x\n")
        result = subprocess.run(
            [sys.executable, str(PIPELINE_CONFIG_PY), "integration"],
            cwd=str(repo), capture_output=True, text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("pipeline.conf", result.stderr)


if __name__ == "__main__":
    unittest.main()
