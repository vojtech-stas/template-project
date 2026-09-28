"""
Regression test for slice #1309 (PRD #1266 criteria 1-2, root-cause #1246):
the published R-LOC jq command in .claude/agents/reviewer.md truncates at the
first non-runtime path instead of summing every runtime-artifact entry.

Live near-miss: PR #1245 reported 165 LoC via this command; the true
runtime-artifact total was 351 (#1246).

Root cause: `select(.path | A or B or C or (.path == D))` pipes `.path`
(a string) into the whole disjunct chain, so the FOURTH disjunct's own
`.path` reference tries to index that string -- `jq: error ... Cannot index
string with string "path"`. Because jq's `or` short-circuits, runtime paths
matching one of the first three `startswith` disjuncts never trigger the
error, but a non-runtime path (or `.claude/settings.json` itself) falls
through to the fourth disjunct and blows up, silently truncating every file
after the error in the stream (jq exits on error mid-stream; nothing after
the erroring entry is ever summed).

This test extracts the jq (and awk) command VERBATIM from reviewer.md's
R-LOC section -- never a hand-copied duplicate -- and executes it against a
fixture file list shaped like the PRD's baseline: a non-runtime path
(`decisions/0081-x.md`) sandwiched between two runtime paths. Per REG-002 /
ADR-0067 D2/D3, this test's commit MUST precede the reviewer.md fix commit
in branch history, and it MUST fail against the pre-fix command (it does:
the pre-fix command prints only "15" -- reviewer.md's own contribution --
then errors out on `decisions/0081-x.md` before ever reaching
`.claude/settings.json` or the trailing hook path).

Per PRD #1266 §4 "No new dependencies": jq is already a hard dependency of
the hook layer (preinstalled on ubuntu-latest); this test skips LOUDLY
(unittest.skipUnless, named reason) rather than silently passing when jq is
absent from PATH.

Runner: stdlib unittest + pytest compatible.
    python -m pytest tests/test_rloc_jq_path_scoping_1309.py -v
    python -m unittest tests.test_rloc_jq_path_scoping_1309 -v
"""

import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
REVIEWER_MD = REPO_ROOT / ".claude" / "pipeline" / "agents" / "reviewer.md"

# Baseline fixture from PRD #1266 §2 criterion 1: a non-runtime path sits
# between two runtime paths. Order matters -- it is what exercises the
# truncate-on-error defect (the erroring entry aborts everything after it
# in the jq stream).
FIXTURE_FILES = [
    {"path": ".claude/agents/reviewer.md", "additions": 10, "deletions": 5},
    {"path": "decisions/0081-x.md", "additions": 100, "deletions": 0},
    {"path": ".claude/settings.json", "additions": 7, "deletions": 3},
    {"path": ".claude/hooks/stop-reviewer-gate.sh", "additions": 4, "deletions": 1},
]
# 15 (reviewer.md) + 10 (settings.json) + 5 (hook) == 30. The 100-line
# decisions/ entry is correctly EXCLUDED (non-runtime, uncapped).
EXPECTED_SUM = 30


def _jq_available() -> bool:
    return shutil.which("jq") is not None


def _extract_r_loc_jq_and_awk() -> tuple:
    """Pull the jq filter and awk expression verbatim out of reviewer.md's
    own R-LOC section, so this test tracks the canonical artifact rather
    than a copy that can silently drift out of sync with it."""
    text = REVIEWER_MD.read_text(encoding="utf-8")
    section_match = re.search(r"### R-LOC.*?(?=\n### )", text, re.DOTALL)
    if not section_match:
        raise AssertionError("R-LOC section not found in reviewer.md")
    section = section_match.group(0)
    cmd_match = re.search(r"--jq\s+'([^']*)'\s*\|\s*awk\s+'([^']*)'", section)
    if not cmd_match:
        raise AssertionError(
            "R-LOC jq/awk command not found verbatim in reviewer.md's R-LOC section"
        )
    return cmd_match.group(1), cmd_match.group(2)


@unittest.skipUnless(
    _jq_available(),
    "jq not available on PATH -- R-LOC regression skipped loudly, not silently passed",
)
class TestRLocJqPathScoping(unittest.TestCase):
    """PRD #1266 criteria 1-2 / slice #1309: the published R-LOC jq command
    must sum every runtime-artifact entry, including entries that appear
    AFTER a non-runtime path in the file list, and must not emit a jq type
    error."""

    def test_extracted_command_sums_correctly_with_non_runtime_path_sandwiched(self):
        jq_filter, awk_expr = _extract_r_loc_jq_and_awk()
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as f:
            json.dump({"files": FIXTURE_FILES}, f)
            fixture_path = f.name
        try:
            jq_proc = subprocess.run(
                ["jq", jq_filter, fixture_path],
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(
                jq_proc.returncode,
                0,
                msg=(
                    "R-LOC jq command must not error on a fixture with a "
                    "non-runtime path between two runtime paths "
                    f"(stdout={jq_proc.stdout!r} stderr={jq_proc.stderr!r})"
                ),
            )
            awk_proc = subprocess.run(
                ["awk", awk_expr],
                input=jq_proc.stdout,
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(awk_proc.returncode, 0, msg=f"awk failed: {awk_proc.stderr}")
            total = int(awk_proc.stdout.strip() or "0")
            self.assertEqual(
                total,
                EXPECTED_SUM,
                msg=(
                    f"R-LOC command summed to {total}, expected {EXPECTED_SUM} "
                    "(15 reviewer.md + 10 settings.json + 5 hook; the 100-line "
                    "non-runtime decisions/0081-x.md entry must be excluded, "
                    "not cause truncation of the entries after it)"
                ),
            )
        finally:
            Path(fixture_path).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
