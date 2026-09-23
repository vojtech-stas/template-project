"""
tests/test_pr_merge_lane_model_1506.py

PRD #1501 criterion 24 / ADR-0090 D4 merge leg 1, round-2 fix of PR #1528
(reviewer finding F1): `tools/pipe/pr-merge`'s lane `MODEL:` leg must read
the WHOLE line value and accept only a Claude Opus identifier. The round-1
code captured the first token and rejected only `sonnet`/`haiku`, so
`Claude Sonnet 4.6`, `claude haiku`, `n/a` and `<id>` all passed, and an
empty `MODEL:` line read the NEXT line's first token (`\\s*` spans the
newline).

Every value the reviewer listed is covered, plus the template placeholder,
empty and contradictory lines, and the accepted forms. The leg is exercised
in-process on `_assert_lane_model_or_refuse` (no gh, no git, no merge), plus
one end-to-end refusal through the real CLI against a fake `gh`: the refusal
fires before any merge call, so no merge, no span and no record-green chain
can run.

The non-lane test proves a reviewer's `MODEL: n/a` on an ordinary PR never
reaches the lane leg. It drives `_do_default` in-process with every
GitHub-, git- and record-green-touching seam stubbed, and a subprocess guard
that fails the test if anything tries to spawn a process.
"""
import importlib.machinery
import importlib.util
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
PR_MERGE = REPO_ROOT / "tools" / "pipe" / "pr-merge"


def _load_pr_merge(name="pr_merge_lane_model_test"):
    loader = importlib.machinery.SourceFileLoader(name, str(PR_MERGE))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def _approve_body(model_line):
    lines = ["VERDICT: APPROVE", "REASON: ok", "ROUND: 1", "CRITIC: reviewer"]
    if model_line is not None:
        lines.append(model_line)
    lines += ["MERGE_STATUS: n/a", "ESCALATE: n/a"]
    return "\n".join(lines)


# Each value the round-1 reviewer listed, plus the placeholder the reviewer
# template itself carries, empty/whitespace-only lines, non-Opus names,
# trailing words, and a missing line. All must refuse.
_REFUSED = [
    ("claude_sonnet_4_6", "MODEL: Claude Sonnet 4.6"),
    ("claude_haiku", "MODEL: claude haiku"),
    ("n_a", "MODEL: n/a"),
    ("id_placeholder", "MODEL: <id>"),
    ("template_placeholder", "MODEL: <id|n/a>"),
    ("sonnet_model_id", "MODEL: claude-sonnet-4-6"),
    ("sonnet_short", "MODEL: sonnet-5"),
    ("haiku_display_name", "MODEL: Claude Haiku 4.5"),
    ("empty_value", "MODEL:"),
    ("whitespace_value", "MODEL:    "),
    ("none_word", "MODEL: none"),
    ("unknown_model", "MODEL: gpt-5"),
    ("opus_with_trailing_words", "MODEL: claude-opus-5-5 (via sonnet)"),
    ("absent_line", None),
]

_ACCEPTED = [
    ("exact_model_id", "MODEL: claude-opus-5-5"),
    ("display_name", "MODEL: Claude Opus 5.5"),
    ("dispatch_alias", "MODEL: opus"),
    ("short_versioned", "MODEL: opus-5.5"),
    ("dated_model_id", "MODEL: claude-opus-4-1-20250805"),
    ("crlf_line_ending", "MODEL: claude-opus-5-5\r"),
]


# Parametrized only when pytest is present; the stdlib unittest fallback
# collects no module-level function in this file anyway.
if pytest is not None:
    @pytest.mark.parametrize("model_line", [v for _, v in _REFUSED], ids=[i for i, _ in _REFUSED])
    def test_pr_merge_lane_model_refuses(model_line, capsys):
        mod = _load_pr_merge()
        ok = mod._assert_lane_model_or_refuse("701", {"body": _approve_body(model_line)})
        assert ok is False, f"MODEL line {model_line!r} must refuse a lane merge"
        assert "MODEL" in capsys.readouterr().err

    @pytest.mark.parametrize("model_line", [v for _, v in _ACCEPTED], ids=[i for i, _ in _ACCEPTED])
    def test_pr_merge_lane_model_accepts(model_line, capsys):
        mod = _load_pr_merge()
        ok = mod._assert_lane_model_or_refuse("702", {"body": _approve_body(model_line)})
        assert ok is True, f"MODEL line {model_line!r} must be accepted; stderr={capsys.readouterr().err!r}"


def test_pr_merge_lane_model_refuses_contradictory_lines():
    """Two MODEL: lines in one APPROVE, one strong and one weak: the comment
    contradicts itself, so the leg fails closed."""
    mod = _load_pr_merge()
    body = _approve_body("MODEL: claude-opus-5-5") + "\nMODEL: claude-sonnet-4-6"
    assert mod._assert_lane_model_or_refuse("703", {"body": body}) is False


# ---------------------------------------------------------------------------
# End to end through the real CLI: refusal before any merge call.
# ---------------------------------------------------------------------------

_FAKE_GH_BODY = r'''
import sys, os, json
p = os.environ.get("FAKE_GH_MARKER_FILE")
if p:
    with open(p, "a", encoding="utf-8") as f:
        f.write(" ".join(sys.argv[1:]) + "\n")
args = sys.argv[1:]
if args[:2] == ["pr", "view"]:
    print(os.environ["FAKE_GH_VIEW_JSON"])
    sys.exit(0)
# Any other gh call is unexpected on a refusal path: fail loudly.
sys.exit(97)
'''


def _write_fake_gh(dirpath):
    if platform.system() == "Windows":
        impl = os.path.join(dirpath, "_fake_gh_impl.py")
        with open(impl, "w", encoding="utf-8") as f:
            f.write(_FAKE_GH_BODY)
        with open(os.path.join(dirpath, "gh.bat"), "w", encoding="utf-8", newline="\r\n") as f:
            f.write(f'@echo off\r\n"{sys.executable}" "{impl}" %*\r\n')
    else:
        sh = os.path.join(dirpath, "gh")
        with open(sh, "w", encoding="utf-8") as f:
            f.write("#!/usr/bin/env python3\n" + _FAKE_GH_BODY)
        os.chmod(sh, 0o755)


def test_pr_merge_lane_model_refuses_claude_sonnet_end_to_end(tmp_path):
    fake_dir = tmp_path / "fakebin"
    fake_dir.mkdir()
    _write_fake_gh(str(fake_dir))
    marker = tmp_path / "gh_calls.marker"
    trace_log = tmp_path / "trace-v3.jsonl"
    view = {
        "comments": [{"body": _approve_body("MODEL: Claude Sonnet 4.6")}],
        "labels": [{"name": "lane"}],
        "body": "Closes #7011",
        "headRefName": "fix/7011-lane-x",
    }
    env = os.environ.copy()
    env["PATH"] = str(fake_dir) + os.pathsep + env.get("PATH", "")
    env["FAKE_GH_VIEW_JSON"] = json.dumps(view)
    env["FAKE_GH_MARKER_FILE"] = str(marker)
    env["TRACE_LOG_OVERRIDE"] = str(trace_log)
    env["PR_MERGE_BUDGET_S"] = "5"
    workdir = tmp_path / "work"
    workdir.mkdir()
    result = subprocess.run(
        [sys.executable, str(PR_MERGE), "7011"],
        capture_output=True, encoding="utf-8", errors="replace",
        cwd=str(workdir), env=env, timeout=60,
    )
    assert result.returncode != 0
    assert "MODEL" in result.stderr, result.stderr
    calls = marker.read_text(encoding="utf-8").splitlines() if marker.exists() else []
    assert calls == ["pr view 7011 --json comments,labels,body,headRefName"], calls
    assert not trace_log.exists() or trace_log.read_text(encoding="utf-8").strip() == ""


# ---------------------------------------------------------------------------
# Non-lane PRs are untouched: the reviewer template's `MODEL: n/a` on an
# ordinary PR never reaches the lane leg (C1/C2 posture: no leak).
# ---------------------------------------------------------------------------

def test_pr_merge_lane_model_never_read_on_non_lane_pr(tmp_path, monkeypatch):
    mod = _load_pr_merge("pr_merge_non_lane_model_test")
    trace_log = tmp_path / "trace-v3.jsonl"
    monkeypatch.setenv("TRACE_LOG_OVERRIDE", str(trace_log))

    def _no_subprocess(*a, **k):
        raise AssertionError(f"unexpected subprocess in a stubbed merge: {a!r}")

    monkeypatch.setattr(mod.subprocess, "run", _no_subprocess)
    monkeypatch.setattr(mod, "_gh", lambda: "gh-disabled-in-test")

    view = {
        "comments": [{"body": _approve_body("MODEL: n/a")}],
        "labels": [{"name": "slice"}],
        "body": "Closes #7020",
        "headRefName": "feat/7020-ordinary",
    }

    class _Res:
        def __init__(self, rc=0, out="", err=""):
            self.returncode, self.stdout, self.stderr = rc, out, err

    def _fake_run_gh(args):
        if args[:2] == ["pr", "view"]:
            return _Res(out=json.dumps(view))
        if args[:2] == ["pr", "merge"]:
            return _Res()
        raise AssertionError(f"unexpected gh call {args!r}")

    def _lane_leg_called(*a, **k):
        raise AssertionError("the lane MODEL leg must never run on a non-lane PR")

    monkeypatch.setattr(mod, "_run_gh", _fake_run_gh)
    monkeypatch.setattr(mod, "_poll_confirm_merged", lambda pr, deadline: (True, "abc7020"))
    monkeypatch.setattr(mod, "_chain_record_green", lambda deadline, sha=None: None)
    monkeypatch.setattr(mod, "_assert_lane_model_or_refuse", _lane_leg_called)

    rc = mod._do_default("7020", time.monotonic() + 5, time.monotonic())

    assert rc == 0
    kinds = [json.loads(l)["kind"] for l in trace_log.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert kinds == ["pr_merged", "verdict"]
