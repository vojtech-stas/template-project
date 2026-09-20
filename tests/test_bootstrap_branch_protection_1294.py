"""
tests/test_bootstrap_branch_protection_1294.py

Regression test for issue #1294 (root-cause capture, `backlog`+`root-cause`):
bootstrap.sh step 5 called the GitHub branch-protection API against `main`
instead of `develop`, contradicting CLAUDE.md rule #4's two-tier delivery
model (agents merge to `develop`; `main` advances only via
`tools/promote.sh`). A fresh clone running bootstrap.sh therefore left
`develop` unprotected while the README claimed otherwise. Fixed in PR #1454.

Per CLAUDE.md rule #13's regression rider (ADR-0067 D3): this test is
committed BEFORE the fix commit so it fails against the pre-fix script
(which called `branches/main/protection`) and passes after the fix
(`branches/develop/protection`). bootstrap.sh makes live `gh api` calls that
are not safe to execute in a unit test, so this asserts statically on the
script's source text, matching the idiom of tests/test_dead_routes_removed_729.py.

Scope: this test targets ONLY the step-5 branch-protection block in
bootstrap.sh -- it is not a general bootstrap.sh test harness.
"""

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BOOTSTRAP_SH = REPO_ROOT / "bootstrap.sh"


def _step5_block(source: str) -> str:
    """Extract bootstrap.sh's step-5 (branch protection) block: from the
    "# ---- step 5:" section-header comment up to (not including) the
    "# ---- step 6:" section-header comment."""
    match = re.search(r"# ---- step 5:.*?(?=# ---- step 6:)", source, re.DOTALL)
    assert match is not None, (
        "bootstrap.sh must contain a '# ---- step 5:' ... '# ---- step 6:' "
        "delimited block (issue #1294 regression test assumption)"
    )
    return match.group(0)


class TestBootstrapBranchProtectionTarget(unittest.TestCase):
    """Step 5's branch-protection API call must target `develop`, not
    `main` (issue #1294)."""

    @classmethod
    def setUpClass(cls):
        cls.source = BOOTSTRAP_SH.read_text(encoding="utf-8", errors="replace")
        cls.step5 = _step5_block(cls.source)

    def test_step5_targets_develop_not_main(self):
        self.assertIn(
            "branches/develop/protection",
            self.step5,
            "bootstrap.sh step 5 must call the branch-protection API against "
            "'develop' (issue #1294)",
        )
        self.assertNotIn(
            "branches/main/protection",
            self.step5,
            "bootstrap.sh step 5 must NOT call the branch-protection API "
            "against 'main' (issue #1294 -- main is only advanced via "
            "tools/promote.sh per CLAUDE.md rule #4)",
        )


if __name__ == "__main__":
    unittest.main()
