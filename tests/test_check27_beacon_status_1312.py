"""
Regression tests for CHECK 27 (`tools/ci-checks.sh`, slice #1312, ADR-0083
D1/D2) -- the closed hook beacon-`status` schema guard.

Root cause (reviewer BLOCK, PR #1421 round 1, verbatim from the verdict):
CHECK 27 ships ~292 lines of new executable logic (regex + AST walk +
scope-keyed resolution + call-graph traversal) with nothing under `tests/`
referencing it. The reviewer independently extracted the check body and ran
it against three trees to confirm the FAIL leg is genuine, but nothing
re-runs that demonstration -- it existed only as prose in a PR body. The
reviewer also found the bash leg recognizes only the exact
`printf '{...}\\n' ... >> "...hook-fires.jsonl"` logical-line shape and
silently contributes zero violations for a beacon written any other way --
the "check that cannot redden on its own primary defect class" failure
ADR-0083 D2 names explicitly -- and that the `.py` subject set was a single
hard-coded literal (`.claude/hooks/pre-tool-bash-classify.py`) rather than a
glob, in conflict with ADR-0083 D2's Enforcement clause and D3's "subject
set is defined, not assumed" (VER-009).

This module does not reimplement CHECK 27's logic. It extracts the actual
heredoc body from `tools/ci-checks.sh` (the same artifact CI runs) and
executes it via subprocess against synthetic fixture trees under
`.claude/hooks/` -- the same technique the reviewer used by hand. Per rule
#21, no fixture data is written under `.claude/logs/`; every fixture lives
in a fresh `tempfile.TemporaryDirectory()`.

R-PROVE / rule #13 regression rider ordering (ADR-0067 D2/D3): the two tests
that expose genuine CODE defects --
`test_unrecognized_bash_shape_is_loud_not_silent` (the silent-skip class)
and `test_py_subject_set_is_derived_from_glob_not_hardcoded` (the
hard-coded-path class) -- were committed FAILING against the pre-fix CHECK
27 body, then the fix commit turned them green. The other four tests
describe already-working behaviour the reviewer verified by hand and are
not defect-exposing; their ordering is not load-bearing.

Runner: stdlib unittest + pytest compatible.
  python -m pytest tests/test_check27_beacon_status_1312.py -v
  python -m unittest tests.test_check27_beacon_status_1312 -v
"""

import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
CI_CHECKS = REPO_ROOT / "tools" / "ci-checks.sh"

START_MARKER = "python3 - << 'BEACONSCHEMA_PYEOF'\n"
END_MARKER = "\nBEACONSCHEMA_PYEOF"


def _extract_check27_source() -> str:
    """Pull CHECK 27's python heredoc body verbatim out of ci-checks.sh."""
    text = CI_CHECKS.read_text(encoding="utf-8")
    start = text.index(START_MARKER) + len(START_MARKER)
    end = text.index(END_MARKER, start)
    return text[start:end]


def _run_check27(source: str, fixture_root: Path):
    """Write `source` to a script at the fixture root (NOT under
    .claude/hooks/, so it is never itself picked up by the check's own
    globs) and execute it with cwd=fixture_root, mirroring exactly how
    `bash tools/ci-checks.sh` invokes it relative to the repo root."""
    script_path = fixture_root / "_check27_under_test.py"
    script_path.write_text(source, encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(script_path)],
        cwd=str(fixture_root),
        capture_output=True,
        text=True,
        timeout=30,
    )


def _hooks_dir(fixture_root: Path) -> Path:
    d = fixture_root / ".claude" / "pipeline" / "hooks"
    d.mkdir(parents=True, exist_ok=True)
    return d


class TestExtraction(unittest.TestCase):
    """Sanity: the heredoc must still be findable before behavioral tests
    can run at all -- a renamed delimiter would otherwise silently collapse
    every test below to a false pass-by-vacuity."""

    def test_check27_heredoc_is_extractable(self):
        source = _extract_check27_source()
        self.assertIn("ALLOWED = {\"attempt\", \"ok\", \"ERROR\"}", source)
        self.assertIn("GRANDFATHER", source)


class TestBashLegValueAndPresence(unittest.TestCase):
    """The two legs ADR-0083 D2's Enforcement clause names directly:
    presence (every emit site carries `status`) and value (every literal
    falls in the closed set, plus the named grandfather)."""

    def setUp(self):
        self.source = _extract_check27_source()

    def test_reddens_on_status_less_emitter(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture_root = Path(tmp)
            hooks = _hooks_dir(fixture_root)
            (hooks / "no-status.sh").write_text(
                "#!/usr/bin/env bash\n"
                "printf '{\"hook\":\"no-status\",\"ts\":\"%s\"}\\n' "
                "\"$(date)\" >> \"$LOG_DIR/hook-fires.jsonl\"\n",
                encoding="utf-8",
            )
            result = _run_check27(self.source, fixture_root)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn('has no "status" key', result.stderr)

    def test_reddens_on_mis_valued_emitter(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture_root = Path(tmp)
            hooks = _hooks_dir(fixture_root)
            (hooks / "bad-value.sh").write_text(
                "#!/usr/bin/env bash\n"
                "printf '{\"hook\":\"bad-value\",\"status\":\"weird\",\"ts\":\"%s\"}\\n' "
                "\"$(date)\" >> \"$LOG_DIR/hook-fires.jsonl\"\n",
                encoding="utf-8",
            )
            result = _run_check27(self.source, fixture_root)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn("outside the closed set", result.stderr)

    def test_greens_on_conforming_fleet(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture_root = Path(tmp)
            hooks = _hooks_dir(fixture_root)
            (hooks / "conforming.sh").write_text(
                "#!/usr/bin/env bash\n"
                "printf '{\"hook\":\"conforming\",\"status\":\"attempt\",\"ts\":\"%s\"}\\n' \\\n"
                "  \"$(date)\" \\\n"
                "  >> \"$LOG_DIR/hook-fires.jsonl\" 2>/dev/null || true\n"
                "printf '{\"hook\":\"conforming\",\"status\":\"ok\",\"ts\":\"%s\"}\\n' \\\n"
                "  \"$(date)\" \\\n"
                "  >> \"$LOG_DIR/hook-fires.jsonl\" 2>/dev/null || true\n",
                encoding="utf-8",
            )
            result = _run_check27(self.source, fixture_root)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("PASS: CHECK 27", result.stdout)


class TestGrandfatherIsLoadBearing(unittest.TestCase):
    """Prove the single named grandfather line actually does something,
    rather than restating that it exists. Mirrors the reviewer's own
    verification ("zeroing GRANDFATHER reddens session-start.sh:42") without
    mutating the shipped file: the extracted source is patched in-memory to
    empty the GRANDFATHER set, then run against the identical fixture."""

    def setUp(self):
        self.source = _extract_check27_source()
        self.assertIn(
            'GRANDFATHER = {("session-start.sh", "python3_selftest")}',
            self.source,
            "grandfather literal shape changed -- update this test's patch target",
        )

    def _session_start_fixture(self, fixture_root: Path):
        hooks = _hooks_dir(fixture_root)
        (hooks / "session-start.sh").write_text(
            "#!/usr/bin/env bash\n"
            "_PY3_STATUS=\"ok\"\n"
            "printf '{\"hook\":\"session-start\",\"status\":\"python3_selftest\","
            "\"result\":\"%s\",\"ts\":\"%s\"}\\n' \\\n"
            "  \"$_PY3_STATUS\" \"$(date)\" \\\n"
            "  >> \"$LOG_DIR/hook-fires.jsonl\" 2>/dev/null || true\n",
            encoding="utf-8",
        )

    def test_grandfathered_literal_passes_with_grandfather_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture_root = Path(tmp)
            self._session_start_fixture(fixture_root)
            result = _run_check27(self.source, fixture_root)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_same_literal_reddens_once_grandfather_is_zeroed(self):
        patched = self.source.replace(
            'GRANDFATHER = {("session-start.sh", "python3_selftest")}',
            "GRANDFATHER = set()",
        )
        with tempfile.TemporaryDirectory() as tmp:
            fixture_root = Path(tmp)
            self._session_start_fixture(fixture_root)
            result = _run_check27(patched, fixture_root)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn("outside the closed set", result.stderr)


class TestUnrecognizedBashShapeIsLoud(unittest.TestCase):
    """Defect-exposing test (R-PROVE ordering: committed failing against the
    pre-fix check body, green after the fix). A beacon appended to
    hook-fires.jsonl via `echo` rather than the recognized
    `printf '{...}\\n' ... >>` shape must not silently contribute zero
    violations -- the exact silent-skip class the reviewer flagged."""

    def test_unrecognized_bash_shape_is_loud_not_silent(self):
        source = _extract_check27_source()
        with tempfile.TemporaryDirectory() as tmp:
            fixture_root = Path(tmp)
            hooks = _hooks_dir(fixture_root)
            (hooks / "weird-shape.sh").write_text(
                "#!/usr/bin/env bash\n"
                "echo '{\"hook\":\"weird-shape\",\"status\":\"attempt\",\"ts\":\"X\"}' "
                ">> \"$LOG_DIR/hook-fires.jsonl\"\n",
                encoding="utf-8",
            )
            result = _run_check27(source, fixture_root)
            self.assertEqual(
                result.returncode,
                1,
                "an echo-based beacon write must be a LOUD violation, not a "
                f"silent pass. stdout={result.stdout!r} stderr={result.stderr!r}",
            )
            self.assertIn("does not match the recognized", result.stderr)

    def test_recognized_shape_is_not_flagged_by_the_fallback(self):
        """Companion assertion: the fallback must not fire on a line that
        DOES match PRINTF_RE -- guards against the fallback and PRINTF_RE
        double-counting the same conforming write site."""
        source = _extract_check27_source()
        with tempfile.TemporaryDirectory() as tmp:
            fixture_root = Path(tmp)
            hooks = _hooks_dir(fixture_root)
            (hooks / "fine.sh").write_text(
                "#!/usr/bin/env bash\n"
                "printf '{\"hook\":\"fine\",\"status\":\"attempt\",\"ts\":\"%s\"}\\n' "
                "\"$(date)\" >> \"$LOG_DIR/hook-fires.jsonl\"\n",
                encoding="utf-8",
            )
            result = _run_check27(source, fixture_root)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class TestPySubjectSetIsDerivedFromGlob(unittest.TestCase):
    """Defect-exposing test (R-PROVE ordering: committed failing against the
    pre-fix check body, green after the fix). ADR-0083 D2's Enforcement
    clause scopes the check to '.claude/hooks/*.sh' AND '.claude/hooks/*.py'
    -- a second, non-canonically-named .py helper with a bad status must be
    caught. Pre-fix, the check only opened the single hard-coded
    'pre-tool-bash-classify.py' path; a fixture tree that never creates a
    file by that literal name proves the hard-coding rather than merely
    describing it."""

    def test_py_subject_set_is_derived_from_glob_not_hardcoded(self):
        source = _extract_check27_source()
        with tempfile.TemporaryDirectory() as tmp:
            fixture_root = Path(tmp)
            hooks = _hooks_dir(fixture_root)
            # Deliberately NOT named pre-tool-bash-classify.py -- a second
            # python helper, the shape ADR-0083 D2 anticipates ("the moment
            # a second lands").
            (hooks / "second-helper.py").write_text(
                "import json\n"
                "with open('hook-fires.jsonl', 'a', encoding='utf-8') as f:\n"
                "    f.write(json.dumps({'hook': 'second-helper', "
                "'status': 'bogus'}) + '\\n')\n",
                encoding="utf-8",
            )
            result = _run_check27(source, fixture_root)
            self.assertEqual(
                result.returncode,
                1,
                "a bad status in a non-hardcoded .py hook helper must be "
                f"caught. stdout={result.stdout!r} stderr={result.stderr!r}",
            )
            self.assertIn("second-helper.py", result.stderr)

    def test_hardcoded_default_helper_is_still_covered(self):
        """The glob must not regress coverage of the one .py helper the
        hard-coded literal used to name."""
        source = _extract_check27_source()
        with tempfile.TemporaryDirectory() as tmp:
            fixture_root = Path(tmp)
            hooks = _hooks_dir(fixture_root)
            (hooks / "pre-tool-bash-classify.py").write_text(
                "import json\n"
                "with open('hook-fires.jsonl', 'a', encoding='utf-8') as f:\n"
                "    f.write(json.dumps({'hook': 'pre-tool-bash', "
                "'status': 'ok'}) + '\\n')\n",
                encoding="utf-8",
            )
            result = _run_check27(source, fixture_root)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("1 python hook helper", result.stdout)


if __name__ == "__main__":
    unittest.main()
