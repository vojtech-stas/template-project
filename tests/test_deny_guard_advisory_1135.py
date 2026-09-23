"""
tests/test_deny_guard_advisory_1135.py

New-feature tests for slice #1135 (PRD #1127 criteria 7d-7e / ADR-0076 D4):
the FINAL half of the deny-guard funnel in .claude/hooks/pre-tool-bash.sh.

Covers:
  7d — `gh issue create --label slice` or `--label prd` (as the actual
       invocation, clause-anchored via the SAME python3 tokenizer #1141/#1133
       landed) produces an advisory `systemMessage` warn naming /to-issues and
       /to-prd -- NEVER a `permissionDecision: deny`. The call proceeds (no
       sanctioned posting verb exists yet, PRD #1127 non-goal). A commit
       message or echo merely MENTIONING the phrase must not warn (same
       invocation-vs-mention discipline as the reviewer round-1 BLOCK fixtures
       in test_deny_guard_mechanical_1133.py).
  7e — pass-through verification: every sanctioned verb invocation form
       (`python tools/pipe/{dispatch,pr-open,pr-merge,qa-verify,record-green,
       prd-close} ...` and `bash tools/promote.sh` in orchestrator context)
       produces NEITHER a warn NOR a deny (the retired `batch-plan` verb's
       own case was deleted with its subject per ADR-0080 D2, slice #1219).
       No new hook code was
       needed for this criterion (see pre-tool-bash.sh's top-of-file note): a
       sanctioned verb's Bash-tool command line has clause-head `python` (or
       `bash` for promote.sh, already gated on subagent-context only), which
       none of the existing deny/warn checks match.

Disclosure (rule #21 fixture discipline): pre-tool-bash.sh's attempt beacon
write ($LOG_DIR/hook-fires.jsonl, resolved via lib-root.sh's git-common-dir
walk) has no WORKFLOW_LOG_DIR override -- every hook invocation below writes
one real attempt-beacon line to the shared production log, identical to the
pre-existing precedent in test_deny_guard_mechanical_1133.py. This is NOT
verification evidence (no claim is derived from beacon content); it is a
disclosed side effect of exercising the hook's decision logic only. No
`gh issue create` or `tools/pipe/*` command below is actually EXECUTED --
these are synthetic command-string payloads fed to the hook's stdin only.

Runner: stdlib unittest + pytest compatible.
    python -m pytest tests/test_deny_guard_advisory_1135.py -v
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
HOOK = REPO_ROOT / ".claude" / "hooks" / "pre-tool-bash.sh"


def _resolve_jq_dir():
    """Resolve jq's directory via a login-shell bash (mirrors
    test_deny_guard_mechanical_1133.py's helper exactly)."""
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


def _run_hook(command: str, extra_env: dict = None):
    """Invoke the hook with the JSON payload delivered via a real temp FILE
    on stdin (mirrors test_deny_guard_mechanical_1133.py's helper exactly).
    `extra_env` overrides/adds env vars on top of a copy of the current
    environment. The payload carries no `agent_id`, so every fire is
    main-thread (orchestrator) context (ADR-0091 D1)."""
    payload = json.dumps({"tool_input": {"command": command}})
    env = os.environ.copy()
    if _JQ_DIR:
        env["PATH"] = _JQ_DIR + os.pathsep + env.get("PATH", "")
    if extra_env:
        env.update(extra_env)
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
    return _JQ_DIR is not None


def _out(result):
    return json.loads(result.stdout) if result.stdout.strip() else {}


@unittest.skipUnless(_jq_available_in_bash(), "jq not visible to the hook's bash context — soft-degrades without it")
class Test7dIssueCreateLabelAdvisoryWarn(unittest.TestCase):
    """ADR-0076 D4 7d: `gh issue create --label slice|prd` warns, never denies."""

    def test_label_slice_warns_and_names_skills(self):
        result = _run_hook('gh issue create --title "x" --body "y" --label slice')
        self.assertEqual(result.returncode, 0)
        out = _out(result)
        self.assertNotIn("hookSpecificOutput", out, msg=f"7d must never deny (call proceeds): {out}")
        self.assertIn("systemMessage", out, msg=f"expected advisory warn: {out}")
        self.assertIn("/to-issues", out["systemMessage"])
        self.assertIn("/to-prd", out["systemMessage"])

    def test_label_prd_warns(self):
        result = _run_hook('gh issue create --title "x" --label prd')
        self.assertEqual(result.returncode, 0)
        out = _out(result)
        self.assertNotIn("hookSpecificOutput", out, msg=f"7d must never deny: {out}")
        self.assertIn("systemMessage", out, msg=f"expected advisory warn: {out}")

    def test_label_comma_separated_containing_slice_warns(self):
        """gh CLI accepts comma-separated --label values; a list containing
        `slice` or `prd` among other labels must still warn."""
        result = _run_hook('gh issue create --title "x" --label bug,slice')
        out = _out(result)
        self.assertIn("systemMessage", out, msg=f"comma-separated label list containing slice must warn: {out}")
        self.assertNotIn("hookSpecificOutput", out)

    def test_label_comma_separated_containing_prd_warns(self):
        result = _run_hook('gh issue create --title "x" --label enhancement,prd')
        out = _out(result)
        self.assertIn("systemMessage", out, msg=f"comma-separated label list containing prd must warn: {out}")

    def test_unrelated_label_not_warned(self):
        """A `gh issue create --label bug` (no slice/prd) must not warn."""
        result = _run_hook('gh issue create --title "x" --label bug')
        out = _out(result)
        self.assertNotIn("systemMessage", out, msg=f"unrelated label must not warn: {out}")
        self.assertNotIn("hookSpecificOutput", out)

    def test_issue_create_without_label_not_warned(self):
        result = _run_hook('gh issue create --title "x" --body "y"')
        out = _out(result)
        self.assertNotIn("systemMessage", out, msg=f"no --label at all must not warn: {out}")

    def test_commit_message_mentioning_gh_issue_create_label_slice_not_warned(self):
        """Invocation-vs-mention discipline (same class as the reviewer
        round-1 F1/F2 fixtures): a commit message merely CONTAINING the
        phrase must never warn."""
        result = _run_hook(
            'git commit -m "add advisory warn for gh issue create --label slice"'
        )
        out = _out(result)
        self.assertNotIn("systemMessage", out, msg=f"commit-message mention must not warn: {out}")
        self.assertNotIn("hookSpecificOutput", out)

    def test_echo_mentioning_gh_issue_create_label_prd_not_warned(self):
        result = _run_hook('echo "reminder: gh issue create --label prd needs review"')
        out = _out(result)
        self.assertNotIn("systemMessage", out, msg=f"echo mention must not warn: {out}")
        self.assertNotIn("hookSpecificOutput", out)

    def test_real_invocation_in_compound_command_still_warns(self):
        """Regression: an ACTUAL invocation clause must still warn even when
        it appears alongside another clause in a compound command (mirrors
        F2's clause-independence discipline)."""
        result = _run_hook(
            'echo "posting a slice" && gh issue create --title "x" --label slice'
        )
        out = _out(result)
        self.assertIn("systemMessage", out, msg=f"real invocation in compound command must still warn: {out}")


@unittest.skipUnless(_jq_available_in_bash(), "jq not visible to the hook's bash context — soft-degrades without it")
class Test7eSanctionedVerbsPassThroughUntouched(unittest.TestCase):
    """ADR-0076 D4 7e: every sanctioned verb invocation form is untouched by
    any deny/warn rule -- demoed per verb via synthetic command-string
    payloads (hook logic tested directly, no real execution)."""

    def _assert_clean_pass(self, command, extra_env=None):
        result = _run_hook(command, extra_env=extra_env)
        self.assertEqual(result.returncode, 0)
        out = _out(result)
        self.assertNotIn("hookSpecificOutput", out, msg=f"{command!r} must not be denied: {out}")
        self.assertNotIn("systemMessage", out, msg=f"{command!r} must not be warned: {out}")

    def test_dispatch_verb_passes(self):
        self._assert_clean_pass("python tools/pipe/dispatch 1135")

    def test_dispatch_end_verb_passes(self):
        self._assert_clean_pass("python tools/pipe/dispatch --end 1135 --result SUCCESS")

    def test_pr_open_verb_passes(self):
        self._assert_clean_pass(
            'python tools/pipe/pr-open --title "feat(hooks): x" --body-file /tmp/body.md'
        )

    def test_pr_open_verb_with_msys_env_prefix_passes(self):
        """The exact invocation form this slice's own PR uses."""
        self._assert_clean_pass(
            'MSYS_NO_PATHCONV=1 python tools/pipe/pr-open --title "x" --body-file /tmp/body.md'
        )

    def test_pr_merge_verb_passes(self):
        self._assert_clean_pass("python tools/pipe/pr-merge 1130")

    def test_pr_merge_confirm_verb_passes(self):
        self._assert_clean_pass("python tools/pipe/pr-merge --confirm 1130")

    def test_qa_verify_verb_passes(self):
        self._assert_clean_pass("python tools/pipe/qa-verify 1127")

    def test_record_green_verb_passes(self):
        self._assert_clean_pass("python tools/pipe/record-green")

    def test_prd_close_verb_passes(self):
        self._assert_clean_pass("python tools/pipe/prd-close 1127")

    def test_promote_sh_orchestrator_context_passes(self):
        """No `agent_id` in the payload -- the sanctioned orchestrator procedure
        (already covered by test_deny_guard_mechanical_1133.py; repeated here
        for a complete per-verb 7e demo in one dedicated location)."""
        self._assert_clean_pass("bash tools/promote.sh")


if __name__ == "__main__":
    unittest.main()
