"""
tests/test_pipeline_package_1603.py

Regression / walking-skeleton tests for slice #1603 (PRD "Make the pipeline
an installable, upgradeable package", ADR-0092 D1-D3) — the package
skeleton at `.claude/pipeline/`: publish, install, refresh, upgrade, check,
shape, plus the root resolver stub and the CHECK 29 consumer sweep.

Every sandbox below is a LOCAL, throwaway git repository under a pytest
tempdir (ADR-0092 D3's throwaway-verification-repository carve-out) — never
this checkout's own history, never a `git worktree` of the shared `.git`,
and never a push to GitHub. Nothing here writes to `.claude/logs/` (rule
#21 fixture discipline).

Runner: stdlib unittest + pytest compatible.
    python -m pytest tests/test_pipeline_package_1603.py -v
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_ROOT = REPO_ROOT / ".claude" / "pipeline"
PACKAGE_PY = PACKAGE_ROOT / "tools" / "package.py"
ROOT_STUB_PY = REPO_ROOT / "tools" / "pipeline_config.py"


def _git(args, cwd, check=True, env=None):
    return subprocess.run(
        ["git"] + args, cwd=str(cwd), capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=check, env=env,
    )


def _init_identity(path):
    _git(["init", "-q"], path)
    _git(["config", "user.email", "test@example.com"], path)
    _git(["config", "user.name", "Test"], path)
    _git(["config", "commit.gpgsign", "false"], path)


def _run_package(args, cwd, check=False):
    return subprocess.run(
        [sys.executable, str(PACKAGE_PY)] + args,
        cwd=str(cwd), capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=check,
    )


def _copy_package_tree(dest_pipeline_dir: Path):
    """Copy this working tree's OWN .claude/pipeline/ into `dest_pipeline_dir`
    — a synthetic source repository, never a split of real home history."""
    shutil.copytree(PACKAGE_ROOT, dest_pipeline_dir)
    # Never carry a stray .git from the source into the copy.
    pycache = dest_pipeline_dir / "__pycache__"
    if pycache.exists():
        shutil.rmtree(pycache)


class PackageSandboxMixin:
    """Builds a synthetic publisher repo W with the real package content
    under .claude/pipeline/, on branch `main`, plus a bare "origin" U whose
    `main` matches W's HEAD (so `publish`'s not-promoted check passes by
    default)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="pk1603-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def _build_publisher(self, version="9.9.9") -> Path:
        scratch = Path(tempfile.mkdtemp(prefix="w-", dir=str(self.tmp)))
        w = scratch / "W"
        w.mkdir()
        _init_identity(w)
        _git(["checkout", "-q", "-b", "main"], w, check=False)
        _copy_package_tree(w / ".claude" / "pipeline")
        (w / ".claude" / "pipeline" / "VERSION").write_text(version + "\n", encoding="utf-8")
        _git(["add", "-A"], w)
        _git(["commit", "-q", "-m", "chore(test): seed package tree"], w)

        u_bare = scratch / "U.git"
        _git(["clone", "-q", "--bare", str(w), str(u_bare)], scratch)
        _git(["remote", "add", "origin", str(u_bare)], w)
        _git(["fetch", "-q", "origin"], w)
        # Simulate "promoted": U's main == W's HEAD.
        head = _git(["rev-parse", "HEAD"], w).stdout.strip()
        _git(["update-ref", "refs/heads/main", head], u_bare)
        _git(["fetch", "-q", "origin"], w)
        self.u_bare = u_bare
        return w

    def _publish(self, w: Path, version: str):
        return _run_package(["publish", version], w)


class TestSquashShape(PackageSandboxMixin, unittest.TestCase):
    """pk_squash_shape: a real add + two pulls chain; `package.py shape`
    exits 0 on each squash commit and non-zero on non-squash-shape ones."""

    def test_pk_squash_shape(self):
        w = self._build_publisher(version="1.0.0")
        r = self._publish(w, "1.0.0")
        self.assertEqual(r.returncode, 0, r.stderr)

        h = self.tmp / "H"
        h.mkdir()
        _init_identity(h)
        (h / "README.txt").write_text("host\n", encoding="utf-8")
        _git(["add", "-A"], h)
        _git(["commit", "-q", "-m", "chore(host): init"], h)
        u_path = str(self.u_bare)
        r = _git(["subtree", "add", "--prefix=.claude/pipeline", u_path, "v1.0.0", "--squash",
                   "-m", "chore(pipeline): install v1.0.0"], h, check=False)
        self.assertEqual(r.returncode, 0, r.stderr)

        # `subtree add`'s HEAD is a merge commit (host history + the squash
        # commit); the squash-shape commit itself is the actual subject.
        install_sha = self._newest_subtree_commit(h)
        ok, note = self._shape(install_sha, h)
        self.assertTrue(ok, note)

        # Publish v1.0.1 and pull it in — a second squash, parented on the first.
        (w / ".claude" / "pipeline" / "UPGRADING.md").write_text("v2\n", encoding="utf-8")
        (w / ".claude" / "pipeline" / "VERSION").write_text("1.0.1\n", encoding="utf-8")
        _git(["add", "-A"], w)
        _git(["commit", "-q", "-m", "chore(test): bump v1.0.1"], w)
        _git(["push", "-q", "origin", "HEAD:main"], w)
        _git(["-C", str(self.u_bare), "update-ref", "refs/heads/main",
              _git(["rev-parse", "HEAD"], w).stdout.strip()], self.tmp)
        r = self._publish(w, "1.0.1")
        self.assertEqual(r.returncode, 0, r.stderr)

        r = _git(["subtree", "pull", "--prefix=.claude/pipeline", u_path, "v1.0.1", "--squash",
                   "-m", "chore(pipeline): upgrade to v1.0.1"], h, check=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        pull_sha = self._newest_subtree_commit(h)
        ok, note = self._shape(pull_sha, h)
        self.assertTrue(ok, note)

        # A whole-repository (non-squash-shape) commit FAILs.
        (h / "extra.txt").write_text("x\n", encoding="utf-8")
        _git(["add", "-A"], h)
        _git(["commit", "-q", "-m", "chore(host): non-subtree commit"], h)
        whole_sha = _git(["rev-parse", "HEAD"], h).stdout.strip()
        ok, note = self._shape(whole_sha, h)
        self.assertFalse(ok, "a non-subtree-trailer commit must not be squash-shape")

    def _shape(self, rev, cwd):
        r = _run_package(["shape", rev], cwd)
        return r.returncode == 0, r.stdout + r.stderr

    def _newest_subtree_commit(self, cwd):
        return _git(
            ["log", "-n1", "--format=%H",
             "--grep=^git-subtree-dir: \\.claude/pipeline$", "HEAD"],
            cwd,
        ).stdout.strip()


class TestPublishRefusals(PackageSandboxMixin, unittest.TestCase):
    def test_pk_publish_refusals(self):
        # dirty-tree
        w = self._build_publisher("1.1.0")
        (w / "junk.txt").write_text("x", encoding="utf-8")
        r = self._publish(w, "1.1.0")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("dirty-tree", r.stderr)

        # version-mismatch
        w = self._build_publisher("1.1.0")
        r = self._publish(w, "9.9.9")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("version-mismatch", r.stderr)

        # tag-exists
        w = self._build_publisher("1.2.0")
        r = self._publish(w, "1.2.0")
        self.assertEqual(r.returncode, 0, r.stderr)
        r = self._publish(w, "1.2.0")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("tag-exists", r.stderr)

        # not-newer
        w = self._build_publisher("1.3.0")
        self.assertEqual(self._publish(w, "1.3.0").returncode, 0)
        (w / ".claude" / "pipeline" / "VERSION").write_text("1.2.5\n", encoding="utf-8")
        _git(["commit", "-am", "chore(test): downgrade"], w)
        r = self._publish(w, "1.2.5")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("not-newer", r.stderr)

        # not-promoted
        w = self._build_publisher("1.4.0")
        _git(["commit", "--allow-empty", "-m", "chore(test): unpromoted commit"], w)
        r = self._publish(w, "1.4.0")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("not-promoted", r.stderr)

        # host-mode
        w = self._build_publisher("1.5.0")
        conf = w / ".claude" / "pipeline.conf"
        conf.write_text("package_source=/anywhere\n", encoding="utf-8")
        _git(["add", "-A"], w)
        _git(["commit", "-q", "-m", "chore(test): become host"], w)
        # publish resolves package_source from the host repo, refusing before publish HEAD promotion checks
        r = self._publish(w, "1.5.0")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("host-mode", r.stderr)


class TestInstallCollision(unittest.TestCase):
    def test_pk_install_collision(self):
        tmp = tempfile.TemporaryDirectory(prefix="pk1603-collide-")
        self.addCleanup(tmp.cleanup)
        h = Path(tmp.name)
        _init_identity(h)
        (h / ".claude" / "skills" / "grill-me").mkdir(parents=True)
        (h / ".claude" / "skills" / "grill-me" / "SKILL.md").write_text(
            "hand-authored, no marker\n", encoding="utf-8"
        )
        _git(["add", "-A"], h)
        _git(["commit", "-q", "-m", "chore(test): host-owned skill"], h)

        install_sh = PACKAGE_ROOT / "install.sh"
        r = subprocess.run(
            ["bash", str(install_sh), "--source", "/nonexistent", "--tag", "v9.9.9"],
            cwd=str(h), capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("collision", r.stderr)


class TestUpgradeRefusals(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="pk1603-upg-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def _host_home_mode(self) -> Path:
        h = self.tmp / "home"
        h.mkdir()
        _init_identity(h)
        _copy_package_tree(h / ".claude" / "pipeline")
        _git(["add", "-A"], h)
        _git(["commit", "-q", "-m", "chore(test): home repo"], h)
        return h

    def test_pk_upgrade_refusals_home_mode(self):
        h = self._host_home_mode()
        r = _run_package(["upgrade", "v9.9.9"], h)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("home-mode", r.stderr)

    def _host_installed(self) -> Path:
        """A host with package_source set (host mode) but no real subtree
        history — enough to exercise dirty-tree / role-branch / not-pristine."""
        h = self.tmp / "host"
        h.mkdir()
        _init_identity(h)
        _copy_package_tree(h / ".claude" / "pipeline")
        (h / ".claude" / "pipeline.conf").parent.mkdir(parents=True, exist_ok=True)
        (h / ".claude" / "pipeline.conf").write_text("package_source=/nowhere\n", encoding="utf-8")
        _git(["checkout", "-q", "-b", "work"], h, check=False)
        _git(["add", "-A"], h)
        _git(["commit", "-q", "-m", "chore(host): install"], h)
        return h

    def test_pk_upgrade_refusals_dirty_tree(self):
        h = self._host_installed()
        (h / "dirty.txt").write_text("x", encoding="utf-8")
        r = _run_package(["upgrade", "v9.9.9"], h)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("dirty-tree", r.stderr)

    def test_pk_upgrade_refusals_role_branch(self):
        h = self._host_installed()
        _git(["checkout", "-q", "-b", "main"], h)
        r = _run_package(["upgrade", "v9.9.9"], h)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("role-branch", r.stderr)

    def test_pk_upgrade_refusals_not_pristine(self):
        h = self._host_installed()
        r = _run_package(["upgrade", "v9.9.9"], h)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("not-pristine", r.stderr)


class TestPristineCatchesSquashMerge(unittest.TestCase):
    def test_pk_pristine_catches_squash_merge(self):
        tmp = tempfile.TemporaryDirectory(prefix="pk1603-pristine-")
        self.addCleanup(tmp.cleanup)
        h = Path(tmp.name)
        _init_identity(h)
        (h / "README.txt").write_text("host\n", encoding="utf-8")
        _git(["add", "-A"], h)
        _git(["commit", "-q", "-m", "chore(host): init"], h)

        w = tmp2 = tempfile.TemporaryDirectory(prefix="pk1603-pristine-w-")
        self.addCleanup(tmp2.cleanup)
        w_path = Path(tmp2.name)
        _init_identity(w_path)
        _copy_package_tree(w_path / ".claude" / "pipeline")
        _git(["add", "-A"], w_path)
        _git(["commit", "-q", "-m", "chore(test): seed"], w_path)
        # `subtree add`'s source ref must itself be package-rooted (a real
        # split), never a repo whose HEAD still nests .claude/pipeline/ —
        # else the add double-nests it (.claude/pipeline/.claude/pipeline/).
        split_sha = _git(["subtree", "split", "--prefix=.claude/pipeline"], w_path).stdout.strip()

        r = _git(["subtree", "add", "--prefix=.claude/pipeline", str(w_path), split_sha, "--squash",
                   "-m", "chore(pipeline): install"], h, check=False)
        self.assertEqual(r.returncode, 0, r.stderr)

        # Force host mode so the pristine arm actually runs (a real `install`
        # would have written this; this test bypasses install.sh on purpose
        # to isolate the pristine predicate).
        (h / ".claude" / "pipeline.conf").write_text(
            f"package_source={w_path}\n", encoding="utf-8"
        )
        _git(["add", "-A"], h)
        _git(["commit", "-q", "-m", "chore(test): become host"], h)

        # Simulate a GitHub squash-merge of an "upgrade": rewrite the tree to
        # a whole-repository shape on a fresh commit whose message keeps the
        # subtree trailers (title-only squash-merge variant, ADR-0092
        # Context) — a commit with the trailers but the wrong tree/parent
        # shape must fail the pristine arm's shape() check.
        squash_sha = _git(["rev-parse", "HEAD"], h).stdout.strip()
        (h / "smuggled.txt").write_text("edited package by hand\n", encoding="utf-8")
        _git(["add", "-A"], h)
        message = (
            "chore(pipeline): upgrade to v9.9.9\n\n"
            f"git-subtree-dir: .claude/pipeline\n"
            f"git-subtree-split: {'0' * 40}\n"
        )
        _git(["commit", "-q", "-m", message], h)

        r = _run_package(["check"], h)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("pristine", r.stdout)


class TestShimTransformsClosed(unittest.TestCase):
    def test_pk_shim_transforms_closed(self):
        sys.path.insert(0, str(PACKAGE_ROOT / "tools"))
        import importlib
        if "package" in sys.modules:
            del sys.modules["package"]
        pkg = importlib.import_module("package")
        source_path = PACKAGE_ROOT / "skills" / "grill-me" / "SKILL.md"
        shim_path = REPO_ROOT / ".claude" / "skills" / "grill-me" / "SKILL.md"
        expected = pkg._render_shim(
            source_path.read_text(encoding="utf-8"), "skills/grill-me/SKILL.md",
            source_path, shim_path,
        )
        actual = shim_path.read_text(encoding="utf-8")
        self.assertEqual(
            actual.replace("\r\n", "\n"), expected.replace("\r\n", "\n"),
            "the tracked shim must equal its source plus exactly the two D2 transforms",
        )


class TestStubContract(unittest.TestCase):
    """Stub ACs: no key parsing, no fallback literal, forwards the package
    module's own objects, byte-identical CLI output, and every consumer
    the sweep says must keep working still works."""

    def test_no_branch_literals_in_stub(self):
        text = ROOT_STUB_PY.read_text(encoding="utf-8")
        import re
        self.assertEqual(
            len(re.findall(r"integration_branch=|\bdevelop\b|\bmain\b", text)), 0
        )

    def test_no_key_parsing_in_stub(self):
        text = ROOT_STUB_PY.read_text(encoding="utf-8")
        import re
        self.assertEqual(
            len(re.findall(r"pipeline\.conf|split\(|partition\(", text)), 0
        )

    def test_pk_stub_forwards(self):
        r1 = subprocess.run(
            [sys.executable, str(ROOT_STUB_PY), "integration"],
            cwd=str(REPO_ROOT), capture_output=True, text=True, encoding="utf-8",
        )
        r2 = subprocess.run(
            [sys.executable, str(PACKAGE_PY.parent / "pipeline_config.py"), "integration"],
            cwd=str(REPO_ROOT), capture_output=True, text=True, encoding="utf-8",
        )
        self.assertEqual(r1.stdout, r2.stdout)
        self.assertEqual(r1.returncode, r2.returncode)

        import importlib.util
        spec = importlib.util.spec_from_file_location("pipeline_config_test_stub_1603", ROOT_STUB_PY)
        stub = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(stub)
        pkg_mod = sys.modules["pipeline_config_package_1603"]
        self.assertIs(stub.integration_branch, pkg_mod.integration_branch)
        self.assertIs(stub.PipelineConfigError, pkg_mod.PipelineConfigError)

    def test_pk_stub_keeps_main_deny(self):
        import json
        payload = {"tool_name": "Bash", "tool_input": {"command": "git" + " push origin HEAD:main"}}
        r = subprocess.run(
            [sys.executable, str(REPO_ROOT / ".claude" / "hooks" / "pre-tool-bash-classify.py")],
            input=json.dumps(payload), cwd=str(REPO_ROOT), capture_output=True, text=True,
            encoding="utf-8",
        )
        self.assertIn('"permissionDecision":"deny"', r.stdout.replace(" ", ""))

    def test_pk_stub_keeps_precommit_release_guard(self):
        tmp = tempfile.TemporaryDirectory(prefix="pk1603-precommit-")
        self.addCleanup(tmp.cleanup)
        h = Path(tmp.name)
        _init_identity(h)
        _git(["checkout", "-q", "-b", "main"], h)
        (h / "f.txt").write_text("x", encoding="utf-8")
        _git(["add", "-A"], h)
        precommit = REPO_ROOT / ".githooks" / "pre-commit"
        r = subprocess.run(
            ["bash", str(precommit)], cwd=str(h), capture_output=True, text=True, encoding="utf-8",
        )
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("direct commits to 'main'", r.stderr)

    def test_pk_stub_keeps_session_start_resolver(self):
        r = subprocess.run(
            ["bash", str(REPO_ROOT / ".claude" / "hooks" / "session-start.sh")],
            cwd=str(REPO_ROOT), capture_output=True, text=True, encoding="utf-8",
        )
        self.assertNotIn("resolver failed", r.stdout + r.stderr)


class TestConsumerSweep(unittest.TestCase):
    def test_slicing_rule_names_package_skills(self):
        text = (REPO_ROOT / ".claude" / "rules" / "slicing.md").read_text(encoding="utf-8")
        self.assertGreaterEqual(text.count(".claude/pipeline/skills/*/SKILL.md"), 1)

    def test_grill_skill_retargeted_in_ci_checks(self):
        text = (REPO_ROOT / "tools" / "ci-checks.sh").read_text(encoding="utf-8")
        self.assertEqual(
            text.count('GRILL_SKILL=".claude/pipeline/skills/grill-me/SKILL.md"'), 1
        )

    def test_check29_canary_package_py(self):
        self._assert_canary_fails("package.py")

    def test_check29_canary_install_sh(self):
        self._assert_canary_fails("install.sh")

    def test_check29_canary_root_stub(self):
        text = ROOT_STUB_PY.read_text(encoding="utf-8")
        marker = '\nCANARY = "origin/main"\n'
        ROOT_STUB_PY.write_text(text + marker, encoding="utf-8")
        try:
            r = subprocess.run(
                [sys.executable, str(REPO_ROOT / "tools" / "check-repo-identity-literals.py")],
                cwd=str(REPO_ROOT), capture_output=True, text=True, encoding="utf-8",
            )
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("pipeline_config.py", r.stdout + r.stderr)
        finally:
            ROOT_STUB_PY.write_text(text, encoding="utf-8")

    def _assert_canary_fails(self, relname):
        target = PACKAGE_ROOT / ("tools/" + relname if relname != "install.sh" else "install.sh")
        text = target.read_text(encoding="utf-8")
        marker = '\nCANARY = "origin/main"\n'
        target.write_text(text + marker, encoding="utf-8")
        try:
            r = subprocess.run(
                [sys.executable, str(REPO_ROOT / "tools" / "check-repo-identity-literals.py")],
                cwd=str(REPO_ROOT), capture_output=True, text=True, encoding="utf-8",
            )
            self.assertNotEqual(r.returncode, 0)
            self.assertIn(relname, r.stdout + r.stderr)
        finally:
            target.write_text(text, encoding="utf-8")


class TestEndToEndPublishInstallUpgrade(PackageSandboxMixin, unittest.TestCase):
    """The walking-skeleton test: P1 (publish), P2' = P2 steps 1/2/4 without
    bootstrap (bash .claude/pipeline/bootstrap.sh does not exist until
    slice 3), then an upgrade rehearsal via `package.py upgrade` directly
    (the `pipeline-upgrade` PR/merge-commit mode is W5, not this slice)."""

    def test_publish_install_upgrade_walking_skeleton(self):
        w = self._build_publisher("2.0.0")
        r = self._publish(w, "2.0.0")
        self.assertEqual(r.returncode, 0, r.stderr)
        u_path = str(self.u_bare)

        h = self.tmp / "H"
        h.mkdir()
        _init_identity(h)
        (h / "CLAUDE.md").write_text("# Host rules\nhost rule one\nhost rule two\n", encoding="utf-8")
        _git(["add", "-A"], h)
        _git(["commit", "-q", "-m", "chore(host): init"], h)

        install_sh = PACKAGE_ROOT / "install.sh"
        r = subprocess.run(
            ["bash", str(install_sh), "--source", u_path, "--tag", "v2.0.0"],
            cwd=str(h), capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

        # criterion 28-shape: H's package tree == v2.0.0's tree.
        u_tree = _git(["rev-parse", "v2.0.0^{tree}"], self.u_bare).stdout.strip()
        h_tree = _git(["rev-parse", "HEAD:.claude/pipeline"], h).stdout.strip()
        self.assertEqual(u_tree, h_tree)

        # criterion 29-shape: CLAUDE.md gained exactly the import line.
        claude_md = (h / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertEqual(
            claude_md,
            "# Host rules\nhost rule one\nhost rule two\n@.claude/pipeline/CLAUDE.md\n",
        )

        # criterion 30: pipeline.conf carries package_source.
        conf = (h / ".claude" / "pipeline.conf").read_text(encoding="utf-8")
        self.assertIn(f"package_source={u_path}", conf)

        # criterion 31/32: mode() is host in H, home in this checkout's package.
        r = _run_package(["check"], h)  # sanity: check runs at all in host mode
        mode_r = subprocess.run(
            [sys.executable, str(h / ".claude" / "pipeline" / "tools" / "pipeline_config.py"), "mode"],
            cwd=str(h), capture_output=True, text=True, encoding="utf-8",
        )
        self.assertEqual(mode_r.stdout.strip(), "host")

        # Publish v2.0.1 and rehearse an upgrade (P4-equivalent, sans the PR).
        (w / ".claude" / "pipeline" / "skills" / "grill-me" / "SKILL.md").write_text(
            (w / ".claude" / "pipeline" / "skills" / "grill-me" / "SKILL.md").read_text(encoding="utf-8")
            + "\ngolden-marker-v1\n",
            encoding="utf-8",
        )
        (w / ".claude" / "pipeline" / "VERSION").write_text("2.0.1\n", encoding="utf-8")
        r = subprocess.run(
            [sys.executable, str(w / ".claude" / "pipeline" / "tools" / "package.py"), "refresh"],
            cwd=str(w), capture_output=True, text=True, encoding="utf-8",
        )
        _git(["add", "-A"], w)
        _git(["commit", "-q", "-m", "chore(test): v2.0.1"], w)
        _git(["push", "-q", "origin", "HEAD:main"], w)
        _git(["-C", str(self.u_bare), "update-ref", "refs/heads/main",
              _git(["rev-parse", "HEAD"], w).stdout.strip()], self.tmp)
        r = self._publish(w, "2.0.1")
        self.assertEqual(r.returncode, 0, r.stderr)

        _git(["checkout", "-q", "-b", "work"], h)
        r = _run_package(["upgrade", "v2.0.1"], h)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

        # criterion 34-shape: tree matches v2.0.1.
        u_tree_v1 = _git(["rev-parse", "v2.0.1^{tree}"], self.u_bare).stdout.strip()
        h_tree_v1 = _git(["rev-parse", "HEAD:.claude/pipeline"], h).stdout.strip()
        self.assertEqual(u_tree_v1, h_tree_v1)

        # criterion 35: CLAUDE.md unchanged across the upgrade.
        self.assertEqual(
            (h / "CLAUDE.md").read_text(encoding="utf-8"), claude_md,
        )

        # criterion 36: the grill-me shim carries v1's change.
        shim = (h / ".claude" / "skills" / "grill-me" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("golden-marker-v1", shim)

        # criterion 37: pkg check exits 0 in H after the upgrade.
        r = _run_package(["check"], h)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
