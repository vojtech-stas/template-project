"""
Regression tests for issue #1012 — Health board shows 'computing...' hiding
valid stale data while refreshing=True (slow-gh).

Backend: _HEALTH_TTL is 30s — shorter than typical slow-gh compute
time, so refreshing=True is near-permanent.
Fix: raise _HEALTH_TTL to >= 180s so the cache isn't perpetually expired.

This test FAILs on develop before the fix and PASSes after.

The former front-end facet (loadHealth()'s _isCold logic) is retired per
ADR-0080 D1: loadHealth() and the entire old Health-tab render pipeline
were deleted with the Health tab; the thin health strip that replaced it
(fetched once per page load, not polled) had no equivalent fast-retry/
_isCold concern, and was itself retired along with the rest of the served
dashboard per ADR-0088 D1. `serve_health()` and its TTL cache survive as
a library shim (captured: #1491) that this test still exercises directly.

NO top-level `import pytest` — stdlib unittest only.

Runner:
  python -m unittest tests.test_health_render_while_refreshing_1012 -v
  python -m pytest tests/test_health_render_while_refreshing_1012.py -v
"""

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
HEALTH_PY  = REPO_ROOT / "dashboard" / "health.py"


def _load_health_py() -> str:
    return HEALTH_PY.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Backend: _HEALTH_TTL must be >= 180 seconds
# ---------------------------------------------------------------------------

class TestHealthTTLSufficient(unittest.TestCase):
    """Regression (b): _HEALTH_TTL in health.py must be raised from 30s to
    a value >= 180s (comfortably above typical slow-gh compute time ~60-95s).

    Before fix (current develop): _HEALTH_TTL = 30
      Under slow-gh, the background recompute takes ~30-95s — as long as or
      longer than the TTL. So the cache expires before the recompute finishes,
      making refreshing=True near-permanent.

    After fix: _HEALTH_TTL >= 180
      This gives the background recompute ample time to finish before the
      cache is considered expired, so refreshing=True is brief, not permanent.
    """

    MIN_TTL = 180  # seconds — fix requirement per issue #1012

    def setUp(self):
        self.content = _load_health_py()

    def _get_health_ttl_value(self) -> int:
        """Parse the _HEALTH_TTL assignment from health.py."""
        m = re.search(r'^_HEALTH_TTL\s*=\s*(\d+)', self.content, re.MULTILINE)
        if not m:
            raise AssertionError(
                "_HEALTH_TTL not found in dashboard/health.py. "
                "Expected: `_HEALTH_TTL = <integer>` at module level."
            )
        return int(m.group(1))

    def test_health_ttl_is_at_least_180(self):
        """_HEALTH_TTL must be >= 180 seconds after the fix.

        Before fix: _HEALTH_TTL = 30
          Under slow-gh the cache expires before recompute finishes
          => refreshing=True is near-permanent.

        After fix: _HEALTH_TTL >= 180
          TTL is comfortably above the typical gh-dependent compute time
          (~60-95s), so refreshing=True is brief.
        """
        actual = self._get_health_ttl_value()
        self.assertGreaterEqual(
            actual,
            self.MIN_TTL,
            msg=(
                f"_HEALTH_TTL = {actual}s is too low (minimum required: {self.MIN_TTL}s).\n\n"
                f"Before fix: _HEALTH_TTL = 30s — under slow-gh the background recompute\n"
                f"takes ~30-95s, so the cache expires before recompute finishes and\n"
                f"refreshing=True is near-permanent.\n\n"
                f"After fix: _HEALTH_TTL >= {self.MIN_TTL}s so the cache remains valid\n"
                f"for the full recompute cycle and refreshing=True is brief (not permanent).\n"
                f"Issue #1012: bump _HEALTH_TTL to {self.MIN_TTL}+ seconds."
            )
        )

    def test_health_ttl_has_1012_comment(self):
        """_HEALTH_TTL line or its vicinity must reference #1012 (fix citation).

        This ensures the TTL bump is intentional and traceable, not accidental.
        """
        # Find the _HEALTH_TTL line and check the surrounding 3 lines for #1012
        lines = self.content.splitlines()
        ttl_line_idx = None
        for i, line in enumerate(lines):
            if re.match(r'\s*_HEALTH_TTL\s*=', line):
                ttl_line_idx = i
                break

        if ttl_line_idx is None:
            self.fail("_HEALTH_TTL not found in health.py")

        # Check the TTL line itself and up to 2 lines after it for '#1012'
        context_lines = lines[ttl_line_idx:ttl_line_idx + 3]
        context_text = '\n'.join(context_lines)
        self.assertIn(
            '1012',
            context_text,
            msg=(
                f"_HEALTH_TTL definition (line {ttl_line_idx + 1}) does not cite #1012.\n"
                f"Context:\n{context_text}\n\n"
                "Please add a comment citing #1012 on or after the _HEALTH_TTL line "
                "to make the intentional TTL bump traceable."
            )
        )


if __name__ == "__main__":
    unittest.main()
