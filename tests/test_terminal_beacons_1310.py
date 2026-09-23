"""
Live-fire regression tests for slice #1310 (PRD #1266, ADR-0083 D1/D2/D5).

These are NOT grep tests. Each case executes the real hook script with a real
stdin payload and then reads the beacon file the hook actually wrote, so a
regression in the emitter is caught by the emitter's own output rather than by
its source text.

What is asserted:

1. `pre-tool-edit.sh` writes EXACTLY ONE terminal beacon per fire, at every one
   of its seven exit sites, and never two (ADR-0083 D1). The seven sites are
   the seven `exit` statements in the script:
     - inside `emit_ask`      -> terminal `status:"ok"`, `outcome:"ask"`
     - inside `emit_deny`     -> terminal `status:"ok"`, `outcome:"deny"`
     - subagent-context skip  -> terminal `status:"ok"`, no gate decision
     - parser-failure path    -> terminal `status:"ERROR"` (already existed;
                                 asserted here so the new trap cannot double-emit)
     - allowlisted file_path  -> terminal `status:"ok"`, no gate decision
     - empty file_path        -> terminal `status:"ok"`, no gate decision
     - untracked file_path    -> terminal `status:"ok"`, no gate decision

2. `status` never carries a gate decision (ADR-0083 D2): the closed lifecycle
   set is {attempt, ok, ERROR}. A DENY is a *completion*, so it reports
   `status:"ok"` with the decision in the additive `outcome` field. A hook that
   forges a crash signature to express a policy decision fails these tests.

3. `stop-reviewer-gate.sh`'s DENY path (exit 2) emits `status:"ok"` +
   `outcome:"deny"`, and its stderr states the observation plus BOTH states
   consistent with it (reviewer not yet dispatched; or the PR is correctly
   awaiting its prerequisite codebase-critic pass per PIP-013) plus the
   `STOP_GATE_BYPASS=1` escape (ADR-0083 D5).

4. `pre-tool-bash.sh` (slice #1312, the third registered gate hook): its
   deny/warn/allow routes each emit exactly one attempt beacon (bash side)
   and exactly one terminal beacon (`status:"ok"` + `outcome` in
   {deny,warn,allow}, written by the companion `pre-tool-bash-classify.py`
   process into the SAME hook-fires.jsonl) — closing the "three-gate"
   coverage this module's AC calls for alongside pre-tool-edit.sh and
   stop-reviewer-gate.sh above.

5. Rule #21 (fixture discipline): every fire in this module writes to a scratch
   `WORKFLOW_LOG_DIR`; the production `.claude/logs/hook-fires.jsonl` must never
   contain this module's synthetic session id.

REACHABILITY DISCIPLINE (the rule this module shipped without)
--------------------------------------------------------------
Asserting on an exit site without first verifying the hook could REACH it is
the defect class these helpers exist to prevent. `pre-tool-edit.sh` routes on
four PATH-resolved tools — `python3` (step 4 parse), `jq` (step 6), `git`
(step 7 tracked-file check) and `gh` (step 8 spec-gate) — and every one of them,
when missing, diverts the fire to a DIFFERENT terminal that still exits 0.

So, before any fire:

  * Preconditions are resolved with the SAME resolver the hook uses —
    `command -v` inside bash, under the exact PATH the fire will run with —
    never with `shutil.which`, which on win32 honours PATHEXT and therefore
    disagrees with MSYS bash about extensionless executables.
  * A tool missing from the ambient PATH is an ENVIRONMENT LIMITATION: the
    affected test SKIPS WITH AN EXPLICIT REASON. It never passes silently on an
    unexercised code path.
  * A tool that resolves ambiently but NOT under the test's own PATH override
    is a TEST BUG: it FAILS LOUDLY as an environment error, because every
    assertion after it would otherwise land on a terminal reached by accident.

Tool PRESENCE is controlled by PREPENDING a shim directory, which is additive
and therefore cannot take `python3`/`jq`/`git` away. Tool ABSENCE is never
simulated by dropping PATH entries: the first version of this file hid `gh` by
removing every PATH directory that provided it, and on the CI runner all four
tools live in /usr/bin — so the whole toolchain went with it, `pre-tool-edit.sh`
took its parser-failure terminal (exit 0, empty stdout), and the emit_ask case
asserted against a site the hook never reached (CI run 33636943004).

Runner: stdlib unittest + pytest compatible.
  python -m pytest tests/test_terminal_beacons_1310.py -v
  python -m unittest tests.test_terminal_beacons_1310 -v
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
PRE_TOOL_EDIT = REPO_ROOT / ".claude" / "hooks" / "pre-tool-edit.sh"
STOP_GATE = REPO_ROOT / ".claude" / "hooks" / "stop-reviewer-gate.sh"
PRE_TOOL_BASH = REPO_ROOT / ".claude" / "hooks" / "pre-tool-bash.sh"

# Synthetic id — must never reach a production data store (CLAUDE.md rule #21).
FIXTURE_SID = "live-fire-1310-fixture"

# Only bash is resolved Python-side: it is the interpreter this module *invokes*,
# not a tool the hook resolves through PATH. Every hook-side tool goes through
# `_resolve_in_bash` instead, so the precondition and the code under test agree.
BASH = shutil.which("bash")

# The closed lifecycle vocabulary (ADR-0083 D2). Anything else is a schema break.
ALLOWED_STATUS = {"attempt", "ok", "ERROR"}
TERMINAL_STATUS = {"ok", "ERROR"}

# `gh` test double for pre-tool-edit.sh's spec-gate: the branch's issue exists
# and is OPEN, so the gate falls through to the rule-#10 ask. Never touches the
# network.
GH_SHIM_ISSUE_OPEN = (
    "#!/bin/sh\n"
    "# Test double for slice #1310: `gh issue view` reports an OPEN issue.\n"
    'case "$1 $2" in\n'
    '  "issue view") echo "OPEN" ;;\n'
    '  *) echo "" ;;\n'
    "esac\n"
    "exit 0\n"
)

# `gh` test double for stop-reviewer-gate.sh: one open PR, zero APPROVE comments.
GH_SHIM_NO_APPROVE = (
    "#!/bin/sh\n"
    "# Test double for slice #1310: one open PR, zero VERDICT: APPROVE comments.\n"
    'case "$1 $2" in\n'
    '  "pr list") echo \'[{"number":999999}]\' ;;\n'
    '  "pr view") echo 0 ;;\n'
    '  *) echo "" ;;\n'
    "esac\n"
    "exit 0\n"
)


def _read_beacons(log_dir: Path, hook_name: str):
    """Parse hook-fires.jsonl, keeping only records emitted by `hook_name`."""
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


class _HookFireMixin:
    def _require_bash(self):
        if BASH is None:
            self.skipTest(
                "SKIPPED (not passed): `bash` is not resolvable from this Python "
                "process, so the hook could not be executed at all. The terminal-beacon "
                "contract is UNVERIFIED on this machine."
            )

    # --- reachability helpers ----------------------------------------------

    def _resolve_in_bash(self, tool: str, path_override=None):
        """Resolve `tool` exactly the way the hook's own bash resolves it.

        Returns the resolved path, or None when bash cannot find the tool.
        """
        self._require_bash()
        env = dict(os.environ)
        if path_override is not None:
            env["PATH"] = path_override
        proc = subprocess.run(
            [BASH, "-c", 'command -v -- "$1"', "_", tool],
            env=env, capture_output=True, text=True, timeout=60,
        )
        resolved = proc.stdout.strip()
        return resolved if proc.returncode == 0 and resolved else None

    def _require_toolchain(self, tools, site: str, path_override=None):
        """Verify the hook can REACH `site` before anything is asserted about it.

        Missing on the ambient PATH -> SKIP with an explicit reason (an honest
        environment limitation). Present ambiently but gone under this test's own
        PATH override -> FAIL LOUDLY: the test broke the toolchain, and the fire
        would land on some other exit site while still exiting 0.
        """
        for tool in tools:
            ambient = self._resolve_in_bash(tool)
            if ambient is None:
                self.skipTest(
                    f"SKIPPED (not passed): `{tool}` is not resolvable by bash on this "
                    f"machine, so pre-flight the hook cannot reach the {site} exit site — "
                    f"it would divert to a different terminal. {site} is UNVERIFIED here."
                )
            if path_override is None:
                continue
            under_override = self._resolve_in_bash(tool, path_override)
            self.assertIsNotNone(
                under_override,
                f"ENVIRONMENT ERROR [{site}]: `{tool}` resolves on the ambient PATH "
                f"({ambient}) but NOT under the PATH this test hands the hook. The test's "
                f"own PATH handling removed part of the toolchain the hook routes on, so "
                f"the fire would reach whatever terminal it hits by accident (historically: "
                f"the parser-failure ERROR exit, which exits 0 with empty stdout) instead of "
                f"{site}. Control tool presence by PREPENDING a shim dir — never by dropping "
                f"PATH directories.",
            )

    def _shim_path(self, name: str, body: str):
        """Prepend a directory providing `name`, and verify it wins command lookup.

        Additive by construction: nothing is removed from PATH, so the rest of the
        hook's toolchain is untouched. Returns the new PATH, or None when the shim
        cannot be made to win lookup (the caller then skips loudly).
        """
        self._require_bash()
        shim_dir = Path(tempfile.mkdtemp(prefix="shim-1310-"))
        self.addCleanup(shutil.rmtree, shim_dir, ignore_errors=True)
        shim = shim_dir / name
        shim.write_text(body, encoding="utf-8", newline="\n")
        shim.chmod(shim.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        new_path = str(shim_dir) + os.pathsep + os.environ.get("PATH", "")
        # Verify the precondition rather than assuming it.
        resolved = self._resolve_in_bash(name, new_path)
        if resolved is None or shim_dir.name not in resolved:
            return None
        return new_path

    def _require_shim(self, name: str, body: str, site: str):
        """`_shim_path` + the loud skip its None return means."""
        path_override = self._shim_path(name, body)
        if path_override is None:
            self.skipTest(
                f"SKIPPED (not passed): the `{name}` test double could not be made to win "
                f"command lookup in bash, so the {site} exit site could not be driven. "
                f"{site} is UNVERIFIED here."
            )
        return path_override

    # --- firing -------------------------------------------------------------

    def _fire(self, script: Path, payload, hook_name: str, extra_env=None, path_override=None):
        """Execute a hook for real and return (CompletedProcess, [beacon records])."""
        self._require_bash()
        log_dir = Path(tempfile.mkdtemp(prefix="beacon-1310-"))
        self.addCleanup(shutil.rmtree, log_dir, ignore_errors=True)

        env = dict(os.environ)
        env["WORKFLOW_LOG_DIR"] = str(log_dir)
        env["CLAUDE_PROJECT_DIR"] = str(REPO_ROOT)
        # This would short-circuit the very paths under test.
        env.pop("STOP_GATE_BYPASS", None)
        if path_override is not None:
            env["PATH"] = path_override
        if extra_env:
            env.update(extra_env)

        stdin = payload if isinstance(payload, str) else json.dumps(payload)
        proc = subprocess.run(
            [BASH, str(script)],
            input=stdin,
            env=env,
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=180,
        )
        return proc, _read_beacons(log_dir, hook_name)

    def _assert_single_terminal(self, beacons, expected_status, expected_outcome, site):
        """Core ADR-0083 D1/D2 assertion: one attempt, exactly one terminal, closed status."""
        self.assertTrue(
            beacons,
            f"[{site}] hook wrote NO beacons at all — attempt beacon missing too.",
        )
        for rec in beacons:
            self.assertIn(
                rec.get("status"), ALLOWED_STATUS,
                f"[{site}] status {rec.get('status')!r} is outside the closed set "
                f"{sorted(ALLOWED_STATUS)} (ADR-0083 D2). Record: {rec}",
            )

        attempts = [r for r in beacons if r.get("status") == "attempt"]
        terminals = [r for r in beacons if r.get("status") in TERMINAL_STATUS]

        self.assertEqual(
            len(attempts), 1,
            f"[{site}] expected exactly 1 attempt beacon, got {len(attempts)}: {beacons}",
        )
        self.assertEqual(
            len(terminals), 1,
            f"[{site}] expected exactly 1 terminal beacon (ADR-0083 D1), got "
            f"{len(terminals)}. A missing terminal means the exit site is silent; two "
            f"means the trap double-fired. Records: {beacons}",
        )

        terminal = terminals[0]
        self.assertEqual(
            terminal.get("status"), expected_status,
            f"[{site}] terminal status mismatch. Records: {beacons}",
        )
        # ADR-0083 D2: the gate decision lives in `outcome`, never in `status`.
        self.assertEqual(
            terminal.get("outcome", ""), expected_outcome,
            f"[{site}] terminal `outcome` mismatch — a policy decision must be recorded "
            f"additively in `outcome`, never forged into `status`. Records: {beacons}",
        )
        return terminal


class TestPreToolEditTerminalBeacons(_HookFireMixin, unittest.TestCase):
    """One terminal beacon at each of pre-tool-edit.sh's seven exit sites."""

    HOOK = "pre-tool-edit"

    def _payload(self, file_path):
        return {
            "session_id": FIXTURE_SID,
            "tool_name": "Edit",
            "tool_input": {"file_path": file_path} if file_path is not None else {},
        }

    # --- site 1: emit_ask ---------------------------------------------------

    def test_site_emit_ask_emits_ok_with_outcome_ask(self):
        """Rule-#10 escalate-to-ask fallback (step 9).

        Driven through the spec-gate's pass-through: a `gh` test double reports
        the branch's issue OPEN, so the gate finds nothing to deny and falls
        through to emit_ask. Offline — the double never touches the network.

        Reaching step 9 requires python3 (step 4 parse), jq (step 6) and git
        (step 7 tracked-file check); without any one of them the fire exits 0 at
        an EARLIER terminal, which is exactly how this case used to assert
        against a site the hook never reached.
        """
        path_override = self._require_shim("gh", GH_SHIM_ISSUE_OPEN, "emit_ask")
        self._require_toolchain(
            ["python3", "jq", "git", "gh"], "emit_ask", path_override=path_override
        )
        proc, beacons = self._fire(
            PRE_TOOL_EDIT,
            self._payload("CLAUDE.md"),
            self.HOOK,
            # Explicit branch: the checkout's real HEAD is a detached merge ref on
            # CI, so the pattern match must not depend on it.
            extra_env={"BRANCH": "feat/1310-live-fire-fixture"},
            path_override=path_override,
        )
        self.assertEqual(proc.returncode, 0, f"stderr={proc.stderr}")
        self.assertIn(
            '"permissionDecision":"ask"', proc.stdout.replace(" ", ""),
            f"expected an ask decision on stdout, got: {proc.stdout!r}",
        )
        self._assert_single_terminal(beacons, "ok", "ask", "emit_ask")

    # --- site 2: emit_deny --------------------------------------------------

    def test_site_emit_deny_emits_ok_with_outcome_deny(self):
        """Spec-gate DENY on a branch that matches no <type>/<issue#>- pattern.

        `gh` must merely be present (the script tests for it); the deny fires in
        the else-branch before `gh` is ever invoked. The test double supplies that
        presence so the precondition is controlled rather than inherited from
        whatever the machine happens to have installed — and so the real `gh`
        binary can never be reached from here.
        """
        path_override = self._require_shim("gh", GH_SHIM_ISSUE_OPEN, "emit_deny")
        self._require_toolchain(
            ["python3", "jq", "git", "gh"], "emit_deny", path_override=path_override
        )
        proc, beacons = self._fire(
            PRE_TOOL_EDIT,
            self._payload("CLAUDE.md"),
            self.HOOK,
            extra_env={"BRANCH": "branch-matching-no-known-pattern"},
            path_override=path_override,
        )
        self.assertEqual(proc.returncode, 0, f"stderr={proc.stderr}")
        self.assertIn(
            '"permissionDecision":"deny"', proc.stdout.replace(" ", ""),
            f"expected a deny decision on stdout, got: {proc.stdout!r}",
        )
        terminal = self._assert_single_terminal(beacons, "ok", "deny", "emit_deny")
        # The load-bearing half of ADR-0083 D1: a DENY is a completion, not a crash.
        self.assertNotEqual(
            terminal.get("status"), "ERROR",
            "a deliberate DENY must not be reported as an ERROR (forged crash signature)",
        )

    # --- site 3: subagent-context skip -------------------------------------

    def test_site_subagent_skip_emits_ok_terminal(self):
        """The skip reads the payload's `agent_id` (ADR-0091 D1), so it sits
        after the step-3 python3 parse: without python3 the fire would stop at
        the parser-failure terminal instead."""
        self._require_toolchain(["python3"], "subagent-skip")
        payload = dict(self._payload("CLAUDE.md"), agent_id="agent-live-fire-1310",
                       agent_type="implementer")
        proc, beacons = self._fire(PRE_TOOL_EDIT, payload, self.HOOK)
        self.assertEqual(proc.returncode, 0, f"stderr={proc.stderr}")
        self._assert_single_terminal(beacons, "ok", "", "subagent-skip")

    # --- site 4: parser-failure ERROR path ---------------------------------

    def test_site_parser_failure_emits_exactly_one_error_terminal(self):
        """Pre-existing ERROR terminal. Asserted so the new EXIT trap cannot
        append a second terminal on top of it.

        python3 is required as a precondition even though this case EXPECTS the
        parser to fail: a missing interpreter produces the same ERROR terminal, so
        without the probe this case would pass while proving nothing about the
        corrupt payload.
        """
        self._require_toolchain(["python3"], "parser-failure")
        proc, beacons = self._fire(
            PRE_TOOL_EDIT, '{"session_id": "' + FIXTURE_SID + '", NOT-JSON', self.HOOK
        )
        self.assertEqual(proc.returncode, 0, f"stderr={proc.stderr}")
        terminal = self._assert_single_terminal(beacons, "ERROR", "", "parser-failure")
        self.assertEqual(terminal.get("session_id"), FIXTURE_SID)

    # --- site 5: allowlisted path ------------------------------------------

    def test_site_allowlisted_path_emits_ok_terminal(self):
        """Step 5 sits AFTER the python3 parse: without python3 the fire would
        stop at the parser-failure terminal instead."""
        self._require_toolchain(["python3"], "allowlist")
        proc, beacons = self._fire(
            PRE_TOOL_EDIT, self._payload(".claude/logs/hook-fires.jsonl"), self.HOOK
        )
        self.assertEqual(proc.returncode, 0, f"stderr={proc.stderr}")
        self._assert_single_terminal(beacons, "ok", "", "allowlist")

    # --- site 6: empty file_path -------------------------------------------

    def test_site_empty_file_path_emits_ok_terminal(self):
        """The empty-path exit sits after the step 6 jq check, so python3 AND jq
        must both resolve or the fire exits at an earlier terminal."""
        self._require_toolchain(["python3", "jq"], "empty-file-path")
        proc, beacons = self._fire(PRE_TOOL_EDIT, self._payload(None), self.HOOK)
        self.assertEqual(proc.returncode, 0, f"stderr={proc.stderr}")
        self._assert_single_terminal(beacons, "ok", "", "empty-file-path")

    # --- site 7: untracked file --------------------------------------------

    def test_site_untracked_file_emits_ok_terminal(self):
        """git is a precondition, not a detail: `git ls-files` failing because git
        is ABSENT produces the same 'untracked' exit as a genuinely untracked
        file, so without the probe this case could pass for the wrong reason."""
        self._require_toolchain(["python3", "jq", "git"], "untracked-file")
        untracked = ".claude/this-path-is-deliberately-untracked-1310.tmp"
        self.assertFalse(
            (REPO_ROOT / untracked).exists(),
            "fixture path must not exist in the repo",
        )
        proc, beacons = self._fire(PRE_TOOL_EDIT, self._payload(untracked), self.HOOK)
        self.assertEqual(proc.returncode, 0, f"stderr={proc.stderr}")
        self._assert_single_terminal(beacons, "ok", "", "untracked-file")


class TestPreToolBashTerminalBeacons(_HookFireMixin, unittest.TestCase):
    """The third registered gate hook (slice #1312, PRD #1266 AC #3): one
    attempt beacon + one terminal beacon (status:"ok" + outcome) at each of
    pre-tool-bash.sh's three real-classifier routes. The attempt beacon is
    written by pre-tool-bash.sh itself (bash); the terminal beacon is written
    by the companion pre-tool-bash-classify.py process it single-spawns
    (ADR-0079 D3 / slice #1198) — both append to the same hook-fires.jsonl.
    """

    HOOK = "pre-tool-bash"

    def _payload(self, command):
        return {"tool_input": {"command": command}}

    def test_deny_route_emits_ok_with_outcome_deny(self):
        self._require_toolchain(["python3"], "deny")
        proc, beacons = self._fire(
            PRE_TOOL_BASH, self._payload("git push origin main"), self.HOOK
        )
        self.assertEqual(proc.returncode, 0, f"stderr={proc.stderr}")
        self.assertIn(
            '"permissionDecision":"deny"', proc.stdout.replace(" ", ""),
            f"expected a deny decision on stdout, got: {proc.stdout!r}",
        )
        terminal = self._assert_single_terminal(beacons, "ok", "deny", "deny-route")
        self.assertNotEqual(
            terminal.get("status"), "ERROR",
            "a deliberate DENY must not be reported as an ERROR (forged crash signature)",
        )

    def test_warn_route_emits_ok_with_outcome_warn(self):
        self._require_toolchain(["python3"], "warn")
        proc, beacons = self._fire(
            PRE_TOOL_BASH, self._payload('git commit -m "WIP: something"'), self.HOOK
        )
        self.assertEqual(proc.returncode, 0, f"stderr={proc.stderr}")
        self._assert_single_terminal(beacons, "ok", "warn", "warn-route")

    def test_allow_route_emits_ok_with_outcome_allow(self):
        self._require_toolchain(["python3"], "allow")
        proc, beacons = self._fire(PRE_TOOL_BASH, self._payload("ls -la"), self.HOOK)
        self.assertEqual(proc.returncode, 0, f"stderr={proc.stderr}")
        self._assert_single_terminal(beacons, "ok", "allow", "allow-route")


class TestStopReviewerGateDenyBeacon(_HookFireMixin, unittest.TestCase):
    """ADR-0083 D1/D2/D5 for stop-reviewer-gate.sh's blocking (exit 2) path."""

    HOOK = "stop-reviewer-gate"

    def _deny_route(self):
        """Shim + probe for the gate's blocking path.

        The gate soft-degrades to an ERROR beacon when `gh` or `jq` is missing and
        loses the session id when python3 is missing — three different ways to
        exit 0-or-wrong while looking plausible. All three are checked first.
        """
        path_override = self._require_shim("gh", GH_SHIM_NO_APPROVE, "stop-gate-deny")
        self._require_toolchain(
            ["python3", "jq", "gh"], "stop-gate-deny", path_override=path_override
        )
        return path_override

    def test_deny_path_emits_ok_status_with_outcome_deny(self):
        path_override = self._deny_route()
        proc, beacons = self._fire(
            STOP_GATE,
            {"session_id": FIXTURE_SID, "stop_hook_active": False},
            self.HOOK,
            path_override=path_override,
        )
        self.assertEqual(
            proc.returncode, 2,
            f"expected the gate to block with exit 2; got {proc.returncode}. "
            f"stdout={proc.stdout!r} stderr={proc.stderr!r}",
        )
        terminal = self._assert_single_terminal(beacons, "ok", "deny", "stop-gate-deny")
        self.assertEqual(terminal.get("session_id"), FIXTURE_SID)

    def test_deny_stderr_names_both_consistent_states_and_the_escape(self):
        """ADR-0083 D5: the message states the observation and the states
        consistent with it, never a single cause it cannot distinguish."""
        path_override = self._deny_route()
        proc, _ = self._fire(
            STOP_GATE,
            {"session_id": FIXTURE_SID, "stop_hook_active": False},
            self.HOOK,
            path_override=path_override,
        )
        err = proc.stderr
        self.assertIn("999999", err, "the message must name the PR(s) it observed")
        low = err.lower()
        # State (a): the reviewer has not been dispatched yet.
        self.assertIn("not been dispatched", low,
                      f"first consistent state not named in: {err!r}")
        # State (b): the PR is correctly awaiting codebase-critic first (PIP-013).
        self.assertIn("codebase-critic", low,
                      f"second consistent state not named in: {err!r}")
        self.assertIn("PIP-013", err, f"the PIP-013 basis is not cited in: {err!r}")
        # The escape hatch stays discoverable.
        self.assertIn("STOP_GATE_BYPASS=1", err,
                      f"the bypass escape is not offered in: {err!r}")

    def test_deny_message_does_not_assert_a_single_undistinguishable_cause(self):
        """The pre-ADR-0083 wording ordered one corrective action as if the cause
        were known. That imperative must be gone (ADR-0083 D5)."""
        needle = "dispatch reviewer subagent before declaring done"
        offending = [
            (n, line.strip())
            for n, line in enumerate(
                STOP_GATE.read_text(encoding="utf-8", errors="replace").splitlines(), 1
            )
            if needle in line
        ]
        self.assertEqual(
            offending, [],
            "the single-cause imperative must be replaced by an observation + the "
            f"states consistent with it (ADR-0083 D5); still present at: {offending}",
        )


class TestNoPathDirectoryRemoval(unittest.TestCase):
    """Guard the fix itself: the removal pattern must not come back.

    CI run 33636943004 failed because this module hid `gh` by dropping every PATH
    directory that provided it, which on the runner is /usr/bin — python3, jq and
    git went with it. Prepending is additive and safe; probing one PATH entry at a
    time is how the removal pattern gets rebuilt, so that shape is banned here.
    """

    def test_module_never_filters_path_by_directory(self):
        source = Path(__file__).read_text(encoding="utf-8", errors="replace")
        # Assembled from fragments so this detector cannot match its own source.
        call = "shutil.which" + "("
        per_dir_kwarg = "path" + "="
        offending = [
            (n, line.strip())
            for n, line in enumerate(source.splitlines(), 1)
            if call in line and per_dir_kwarg in line
        ]
        self.assertEqual(
            offending, [],
            "per-directory PATH filtering removes every tool the directory "
            "provides, not just the named one. Control tool presence by prepending "
            f"a shim dir (`_shim_path`) instead. Found at: {offending}",
        )


class TestFixtureDiscipline(unittest.TestCase):
    """CLAUDE.md rule #21 — this module's synthetic data must never reach a
    production data store."""

    def test_production_beacon_log_has_no_fixture_session_id(self):
        prod = REPO_ROOT / ".claude" / "logs" / "hook-fires.jsonl"
        if not prod.exists():
            self.skipTest(
                "SKIPPED (not passed): no production beacon log on this machine, so the "
                "no-leak property could not be observed."
            )
        text = prod.read_text(encoding="utf-8", errors="replace")
        self.assertNotIn(
            FIXTURE_SID, text,
            "synthetic live-fire session id leaked into the production beacon log — "
            "every fire in this module must be redirected via WORKFLOW_LOG_DIR (rule #21)",
        )


if __name__ == "__main__":
    unittest.main()
