"""
tests/test_pipeline_hooks_bootstrap_1605.py

Regression tests for slice #1605 (PRD "Make the pipeline an installable,
upgradeable package", ADR-0092 D1-D2) — moving `.claude/hooks/`,
`.githooks/` and `bootstrap.sh` into the package, and extending
`package.py refresh` to merge the package-owned `.claude/settings.json`
hook entries and the generated `.gitignore` block.

Fixture discipline (rule #21): every fixture below is a synthetic tempdir
repo (or a tempdir copy of this checkout's own package tree) — never this
checkout's own `.claude/settings.json`, `.gitignore`, `.claude/logs/` or
git history. No test here writes to the production `.claude/logs/`.

Runner: stdlib unittest + pytest compatible.
    python -m pytest tests/test_pipeline_hooks_bootstrap_1605.py -v
"""
import importlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_ROOT = REPO_ROOT / ".claude" / "pipeline"


def _load_package_module():
    tools_dir = PACKAGE_ROOT / "tools"
    if str(tools_dir) not in sys.path:
        sys.path.insert(0, str(tools_dir))
    if "package" in sys.modules:
        del sys.modules["package"]
    return importlib.import_module("package")


def _copy_package_tree(dest_pipeline_dir: Path):
    """Copy this working tree's OWN .claude/pipeline/ into `dest_pipeline_dir`
    — a throwaway sandbox copy, never this checkout's own tracked files."""
    shutil.copytree(PACKAGE_ROOT, dest_pipeline_dir)
    pycache = dest_pipeline_dir / "__pycache__"
    if pycache.exists():
        shutil.rmtree(pycache)


def _git(*args, cwd):
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True
    )


class TestSettingsKeepHostEntry(unittest.TestCase):
    """pk_settings_keep_host_entry: `pkg refresh` in a host whose
    `.claude/settings.json` holds a host-owned hook entry leaves that entry
    byte-identical (ADR-0092 D2)."""

    def test_pk_settings_keep_host_entry(self):
        pkg = _load_package_module()
        with tempfile.TemporaryDirectory(prefix="pk1605-settings-host-") as tmp:
            tmp_root = Path(tmp)
            pkg_root = tmp_root / ".claude" / "pipeline"
            _copy_package_tree(pkg_root)

            host_entry = {
                "matcher": "",
                "hooks": [{"type": "command", "command": "bash \"$CLAUDE_PROJECT_DIR/my-host-hook.sh\""}],
            }
            settings_path = tmp_root / ".claude" / "settings.json"
            settings_path.parent.mkdir(parents=True, exist_ok=True)
            settings_path.write_text(
                json.dumps({"hooks": {"SessionStart": [host_entry]}}, indent=2) + "\n",
                encoding="utf-8",
            )

            pkg.refresh(tmp_root, pkg_root, write=True)

            merged = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertIn(host_entry, merged["hooks"]["SessionStart"])
            # And the package's own SessionStart entry was added alongside it.
            self.assertTrue(
                any(
                    "/.claude/pipeline/hooks/" in h.get("command", "")
                    for grp in merged["hooks"]["SessionStart"]
                    for h in grp.get("hooks", [])
                ),
                merged["hooks"]["SessionStart"],
            )


class TestSettingsIdempotent(unittest.TestCase):
    """pk_settings_idempotent: running `pkg refresh` twice leaves each
    package-owned hook entry appearing exactly once."""

    def test_pk_settings_idempotent(self):
        pkg = _load_package_module()
        with tempfile.TemporaryDirectory(prefix="pk1605-settings-idem-") as tmp:
            tmp_root = Path(tmp)
            pkg_root = tmp_root / ".claude" / "pipeline"
            _copy_package_tree(pkg_root)

            pkg.refresh(tmp_root, pkg_root, write=True)
            first = (tmp_root / ".claude" / "settings.json").read_text(encoding="utf-8")
            changed = pkg.refresh(tmp_root, pkg_root, write=True)
            second = (tmp_root / ".claude" / "settings.json").read_text(encoding="utf-8")

            self.assertEqual(first, second)
            self.assertNotIn(".claude/settings.json", changed)

            merged = json.loads(second)
            session_start_commands = [
                h.get("command", "")
                for grp in merged["hooks"]["SessionStart"]
                for h in grp.get("hooks", [])
            ]
            package_hits = [c for c in session_start_commands if "/.claude/pipeline/hooks/" in c]
            self.assertEqual(len(package_hits), 1, session_start_commands)


class TestGitignoreBlockGenerated(unittest.TestCase):
    """The .gitignore generated block (D2's sixth shim row) is written by
    refresh, marked, and idempotent."""

    def test_gitignore_block_written_and_idempotent(self):
        pkg = _load_package_module()
        with tempfile.TemporaryDirectory(prefix="pk1605-gitignore-") as tmp:
            tmp_root = Path(tmp)
            pkg_root = tmp_root / ".claude" / "pipeline"
            _copy_package_tree(pkg_root)

            pkg.refresh(tmp_root, pkg_root, write=True)
            gitignore = (tmp_root / ".gitignore").read_text(encoding="utf-8")
            self.assertIn("generated by .claude/pipeline/tools/package.py refresh", gitignore)
            self.assertIn(".claude/worktrees/", gitignore)

            changed = pkg.refresh(tmp_root, pkg_root, write=True)
            self.assertNotIn(".gitignore", changed)


class TestSelfLocationCatchesInjectedClimb(unittest.TestCase):
    """A literal own-location climb appended to a moved package hook is a
    self-location arm FAIL (PRD criterion 72 shape)."""

    def test_injected_climb_fails_self_location(self):
        pkg = _load_package_module()
        with tempfile.TemporaryDirectory(prefix="pk1605-selfloc-") as tmp:
            tmp_root = Path(tmp)
            pkg_root = tmp_root / ".claude" / "pipeline"
            _copy_package_tree(pkg_root)

            target = pkg_root / "hooks" / "pre-tool-bash-classify.py"
            original = target.read_text(encoding="utf-8")
            target.write_text(
                original
                + '\n_X_ROOT = os.path.join(os.path.dirname(__file__), "..", "..", "tools")\n',
                encoding="utf-8",
            )

            failures = pkg.self_location_arm(pkg_root)
            self.assertTrue(
                any("pre-tool-bash-classify.py" in f for f in failures), failures
            )


class TestGuardrailMembershipExtended(unittest.TestCase):
    """pk_guardrail_membership extended to the slice-3 (#1605) members:
    .claude/pipeline/hooks/ (covering settings-hooks.json) and
    .claude/pipeline/githooks/."""

    def test_pk_guardrail_membership_hooks_githooks(self):
        from dashboard import health

        for path in (
            ".claude/pipeline/hooks/session-start.sh",
            ".claude/pipeline/hooks/settings-hooks.json",
            ".claude/pipeline/githooks/pre-commit",
            # pre-move members stay members.
            ".claude/hooks/session-start.sh",
            ".githooks/pre-commit",
        ):
            with self.subTest(path=path):
                self.assertTrue(health._is_guardrail_path(path), path)


class TestCheck29HooksGithooksBootstrapCanaries(unittest.TestCase):
    """CHECK 29 arm (a): a branch-literal canary in a package hook, githook
    and bootstrap.sh is caught, and the bare pre-move roots are no longer
    scanned at all."""

    def _load_repo_identity_literals(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "check_repo_identity_literals_1605",
            str(REPO_ROOT / "tools" / "check-repo-identity-literals.py"),
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def _canary_repo(self, tmp, rel_path, text):
        subprocess.run(["git", "init", "-q", tmp], check=True)
        target = Path(tmp) / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        subprocess.run(["git", "-C", tmp, "add", "-A"], check=True)
        subprocess.run(
            ["git", "-C", tmp, "-c", "user.email=t@example.com",
             "-c", "user.name=t", "commit", "-q", "-m", "canary"],
            check=True,
        )

    def test_package_hook_canary_caught(self):
        mod = self._load_repo_identity_literals()
        with tempfile.TemporaryDirectory() as tmp:
            self._canary_repo(
                tmp, ".claude/pipeline/hooks/canary.sh",
                "#!/bin/bash\ngit fetch origin develop\n",
            )
            pattern = mod.build_pattern(mod.configured_defaults())
            violations = mod.scan_repo(tmp, pattern)
            self.assertIsNotNone(violations)
            paths = [v[0].replace("\\", "/") for v in violations]
            self.assertIn(".claude/pipeline/hooks/canary.sh", paths)

    def test_package_githook_canary_caught(self):
        mod = self._load_repo_identity_literals()
        with tempfile.TemporaryDirectory() as tmp:
            self._canary_repo(
                tmp, ".claude/pipeline/githooks/canary",
                "#!/bin/sh\ngit checkout develop\n",
            )
            pattern = mod.build_pattern(mod.configured_defaults())
            violations = mod.scan_repo(tmp, pattern)
            self.assertIsNotNone(violations)
            paths = [v[0].replace("\\", "/") for v in violations]
            self.assertIn(".claude/pipeline/githooks/canary", paths)

    def test_package_bootstrap_canary_caught(self):
        mod = self._load_repo_identity_literals()
        with tempfile.TemporaryDirectory() as tmp:
            self._canary_repo(
                tmp, ".claude/pipeline/bootstrap.sh",
                "#!/bin/bash\ngit checkout develop\n",
            )
            pattern = mod.build_pattern(mod.configured_defaults())
            violations = mod.scan_repo(tmp, pattern)
            self.assertIsNotNone(violations)
            paths = [v[0].replace("\\", "/") for v in violations]
            self.assertIn(".claude/pipeline/bootstrap.sh", paths)

    def test_bare_pre_move_roots_no_longer_scanned(self):
        mod = self._load_repo_identity_literals()
        self.assertFalse(mod.is_subject_file(".claude/hooks/canary.sh"))
        self.assertFalse(mod.is_subject_file(".githooks/canary"))
        self.assertFalse(mod.is_subject_file("bootstrap.sh"))


class TestLabelsAndHooksPath(unittest.TestCase):
    """LABELS gains `pipeline-upgrade`; core.hooksPath points at the moved
    githooks directory."""

    def test_labels_gains_pipeline_upgrade(self):
        text = (PACKAGE_ROOT / "bootstrap.sh").read_text(encoding="utf-8")
        self.assertGreaterEqual(text.count('"pipeline-upgrade|'), 1)

    def test_core_hooks_path_points_at_package_githooks(self):
        text = (PACKAGE_ROOT / "bootstrap.sh").read_text(encoding="utf-8")
        self.assertIn('core.hooksPath .claude/pipeline/githooks', text)


class TestCheck27SubjectCountParity(unittest.TestCase):
    """CHECK 27's hook/py subject globs, retargeted at the package, still
    find every moved hook script (no silent narrowing to an empty set)."""

    def test_check27_globs_find_moved_hooks(self):
        import glob
        import os

        cwd = os.getcwd()
        os.chdir(str(REPO_ROOT))
        try:
            sh_files = sorted(glob.glob(".claude/pipeline/hooks/*.sh"))
            py_files = sorted(glob.glob(".claude/pipeline/hooks/*.py"))
        finally:
            os.chdir(cwd)
        self.assertGreaterEqual(len(sh_files), 6, sh_files)
        self.assertGreaterEqual(len(py_files), 1, py_files)


class TestPackageCheckPasses(unittest.TestCase):
    """`pkg check` passes at HEAD on this checkout's own real package tree
    (the walking-skeleton proof that the move + refresh wiring is whole)."""

    def test_check_passes_on_real_tree(self):
        result = subprocess.run(
            [sys.executable, str(PACKAGE_ROOT / "tools" / "package.py"), "check"],
            cwd=str(REPO_ROOT), capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
