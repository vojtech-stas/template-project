"""
tests/test_deny_guard_mechanical_1133.py

New-feature tests for slice #1133 (PRD #1127 criteria 7a-7c / ADR-0076 D4):
the mechanical deny-guard funnel in .claude/hooks/pre-tool-bash.sh.

Covers:
  7a — raw `gh pr merge` upgraded from advisory warn (PRD #1075 slice #1086)
       to permissionDecision: deny, naming tools/pipe/pr-merge. The sanctioned
       wrapper invocation (`python tools/pipe/pr-merge <pr>`) is never denied
       -- its own internal `gh pr merge` subprocess call is invisible to this
       hook; only the literal Bash-tool command line is inspected.
  7b — `tools/promote.sh` invoked from a SUBAGENT context (the hook payload
       carries `agent_id`, ADR-0091 D1) is denied; the identical
       orchestrator-context invocation (no `agent_id`) passes through
       un-denied -- the same discriminator pre-tool-edit.sh uses.
  7c — `git push` targeting main via ALL refspec forms (`origin main`,
       `HEAD:main`, `refs/heads/main`, `:main`) is denied; pushes to OTHER
       branches (develop, feature branches) are NOT denied.

Disclosure (rule #21 fixture discipline): pre-tool-bash.sh's attempt beacon
write (`$LOG_DIR/hook-fires.jsonl`, resolved via lib-root.sh's git-common-dir
walk) has no WORKFLOW_LOG_DIR override -- every hook invocation below writes
one real attempt-beacon line to the shared production log, identical to the
pre-existing precedent in test_pre_tool_bash_gh_merge_nudge_1086.py. This is
NOT verification evidence (no claim is derived from beacon content); it is a
disclosed side effect of exercising the hook's decision logic only.

Runner: stdlib unittest + pytest compatible.
    python -m pytest tests/test_deny_guard_mechanical_1133.py -v
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
    """Resolve jq's directory via a login-shell bash (sources rc files, so it
    sees PATH entries a bare non-login python-spawned bash would miss) and
    convert to a Windows-native path for env["PATH"] prepending. Returns None
    if jq cannot be located at all. (Mirrors
    test_pre_tool_bash_gh_merge_nudge_1086.py's helper exactly.)"""
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


def _run_hook(command: str, agent_id: str = None):
    """Invoke the hook with the JSON payload delivered via a real temp FILE
    (not an anonymous pipe) on stdin -- the hook's `jq ... </dev/stdin` read
    fails ("No such file or directory") when fed an anonymous pipe created by
    a non-MSYS parent process (python.exe); a genuine file redirection works
    reliably across that boundary. `agent_id`, when given, is put into the
    payload exactly as Claude Code does for a hook fired inside a subagent
    call (ADR-0091 D1) -- the 7b discriminator is read from the payload,
    never from the environment."""
    body = {"tool_input": {"command": command}}
    if agent_id is not None:
        body["agent_id"] = agent_id
        body["agent_type"] = "implementer"
    payload = json.dumps(body)
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
    """Whether jq is resolvable at all -- the hook soft-degrades to a silent
    exit 0 with no output when it can't find jq; skip rather than conflate
    that with a real bug in this environment."""
    return _JQ_DIR is not None


def _decision(result):
    out = json.loads(result.stdout) if result.stdout.strip() else {}
    return out.get("hookSpecificOutput", {}).get("permissionDecision"), out


@unittest.skipUnless(_jq_available_in_bash(), "jq not visible to the hook's bash context — soft-degrades without it")
class Test7aRawGhPrMergeDenied(unittest.TestCase):
    """ADR-0076 D4 7a: raw `gh pr merge` upgraded from warn to deny."""

    def test_raw_gh_pr_merge_denied(self):
        result = _run_hook("gh pr merge 123 --squash --auto")
        self.assertEqual(result.returncode, 0)
        decision, out = _decision(result)
        self.assertEqual(decision, "deny", msg=f"expected deny, got: {out}")
        self.assertIn(
            "tools/pipe/pr-merge",
            out["hookSpecificOutput"]["permissionDecisionReason"],
        )

    def test_wrapper_invocation_not_denied(self):
        """The sanctioned wrapper's Bash-tool command line contains no literal
        `gh pr merge` substring -- its `gh pr merge` subprocess call happens
        INSIDE the Python process (subprocess.run(["gh","pr","merge",...]) in
        tools/pipe/pr-merge), never as a Bash-tool call this hook observes."""
        result = _run_hook("python tools/pipe/pr-merge 123")
        self.assertEqual(result.returncode, 0)
        out = json.loads(result.stdout) if result.stdout.strip() else {}
        self.assertNotIn("hookSpecificOutput", out, msg=f"sanctioned wrapper must not be denied: {out}")

    def test_wrapper_confirm_invocation_not_denied(self):
        result = _run_hook("python tools/pipe/pr-merge --confirm 123")
        self.assertEqual(result.returncode, 0)
        out = json.loads(result.stdout) if result.stdout.strip() else {}
        self.assertNotIn("hookSpecificOutput", out, msg=f"sanctioned wrapper --confirm form must not be denied: {out}")


@unittest.skipUnless(_jq_available_in_bash(), "jq not visible to the hook's bash context — soft-degrades without it")
class Test7bPromoteSubagentContextDenied(unittest.TestCase):
    """ADR-0076 D4 7b: tools/promote.sh denied ONLY from a subagent context."""

    def test_promote_denied_in_implementer_subagent_context(self):
        result = _run_hook("bash tools/promote.sh", agent_id="agent-implementer-1133")
        self.assertEqual(result.returncode, 0)
        decision, out = _decision(result)
        self.assertEqual(decision, "deny", msg=f"expected deny in subagent context, got: {out}")
        self.assertIn("subagent context", out["hookSpecificOutput"]["permissionDecisionReason"])

    def test_promote_denied_in_reviewer_subagent_context(self):
        """Any non-empty payload `agent_id` triggers the deny -- the
        discriminator is its presence, not its specific value (the same
        check pre-tool-edit.sh applies)."""
        result = _run_hook("bash tools/promote.sh", agent_id="agent-reviewer-1133")
        decision, out = _decision(result)
        self.assertEqual(decision, "deny", msg=f"expected deny, got: {out}")

    def test_promote_not_denied_in_orchestrator_context(self):
        """The orchestrator's own invocation (no `agent_id` in the payload)
        must pass through un-denied -- the same discriminator pre-tool-edit.sh
        uses for its subagent-context skip."""
        result = _run_hook("bash tools/promote.sh")
        self.assertEqual(result.returncode, 0)
        out = json.loads(result.stdout) if result.stdout.strip() else {}
        self.assertNotIn("hookSpecificOutput", out, msg=f"orchestrator invocation must not be denied: {out}")


@unittest.skipUnless(_jq_available_in_bash(), "jq not visible to the hook's bash context — soft-degrades without it")
class Test7cPushMainAllRefspecForms(unittest.TestCase):
    """ADR-0076 D4 7c: push-to-main deny closes the refspec evasion."""

    def _assert_denied(self, command):
        result = _run_hook(command)
        self.assertEqual(result.returncode, 0)
        decision, out = _decision(result)
        self.assertEqual(decision, "deny", msg=f"expected deny for {command!r}, got: {out}")

    def _assert_not_denied(self, command):
        result = _run_hook(command)
        self.assertEqual(result.returncode, 0)
        out = json.loads(result.stdout) if result.stdout.strip() else {}
        self.assertNotIn("hookSpecificOutput", out, msg=f"{command!r} must not be denied: {out}")

    def test_plain_origin_main_denied(self):
        self._assert_denied("git push origin main")

    def test_force_origin_main_still_denied(self):
        self._assert_denied("git push --force origin main")

    def test_head_colon_main_denied(self):
        self._assert_denied("git push origin HEAD:main")

    def test_refs_heads_main_bare_denied(self):
        self._assert_denied("git push origin refs/heads/main")

    def test_head_colon_refs_heads_main_denied(self):
        self._assert_denied("git push origin HEAD:refs/heads/main")

    def test_delete_remote_main_colon_form_denied(self):
        self._assert_denied("git push origin :main")

    def test_arbitrary_source_colon_main_denied(self):
        self._assert_denied("git push origin feat/1133-deny-guard-mechanical:main")

    # --- Negative tests: OTHER branches must NOT be denied ---

    def test_push_develop_not_denied(self):
        self._assert_not_denied("git push origin develop")

    def test_push_feature_branch_not_denied(self):
        self._assert_not_denied("git push origin feat/1133-deny-guard-mechanical")

    def test_head_colon_develop_not_denied(self):
        self._assert_not_denied("git push origin HEAD:develop")

    def test_refs_heads_develop_not_denied(self):
        self._assert_not_denied("git push origin refs/heads/develop")


@unittest.skipUnless(_jq_available_in_bash(), "jq not visible to the hook's bash context — soft-degrades without it")
class TestExistingBehaviorRegression(unittest.TestCase):
    """Existing warns/denies unrelated to 7a-7c must still fire unchanged."""

    def test_wip_commit_still_warns_not_denies(self):
        result = _run_hook('git commit -m "WIP: something"')
        self.assertEqual(result.returncode, 0)
        out = json.loads(result.stdout)
        self.assertIn("systemMessage", out)
        self.assertNotIn("hookSpecificOutput", out)

    def test_unrelated_command_passes_through_clean(self):
        result = _run_hook("ls -la")
        self.assertEqual(result.returncode, 0)
        out = json.loads(result.stdout) if result.stdout.strip() else {}
        self.assertNotIn("hookSpecificOutput", out)
        self.assertNotIn("systemMessage", out)


@unittest.skipUnless(_jq_available_in_bash(), "jq not visible to the hook's bash context — soft-degrades without it")
class TestReviewerRound1FalsePositiveFixtures(unittest.TestCase):
    """Reviewer round-1 BLOCK on PR #1141 (comment 5159763786): two
    independently-confirmed false-positive regressions in the 7a/7c hard-deny
    paths, reproduced here as fixtures BEFORE the clause-anchoring fix
    (ADR-0067 D2/D3 test-first ordering — these FAIL against the pre-fix
    substring-matching code and PASS after).

    F1 — 7a matched the PHRASE `gh pr merge` anywhere in the command string,
    not the actual invocation; a commit message containing that phrase (this
    PR's own commit-message shape) was wrongly denied.

    F2 — 7c's push-to-main check decoupled the `git push` clause from the
    main-refspec match across compound commands (`&&`), so an unrelated
    `refs/heads/main` mention in a LATER clause (e.g. inside an `echo`)
    wrongly denied a push to a non-main branch.
    """

    # --- F1 reproduction: substring-vs-invocation for 7a ---

    def test_f1_commit_message_mentioning_gh_pr_merge_not_denied(self):
        """Reviewer's exact repro: this PR's own commit-message shape."""
        result = _run_hook(
            'git commit -m "raw gh pr merge upgrades from advisory warn to a hard deny"'
        )
        self.assertEqual(result.returncode, 0)
        out = json.loads(result.stdout) if result.stdout.strip() else {}
        self.assertNotIn(
            "hookSpecificOutput", out,
            msg=f"a commit message merely CONTAINING the phrase 'gh pr merge' must never be denied: {out}",
        )

    def test_f1_echo_mentioning_gh_pr_merge_not_denied(self):
        result = _run_hook('echo "reminder: never run gh pr merge directly"')
        self.assertEqual(result.returncode, 0)
        out = json.loads(result.stdout) if result.stdout.strip() else {}
        self.assertNotIn("hookSpecificOutput", out, msg=f"echo mention must not be denied: {out}")

    def test_f1_grep_pattern_mentioning_gh_pr_merge_not_denied(self):
        result = _run_hook("grep -rn 'gh pr merge' tests/")
        self.assertEqual(result.returncode, 0)
        out = json.loads(result.stdout) if result.stdout.strip() else {}
        self.assertNotIn("hookSpecificOutput", out, msg=f"grep pattern mention must not be denied: {out}")

    def test_f1_real_gh_pr_merge_invocation_still_denied(self):
        """Regression: the actual invocation (not a mention) must still deny."""
        result = _run_hook("gh pr merge 123 --squash --auto")
        self.assertEqual(result.returncode, 0)
        decision, out = _decision(result)
        self.assertEqual(decision, "deny", msg=f"real invocation must still deny: {out}")

    # --- F2 reproduction: clause-decoupling for 7c ---

    def test_f2_push_develop_with_unrelated_main_mention_not_denied(self):
        """Reviewer's exact repro: push to develop, unrelated refs/heads/main
        mention in a LATER, separate compound-command clause."""
        result = _run_hook('git push origin develop && echo "closing the refs/heads/main evasion"')
        self.assertEqual(result.returncode, 0)
        out = json.loads(result.stdout) if result.stdout.strip() else {}
        self.assertNotIn(
            "hookSpecificOutput", out,
            msg=f"push to develop must not be denied by an unrelated main-mention in a later clause: {out}",
        )

    def test_f2_push_feature_branch_with_unrelated_main_mention_not_denied(self):
        result = _run_hook('git push origin feat/foo; echo "see refs/heads/main for context"')
        self.assertEqual(result.returncode, 0)
        out = json.loads(result.stdout) if result.stdout.strip() else {}
        self.assertNotIn("hookSpecificOutput", out, msg=f"must not be denied: {out}")

    def test_f2_real_push_to_main_still_denied_within_compound_command(self):
        """Regression: an ACTUAL push-to-main clause must still deny even
        when it appears alongside other clauses in a compound command."""
        result = _run_hook('echo "about to push" && git push origin main')
        self.assertEqual(result.returncode, 0)
        decision, out = _decision(result)
        self.assertEqual(decision, "deny", msg=f"real push-to-main clause must still deny: {out}")

    # --- Rule #19 class sweep: 7b promote.sh mention-vs-invocation ---

    def test_7b_echo_mentioning_promote_sh_not_denied_even_in_subagent_context(self):
        result = _run_hook(
            'echo "tools/promote.sh must never run from a subagent context"',
            agent_id="agent-implementer-1133",
        )
        self.assertEqual(result.returncode, 0)
        out = json.loads(result.stdout) if result.stdout.strip() else {}
        self.assertNotIn("hookSpecificOutput", out, msg=f"mention-only must not be denied: {out}")

    def test_7b_real_promote_sh_invocation_still_denied_in_subagent_context(self):
        result = _run_hook("bash tools/promote.sh", agent_id="agent-implementer-1133")
        self.assertEqual(result.returncode, 0)
        decision, out = _decision(result)
        self.assertEqual(decision, "deny", msg=f"real invocation must still deny in subagent context: {out}")


if __name__ == "__main__":
    unittest.main()
