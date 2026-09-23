"""
Regression tests for slice #960 — transcript-sourced runtime edges + capture banner.

PRD #956 §2 AC #7:
  #7: when NEITHER transcript NOR hook log is live, capture_unavailable=True is
      set in the observe() return dict (comparison.py threads it to the UI banner).

Trimmed by PRD #1214 slice #1218 (module deletion): groups 1/2/3/5/6, which
exercised the runtime observer module directly, were deleted with their subject.
Only ComparisonThread survives — it exercises comparison.py (a kept module).

Post-deletion, comparison.py's `_apply_runtime_observation()` line-74
try/except (byte-identical, ADR-0080 D1's ADR-0055 supersession) now ALWAYS
takes its dark-shape branch, because `from runtime_observer import observe`
unconditionally raises ModuleNotFoundError. This class proves that dark
shape directly against the real deleted-module condition — no mocking of
runtime_observer (it no longer exists to mock).

Runner: stdlib unittest + pytest compatible.
  python -m pytest tests/test_topology_transcript_960.py -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "dashboard"


def _inject_dashboard() -> None:
    s = str(DASHBOARD_DIR)
    if s not in sys.path:
        sys.path.insert(0, s)


def _make_prd_trail(
    created_at: str = "2026-06-01T09:00:00Z",
    closed_at: str | None = None,
) -> dict:
    """Minimal PRD trail dict with timestamps."""
    return {
        "prd_number": 956,
        "prd_title": "PRD: transcript-sourced execution truth",
        "prd_created_at": created_at,
        "prd_closed_at": closed_at or "",
        "slices": [],
        "prs": {},
        "prd_verdicts": [],
    }


# ---------------------------------------------------------------------------
# ComparisonThread — comparison._apply_runtime_observation() dark-shape proof
# ---------------------------------------------------------------------------

class TestComparisonThread(unittest.TestCase):
    """comparison._apply_runtime_observation() takes the dark-shape branch
    now that the runtime observer module is permanently deleted (AC #7 / PRD #1214
    §2 1e's named exception: capture_liveness/capture_unavailable/
    _observer_error take the capture-unavailable dark shape permanently).
    """

    def setUp(self):
        _inject_dashboard()
        import comparison as cmp
        self._cmp = cmp

    def _build_report_skeleton(self) -> dict:
        """Minimal comparison report skeleton for _apply_runtime_observation."""
        from pipeline_spec import get_spec  # type: ignore[import]
        spec = get_spec()
        return {
            "prd_number": 956,
            "run_pass": False,
            "edges": {
                e["id"]: {
                    "state": "not-evaluated",
                    "detail": "",
                    "evidence": e.get("evidence", "github"),
                    "required": e.get("required", "always"),
                }
                for e in spec.get("edges", [])
            },
            "violations": [],
            "unexpected": [],
        }

    def test_runtime_observer_import_unconditionally_raises(self):
        """Precondition: the runtime observer module is deleted, so the exact import
        comparison.py's try/except performs (line 74: `from runtime_observer
        import observe`) raises with a non-empty message — the value that
        gets stored in the try/except's local `_observer_error`.

        comparison.py itself stays byte-identical (not touched by this
        slice), so `_observer_error` is not re-exposed on the returned
        report dict; this test proves the exception it would catch is real
        and populated, as the closest observable proxy.
        """
        try:
            from runtime_observer import observe  # noqa: F401,PLC0415
        except Exception as exc:
            observer_error = str(exc)
        else:
            self.fail("runtime_observer import must fail — module was deleted (#1218)")

        self.assertTrue(
            observer_error,
            "the ImportError comparison.py's try/except catches must carry "
            "a populated (non-empty) message for _observer_error",
        )

    def test_capture_liveness_false_when_observer_deleted(self):
        """capture_liveness must be False — comparison.py's dark-shape branch
        (byte-identical try/except) fires unconditionally post-deletion."""
        trail = _make_prd_trail(created_at="2026-06-01T09:00:00Z")
        report = self._build_report_skeleton()

        updated = self._cmp._apply_runtime_observation(report, trail)

        self.assertIn("capture_liveness", updated,
                      "capture_liveness key must be present in updated report")
        self.assertFalse(
            updated.get("capture_liveness"),
            f"capture_liveness must be False (observer permanently absent), "
            f"got: {updated.get('capture_liveness')!r}",
        )

    def test_apply_runtime_observation_does_not_crash(self):
        """_apply_runtime_observation() must never crash even though the
        underlying observer module is gone — the try/except is advisory."""
        trail = _make_prd_trail(created_at="2026-06-01T09:00:00Z")
        report = self._build_report_skeleton()

        try:
            updated = self._cmp._apply_runtime_observation(report, trail)
        except Exception as exc:
            self.fail(f"_apply_runtime_observation() raised {type(exc).__name__}: {exc}")

        self.assertIn("runtime_edges", updated)
        self.assertIn("runtime_coverage", updated)
        self.assertIn("coverage_strip", updated)


if __name__ == "__main__":
    unittest.main()
