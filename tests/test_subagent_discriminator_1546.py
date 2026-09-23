"""
tests/test_subagent_discriminator_1546.py

Regression tests for bug #1546 (ADR-0091 D1): every hook that treats a
subagent differently from the main thread must read that fact from the hook's
own stdin payload, never from the environment.

Claude Code puts `agent_id` into the payload of a hook that fires inside a
subagent call, and only then ("Present only when the hook fires inside a
subagent call. Use this to distinguish subagent hook calls from main-thread
calls." -- hooks reference, "Common input fields"). `agent_type` alone is NOT
a subagent marker: the main thread carries it too when a session runs under
`--agent`. It sets no environment variable naming the agent, so a hook that
keys on one never sees a subagent at all -- the three sites below were dead
code until this fix:

  1. pre-tool-bash.sh (via pre-tool-bash-classify.py) -- ADR-0076 D4's
     subagent-context `tools/promote.sh` deny (PIP-017).
  2. pre-tool-edit.sh -- ADR-0023 D3's subagent-context skip of the rule-#10
     gate.
  3. stop-reviewer-gate.sh -- ADR-0029 D3's subagent-context skip.

Each case fires the REAL hook with a real payload twice, once with and once
without `agent_id` (the rest of the payload identical), and asserts the
decision plus the HOK-008 beacon contract (one attempt, exactly one terminal,
status in the closed set). No case sets any environment variable to fake a
subagent. `tools/promote.sh` is never executed: the classifier only reads the
command TEXT from the payload.

Fixture discipline (rule #21): every fire writes to a scratch
WORKFLOW_LOG_DIR; the fixture session id never reaches `.claude/logs/*`.
Tool presence is controlled by PREPENDING a shim dir (the
test_terminal_beacons_1310.py discipline); a hook-side tool missing on the
ambient PATH skips with an explicit reason. No Windows-only resolver is used,
so every case runs on the ubuntu CI runner.

Runner: stdlib unittest + pytest compatible.
    python -m pytest tests/test_subagent_discriminator_1546.py -v
"""

import json
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOKS = REPO_ROOT / ".claude" / "hooks"
PRE_TOOL_BASH = HOOKS / "pre-tool-bash.sh"
PRE_TOOL_EDIT = HOOKS / "pre-tool-edit.sh"
STOP_GATE = HOOKS / "stop-reviewer-gate.sh"

BASH = shutil.which("bash")

# Synthetic ids -- must never reach a production data store (rule #21).
FIXTURE_SID = "subagent-discriminator-1546-fixture"
FIXTURE_AGENT_ID = "agent-fixture1546"

ALLOWED_STATUS = {"attempt", "ok", "ERROR"}
TERMINAL_STATUS = {"ok", "ERROR"}

# `gh` test double: records every call, reports one open PR with zero APPROVE
# comments (stop gate) and an OPEN issue (edit spec-gate). Never touches the
# network.
GH_SHIM = (
    "#!/bin/sh\n"
    "# Test double for bug #1546.\n"
    'if [ -n "${GH_SHIM_CALLS_1546:-}" ]; then echo "$*" >> "$GH_SHIM_CALLS_1546"; fi\n'
    'case "$1 $2" in\n'
    '  "pr list") echo \'[{"number":999999}]\' ;;\n'
    '  "pr view") echo 0 ;;\n'
    '  "issue view") echo "OPEN" ;;\n'
    '  *) echo "" ;;\n'
    "esac\n"
    "exit 0\n"
)


def _payload(event, subagent=False, agent_type_only=False, agent_id=None, **extra):
    """A hook payload carrying the documented common input fields.

    subagent=True adds `agent_id` + `agent_type` (a subagent call).
    agent_type_only=True adds `agent_type` WITHOUT `agent_id` (the main thread
    of a session started with `--agent`).
    agent_id overrides the `agent_id` value verbatim (e.g. "").
    """
    payload = {
        "session_id": FIXTURE_SID,
        "transcript_path": str(Path(tempfile.gettempdir()) / f"{FIXTURE_SID}.jsonl"),
        "cwd": str(REPO_ROOT),
        "permission_mode": "default",
        "hook_event_name": event,
    }
    if subagent:
        payload["agent_id"] = FIXTURE_AGENT_ID
        payload["agent_type"] = "implementer"
    if agent_type_only:
        payload["agent_type"] = "implementer"
    if agent_id is not None:
        payload["agent_id"] = agent_id
    payload.update(extra)
    return payload


def _read_beacons(log_dir: Path, hook_name: str):
    path = log_dir / "hook-fires.jsonl"
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("hook") == hook_name:
            records.append(rec)
    return records


def _decision(stdout: str):
    text = stdout.strip()
    if not text:
        return None
    return json.loads(text).get("hookSpecificOutput", {}).get("permissionDecision")


class _Fire:
    """Real-hook firing with reachability preconditions checked first."""

    def _require_bash(self):
        if BASH is None:
            self.skipTest("SKIPPED (not passed): bash is not resolvable, so no hook could run.")

    def _resolve_in_bash(self, tool, path=None):
        env = dict(os.environ)
        if path is not None:
            env["PATH"] = path
        proc = subprocess.run(
            [BASH, "-c", 'command -v -- "$1"', "_", tool],
            env=env, capture_output=True, text=True, timeout=60,
        )
        out = proc.stdout.strip()
        return out if proc.returncode == 0 and out else None

    def _require(self, tools, site, path=None):
        """Skip (never pass) when a tool the hook routes on is absent ambiently;
        fail loudly when this test's own PATH lost a tool the ambient PATH has."""
        self._require_bash()
        for tool in tools:
            if self._resolve_in_bash(tool) is None:
                self.skipTest(
                    f"SKIPPED (not passed): `{tool}` is not resolvable by bash, so the "
                    f"hook cannot reach {site}. {site} is UNVERIFIED here."
                )
            if path is not None:
                self.assertIsNotNone(
                    self._resolve_in_bash(tool, path),
                    f"ENVIRONMENT ERROR [{site}]: `{tool}` vanished under the test's PATH.",
                )

    def _gh_shim_path(self):
        """Prepend a dir whose `gh` is the recording test double; verify it wins."""
        self._require_bash()
        shim_dir = Path(tempfile.mkdtemp(prefix="gh-shim-1546-"))
        self.addCleanup(shutil.rmtree, shim_dir, ignore_errors=True)
        shim = shim_dir / "gh"
        shim.write_text(GH_SHIM, encoding="utf-8", newline="\n")
        shim.chmod(shim.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        path = str(shim_dir) + os.pathsep + os.environ.get("PATH", "")
        resolved = self._resolve_in_bash("gh", path)
        if resolved is None or shim_dir.name not in resolved:
            self.skipTest("SKIPPED (not passed): the `gh` test double could not win command lookup.")
        return path

    def _fire(self, script, payload, hook_name, extra_env=None, path=None):
        self._require_bash()
        log_dir = Path(tempfile.mkdtemp(prefix="beacon-1546-"))
        self.addCleanup(shutil.rmtree, log_dir, ignore_errors=True)
        calls = log_dir / "gh-calls.txt"
        env = dict(os.environ)
        env["WORKFLOW_LOG_DIR"] = str(log_dir)
        env["CLAUDE_PROJECT_DIR"] = str(REPO_ROOT)
        env["GH_SHIM_CALLS_1546"] = str(calls)
        env.pop("STOP_GATE_BYPASS", None)
        if path is not None:
            env["PATH"] = path
        if extra_env:
            env.update(extra_env)
        proc = subprocess.run(
            [BASH, str(script)],
            input=json.dumps(payload),
            env=env,
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=180,
        )
        gh_calls = calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []
        return proc, _read_beacons(log_dir, hook_name), gh_calls

    def _assert_one_terminal(self, beacons, outcome, site):
        """HOK-008: one attempt, exactly one terminal, status in the closed set."""
        for rec in beacons:
            self.assertIn(rec.get("status"), ALLOWED_STATUS, f"[{site}] bad status: {rec}")
        attempts = [r for r in beacons if r.get("status") == "attempt"]
        terminals = [r for r in beacons if r.get("status") in TERMINAL_STATUS]
        self.assertEqual(len(attempts), 1, f"[{site}] attempts: {beacons}")
        self.assertEqual(len(terminals), 1, f"[{site}] terminals: {beacons}")
        self.assertEqual(terminals[0].get("status"), "ok", f"[{site}] terminal: {beacons}")
        self.assertEqual(terminals[0].get("outcome", ""), outcome, f"[{site}] outcome: {beacons}")
        return terminals[0]


class TestPreToolBashPromoteDiscriminator(_Fire, unittest.TestCase):
    """ADR-0076 D4 clause (2) / PIP-017: `tools/promote.sh` is denied only in
    subagent context, and subagent context is the payload's `agent_id`."""

    HOOK = "pre-tool-bash"

    def _run(self, command, **kw):
        self._require(["python3"], "the classifier")
        payload = _payload("PreToolUse", tool_name="Bash",
                           tool_input={"command": command}, **kw)
        proc, beacons, _ = self._fire(PRE_TOOL_BASH, payload, self.HOOK)
        self.assertEqual(proc.returncode, 0, f"stderr={proc.stderr}")
        return proc, beacons

    def test_promote_denied_when_payload_carries_agent_id(self):
        proc, beacons = self._run("bash tools/promote.sh", subagent=True)
        self.assertEqual(_decision(proc.stdout), "deny", f"stdout={proc.stdout!r}")
        self.assertIn("subagent context", json.loads(proc.stdout)["hookSpecificOutput"]
                      ["permissionDecisionReason"])
        self._assert_one_terminal(beacons, "deny", "promote-subagent")

    def test_direct_promote_path_denied_when_payload_carries_agent_id(self):
        proc, beacons = self._run("./tools/promote.sh", subagent=True)
        self.assertEqual(_decision(proc.stdout), "deny", f"stdout={proc.stdout!r}")
        self._assert_one_terminal(beacons, "deny", "promote-subagent-direct")

    def test_promote_allowed_when_payload_has_no_agent_id(self):
        proc, beacons = self._run("bash tools/promote.sh")
        self.assertEqual(proc.stdout.strip(), "", "main-thread promote must pass untouched")
        self._assert_one_terminal(beacons, "allow", "promote-main")

    def test_promote_allowed_when_only_agent_type_present(self):
        """`agent_type` without `agent_id` is the main thread of an `--agent`
        session -- the operator's own invocation, never a subagent."""
        proc, beacons = self._run("bash tools/promote.sh", agent_type_only=True)
        self.assertEqual(proc.stdout.strip(), "", f"stdout={proc.stdout!r}")
        self._assert_one_terminal(beacons, "allow", "promote-agent-type-only")

    def test_promote_allowed_when_agent_id_is_empty(self):
        proc, beacons = self._run("bash tools/promote.sh", agent_id="")
        self.assertEqual(proc.stdout.strip(), "", f"stdout={proc.stdout!r}")
        self._assert_one_terminal(beacons, "allow", "promote-empty-agent-id")

    def test_promote_mention_not_denied_even_with_agent_id(self):
        proc, beacons = self._run('echo "tools/promote.sh is orchestrator-only"', subagent=True)
        self.assertEqual(proc.stdout.strip(), "", f"stdout={proc.stdout!r}")
        self._assert_one_terminal(beacons, "allow", "promote-mention")


class TestPreToolEditSubagentSkip(_Fire, unittest.TestCase):
    """ADR-0023 D3 step 1: a subagent's tracked-file write skips the rule-#10
    gate; the main thread's identical write is gated."""

    HOOK = "pre-tool-edit"
    # Matches no <type>/<issue#>- pattern: a gated fire ends in the spec-gate
    # deny (jq present) or the jq-missing ask -- a decision either way, and
    # never a real `gh` call.
    BRANCH = "no-issue-branch-1546"

    def _run(self, **kw):
        path = self._gh_shim_path()
        self._require(["python3", "git"], "the subagent/main split", path=path)
        payload = _payload("PreToolUse", tool_name="Edit",
                           tool_input={"file_path": "CLAUDE.md"}, **kw)
        proc, beacons, gh_calls = self._fire(
            PRE_TOOL_EDIT, payload, self.HOOK, extra_env={"BRANCH": self.BRANCH}, path=path,
        )
        self.assertEqual(proc.returncode, 0, f"stderr={proc.stderr}")
        return proc, beacons, gh_calls

    def test_tracked_edit_skipped_when_payload_carries_agent_id(self):
        proc, beacons, gh_calls = self._run(subagent=True)
        self.assertEqual(proc.stdout.strip(), "",
                         f"a subagent write must skip the gate, got: {proc.stdout!r}")
        self.assertEqual(gh_calls, [], "the skip must precede the spec-gate")
        terminal = self._assert_one_terminal(beacons, "", "edit-subagent-skip")
        self.assertEqual(terminal.get("session_id"), FIXTURE_SID)

    def test_tracked_edit_gated_when_payload_has_no_agent_id(self):
        proc, beacons, _ = self._run()
        decision = _decision(proc.stdout)
        self.assertIn(decision, ("ask", "deny"), f"stdout={proc.stdout!r}")
        self._assert_one_terminal(beacons, decision, "edit-main")

    def test_tracked_edit_gated_when_only_agent_type_present(self):
        proc, beacons, _ = self._run(agent_type_only=True)
        decision = _decision(proc.stdout)
        self.assertIn(decision, ("ask", "deny"), f"stdout={proc.stdout!r}")
        self._assert_one_terminal(beacons, decision, "edit-agent-type-only")


class TestStopGateSubagentSkip(_Fire, unittest.TestCase):
    """ADR-0029 D3: the stop gate skips a subagent's Stop; the main thread's
    Stop with an unreviewed open PR is blocked (exit 2)."""

    HOOK = "stop-reviewer-gate"

    def _run(self, tools, **kw):
        path = self._gh_shim_path()
        self._require(tools, "the stop gate", path=path)
        payload = _payload("Stop", stop_hook_active=False, **kw)
        return self._fire(STOP_GATE, payload, self.HOOK, path=path)

    def test_stop_gate_skipped_when_payload_carries_agent_id(self):
        proc, beacons, gh_calls = self._run(["python3"], subagent=True)
        self.assertEqual(proc.returncode, 0,
                         f"a subagent Stop must not be blocked; stderr={proc.stderr!r}")
        self.assertEqual(gh_calls, [], "the skip must precede every gh call")
        terminal = self._assert_one_terminal(beacons, "", "stop-subagent-skip")
        self.assertEqual(terminal.get("session_id"), FIXTURE_SID)

    def test_stop_gate_blocks_when_payload_has_no_agent_id(self):
        proc, beacons, gh_calls = self._run(["python3", "jq"])
        self.assertEqual(proc.returncode, 2, f"stderr={proc.stderr!r}")
        self.assertTrue(gh_calls, "the main-thread Stop must consult gh")
        self._assert_one_terminal(beacons, "deny", "stop-main")


class TestFixtureDiscipline(unittest.TestCase):
    """Rule #21: this module's synthetic ids never reach the production log."""

    def test_production_beacon_log_has_no_fixture_session_id(self):
        common = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=60,
        )
        if common.returncode != 0 or not common.stdout.strip():
            self.skipTest("SKIPPED (not passed): git common dir unresolvable.")
        prod = Path(common.stdout.strip()).parent / ".claude" / "logs" / "hook-fires.jsonl"
        if not prod.exists():
            self.skipTest("SKIPPED (not passed): no production beacon log on this machine.")
        self.assertNotIn(FIXTURE_SID, prod.read_text(encoding="utf-8", errors="replace"))


if __name__ == "__main__":
    unittest.main()
