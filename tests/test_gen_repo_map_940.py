"""
Regression tests for PRD #937 slice #940 — gen_repo_map.py + @import repo-map.

Verifies:
1. gen_repo_map.generate() produces _repo-map.md with ≥10 table rows.
2. Adding a fixture skill + regenerating makes its row appear in the output.
3. --check mode detects stale/missing _repo-map.md.
4. --check mode exits 0 on a clean tree.
5. CLAUDE.md @imports _repo-map.md.
6. Real .claude/generated/_repo-map.md exists and has ≥10 rows (relocated
   out of .claude/rules/ in slice #945 to avoid double-load).

All fixture-based tests use a temp directory and monkeypatching.
Tests are offline, deterministic, and network-free.

Runner: stdlib unittest + pytest compatible.
  python -m pytest tests/test_gen_repo_map_940.py -v
"""

import sys
import unittest
import tempfile
import os
from pathlib import Path
from unittest.mock import patch

# ---------------------------------------------------------------------------
# Ensure tools/ and dashboard/ are importable
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent
_TOOLS_DIR = _REPO_ROOT / "tools"
_DASHBOARD_DIR = _REPO_ROOT / "dashboard"
for d in (_TOOLS_DIR, _DASHBOARD_DIR):
    if str(d) not in sys.path:
        sys.path.insert(0, str(d))

import gen_repo_map  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture skill data (minimal structure matching discover_skills() output)
# ---------------------------------------------------------------------------

_FIXTURE_SKILLS = [
    {
        "name": "ship",
        "path": ".claude/skills/ship/SKILL.md",
        "description": "Autonomous PRD-to-merge pipeline orchestrator.",
    },
    {
        "name": "to-prd",
        "path": ".claude/skills/to-prd/SKILL.md",
        "description": "Turn grilled context into a PRD issue.",
    },
    {
        "name": "grill-me",
        "path": ".claude/skills/grill-me/SKILL.md",
        "description": "Interview the user about a plan.",
    },
]

_FIXTURE_AGENTS = [
    {
        "name": "reviewer",
        "stem": "reviewer",
        "type": "critic",
        "path": ".claude/agents/reviewer.md",
        "description": "Audit a PR for quality.",
    },
    {
        "name": "implementer",
        "stem": "implementer",
        "type": "generator",
        "path": ".claude/agents/implementer.md",
        "description": "Implement a slice issue end-to-end.",
    },
]


def _run_with_fixture(skills, agents, check_mode=False):
    """
    Run gen_repo_map.generate() with mocked discover_skills/discover_agents.

    Returns (exit_code, content_str_or_None).
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        rules_dir = Path(tmpdir) / ".claude" / "rules"
        rules_dir.mkdir(parents=True, exist_ok=True)
        repo_map_file = rules_dir / "_repo-map.md"

        # If check mode, pre-generate first so we have a clean file to check against
        if check_mode:
            with (
                patch.object(gen_repo_map, "REPO_MAP_FILE", repo_map_file),
                patch("gen_repo_map.discover_skills", return_value=skills),
                patch("gen_repo_map.discover_agents", return_value=agents),
            ):
                gen_repo_map.generate(check_mode=False)
            with (
                patch.object(gen_repo_map, "REPO_MAP_FILE", repo_map_file),
                patch("gen_repo_map.discover_skills", return_value=skills),
                patch("gen_repo_map.discover_agents", return_value=agents),
            ):
                exit_code = gen_repo_map.generate(check_mode=True)
            content = repo_map_file.read_text(encoding="utf-8") if repo_map_file.exists() else None
        else:
            with (
                patch.object(gen_repo_map, "REPO_MAP_FILE", repo_map_file),
                patch("gen_repo_map.discover_skills", return_value=skills),
                patch("gen_repo_map.discover_agents", return_value=agents),
            ):
                exit_code = gen_repo_map.generate(check_mode=False)
            content = repo_map_file.read_text(encoding="utf-8") if repo_map_file.exists() else None

    return exit_code, content


# ---------------------------------------------------------------------------
# Tests: basic generation
# ---------------------------------------------------------------------------

class TestGenRepoMapBasic(unittest.TestCase):
    """gen_repo_map.generate() produces a valid _repo-map.md."""

    def test_generate_exits_zero(self):
        """generate() returns 0 on success."""
        exit_code, _ = _run_with_fixture(_FIXTURE_SKILLS, _FIXTURE_AGENTS)
        self.assertEqual(exit_code, 0, "generate() must return 0 on success")

    def test_output_file_created(self):
        """generate() creates _repo-map.md."""
        exit_code, content = _run_with_fixture(_FIXTURE_SKILLS, _FIXTURE_AGENTS)
        self.assertIsNotNone(content, "_repo-map.md must be created")

    def test_output_has_generated_header(self):
        """_repo-map.md must contain the DO-NOT-EDIT generated header."""
        _, content = _run_with_fixture(_FIXTURE_SKILLS, _FIXTURE_AGENTS)
        self.assertIsNotNone(content)
        self.assertIn(
            "GENERATED by tools/gen_repo_map.py",
            content,
            "_repo-map.md must contain the generated header",
        )

    def test_output_has_marker_begin_end(self):
        """_repo-map.md must contain the BEGIN/END GENERATED:repo-map markers."""
        _, content = _run_with_fixture(_FIXTURE_SKILLS, _FIXTURE_AGENTS)
        self.assertIn("BEGIN GENERATED:repo-map", content)
        self.assertIn("END GENERATED:repo-map", content)

    def test_skills_appear_in_output(self):
        """Each fixture skill name must appear as a table row in _repo-map.md."""
        _, content = _run_with_fixture(_FIXTURE_SKILLS, _FIXTURE_AGENTS)
        self.assertIsNotNone(content)
        for skill in _FIXTURE_SKILLS:
            self.assertIn(
                skill["name"],
                content,
                f"Skill '{skill['name']}' must appear in _repo-map.md",
            )

    def test_agents_appear_in_output(self):
        """Each fixture agent name must appear as a table row in _repo-map.md."""
        _, content = _run_with_fixture(_FIXTURE_SKILLS, _FIXTURE_AGENTS)
        self.assertIsNotNone(content)
        for agent in _FIXTURE_AGENTS:
            self.assertIn(
                agent["name"],
                content,
                f"Agent '{agent['name']}' must appear in _repo-map.md",
            )


# ---------------------------------------------------------------------------
# Tests: fixture skill adds its row (AC from slice #940)
# ---------------------------------------------------------------------------

class TestFixtureSkillAddsRow(unittest.TestCase):
    """Adding a fixture skill + regenerating makes its row appear."""

    _NEW_SKILL = {
        "name": "zzztest-fixture-skill",
        "path": ".claude/skills/zzztest-fixture-skill/SKILL.md",
        "description": "Fixture skill for testing row addition.",
    }

    def test_new_skill_row_added_on_regen(self):
        """After adding a skill to the discovery list, regenerating adds its row."""
        # Generate without the new skill
        _, content_before = _run_with_fixture(_FIXTURE_SKILLS, _FIXTURE_AGENTS)
        self.assertIsNotNone(content_before)
        self.assertNotIn(
            self._NEW_SKILL["name"],
            content_before,
            "New skill must NOT appear before it is added",
        )

        # Generate with the new skill added
        skills_with_new = _FIXTURE_SKILLS + [self._NEW_SKILL]
        _, content_after = _run_with_fixture(skills_with_new, _FIXTURE_AGENTS)
        self.assertIsNotNone(content_after)
        self.assertIn(
            self._NEW_SKILL["name"],
            content_after,
            "New skill must appear in _repo-map.md after regeneration",
        )

    def test_check_detects_stale_after_new_skill(self):
        """--check returns non-zero when a new skill is added but repo-map not regenerated."""
        # Pre-generate without new skill
        with tempfile.TemporaryDirectory() as tmpdir:
            rules_dir = Path(tmpdir) / ".claude" / "rules"
            rules_dir.mkdir(parents=True, exist_ok=True)
            repo_map_file = rules_dir / "_repo-map.md"

            # Generate without the new skill
            with (
                patch.object(gen_repo_map, "REPO_MAP_FILE", repo_map_file),
                patch("gen_repo_map.discover_skills", return_value=_FIXTURE_SKILLS),
                patch("gen_repo_map.discover_agents", return_value=_FIXTURE_AGENTS),
            ):
                gen_repo_map.generate(check_mode=False)

            # Now --check with the new skill added (file is stale)
            skills_with_new = _FIXTURE_SKILLS + [self._NEW_SKILL]
            with (
                patch.object(gen_repo_map, "REPO_MAP_FILE", repo_map_file),
                patch("gen_repo_map.discover_skills", return_value=skills_with_new),
                patch("gen_repo_map.discover_agents", return_value=_FIXTURE_AGENTS),
            ):
                exit_code = gen_repo_map.generate(check_mode=True)

        self.assertEqual(
            exit_code, 1,
            "--check must return 1 when repo-map is stale after new skill added",
        )


# ---------------------------------------------------------------------------
# Tests: --check mode
# ---------------------------------------------------------------------------

class TestGenRepoMapCheckMode(unittest.TestCase):
    """--check mode detects stale/missing _repo-map.md and passes on clean tree."""

    def test_check_mode_passes_on_clean_tree(self):
        """--check returns 0 when _repo-map.md matches fresh output."""
        exit_code, _ = _run_with_fixture(
            _FIXTURE_SKILLS, _FIXTURE_AGENTS, check_mode=True
        )
        self.assertEqual(exit_code, 0, "--check must return 0 on clean tree")

    def test_check_mode_fails_on_missing_file(self):
        """--check returns 1 when _repo-map.md does not exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            rules_dir = Path(tmpdir) / ".claude" / "rules"
            rules_dir.mkdir(parents=True, exist_ok=True)
            repo_map_file = rules_dir / "_repo-map.md"
            # Do NOT create the file
            with (
                patch.object(gen_repo_map, "REPO_MAP_FILE", repo_map_file),
                patch("gen_repo_map.discover_skills", return_value=_FIXTURE_SKILLS),
                patch("gen_repo_map.discover_agents", return_value=_FIXTURE_AGENTS),
            ):
                exit_code = gen_repo_map.generate(check_mode=True)
        self.assertEqual(exit_code, 1, "--check must return 1 when file is missing")

    def test_check_mode_fails_on_stale_file(self):
        """--check returns 1 when _repo-map.md content differs from fresh output."""
        with tempfile.TemporaryDirectory() as tmpdir:
            rules_dir = Path(tmpdir) / ".claude" / "rules"
            rules_dir.mkdir(parents=True, exist_ok=True)
            repo_map_file = rules_dir / "_repo-map.md"
            # Write stale content
            repo_map_file.write_text("# stale content\n", encoding="utf-8")
            with (
                patch.object(gen_repo_map, "REPO_MAP_FILE", repo_map_file),
                patch("gen_repo_map.discover_skills", return_value=_FIXTURE_SKILLS),
                patch("gen_repo_map.discover_agents", return_value=_FIXTURE_AGENTS),
            ):
                exit_code = gen_repo_map.generate(check_mode=True)
        self.assertEqual(exit_code, 1, "--check must return 1 on stale file")


# ---------------------------------------------------------------------------
# Integration tests: real repo
# ---------------------------------------------------------------------------

class TestGenRepoMapRealRepo(unittest.TestCase):
    """Integration: real .claude/pipeline/generated/_repo-map.md and CLAUDE.md
    (relocated into the package per ADR-0092 D1, slice #1604)."""

    def test_repo_map_file_exists(self):
        """Real .claude/pipeline/generated/_repo-map.md must exist."""
        repo_map = _REPO_ROOT / ".claude" / "pipeline" / "generated" / "_repo-map.md"
        self.assertTrue(
            repo_map.exists(),
            ".claude/pipeline/generated/_repo-map.md must exist (run 'python tools/gen_repo_map.py')",
        )

    def test_repo_map_has_at_least_10_rows(self):
        """Real _repo-map.md must have ≥10 table rows (AC from slice #940)."""
        repo_map = _REPO_ROOT / ".claude" / "pipeline" / "generated" / "_repo-map.md"
        if not repo_map.exists():
            self.skipTest("_repo-map.md not found")
        content = repo_map.read_text(encoding="utf-8")
        # Count data rows: lines starting with | that are not header/separator rows
        import re
        row_count = sum(
            1 for line in content.splitlines()
            if line.strip().startswith("|")
            and "---" not in line
            and not re.match(r"\|\s*(Skill|Agent|Tool|Directory|Type|Path|Description)\s*\|",
                             line.strip())
        )
        self.assertGreaterEqual(
            row_count, 10,
            f"_repo-map.md must have ≥10 table rows, found {row_count}",
        )

    def test_claude_md_imports_repo_map(self):
        """Real pipeline CLAUDE.md must contain the @import line for
        _repo-map.md (relocated per ADR-0092 D1, slice #1604)."""
        claude_md = _REPO_ROOT / ".claude" / "pipeline" / "CLAUDE.md"
        self.assertTrue(claude_md.exists(), "pipeline CLAUDE.md must exist")
        content = claude_md.read_text(encoding="utf-8")
        self.assertIn(
            "@generated/_repo-map.md",
            content,
            "pipeline CLAUDE.md must @import generated/_repo-map.md",
        )

    def test_gen_repo_map_check_exits_zero(self):
        """gen_repo_map.py --check must exit 0 on a clean committed tree."""
        import subprocess
        result = subprocess.run(
            [sys.executable, str(_TOOLS_DIR / "gen_repo_map.py"), "--check"],
            capture_output=True,
            text=True,
            cwd=str(_REPO_ROOT),
        )
        self.assertEqual(
            result.returncode,
            0,
            f"gen_repo_map.py --check failed:\n{result.stdout}\n{result.stderr}",
        )


if __name__ == "__main__":
    unittest.main()
