r"""
Regression tests for tests/conftest.py's quarantine-enforcement hook
(PR #1468 round-1 reviewer BLOCK on R-TESTS, slice #1463).

tests/conftest.py implements ADR-0067 D4's "run-and-log, never gate"
quarantine enforcement: `pytest_collection_modifyitems` reads
tests/quarantine.txt's active entries and marks any collected test whose
node id matches one as `xfail(strict=False)`. It shipped with zero
automated coverage -- the reviewer confirmed via
`grep -rln "pytest_collection_modifyitems\|_active_quarantine_node_ids\|quarantine.txt" tests/*.py`
that only conftest.py itself referenced this logic.

Per rule #21 / CRI-004 (fixture discipline), these tests NEVER write to the
real tracked tests/quarantine.txt. Instead each test copies the real
tests/conftest.py verbatim into a fresh scratch temp directory -- so
`Path(__file__).parent` inside the hook resolves to the scratch dir and it
reads a scratch quarantine.txt there, never the tracked one -- then
subprocess-invokes `python -m pytest` against a throwaway scratch test file.
This exercises the real, shipped hook end-to-end rather than a
reimplementation of its parsing logic.

Runner: stdlib unittest + pytest compatible.
    python -m pytest tests/test_conftest_quarantine_1463.py -v
    python -m unittest tests.test_conftest_quarantine_1463 -v
"""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
CONFTEST_SRC = REPO_ROOT / "tests" / "conftest.py"


def _pytest_available():
    """The hook only runs under pytest (tests/README.md); if pytest isn't
    installed for this interpreter, the subprocess-pytest tests below
    can't meaningfully exercise it -- skip rather than false-fail.
    """
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "--version"],
            capture_output=True,
            timeout=15,
        )
        return result.returncode == 0
    except OSError:
        return False


_PYTEST_AVAILABLE = _pytest_available()


def _run_scratch_pytest(quarantine_text, test_file_text):
    """Run pytest in a fresh scratch dir seeded with a COPY of the real
    tests/conftest.py plus a scratch quarantine.txt and test file.

    Never touches the real tracked tests/quarantine.txt (rule #21 / CRI-004).
    Returns the completed subprocess.CompletedProcess.
    """
    with tempfile.TemporaryDirectory(prefix="conftest_quarantine_1463_") as tmp:
        scratch = Path(tmp)
        (scratch / "conftest.py").write_text(
            CONFTEST_SRC.read_text(encoding="utf-8"), encoding="utf-8"
        )
        (scratch / "quarantine.txt").write_text(quarantine_text, encoding="utf-8")
        (scratch / "test_scratch.py").write_text(test_file_text, encoding="utf-8")
        return subprocess.run(
            [sys.executable, "-m", "pytest", ".", "-v", "-p", "no:cacheprovider"],
            cwd=str(scratch),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )


class TestConftestSourceExists(unittest.TestCase):
    """Sanity: the shipped hook must exist before behavioral tests can run."""

    def test_conftest_file_exists(self):
        self.assertTrue(
            CONFTEST_SRC.is_file(),
            f"tests/conftest.py not found at {CONFTEST_SRC}",
        )

    def test_conftest_defines_the_hook(self):
        src = CONFTEST_SRC.read_text(encoding="utf-8")
        self.assertIn("def pytest_collection_modifyitems(", src)
        self.assertIn("_active_quarantine_node_ids", src)


@unittest.skipUnless(
    _PYTEST_AVAILABLE,
    "pytest not installed for this interpreter -- required to exercise "
    "the pytest-only quarantine hook",
)
class TestQuarantinedTestMarkedXfail(unittest.TestCase):
    """AC 1: a test node listed in a quarantine.txt-shaped register is
    collected and marked xfail(strict=False) -- so a deliberately-failing
    scratch test does not fail the overall pytest run's exit code.
    """

    def test_quarantined_failure_does_not_gate_the_run(self):
        quarantine_text = (
            "# scratch quarantine register (fixture, never the tracked file)\n"
            "test_scratch.py::test_deliberately_fails"
            "  # captured #9999  [quarantined: 2026-01-01]\n"
        )
        test_file_text = (
            "def test_deliberately_fails():\n"
            "    assert False, 'scratch failure -- should be xfail, not FAIL'\n"
        )
        result = _run_scratch_pytest(quarantine_text, test_file_text)
        self.assertEqual(
            result.returncode,
            0,
            "a quarantined failing test gated the run "
            f"(exit {result.returncode}):\n{result.stdout}\n{result.stderr}",
        )
        self.assertIn("xfail", result.stdout.lower())


@unittest.skipUnless(
    _PYTEST_AVAILABLE,
    "pytest not installed for this interpreter -- required to exercise "
    "the pytest-only quarantine hook",
)
class TestNonQuarantinedTestFailsNormally(unittest.TestCase):
    """AC 2 (negative case): a test node NOT listed in quarantine is
    unaffected -- no xfail marker, fails normally and gates the run. Proves
    the hook is selective, not blanket.
    """

    def test_unlisted_failure_still_gates_the_run(self):
        quarantine_text = (
            "# scratch quarantine register (fixture, never the tracked file)\n"
            "test_scratch.py::test_some_other_test"
            "  # captured #9999  [quarantined: 2026-01-01]\n"
        )
        test_file_text = (
            "def test_deliberately_fails():\n"
            "    assert False, 'scratch failure -- NOT quarantined, must FAIL'\n"
        )
        result = _run_scratch_pytest(quarantine_text, test_file_text)
        self.assertNotEqual(
            result.returncode,
            0,
            "a non-quarantined failing test did not gate the run -- hook is "
            f"marking everything xfail, not just listed nodes:\n{result.stdout}",
        )
        self.assertNotIn("1 xfailed", result.stdout.lower())


@unittest.skipUnless(
    _PYTEST_AVAILABLE,
    "pytest not installed for this interpreter -- required to exercise "
    "the pytest-only quarantine hook",
)
class TestCommentAndBlankLinesIgnored(unittest.TestCase):
    """AC 3: comment lines (full-line `#`) and blank lines in the register
    are ignored, matching tests/quarantine.txt's own documented format. A
    node id that is COMMENTED OUT must NOT be treated as quarantined.
    """

    def test_commented_out_entry_is_not_honored(self):
        quarantine_text = (
            "\n"
            "# a full-line comment naming the target node -- must be ignored\n"
            "# test_scratch.py::test_deliberately_fails"
            "  # captured #9999  [quarantined: 2026-01-01]\n"
            "\n"
        )
        test_file_text = (
            "def test_deliberately_fails():\n"
            "    assert False, 'scratch failure -- comment must not quarantine it'\n"
        )
        result = _run_scratch_pytest(quarantine_text, test_file_text)
        self.assertNotEqual(
            result.returncode,
            0,
            "a commented-out quarantine entry was honored anyway -- comment "
            f"parsing is broken:\n{result.stdout}",
        )

    def test_active_entry_after_blank_and_comment_lines_is_honored(self):
        quarantine_text = (
            "\n"
            "# header comment above the active entry\n"
            "\n"
            "test_scratch.py::test_deliberately_fails"
            "  # captured #9999  [quarantined: 2026-01-01]\n"
        )
        test_file_text = (
            "def test_deliberately_fails():\n"
            "    assert False, 'scratch failure -- should be xfail'\n"
        )
        result = _run_scratch_pytest(quarantine_text, test_file_text)
        self.assertEqual(
            result.returncode,
            0,
            "blank/comment lines before the active entry broke parsing "
            f"(exit {result.returncode}):\n{result.stdout}\n{result.stderr}",
        )


if __name__ == "__main__":
    unittest.main()
