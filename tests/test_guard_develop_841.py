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
    """ci-checks.sh CHECK 3 must resolve the integration branch via
    tools/pipeline_config.py (ADR-0089 D1) rather than hardcoding a
    'develop'/'main' literal — superseding the #841-era assertions that
    pinned the integration branch to the literal 'develop' (ADR-0089 D1/D3,
    S2-scan of slice #1512).
    """

    def setUp(self):
        self.content = _read(CI_SH)

    def test_no_hardcoded_branch_literal_outside_comments(self):
        """Non-comment lines must not hardcode 'develop' or 'main' as a
        git ref/branch name — CHECK 3 must route through the resolved
        $INTEGRATION_BRANCH variable instead. Comment/docblock lines are
        exempt (S2-prose). CHECK 9's own pass message ("clean main") is the
        one out-of-scope line the slice's S2-prose oracle names: it
        describes the DOCS-* registry run, naming no branch that a site in
        this slice acts on."""
        offending = []
        for lineno, line in enumerate(self.content.splitlines(), start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "clean main" in line:
                continue
            if re.search(r"\b(develop|main)\b", line):
                offending.append((lineno, line))
        self.assertEqual(
            [],
            offending,
            msg=(
                "ci-checks.sh contains hardcoded branch literal(s) on "
                "non-comment line(s) — roles must resolve via "
                "tools/pipeline_config.py (ADR-0089 D1):\n"
                + "\n".join(f"  {n}: {v}" for n, v in offending)
            ),
        )

    def test_resolves_integration_branch_via_pipeline_config(self):
        """The integration branch is resolved once via pipeline_config.py,
        located relative to this script's own path (BASH_SOURCE[0])."""
        self.assertIn(
            '_CI_TOOLS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -W 2>/dev/null || pwd)"',
            self.content,
        )
        self.assertIn('pipeline_config.py" integration', self.content)
        self.assertIn("INTEGRATION_BRANCH=", self.content)

    def test_check3_fetches_integration_branch_var(self):
        """CHECK 3 must fetch origin "$INTEGRATION_BRANCH", never a literal."""
        self.assertIn(
            'git fetch origin "$INTEGRATION_BRANCH"',
            self.content,
            msg='CHECK 3 must fetch origin "$INTEGRATION_BRANCH"',
        )

    def test_commit_range_uses_integration_branch_var(self):
        """CHECK 3 git log range must be origin/${INTEGRATION_BRANCH}..HEAD."""
        self.assertIn(
            "origin/${INTEGRATION_BRANCH}..HEAD",
            self.content,
            msg="ci-checks.sh CHECK 3 must scan 'origin/${INTEGRATION_BRANCH}..HEAD' range",
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
    """session-start.sh must resolve the integration branch via
    tools/pipeline_config.py (ADR-0089 D1), superseding the #841-era
    assertions that pinned the integration branch to the literal 'develop'.
    Its degrade-not-exit behaviour on a resolver failure (S2-d) is tested
    by running the hook, in tests/test_branch_roles_sandbox_1512.py.
    """

    def setUp(self):
        self.content = _read(SESSION_START_SH)

    def test_no_hardcoded_branch_literal_outside_comments(self):
        """Non-comment lines must not hardcode 'develop' or 'main' as a
        git ref/branch name — the fetch, divergence count and context
        string must all route through the resolved $INTEGRATION_BRANCH
        variable instead. Comment/docblock lines are exempt (S2-prose)."""
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
                "session-start.sh contains hardcoded branch literal(s) on "
                "non-comment line(s) — roles must resolve via "
                "tools/pipeline_config.py (ADR-0089 D1):\n"
                + "\n".join(f"  {n}: {v}" for n, v in offending)
            ),
        )

    def test_resolves_integration_branch_via_pipeline_config(self):
        """The integration branch is resolved once via pipeline_config.py,
        located relative to this script's own path (S1-c), through the
        `pwd -W` fallback idiom the other bash call sites use (#1535)."""
        self.assertIn(
            '_SS_TOOLS_DIR="$(cd "$SCRIPT_DIR/../../tools" && { pwd -W 2>/dev/null || pwd; })"',
            self.content,
        )
        self.assertIn('PIPELINE_CONFIG_PY="$_SS_TOOLS_DIR/pipeline_config.py"', self.content)
        self.assertIn("INTEGRATION_BRANCH=", self.content)

    def test_fetch_uses_resolved_variable(self):
        """session-start.sh must fetch origin "$INTEGRATION_BRANCH"."""
        self.assertIn(
            'git fetch origin "$INTEGRATION_BRANCH"',
            self.content,
            msg='session-start.sh must fetch origin "$INTEGRATION_BRANCH"',
        )

    def test_divergence_count_uses_resolved_variable(self):
        """Divergence count must compare HEAD against origin/$INTEGRATION_BRANCH."""
        self.assertIn(
            "HEAD..origin/$INTEGRATION_BRANCH",
            self.content,
            msg="session-start.sh divergence count must use 'HEAD..origin/$INTEGRATION_BRANCH'",
        )

    def test_context_string_interpolates_role(self):
        """Injected context string must interpolate the resolved role, never
        hardcode 'behind origin/develop' or 'behind origin/main'."""
        self.assertIn("behind origin/%s", self.content)
        self.assertIn('"${INTEGRATION_BRANCH:-?}"', self.content)


if __name__ == "__main__":
    unittest.main()
