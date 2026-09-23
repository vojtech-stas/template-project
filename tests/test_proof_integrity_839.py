"""
Regression tests for slice #839 — PROOF-INTEGRITY health check.

ADR-0070 D5: for browser-route proofs, assert the claimed string appears in
captured rendered-DOM inner_text (NOT API JSON — the #811/#833 class shipped
because API-layer proof passed while the DOM was empty).

Three test groups (ADR-0067 D2 test-first ordering):
  1. PROOF-INTEGRITY FAILs on a fixture proof that has the claimed string only
     in API JSON / empty DOM (no inner_text: assertion).
  2. PROOF-INTEGRITY PASSes on a fixture proof with DOM inner_text: attestation.
  3. PROOF-INTEGRITY WARNs honestly when there is no qualifying data.

All assertions run the check via subprocess with env-var injection so we never
need network access or a running dashboard — fully deterministic.

Runner: stdlib unittest + pytest compatible.
  python -m pytest tests/test_proof_integrity_839.py -v
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# ---------------------------------------------------------------------------
# Repo root + paths
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).parent.parent
HEALTH_PY = REPO_ROOT / "dashboard" / "health.py"


# ---------------------------------------------------------------------------
# Helper: run check_proof_integrity() via subprocess with env-var injection.
# ---------------------------------------------------------------------------

def _run_proof_integrity_check(
    pr_data_override: list | None = None,
    no_data: bool = False,
) -> dict:
    """Invoke check_proof_integrity() through a subprocess.

    - pr_data_override: list of dicts, each representing a synthetic PR with
      keys: ``number``, ``headRefName``, ``labels``, ``files``, ``body``,
      ``comments``.  Injected via _PROOF_INTEGRITY_PR_OVERRIDE env var as JSON.
    - no_data: if True, inject an empty list (simulates no qualifying PRs).
    """
    env = os.environ.copy()

    if no_data:
        env["_PROOF_INTEGRITY_PR_OVERRIDE"] = json.dumps([])
    elif pr_data_override is not None:
        env["_PROOF_INTEGRITY_PR_OVERRIDE"] = json.dumps(pr_data_override)
    # else: no override — check uses real network (not used in unit tests)

    script = f"""
import sys
sys.path.insert(0, r'{REPO_ROOT / "dashboard"}')
from health import check_proof_integrity
import json
result = check_proof_integrity()
print(json.dumps(result))
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(REPO_ROOT / "dashboard"),
    )
    if result.returncode not in (0, 1):
        raise RuntimeError(
            f"check_proof_integrity() subprocess error (exit {result.returncode}):\n"
            f"STDOUT: {result.stdout}\nSTDERR: {result.stderr}"
        )
    return json.loads(result.stdout.strip())


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

def _make_browser_pr(
    number: int,
    has_inner_text: bool,
    has_api_json_only: bool = False,
    has_fixture_source: bool = False,
    empty_env: bool = False,
) -> dict:
    """Build a synthetic browser-route PR dict for injection.

    - has_inner_text: includes ``inner_text: Health`` in the PR body
    - has_api_json_only: body only has JSON-shaped API response (no inner_text)
    - has_fixture_source: PROOF_SOURCE contains 'fixture' substring
    - empty_env: ENV: field is empty/absent
    """
    if has_inner_text:
        body = (
            "## Verification\n"
            "inner_text: Health\n"
            "screenshot: /qa-proof/839/health.png\n"
            "PRODUCTION_VERIFY: PASS\n"
            "PROOF_SOURCE: real-session-abc123@2026-06-17T10:00:00Z\n"
            "ENV: abc1234567890abcdef@2026-06-17T09:55:00Z\n"
        )
    elif has_api_json_only:
        # API JSON present but no inner_text assertion — the #811/#833 failure class
        body = (
            "## Verification\n"
            '{"result": "ok", "checks": [{"id": "HEALTH", "result": "PASS"}]}\n'
            "screenshot: /qa-proof/839/health.png\n"
            "PRODUCTION_VERIFY: PASS\n"
            "PROOF_SOURCE: real-session-abc123@2026-06-17T10:00:00Z\n"
            "ENV: abc1234567890abcdef@2026-06-17T09:55:00Z\n"
        )
    elif has_fixture_source:
        body = (
            "## Verification\n"
            "inner_text: Health\n"
            "screenshot: /qa-proof/839/health.png\n"
            "PRODUCTION_VERIFY: PASS\n"
            "PROOF_SOURCE: fixture-session@2026-06-17T10:00:00Z\n"
            "ENV: abc1234567890abcdef@2026-06-17T09:55:00Z\n"
        )
    elif empty_env:
        body = (
            "## Verification\n"
            "inner_text: Health\n"
            "screenshot: /qa-proof/839/health.png\n"
            "PRODUCTION_VERIFY: PASS\n"
            "PROOF_SOURCE: real-session-abc123@2026-06-17T10:00:00Z\n"
            "ENV:\n"
        )
    else:
        body = (
            "## Verification\n"
            "inner_text: Health\n"
            "PRODUCTION_VERIFY: PASS\n"
            "PROOF_SOURCE: real-session@2026-06-17T10:00:00Z\n"
            "ENV: deadbeef1234@2026-06-17T09:00:00Z\n"
        )

    return {
        "number": number,
        "headRefName": f"feat/{number}-some-feature",
        "labels": [],
        "files": [{"path": "site/preview.html"}],
        "body": body,
        "comments": [],
    }


# ---------------------------------------------------------------------------
# Group 1: PROOF-INTEGRITY FAILs on API-only / empty-DOM proof
# ---------------------------------------------------------------------------

class TestProofIntegrityFailsOnApiOnlyProof(unittest.TestCase):
    """PROOF-INTEGRITY must FAIL when a browser-route PR has no inner_text:
    assertion — only an API JSON response or a screenshot-only proof.

    This is the #811/#833 failure class: the PR body has an API-layer response
    (rendered as a JSON block or plain JSON string) and a screenshot, but no
    inner_text: line that would confirm the rendered DOM contained the expected text.
    """

    def test_fail_on_api_json_only_proof(self):
        """FAIL when browser-route PR has only API JSON in body (no inner_text:)."""
        pr = _make_browser_pr(900, has_inner_text=False, has_api_json_only=True)
        result = _run_proof_integrity_check(pr_data_override=[pr])
        self.assertEqual(
            "FAIL",
            result["result"],
            msg=(
                f"Expected FAIL for API-only browser proof but got "
                f"{result['result']}: {result.get('detail')}"
            ),
        )
        # Detail should explain the DOM-attestation failure
        detail = result.get("detail", "")
        self.assertTrue(
            "inner_text" in detail.lower() or "dom" in detail.lower(),
            msg=f"FAIL detail should mention inner_text or dom: {detail}",
        )

    def test_fail_on_fixture_tagged_proof_source(self):
        """FAIL when PROOF_SOURCE contains 'fixture' (rule #21 violation)."""
        pr = _make_browser_pr(901, has_inner_text=False, has_fixture_source=True)
        result = _run_proof_integrity_check(pr_data_override=[pr])
        self.assertEqual(
            "FAIL",
            result["result"],
            msg=(
                f"Expected FAIL for fixture-tagged PROOF_SOURCE but got "
                f"{result['result']}: {result.get('detail')}"
            ),
        )

    def test_fail_on_empty_env_field(self):
        """FAIL when ENV: field is empty/absent (no sha freshness attestation)."""
        pr = _make_browser_pr(902, has_inner_text=False, empty_env=True)
        result = _run_proof_integrity_check(pr_data_override=[pr])
        self.assertEqual(
            "FAIL",
            result["result"],
            msg=(
                f"Expected FAIL for empty ENV: field but got "
                f"{result['result']}: {result.get('detail')}"
            ),
        )


# ---------------------------------------------------------------------------
# Group 2: PROOF-INTEGRITY PASSes on DOM-inner_text-attested proof
# ---------------------------------------------------------------------------

class TestProofIntegrityPassesOnDomAttestedProof(unittest.TestCase):
    """PROOF-INTEGRITY must PASS when browser-route PR has inner_text: attestation
    with a live non-fixture PROOF_SOURCE and non-empty ENV sha.
    """

    def test_pass_on_inner_text_attested_proof(self):
        """PASS when browser-route PR body contains inner_text: assertion."""
        pr = _make_browser_pr(910, has_inner_text=True)
        result = _run_proof_integrity_check(pr_data_override=[pr])
        self.assertEqual(
            "PASS",
            result["result"],
            msg=(
                f"Expected PASS for DOM-attested proof but got "
                f"{result['result']}: {result.get('detail')}"
            ),
        )

    def test_pass_on_multiple_prs_all_attested(self):
        """PASS when multiple browser-route PRs all have inner_text: attestation."""
        prs = [_make_browser_pr(911 + i, has_inner_text=True) for i in range(3)]
        result = _run_proof_integrity_check(pr_data_override=prs)
        self.assertEqual(
            "PASS",
            result["result"],
            msg=(
                f"Expected PASS for multiple attested PRs but got "
                f"{result['result']}: {result.get('detail')}"
            ),
        )

    def test_pass_inner_text_in_comment(self):
        """PASS when inner_text: appears in a PR comment rather than body."""
        pr = {
            "number": 920,
            "headRefName": "feat/920-dashboard-feature",
            "labels": [],
            "files": [{"path": "site/preview.html"}],
            "body": (
                "## Verification\n"
                "screenshot: /qa-proof/920/health.png\n"
                "PRODUCTION_VERIFY: PASS\n"
                "PROOF_SOURCE: real-session-xyz789@2026-06-17T11:00:00Z\n"
                "ENV: deadbeef0000@2026-06-17T10:55:00Z\n"
            ),
            "comments": [
                {
                    "body": (
                        "inner_text: Workflow Dashboard loaded\n"
                        "Rendered DOM confirms the health tab is visible."
                    )
                }
            ],
        }
        result = _run_proof_integrity_check(pr_data_override=[pr])
        self.assertEqual(
            "PASS",
            result["result"],
            msg=(
                f"Expected PASS when inner_text: is in a comment but got "
                f"{result['result']}: {result.get('detail')}"
            ),
        )


# ---------------------------------------------------------------------------
# Group 3: PROOF-INTEGRITY WARNs honestly on no-data
# ---------------------------------------------------------------------------

class TestProofIntegrityHonestWarn(unittest.TestCase):
    """PROOF-INTEGRITY must WARN (not FAIL) when there are no qualifying
    browser-route PRs to evaluate — honest day-one behavior.
    """

    def test_warn_when_no_qualifying_prs(self):
        """WARN when the PR list is empty (no qualifying browser-route PRs)."""
        result = _run_proof_integrity_check(no_data=True)
        self.assertEqual(
            "WARN",
            result["result"],
            msg=(
                f"Expected WARN when no qualifying PRs but got "
                f"{result['result']}: {result.get('detail')}"
            ),
        )
        # Detail should indicate no data found
        detail = result.get("detail", "")
        self.assertTrue(
            len(detail) > 0,
            msg="WARN result should include a non-empty detail message",
        )

    def test_warn_detail_is_informative(self):
        """WARN detail explains that no qualifying data was found."""
        result = _run_proof_integrity_check(no_data=True)
        detail = result.get("detail", "")
        # Should mention no data / no PRs found
        self.assertTrue(
            any(kw in detail.lower() for kw in ("no", "found", "data", "qualifying")),
            msg=f"WARN detail should be informative about missing data: {detail}",
        )

    def test_non_browser_route_prs_are_skipped(self):
        """WARN when PRs are non-browser-route (static/docs only) — no browser PRs to check."""
        pr = {
            "number": 930,
            "headRefName": "docs/930-update-readme",
            "labels": [],
            "files": [{"path": "README.md"}],
            "body": "## Verification\ngrep count=5\n",
            "comments": [],
        }
        result = _run_proof_integrity_check(pr_data_override=[pr])
        # Non-browser PRs should be skipped; result should be WARN (no browser PRs found)
        self.assertEqual(
            "WARN",
            result["result"],
            msg=(
                f"Expected WARN when only non-browser PRs present but got "
                f"{result['result']}: {result.get('detail')}"
            ),
        )


# ---------------------------------------------------------------------------
# Group 4: Check is registered in CHECK_REGISTRY
# ---------------------------------------------------------------------------

class TestProofIntegrityRegistration(unittest.TestCase):
    """PROOF-INTEGRITY must be registered in CHECK_REGISTRY and listable via --list."""

    def test_check_id_in_registry_list(self):
        """--list output must include PROOF-INTEGRITY."""
        result = subprocess.run(
            [sys.executable, str(HEALTH_PY), "--list"],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        self.assertEqual(
            0,
            result.returncode,
            msg=f"--list failed (exit {result.returncode}): {result.stderr}",
        )
        self.assertIn(
            "PROOF-INTEGRITY",
            result.stdout,
            msg="PROOF-INTEGRITY must appear in --list output",
        )

    def test_check_runs_via_cli(self):
        """--check PROOF-INTEGRITY must run without error (exit 0 or 1, not 2)."""
        result = subprocess.run(
            [sys.executable, str(HEALTH_PY), "--check", "PROOF-INTEGRITY"],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        self.assertNotEqual(
            2,
            result.returncode,
            msg=(
                f"--check PROOF-INTEGRITY exited 2 (unknown ID or bad args): "
                f"{result.stderr}"
            ),
        )


if __name__ == "__main__":
    unittest.main()
