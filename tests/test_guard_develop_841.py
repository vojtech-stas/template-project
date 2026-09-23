"""
Regression tests for slice #841 — guard layer migrated to origin/develop.

Asserts that tools/worktree-guard.sh and tools/ci-checks.sh reference
origin/develop (not origin/main) as the integration branch, and that
session-start.sh reports divergence vs origin/develop, per ADR-0070 D1.

All assertions are offline (file-system content greps); no network calls;
deterministic on all platforms.

Runner: stdlib unittest + pytest compatible.
  python -m pytest tests/test_guard_develop_841.py -v
"""

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
GUARD_SH = REPO_ROOT / "tools" / "worktree-guard.sh"
CI_SH = REPO_ROOT / "tools" / "ci-checks.sh"
SESSION_START_SH = REPO_ROOT / ".claude" / "hooks" / "session-start.sh"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


class TestWorktreeGuardDevelop(unittest.TestCase):
    """worktree-guard.sh must resolve both branch roles via
    tools/pipeline_config.py (ADR-0089 D1) rather than hardcoding
    'origin/develop'/'origin/main' literals — superseding the #841-era
    assertions that pinned the integration branch to the literal 'develop'
    (ADR-0089 D1/D3, S1-c/S1-scan of slice #1511).
    """

    def setUp(self):
        self.content = _read(GUARD_SH)

    def test_no_hardcoded_branch_literal_outside_comments(self):
        """Non-comment lines must not hardcode 'develop' or 'main' as a
        git ref/branch name — every branch operation must route through the
        resolved $INTEGRATION_BRANCH / $RELEASE_BRANCH variables instead.
        Comment/docblock lines (which describe the resolved roles using this
        repo's own develop/main values as illustrative examples) are exempt.
        """
        offending = []
        for lineno, line in enumerate(self.content.splitlines(), start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if re.search(r"\b(develop|main)\b", line):
                offending.append((lineno, line))
        self.assertEqual(
            [],
            offending,
            msg=(
                "worktree-guard.sh contains hardcoded branch literal(s) on "
                "non-comment line(s) — roles must resolve via "
                "tools/pipeline_config.py (ADR-0089 D1):\n"
                + "\n".join(f"  {n}: {v}" for n, v in offending)
            ),
        )

    def test_resolves_both_roles_via_pipeline_config(self):
        """Both roles are resolved once per invocation via pipeline_config.py,
        located relative to this script's own path (BASH_SOURCE[0]) — never
        via $REPO_ROOT or `git rev-parse --show-toplevel` of the cwd repo
        (S1-c: callers locate the module relative to their own file)."""
        self.assertIn(
            '_WG_TOOLS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"',
            self.content,
            msg="tools dir must resolve relative to BASH_SOURCE[0], not cwd/$REPO_ROOT",
        )
        self.assertIn('pipeline_config.py" integration', self.content)
        self.assertIn('pipeline_config.py" release', self.content)
        self.assertIn("INTEGRATION_BRANCH=", self.content)
        self.assertIn("RELEASE_BRANCH=", self.content)

    def test_release_hard_align_uses_resolved_variable(self):
        """The release hard-align block (#950 fix) must key off the resolved
        $RELEASE_BRANCH variable, not a literal 'main' comparison."""
        self.assertIn('if [ "$EXPECTED" = "$RELEASE_BRANCH" ]', self.content)
        self.assertIn('git reset --hard "origin/$RELEASE_BRANCH"', self.content)

    def test_branch_restore_fetches_integration_branch_var(self):
        """branch-restore mode must fetch origin "$INTEGRATION_BRANCH"."""
        self.assertIn(
            'git fetch origin "$INTEGRATION_BRANCH"',
            self.content,
            msg='branch-restore must fetch origin "$INTEGRATION_BRANCH"',
        )

    def test_branch_restore_ff_check_uses_integration_branch_var(self):
        """FF-only check must compare HEAD against origin/$INTEGRATION_BRANCH."""
        self.assertRegex(
            self.content,
            r'merge-base --is-ancestor HEAD "origin/\$INTEGRATION_BRANCH"',
            msg="merge-base --is-ancestor must use origin/$INTEGRATION_BRANCH",
        )

    def test_branch_restore_checkout_targets_integration_branch_var(self):
        """ff-restore checkout must target origin/$INTEGRATION_BRANCH."""
        self.assertRegex(
            self.content,
            r'checkout -B "\$EXPECTED" "origin/\$INTEGRATION_BRANCH"',
            msg="checkout -B must target origin/$INTEGRATION_BRANCH",
        )

    def test_root_sync_uses_integration_branch_var(self):
        """root-sync must fetch/checkout/merge against $INTEGRATION_BRANCH."""
        self.assertIn('fetch origin "$INTEGRATION_BRANCH"', self.content)
        self.assertIn('checkout "$INTEGRATION_BRANCH"', self.content)
        self.assertIn('merge --ff-only "origin/$INTEGRATION_BRANCH"', self.content)

    def test_prune_zero_ahead_uses_integration_branch_var(self):
        """is_branch_zero_ahead must count commits ahead of
        origin/${INTEGRATION_BRANCH}."""
        self.assertRegex(
            self.content,
            r"origin/\$\{INTEGRATION_BRANCH\}\.\.HEAD",
            msg="is_branch_zero_ahead must use 'origin/${INTEGRATION_BRANCH}..HEAD' range",
        )


class TestCiChecksDevelop(unittest.TestCase):
    """ci-checks.sh CHECK 3 must scan origin/develop..HEAD, not origin/main..HEAD,
    per ADR-0070 D1 (integration branch is develop).
    """

    def setUp(self):
        self.content = _read(CI_SH)

    def test_no_fetch_origin_main_in_check3(self):
        """CHECK 3 must not fetch origin main (should be origin develop)."""
        # Verify the CHECK 3 fetch line uses develop
        check3_section = re.search(
            r'CHECK 3.*?CHECK 4',
            self.content,
            re.DOTALL,
        )
        self.assertIsNotNone(check3_section, "CHECK 3 section not found in ci-checks.sh")
        section = check3_section.group(0)
        self.assertNotIn(
            "fetch origin main",
            section,
            msg="CHECK 3 must not fetch origin main; should fetch origin develop",
        )
        self.assertIn(
            "fetch origin develop",
            section,
            msg="CHECK 3 must fetch origin develop",
        )

    def test_commit_range_uses_develop(self):
        """CHECK 3 git log range must be origin/develop..HEAD."""
        self.assertIn(
            "origin/develop..HEAD",
            self.content,
            msg="ci-checks.sh CHECK 3 must scan 'origin/develop..HEAD' range",
        )
        self.assertNotIn(
            "origin/main..HEAD",
            self.content,
            msg="ci-checks.sh must not reference 'origin/main..HEAD' (migrate to develop)",
        )

    def test_adr_comment_updated(self):
        """ci-checks.sh header comment must reference ADR-0070 D1, not ADR-0041 D2,
        for the commit-range base.
        """
        self.assertIn(
            "ADR-0070 D1",
            self.content,
            msg="ci-checks.sh header comment must cite ADR-0070 D1 for develop base",
        )


class TestSessionStartDevelop(unittest.TestCase):
    """session-start.sh must fetch origin develop and report divergence vs
    origin/develop (not origin/main), per ADR-0070 D1.
    """

    def setUp(self):
        self.content = _read(SESSION_START_SH)

    def test_fetch_origin_develop(self):
        """session-start.sh must fetch origin develop."""
        self.assertIn(
            "fetch origin develop",
            self.content,
            msg="session-start.sh must fetch origin develop (not origin main)",
        )
        self.assertNotIn(
            "fetch origin main",
            self.content,
            msg="session-start.sh must not fetch origin main after two-tier migration",
        )

    def test_divergence_count_uses_develop(self):
        """Divergence count must compare HEAD against origin/develop."""
        self.assertIn(
            "HEAD..origin/develop",
            self.content,
            msg="session-start.sh divergence count must use 'HEAD..origin/develop'",
        )
        self.assertNotIn(
            "HEAD..origin/main",
            self.content,
            msg="session-start.sh must not count commits behind origin/main",
        )

    def test_context_string_says_develop(self):
        """Injected context string must say 'behind origin/develop', not 'behind origin/main'."""
        self.assertIn(
            "behind origin/develop",
            self.content,
            msg="Context string must say 'commit(s) behind origin/develop'",
        )
        self.assertNotIn(
            "behind origin/main",
            self.content,
            msg="Context string must not say 'behind origin/main' after migration",
        )

    def test_comment_updated(self):
        """Header comment must reference origin/develop divergence."""
        self.assertIn(
            "origin/develop",
            self.content,
            msg="session-start.sh header comment must mention origin/develop",
        )
        self.assertNotIn(
            "origin/main",
            self.content,
            msg="session-start.sh must have no origin/main references after migration",
        )


if __name__ == "__main__":
    unittest.main()
