"""
tests/test_branch_topology_step6_warn_1525.py

Regression test for issue #1525: BRANCH-TOPOLOGY step 6 (branch-protection
advisory) must WARN, not PASS, when its protection fetch is unconfirmed
(gh failure or exception) -- the same "never assert an unobserved state"
class as ADR-0087 D3 / #1448.

Reopened after the post-merge audit of 6902ca0 (PR #1527): that merge made
step 6 WARN on a failed/raising fetch, but still (a) dropped the seam's
source label from the WARN text, (b) treated a confirmed source paired
with an empty, unparsable, or `protected`-less payload as an OBSERVED
"protection off" state (silently defaulting via `bp.get("protected",
False)`), and (c) had no test pinning `source=` or asserting PASS on the
confirmed leg. This file closes all three gaps, mirroring how step 5
(PR #1516, closes #1448) already treats its own confirmed-but-empty/
unparsable payload as unconfirmed rather than zero.

Round 2 (PR #1539 round-1 reviewer BLOCK, finding F2): the exception-
raising leg of step 6's try/except had no test of its own -- a fail-open
mutant survived all five round-1 tests. Adds a raise-path case and, for
class completeness (rule #19), a JSON-list-payload case exercising the
`not isinstance(bp, dict)` leg distinctly from the unparsable-payload
case (which exercises `json.loads` raising instead).

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


def _make_fake_fetch(bp_response):
    """bp_response: callable(with_source: bool) -> tuple for the
    branches/develop call. Step 5's own PR-list call always CONFIRMS
    empty, so step 6 is the sole thing under test."""

    def fake_fetch(args, *, ttl=60.0, timeout=5.0, with_source=False):
        if args and args[0] == "pr":
            return (0, "[]", "live") if with_source else (0, "[]")
        if args and any("branches/develop" in a for a in args):
            return bp_response(with_source)
        return (1, "", "unavailable") if with_source else (1, "")

    return fake_fetch


class TestBranchTopologyStep6Warn(unittest.TestCase):
    def setUp(self):
        self._orig_fetch = health._health_gh_fetch

    def tearDown(self):
        health._health_gh_fetch = self._orig_fetch

    def test_step6_warns_on_unconfirmed_protection_fetch(self):
        health._health_gh_fetch = _make_fake_fetch(
            lambda ws: (1, "", "unavailable") if ws else (1, "")
        )

        result = health.check_branch_topology()

        self.assertEqual(
            result["result"], "WARN",
            f"step 6 must WARN on an unconfirmed protection fetch, got {result}",
        )
        self.assertIn("branch-protection", result["detail"])
        self.assertIn("unconfirmed", result["detail"])
        # (a): the WARN must carry the seam's own source label, not a
        # generic "API unavailable" / "check skipped" string that discards
        # it (post-merge audit finding F1 on PR #1527).
        self.assertIn(
            "source=unavailable", result["detail"],
            f"WARN must name the unconfirmed source, got: {result['detail']!r}",
        )

    def test_step6_warns_on_empty_payload(self):
        """(b): a confirmed source (rc=0) paired with an EMPTY payload must
        not be read as an observed 'protection off' state -- mirrors step
        5's own empty-payload handling."""
        health._health_gh_fetch = _make_fake_fetch(
            lambda ws: (0, "", "live") if ws else (0, "")
        )

        result = health.check_branch_topology()

        self.assertEqual(result["result"], "WARN", result)
        self.assertIn("branch-protection", result["detail"])
        self.assertIn("unconfirmed", result["detail"])
        self.assertIn("source=live", result["detail"], result["detail"])
        self.assertNotIn("branch-protection=off", result["detail"], result["detail"])

    def test_step6_warns_on_unparsable_payload(self):
        health._health_gh_fetch = _make_fake_fetch(
            lambda ws: (0, "not-json", "live") if ws else (0, "not-json")
        )

        result = health.check_branch_topology()

        self.assertEqual(result["result"], "WARN", result)
        self.assertIn("unconfirmed", result["detail"])
        self.assertIn("source=live", result["detail"], result["detail"])
        self.assertNotIn("branch-protection=off", result["detail"], result["detail"])

    def test_step6_warns_on_protected_less_payload(self):
        """(b): a confirmed, parsable, dict-shaped payload that simply lacks
        a `protected` field must not silently default to 'off' via
        `bp.get("protected", False)` -- that asserts a state never
        observed (post-merge audit finding F1, same class as rule #19)."""
        health._health_gh_fetch = _make_fake_fetch(
            lambda ws: (0, '{"name": "develop"}', "live") if ws else (0, '{"name": "develop"}')
        )

        result = health.check_branch_topology()

        self.assertEqual(
            result["result"], "WARN",
            f"a protected-less payload must WARN, not PASS with 'off', got {result}",
        )
        self.assertIn("unconfirmed", result["detail"])
        self.assertIn("source=live", result["detail"], result["detail"])
        self.assertNotIn("branch-protection=off", result["detail"], result["detail"])

    def test_step6_still_passes_when_protection_fetch_confirms(self):
        health._health_gh_fetch = _make_fake_fetch(
            lambda ws: (0, '{"protected": true}', "live") if ws else (0, '{"protected": true}')
        )

        result = health.check_branch_topology()

        # main-is-ancestor / pr-base are read from THIS repo's real git
        # state (main is fetched as an ancestor of develop and the stubbed
        # PR list is confirmed-empty), so a fully confirmed protection leg
        # must reach step 6's own PASS -- (c): the confirmed case must
        # assert PASS with the source label, never merely "not unconfirmed".
        self.assertEqual(
            result["result"], "PASS",
            f"a fully confirmed protection={{protected: true}} leg must PASS, got {result}",
        )
        self.assertIn("branch-protection=on", result["detail"], result["detail"])
        self.assertNotIn("branch-protection fetch unconfirmed", result["detail"])

    def test_step6_warns_on_raising_protection_fetch(self):
        """The exception-raising leg of step 6's try/except -- distinct
        from the rc!=0 unconfirmed leg above -- must still WARN with a
        source label. The raise happens before the fetch's own source
        tuple is unpacked, so the label reported is the pre-fetch default
        (PR #1539 round-1 reviewer finding F2: this leg had no test
        pinning `source=`, and a fail-open mutant survived all five prior
        tests)."""

        def fake_fetch(args, *, ttl=60.0, timeout=5.0, with_source=False):
            if args and args[0] == "pr":
                return (0, "[]", "live") if with_source else (0, "[]")
            if args and any("branches/develop" in a for a in args):
                raise RuntimeError("boom: gh api raised")
            return (1, "", "unavailable") if with_source else (1, "")

        health._health_gh_fetch = fake_fetch

        result = health.check_branch_topology()

        self.assertEqual(
            result["result"], "WARN",
            f"step 6 must WARN when the protection fetch itself raises, got {result}",
        )
        self.assertIn("unconfirmed", result["detail"], result["detail"])
        self.assertIn("source=computing", result["detail"], result["detail"])
        self.assertNotIn("branch-protection=off", result["detail"], result["detail"])
        self.assertNotIn("branch-protection=on", result["detail"], result["detail"])

    def test_step6_warns_on_list_payload(self):
        """(b), the `not isinstance(bp, dict)` leg specifically: a
        confirmed fetch whose payload parses as a JSON *list* must WARN as
        unconfirmed -- distinct from the unparsable-payload case above,
        which exercises `json.loads` raising rather than this isinstance
        check (round-2 class-completeness addition, rule #19)."""
        health._health_gh_fetch = _make_fake_fetch(
            lambda ws: (0, "[]", "live") if ws else (0, "[]")
        )

        result = health.check_branch_topology()

        self.assertEqual(result["result"], "WARN", result)
        self.assertIn("unconfirmed", result["detail"], result["detail"])
        self.assertIn("source=live", result["detail"], result["detail"])
        self.assertNotIn("branch-protection=off", result["detail"], result["detail"])


if __name__ == "__main__":
    unittest.main()
