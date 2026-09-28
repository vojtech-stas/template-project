"""
tests/test_meta_tripwire_840.py

Regression tests for slice #840 — promotion meta-tripwire (ADR-0070 D4):
  1. check_meta_tripwire() — FAIL on guardrail-path touch without promotion-ack;
     PASS with ack or no guardrail touch.
  2. check_r_sensitive_detector() — rewritten to count guardrail-touching promotions
     and their ack status; detail mentions "guardrail-touching promotions".
  3. check_release_ready() condition (f) — wired to check_meta_tripwire(); no longer
     a stub.
  4. reviewer.md R-SENSITIVE section — one-line pointer; grep acceptance criterion.

Per ADR-0067 D2: this test file is committed BEFORE the implementation.
These tests FAIL before the implementation and PASS after.

Runner: stdlib unittest + pytest compatible.
  python -m pytest tests/test_meta_tripwire_840.py -v
"""

import importlib
import json
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
HEALTH_PY = REPO_ROOT / "dashboard" / "health.py"
REVIEWER_MD = REPO_ROOT / ".claude" / "pipeline" / "agents" / "reviewer.md"

# Ensure dashboard/ is importable.
_DASHBOARD_DIR = str(REPO_ROOT / "dashboard")
if _DASHBOARD_DIR not in sys.path:
    sys.path.insert(0, _DASHBOARD_DIR)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_health():
    """Reload health module to pick up current code state."""
    import health as _h
    importlib.reload(_h)
    return _h


def _call_release_ready(env_overrides: dict | None = None) -> dict:
    """Call check_release_ready() with env-var injection (fast-path)."""
    defaults = {
        "_RELEASE_READY_CI_RESULT": "PASS",
        "_RELEASE_READY_TESTS_RESULT": "PASS",
        "_RELEASE_READY_PROOF_INTEGRITY_RESULT": "PASS",
        "_RELEASE_READY_STREAK_RESULT": "PASS",
        "_RELEASE_READY_NEEDS_HUMAN_COUNT": "0",
    }
    env_patch = {**defaults, **(env_overrides or {})}
    old_vals = {}
    for k, v in env_patch.items():
        old_vals[k] = os.environ.get(k)
        os.environ[k] = v
    try:
        import health as _h
        importlib.reload(_h)
        return _h.check_release_ready()
    finally:
        for k, old_v in old_vals.items():
            if old_v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = old_v


# ---------------------------------------------------------------------------
# Group 1: check_meta_tripwire() — function exists + returns correct id
# ---------------------------------------------------------------------------

class TestMetaTripwireExists(unittest.TestCase):
    """check_meta_tripwire() must exist and return a well-formed dict."""

    def test_function_exists(self):
        """health.check_meta_tripwire must be callable."""
        h = _load_health()
        self.assertTrue(
            callable(getattr(h, "check_meta_tripwire", None)),
            "health.check_meta_tripwire must exist and be callable (ADR-0070 D4).",
        )

    def test_returns_dict_with_id(self):
        """check_meta_tripwire() must return a dict with id='META-TRIPWIRE'."""
        h = _load_health()
        result = h.check_meta_tripwire()
        self.assertIsInstance(result, dict, "check_meta_tripwire() must return a dict")
        self.assertEqual(
            result.get("id"), "META-TRIPWIRE",
            f"check_meta_tripwire() must return id='META-TRIPWIRE'; "
            f"got: {result.get('id')!r}",
        )

    def test_result_field_is_pass_warn_or_fail(self):
        """check_meta_tripwire() result must be one of PASS/WARN/FAIL."""
        h = _load_health()
        result = h.check_meta_tripwire()
        self.assertIn(
            result.get("result", ""),
            {"PASS", "WARN", "FAIL"},
            f"check_meta_tripwire() result must be PASS/WARN/FAIL; "
            f"got: {result.get('result')!r}",
        )


# ---------------------------------------------------------------------------
# Group 2: check_meta_tripwire() — injection: FAIL without promotion-ack
# ---------------------------------------------------------------------------

class TestMetaTripwireFailsWithoutAck(unittest.TestCase):
    """A simulated guardrail-path change without promotion-ack must FAIL."""

    def setUp(self):
        """Store existing env var value."""
        self._old = os.environ.get("_META_TRIPWIRE_RESULT_OVERRIDE")

    def tearDown(self):
        """Restore env var."""
        if self._old is None:
            os.environ.pop("_META_TRIPWIRE_RESULT_OVERRIDE", None)
        else:
            os.environ["_META_TRIPWIRE_RESULT_OVERRIDE"] = self._old

    def test_fail_via_injection(self):
        """Setting _META_TRIPWIRE_RESULT_OVERRIDE=FAIL must return result=FAIL."""
        os.environ["_META_TRIPWIRE_RESULT_OVERRIDE"] = "FAIL"
        h = _load_health()
        result = h.check_meta_tripwire()
        self.assertEqual(
            result.get("result"), "FAIL",
            f"check_meta_tripwire() must return FAIL when "
            f"_META_TRIPWIRE_RESULT_OVERRIDE=FAIL; got: {result.get('result')!r}",
        )

    def test_fail_detail_mentions_guardrail(self):
        """FAIL result detail must mention guardrail to aid diagnosis."""
        os.environ["_META_TRIPWIRE_RESULT_OVERRIDE"] = "FAIL"
        h = _load_health()
        result = h.check_meta_tripwire()
        detail = result.get("detail", "")
        self.assertTrue(
            "guardrail" in detail.lower() or "promotion-ack" in detail.lower(),
            f"FAIL detail must mention 'guardrail' or 'promotion-ack'; "
            f"got: {detail!r}",
        )


# ---------------------------------------------------------------------------
# Group 3: check_meta_tripwire() — injection: PASS with ack / no guardrail touch
# ---------------------------------------------------------------------------

class TestMetaTripwirePassesWithAckOrClean(unittest.TestCase):
    """PASS when injection says PASS (ack present or no guardrail touch)."""

    def setUp(self):
        self._old = os.environ.get("_META_TRIPWIRE_RESULT_OVERRIDE")

    def tearDown(self):
        if self._old is None:
            os.environ.pop("_META_TRIPWIRE_RESULT_OVERRIDE", None)
        else:
            os.environ["_META_TRIPWIRE_RESULT_OVERRIDE"] = self._old

    def test_pass_via_injection(self):
        """Setting _META_TRIPWIRE_RESULT_OVERRIDE=PASS must return result=PASS."""
        os.environ["_META_TRIPWIRE_RESULT_OVERRIDE"] = "PASS"
        h = _load_health()
        result = h.check_meta_tripwire()
        self.assertEqual(
            result.get("result"), "PASS",
            f"check_meta_tripwire() must return PASS when "
            f"_META_TRIPWIRE_RESULT_OVERRIDE=PASS; got: {result.get('result')!r}",
        )


# ---------------------------------------------------------------------------
# Group 4: check_meta_tripwire() — registered in CHECK_REGISTRY
# ---------------------------------------------------------------------------

class TestMetaTripwireRegistered(unittest.TestCase):
    """META-TRIPWIRE must be registered in the CHECK_REGISTRY."""

    def test_registered_in_check_registry(self):
        """CHECK_REGISTRY must include 'META-TRIPWIRE' key."""
        h = _load_health()
        registry = getattr(h, "CHECK_REGISTRY", {})
        self.assertIn(
            "META-TRIPWIRE",
            registry,
            "CHECK_REGISTRY must include 'META-TRIPWIRE' (ADR-0070 D4 / ADR-0064 D3).",
        )

    def test_cli_exit_0(self):
        """python dashboard/health.py --check META-TRIPWIRE must exit 0.

        Test-isolation (reviewer round-1 finding on PR #1045): this test
        exercises the CLI-exit-0-on-PASS mechanics of the --check flag, NOT
        live promotion/guardrail state. Without injecting
        _META_TRIPWIRE_RESULT_OVERRIDE, this subprocess reads the REAL
        workflow-events.jsonl + git log against whatever guardrail-touching
        commits happen to be unpromoted on the branch under test — which is
        exactly what slice #1041's own .githooks/* change triggers (FAIL,
        not PASS). Inject PASS via env so this test is independent of live
        promotion history, matching the pattern already used by
        TestMetaTripwireFailsWithoutAck / TestMetaTripwirePassesWithAckOrClean
        above (in-process) — here via subprocess env since --check spawns a
        fresh process. The live (uninjected) check_meta_tripwire() still
        fires correctly at real promotion time (RELEASE-READY condition f).
        """
        env = os.environ.copy()
        env["_META_TRIPWIRE_RESULT_OVERRIDE"] = "PASS"
        result = subprocess.run(
            [sys.executable, str(HEALTH_PY), "--check", "META-TRIPWIRE"],
            capture_output=True, text=True, env=env,
            cwd=str(REPO_ROOT), timeout=30,
        )
        self.assertEqual(
            result.returncode, 0,
            f"--check META-TRIPWIRE must exit 0; "
            f"got {result.returncode}. stderr: {result.stderr[:200]!r}",
        )


# ---------------------------------------------------------------------------
# Group 5: check_r_sensitive_detector() — rewritten to mention guardrail promotions
# ---------------------------------------------------------------------------

class TestRSensitiveDetectorRewritten(unittest.TestCase):
    """check_r_sensitive_detector() must mention guardrail-touching promotions."""

    def _run_check(self) -> dict:
        script = f"""
import sys, json
sys.path.insert(0, r'{_DASHBOARD_DIR}')
from health import check_r_sensitive_detector
print(json.dumps(check_r_sensitive_detector()))
"""
        proc = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True, text=True,
            cwd=str(REPO_ROOT), timeout=45,
        )
        self.assertEqual(0, proc.returncode, f"check script failed: {proc.stderr[:300]}")
        return json.loads(proc.stdout.strip())

    def test_detail_mentions_guardrail_touching_promotions(self):
        """R-SENSITIVE-DETECTOR detail must mention 'guardrail-touching promotions'
        (rewritten per ADR-0070 D4 from per-PR advisory to per-promotion counter).
        """
        result = self._run_check()
        detail = result.get("detail", "")
        self.assertIn(
            "guardrail",
            detail.lower(),
            f"R-SENSITIVE-DETECTOR detail must mention 'guardrail' "
            f"(rewritten to count guardrail-touching promotions per ADR-0070 D4); "
            f"got: {detail!r}",
        )

    def test_result_is_warn(self):
        """R-SENSITIVE-DETECTOR must always return WARN (advisory)."""
        result = self._run_check()
        self.assertEqual(
            result.get("result"), "WARN",
            f"R-SENSITIVE-DETECTOR must always return WARN (advisory); "
            f"got: {result.get('result')!r}",
        )

    def test_id_is_correct(self):
        """R-SENSITIVE-DETECTOR must return id='R-SENSITIVE-DETECTOR'."""
        result = self._run_check()
        self.assertEqual(
            result.get("id"), "R-SENSITIVE-DETECTOR",
            f"R-SENSITIVE-DETECTOR must return id='R-SENSITIVE-DETECTOR'; "
            f"got: {result.get('id')!r}",
        )


# ---------------------------------------------------------------------------
# Group 6: check_release_ready() condition (f) — wired to check_meta_tripwire()
# ---------------------------------------------------------------------------

class TestReleaseReadyConditionF(unittest.TestCase):
    """Condition (f) must now call check_meta_tripwire(), not a stub."""

    def setUp(self):
        self._old_mt = os.environ.get("_META_TRIPWIRE_RESULT_OVERRIDE")

    def tearDown(self):
        if self._old_mt is None:
            os.environ.pop("_META_TRIPWIRE_RESULT_OVERRIDE", None)
        else:
            os.environ["_META_TRIPWIRE_RESULT_OVERRIDE"] = self._old_mt

    def test_condition_f_blocks_when_meta_tripwire_fails(self):
        """Gate must hold on condition (f) when check_meta_tripwire() returns FAIL."""
        os.environ["_META_TRIPWIRE_RESULT_OVERRIDE"] = "FAIL"
        r = _call_release_ready()
        self.assertNotEqual(
            r.get("result"), "PASS",
            f"RELEASE-READY must not PASS when meta-tripwire FAIL; "
            f"got result={r.get('result')!r}, detail={r.get('detail')!r}",
        )
        # first_failing_condition must be 'f'
        self.assertEqual(
            r.get("first_failing_condition"), "f",
            f"first_failing_condition must be 'f' when meta-tripwire FAIL; "
            f"got: {r.get('first_failing_condition')!r}",
        )

    def test_condition_f_passes_when_meta_tripwire_passes(self):
        """Gate must not block on (f) when check_meta_tripwire() returns PASS."""
        os.environ["_META_TRIPWIRE_RESULT_OVERRIDE"] = "PASS"
        r = _call_release_ready()
        # (f) should not be the first failing condition
        self.assertNotEqual(
            r.get("first_failing_condition"), "f",
            f"Condition (f) must not block when meta-tripwire PASS; "
            f"got first_failing={r.get('first_failing_condition')!r}",
        )

    def test_condition_f_no_longer_stub(self):
        """check_release_ready() must not mention '#840' stub/deferred in (f) detail."""
        os.environ["_META_TRIPWIRE_RESULT_OVERRIDE"] = "PASS"
        r = _call_release_ready()
        condition_f_note = r.get("condition_f", "")
        detail = r.get("detail", "")
        combined = (condition_f_note + " " + detail).lower()
        self.assertNotIn(
            "stub",
            combined,
            f"condition (f) must no longer say 'stub' — #840 wires the real check; "
            f"got condition_f={condition_f_note!r}, detail={detail!r}",
        )

    def test_detail_mentions_meta_tripwire_or_guardrail(self):
        """PASS detail must mention meta-tripwire or guardrail for (f)."""
        os.environ["_META_TRIPWIRE_RESULT_OVERRIDE"] = "PASS"
        r = _call_release_ready()
        if r.get("result") == "PASS":
            detail = r.get("detail", "")
            self.assertTrue(
                "guardrail" in detail.lower()
                or "meta-tripwire" in detail.lower()
                or "tripwire" in detail.lower(),
                f"PASS detail must mention guardrail/meta-tripwire for condition (f); "
                f"got: {detail!r}",
            )


# ---------------------------------------------------------------------------
# Group 7: check_release_ready() old-stub test updated — #840 wired
# ---------------------------------------------------------------------------

class TestConditionFNoLongerStubbed(unittest.TestCase):
    """Condition (f) stub is now wired; the field must NOT say 'stub' / 'deferred to #840'.

    This supersedes the test_detail_or_field_acknowledges_stub test from
    test_release_ready_838.py which was correct when (f) was a stub.
    """

    def setUp(self):
        self._old_mt = os.environ.get("_META_TRIPWIRE_RESULT_OVERRIDE")

    def tearDown(self):
        if self._old_mt is None:
            os.environ.pop("_META_TRIPWIRE_RESULT_OVERRIDE", None)
        else:
            os.environ["_META_TRIPWIRE_RESULT_OVERRIDE"] = self._old_mt

    def test_no_stub_marker_in_condition_f_note(self):
        """condition_f field must not say 'stub' or 'deferred to #840'."""
        os.environ["_META_TRIPWIRE_RESULT_OVERRIDE"] = "PASS"
        r = _call_release_ready()
        condition_f_note = r.get("condition_f", "")
        self.assertNotIn(
            "stub",
            condition_f_note.lower(),
            f"condition_f note must not say 'stub' — #840 wired the real check; "
            f"got: {condition_f_note!r}",
        )
        self.assertNotIn(
            "#840",
            condition_f_note,
            f"condition_f note must not reference '#840' deferred stub; "
            f"got: {condition_f_note!r}",
        )


# ---------------------------------------------------------------------------
# Group 8: reviewer.md R-SENSITIVE acceptance criterion
# ---------------------------------------------------------------------------

class TestReviewerMdRSensitive(unittest.TestCase):
    """reviewer.md R-SENSITIVE section must be a one-line pointer to the
    promotion meta-tripwire; the old rule body must be retired.

    Acceptance criterion (verbatim from slice #840):
        grep 'R-SENSITIVE' .claude/agents/reviewer.md |
          grep -v 'retired|tripwire' | wc -l
        == 0

    Every line containing 'R-SENSITIVE' must also contain 'retired' or 'tripwire'.
    """

    def _reviewer_lines_with_rsensitive(self) -> list[str]:
        text = REVIEWER_MD.read_text(encoding="utf-8")
        return [ln for ln in text.splitlines() if "R-SENSITIVE" in ln]

    def test_rsensitive_lines_all_contain_retired_or_tripwire(self):
        """Every 'R-SENSITIVE' line must contain 'retired' or 'tripwire'."""
        lines = self._reviewer_lines_with_rsensitive()
        failing = [
            ln for ln in lines
            if "retired" not in ln.lower() and "tripwire" not in ln.lower()
        ]
        self.assertEqual(
            failing, [],
            f"These R-SENSITIVE lines lack 'retired' or 'tripwire':\n"
            + "\n".join(f"  {ln!r}" for ln in failing)
            + "\n(Acceptance criterion: grep 'R-SENSITIVE' reviewer.md | "
            + "grep -v 'retired|tripwire' | wc -l == 0)",
        )

    def test_rsensitive_section_still_exists(self):
        """'### R-SENSITIVE' heading must still exist (slot preserved, body retired)."""
        text = REVIEWER_MD.read_text(encoding="utf-8")
        self.assertIn(
            "### R-SENSITIVE",
            text,
            "reviewer.md must still have a '### R-SENSITIVE' section heading.",
        )

    def test_rsensitive_references_adr_0070(self):
        """The R-SENSITIVE section must reference ADR-0070 (meta-tripwire authority)."""
        text = REVIEWER_MD.read_text(encoding="utf-8")
        start = text.find("### R-SENSITIVE")
        if start == -1:
            self.fail("### R-SENSITIVE not found in reviewer.md")
        # Find section end
        next_section = text.find("### R-", start + len("### R-SENSITIVE"))
        section = text[start:next_section] if next_section != -1 else text[start:]
        self.assertIn(
            "ADR-0070",
            section,
            "R-SENSITIVE section must reference ADR-0070 (the meta-tripwire ADR).",
        )


if __name__ == "__main__":
    unittest.main()
