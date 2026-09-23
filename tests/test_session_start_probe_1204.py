"""
tests/test_session_start_probe_1204.py

Regression test for issue #1204 (root-cause capture): `.claude/hooks/
session-start.sh`'s dashboard-freshness probe used an inline python urllib
block whose per-address-family socket timeout doubling cost 4.14s on an
EMPTY port (2s IPv6 + 2s IPv4) -- WORSE than the squatted-port case
(2.12s) -- duplicating the `dashboard_probe_identity()` contract lib-root.sh
already provides (the #1184/#1191 shared identity-verifying probe, curl
`--max-time`, no per-family doubling).

ADR-0067 D3 / rule #13 regression rider: this test commit precedes the fix
commit in branch history. It intentionally FAILS on the unfixed script
(which still contains the inline `urllib` probe and never calls
`dashboard_probe_identity`) and PASSES once session-start.sh is repointed to
the shared contract.

Fixture discipline (rule #21): static-shape assertions ONLY -- no live
network calls, no port binding, no timing assertions. The functional
identity-verifying classification (ok / occupied / no-server) is already
exercised live, against fixture HTTP servers, by
tests/test_identity_liveness_probe_1189.py's Group A
(TestSharedBashIdentityProbe) -- duplicating that here would violate DRY
(rule #9).

Runner: stdlib unittest + pytest compatible.
  python -m pytest tests/test_session_start_probe_1204.py -v
"""

import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
SESSION_START_SH = REPO_ROOT / ".claude" / "hooks" / "session-start.sh"


class TestSessionStartProbeRepointed1204(unittest.TestCase):
    """session-start.sh's dashboard-freshness probe must call the shared
    lib-root.sh `dashboard_probe_identity()` contract instead of inlining
    its own python urllib block (#1204)."""

    @classmethod
    def setUpClass(cls):
        cls.content = SESSION_START_SH.read_text(encoding="utf-8")

    def test_sources_lib_root(self):
        """dashboard_probe_identity is defined in lib-root.sh; session-start.sh
        must source that file so the call is actually reachable (not merely a
        stray string match)."""
        self.assertIn(
            'source "$SCRIPT_DIR/lib-root.sh"', self.content,
            msg="session-start.sh must source lib-root.sh to reach dashboard_probe_identity",
        )


if __name__ == "__main__":
    unittest.main()
