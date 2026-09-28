"""
Regression test for issue #999 — discover_* functions must enumerate via the
committed git tree, not arbitrary on-disk paths.

Root cause: discover_skills() / discover_agents() / discover_adrs() (in
dashboard/discovery.py) enumerate via Path.glob / iterdir against the
filesystem. When an agent worktree carries untracked stale dirs (e.g. leftover
.claude/skills/audit-meta after its git-tracked deletion), the generator counts
them → produces a README that diverges from the committed tree → false
R-DOCS-CURRENT.  This is the sibling bug of #926 which fixed health.py but
did NOT cover discovery.py.

Fix: route enumeration through git ls-files so untracked paths are invisible.

Runner: stdlib unittest; NO top-level pytest import.
  python -m pytest tests/test_discover_git_tree_999.py -v
  python -m unittest tests.test_discover_git_tree_999 -v
"""

import os
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DISCOVERY_PY = REPO_ROOT / "dashboard" / "discovery.py"


class TestDiscoverGitTree999(unittest.TestCase):
    """discover_skills() must enumerate only git-tracked paths.

    Creates an untracked decoy .claude/skills/zzdecoy999/SKILL.md in the
    REAL repo root, calls discover_skills(), and asserts:
      1. The decoy skill name/path is NOT present in the result.
      2. The count matches the number of SKILL.md paths returned by
         `git ls-files .claude/skills/*/SKILL.md`.

    Before fix: discover_skills() uses Path.glob which picks up the untracked
    decoy → count is off by 1 and the decoy appears in results → FAIL.
    After fix: uses git ls-files → decoy is invisible → PASS.
    """

    _decoy_dir: Path
    _decoy_file: Path

    def setUp(self):
        """Create an untracked decoy skill dir in the real repo root."""
        self._decoy_dir = REPO_ROOT / ".claude" / "pipeline" / "skills" / "zzdecoy999"
        self._decoy_file = self._decoy_dir / "SKILL.md"
        self._decoy_dir.mkdir(parents=True, exist_ok=True)
        self._decoy_file.write_text(
            "---\nname: zzdecoy999\ndescription: untracked decoy for #999 regression\n---\n"
            "# Decoy Skill — DO NOT COMMIT\n"
            "This file is an untracked regression-test fixture created by "
            "test_discover_git_tree_999.py. It must not be counted by discover_skills().\n",
            encoding="utf-8",
        )
        # Confirm the decoy is NOT tracked (sanity guard)
        result = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "ls-files",
             ".claude/pipeline/skills/zzdecoy999/SKILL.md"],
            capture_output=True, text=True,
        )
        self.assertEqual(
            result.stdout.strip(), "",
            msg="setUp: decoy should NOT be git-tracked, but git ls-files returned output. "
                "Remove it from the index before running this test.",
        )

    def tearDown(self):
        """Remove the decoy dir so it leaves no trace in the worktree."""
        try:
            self._decoy_file.unlink(missing_ok=True)
            self._decoy_dir.rmdir()
        except Exception:
            pass

    def _tracked_skill_count(self) -> int:
        """Ask git how many SKILL.md files are tracked (the ground truth)."""
        result = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "ls-files", ".claude/pipeline/skills/*/SKILL.md"],
            capture_output=True, text=True, timeout=10,
        )
        lines = [l.strip() for l in result.stdout.splitlines() if l.strip()]
        return len(lines)

    def _run_discover_skills(self) -> list:
        """Import discover_skills from discovery.py in a subprocess to get a fresh result."""
        script = (
            "import sys\n"
            f"sys.path.insert(0, r'{REPO_ROOT / 'dashboard'}')\n"
            "from discovery import discover_skills\n"
            "import json\n"
            "print(json.dumps(discover_skills()))\n"
        )
        proc = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True, text=True,
            cwd=str(REPO_ROOT / "dashboard"),
            timeout=30,
        )
        if proc.returncode != 0:
            self.fail(
                f"discover_skills subprocess failed (exit={proc.returncode}):\n"
                f"stderr: {proc.stderr[:500]}"
            )
        import json
        return json.loads(proc.stdout.strip())

    def test_discover_skills_excludes_untracked_decoy(self):
        """discover_skills() must NOT include the untracked zzdecoy999 dir.

        Before fix (fs-glob): the decoy appears in results — FAIL.
        After fix (git ls-files): the decoy is invisible — PASS.
        """
        skills = self._run_discover_skills()
        skill_names = [s.get("name", "") for s in skills]
        skill_paths = [s.get("path", "") for s in skills]

        # Assert decoy is absent
        self.assertNotIn(
            "zzdecoy999",
            skill_names,
            msg=(
                "discover_skills() returned 'zzdecoy999' which is an UNTRACKED decoy. "
                "The function must enumerate only git-tracked paths via git ls-files, "
                "not arbitrary filesystem dirs. Skill names found: "
                + str(skill_names)
            ),
        )
        for path in skill_paths:
            self.assertNotIn(
                "zzdecoy999",
                path,
                msg=(
                    f"discover_skills() path '{path}' references the untracked decoy. "
                    "Must enumerate via git ls-files only."
                ),
            )

        # Assert count matches git ls-files
        expected_count = self._tracked_skill_count()
        actual_count = len(skills)
        self.assertEqual(
            expected_count,
            actual_count,
            msg=(
                f"discover_skills() returned {actual_count} skills but git ls-files "
                f"reports {expected_count} tracked SKILL.md files. "
                f"The decoy inflated the count by {actual_count - expected_count}. "
                f"Skill names found: {skill_names}"
            ),
        )


if __name__ == "__main__":
    unittest.main()
