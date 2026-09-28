"""
tests/test_repo_identity_anchors_1514.py

Regression tests for PRD #1500 slice #1514 — CI CHECK 29
(REPO-IDENTITY-LITERALS) arm (b) (tools/check-repo-identity-literals.py),
ADR-0089 D4.

Round 2 (reviewer BLOCK on PR #1617, rule R-TESTS): slice #1514 names an
"arm (b) firing test (26)", but the arm (b) detector functions
(find_anchor_violations_in_text / scan_repo_anchors / _is_flagged_anchor_value,
~L186-288 of tools/check-repo-identity-literals.py) shipped with no dedicated
regression test of their own. The reviewer confirmed the detector already
behaves correctly (this is test-only work, no production code changes).

Covers the six shapes ADR-0089 D4 names:
  1. a module-level PR-number constant (`_X_BOOTSTRAP_PR = 900`) -> flagged.
  2. a 7-40-char hex `*_ANCHOR_SHA` string -> flagged.
  3. a `*_WINDOW_EXCEPTIONS` set/list literal of numbers -> flagged.
  4. `_META_TRIPWIRE_BOOTSTRAP_PROMOTION = 0` -> legal (S4-self, `0` is
     deliberately excluded -- a count a new project also starts at).
  5. the `GRANDFATHER_UNTIL` dict of ISO instants -> does not self-flag,
     despite containing "GRANDFATHER" in its own name (S4-self).
  6. an indented, function-local anchor (the exact `_GRANDFATHERED_BELOW`
     shape captured in #1618 -- CHECK 29 arm (b) misses function-local
     grandfather anchors) -> out of scope by design: D4 says "a
     module-level constant", and indentation is the discriminator
     (find_anchor_violations_in_text's own `if not line or line[0] in
     (" ", "\\t"): continue`).

Plus one end-to-end pair: `main()`'s exit code over a throwaway git repo
(never the live worktree/branch set) containing a violating anchor file,
and one without.

All tests are offline, deterministic, and network-free.

Runner: pytest compatible.
  python -m pytest tests/test_repo_identity_anchors_1514.py -v
"""

import importlib.util as _ilu
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TOOLS_DIR = _REPO_ROOT / "tools"
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

_spec = _ilu.spec_from_file_location(
    "check_repo_identity_literals",
    str(_TOOLS_DIR / "check-repo-identity-literals.py"),
)
_mod = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

find_anchor_violations_in_text = _mod.find_anchor_violations_in_text
scan_repo_anchors = _mod.scan_repo_anchors
is_anchor_subject_file = _mod.is_anchor_subject_file
_is_flagged_anchor_value = _mod._is_flagged_anchor_value
main = _mod.main


class TestAnchorValueShapesFlagged(unittest.TestCase):
    """The three flagged shapes (ADR-0089 D4), each at module level
    (column 0, no leading whitespace)."""

    def test_module_level_pr_number_constant_flagged(self):
        text = '_X_BOOTSTRAP_PR = 900\n'
        hits = find_anchor_violations_in_text(text)
        self.assertEqual(len(hits), 1)
        self.assertIn("_X_BOOTSTRAP_PR = 900", hits[0][1])

    def test_hex_anchor_sha_flagged(self):
        # 7-char hex string, quoted -- the _HEX_STRING_RE lower bound.
        text = '_FOO_ANCHOR_SHA = "f271843"\n'
        hits = find_anchor_violations_in_text(text)
        self.assertEqual(len(hits), 1)

    def test_hex_anchor_sha_40_char_flagged(self):
        # 40-char hex string, quoted -- the _HEX_STRING_RE upper bound.
        text = "_FOO_ANCHOR_SHA = '" + "a" * 40 + "'\n"
        hits = find_anchor_violations_in_text(text)
        self.assertEqual(len(hits), 1)

    def test_window_exceptions_number_set_flagged(self):
        text = '_FOO_WINDOW_EXCEPTIONS = {"1089", "900"}\n'
        hits = find_anchor_violations_in_text(text)
        self.assertEqual(len(hits), 1)

    def test_window_exceptions_number_list_flagged(self):
        text = "_FOO_WINDOW_EXCEPTIONS = [1089, 900]\n"
        hits = find_anchor_violations_in_text(text)
        self.assertEqual(len(hits), 1)


class TestAnchorValueShapesLegal(unittest.TestCase):
    """The two explicitly-legal shapes (S4-self)."""

    def test_meta_tripwire_zero_is_legal(self):
        text = "_META_TRIPWIRE_BOOTSTRAP_PROMOTION = 0\n"
        hits = find_anchor_violations_in_text(text)
        self.assertEqual(hits, [])

    def test_grandfather_until_dict_does_not_self_flag(self):
        # The real multi-line dict shape from dashboard/_constants.py: each
        # entry is an indented `"KEY": "value",` line (not a bare
        # `NAME = value` module-level assignment), and the opening
        # `GRANDFATHER_UNTIL = {` line's own "value" is just `{`, which
        # matches none of the three flagged shapes.
        text = (
            "GRANDFATHER_UNTIL = {\n"
            '    "CI-GATE": "2026-06-11T09:03:09Z",\n'
            '    "SILENT-DRIFT": "2026-06-12T11:45:25Z",\n'
            "}\n"
        )
        hits = find_anchor_violations_in_text(text)
        self.assertEqual(hits, [])


class TestFunctionLocalAnchorOutOfScope(unittest.TestCase):
    """An indented (function-local) anchor is deliberately NOT module-level
    per D4's own wording ("a module-level constant") -- out of scope by
    design, not a detector gap. See #1618 (CHECK 29 arm (b) misses
    function-local grandfather anchors) for the follow-up capture that
    tracks whether this scope should someday widen; this test asserts
    CURRENT behavior, not a wish."""

    def test_indented_local_grandfathered_below_not_flagged(self):
        text = (
            "def check_silent_drift():\n"
            "    _GRANDFATHERED_BELOW = 799\n"
            "    return _GRANDFATHERED_BELOW\n"
        )
        hits = find_anchor_violations_in_text(text)
        self.assertEqual(hits, [])

    def test_tab_indented_local_anchor_not_flagged(self):
        text = "def f():\n\t_BOOTSTRAP_CUTOFF = 22\n"
        hits = find_anchor_violations_in_text(text)
        self.assertEqual(hits, [])


class TestScanRepoAnchorsIntegration(unittest.TestCase):
    """End-to-end: main()'s exit code over a throwaway git repo (never the
    live worktree/branch set), with and without a violating anchor file."""

    def _make_repo(self, tmp, relpath, content):
        target = Path(tmp) / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def _commit(self, tmp):
        subprocess.run(["git", "-C", tmp, "add", "-A"], check=True)
        subprocess.run(
            ["git", "-C", tmp, "-c", "user.email=t@example.com",
             "-c", "user.name=t", "commit", "-q", "-m", "planted"],
            check=True,
        )

    def test_exit_code_1_on_violating_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init", "-q", tmp], check=True)
            self._make_repo(tmp, "dashboard/planted.py", "_X_BOOTSTRAP_PR = 900\n")
            self._commit(tmp)
            self.assertEqual(main(["--root", tmp]), 1)

    def test_exit_code_0_on_clean_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init", "-q", tmp], check=True)
            self._make_repo(
                tmp, "dashboard/clean.py",
                "GRANDFATHER_UNTIL = {\n"
                '    "CI-GATE": "2026-06-11T09:03:09Z",\n'
                "}\n"
                "_META_TRIPWIRE_BOOTSTRAP_PROMOTION = 0\n",
            )
            self._commit(tmp)
            self.assertEqual(main(["--root", tmp]), 0)

    def test_scan_repo_anchors_names_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init", "-q", tmp], check=True)
            self._make_repo(tmp, "tools/planted.py", '_FOO_ANCHOR_SHA = "f271843"\n')
            self._commit(tmp)
            violations = scan_repo_anchors(tmp)
            self.assertIsNotNone(violations)
            paths = [v[0].replace("\\", "/") for v in violations]
            self.assertIn("tools/planted.py", paths)

    def test_is_anchor_subject_file_scope(self):
        self.assertTrue(is_anchor_subject_file("dashboard/health.py"))
        self.assertTrue(is_anchor_subject_file("tools/check-repo-identity-literals.py"))
        self.assertFalse(is_anchor_subject_file("dashboard/README.md"))
        self.assertFalse(is_anchor_subject_file("tests/test_something.py"))


if __name__ == "__main__":
    unittest.main()
