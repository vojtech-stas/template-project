"""Tests for Health tab group-field enrichment (slice #931 / PRD #927 §2 #10).

Verifies:
- _CHECK_GROUP_MAP covers every check ID emitted by _build_health_data substrateMeta checks.
- _enrich_group sets 'group' on each check dict.
- Known IDs map to expected group labels.
- Unknown IDs fall back to 'Other'.
"""
import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'dashboard'))

from health import _CHECK_GROUP_MAP, _enrich_group


class TestCheckGroupMap(unittest.TestCase):

    def test_known_ids_have_expected_groups(self):
        """Core check IDs map to the human-readable groups they belong to."""
        cases = [
            ("DOCS-1", "Docs in sync"),
            ("DOCS-11", "Docs in sync"),
            ("HOOK-INTEGRITY", "Hooks live"),
            ("HOOK-LIVENESS", "Hooks live"),
            ("CAPTURE-SLO", "No drift"),
            ("ISOLATION-GROUP", "No drift"),
            ("RULE-COVERAGE", "Rules enforced"),
            ("PARITY", "Rules enforced"),
            ("PROOF-PRESENCE", "Verification integrity"),
            ("GREEN-MAIN", "Verification integrity"),
            ("BRANCH-TOPOLOGY", "Release gates"),
            ("RELEASE-READY", "Release gates"),
            ("SESSION-INJECTION", "Session hygiene"),
            ("STALE-BRANCHES", "No drift"),
            ("QUERY-HONESTY", "Verification integrity"),
        ]
        for check_id, expected_group in cases:
            with self.subTest(check_id=check_id):
                self.assertEqual(
                    _CHECK_GROUP_MAP[check_id],
                    expected_group,
                    f"{check_id} should map to '{expected_group}'"
                )

    def test_map_has_at_least_two_distinct_groups(self):
        """Map must define ≥2 distinct group labels (AC #10)."""
        groups = set(_CHECK_GROUP_MAP.values())
        self.assertGreaterEqual(len(groups), 2, "Need ≥2 distinct group names")

    def test_enrich_group_sets_group_field(self):
        """_enrich_group mutates dicts in-place with a 'group' key."""
        checks = [
            {"id": "HOOK-INTEGRITY", "result": "PASS", "detail": ""},
            {"id": "CAPTURE-SLO", "result": "PASS", "detail": ""},
        ]
        result = _enrich_group(checks)
        self.assertIs(result, checks, "_enrich_group should return the same list")
        self.assertEqual(checks[0]["group"], "Hooks live")
        self.assertEqual(checks[1]["group"], "No drift")

    def test_enrich_group_unknown_id_falls_back(self):
        """Unknown check IDs fall back to 'Other' without raising."""
        checks = [{"id": "UNKNOWN-CHECK-XYZ", "result": "PASS", "detail": ""}]
        _enrich_group(checks)
        self.assertEqual(checks[0]["group"], "Other")

    def test_enrich_group_empty_list(self):
        """_enrich_group handles an empty check list gracefully."""
        result = _enrich_group([])
        self.assertEqual(result, [])

    def test_enrich_group_missing_id_key(self):
        """_enrich_group handles a dict without 'id' key gracefully."""
        checks = [{"result": "PASS", "detail": ""}]
        _enrich_group(checks)
        self.assertEqual(checks[0]["group"], "Other")


if __name__ == '__main__':
    unittest.main()
