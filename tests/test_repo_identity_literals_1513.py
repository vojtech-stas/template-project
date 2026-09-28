"""
tests/test_repo_identity_literals_1513.py

Regression tests for PRD #1500 slice #1513 — CI CHECK 29
(REPO-IDENTITY-LITERALS) arm (a) (tools/check-repo-identity-literals.py),
ADR-0089 D1/D3.

Covers:
  1. is_subject_file()/is_excluded() classify the ADR-0089 D1 subject set
     correctly (agents/*.md in, skills/*/SKILL.md in, tools/*.md and
     dashboard/*.md out, the resolver itself out).
  2. find_violations_in_text() flags every token shape D1 names (origin/b,
     origin b, refs/heads/b, branches/b, --base b, quoted "b"/'b', :b
     refspec, checkout|rev-parse|reset --hard b) and does NOT flag a
     comment-only line in a non-.md file, while a `.md` file gets no such
     exemption (S3-rescan / D1 subject-set contract).
  3. S3-firing: scan_repo() over a throwaway git repo with a planted
     literal produces a FAIL naming the file (main()'s FAIL-line shape).
  4. S3-self: this check's own source produces zero violations against the
     pattern it builds from the resolver's own defaults.

All tests are offline, deterministic, and network-free — the throwaway
repo fixture is a `tempfile.mkdtemp()` + `git init`, never the live
worktree/branch set (isolation discipline).

Runner: pytest compatible.
  python -m pytest tests/test_repo_identity_literals_1513.py -v
"""

import importlib.util as _ilu
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TOOLS_DIR = _REPO_ROOT / "tools"
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

_spec = _ilu.spec_from_file_location(
    "check_repo_identity_literals",
    str(_TOOLS_DIR / "check-repo-identity-literals.py"),
)
_mod = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

is_subject_file = _mod.is_subject_file
is_excluded = _mod.is_excluded
build_pattern = _mod.build_pattern
find_violations_in_text = _mod.find_violations_in_text
configured_defaults = _mod.configured_defaults
scan_repo = _mod.scan_repo
main = _mod.main


class TestSubjectSetClassification(unittest.TestCase):
    def test_agent_prompt_is_subject(self):
        self.assertTrue(is_subject_file(".claude/agents/reviewer.md"))

    def test_skill_prompt_is_subject(self):
        self.assertTrue(is_subject_file(".claude/skills/ship/SKILL.md"))

    def test_tools_py_is_subject(self):
        self.assertTrue(is_subject_file("tools/ci-checks.sh"))

    def test_dashboard_py_is_subject(self):
        self.assertTrue(is_subject_file("dashboard/health.py"))

    def test_hooks_is_subject(self):
        self.assertTrue(is_subject_file(".claude/hooks/session-start.sh"))

    def test_githooks_is_subject(self):
        self.assertTrue(is_subject_file(".githooks/pre-commit"))

    def test_bootstrap_is_subject(self):
        self.assertTrue(is_subject_file("bootstrap.sh"))

    def test_non_skill_md_not_subject(self):
        self.assertFalse(is_subject_file(".claude/skills/ship/notes.md"))

    def test_unrelated_path_not_subject(self):
        self.assertFalse(is_subject_file("decisions/0089-per-repo-pipeline-identity.md"))

    def test_tools_md_excluded(self):
        self.assertTrue(is_excluded("tools/README.md"))

    def test_dashboard_md_excluded(self):
        self.assertTrue(is_excluded("dashboard/README.md"))

    def test_gen_rules_excluded(self):
        self.assertTrue(is_excluded("tools/gen_rules.py"))

    def test_resolver_itself_excluded(self):
        self.assertTrue(is_excluded("tools/pipeline_config.py"))

    def test_agent_md_not_excluded(self):
        self.assertFalse(is_excluded(".claude/agents/reviewer.md"))


class TestTokenShapes(unittest.TestCase):
    def setUp(self):
        self.pattern = build_pattern(["develop", "main"])

    def _hits(self, text, path="tools/example.sh"):
        return find_violations_in_text(path, text, self.pattern)

    def test_origin_slash(self):
        self.assertTrue(self._hits("git fetch origin/develop"))

    def test_origin_space(self):
        self.assertTrue(self._hits("git push origin main"))

    def test_refs_heads(self):
        self.assertTrue(self._hits("refs/heads/develop"))

    def test_branches_path(self):
        self.assertTrue(self._hits('curl "branches/main/protection"'))

    def test_dash_dash_base(self):
        self.assertTrue(self._hits("gh pr create --base develop"))

    def test_double_quoted(self):
        self.assertTrue(self._hits('x = "main"'))

    def test_single_quoted(self):
        self.assertTrue(self._hits("x = 'develop'"))

    def test_refspec_destination(self):
        # `(?<![\w/])` requires the char before ':' be neither a word char
        # nor '/' — a delete-refspec form (`git push origin :main`) matches;
        # a `HEAD:main` form does not, by the shared SCAN29 definition.
        self.assertTrue(self._hits("git push origin :main"))

    def test_checkout(self):
        self.assertTrue(self._hits("git checkout develop"))

    def test_reset_hard(self):
        self.assertTrue(self._hits("git reset --hard main"))

    def test_no_hit_on_unrelated_text(self):
        self.assertFalse(self._hits("this file has no branch literal at all"))

    def test_comment_only_line_exempt_in_code_file(self):
        self.assertFalse(self._hits("# see origin/main for details", path="tools/example.sh"))

    def test_comment_only_line_not_exempt_in_md(self):
        self.assertTrue(self._hits("# see origin/main for details", path=".claude/agents/foo.md"))

    def test_non_comment_line_in_code_file_still_flagged(self):
        self.assertTrue(self._hits("git fetch origin/main  # not a comment-only line"))


class TestScanRepoIntegration(unittest.TestCase):
    """S3-firing: a planted literal in a throwaway git repo produces a FAIL
    naming the file. Never run against the live worktree/branch set."""

    def test_planted_literal_is_caught(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init", "-q", tmp], check=True)
            agents_dir = Path(tmp) / ".claude" / "agents"
            agents_dir.mkdir(parents=True)
            planted = agents_dir / "planted.md"
            planted.write_text("Verify ADR existence on origin/main.\n", encoding="utf-8")
            subprocess.run(["git", "-C", tmp, "add", "-A"], check=True)
            subprocess.run(
                ["git", "-C", tmp, "-c", "user.email=t@example.com",
                 "-c", "user.name=t", "commit", "-q", "-m", "planted"],
                check=True,
            )
            pattern = build_pattern(configured_defaults())
            violations = scan_repo(tmp, pattern)
            self.assertIsNotNone(violations)
            paths = [v[0].replace("\\", "/") for v in violations]
            self.assertIn(".claude/agents/planted.md", paths)

    def test_clean_repo_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init", "-q", tmp], check=True)
            agents_dir = Path(tmp) / ".claude" / "agents"
            agents_dir.mkdir(parents=True)
            clean = agents_dir / "clean.md"
            clean.write_text("Verify ADR existence on the integration branch.\n", encoding="utf-8")
            subprocess.run(["git", "-C", tmp, "add", "-A"], check=True)
            subprocess.run(
                ["git", "-C", tmp, "-c", "user.email=t@example.com",
                 "-c", "user.name=t", "commit", "-q", "-m", "clean"],
                check=True,
            )
            pattern = build_pattern(configured_defaults())
            violations = scan_repo(tmp, pattern)
            self.assertEqual(violations, [])

    def test_main_exit_code_reflects_violations(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init", "-q", tmp], check=True)
            agents_dir = Path(tmp) / ".claude" / "agents"
            agents_dir.mkdir(parents=True)
            (agents_dir / "planted.md").write_text(
                "checkout origin/develop before editing.\n", encoding="utf-8"
            )
            subprocess.run(["git", "-C", tmp, "add", "-A"], check=True)
            subprocess.run(
                ["git", "-C", tmp, "-c", "user.email=t@example.com",
                 "-c", "user.name=t", "commit", "-q", "-m", "planted"],
                check=True,
            )
            self.assertEqual(main(["--root", tmp]), 1)


class TestSelfCheck(unittest.TestCase):
    """S3-self: CHECK 29's own source has zero hits, and its token table
    never spells a flagged shape (branch names come from the resolver's
    own defaults, never a literal in this file)."""

    def test_own_source_has_no_violations(self):
        own_path = _TOOLS_DIR / "check-repo-identity-literals.py"
        text = own_path.read_text(encoding="utf-8")
        pattern = build_pattern(configured_defaults())
        hits = find_violations_in_text("tools/check-repo-identity-literals.py", text, pattern)
        self.assertEqual(hits, [])

    def test_configured_defaults_are_develop_and_main_on_this_repo(self):
        self.assertEqual(configured_defaults(), sorted(["develop", "main"]))


if __name__ == "__main__":
    unittest.main()
