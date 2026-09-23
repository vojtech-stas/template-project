"""
tests/test_release_check_resolver_1506.py

PR #1528 fix round (slice #1506 / PRD #1501, ADR-0090 D3/D4): the defects
the orchestrator-supervised lane run (lane `fix/1525-lane-misc`, lane PR
#1539) found in `tools/release.py`, plus the rest of each defect's class
(rule #19). Every gh read is stubbed in-process; no test calls `gh`, and
nothing is written outside pytest/tempfile dirs (rule #21).

  R1  `verify` resolves the MOST RECENTLY MERGED lane PR (by `mergedAt`),
      never the first search hit -- a bug closed by two lane PRs (#1525:
      #1527, then #1539) must read the newer PR's `Check #<n>:` line. Only
      a `lane`-labeled PR counts, and only a PR that closes the bug on a
      whole `Closes #<n>` line (the R8 class: prose `closes #<n>` is not a
      closing reference).
  R2  one layer of surrounding markdown backticks is stripped from an
      extracted check, for the issue's `Check:` line and the lane PR's
      `Check #<n>:` line alike.
  R5  `path:N-M` refs parse in `lanes` and in the packet.
  R6  the packet and `verify` share ONE check resolver.
  class sweep (Check lines):
      - `Check:` is matched case-sensitively, so a pasted packet's own
        `CHECK: MISSING` line is never executed as a command named MISSING;
      - an empty `Check:` / `Check #<n>:` line never reads the NEXT line as
        its command (`\\s*` spanning the newline -- the F1 `MODEL:` class).
"""
import importlib.util
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
RELEASE_PY = REPO_ROOT / "tools" / "release.py"


def _load_release(name="release_check_resolver_test"):
    spec = importlib.util.spec_from_file_location(name, RELEASE_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Res:
    def __init__(self, stdout="", returncode=0):
        self.stdout, self.stderr, self.returncode = stdout, "", returncode


def _pr(number, body, merged_at, labels=("lane",)):
    return {
        "number": number,
        "body": body,
        "mergedAt": merged_at,
        "labels": [{"name": l} for l in labels],
    }


# The real #1525 shape: #1527 merged first (12:10:45Z) with a non-
# discriminating check; #1539 merged later (14:59:00Z) with the real one.
_PR_1527 = _pr(1527, "Closes #1525\n\nCheck #1525: old-check-1527\n", "2026-09-23T12:10:45Z")
_PR_1539 = _pr(1539, "Closes #1525\n\nCheck #1525: new-check-1539\n", "2026-09-23T14:59:00Z")


def _stub_pr_lookup(release, prs, calls=None):
    """Every gh call returns `prs` as JSON, whatever the subcommand, so the
    test pins the SELECTION among the returned PRs, not the query form."""
    def _fake_run_gh(args):
        if calls is not None:
            calls.append(list(args))
        return _Res(json.dumps(prs))
    release._run_gh = _fake_run_gh


def _no_own_check(release):
    release._fetch_issue_json = lambda owner, repo, num: {"number": int(num), "body": "No check here."}
    release._fetch_comments = lambda owner, repo, num: []


# ---------------------------------------------------------------------------
# R1: the most recently merged lane PR wins.
# ---------------------------------------------------------------------------

class TestR1MostRecentlyMergedLanePr(unittest.TestCase):
    def test_r1_newest_merged_lane_pr_wins_when_older_is_listed_first(self):
        release = _load_release()
        _stub_pr_lookup(release, [_PR_1527, _PR_1539])
        pr = release._find_lane_pr_for_issue("o", "r", "1525")
        self.assertIsNotNone(pr)
        self.assertEqual(pr["number"], 1539)

    def test_r1_selection_is_order_independent(self):
        release = _load_release()
        _stub_pr_lookup(release, [_PR_1539, _PR_1527])
        self.assertEqual(release._find_lane_pr_for_issue("o", "r", "1525")["number"], 1539)

    def test_r1_verify_resolves_the_newer_prs_check_line(self):
        release = _load_release()
        _no_own_check(release)
        _stub_pr_lookup(release, [_PR_1527, _PR_1539])
        self.assertEqual(release._resolve_check("o", "r", "1525"), "new-check-1539")

    def test_r1_lookup_requests_merged_at(self):
        release = _load_release()
        calls = []
        _stub_pr_lookup(release, [_PR_1539], calls)
        release._find_lane_pr_for_issue("o", "r", "1525")
        self.assertEqual(len(calls), 1, calls)
        json_fields = calls[0][calls[0].index("--json") + 1].split(",")
        self.assertIn("mergedAt", json_fields)
        self.assertIn("labels", json_fields)

    def test_r1_a_newer_non_lane_pr_is_not_the_lane_pr(self):
        release = _load_release()
        hotfix = _pr(1600, "Closes #1525\n", "2026-09-24T09:00:00Z", labels=("trivial",))
        _stub_pr_lookup(release, [hotfix, _PR_1527, _PR_1539])
        self.assertEqual(release._find_lane_pr_for_issue("o", "r", "1525")["number"], 1539)

    def test_r1_prose_closes_mention_is_not_a_closing_reference(self):
        release = _load_release()
        prose = _pr(
            1601,
            "Follows up on the step-5 fix (this closes #1525 in passing).\n"
            "Check #1525: prose-check-1601\n",
            "2026-09-24T10:00:00Z",
        )
        _stub_pr_lookup(release, [prose, _PR_1527, _PR_1539])
        self.assertEqual(release._find_lane_pr_for_issue("o", "r", "1525")["number"], 1539)

    def test_r1_no_matching_lane_pr_is_none(self):
        release = _load_release()
        _stub_pr_lookup(release, [_pr(1602, "Closes #15250\n", "2026-09-24T10:00:00Z")])
        self.assertIsNone(release._find_lane_pr_for_issue("o", "r", "1525"))


# ---------------------------------------------------------------------------
# R2: one layer of surrounding backticks is stripped.
# ---------------------------------------------------------------------------

class TestR2BacktickStrip(unittest.TestCase):
    CASES = [
        ("`python -m pytest tests/x.py -q`", "python -m pytest tests/x.py -q"),
        ("``python -m pytest tests/x.py -q``", "python -m pytest tests/x.py -q"),
        ("`` echo `inner` ``", "echo `inner`"),
        ("python -m pytest tests/x.py -q", "python -m pytest tests/x.py -q"),
        ("echo `a` and `b`", "echo `a` and `b`"),
        ("`a` and `b`", "`a` and `b`"),
    ]

    def test_r2_lane_pr_check_line_strips_one_backtick_layer(self):
        for raw, want in self.CASES:
            with self.subTest(raw=raw):
                release = _load_release()
                _no_own_check(release)
                pr = _pr(1539, f"Closes #1525\nCheck #1525: {raw}\n", "2026-09-23T14:59:00Z")
                _stub_pr_lookup(release, [pr])
                self.assertEqual(release._resolve_check("o", "r", "1525"), want)

    def test_r2_issue_check_line_strips_one_backtick_layer(self):
        for raw, want in self.CASES:
            with self.subTest(raw=raw):
                release = _load_release()
                release._fetch_issue_json = lambda owner, repo, num, raw=raw: {
                    "number": int(num), "body": f"Broken.\r\nCheck: {raw}\r\n",
                }
                release._fetch_comments = lambda owner, repo, num: []
                self.assertEqual(release._resolve_check("o", "r", "7"), want)

    def test_r2_verify_runs_the_unwrapped_command(self):
        """End to end through `_cmd_verify`'s real shell run: a backtick-
        wrapped check fails verbatim (cmd.exe: not recognized; sh: command
        substitution runs `print(1)`'s output `1` as a command, rc=127)."""
        release = _load_release()
        _no_own_check(release)
        cmd = f'"{sys.executable}" -c "print(1)"'
        pr = _pr(1539, f"Closes #1525\nCheck #1525: `{cmd}`\n", "2026-09-23T14:59:00Z")
        _stub_pr_lookup(release, [pr])
        release._remote_owner_repo = lambda: ("o", "r")

        class _Args:
            issues = ["1525"]

        old = sys.stdout
        sys.stdout = io.StringIO()
        try:
            rc = release._cmd_verify(_Args())
            out = sys.stdout.getvalue()
        finally:
            sys.stdout = old
        self.assertEqual(out.strip(), "PASS #1525")
        self.assertEqual(rc, 0)


# ---------------------------------------------------------------------------
# Check-line class sweep: case, and empty lines never read the next line.
# ---------------------------------------------------------------------------

class TestCheckLineClassSweep(unittest.TestCase):
    def test_sweep_pasted_packet_check_missing_is_not_a_command(self):
        release = _load_release()
        release._fetch_issue_json = lambda owner, repo, num: {"number": int(num), "body": "No check."}
        release._fetch_comments = lambda owner, repo, num: [{
            "author_association": "OWNER",
            "body": "Packet as dispatched:\nSHA: abc\n#### Bug #7\n(no cited path:line refs)\nCHECK: MISSING\n",
        }]
        _stub_pr_lookup(release, [])
        self.assertIsNone(release._resolve_check("o", "r", "7"))

    def test_sweep_empty_issue_check_line_does_not_read_next_line(self):
        release = _load_release()
        release._fetch_issue_json = lambda owner, repo, num: {
            "number": int(num), "body": "Check:\nrm -rf build\n",
        }
        release._fetch_comments = lambda owner, repo, num: []
        _stub_pr_lookup(release, [])
        self.assertIsNone(release._resolve_check("o", "r", "7"))

    def test_sweep_empty_pr_check_line_does_not_read_next_line(self):
        release = _load_release()
        _no_own_check(release)
        pr = _pr(1539, "Closes #1525\nCheck #1525:\nrm -rf build\n", "2026-09-23T14:59:00Z")
        _stub_pr_lookup(release, [pr])
        self.assertIsNone(release._resolve_check("o", "r", "1525"))


# ---------------------------------------------------------------------------
# R5: `path:N-M` refs parse in lanes and the packet.
# ---------------------------------------------------------------------------

class _GitRepo(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="release_check_resolver_"))
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        for args in (["init", "-q"], ["config", "user.email", "t@example.com"], ["config", "user.name", "t"]):
            subprocess.run(["git"] + args, cwd=self.repo, check=True)
        (self.repo / "widget.py").write_text(
            "\n".join(f"line {n}" for n in range(1, 101)) + "\n", encoding="utf-8",
        )
        subprocess.run(["git", "add", "widget.py"], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=self.repo, check=True)
        self.sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.repo, capture_output=True, text=True, check=True,
        ).stdout.strip()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestR5LineRangeRefs(_GitRepo):
    def test_r5_extract_refs_reads_a_line_range(self):
        release = _load_release()
        self.assertEqual(
            release._extract_refs("Seen at `dashboard/health.py:5663-5677` on 837f078."),
            {"dashboard/health.py"},
        )

    def test_r5_lanes_groups_a_line_range_ref_by_its_path(self):
        release = _load_release()
        release._remote_owner_repo = lambda: ("o", "r")
        release._fetch_open_issues = lambda owner, repo: [{
            "number": 1525, "state": "open", "labels": [{"name": "bug"}],
            "milestone": {"number": 1, "title": "v1.0"},
            "body": "At `dashboard/health.py:5663-5677` on 837f078.",
        }]
        release._fetch_comments = lambda owner, repo, num: []

        class _Args:
            version = "v1.0"
            evidence = None
            priority = None

        old = sys.stdout
        sys.stdout = io.StringIO()
        try:
            rc = release._cmd_lanes(_Args())
            lanes = json.loads(sys.stdout.getvalue())
        finally:
            sys.stdout = old
        self.assertEqual(rc, 0)
        self.assertEqual(lanes[0]["paths"], ["dashboard/health.py"])
        self.assertFalse(lanes[0]["exclusive"])
        self.assertEqual(lanes[0]["lane"], "fix/1525-lane-health-py")

    def test_r5_packet_excerpts_a_line_range(self):
        release = _load_release()
        release._fetch_issue_json = lambda owner, repo, num: {
            "number": int(num), "body": "Broken across `widget.py:40-45`.\nCheck: echo ok",
        }
        release._fetch_comments = lambda owner, repo, num: []
        packet = release.build_packet("o", "r", ["9"], self.sha, repo_root=str(self.repo))
        self.assertNotIn("(no cited path:line refs)", packet)
        self.assertIn("widget.py:40-45", packet)
        lines = packet.splitlines()
        # ±20 lines around the whole range: 20..65 inclusive.
        self.assertIn("line 20", lines)
        self.assertIn("line 65", lines)
        self.assertNotIn("line 19", lines)
        self.assertNotIn("line 66", lines)

    def test_r5_single_line_ref_is_unchanged(self):
        release = _load_release()
        release._fetch_issue_json = lambda owner, repo, num: {
            "number": int(num), "body": "Broken at `widget.py:30`.\nCheck: echo ok",
        }
        release._fetch_comments = lambda owner, repo, num: []
        packet = release.build_packet("o", "r", ["9"], self.sha, repo_root=str(self.repo))
        lines = packet.splitlines()
        self.assertIn("widget.py:30", lines)
        self.assertIn("line 10", lines)
        self.assertIn("line 50", lines)
        self.assertNotIn("line 9", lines)
        self.assertNotIn("line 51", lines)


# ---------------------------------------------------------------------------
# R6: packet and verify share one check resolver.
# ---------------------------------------------------------------------------

class TestR6OneResolver(_GitRepo):
    def test_r6_packet_carries_the_lane_pr_check_verify_would_run(self):
        release = _load_release()
        _no_own_check(release)
        _stub_pr_lookup(release, [_PR_1527, _PR_1539])
        packet = release.build_packet("o", "r", ["1525"], self.sha, repo_root=str(self.repo))
        self.assertIn("Check: new-check-1539", packet)
        self.assertNotIn("CHECK: MISSING", packet)
        self.assertEqual(release._resolve_check("o", "r", "1525"), "new-check-1539")

    def test_r6_packet_calls_the_shared_resolver(self):
        release = _load_release()
        release._fetch_issue_json = lambda owner, repo, num: {"number": int(num), "body": "x"}
        release._fetch_comments = lambda owner, repo, num: []
        seen = []

        def _sentinel(owner, repo, num, *a, **k):
            seen.append(num)
            return "sentinel-check"

        release._resolve_check = _sentinel
        packet = release.build_packet("o", "r", ["41", "42"], self.sha, repo_root=str(self.repo))
        self.assertEqual(seen, ["41", "42"])
        self.assertEqual(packet.count("Check: sentinel-check"), 2)

    def test_r6_packet_strips_backticks_like_verify(self):
        release = _load_release()
        release._fetch_issue_json = lambda owner, repo, num: {
            "number": int(num), "body": "Check: `echo wrapped`",
        }
        release._fetch_comments = lambda owner, repo, num: []
        packet = release.build_packet("o", "r", ["43"], self.sha, repo_root=str(self.repo))
        self.assertIn("Check: echo wrapped\n", packet)


if __name__ == "__main__":
    unittest.main()
