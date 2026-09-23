"""
tests/test_branch_topology_step6_warn_1525.py

Regression test for issue #1525: BRANCH-TOPOLOGY step 6 (branch-protection
advisory) must WARN, not PASS, when its protection fetch is unconfirmed
(gh failure or exception) -- the same "never assert an unobserved state"
class as ADR-0087 D3 / #1448.

Stubs `health._health_gh_fetch` only (the health-registry's sole gh seam);
git-based checks (origin/develop, origin/main, ancestor, PR-base) run
against this real repo's real state, matching check_branch_topology's own
"always emits real data" design.
"""
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
_DASHBOARD_DIR = str(REPO_ROOT / "dashboard")
if _DASHBOARD_DIR not in sys.path:
    sys.path.insert(0, _DASHBOARD_DIR)

import health  # noqa: E402


class TestBranchTopologyStep6Warn(unittest.TestCase):
    def setUp(self):
        self._orig_fetch = health._health_gh_fetch

    def tearDown(self):
        health._health_gh_fetch = self._orig_fetch

    def test_step6_warns_on_unconfirmed_protection_fetch(self):
        def fake_fetch(args, *, ttl=60.0, timeout=5.0, with_source=False):
            if args and args[0] == "pr":
                # Step 5 (already fixed, #1516) must CONFIRM here so step 6
                # is the sole thing under test.
                return (0, "[]", "live") if with_source else (0, "[]")
            if args and any("branches/develop" in a for a in args):
                result = (1, "")  # unconfirmed: gh failed
                return result + ("unavailable",) if with_source else result
            return (1, "", "unavailable") if with_source else (1, "")

        health._health_gh_fetch = fake_fetch

        result = health.check_branch_topology()

        self.assertEqual(
            result["result"], "WARN",
            f"step 6 must WARN on an unconfirmed protection fetch, got {result}",
        )
        self.assertIn("branch-protection", result["detail"])
        self.assertIn("unconfirmed", result["detail"])

    def test_step6_still_passes_when_protection_fetch_confirms(self):
        def fake_fetch(args, *, ttl=60.0, timeout=5.0, with_source=False):
            if args and args[0] == "pr":
                return (0, "[]", "live") if with_source else (0, "[]")
            if args and any("branches/develop" in a for a in args):
                result = (0, '{"protected": true}')
                return result + ("live",) if with_source else result
            return (1, "", "unavailable") if with_source else (1, "")

        health._health_gh_fetch = fake_fetch

        result = health.check_branch_topology()

        # main-is-ancestor / pr-base are read from THIS repo's real git state,
        # so this only asserts the confirmed-fetch path is not itself WARN'd
        # by the protection leg -- it never regresses to unconfirmed-shaped text.
        self.assertNotIn("branch-protection fetch unconfirmed", result["detail"])


if __name__ == "__main__":
    unittest.main()
