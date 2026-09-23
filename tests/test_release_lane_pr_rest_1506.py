"""
tests/test_release_lane_pr_rest_1506.py

PR #1528 round-2 BLOCK (slice #1506 / PRD #1501, ADR-0090 D3/D4, ADR-0087
context): the `verify` lane-PR lookup read the GitHub search index as
ground truth, and ran a lane PR's `Check #<n>:` line with no trusted-author
filter. Every gh read is stubbed in-process through `release._run_gh`; no
test reaches a real gh, and nothing is written outside tempfile dirs
(rule #21).

  B1  the lane PR is resolved from a REST read of the issue's own timeline
      (`repos/{owner}/{repo}/issues/<n>/timeline`, `cross-referenced`
      events), never `gh pr list --search`: under a renamed slug the search
      index answers 0 while REST answers 2 (#1525: #1527 and #1539). The
      newest `merged_at` still wins (R1). A read that fails or cannot be
      parsed prints `UNCONFIRMED #<n>` and exits non-zero; `MISSING` stays
      reserved for a lookup that succeeded and found no check. The packet
      refuses rather than print `CHECK: MISSING` from a failed read, and
      `dispatch --lane` then refuses with no span.
  B1 sweep (rule #19, the same class in the release tools' other reads):
      a failed comment read, issue read, sub-issue read or a paginated
      answer with an undecodable page is never read as "none".
  B2  a lane PR's check is executed or carried into the packet only when
      the PR author's `author_association` is OWNER, MEMBER or
      COLLABORATOR; otherwise the check is absent (`MISSING`), and the
      packet names the ignored source.
  Rec 4  the packet labels its check with its source: `(from issue)` or
      `(from lane PR #<m>)`, on the line after the `Check:` line so the
      `Check:` line stays exactly the command `verify` runs.
"""
import argparse
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import re
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
RELEASE_PY = REPO_ROOT / "tools" / "release.py"
DISPATCH = REPO_ROOT / "tools" / "pipe" / "dispatch"

REPO_URL = "https://api.github.com/repos/o/r"
FOREIGN_REPO_URL = "https://api.github.com/repos/someone-else/elsewhere"

PASS_CMD = f'"{sys.executable}" -c "import sys; sys.exit(0)"'
FAIL_CMD = f'"{sys.executable}" -c "import sys; sys.exit(1)"'

_COUNTER = [0]


def _load(path, prefix):
    _COUNTER[0] += 1
    loader = importlib.machinery.SourceFileLoader(f"{prefix}_{_COUNTER[0]}", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class _Res:
    def __init__(self, stdout="", returncode=0):
        self.stdout, self.stderr, self.returncode = stdout, "", returncode


def _ok(obj):
    return (json.dumps(obj), 0)


_ERR = ("", 1)


class _FakeGh:
    """Routes `_run_gh(args)` by the request it names. `issues`,
    `comments`, `timeline` and `search` map an issue number to a
    `(stdout, returncode)` answer. Any other call answers rc=1."""

    def __init__(self, issues=None, comments=None, timeline=None, search=None):
        self.issues = issues or {}
        self.comments = comments or {}
        self.timeline = timeline or {}
        self.search = search or {}
        self.calls = []

    def __call__(self, args):
        args = list(args)
        self.calls.append(args)
        if "--search" in args:
            m = re.search(r"#(\d+)", args[args.index("--search") + 1])
            return _Res(*self.search.get(m.group(1) if m else "", _ERR))
        if len(args) >= 2 and args[0] == "api":
            m = re.fullmatch(r"repos/o/r/issues/(\d+)(/comments|/timeline)?", args[1])
            if m:
                table = {None: self.issues, "/comments": self.comments, "/timeline": self.timeline}[m.group(2)]
                return _Res(*table.get(m.group(1), _ERR))
        return _Res(*_ERR)


def _issue(num, body="No check here.", **extra):
    issue = {
        "number": int(num), "state": "open", "body": body, "repository_url": REPO_URL,
        "labels": [{"name": "bug"}], "milestone": {"number": 1, "title": "v1.0"},
    }
    issue.update(extra)
    return issue


def _xref(number, body, merged_at, labels=("lane",), association="OWNER", repo_url=REPO_URL):
    """A REST timeline `cross-referenced` event whose source is a PR."""
    pr = {"url": f"{repo_url}/pulls/{number}", "merged_at": merged_at}
    return {
        "event": "cross-referenced",
        "source": {"type": "issue", "issue": {
            "number": number, "body": body, "author_association": association,
            "labels": [{"name": l} for l in labels], "repository_url": repo_url,
            "pull_request": pr,
        }},
    }


def _search_pr(number, body, merged_at, labels=("lane",)):
    """The same PR as `gh pr list --search ... --json` shapes it."""
    return {"number": number, "body": body, "mergedAt": merged_at,
            "labels": [{"name": l} for l in labels]}


def _verify(release, fake, nums):
    release._run_gh = fake
    release._remote_owner_repo = lambda: ("o", "r")
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = release._cmd_verify(argparse.Namespace(issues=[str(n) for n in nums]))
    return rc, out.getvalue(), err.getvalue()


def _lines(text):
    return [l for l in text.splitlines() if l.strip()]


# The real #1525 shape: #1527 merged first with a non-discriminating check;
# #1539 merged later with the real one. Here #1527's check FAILS and
# #1539's PASSES, so the printed line names which PR's check ran.
_BODY_1527 = f"Closes #1525\n\nCheck #1525: {FAIL_CMD}\n"
_BODY_1539 = f"Closes #1525\n\nCheck #1525: {PASS_CMD}\n"
_EVENTS_1525 = [
    {"event": "labeled", "label": {"name": "bug"}},
    _xref(1527, _BODY_1527, "2026-09-23T12:10:45Z"),
    {"event": "referenced", "commit_id": "6902ca048f1ed5d489f77274760c690d2d799ef2"},
    _xref(1528, "Closes #1506\n", None, labels=()),
    _xref(1539, _BODY_1539, "2026-09-23T14:59:00Z"),
]


def _renamed_slug_gh(timeline_events=_EVENTS_1525):
    """Search answers 0 (the renamed-slug index), REST answers the PRs."""
    return _FakeGh(
        issues={"1525": _ok(_issue(1525))},
        comments={"1525": _ok([])},
        timeline={"1525": _ok(timeline_events)},
        search={"1525": _ok([])},
    )


# ---------------------------------------------------------------------------
# B1: the lane PR comes from REST, never the search index.
# ---------------------------------------------------------------------------

class TestB1RestTimelineLookup(unittest.TestCase):
    def test_b1_renamed_slug_search_empty_rest_two_resolves_newest(self):
        release = _load(RELEASE_PY, "b1_renamed")
        rc, out, _err = _verify(release, _renamed_slug_gh(), [1525])
        self.assertEqual(_lines(out), ["PASS #1525"])
        self.assertEqual(rc, 0)

    def test_b1_lookup_makes_no_search_call(self):
        release = _load(RELEASE_PY, "b1_nosearch")
        fake = _renamed_slug_gh()
        _verify(release, fake, [1525])
        self.assertFalse(
            [c for c in fake.calls if "--search" in c or "search" in c[:2]],
            f"the lane-PR lookup must not touch the search index: {fake.calls}",
        )
        self.assertTrue(
            [c for c in fake.calls if c[:2] == ["api", "repos/o/r/issues/1525/timeline"]],
            f"the lane-PR lookup must read the issue's REST timeline: {fake.calls}",
        )

    def test_b1_newest_merged_at_wins_whatever_the_event_order(self):
        release = _load(RELEASE_PY, "b1_newest")
        events = [
            _xref(1539, f"Closes #1525\nCheck #1525: {FAIL_CMD}\n", "2026-09-23T14:59:00Z"),
            _xref(1527, f"Closes #1525\nCheck #1525: {PASS_CMD}\n", "2026-09-23T12:10:45Z"),
        ]
        rc, out, _err = _verify(release, _renamed_slug_gh(events), [1525])
        self.assertEqual(_lines(out), ["FAIL #1525"])
        self.assertNotEqual(rc, 0)

    def test_b1_timeline_gh_error_is_unconfirmed_not_missing(self):
        release = _load(RELEASE_PY, "b1_err")
        fake = _FakeGh(
            issues={"1525": _ok(_issue(1525))}, comments={"1525": _ok([])},
            timeline={"1525": _ERR}, search={"1525": _ERR},
        )
        rc, out, err = _verify(release, fake, [1525])
        self.assertEqual(_lines(out), ["UNCONFIRMED #1525"])
        self.assertNotIn("MISSING", out)
        self.assertNotEqual(rc, 0)
        self.assertIn("unconfirmed", err.lower())

    def test_b1_timeline_unparsable_is_unconfirmed_not_missing(self):
        release = _load(RELEASE_PY, "b1_garbage")
        fake = _FakeGh(
            issues={"1525": _ok(_issue(1525))}, comments={"1525": _ok([])},
            timeline={"1525": ("<html>rate limited</html>", 0)},
            search={"1525": ("<html>rate limited</html>", 0)},
        )
        rc, out, _err = _verify(release, fake, [1525])
        self.assertEqual(_lines(out), ["UNCONFIRMED #1525"])
        self.assertNotEqual(rc, 0)

    def test_b1_truncated_page_is_unconfirmed_not_missing(self):
        release = _load(RELEASE_PY, "b1_truncated")
        page1 = json.dumps([{"event": "labeled"}])
        fake = _FakeGh(
            issues={"1525": _ok(_issue(1525))}, comments={"1525": _ok([])},
            timeline={"1525": (page1 + '[{"event": "cross-ref', 0)},
            search={"1525": (page1 + '[{"number": 15', 0)},
        )
        rc, out, _err = _verify(release, fake, [1525])
        self.assertEqual(_lines(out), ["UNCONFIRMED #1525"])
        self.assertNotEqual(rc, 0)

    def test_b1_issue_read_error_is_unconfirmed_not_missing(self):
        release = _load(RELEASE_PY, "b1_issue_err")
        fake = _FakeGh(
            issues={"1525": _ERR}, comments={"1525": _ok([])},
            timeline={"1525": _ok([])}, search={"1525": _ok([])},
        )
        rc, out, _err = _verify(release, fake, [1525])
        self.assertEqual(_lines(out), ["UNCONFIRMED #1525"])
        self.assertNotEqual(rc, 0)

    def test_b1_comment_read_error_is_unconfirmed_not_missing(self):
        """A trusted comment may carry the issue's own `Check:` line, so a
        failed comment read cannot be read as "no check"."""
        release = _load(RELEASE_PY, "b1_comments_err")
        fake = _FakeGh(
            issues={"1525": _ok(_issue(1525))}, comments={"1525": _ERR},
            timeline={"1525": _ok([])}, search={"1525": _ok([])},
        )
        rc, out, _err = _verify(release, fake, [1525])
        self.assertEqual(_lines(out), ["UNCONFIRMED #1525"])
        self.assertNotEqual(rc, 0)

    def test_b1_a_successful_empty_lookup_is_still_missing(self):
        release = _load(RELEASE_PY, "b1_empty")
        fake = _FakeGh(
            issues={"1525": _ok(_issue(1525))}, comments={"1525": _ok([])},
            timeline={"1525": _ok([{"event": "labeled"}])}, search={"1525": _ok([])},
        )
        rc, out, _err = _verify(release, fake, [1525])
        self.assertEqual(_lines(out), ["MISSING #1525"])
        self.assertNotEqual(rc, 0)

    def test_b1_one_line_per_issue_with_an_unconfirmed_one(self):
        release = _load(RELEASE_PY, "b1_mixed")
        fake = _FakeGh(
            issues={"1": _ERR, "2": _ok(_issue(2, body=f"Check: {PASS_CMD}"))},
            comments={"1": _ok([]), "2": _ok([])},
        )
        rc, out, _err = _verify(release, fake, [1, 2])
        self.assertEqual(_lines(out), ["UNCONFIRMED #1", "PASS #2"])
        self.assertNotEqual(rc, 0)


class TestB1PacketAndDispatch(unittest.TestCase):
    def test_b1_packet_resolves_the_lane_pr_via_rest(self):
        release = _load(RELEASE_PY, "b1_packet_rest")
        release._run_gh = _renamed_slug_gh()
        packet = release.build_packet("o", "r", ["1525"], "abc123")
        self.assertIn(f"Check: {PASS_CMD}\n", packet)
        self.assertNotIn("CHECK: MISSING", packet)

    def test_b1_packet_refuses_on_an_unconfirmed_lookup(self):
        release = _load(RELEASE_PY, "b1_packet_err")
        release._run_gh = _FakeGh(
            issues={"1525": _ok(_issue(1525))}, comments={"1525": _ok([])},
            timeline={"1525": _ERR}, search={"1525": _ERR},
        )
        release._remote_owner_repo = lambda: ("o", "r")
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = release._cmd_packet(argparse.Namespace(issues=["1525"], sha="abc123"))
        self.assertNotEqual(rc, 0)
        self.assertNotIn("CHECK: MISSING", out.getvalue())
        self.assertIn("unconfirmed", err.getvalue().lower())

    def test_b1_dispatch_lane_refuses_with_no_span_on_an_unconfirmed_lookup(self):
        release = _load(RELEASE_PY, "b1_dispatch_release")
        release._run_gh = _FakeGh(
            issues={"1525": _ok(_issue(1525))}, comments={"1525": _ok([])},
            timeline={"1525": _ERR}, search={"1525": _ERR},
        )
        release._remote_owner_repo = lambda: ("o", "r")
        dispatch = _load(DISPATCH, "b1_dispatch")
        spans = []
        dispatch._load_release = lambda: release
        dispatch._load_trace = lambda: types.SimpleNamespace(emit_span=lambda **kw: spans.append(kw))
        dispatch._fetch_integration_sha = lambda: ("lanebase", "abc123")
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = dispatch.main(
                ["--lane", "fix/1525-lane-x", "--milestone", "v1.0", "--model", "sonnet", "1525"]
            )
        self.assertNotEqual(rc, 0)
        self.assertEqual(spans, [])
        self.assertNotIn("CHECK: MISSING", out.getvalue())


# ---------------------------------------------------------------------------
# B1 sweep (rule #19): the release tools' other reads never read a failed
# answer as "none".
# ---------------------------------------------------------------------------

class TestB1SweepOtherReads(unittest.TestCase):
    def test_sweep_paginated_read_with_an_undecodable_page_is_none(self):
        release = _load(RELEASE_PY, "sweep_paginate")
        release._run_gh = lambda args: _Res('[{"a": 1}]\n[{"b": ')
        self.assertIsNone(release._gh_api_json("repos/o/r/issues/1/comments", paginate=True))

    def test_sweep_lanes_refuses_on_a_comment_read_error(self):
        release = _load(RELEASE_PY, "sweep_lanes")
        release._remote_owner_repo = lambda: ("o", "r")
        release._fetch_open_issues = lambda owner, repo: [_issue(1525)]
        release._run_gh = _FakeGh(comments={"1525": _ERR})
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = release._cmd_lanes(argparse.Namespace(version="v1.0", evidence=None, priority=None))
        self.assertNotEqual(rc, 0)
        self.assertEqual(out.getvalue(), "")
        self.assertIn("refused", err.getvalue())

    def test_sweep_freeze_refuses_before_any_mutation_on_a_sub_issue_read_error(self):
        release = _load(RELEASE_PY, "sweep_freeze")
        release._remote_owner_repo = lambda: ("o", "r")
        calls = []

        def _gh(args):
            args = list(args)
            calls.append(args)
            if args[:2] == ["api", "repos/o/r/issues"]:
                return _Res(json.dumps([
                    {"number": 201, "state": "open", "labels": [{"name": "feature"}], "milestone": None},
                    {"number": 101, "state": "open", "labels": [{"name": "bug"}], "milestone": None},
                ]))
            if args[:2] == ["api", "repos/o/r/milestones"] and "POST" not in args:
                return _Res(json.dumps([{"number": 1, "title": "v1.0"}, {"number": 2, "title": "v1.1"}]))
            if args[:2] == ["api", "repos/o/r/issues/201/sub_issues"]:
                return _Res("", 1)
            return _Res("{}")

        release._run_gh = _gh
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            rc = release._cmd_freeze(argparse.Namespace(
                version="v1.0", next_version="v1.1", features="201", classes=None,
            ))
        self.assertNotEqual(rc, 0)
        mutations = [c for c in calls if "PATCH" in c or "POST" in c or c[:2] == ["issue", "edit"]]
        self.assertEqual(mutations, [], "freeze must refuse before any mutation")
        self.assertIn("sub-issue", err.getvalue())


# ---------------------------------------------------------------------------
# B2: a lane PR's check counts only from a trusted author.
# ---------------------------------------------------------------------------

class TestB2TrustedLanePrAuthor(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="release_b2_"))
        self.sentinel = self.tmp / "executed"
        self.cmd = (
            f'"{sys.executable}" -c "open(\'{self.sentinel.as_posix()}\', \'w\').write(\'x\')"'
        )

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _gh(self, association, body=None, merged_at="2026-09-23T14:59:00Z", extra=()):
        body = body or f"Closes #1525\nCheck #1525: {self.cmd}\n"
        return _FakeGh(
            issues={"1525": _ok(_issue(1525))}, comments={"1525": _ok([])},
            timeline={"1525": _ok(list(extra) + [_xref(1539, body, merged_at, association=association)])},
            search={"1525": _ok([_search_pr(1539, body, merged_at)])},
        )

    def test_b2_untrusted_lane_pr_check_is_never_executed(self):
        release = _load(RELEASE_PY, "b2_untrusted")
        rc, out, _err = _verify(release, self._gh("CONTRIBUTOR"), [1525])
        self.assertEqual(_lines(out), ["MISSING #1525"])
        self.assertNotEqual(rc, 0)
        self.assertFalse(self.sentinel.exists(), "an untrusted lane PR's check ran")

    def test_b2_only_owner_member_collaborator_are_trusted(self):
        for association, trusted in [
            ("OWNER", True), ("MEMBER", True), ("COLLABORATOR", True),
            ("CONTRIBUTOR", False), ("FIRST_TIME_CONTRIBUTOR", False),
            ("FIRST_TIMER", False), ("MANNEQUIN", False), ("NONE", False),
        ]:
            with self.subTest(association=association):
                if self.sentinel.exists():
                    self.sentinel.unlink()
                release = _load(RELEASE_PY, "b2_assoc")
                _rc, out, _err = _verify(release, self._gh(association), [1525])
                self.assertEqual(_lines(out), ["PASS #1525" if trusted else "MISSING #1525"])
                self.assertEqual(self.sentinel.exists(), trusted)

    def test_b2_an_untrusted_newest_pr_does_not_fall_back_to_an_older_trusted_one(self):
        release = _load(RELEASE_PY, "b2_no_fallback")
        older = _xref(1527, f"Closes #1525\nCheck #1525: {PASS_CMD}\n", "2026-09-23T12:10:45Z")
        rc, out, _err = _verify(release, self._gh("NONE", extra=[older]), [1525])
        self.assertEqual(_lines(out), ["MISSING #1525"])
        self.assertNotEqual(rc, 0)
        self.assertFalse(self.sentinel.exists())

    def test_b2_a_cross_repository_reference_is_never_a_lane_pr(self):
        """Guard for the REST source (search was scoped by `--repo`, so this
        is new-code surface, not a pre-fix defect): a PR in ANOTHER
        repository can cross-reference the issue, and its OWNER
        association is relative to that repository, so it never counts."""
        release = _load(RELEASE_PY, "b2_foreign")
        body = f"Closes #1525\nCheck #1525: {self.cmd}\n"
        fake = _FakeGh(
            issues={"1525": _ok(_issue(1525))}, comments={"1525": _ok([])},
            timeline={"1525": _ok([_xref(77, body, "2026-09-24T09:00:00Z", repo_url=FOREIGN_REPO_URL)])},
        )
        rc, out, _err = _verify(release, fake, [1525])
        self.assertEqual(_lines(out), ["MISSING #1525"])
        self.assertNotEqual(rc, 0)
        self.assertFalse(self.sentinel.exists(), "a foreign repository's check ran")

    def test_b2_packet_omits_an_untrusted_lane_pr_check_and_names_its_source(self):
        release = _load(RELEASE_PY, "b2_packet")
        release._run_gh = self._gh("CONTRIBUTOR")
        packet = release.build_packet("o", "r", ["1525"], "abc123")
        self.assertNotIn(self.cmd, packet)
        self.assertIn("CHECK: MISSING\n", packet)
        self.assertIn("lane PR #1539", packet)
        self.assertIn("CONTRIBUTOR", packet)


# ---------------------------------------------------------------------------
# Rec 4: the packet labels its check with its source.
# ---------------------------------------------------------------------------

class TestRec4PacketCheckProvenance(unittest.TestCase):
    def test_rec4_issue_check_is_labeled_from_issue(self):
        release = _load(RELEASE_PY, "rec4_issue")
        release._run_gh = _FakeGh(
            issues={"7": _ok(_issue(7, body="Broken.\nCheck: echo own"))}, comments={"7": _ok([])},
        )
        packet = release.build_packet("o", "r", ["7"], "abc123")
        self.assertIn("Check: echo own\n(from issue)\n", packet)

    def test_rec4_lane_pr_check_is_labeled_from_lane_pr(self):
        release = _load(RELEASE_PY, "rec4_lane_pr")
        release._run_gh = _renamed_slug_gh()
        packet = release.build_packet("o", "r", ["1525"], "abc123")
        self.assertIn(f"Check: {PASS_CMD}\n(from lane PR #1539)\n", packet)


if __name__ == "__main__":
    unittest.main()
