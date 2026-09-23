"""
tests/test_gh_cache_health_firing_996.py — slice #996 acceptance tests.

Verifies that:
  cr.3  the health-payload build path (affected check functions) returns in <10s
        under a simulated slow/failing gh.  No serial stall.
  cr.4  A health row whose gh data is unavailable within the timeout reports
        "computing"/last-known while other rows still return normally.

cr.5 (prd_firing.fetch_prd_firing() cold-load timing) was removed by PRD #1214
slice #1218 (module deletion) — prd_firing.py exercised exclusively via
dynamic importlib import (`_reimport("prd_firing")`), missed by the slice's
static-import audit, deleted with its subject.

All tests monkeypatch at the gh_cache.gh_fetch layer (the single injection point
routed through by slice #996), so no subprocess patching is needed.  stdlib
unittest only — no top-level pytest dependency.
"""

import importlib
import os
import sys
import time
import unittest

# ---------------------------------------------------------------------------
# Add dashboard/ to sys.path so "import health" resolves.
# ---------------------------------------------------------------------------
_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_TESTS_DIR)
_DASHBOARD_DIR = os.path.join(_REPO_ROOT, "dashboard")
if _DASHBOARD_DIR not in sys.path:
    sys.path.insert(0, _DASHBOARD_DIR)

# ---------------------------------------------------------------------------
# Helpers to (re)import modules with a clean sys.modules entry so monkeypatching
# the module-level name is reliable across tests.
# ---------------------------------------------------------------------------

def _reimport(module_name: str):
    """Force a fresh import of *module_name* and return the module object."""
    if module_name in sys.modules:
        del sys.modules[module_name]
    return importlib.import_module(module_name)


# ---------------------------------------------------------------------------
# Shared slow/failing gh_fetch stub
# ---------------------------------------------------------------------------

class _GhResult:
    """Minimal stand-in for gh_cache.GhResult (a NamedTuple)."""
    def __init__(self, value, fetched_at, source):
        self.value = value
        self.fetched_at = fetched_at
        self.source = source


def _make_computing_result():
    return _GhResult(value=None, fetched_at="2026-01-01T00:00:00+00:00", source="computing")


def _slow_gh_fetch(args, *, ttl, timeout):
    """Simulates a gh call that always times out — returns computing sentinel instantly."""
    # We do NOT actually sleep here: the contract is that _health_gh_fetch in
    # health.py calls gh_fetch and gh_cache is supposed to short-circuit the
    # blocking subprocess.  So a "slow gh" means gh_fetch returns computing immediately
    # (simulating the already-elapsed-timeout path).
    return _make_computing_result()


# ===========================================================================
# cr.3 — health-payload build path returns <10s under slow gh
# ===========================================================================

class TestCr3NoSerialStall(unittest.TestCase):
    """Verify that health check functions return quickly when gh is slow.

    We test the most gh-heavy check functions individually (not the full
    serve_health() pipeline, which would require a real server), injecting
    _slow_gh_fetch at the gh_cache layer.  Each call should complete in <<10s
    (they should complete in milliseconds since we never actually sleep).
    """

    def _patch_health_gh_cache(self, health_mod):
        """Inject _slow_gh_fetch into health module's _gh_fetch_impl."""
        health_mod._gh_fetch_impl = _slow_gh_fetch
        health_mod._GH_CACHE_AVAILABLE = True

    def test_spec_coverage_no_stall(self):
        """SPEC-COVERAGE returns quickly (gh unavailable → WARN) under slow gh."""
        health = _reimport("health")
        self._patch_health_gh_cache(health)

        t0 = time.monotonic()
        result = health.check_spec_coverage()
        elapsed = time.monotonic() - t0

        self.assertLess(elapsed, 10.0, f"SPEC-COVERAGE took {elapsed:.2f}s — serial stall")
        self.assertIn("result", result)
        # With computing sentinel, gh data unavailable → WARN
        self.assertEqual(result["result"], "WARN")

    def test_residual_ratio_no_stall(self):
        """RESIDUAL-RATIO returns quickly under slow gh."""
        health = _reimport("health")
        self._patch_health_gh_cache(health)

        t0 = time.monotonic()
        result = health.check_residual_ratio()
        elapsed = time.monotonic() - t0

        self.assertLess(elapsed, 10.0, f"RESIDUAL-RATIO took {elapsed:.2f}s — serial stall")
        self.assertIn("result", result)
        # No closed PRDs found (gh unavailable) → WARN
        self.assertEqual(result["result"], "WARN")

    def test_capture_shape_no_stall(self):
        """CAPTURE-SHAPE returns quickly under slow gh."""
        health = _reimport("health")
        self._patch_health_gh_cache(health)

        t0 = time.monotonic()
        result = health.check_capture_shape()
        elapsed = time.monotonic() - t0

        self.assertLess(elapsed, 10.0, f"CAPTURE-SHAPE took {elapsed:.2f}s — serial stall")
        self.assertIn("result", result)

    def test_silent_drift_no_stall(self):
        """SILENT-DRIFT returns quickly under slow gh."""
        health = _reimport("health")
        self._patch_health_gh_cache(health)

        t0 = time.monotonic()
        result = health.check_silent_drift()
        elapsed = time.monotonic() - t0

        self.assertLess(elapsed, 10.0, f"SILENT-DRIFT took {elapsed:.2f}s — serial stall")
        self.assertIn("result", result)
        # gh unavailable → honest WARN
        self.assertEqual(result["result"], "WARN")

    def test_test_ordering_no_stall(self):
        """TEST-ORDERING returns quickly under slow gh."""
        health = _reimport("health")
        self._patch_health_gh_cache(health)

        t0 = time.monotonic()
        result = health.check_test_ordering()
        elapsed = time.monotonic() - t0

        self.assertLess(elapsed, 10.0, f"TEST-ORDERING took {elapsed:.2f}s — serial stall")
        self.assertIn("result", result)

    def test_required_labels_no_stall(self):
        """REQUIRED-LABELS returns quickly under slow gh."""
        health = _reimport("health")
        self._patch_health_gh_cache(health)

        t0 = time.monotonic()
        result = health.check_required_labels()
        elapsed = time.monotonic() - t0

        self.assertLess(elapsed, 10.0, f"REQUIRED-LABELS took {elapsed:.2f}s — serial stall")
        self.assertIn("result", result)
        # gh unavailable → WARN (degrades honestly)
        self.assertEqual(result["result"], "WARN")

    def test_branch_topology_no_stall(self):
        """BRANCH-TOPOLOGY returns quickly under slow gh (gh steps 5+6 only)."""
        health = _reimport("health")
        self._patch_health_gh_cache(health)

        t0 = time.monotonic()
        result = health.check_branch_topology()
        elapsed = time.monotonic() - t0

        self.assertLess(elapsed, 10.0, f"BRANCH-TOPOLOGY took {elapsed:.2f}s — serial stall")
        self.assertIn("result", result)


# ===========================================================================
# cr.4 — unavailable gh shows "computing"/WARN label; other rows still render
# ===========================================================================

class TestCr4DegradeLabel(unittest.TestCase):
    """Verify that a check degraded by slow gh returns WARN (not crash/hang)
    and that a check whose gh data IS available still returns normally.
    """

    def test_degraded_row_is_warn_not_fail(self):
        """SPEC-COVERAGE with unavailable gh: result is WARN (honest degrade), not FAIL."""
        health = _reimport("health")
        health._gh_fetch_impl = _slow_gh_fetch
        health._GH_CACHE_AVAILABLE = True

        result = health.check_spec_coverage()
        self.assertEqual(result["result"], "WARN",
                         "degraded row should be WARN, not FAIL or error")
        # Detail should mention unavailability, not an exception traceback
        detail = result.get("detail", "")
        self.assertIsInstance(detail, str)
        self.assertGreater(len(detail), 0)

    def test_non_gh_row_unaffected(self):
        """HOOK-INTEGRITY (a non-gh check) still returns its normal result
        even when _gh_fetch_impl is set to always fail.
        This confirms the degradation is isolated to gh-dependent rows.
        """
        health = _reimport("health")
        health._gh_fetch_impl = _slow_gh_fetch
        health._GH_CACHE_AVAILABLE = True

        # HOOK-INTEGRITY reads from the event log (file-based), not gh
        result = health.check_hook_integrity()
        # It should return a dict with an "id" key — not raise an exception
        self.assertIn("id", result)
        self.assertEqual(result["id"], "HOOK-INTEGRITY")
        self.assertIn(result["result"], ("PASS", "WARN", "FAIL"))

    def test_stale_value_returned_when_cached(self):
        """When gh_fetch returns source='stale', _health_gh_fetch still returns rc=0.

        This verifies that a previously-cached (stale) value is served rather than
        the computing sentinel — the row renders with last-known data.
        """
        health = _reimport("health")

        stale_result = _GhResult(
            value='[{"name": "stale-label"}]',
            fetched_at="2026-01-01T00:00:00+00:00",
            source="stale",
        )

        def _stale_gh_fetch(args, *, ttl, timeout):
            return stale_result

        health._gh_fetch_impl = _stale_gh_fetch
        health._GH_CACHE_AVAILABLE = True

        # Call _health_gh_fetch directly to verify the stale-source path
        rc, out = health._health_gh_fetch(
            ["label", "list"], ttl=60.0, timeout=5.0
        )
        self.assertEqual(rc, 0, "stale result should yield rc=0 (serve the value)")
        self.assertIn("stale-label", out)

    def test_computing_sentinel_yields_rc1(self):
        """When gh_fetch returns source='computing', _health_gh_fetch returns rc=1."""
        health = _reimport("health")
        health._gh_fetch_impl = _slow_gh_fetch
        health._GH_CACHE_AVAILABLE = True

        rc, out = health._health_gh_fetch(
            ["issue", "list"], ttl=60.0, timeout=5.0
        )
        self.assertEqual(rc, 1, "computing sentinel should yield rc=1")
        self.assertEqual(out, "")


if __name__ == "__main__":
    unittest.main()
