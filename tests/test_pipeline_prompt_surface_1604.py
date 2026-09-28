"""
tests/test_pipeline_prompt_surface_1604.py

Regression tests for slice #1604 (PRD "Make the pipeline an installable,
upgradeable package", ADR-0092 D1-D2) — moving the remaining skills, all
agents, area rules and generated/ into the package behind shims.

Every check below reads this checkout's OWN tracked files or runs a
subprocess against a LOCAL throwaway git repository under a pytest tempdir
(never this checkout's own history, never a push). Nothing here writes to
`.claude/logs/` (rule #21 fixture discipline).

Runner: stdlib unittest + pytest compatible.
    python -m pytest tests/test_pipeline_prompt_surface_1604.py -v
"""
import importlib
import importlib.util
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


def _load_repo_identity_literals():
    spec = importlib.util.spec_from_file_location(
        "check_repo_identity_literals_1604",
        str(REPO_ROOT / "tools" / "check-repo-identity-literals.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestShimMembership(unittest.TestCase):
    """pk_shim_membership: the tracked shim set equals exactly one shim per
    package skill, agent and area rule, with no shim of generated/_global.md
    (ADR-0092 D2 — generated/ is imported, never shimmed)."""

    def test_pk_shim_membership(self):
        pkg = _load_package_module()
        expected_shims = {
            shim_path.relative_to(REPO_ROOT).as_posix()
            for _src_rel, _src_path, shim_path in pkg._shim_pairs(PACKAGE_ROOT)
        }
        # One shim per package skill.
        skill_names = {p.parent.name for p in (PACKAGE_ROOT / "skills").glob("*/SKILL.md")}
        for name in skill_names:
            self.assertIn(f".claude/skills/{name}/SKILL.md", expected_shims)
        # One shim per package agent.
        agent_names = {p.stem for p in (PACKAGE_ROOT / "agents").glob("*.md")}
        for name in agent_names:
            self.assertIn(f".claude/agents/pipeline/{name}.md", expected_shims)
        # One shim per package area rule.
        rule_names = {p.stem for p in (PACKAGE_ROOT / "rules").glob("*.md")}
        for name in rule_names:
            self.assertIn(f".claude/rules/pipeline/{name}.md", expected_shims)
        # generated/ is never shimmed.
        self.assertFalse(any("generated" in s for s in expected_shims))
        # Every expected shim is actually tracked on disk with the marker.
        for rel in expected_shims:
            path = REPO_ROOT / rel
            self.assertTrue(path.is_file(), f"missing shim: {rel}")

    def test_ship_shim_drift_fails_currency(self):
        """Editing the ship shim directly (never the source) must FAIL the
        currency arm — the D2 anti-fork invariant. Runs entirely against a
        tempdir copy of the package tree plus a real `pkg refresh` there, so
        no tracked file in this checkout is ever touched (reviewer finding
        6, PR #1627 round 1)."""
        pkg = _load_package_module()
        with tempfile.TemporaryDirectory(prefix="pk1604-currency-") as tmp:
            tmp_root = Path(tmp)
            tmp_pkg_root = tmp_root / ".claude" / "pipeline"
            _copy_package_tree(tmp_pkg_root)
            pkg.refresh(tmp_root, tmp_pkg_root)

            shim_path = tmp_root / ".claude" / "skills" / "ship" / "SKILL.md"
            self.assertTrue(shim_path.is_file(), "refresh must have written the ship shim")
            original = shim_path.read_text(encoding="utf-8")
            shim_path.write_text(original + "\ndrift\n", encoding="utf-8")

            failures = pkg.currency_arm(tmp_root, tmp_pkg_root)
            self.assertTrue(
                any("ship" in f and "SKILL.md" in f for f in failures),
                f"expected a currency FAIL naming the drifted ship shim, got: {failures}",
            )
            # Sanity: this checkout's own tracked shim is untouched.
            real_shim = REPO_ROOT / ".claude" / "skills" / "ship" / "SKILL.md"
            self.assertNotIn("\ndrift\n", real_shim.read_text(encoding="utf-8"))


class TestGuardrailMembership(unittest.TestCase):
    """pk_guardrail_membership: _is_guardrail_path() is True for every
    slice-2 member (ADR-0092 D2), a canary package critic included."""

    def test_pk_guardrail_membership(self):
        from dashboard import health

        for path in (
            ".claude/pipeline/agents/reviewer.md",
            ".claude/agents/pipeline/reviewer.md",
            ".claude/pipeline/agents/adr-critic.md",
            ".claude/pipeline/agents/prd-critic.md",
            ".claude/pipeline/agents/slicer-critic.md",
            ".claude/pipeline/agents/backlog-critic.md",
            ".claude/pipeline/agents/codebase-critic.md",
            ".claude/agents/pipeline/adr-critic.md",
            # Canary: an arbitrary package critic name, not one that exists
            # on disk — the regex, not a hardcoded list, must catch it.
            ".claude/pipeline/agents/x-critic.md",
            ".claude/agents/pipeline/x-critic.md",
            # Pre-move members stay members (a promotion diff spanning the
            # move still names them).
            ".claude/agents/reviewer.md",
            ".claude/agents/adr-critic.md",
        ):
            with self.subTest(path=path):
                self.assertTrue(health._is_guardrail_path(path), path)


class TestCheck29PackageCanary(unittest.TestCase):
    """A CHECK 29 canary on a package agent: a planted branch-name literal
    under .claude/pipeline/agents/ is caught, and the bare (pre-slice-1604)
    .claude/agents/ root is no longer scanned at all."""

    def test_planted_literal_in_package_agent_is_caught(self):
        mod = _load_repo_identity_literals()
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init", "-q", tmp], check=True)
            agents_dir = Path(tmp) / ".claude" / "pipeline" / "agents"
            agents_dir.mkdir(parents=True)
            (agents_dir / "canary-critic.md").write_text(
                "checkout origin/develop before editing.\n", encoding="utf-8"
            )
            subprocess.run(["git", "-C", tmp, "add", "-A"], check=True)
            subprocess.run(
                ["git", "-C", tmp, "-c", "user.email=t@example.com",
                 "-c", "user.name=t", "commit", "-q", "-m", "canary"],
                check=True,
            )
            pattern = mod.build_pattern(mod.configured_defaults())
            violations = mod.scan_repo(tmp, pattern)
            self.assertIsNotNone(violations)
            paths = [v[0].replace("\\", "/") for v in violations]
            self.assertIn(".claude/pipeline/agents/canary-critic.md", paths)

    def test_bare_agents_root_no_longer_scanned(self):
        mod = _load_repo_identity_literals()
        self.assertFalse(mod.is_subject_file(".claude/agents/canary-critic.md"))


class TestOpenAIInstructionsListPackageClaudeMd(unittest.TestCase):
    """openai_workflow.py instructions() lists .claude/pipeline/CLAUDE.md
    among the sources for any changed package path (ADR-0092 D1)."""

    def test_instructions_lists_package_claude_md(self):
        from tools import openai_workflow as workflow

        sources = workflow.instructions(REPO_ROOT, [".claude/pipeline/tools/trace.py"])
        self.assertIn(".claude/pipeline/CLAUDE.md", sources)


class TestSlicingScopePathsUpdated(unittest.TestCase):
    """The slicing area rule's paths: line names the package agent glob
    (gen_rules.py SCOPE_PATHS, rendered into the shim CHECK 17/20 checks)."""

    def test_slicing_shim_names_package_slicer_glob(self):
        text = (REPO_ROOT / ".claude" / "rules" / "pipeline" / "slicing.md").read_text(
            encoding="utf-8"
        )
        self.assertGreaterEqual(
            len(re.findall(re.escape(".claude/pipeline/agents/slicer*.md"), text)), 1
        )


class TestPromptPathHostMode(unittest.TestCase):
    """package.py's prompt_path_arm() runs (and correctly scans) in host
    mode too, instead of short-circuiting to an empty-subject PASS (reviewer
    finding 3, PR #1627 round 1). A not-yet-moved root (tools/) is NOT a
    moved-root hit in host mode; an actually-moved root (.claude/generated/)
    still is."""

    def _make_host_sandbox(self, tmp_root: Path) -> Path:
        subprocess.run(["git", "init", "-q", str(tmp_root)], check=True)
        pkg_root = tmp_root / ".claude" / "pipeline"
        _copy_package_tree(pkg_root)
        (tmp_root / ".claude" / "pipeline.conf").write_text(
            "package_source=/tmp/fixture-source\n", encoding="utf-8"
        )
        return pkg_root

    def test_host_mode_is_not_empty_short_circuit(self):
        pkg = _load_package_module()
        sys.path.insert(0, str(PACKAGE_ROOT / "tools"))
        import pipeline_config as pc

        with tempfile.TemporaryDirectory(prefix="pk1604-hostmode-") as tmp:
            tmp_root = Path(tmp)
            pkg_root = self._make_host_sandbox(tmp_root)
            self.assertEqual(pc.mode(str(tmp_root)), "host")

            # tools/ has not moved yet (this slice does not move it) — a bare
            # reference to it in a package source is NOT a moved-root hit.
            subject = pkg_root / "agents" / "implementer.md"
            original = subject.read_text(encoding="utf-8")
            subject.write_text(original + "\nrun `python tools/gen_rules.py`\n", encoding="utf-8")
            failures = pkg.prompt_path_arm(tmp_root, pkg_root)
            self.assertFalse(
                any("tools" in f and "implementer.md" in f for f in failures),
                f"tools/ has not moved; host mode must not flag it: {failures}",
            )
            subject.write_text(original, encoding="utf-8")

            # .claude/generated/ HAS moved (this slice's own move) — a bare
            # reference to it in a package source IS a moved-root hit, in
            # host mode exactly as in home mode.
            subject.write_text(
                original + "\nsee `.claude/generated/_global.md`\n", encoding="utf-8"
            )
            failures = pkg.prompt_path_arm(tmp_root, pkg_root)
            self.assertTrue(
                any("generated" in f and "implementer.md" in f for f in failures),
                f".claude/generated/ has moved; host mode must flag it: {failures}",
            )
            subject.write_text(original, encoding="utf-8")


def _extract_check6_script() -> str:
    """Pull CHECK 6's embedded python heredoc verbatim out of ci-checks.sh,
    the same never-hand-copy discipline as test_rloc_jq_path_scoping_1309.py."""
    text = (REPO_ROOT / "tools" / "ci-checks.sh").read_text(encoding="utf-8")
    start = text.index("--- CHECK 6: dangling ADR D-ID citations")
    end = text.index("CHECK6_EXIT=$?", start)
    section = text[start:end]
    m = re.search(r"python3 - << 'PYEOF'\n(.*?)\nPYEOF", section, re.DOTALL)
    if not m:
        raise AssertionError("CHECK 6 python heredoc not found verbatim in ci-checks.sh")
    return m.group(1)


class TestCheck6GeneratedShimSkip(unittest.TestCase):
    """CHECK 6 (dangling ADR D-ID citations) skips a generated-marker-carrying
    shim, but still flags the same dangling citation at a non-shim path
    (reviewer finding 4, PR #1627 round 1)."""

    def test_shim_skipped_nonshim_flagged(self):
        script = _extract_check6_script()
        with tempfile.TemporaryDirectory(prefix="pk1604-check6-") as tmp:
            tmp_root = Path(tmp)
            subprocess.run(["git", "init", "-q", str(tmp_root)], check=True)

            decisions_dir = tmp_root / "decisions"
            decisions_dir.mkdir()
            # A real ADR file with NO "### D1" heading, so "ADR-9999 D1" is a
            # genuinely dangling citation wherever it appears unshielded.
            (decisions_dir / "9999-fixture.md").write_text(
                "# Fixture ADR\n\n### D9 — unrelated\nbody\n", encoding="utf-8"
            )

            shim_dir = tmp_root / ".claude" / "skills" / "x"
            shim_dir.mkdir(parents=True)
            (shim_dir / "SKILL.md").write_text(
                "<!-- GENERATED by .claude/pipeline/tools/package.py refresh from "
                "skills/x/SKILL.md -->\nCites ADR-9999 D1 here.\n",
                encoding="utf-8",
            )

            nonshim_dir = tmp_root / "docs"
            nonshim_dir.mkdir()
            (nonshim_dir / "notes.md").write_text(
                "Also cites ADR-9999 D1 here, unshielded.\n", encoding="utf-8"
            )

            subprocess.run(["git", "-C", str(tmp_root), "add", "-A"], check=True)

            script_path = tmp_root / "_check6.py"
            script_path.write_text(script, encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, str(script_path)],
                cwd=str(tmp_root), capture_output=True, text=True,
            )

            self.assertNotEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
            combined = proc.stdout + proc.stderr
            self.assertIn("docs/notes.md", combined)
            self.assertNotIn(".claude/skills/x/SKILL.md", combined)


class TestDocs6GeneratedShimSkip(unittest.TestCase):
    """DOCS-6 (GLOSSARY.md refs) skips a generated-marker-carrying shim, but
    still flags the same reference at a non-shim path (reviewer finding 4,
    PR #1627 round 1)."""

    def _run_docs6_in_subprocess(self, tmp_root: Path) -> dict:
        script = (
            "import sys, json\n"
            f"sys.path.insert(0, r'{REPO_ROOT / 'dashboard'}')\n"
            "import health as h\n"
            f"h._HEALTH_REPO_ROOT = __import__('pathlib').Path(r'{tmp_root}')\n"
            "result = h.check_docs6_glossary_md_refs()\n"
            "print(json.dumps(result))\n"
        )
        proc = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True, text=True,
            cwd=str(REPO_ROOT / "dashboard"),
            timeout=30,
        )
        if proc.returncode != 0:
            raise AssertionError(
                f"DOCS-6 subprocess failed (exit={proc.returncode}): {proc.stderr[:800]}"
            )
        import json as _json
        return _json.loads(proc.stdout.strip())

    def test_shim_skipped_nonshim_flagged(self):
        with tempfile.TemporaryDirectory(prefix="pk1604-docs6-") as tmp:
            tmp_root = Path(tmp)

            shim_dir = tmp_root / ".claude" / "skills" / "y"
            shim_dir.mkdir(parents=True)
            (shim_dir / "SKILL.md").write_text(
                "<!-- GENERATED by .claude/pipeline/tools/package.py refresh from "
                "skills/y/SKILL.md -->\nSee GLOSSARY.md for terms.\n",
                encoding="utf-8",
            )

            nonshim_dir = tmp_root / "docs"
            nonshim_dir.mkdir()
            (nonshim_dir / "notes.md").write_text(
                "See GLOSSARY.md for terms, unshielded.\n", encoding="utf-8"
            )

            result = self._run_docs6_in_subprocess(tmp_root)
            detail = result.get("detail", "")
            self.assertIn("docs/notes.md", detail)
            self.assertNotIn(".claude/skills/y/SKILL.md", detail)
            self.assertEqual(result.get("result"), "FAIL")


if __name__ == "__main__":
    unittest.main()
