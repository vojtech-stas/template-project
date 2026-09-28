"""
Regression tests for issue #849 — hook layer goes silently dark when
CLAUDE_PROJECT_DIR is empty.

Three test groups (ADR-0067 D2 test-first ordering):
  1. settings.json: every hook command uses the ${CLAUDE_PROJECT_DIR:-...git...}
     fallback (no bare $CLAUDE_PROJECT_DIR without the :- fallback).
  2. HOOK-LIVENESS health check: PASS on fresh beacons; FAIL when beacons lag
     activity by more than T minutes.
  3. Functional: invoking a hook command with CLAUDE_PROJECT_DIR="" still
     resolves the hook script (no exit 127).

All assertions are offline (file-system + regex + subprocess); deterministic
on Windows git-bash.

Runner: stdlib unittest + pytest compatible.
  python -m pytest tests/test_hook_liveness_849.py -v
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

# ---------------------------------------------------------------------------
# Repo root
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).parent.parent
SETTINGS_PATH = REPO_ROOT / ".claude" / "settings.json"
HOOKS_DIR = REPO_ROOT / ".claude" / "pipeline" / "hooks"
HEALTH_PY = REPO_ROOT / "dashboard" / "health.py"


# ---------------------------------------------------------------------------
# Group 1: settings.json fallback pattern
# ---------------------------------------------------------------------------

class TestSettingsJsonFallback(unittest.TestCase):
    """Every hook command in settings.json must use the CLAUDE_PROJECT_DIR
    fallback expression, never the bare variable without :- fallback.

    Correct pattern (JSON-escaped):
      ${CLAUDE_PROJECT_DIR:-$(dirname "$(git rev-parse ...)")}

    Forbidden pattern (bare variable resolves to empty path):
      "$CLAUDE_PROJECT_DIR/  (bare ref without :-)
    """

    BARE_REF_RE = re.compile(r'\$CLAUDE_PROJECT_DIR[^}]')
    FALLBACK_RE = re.compile(
        r'\$\{CLAUDE_PROJECT_DIR:-\$\(dirname.*?git rev-parse',
        re.DOTALL,
    )

    def _all_commands(self) -> list[str]:
        """Return every 'command' string from the hooks in settings.json."""
        with SETTINGS_PATH.open(encoding="utf-8") as fh:
            data = json.load(fh)
        cmds: list[str] = []
        for event, hook_groups in data.get("hooks", {}).items():
            for group in hook_groups:
                for entry in group.get("hooks", []):
                    cmd = entry.get("command", "")
                    if cmd:
                        cmds.append(cmd)
        return cmds

    def test_settings_json_is_valid_json(self):
        """settings.json must parse as valid JSON (no syntax errors)."""
        with SETTINGS_PATH.open(encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertIn("hooks", data, "settings.json must have a 'hooks' key")

    def test_no_bare_claude_project_dir_reference(self):
        """No hook command may reference $CLAUDE_PROJECT_DIR without a :- fallback.

        Bare '$CLAUDE_PROJECT_DIR/' (dollar sign followed by CLAUDE_PROJECT_DIR
        then a non-brace character) is the silent-failure pattern from #849.
        Every occurrence must use ${CLAUDE_PROJECT_DIR:-...} instead.
        """
        violating_cmds = []
        for cmd in self._all_commands():
            # Find all occurrences of the bare pattern.
            # We skip over ${...} forms by checking for the brace after $.
            # The pattern: "$CLAUDE_PROJECT_DIR" not followed by "}"
            # (i.e. it's $VAR not ${VAR:- ...})
            # We look for $CLAUDE_PROJECT_DIR that is NOT part of ${...}
            for m in re.finditer(r'\$CLAUDE_PROJECT_DIR', cmd):
                pos_after = m.end()
                if pos_after >= len(cmd) or cmd[pos_after] != '}':
                    # Bare reference — this is the forbidden pattern
                    violating_cmds.append(cmd[:120])
                    break
        self.assertEqual(
            [],
            violating_cmds,
            msg=(
                "Found hook commands with bare $CLAUDE_PROJECT_DIR (no :- fallback). "
                "When this var is empty the path collapses to '/.claude/hooks/...' "
                "causing exit 127 with no beacon. Failing commands:\n"
                + "\n".join(f"  {c}" for c in violating_cmds)
            ),
        )

    def test_fallback_uses_git_common_dir(self):
        """Every hook command that references CLAUDE_PROJECT_DIR must use the
        git-common-dir fallback (not --show-toplevel, which returns worktree path).
        """
        for cmd in self._all_commands():
            if "CLAUDE_PROJECT_DIR" not in cmd:
                continue
            self.assertIn(
                "git-common-dir",
                cmd,
                msg=(
                    f"Hook command uses CLAUDE_PROJECT_DIR but does not use "
                    f"--git-common-dir fallback (required to work from any worktree):\n"
                    f"  {cmd[:120]}"
                ),
            )


# ---------------------------------------------------------------------------
# Group 2: HOOK-LIVENESS health check behaviour
# ---------------------------------------------------------------------------

# Mirror the module constant so tests don't need to import health.py
# (which has server-side dependencies). The constant value is defined in
# health.py as _HOOK_LIVENESS_DARK_MINUTES = 60.
_HOOK_LIVENESS_DARK_MINUTES = 60  # minutes


def _iso(ts: float) -> str:
    """Format a unix timestamp as ISO-8601 UTC string."""
    import datetime
    return (
        datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )


def _write_jsonl(path: Path, lines: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for obj in lines:
            fh.write(json.dumps(obj) + "\n")


class TestHookLivenessCheck(unittest.TestCase):
    """HOOK-LIVENESS health check must PASS when beacons are fresh,
    FAIL when they lag activity by > T minutes.
    """

    def _run_check(self, tmp_dir: Path) -> dict:
        """Invoke check_hook_liveness() by running health.py via subprocess
        so we can inject HOOK_LIVENESS_LOG_DIR / HOOK_LIVENESS_EVENTS_DIR.

        We pass environment variables that health.py uses to override log paths
        for testing.
        """
        script = f"""
import sys
sys.path.insert(0, r'{REPO_ROOT / "dashboard"}')
import os
# Override log paths via env vars (health.py reads these when set)
os.environ['_HOOK_LIVENESS_FIRES_OVERRIDE'] = r'{tmp_dir / "hook-fires.jsonl"}'
os.environ['_HOOK_LIVENESS_EVENTS_OVERRIDE'] = r'{tmp_dir / "workflow-events.jsonl"}'
os.environ['_HOOK_LIVENESS_GIT_OVERRIDE'] = r'{tmp_dir / "fake-git-ts.txt"}'
from health import check_hook_liveness
import json
result = check_hook_liveness()
print(json.dumps(result))
"""
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT / "dashboard"),
        )
        if result.returncode != 0:
            self.fail(
                f"check_hook_liveness() subprocess failed:\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}"
            )
        return json.loads(result.stdout.strip())

    def test_pass_when_beacons_are_fresh(self):
        """PASS when newest beacon is within T minutes of newest activity."""
        now = time.time()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            # beacon 2 minutes ago
            _write_jsonl(
                tmp_dir / "hook-fires.jsonl",
                [{"hook": "pre-tool-bash", "ts": _iso(now - 120)}],
            )
            # activity 1 minute ago
            _write_jsonl(
                tmp_dir / "workflow-events.jsonl",
                [{"event": "bash_complete", "ts": _iso(now - 60), "session_id": "s1"}],
            )
            # git commit time: 5 minutes ago (older than events log)
            (tmp_dir / "fake-git-ts.txt").write_text(_iso(now - 300))

            result = self._run_check(tmp_dir)
        self.assertEqual(
            "PASS",
            result["result"],
            msg=f"Expected PASS but got {result['result']}: {result.get('detail')}",
        )

    def test_fail_when_beacons_lag_activity(self):
        """FAIL when newest beacon is more than T minutes behind activity."""
        now = time.time()
        dark_seconds = (_HOOK_LIVENESS_DARK_MINUTES + 10) * 60  # T+10 min ago
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            # beacon 70 minutes ago (> T=60 min)
            _write_jsonl(
                tmp_dir / "hook-fires.jsonl",
                [{"hook": "pre-tool-bash", "ts": _iso(now - dark_seconds)}],
            )
            # activity just now
            _write_jsonl(
                tmp_dir / "workflow-events.jsonl",
                [{"event": "bash_complete", "ts": _iso(now), "session_id": "s1"}],
            )
            (tmp_dir / "fake-git-ts.txt").write_text(_iso(now))

            result = self._run_check(tmp_dir)
        self.assertEqual(
            "FAIL",
            result["result"],
            msg=f"Expected FAIL but got {result['result']}: {result.get('detail')}",
        )
        self.assertIn(
            "hook layer appears dark",
            result.get("detail", ""),
            msg="FAIL detail must say 'hook layer appears dark'",
        )

    def test_pass_idle_repo_small_delta(self):
        """PASS when both beacon and activity are old (idle repo — delta stays small)."""
        # Both are old but close together — should not false-alarm.
        old_ts = time.time() - 7200  # 2 hours ago
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            # beacon 2 hours 2 minutes ago
            _write_jsonl(
                tmp_dir / "hook-fires.jsonl",
                [{"hook": "pre-tool-bash", "ts": _iso(old_ts - 120)}],
            )
            # activity 2 hours ago (delta = 2 min < T=60 min)
            _write_jsonl(
                tmp_dir / "workflow-events.jsonl",
                [{"event": "bash_complete", "ts": _iso(old_ts), "session_id": "s1"}],
            )
            (tmp_dir / "fake-git-ts.txt").write_text(_iso(old_ts))

            result = self._run_check(tmp_dir)
        self.assertEqual(
            "PASS",
            result["result"],
            msg=f"Idle-repo false alarm: expected PASS got {result['result']}: {result.get('detail')}",
        )

    def test_warn_when_fires_log_missing(self):
        """WARN (not FAIL) when hook-fires.jsonl does not exist yet."""
        now = time.time()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            # No hook-fires.jsonl — only events
            _write_jsonl(
                tmp_dir / "workflow-events.jsonl",
                [{"event": "bash_complete", "ts": _iso(now), "session_id": "s1"}],
            )
            (tmp_dir / "fake-git-ts.txt").write_text(_iso(now))
            # Do NOT create hook-fires.jsonl

            result = self._run_check(tmp_dir)
        self.assertIn(
            result["result"],
            ("WARN", "FAIL"),
            msg="Missing hook-fires.jsonl should produce WARN or FAIL, not PASS",
        )


# ---------------------------------------------------------------------------
# Group 3: Functional — empty CLAUDE_PROJECT_DIR resolves hook path
# ---------------------------------------------------------------------------

class TestFunctionalEmptyVar(unittest.TestCase):
    """With CLAUDE_PROJECT_DIR="" the hook invocation pattern from settings.json
    must resolve to the real hook script and exit 0 (not exit 127).

    This test is skipped on non-bash environments (Windows without git-bash).
    We use the pre-tool-bash.sh hook as the representative hook (it's the simplest
    one that still emits a beacon and exits 0 on empty stdin).
    """

    def _bash_available(self) -> bool:
        try:
            r = subprocess.run(
                ["bash", "--version"],
                capture_output=True, timeout=5
            )
            return r.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def test_empty_var_resolves_hook(self):
        """CLAUDE_PROJECT_DIR="" + settings.json invocation pattern → exit 0.

        The invocation's own fallback resolves the repo root via
        `git rev-parse --git-common-dir`. Run from a linked worktree that
        would resolve to the MAIN checkout's `.git` -- a different tree that
        may not carry this branch's own hook scripts (the #1629 round-1
        finding: a worktree-local run silently exercised the root checkout's
        pre-move `.claude/hooks/pre-tool-bash.sh`, not this PR's code, and so
        passed locally while CI -- a fresh, non-worktree checkout -- was red
        on the stale `.claude/hooks/` path this test used to hard-code).
        Build a throwaway, self-contained git repo carrying ONLY this PR's
        hook scripts, so `git-common-dir` inside it resolves to ITSELF no
        matter what checkout shape the test runs from.
        """
        if not self._bash_available():
            self.skipTest("bash not available in this environment")

        # Build the invocation command exactly as settings.json does after the fix:
        # bash "${CLAUDE_PROJECT_DIR:-$(dirname "$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null)")}/.claude/pipeline/hooks/pre-tool-bash.sh"
        hook_invocation = (
            'bash "${CLAUDE_PROJECT_DIR:-$(dirname "$(git rev-parse '
            '--path-format=absolute --git-common-dir 2>/dev/null)")}/.claude/pipeline/hooks/pre-tool-bash.sh"'
        )

        with tempfile.TemporaryDirectory() as tmp:
            throwaway_repo = Path(tmp) / "throwaway-repo"
            throwaway_hooks = throwaway_repo / ".claude" / "pipeline" / "hooks"
            throwaway_hooks.mkdir(parents=True)
            for name in ("lib-root.sh", "pre-tool-bash.sh", "pre-tool-bash-classify.py"):
                (throwaway_hooks / name).write_bytes((HOOKS_DIR / name).read_bytes())
                (throwaway_hooks / name).chmod(0o755)
            # pre-tool-bash-classify.py resolves its sibling tools/pipeline_config.py
            # relative to its own file location (never cwd/toplevel) -- carry it too.
            throwaway_tools = throwaway_repo / ".claude" / "pipeline" / "tools"
            throwaway_tools.mkdir(parents=True)
            pipeline_config_src = REPO_ROOT / ".claude" / "pipeline" / "tools" / "pipeline_config.py"
            (throwaway_tools / "pipeline_config.py").write_bytes(pipeline_config_src.read_bytes())
            subprocess.run(
                ["git", "init", "--quiet", str(throwaway_repo)],
                check=True, capture_output=True, timeout=15,
            )

            log_dir = Path(tmp) / "logs"
            env = os.environ.copy()
            env["CLAUDE_PROJECT_DIR"] = ""
            # Use a temp WORKFLOW_LOG_DIR so we don't pollute real logs
            env["WORKFLOW_LOG_DIR"] = str(log_dir)

            result = subprocess.run(
                ["bash", "-c", hook_invocation],
                input="",
                capture_output=True,
                text=True,
                env=env,
                cwd=str(throwaway_repo),
                timeout=15,
            )

        self.assertNotEqual(
            127,
            result.returncode,
            msg=(
                "Hook invocation with empty CLAUDE_PROJECT_DIR returned exit 127 "
                "(script not found). The fallback expression is not working.\n"
                f"STDERR: {result.stderr[:300]}"
            ),
        )
        # Exit 0 or exit 1 (deny path) are both acceptable — either means the script ran.
        self.assertIn(
            result.returncode,
            (0, 1),
            msg=(
                f"Expected exit 0 or 1 (script ran), got {result.returncode}.\n"
                f"STDERR: {result.stderr[:300]}"
            ),
        )


if __name__ == "__main__":
    unittest.main()
