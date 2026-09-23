"""
tests/test_release_mode_encoding_1506.py

Round-2 fix of PR #1528 (reviewer findings F2 and R-TESTS): the release-mode
tools must decode every gh/git/check subprocess as UTF-8 and write their
packets to stdout as UTF-8, whatever the host's locale. The round-1 review
ran on a Windows host whose locale and pipe encoding are cp1252:

- a packet excerpt decoded as cp1252 turned an em-dash into mojibake, so it
  was not the text at that sha (criterion 21);
- the verify lookup (`gh search prs` then; the REST issue timeline since
  the round-2 BLOCK) and the verify check run had the same
  gap, and a byte cp1252 leaves undefined (0x81) crashes a strict decode;
- `dispatch --lane` recorded its span and then died writing a U+2192 packet
  to cp1252 stdout: a span with no delivered packet (a PIP-014 half-success).

The host locale is simulated deterministically on every platform:
- `_cp1252_host_run` stands in for `subprocess.run`: a call that names no
  `encoding` decodes its captured bytes as strict cp1252, exactly what
  `text=True` does on that host. Canned gh answers mean no test reaches a
  real gh; any other gh call fails the test.
- stdout is a cp1252 `TextIOWrapper` in-process, or `PYTHONIOENCODING=cp1252`
  for the one real-CLI test.

Also covers the two earlier round-1 fix commits, which shipped without
tests: 59135c1 (`_run_gh` decodes UTF-8 in release.py, dispatch and
pr-merge) and 8da5415 (`_gh_api_json` always names `-X <method>`, because
`gh api` with `-f` fields and no `-X` sends a POST).

No test here runs `freeze`, a merge that chains record-green, or any
push: every repository is a throwaway under pytest's tmp_path (rule #21).
"""
import argparse
import ast
import importlib.machinery
import importlib.util
import io
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

try:
    import pytest
except ImportError:  # CI's unittest fallback runs without pytest (CHECK 12 / #985)
    pytest = None

REPO_ROOT = Path(__file__).parent.parent
RELEASE_PY = REPO_ROOT / "tools" / "release.py"
DISPATCH = REPO_ROOT / "tools" / "pipe" / "dispatch"
PR_MERGE = REPO_ROOT / "tools" / "pipe" / "pr-merge"

_REAL_RUN = subprocess.run
# U+00C1 encodes to C3 81 in UTF-8; 0x81 is undefined in cp1252, so a strict
# cp1252 decode of it raises -- the "first non-ASCII gh byte" crash.
_NON_CP1252 = "Á — →"


def _load(path, name):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def _is_gh(cmd):
    if isinstance(cmd, (list, tuple)) and cmd:
        return os.path.basename(str(cmd[0])).lower().split(".")[0] == "gh"
    return False


def _cp1252_host_run(canned=()):
    """A `subprocess.run` stand-in for a host whose locale encoding is
    cp1252. `canned` is a list of `(predicate(cmd), returncode, stdout_bytes)`;
    a matching call never spawns a process."""
    def run(cmd, *args, **kwargs):
        text = kwargs.pop("text", None)
        universal = kwargs.pop("universal_newlines", None)
        encoding = kwargs.pop("encoding", None)
        errors = kwargs.pop("errors", None)
        check = kwargs.pop("check", False)
        for match, rc, out in canned:
            if match(cmd):
                res = subprocess.CompletedProcess(cmd, rc, out, b"")
                break
        else:
            if _is_gh(cmd):
                raise AssertionError(f"unexpected real gh call in a test: {cmd!r}")
            res = _REAL_RUN(cmd, *args, **kwargs)
        if text or universal or encoding or errors:
            enc, err = encoding or "cp1252", errors or "strict"
            if isinstance(res.stdout, bytes):
                res.stdout = res.stdout.decode(enc, err)
            if isinstance(res.stderr, bytes):
                res.stderr = res.stderr.decode(enc, err)
        if check and res.returncode != 0:
            raise subprocess.CalledProcessError(res.returncode, cmd, res.stdout, res.stderr)
        return res
    return run


def _cp1252_stdout():
    raw = io.BytesIO()
    return raw, io.TextIOWrapper(raw, encoding="cp1252", write_through=True)


def _git(args, cwd):
    return _REAL_RUN(["git"] + args, cwd=str(cwd), check=True, capture_output=True)


def _repo_with_notes(tmp_path, text):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-q"], repo)
    _git(["config", "user.email", "t@example.com"], repo)
    _git(["config", "user.name", "t"], repo)
    (repo / "notes.md").write_bytes((text + "\n").encode("utf-8"))
    _git(["add", "notes.md"], repo)
    _git(["commit", "-q", "-m", "seed"], repo)
    sha = _git(["rev-parse", "HEAD"], repo).stdout.decode("ascii").strip()
    return repo, sha


# ---------------------------------------------------------------------------
# The class guard: every text-mode subprocess.run in the Python files this
# PR touches names an encoding (so none falls back to the host locale).
# ---------------------------------------------------------------------------

def _text_runs_without_encoding(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    offenders = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "run"):
            continue
        kw = {k.arg: k.value for k in node.keywords if k.arg}
        text_mode = any(
            isinstance(kw.get(name), ast.Constant) and kw[name].value is True
            for name in ("text", "universal_newlines")
        )
        if text_mode and "encoding" not in kw:
            offenders.append(node.lineno)
    return offenders


_THREE_FILES = [RELEASE_PY, DISPATCH, PR_MERGE]
_THREE_IDS = ["release", "dispatch", "pr-merge"]
# Every Python file PR #1528 touches: the three release-mode tools plus the
# three it edits for the rule layer and DRAIN-LEDGER (rule #19: the class is
# closed across the PR's files, not only the ones the review named).
_TOUCHED_FILES = _THREE_FILES + [
    REPO_ROOT / "dashboard" / "health.py",
    REPO_ROOT / "tools" / "gen_rules.py",
    REPO_ROOT / "tools" / "gen_repo_map.py",
]
_TOUCHED_IDS = _THREE_IDS + ["health", "gen_rules", "gen_repo_map"]

# Parametrized only when pytest is present; the stdlib unittest fallback
# collects no module-level function in this file anyway.
if pytest is not None:
    @pytest.mark.parametrize("path", _TOUCHED_FILES, ids=_TOUCHED_IDS)
    def test_every_text_subprocess_names_an_encoding(path):
        assert _text_runs_without_encoding(path) == [], (
            f"{path.name}: text-mode subprocess.run without encoding= at these lines "
            "(they decode as the host locale, cp1252 on Windows)"
        )

    # 59135c1: `_run_gh` decodes UTF-8 in all three files.
    @pytest.mark.parametrize("path", _THREE_FILES, ids=_THREE_IDS)
    def test_run_gh_decodes_utf8_on_cp1252_host(path, monkeypatch):
        mod = _load(path, f"enc_run_gh_{path.name.replace('.', '_').replace('-', '_')}")
        canned = [(_is_gh, 0, (_NON_CP1252 + "\n").encode("utf-8"))]
        monkeypatch.setattr(subprocess, "run", _cp1252_host_run(canned))
        res = mod._run_gh(["api", "repos/o/r/issues/1"])
        assert res.stdout == _NON_CP1252 + "\n"


# ---------------------------------------------------------------------------
# 8da5415: every `_gh_api_json` call names its method, so a read with `-f`
# fields stays a GET (gh api defaults to POST once a field is given).
# ---------------------------------------------------------------------------

def test_gh_api_json_names_the_method_on_every_read_and_write(monkeypatch):
    release = _load(RELEASE_PY, "enc_gh_api_method")
    calls = []

    class _Res:
        returncode = 0
        stderr = ""

        def __init__(self, out):
            self.stdout = out

    def _record(args):
        calls.append(list(args))
        return _Res("[]" if "--paginate" in args else "{}")

    monkeypatch.setattr(release, "_run_gh", _record)

    release._fetch_open_issues("o", "r")
    release._fetch_milestones("o", "r")
    release._fetch_comments("o", "r", "7")
    release._fetch_sub_issues("o", "r", "7")
    release._fetch_issue_json("o", "r", "7")
    reads = list(calls)
    calls.clear()
    release._gh_api_json("repos/o/r/milestones", method="POST", fields={"title": "v9.9"})

    def _method(args):
        i = args.index("-X") if "-X" in args else -1
        return args[i + 1] if i >= 0 else None

    assert len(reads) == 5
    assert [_method(a) for a in reads] == ["GET"] * 5, reads
    assert _method(calls[0]) == "POST", calls


# ---------------------------------------------------------------------------
# release.py: packet excerpt, verify lookup, verify check run, packet CLI.
# ---------------------------------------------------------------------------

def test_packet_excerpt_is_the_utf8_text_at_that_sha_on_cp1252_host(tmp_path, monkeypatch):
    line = "alpha — beta → gamma"
    repo, sha = _repo_with_notes(tmp_path, line)
    release = _load(RELEASE_PY, "enc_packet_excerpt")
    monkeypatch.setattr(subprocess, "run", _cp1252_host_run())
    excerpt = release._read_blob_excerpt(sha, "notes.md", 1, repo_root=str(repo))
    assert excerpt == line


def test_verify_lane_pr_lookup_decodes_utf8_on_cp1252_host(monkeypatch):
    release = _load(RELEASE_PY, "enc_verify_lookup")
    repo_url = "https://api.github.com/repos/o/r"
    events = [{"event": "cross-referenced", "source": {"type": "issue", "issue": {
        "number": 12, "body": "Closes #7\nCheck #7: grep -c '—' notes.md",
        "author_association": "OWNER", "repository_url": repo_url,
        "labels": [{"name": "lane"}], "pull_request": {"merged_at": "2026-09-23T14:59:00Z"},
    }}}]
    # The lookup reads the issue's REST timeline (round-2 BLOCK: never the
    # search index); its output is UTF-8 whatever the host locale.
    canned = [(lambda c: _is_gh(c) and "repos/o/r/issues/7/timeline" in c, 0,
               json.dumps(events, ensure_ascii=False).encode("utf-8"))]
    monkeypatch.setattr(subprocess, "run", _cp1252_host_run(canned))
    monkeypatch.setattr(release, "_fetch_issue_json",
                        lambda o, r, n: {"number": int(n), "body": "no check", "repository_url": repo_url})
    monkeypatch.setattr(release, "_fetch_comments", lambda o, r, n: [])
    assert release._resolve_check("o", "r", "7") == ("grep -c '—' notes.md", "lane PR #12")


def test_verify_check_run_survives_non_cp1252_output(monkeypatch, capsys):
    release = _load(RELEASE_PY, "enc_verify_run")
    check = "emit-a-non-cp1252-byte"
    canned = [(lambda c: c == check, 0, _NON_CP1252.encode("utf-8"))]
    monkeypatch.setattr(subprocess, "run", _cp1252_host_run(canned))
    monkeypatch.setattr(release, "_remote_owner_repo", lambda: ("o", "r"))
    monkeypatch.setattr(release, "_resolve_check", lambda o, r, n, issue=None: (check, "issue"))
    rc = release._cmd_verify(argparse.Namespace(issues=["7"]))
    assert rc == 0
    assert capsys.readouterr().out == "PASS #7\n"


def test_packet_cli_writes_utf8_to_cp1252_stdout(monkeypatch):
    release = _load(RELEASE_PY, "enc_packet_cli")
    packet = "SHA: abc\n#### Bug #7\nCheck: grep -c '→' notes.md — ok\n"
    monkeypatch.setattr(release, "_remote_owner_repo", lambda: ("o", "r"))
    monkeypatch.setattr(release, "build_packet", lambda o, r, nums, sha: packet)
    raw, out = _cp1252_stdout()
    monkeypatch.setattr(sys, "stdout", out)
    rc = release._cmd_packet(argparse.Namespace(issues=["7"], sha="abc"))
    out.flush()
    assert rc == 0
    assert raw.getvalue().decode("utf-8") == packet


# ---------------------------------------------------------------------------
# dispatch --lane: the packet reaches a cp1252 stdout, and a failed write
# records no span (packet first, span after).
# ---------------------------------------------------------------------------

_LANE_PACKET = "SHA: abc123\n#### Bug #7\nnotes.md:1\nCheck: grep -c '→' notes.md — ok\n"


def _dispatch_with_stubs(monkeypatch, tmp_path):
    import types

    dispatch = _load(DISPATCH, f"enc_dispatch_{tmp_path.name}")
    stub_release = types.SimpleNamespace(
        _remote_owner_repo=lambda: ("o", "r"),
        _fetch_issue_json=lambda o, r, n: {
            "number": int(n), "state": "open",
            "labels": [{"name": "bug"}], "milestone": {"number": 1, "title": "v9.9"},
        },
        _issue_labels=lambda issue: {l["name"] for l in issue.get("labels") or []},
        build_packet=lambda o, r, nums, sha: _LANE_PACKET,
    )
    monkeypatch.setattr(dispatch, "_load_release", lambda: stub_release)
    monkeypatch.setattr(dispatch, "_fetch_integration_sha", lambda: ("lanebase", "abc123"))

    def _no_subprocess(*a, **k):
        raise AssertionError(f"unexpected subprocess in a stubbed dispatch: {a!r}")

    monkeypatch.setattr(subprocess, "run", _no_subprocess)
    trace_log = tmp_path / "trace-v3.jsonl"
    monkeypatch.setenv("TRACE_LOG_OVERRIDE", str(trace_log))
    return dispatch, trace_log


def _spans(trace_log):
    if not trace_log.exists():
        return []
    return [json.loads(l) for l in trace_log.read_text(encoding="utf-8").splitlines() if l.strip()]


def test_dispatch_lane_packet_reaches_cp1252_stdout(monkeypatch, tmp_path):
    dispatch, trace_log = _dispatch_with_stubs(monkeypatch, tmp_path)
    raw, out = _cp1252_stdout()
    monkeypatch.setattr(sys, "stdout", out)
    rc = dispatch._cmd_dispatch_lane("fix/7-lane-notes", "v9.9", "sonnet", ["7"])
    out.flush()
    assert rc == 0
    assert raw.getvalue().decode("utf-8") == _LANE_PACKET
    spans = _spans(trace_log)
    assert [s["kind"] for s in spans] == ["dispatch"]
    assert spans[0]["attrs"]["lane"] == "fix/7-lane-notes"


class _BrokenPipeRaw(io.RawIOBase):
    def writable(self):
        return True

    def write(self, b):
        raise BrokenPipeError(32, "Broken pipe")


class _BrokenPipeStdout:
    encoding = "cp1252"
    buffer = _BrokenPipeRaw()

    def write(self, s):
        raise BrokenPipeError(32, "Broken pipe")

    def flush(self):
        pass


def test_dispatch_lane_failed_packet_write_records_no_span(monkeypatch, tmp_path, capsys):
    dispatch, trace_log = _dispatch_with_stubs(monkeypatch, tmp_path)
    monkeypatch.setattr(sys, "stdout", _BrokenPipeStdout())
    rc = dispatch._cmd_dispatch_lane("fix/7-lane-notes", "v9.9", "sonnet", ["7"])
    assert rc != 0
    assert _spans(trace_log) == [], "a dispatch span with no delivered packet is a half-success"
    assert "packet" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# dispatch --lane through the real CLI, with PYTHONIOENCODING=cp1252 so the
# child's stdout is cp1252 on every platform. Throwaway origin built by
# `git clone --bare` (no push), integration branch named by a throwaway
# `.claude/pipeline.conf`.
# ---------------------------------------------------------------------------

_FAKE_GH_BODY = r'''
import sys, os, json
a = sys.argv[1:]
key = " ".join(a)
if a and a[0] == "api":
    method = a[a.index("-X") + 1] if "-X" in a else "GET"
    fields = sorted(a[i + 1] for i in range(len(a)) if a[i] in ("-f", "-F") and i + 1 < len(a))
    key = ("API " + method + " " + a[1] + " " + " ".join(fields)).strip()
with open(os.environ["FAKE_GH_ROUTES_FILE"], encoding="utf-8") as f:
    routes = json.load(f)
if key not in routes:
    sys.stderr.write("fake gh: no route for " + key + "\n")
    sys.exit(97)
out, code = routes[key]
sys.stdout.write(out)
sys.exit(code)
'''


def _write_fake_gh(dirpath):
    if platform.system() == "Windows":
        impl = dirpath / "_fake_gh_impl.py"
        impl.write_text(_FAKE_GH_BODY, encoding="utf-8")
        with open(dirpath / "gh.bat", "w", encoding="utf-8", newline="\r\n") as f:
            f.write(f'@echo off\r\n"{sys.executable}" "{impl}" %*\r\n')
    else:
        sh = dirpath / "gh"
        sh.write_text("#!/usr/bin/env python3\n" + _FAKE_GH_BODY, encoding="utf-8")
        os.chmod(sh, 0o755)


def test_dispatch_lane_cli_prints_utf8_packet_under_cp1252_stdout(tmp_path):
    line = "alpha — beta → gamma"
    seed = tmp_path / "seed"
    seed.mkdir()
    _git(["init", "-q", "-b", "lanebase"], seed)
    _git(["config", "user.email", "t@example.com"], seed)
    _git(["config", "user.name", "t"], seed)
    (seed / "notes.md").write_bytes((line + "\n").encode("utf-8"))
    _git(["add", "notes.md"], seed)
    _git(["commit", "-q", "-m", "seed"], seed)
    origin = tmp_path / "o" / "r.git"
    origin.parent.mkdir()
    _git(["clone", "-q", "--bare", str(seed), str(origin)], tmp_path)
    work = tmp_path / "work"
    _git(["clone", "-q", str(origin).replace("\\", "/"), str(work)], tmp_path)
    (work / ".claude").mkdir()
    (work / ".claude" / "pipeline.conf").write_text(
        "integration_branch=lanebase\nrelease_branch=lanerelease\n", encoding="utf-8",
    )

    issue = {
        "number": 7, "state": "open",
        "labels": [{"name": "bug"}], "milestone": {"number": 1, "title": "v9.9"},
        "body": "Broken at `notes.md:1`.\nCheck: grep -c '→' notes.md",
    }
    routes = {
        "API GET repos/o/r/issues/7": [json.dumps(issue), 0],
        "API GET repos/o/r/issues/7/comments per_page=100": ["[]", 0],
    }
    routes_file = tmp_path / "routes.json"
    routes_file.write_text(json.dumps(routes), encoding="utf-8")
    fake_dir = tmp_path / "fakebin"
    fake_dir.mkdir()
    _write_fake_gh(fake_dir)
    trace_log = tmp_path / "trace-v3.jsonl"

    env = os.environ.copy()
    env["PATH"] = str(fake_dir) + os.pathsep + env.get("PATH", "")
    env["FAKE_GH_ROUTES_FILE"] = str(routes_file)
    env["TRACE_LOG_OVERRIDE"] = str(trace_log)
    env["PYTHONIOENCODING"] = "cp1252"
    env.pop("PYTHONUTF8", None)
    res = _REAL_RUN(
        [sys.executable, str(DISPATCH), "--lane", "fix/7-lane-notes",
         "--milestone", "v9.9", "--model", "sonnet", "7"],
        capture_output=True, cwd=str(work), env=env, timeout=120,
    )
    stdout = res.stdout.decode("utf-8")
    stderr = res.stderr.decode("utf-8", "replace")
    assert res.returncode == 0, stderr
    assert line in stdout, stdout
    assert "Check: grep -c '→' notes.md" in stdout
    spans = _spans(trace_log)
    assert [s["kind"] for s in spans] == ["dispatch"]
    assert stdout.startswith("SHA: " + spans[0]["attrs"]["sha"])


# ---------------------------------------------------------------------------
# pr-merge: echoing gh's merge output to a cp1252 stdout must not abort the
# post-merge recording (a merged PR with no pr_merged span).
# ---------------------------------------------------------------------------

def test_pr_merge_echo_of_non_cp1252_gh_output_still_records(monkeypatch, tmp_path):
    mod = _load(PR_MERGE, "enc_pr_merge_echo")
    trace_log = tmp_path / "trace-v3.jsonl"
    monkeypatch.setenv("TRACE_LOG_OVERRIDE", str(trace_log))

    def _no_subprocess(*a, **k):
        raise AssertionError(f"unexpected subprocess in a stubbed merge: {a!r}")

    monkeypatch.setattr(subprocess, "run", _no_subprocess)
    monkeypatch.setattr(mod, "_gh", lambda: "gh-disabled-in-test")
    view = {
        "comments": [{"body": "VERDICT: APPROVE\nREASON: ok\nROUND: 1\nCRITIC: reviewer"}],
        "labels": [{"name": "slice"}], "body": "Closes #7030", "headRefName": "feat/7030-x",
    }

    class _Res:
        def __init__(self, rc=0, out="", err=""):
            self.returncode, self.stdout, self.stderr = rc, out, err

    def _fake_run_gh(args):
        if args[:2] == ["pr", "view"]:
            return _Res(out=json.dumps(view))
        if args[:2] == ["pr", "merge"]:
            return _Res(out="✓ Squashed and merged pull request #7030 (fix → thing)\n")
        raise AssertionError(f"unexpected gh call {args!r}")

    monkeypatch.setattr(mod, "_run_gh", _fake_run_gh)
    monkeypatch.setattr(mod, "_poll_confirm_merged", lambda pr, deadline: (True, "abc7030"))
    monkeypatch.setattr(mod, "_chain_record_green", lambda deadline, sha=None: None)
    raw, out = _cp1252_stdout()
    monkeypatch.setattr(sys, "stdout", out)

    rc = mod._do_default("7030", time.monotonic() + 5, time.monotonic())
    out.flush()

    assert rc == 0
    assert [s["kind"] for s in _spans(trace_log)] == ["pr_merged", "verdict"]
    assert "✓ Squashed and merged" in raw.getvalue().decode("utf-8")
