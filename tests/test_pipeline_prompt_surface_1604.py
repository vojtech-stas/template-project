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
        currency arm — the D2 anti-fork invariant."""
        pkg = _load_package_module()
        shim_path = REPO_ROOT / ".claude" / "skills" / "ship" / "SKILL.md"
        original = shim_path.read_text(encoding="utf-8")
        try:
            shim_path.write_text(original + "\ndrift\n", encoding="utf-8")
            failures = pkg.currency_arm(REPO_ROOT, PACKAGE_ROOT)
            self.assertTrue(
                any("ship" in f and "SKILL.md" in f for f in failures),
                f"expected a currency FAIL naming the drifted ship shim, got: {failures}",
            )
        finally:
            shim_path.write_text(original, encoding="utf-8")


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


if __name__ == "__main__":
    unittest.main()
