"""
tests/test_verb_resolver_order_1512.py

Regression tests for PR #1531 reviewer round 1, finding F2 and its class
(slice #1512, ADR-0089 D1; the verb contract of ADR-0076 D1): a guarded verb
must never consult the branch-role resolver AFTER its irreversible side
effect. A malformed .claude/pipeline.conf read at that point raises
PipelineConfigError and turns a completed side effect into a non-zero exit.

  pr-merge      `_chain_record_green` resolved the integration branch for
                its failure message after the merge and both spans. On a
                malformed conf record-green refuses, so that branch is always
                reached, and pr-merge exited 1 on a merge that succeeded.
  record-green  `_resolve_develop_sha` resolved the integration branch after
                tools/record-green.sh had already written its v2 event.

Safety: both verbs are loaded IN-PROCESS with every external stubbed. There
is no real `gh` call, no merge, no tools/record-green.sh run, and no trace or
event write: pr-merge's chained record-green is a temp python stub that
exits 1, pipe/record-green's record-green.sh is a temp bash stub, `gh` is an
in-module fake that rejects any call it does not expect, and `_load_trace`
returns an in-memory fake.

Runner: stdlib unittest + pytest compatible.
  python -m pytest tests/test_verb_resolver_order_1512.py -v
"""

import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

try:
    import pytest  # noqa: F401
except ImportError:
    pytest = None

REPO_ROOT = Path(__file__).parent.parent
PR_MERGE = REPO_ROOT / "tools" / "pipe" / "pr-merge"
PIPE_RECORD_GREEN = REPO_ROOT / "tools" / "pipe" / "record-green"


def _load_verb(path: Path, name: str):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class _FakeTrace:
    def __init__(self):
        self.spans = []

    def read_spans(self):
        return []

    def emit_span(self, **kwargs):
        self.spans.append(kwargs)


class _MalformedConfCwd(unittest.TestCase):
    """cwd is a temp git repo whose .claude/pipeline.conf is malformed (both
    roles equal); the verbs resolve roles for the cwd repo."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="verb_resolver_order_1512_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = os.path.join(self.tmp, "repo")
        subprocess.run(["git", "init", "-q", self.repo], check=True, capture_output=True)
        os.makedirs(os.path.join(self.repo, ".claude"))
        with open(os.path.join(self.repo, ".claude", "pipeline.conf"), "w") as f:
            f.write("integration_branch=x\nrelease_branch=x\n")
        old_cwd = os.getcwd()
        os.chdir(self.repo)
        # Cleanups run LIFO: leave the sandbox before it is removed.
        self.addCleanup(os.chdir, old_cwd)


class TestPrMergeExitReflectsMergeOnly(_MalformedConfCwd):
    """F2: once the merge is confirmed and recorded, pr-merge exits 0 and
    names record-green's refusal; the malformed conf never changes that."""

    def test_completed_merge_exits_zero_on_malformed_conf(self):
        mod = _load_verb(PR_MERGE, "pr_merge_resolver_order_1512")

        refusing_record_green = os.path.join(self.tmp, "record_green_refuses.py")
        with open(refusing_record_green, "w") as f:
            f.write("import sys\nsys.exit(1)\n")
        mod._RECORD_GREEN = refusing_record_green

        trace = _FakeTrace()
        mod._load_trace = lambda: trace
        mod._remote_owner_repo = lambda: ("owner", "repo")

        def fake_gh(args):
            if args[:2] == ["pr", "view"]:
                body = "VERDICT: APPROVE\nCRITIC: reviewer\nROUND: 1"
                return subprocess.CompletedProcess(args, 0, json.dumps({"comments": [{"body": body}]}), "")
            if args[:2] == ["pr", "merge"]:
                return subprocess.CompletedProcess(args, 0, "", "")
            if args[:1] == ["api"]:
                merged = {"merged": True, "merge_commit_sha": "abc123"}
                return subprocess.CompletedProcess(args, 0, json.dumps(merged), "")
            raise AssertionError(f"unexpected gh call: {args!r}")

        mod._run_gh = fake_gh

        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = mod.main(["4242"])

        self.assertEqual(rc, 0, msg=f"stdout={out.getvalue()!r}\nstderr={err.getvalue()!r}")
        self.assertIn("pr-merge: PR #4242 merged sha=abc123", out.getvalue())
        self.assertEqual([s["kind"] for s in trace.spans], ["pr_merged", "verdict"])
        self.assertIn("evaluation FAILED (exit=1)", err.getvalue())
        self.assertIn("NOT green", err.getvalue())


class TestPipeRecordGreenResolvesBeforeSideEffect(_MalformedConfCwd):
    """F2 class sweep: pipe/record-green resolves the integration role before
    tools/record-green.sh runs. The stub below succeeds, standing in for a
    record-green.sh run that got past its own resolver (the conf changed in
    between), so the test pins the ordering itself: on a malformed conf the
    wrapper refuses with nothing run and nothing recorded."""

    def test_malformed_conf_refuses_before_record_green_sh_runs(self):
        mod = _load_verb(PIPE_RECORD_GREEN, "pipe_record_green_resolver_order_1512")

        marker = os.path.join(self.tmp, "record_green_sh_ran")
        stub = os.path.join(self.tmp, "record-green-stub.sh")
        with open(stub, "w", newline="\n") as f:
            f.write(f'#!/bin/bash\necho ran >> "{Path(marker).as_posix()}"\nexit 0\n')
        mod._RECORD_GREEN_SH = stub

        trace = _FakeTrace()
        mod._load_trace = lambda: trace

        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = mod.main([])

        self.assertEqual(rc, 1, msg=f"stdout={out.getvalue()!r}\nstderr={err.getvalue()!r}")
        self.assertFalse(
            os.path.exists(marker),
            msg="record-green.sh must not run once the integration role cannot be resolved",
        )
        self.assertEqual(trace.spans, [])
        self.assertIn("malformed pipeline.conf", err.getvalue())


if __name__ == "__main__":
    unittest.main()
