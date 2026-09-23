"""
tests/test_fleet_economics_removal_854.py

Regression tests for slice #854: fleet-economics machinery removal.

These tests MUST FAIL on the pre-removal codebase and PASS after removal.
Per ADR-0067 D2 (test-first discipline: test commit precedes fix commit).

Tests assert:
1. EFFORT-BUDGET is NOT in CHECK_REGISTRY
2. REASSURANCE-RERUN is NOT in CHECK_REGISTRY
3. DECLARED-PARITY is NOT in CHECK_REGISTRY
4. DORA-PANEL is NOT in CHECK_REGISTRY

(Former item 5, "/api/dora is NOT a registered route in server.py", was
retired with server.py itself per ADR-0088 D1 — slice #1481.)
"""

import re
import sys
import unittest
from pathlib import Path

# Allow importing dashboard modules from any cwd
_REPO_ROOT = Path(__file__).resolve().parent.parent
_DASHBOARD_DIR = _REPO_ROOT / "dashboard"
if str(_DASHBOARD_DIR) not in sys.path:
    sys.path.insert(0, str(_DASHBOARD_DIR))


class TestFleetEconomicsRemoved(unittest.TestCase):
    """Assert the four economics check IDs are absent from CHECK_REGISTRY."""

    def _get_registry(self):
        # Re-import to pick up current state (avoids module-level caching)
        import importlib
        import health
        importlib.reload(health)
        return health.CHECK_REGISTRY

    def test_effort_budget_not_in_registry(self):
        """EFFORT-BUDGET must not be in CHECK_REGISTRY after removal."""
        registry = self._get_registry()
        self.assertNotIn(
            "EFFORT-BUDGET",
            registry,
            "EFFORT-BUDGET should have been removed from CHECK_REGISTRY (slice #854)",
        )

    def test_reassurance_rerun_not_in_registry(self):
        """REASSURANCE-RERUN must not be in CHECK_REGISTRY after removal."""
        registry = self._get_registry()
        self.assertNotIn(
            "REASSURANCE-RERUN",
            registry,
            "REASSURANCE-RERUN should have been removed from CHECK_REGISTRY (slice #854)",
        )

    def test_declared_parity_not_in_registry(self):
        """DECLARED-PARITY must not be in CHECK_REGISTRY after removal."""
        registry = self._get_registry()
        self.assertNotIn(
            "DECLARED-PARITY",
            registry,
            "DECLARED-PARITY should have been removed from CHECK_REGISTRY (slice #854)",
        )

    def test_dora_panel_not_in_registry(self):
        """DORA-PANEL must not be in CHECK_REGISTRY after removal."""
        registry = self._get_registry()
        self.assertNotIn(
            "DORA-PANEL",
            registry,
            "DORA-PANEL should have been removed from CHECK_REGISTRY (slice #854)",
        )


if __name__ == "__main__":
    unittest.main()
