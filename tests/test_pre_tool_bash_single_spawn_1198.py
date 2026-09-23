"""
tests/test_pre_tool_bash_single_spawn_1198.py

New-feature tests for slice #1198 (PRD #1193 criteria 3a/3a2/3b / ADR-0079 D3):
the single-spawn consolidation + outcome beacon in .claude/hooks/pre-tool-bash.sh.

Covers:
  3a  — allow-path timing: 5x timed synthetic fires against the REAL deployed
        hook, median asserted with a CI-safe margin (<=500 ms). Target from
        the PRD is <=350 ms.
  3a2 — decision-identity fixture on the audit's own evasion probes: a
        QUOTED mention (`echo "git push origin main"`) must allow; a
        COMPOUND real invocation (`ls && gh pr merge 5`) must deny. This is
        the ADDED fixture required alongside the three unmodified existing
        deny-guard suites (test_deny_guard_mechanical_1133.py,
        test_deny_guard_advisory_1135.py,
        test_pre_tool_bash_gh_merge_nudge_1086.py -- untouched by this slice).
  3b  — outcome-field assertions: synthetic deny + warn + allow fires each
        produce a beacon line in hook-fires.jsonl carrying the additive
        `outcome` field with the correct value (deny|warn|allow).

Fixture discipline (rule #21 / ADR-0079 D3): every hook invocation below sets
WORKFLOW_LOG_DIR to a per-test tempdir (the sandbox seam pre-tool-bash.sh
gained in this slice, mirroring pre-tool-edit.sh / stop-reviewer-gate.sh) --
none of these tests write to the production hook-fires.jsonl.

Runner: stdlib unittest + pytest compatible.
    python -m pytest tests/test_pre_tool_bash_single_spawn_1198.py -v
"""
import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
HOOK = REPO_ROOT / ".claude" / "hooks" / "pre-tool-bash.sh"

# CI-safe margin per PRD #1193 criterion 3a; the slice's own target is <=350 ms.
CI_SAFE_MARGIN_MS = 500


def _run_hook(command: str, log_dir: str, extra_env: dict = None):
    """Invoke the hook with the JSON payload delivered via a real temp FILE
    on stdin (mirrors the three pre-existing deny-guard test files' helper
    exactly -- an anonymous pipe from a non-MSYS parent process is unreliable
    on this platform). WORKFLOW_LOG_DIR is ALWAYS set to the sandbox seam
    (rule #21) -- these tests never touch the production fire log."""
    payload = json.dumps({"tool_input": {"command": command}})
    env = os.environ.copy()
    env["WORKFLOW_LOG_DIR"] = log_dir
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


def _out(result):
    return json.loads(result.stdout) if result.stdout.strip() else {}


def _beacon_lines(log_dir: str):
    fires = Path(log_dir) / "hook-fires.jsonl"
    if not fires.exists():
        return []
    out = []
    for line in fires.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


class Test3a2EvasionProbes(unittest.TestCase):
    """3a2: decision identity on the audit's own evasion probes, on top of
    the three unmodified existing suites."""

    def test_quoted_push_main_mention_allowed(self):
        """`echo "git push origin main"` is a MENTION inside an echo argument
        (one quoted token), not the actual invocation -- must allow."""
        with tempfile.TemporaryDirectory() as tmp:
            result = _run_hook('echo "git push origin main"', tmp)
        self.assertEqual(result.returncode, 0)
        out = _out(result)
        self.assertNotIn("hookSpecificOutput", out, msg=f"quoted mention must not be denied: {out}")
        self.assertNotIn("systemMessage", out, msg=f"quoted mention must not be warned: {out}")

    def test_compound_gh_pr_merge_denied(self):
        """`ls && gh pr merge 5` -- the SECOND clause is a real invocation of
        `gh pr merge`; the compound command must still deny."""
        with tempfile.TemporaryDirectory() as tmp:
            result = _run_hook("ls && gh pr merge 5", tmp)
        self.assertEqual(result.returncode, 0)
        out = _out(result)
        self.assertEqual(
            out.get("hookSpecificOutput", {}).get("permissionDecision"), "deny",
            msg=f"real gh pr merge clause in a compound command must deny: {out}",
        )
        self.assertIn("tools/pipe/pr-merge", out["hookSpecificOutput"]["permissionDecisionReason"])


class Test3bOutcomeBeacon(unittest.TestCase):
    """3b: synthetic deny + warn + allow fires each produce a beacon line
    carrying the correct `outcome` value."""

    def test_deny_fire_produces_outcome_deny_beacon(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = _run_hook("git push origin main", tmp)
            self.assertEqual(result.returncode, 0)
            beacons = _beacon_lines(tmp)
        outcomes = [b.get("outcome") for b in beacons if b.get("status") == "ok"]
        self.assertIn("deny", outcomes, msg=f"expected an outcome=deny beacon, got: {beacons}")

    def test_warn_fire_produces_outcome_warn_beacon(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = _run_hook('git commit -m "WIP: something"', tmp)
            self.assertEqual(result.returncode, 0)
            beacons = _beacon_lines(tmp)
        outcomes = [b.get("outcome") for b in beacons if b.get("status") == "ok"]
        self.assertIn("warn", outcomes, msg=f"expected an outcome=warn beacon, got: {beacons}")

    def test_allow_fire_produces_outcome_allow_beacon(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = _run_hook("ls -la", tmp)
            self.assertEqual(result.returncode, 0)
            beacons = _beacon_lines(tmp)
        outcomes = [b.get("outcome") for b in beacons if b.get("status") == "ok"]
        self.assertIn("allow", outcomes, msg=f"expected an outcome=allow beacon, got: {beacons}")

    def test_attempt_beacon_still_precedes_outcome_beacon(self):
        """HOK-008/ADR-0083 D1/D2 preserved: the attempt beacon
        (`status:"attempt"`) is still written first, and precedes the
        terminal outcome-carrying beacon (`status:"ok"`)."""
        with tempfile.TemporaryDirectory() as tmp:
            result = _run_hook("ls -la", tmp)
            self.assertEqual(result.returncode, 0)
            beacons = _beacon_lines(tmp)
        self.assertGreaterEqual(len(beacons), 2, msg=f"expected attempt + outcome beacons, got: {beacons}")
        self.assertEqual(beacons[0].get("status"), "attempt", msg=f"first beacon must be the attempt beacon: {beacons[0]}")
        self.assertEqual(beacons[1].get("status"), "ok")
        self.assertEqual(beacons[1].get("outcome"), "allow")


class Test3aAllowPathTiming(unittest.TestCase):
    """3a: 5x timed synthetic allow fires against the REAL deployed hook;
    median asserted with a CI-safe margin. This is NOT a golden-set
    micro-benchmark -- it is a regression guard against re-introducing the
    eight-jq-spawn allow path."""

    def test_allow_path_median_under_ci_safe_margin(self):
        durations_ms = []
        with tempfile.TemporaryDirectory() as tmp:
            for _ in range(5):
                t0 = time.perf_counter()
                result = _run_hook("ls -la", tmp)
                t1 = time.perf_counter()
                self.assertEqual(result.returncode, 0)
                durations_ms.append((t1 - t0) * 1000.0)
        durations_ms.sort()
        median = durations_ms[len(durations_ms) // 2]
        self.assertLessEqual(
            median, CI_SAFE_MARGIN_MS,
            msg=f"allow-path median {median:.1f} ms exceeds CI-safe margin "
                f"{CI_SAFE_MARGIN_MS} ms (all samples: {durations_ms})",
        )


if __name__ == "__main__":
    unittest.main()
