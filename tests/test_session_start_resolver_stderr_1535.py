"""
tests/test_session_start_resolver_stderr_1535.py

Regression test for #1535: `.claude/hooks/session-start.sh` captured the
branch-role resolver with `2>&1`, so anything the resolver wrote to stderr
on SUCCESS was merged into INTEGRATION_BRANCH. The merged text then leaked
into the injected context ("... behind origin/<warning>\\n<branch>") and
broke the `git fetch origin <branch>` divergence count.

Harness: a sandbox copy of the REAL hook (session-start.sh + lib-root.sh,
copied with LF endings, otherwise unmodified) at <sandbox>/.claude/hooks/,
plus a STUB <sandbox>/tools/pipeline_config.py, so the hook's own
`$SCRIPT_DIR/../../tools/pipeline_config.py` lookup finds the stub (the same
two-level shape test_session_start_hookspath_1282.py mirrors). The sandbox
is its own git repo whose bare origin holds `trunk`, so lib-root.sh puts
LOG_DIR under the sandbox, and WORKFLOW_LOG_DIR points the python emitters
there too: no test writes this checkout's `.claude/logs/*` (rule #21). A
fake `gh` on PATH fails `gh auth status`, which keeps the GH_OK block off,
so no run touches the network.

Cases:
  1. The resolver exits 0, prints `trunk` on stdout and a warning on stderr.
     The context's first line reads exactly
     `Branch: trunk | 0 commit(s) behind origin/trunk` and the warning text
     appears nowhere in the context. FAILS before the fix: the warning is
     merged into the branch name, and the fetch of that multi-line name
     fails.
  2. The resolver exits 1 with its refusal text on stderr. The S2-d
     placeholder (slice #1512) still carries that exact text, which guards
     the fix against dropping stderr from the failure path.

No platform skip: the cases need only bash, git and python3, so they run on
the Linux CI runner as well as on Windows Git Bash.

Runner: stdlib unittest + pytest compatible.
  python -m pytest tests/test_session_start_resolver_stderr_1535.py -v
"""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
HOOKS_DIR = REPO_ROOT / ".claude" / "hooks"

WARNING_TEXT = "resolver-warning-1535 stderr noise on a successful resolve"
REFUSAL_TEXT = "resolver-refusal-1535 malformed pipeline.conf from the stub"

# The warning is written and flushed BEFORE the branch name is printed, so
# a merged `2>&1` capture holds "<warning>\ntrunk", never a clean "trunk".
_STUB_SUCCESS = (
    "import sys\n"
    f"sys.stderr.write({WARNING_TEXT!r} + '\\n')\n"
    "sys.stderr.flush()\n"
    "print('trunk')\n"
)
_STUB_FAILURE = (
    "import sys\n"
    f"sys.stderr.write({REFUSAL_TEXT!r} + '\\n')\n"
    "sys.exit(1)\n"
)


def _git(*args):
    return subprocess.run(
        ["git"] + list(args), check=True, capture_output=True, text=True,
    )


class TestSessionStartResolverStderr1535(unittest.TestCase):
    """#1535: only the resolver's stdout names the integration branch; its
    stderr still reaches the S2-d failure placeholder."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="session_start_resolver_1535_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

        bare = os.path.join(self.tmp, "origin.git")
        _git("init", "-q", "--bare", bare)
        self.work = os.path.join(self.tmp, "work")
        _git("init", "-q", "-b", "trunk", self.work)
        _git("-C", self.work, "config", "user.email", "test@example.com")
        _git("-C", self.work, "config", "user.name", "Test")
        # --no-verify: a fixture commit in a throwaway repo, kept clear of
        # any machine-wide core.hooksPath.
        _git("-C", self.work, "commit", "-q", "--no-verify", "--allow-empty", "-m", "init")
        _git("-C", self.work, "remote", "add", "origin", bare)
        _git("-C", self.work, "push", "-q", "--no-verify", "origin", "trunk")

        hooks = Path(self.work) / ".claude" / "hooks"
        hooks.mkdir(parents=True)
        for name in ("session-start.sh", "lib-root.sh"):
            text = (HOOKS_DIR / name).read_text(encoding="utf-8")
            (hooks / name).write_text(text, encoding="utf-8", newline="\n")
        self.hook = hooks / "session-start.sh"
        tools = Path(self.work) / "tools"
        tools.mkdir()
        self.stub = tools / "pipeline_config.py"

        self.log_dir = os.path.join(self.work, ".claude", "logs")
        self.bin = os.path.join(self.tmp, "fake-bin")
        os.makedirs(self.bin)
        gh = os.path.join(self.bin, "gh")
        with open(gh, "w", encoding="utf-8", newline="\n") as f:
            f.write("#!/bin/sh\nexit 1\n")
        os.chmod(gh, 0o755)

    def _run_session_start(self, stub_body: str) -> str:
        self.stub.write_text(stub_body, encoding="utf-8", newline="\n")
        env = os.environ.copy()
        env["PATH"] = self.bin + os.pathsep + env.get("PATH", "")
        env["CLAUDE_PROJECT_DIR"] = self.work.replace(os.sep, "/")
        env["WORKFLOW_LOG_DIR"] = self.log_dir
        result = subprocess.run(
            ["bash", self.hook.as_posix()],
            input=json.dumps({"session_id": "fixture-1535"}),
            cwd=self.work, capture_output=True, text=True, timeout=60, env=env,
        )
        self.assertEqual(
            result.returncode, 0,
            msg=f"session-start.sh must exit 0.\nstdout={result.stdout!r}\nstderr={result.stderr!r}",
        )
        self.assertTrue(result.stdout.strip(), msg=f"no context JSON; stderr={result.stderr!r}")
        return json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]

    def test_success_with_stderr_warning_keeps_branch_clean(self):
        ctx = self._run_session_start(_STUB_SUCCESS)
        self.assertEqual(
            ctx.splitlines()[0], "Branch: trunk | 0 commit(s) behind origin/trunk", msg=ctx,
        )
        self.assertNotIn("resolver-warning-1535", ctx, msg=ctx)
        self.assertNotIn("(resolver failed", ctx, msg=ctx)

    def test_resolver_failure_still_shows_refusal_text(self):
        ctx = self._run_session_start(_STUB_FAILURE)
        self.assertEqual(
            ctx.splitlines()[0],
            f"Branch: trunk | (resolver failed: {REFUSAL_TEXT}) commit(s) behind origin/?",
            msg=ctx,
        )


if __name__ == "__main__":
    unittest.main()
