"""
Regression test for issues #947, #948, #949 — qa-tester.md's production-verify
section must contain three explicit shift-left rules.

Root causes surfaced from the sreality test-drive:
  #947: slice shipped a degenerate ensemble because production-verify checked
        a selection-time mean (CV metric) rather than the FINAL shipped artifact.
  #948: two geo-layer URLs were dead (404/499) but slices only ran offline
        fixture tests — no live probe against the registered endpoint.
  #949: installer script used invalid schtasks flags that passed -DryRun
        (print-only) but errored on real registration.

Fix: add three explicit rules to qa-tester.md's production-verify-mode section.

These tests FAIL on develop before the fix (the rules do not yet exist in
qa-tester.md) and PASS after the fix.

Runner: stdlib unittest — NO top-level pytest import.
  python -m unittest tests.test_prodverify_shift_left_947 -v
"""

import re
import unittest
from pathlib import Path

QA_TESTER_MD = (
    Path(__file__).parent.parent / ".claude" / "pipeline" / "agents" / "qa-tester.md"
)


def _qa_tester_text() -> str:
    return QA_TESTER_MD.read_text(encoding="utf-8")


def _prodverify_section(text: str) -> str:
    """Return the text of the ## Production-verify mode section (and sub-sections).

    Returns text from the first '## Production-verify mode' heading (case-insensitive)
    through to the next top-level '## ' heading, or end of file.  Returns the full
    file if the section marker is not found (so assertions can fail clearly).
    """
    start = re.search(r'^## Production-verify mode', text, re.IGNORECASE | re.MULTILINE)
    if not start:
        return text  # section not found; return full text so assertions produce clear messages
    body_start = start.start()
    # Find the next ## heading (not ###) after our section
    next_section = re.search(r'\n## ', text[start.end():])
    if next_section:
        return text[body_start: start.end() + next_section.start()]
    return text[body_start:]


class TestProdverifyShiftLeft(unittest.TestCase):
    """qa-tester.md production-verify section must contain three shift-left rules."""

    def setUp(self):
        self.text = _qa_tester_text()
        self.pv_section = _prodverify_section(self.text)

    # ------------------------------------------------------------------
    # Rule 1 — verify the SHIPPED artifact, not a proxy (#947)
    # ------------------------------------------------------------------

    def test_shipped_artifact_not_proxy_rule_present(self):
        """production-verify section must document the shipped-artifact-not-proxy rule (#947)."""
        # Grep for key terms: "shipped artifact" or "SHIPPED artifact" or
        # "final canonical artifact" or "proxy" in the context of a rule.
        pattern = re.compile(
            r'(shipped\s+artifact|SHIPPED\s+artifact|final\s+canonical\s+artifact'
            r'|proxy\s+metric|selection.{0,20}proxy'
            r'|not\s+a\s+(proxy|selection.{0,20}metric))',
            re.IGNORECASE,
        )
        self.assertRegex(
            self.pv_section,
            pattern,
            "qa-tester.md's production-verify section must contain the rule that "
            "production-verify exercises the SHIPPED artifact, not a proxy/selection-time "
            "metric (#947: degenerate ensemble passed because CV mean was checked instead "
            "of the shipped canonical-refit metric).",
        )

    def test_issue_947_ref_in_prodverify_section(self):
        """production-verify section must reference issue #947."""
        self.assertIn(
            "#947",
            self.pv_section,
            "qa-tester.md's production-verify section must cite '#947' in or near the "
            "shipped-artifact rule so the root-cause is traceable.",
        )

    # ------------------------------------------------------------------
    # Rule 2 — live-probe registered endpoints (#948)
    # ------------------------------------------------------------------

    def test_live_probe_endpoints_rule_present(self):
        """production-verify section must document the live-probe-endpoints rule (#948)."""
        # The rule must explicitly talk about live-probing a registered/new endpoint.
        # We require both "live" and ("probe" or "request" or "fetch") in close proximity
        # to a word indicating an external URL/endpoint — to avoid matching the existing
        # hook-registration-liveness probe (Step 1b) which is about hook-fire, not URLs.
        # The simpler check is: does #948 appear in the section? (covered by a separate
        # test). For the content check we look for the key concept: a new registered URL
        # must be live-verified — combining "register" (or "new endpoint/URL") with
        # "live" or "real" and "probe" or "request" or "fetch".
        pattern = re.compile(
            r'(register.{0,60}(live|real).{0,30}(probe|request|fetch|reachable)'
            r'|(live|real).{0,30}(probe|request|fetch).{0,60}(endpoint|url|data.source)'
            r'|new.{0,30}(endpoint|url).{0,60}(live|real).{0,30}(probe|request|fetch)'
            r'|offline.{0,30}fixture.{0,30}(not\s+sufficient|insufficient)'
            r'|fixture.{0,30}test.{0,60}(live|real).{0,30}(probe|fetch))',
            re.IGNORECASE | re.DOTALL,
        )
        self.assertRegex(
            self.pv_section,
            pattern,
            "qa-tester.md's production-verify section must contain the rule that a "
            "slice registering a live external endpoint must live-probe it (real HTTP "
            "request, not only offline fixture tests) (#948: 2 of 6 geo URLs were "
            "404/499 — invisible until the closing live fetch).",
        )

    def test_issue_948_ref_in_prodverify_section(self):
        """production-verify section must reference issue #948."""
        self.assertIn(
            "#948",
            self.pv_section,
            "qa-tester.md's production-verify section must cite '#948' in or near the "
            "live-probe-endpoints rule so the root-cause is traceable.",
        )

    # ------------------------------------------------------------------
    # Rule 3 — real-run-smoke for installers/CLIs (#949)
    # ------------------------------------------------------------------

    def test_real_run_smoke_rule_present(self):
        """production-verify section must document the real-run-smoke rule (#949)."""
        pattern = re.compile(
            r'(real.{0,10}run|real.{0,10}smoke|smoke.{0,15}run'
            r'|DryRun|dry.run|print.only'
            r'|real\s+execution|genuine\s+smoke)',
            re.IGNORECASE,
        )
        self.assertRegex(
            self.pv_section,
            pattern,
            "qa-tester.md's production-verify section must contain the rule that "
            "installer/CLI slices must REAL-RUN the command (genuine smoke execution), "
            "not -DryRun/print-only (#949: invalid schtasks flags passed dry-run "
            "but errored on real registration).",
        )

    def test_issue_949_ref_in_prodverify_section(self):
        """production-verify section must reference issue #949."""
        self.assertIn(
            "#949",
            self.pv_section,
            "qa-tester.md's production-verify section must cite '#949' in or near the "
            "real-run-smoke rule so the root-cause is traceable.",
        )


if __name__ == "__main__":
    unittest.main()
