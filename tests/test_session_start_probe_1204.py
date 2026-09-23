"""
tests/test_session_start_probe_1204.py

Regression test for issue #1204 (root-cause capture, historical): `.claude/
hooks/session-start.sh` used to run its own dashboard-freshness probe via an
inline python urllib block whose per-address-family socket timeout doubling
cost 4.14s on an EMPTY port (2s IPv6 + 2s IPv4) -- WORSE than the
squatted-port case (2.12s) -- duplicating a shared identity-verifying probe
contract lib-root.sh already provided (the #1184/#1191 probe: curl
`--max-time`, no per-family doubling). The fix repointed session-start.sh to
that shared contract.

That contract and the dashboard-freshness feature it served were later
removed entirely (ADR-0088 D1, slice #1482) -- there is no more probe to
call. The surviving test below now guards a narrower, still-load-bearing
fact: session-start.sh continues to source lib-root.sh for its
MAIN_ROOT/LOG_DIR helpers, which outlived the probe.

Fixture discipline (rule #21): static-shape assertions ONLY -- no live
network calls, no port binding, no timing assertions.

Runner: stdlib unittest + pytest compatible.
  python -m pytest tests/test_session_start_probe_1204.py -v
"""

import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
SESSION_START_SH = REPO_ROOT / ".claude" / "hooks" / "session-start.sh"


class TestSessionStartProbeRepointed1204(unittest.TestCase):
    """Historical: session-start.sh's now-removed dashboard-freshness probe
    used to call a shared lib-root.sh identity-verifying contract (#1204).
    The surviving check below only verifies session-start.sh still sources
    lib-root.sh, for the MAIN_ROOT/LOG_DIR helpers that outlived the probe."""

    @classmethod
    def setUpClass(cls):
        cls.content = SESSION_START_SH.read_text(encoding="utf-8")

    def test_sources_lib_root(self):
        """session-start.sh must source lib-root.sh so MAIN_ROOT/LOG_DIR
        resolution stays reachable (not merely a stray string match)."""
        self.assertIn(
            'source "$SCRIPT_DIR/lib-root.sh"', self.content,
            msg="session-start.sh must source lib-root.sh for MAIN_ROOT/LOG_DIR",
        )


if __name__ == "__main__":
    unittest.main()
