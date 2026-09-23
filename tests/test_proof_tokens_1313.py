"""
tests/test_proof_tokens_1313.py

Regression tests for slice #1313 — evidence-shaped proof tokens (ADR-0083 D4).

The `hook-fire` proof-token class previously matched the ordinary PROSE used to
*describe* hook evidence (the log filename, the route name) rather than a
shape only a real beacon record produces. `_pr_has_proof_token`'s
any-over-classes loop also let a PR satisfy a multi-class route union by
matching only ONE class, contradicting ADR-0061 D1's own conjunctive-union
rule ("A change matching multiple globs takes the union of proof classes").

Three-legged fixture corpus (ADR-0083 D4 Enforcement), asserted in every
direction the decision has:
  1. reference-prose corpus -> UNSATISFIED (hook-fire class not matched by
     prose that merely refers to the evidence) -- D4(a)'s tightening leg
     (PRD #1266 criterion 10).
  2. pasted-artifact corpus -> SATISFIED (a genuine pasted beacon line still
     matches) -- D4(a)'s counterweight leg, so tightening cannot become
     unfalsifiable (PRD #1266 criterion 11).
  3. partial-union corpus -> UNSATISFIED, naming the unmatched class, both at
     the raw-function level and in check_proof_presence's own detail string
     -- D4(b) (conjunctive union) and D4(c) (name what was missed) together
     (PRD #1266 criteria 12, 13).

Per the ADR-0083 D4 Enforcement note ("the corpus commits before the fix and
fails against c380bb3 on all three legs"), this test file's commit precedes
the health.py retokenization commit (REG-002-style test-first ordering) and
MUST FAIL against the pre-#1313 codebase (bare-substring hook-fire tokens;
any-over-classes union) before that commit lands.

Runner: stdlib unittest; pytest compatible.
  python -m pytest tests/test_proof_tokens_1313.py -v
"""

import importlib
import json
import os
import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DASHBOARD_DIR = _REPO_ROOT / "dashboard"
if str(_DASHBOARD_DIR) not in sys.path:
    sys.path.insert(0, str(_DASHBOARD_DIR))


def _health():
    """Import (or re-import) health fresh, so no module-level state leaks
    between tests that touch environment variables."""
    if "health" in sys.modules:
        import health
        importlib.reload(health)
        return health
    import health
    return health


# ---------------------------------------------------------------------------
# Leg 1 — reference-prose corpus: describing the evidence is not the evidence.
# ---------------------------------------------------------------------------

class TestReferenceProseIsNotProof(unittest.TestCase):
    """D4(a) tightening leg (criterion 10): prose that merely names or
    describes hook-fire evidence must NOT satisfy the hook-fire class."""

    def test_filename_reference_is_not_proof(self):
        health = _health()
        unsatisfied = health._pr_has_proof_token(
            "written to .claude/logs/hook-fires.jsonl", [], {"hook-fire"}
        )
        self.assertEqual({"hook-fire"}, unsatisfied)

    def test_route_name_reference_is_not_proof(self):
        health = _health()
        unsatisfied = health._pr_has_proof_token(
            "the hook-fire route", [], {"hook-fire"}
        )
        self.assertEqual({"hook-fire"}, unsatisfied)

    def test_vague_log_reference_is_not_proof(self):
        health = _health()
        unsatisfied = health._pr_has_proof_token(
            "see the beacon log", [], {"hook-fire"}
        )
        self.assertEqual({"hook-fire"}, unsatisfied)


# ---------------------------------------------------------------------------
# Leg 2 — pasted-artifact corpus: real evidence must still be recognized.
# ---------------------------------------------------------------------------

class TestPastedBeaconLineIsProof(unittest.TestCase):
    """D4(a) counterweight leg (criterion 11): a genuine pasted beacon line
    must still satisfy the hook-fire class -- tightening must not become
    unfalsifiable against real evidence."""

    def test_pasted_ok_beacon_line_is_proof(self):
        health = _health()
        beacon = '{"hook":"stop-reviewer-gate","status":"ok","ts":"2026-09-15T10:00:00Z"}'
        unsatisfied = health._pr_has_proof_token(beacon, [], {"hook-fire"})
        self.assertEqual(set(), unsatisfied)

    def test_pasted_error_beacon_line_is_proof(self):
        health = _health()
        beacon = '{"hook":"pre-tool-edit","status":"ERROR","ts":"2026-09-15T10:00:01Z"}'
        unsatisfied = health._pr_has_proof_token(beacon, [], {"hook-fire"})
        self.assertEqual(set(), unsatisfied)

    def test_beacon_in_comment_counts_as_proof(self):
        """A pasted beacon line in a PR comment (not just the body) counts."""
        health = _health()
        beacon = '{"hook":"stop-reviewer-gate","status":"ok","ts":"2026-09-15T10:00:00Z"}'
        unsatisfied = health._pr_has_proof_token(
            "no proof in body", [beacon], {"hook-fire"}
        )
        self.assertEqual(set(), unsatisfied)


# ---------------------------------------------------------------------------
# Leg 3 — partial-union corpus: conjunctive union + name-the-miss.
# ---------------------------------------------------------------------------

class TestPartialUnionNamesUnsatisfiedClass(unittest.TestCase):
    """D4(b) conjunctive union + D4(c) name-the-miss (criteria 12-13): a PR
    satisfying only ONE class of a multi-class route union must be reported
    as unsatisfied, naming exactly the class that was missed -- both at the
    raw-function level and in check_proof_presence's own detail string."""

    def test_raw_function_names_unsatisfied_class_only(self):
        """'exit=0' satisfies command-run but not browser; the union must
        report browser as unsatisfied, not treat the PR as fully proven."""
        health = _health()
        unsatisfied = health._pr_has_proof_token(
            "ran it, exit=0", [], {"browser", "command-run"}
        )
        self.assertEqual({"browser"}, unsatisfied)

    def test_check_proof_presence_detail_names_unsatisfied_class(self):
        """check_proof_presence's detail must name the missed class, not just
        the bare PR number (ADR-0083 D4(c))."""
        health = _health()
        pr = {
            "number": 1230,
            "headRefName": "feat/1230-something",
            "labels": [],
            "files": [
                {"path": "site/preview.html"},  # -> browser
                {"path": "tools/foo.py"},        # -> command-run
            ],
            "body": "ran it, exit=0",
            "comments": [],
        }
        os.environ["_PROOF_PRESENCE_PR_OVERRIDE"] = json.dumps([pr])
        try:
            result = health.check_proof_presence()
        finally:
            del os.environ["_PROOF_PRESENCE_PR_OVERRIDE"]
        self.assertIn("1230", result["detail"])
        self.assertIn("browser", result["detail"])
        self.assertNotIn("command-run", result["detail"])
        self.assertEqual("WARN", result["result"])


if __name__ == "__main__":
    unittest.main()
