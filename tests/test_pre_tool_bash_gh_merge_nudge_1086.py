"""
tests/test_pre_tool_bash_gh_merge_nudge_1086.py

Originally: a new-feature test for slice #1086 (PRD #1075 closing pass) —
the advisory deny-guard nudge in .claude/hooks/pre-tool-bash.sh, where a raw
`gh pr merge` Bash invocation was warned (systemMessage) but never denied.

UPGRADED per slice #1133 (PRD #1127 criterion 7a / ADR-0076 D4): the raw
`gh pr merge` form is now a hard `permissionDecision: deny` naming
tools/pipe/pr-merge, not an advisory warn. `test_raw_gh_pr_merge_...` below
is updated to assert the new deny behavior; the wrapper-not-nudged and
push-still-denied regression tests are otherwise unchanged. See also
tests/test_deny_guard_mechanical_1133.py for the full 7a-7c coverage.

Runner: stdlib unittest + pytest compatible.
    python -m pytest tests/test_pre_tool_bash_gh_merge_nudge_1086.py -v
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
HOOK = REPO_ROOT / ".claude" / "pipeline" / "hooks" / "pre-tool-bash.sh"


def _resolve_jq_dir():
    """Resolve jq's directory via a login-shell bash (sources rc files, so it
    sees PATH entries a bare non-login python-spawned bash would miss) and
    convert to a Windows-native path for env["PATH"] prepending. Returns None
    if jq cannot be located at all."""
    try:
        found = subprocess.run(
            ["bash", "-lc", "command -v jq"], capture_output=True, text=True, timeout=10,
        )
        if found.returncode != 0 or not found.stdout.strip():
            return None
        posix_path = found.stdout.strip()
        win = subprocess.run(
            ["bash", "-lc", f"cygpath -w '{posix_path}'"], capture_output=True, text=True, timeout=10,
        )
        if win.returncode != 0 or not win.stdout.strip():
            return None
        return str(Path(win.stdout.strip()).parent)
    except Exception:
        return None


_JQ_DIR = _resolve_jq_dir()


def _run_hook(command: str):
    """Invoke the hook with the JSON payload delivered via a real temp FILE
    (not an anonymous pipe) on stdin — the hook's `jq ... </dev/stdin` read
    fails ("No such file or directory") when fed an anonymous pipe created by
    a non-MSYS parent process (python.exe), a Windows/MSYS fd-inheritance
    quirk; a genuine file redirection works reliably across that boundary."""
    payload = json.dumps({"tool_input": {"command": command}})
    env = os.environ.copy()
    if _JQ_DIR:
        env["PATH"] = _JQ_DIR + os.pathsep + env.get("PATH", "")
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
        f.write(payload)
        tmp_path = f.name
    try:
        with open(tmp_path, "r", encoding="utf-8") as stdin_f:
            return subprocess.run(
                ["bash", str(HOOK)], stdin=stdin_f, capture_output=True, text=True,
                timeout=15, env=env,
            )
    finally:
        os.unlink(tmp_path)


def _jq_available_in_bash() -> bool:
    """Whether jq is resolvable at all — the hook soft-degrades to a silent
    exit 0 with no output when it can't find jq; skip rather than conflate
    that with a real bug in this environment."""
    return _JQ_DIR is not None


@unittest.skipUnless(_jq_available_in_bash(), "jq not visible to the hook's bash context — soft-degrades without it")
class TestGhPrMergeAdvisoryNudge(unittest.TestCase):
    def test_raw_gh_pr_merge_denied(self):
        """Upgraded per slice #1133 / ADR-0076 D4 7a: raw `gh pr merge` is now
        a hard deny (was an advisory warn under PRD #1075 slice #1086)."""
        result = _run_hook("gh pr merge 123 --squash --auto")
        self.assertEqual(result.returncode, 0)
        out = json.loads(result.stdout)
        self.assertEqual(
            out.get("hookSpecificOutput", {}).get("permissionDecision"), "deny",
            msg=f"expected deny (upgraded from advisory warn), got: {out}",
        )
        self.assertIn("tools/pipe/pr-merge", out["hookSpecificOutput"]["permissionDecisionReason"])
        self.assertNotIn("systemMessage", out, msg="deny replaces the old warn-only systemMessage")

    def test_wrapper_invocation_not_nudged(self):
        result = _run_hook("python tools/pipe/pr-merge 123")
        self.assertEqual(result.returncode, 0)
        out = json.loads(result.stdout) if result.stdout.strip() else {}
        self.assertNotIn("systemMessage", out, msg=f"the sanctioned wrapper call must not be nudged: {out}")
        self.assertNotIn("hookSpecificOutput", out, msg=f"the sanctioned wrapper call must not be denied: {out}")

    def test_push_main_still_denied_not_downgraded(self):
        """Regression guard: adding the advisory nudge must not weaken the
        existing hard deny on `git push origin main` (ADR-0023 D4)."""
        result = _run_hook("git push origin main")
        self.assertEqual(result.returncode, 0)
        out = json.loads(result.stdout)
        self.assertEqual(
            out.get("hookSpecificOutput", {}).get("permissionDecision"), "deny"
        )


if __name__ == "__main__":
    unittest.main()
