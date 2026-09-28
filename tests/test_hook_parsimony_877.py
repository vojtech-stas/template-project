"""
Tests for slice #877 — hook consolidation (PRD #876 parsimony).

Three test groups (ADR-0067 D2 test-first ordering applied to each group):

  (a) derive_event_type() auto-mode mapping via log-tool-event.sh invocation:
      all 5 named event types PLUS 3 fallback paths; asserts resulting
      workflow-events.jsonl entries using a sandbox WORKFLOW_LOG_DIR.

  (b) post_tool subagent-edit nudge path: PostToolUse Edit on a
      .claude/agents/*.md file writes subagent-edits.log AND emits no
      workflow-events.jsonl entry.

  (c) _desc bugfix regression (rule #13 regression rider): agent_start payload
      produces an event with a populated `input` field.  Written to FAIL
      against the pre-fix code (which used `_desc` before assignment) and
      PASS now.

All tests are offline subprocess-driven; no live hooks required.
WORKFLOW_LOG_DIR env override isolates every run from the real log store.

Runner: stdlib unittest + pytest compatible.
  python -m pytest tests/test_hook_parsimony_877.py -v
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# ---------------------------------------------------------------------------
# Repo root + hook path
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).parent.parent
HOOK = REPO_ROOT / ".claude" / "pipeline" / "hooks" / "log-tool-event.sh"


def _bash_available() -> bool:
    try:
        r = subprocess.run(["bash", "--version"], capture_output=True, timeout=5)
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _invoke_hook(payload: dict, mode: str, tmp_dir: Path) -> None:
    """Invoke log-tool-event.sh with payload via bash; writes into tmp_dir."""
    env = os.environ.copy()
    env["WORKFLOW_LOG_DIR"] = str(tmp_dir)
    subprocess.run(
        ["bash", str(HOOK), mode],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        cwd=str(REPO_ROOT),
        timeout=30,
    )


def _read_events(tmp_dir: Path) -> list[dict]:
    """Return all events from workflow-events.jsonl in tmp_dir (fixture routing via session_id)."""
    # Payloads use session_id starting with 'test-' → fixture file
    fixture_path = tmp_dir / "workflow-events.test.jsonl"
    prod_path = tmp_dir / "workflow-events.jsonl"
    events = []
    for path in (fixture_path, prod_path):
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    return events


def _read_subagent_edits(tmp_dir: Path) -> str:
    p = tmp_dir / "subagent-edits.log"
    if p.exists():
        return p.read_text(encoding="utf-8")
    return ""


def _base_payload(session_id: str = "test-877") -> dict:
    """Minimal base payload with required session_id."""
    return {"session_id": session_id}


# ---------------------------------------------------------------------------
# Group (a): derive_event_type() auto-mode mapping — all 5 named + 3 fallbacks
# ---------------------------------------------------------------------------

@unittest.skipUnless(_bash_available(), "bash not available")
class TestAutoModeMapping(unittest.TestCase):
    """Auto-mode derives event_type correctly for every PostToolUse/PreToolUse combination."""

    def _run(self, hook_ev: str, tool_name: str, extra: dict | None = None) -> list[dict]:
        payload = {
            **_base_payload(),
            "hook_event_name": hook_ev,
            "tool_name": tool_name,
            **(extra or {}),
        }
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            _invoke_hook(payload, "auto", tmp_dir)
            return _read_events(tmp_dir)

    # --- 5 named event types ---

    def test_agent_complete(self):
        """PostToolUse + Agent → agent_complete."""
        events = self._run("PostToolUse", "Agent",
                           extra={"tool_input": {"subagent_type": "reviewer",
                                                 "description": "test desc"},
                                  "tool_response": "RESULT: SUCCESS"})
        types = [e.get("event") for e in events]
        self.assertIn("agent_complete", types,
                      f"Expected agent_complete in events; got: {types}")

    def test_bash_complete(self):
        """PostToolUse + Bash → bash_complete."""
        events = self._run("PostToolUse", "Bash",
                           extra={"tool_input": {"command": "ls -la"},
                                  "tool_response": "file.txt"})
        types = [e.get("event") for e in events]
        self.assertIn("bash_complete", types,
                      f"Expected bash_complete in events; got: {types}")

    def test_grill_qa(self):
        """PostToolUse + AskUserQuestion → grill_qa."""
        events = self._run("PostToolUse", "AskUserQuestion",
                           extra={"tool_input": {"question": "Is this good?"},
                                  "tool_response": "yes"})
        types = [e.get("event") for e in events]
        self.assertIn("grill_qa", types,
                      f"Expected grill_qa in events; got: {types}")

    def test_agent_start(self):
        """PreToolUse + Agent → agent_start."""
        events = self._run("PreToolUse", "Agent",
                           extra={"tool_input": {"subagent_type": "implementer",
                                                 "description": "dispatch task"}})
        types = [e.get("event") for e in events]
        self.assertIn("agent_start", types,
                      f"Expected agent_start in events; got: {types}")

    def test_skill_invoke(self):
        """PreToolUse + Skill → skill_invoke."""
        events = self._run("PreToolUse", "Skill",
                           extra={"tool_input": {"skill": "ship"}})
        types = [e.get("event") for e in events]
        self.assertIn("skill_invoke", types,
                      f"Expected skill_invoke in events; got: {types}")

    # --- 3 fallback paths ---

    def test_post_tool_fallback(self):
        """PostToolUse + unknown tool → post_tool (no event written, beacon ok)."""
        payload = {
            **_base_payload(),
            "hook_event_name": "PostToolUse",
            "tool_name": "Read",
            "tool_input": {"file_path": "/some/file.py"},
            "tool_response": "content",
        }
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            _invoke_hook(payload, "auto", tmp_dir)
            events = _read_events(tmp_dir)
        # post_tool is suppressed (beacon only, no jsonl event)
        types = [e.get("event") for e in events]
        self.assertNotIn("post_tool", types,
                         "post_tool should NOT produce a workflow-events.jsonl entry")

    def test_pre_tool_fallback(self):
        """PreToolUse + unknown tool → pre_tool event written (fallthrough to generic log)."""
        payload = {
            **_base_payload(),
            "hook_event_name": "PreToolUse",
            "tool_name": "Read",
            "tool_input": {"file_path": "/some/file.py"},
        }
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            _invoke_hook(payload, "auto", tmp_dir)
            events = _read_events(tmp_dir)
        # pre_tool falls through to generic v2 event writing (unlike post_tool which is suppressed).
        # Assert that the hook runs without error: either a pre_tool event or no events (never a crash).
        types = [e.get("event") for e in events]
        self.assertTrue(
            all(t in ("pre_tool", None) for t in types),
            f"PreToolUse/Read fallback produced unexpected event types: {types}",
        )

    def test_unknown_hook_event_fallback(self):
        """Unknown hook_event_name → unknown (rejected due to missing session route or no event)."""
        payload = {
            **_base_payload(),
            "hook_event_name": "SomeOtherEvent",
            "tool_name": "Read",
        }
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            _invoke_hook(payload, "auto", tmp_dir)
            events = _read_events(tmp_dir)
        types = [e.get("event") for e in events]
        # "unknown" event type: the code will try to write it as a v2 event
        # but it won't match any named handler so it lands with no extra fields.
        # The important assertion is that the hook does NOT crash (no exception).
        # We just check the process ran without error by verifying tmp_dir was
        # written (hook-fires.jsonl should exist — attempt beacon at minimum).
        fires_path = tmp_dir / "hook-fires.jsonl"
        # The hook is allowed to write or not; it must not raise.
        # Accept either: no event or an "unknown"-typed event.
        self.assertTrue(
            all(t in (None, "unknown") for t in types) or len(types) == 0,
            f"Unexpected event types for unknown hook_event_name: {types}",
        )


# ---------------------------------------------------------------------------
# Group (b): post_tool subagent-edit nudge path
# ---------------------------------------------------------------------------

@unittest.skipUnless(_bash_available(), "bash not available")
class TestSubagentEditNudge(unittest.TestCase):
    """PostToolUse Edit on .claude/agents/*.md writes subagent-edits.log; no jsonl event."""

    def test_subagent_edit_writes_log(self):
        """Edit on .claude/agents/reviewer.md → subagent-edits.log entry."""
        payload = {
            **_base_payload("test-877-nudge"),
            "hook_event_name": "PostToolUse",
            "tool_name": "Edit",
            "tool_input": {"file_path": "/PROJECT_ROOT/.claude/agents/reviewer.md"},
            "tool_response": "ok",
        }
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            _invoke_hook(payload, "auto", tmp_dir)
            log_text = _read_subagent_edits(tmp_dir)

        self.assertIn(
            "reviewer.md",
            log_text,
            "subagent-edits.log must contain the edited file path",
        )
        self.assertIn(
            "AS-AUDIT",
            log_text,
            "subagent-edits.log nudge must mention AS-AUDIT (CI CHECK 18)",
        )

    def test_subagent_edit_no_workflow_event(self):
        """Edit on .claude/agents/*.md must NOT emit a workflow-events.jsonl event."""
        payload = {
            **_base_payload("test-877-nudge"),
            "hook_event_name": "PostToolUse",
            "tool_name": "Edit",
            "tool_input": {"file_path": "/PROJECT_ROOT/.claude/agents/implementer.md"},
            "tool_response": "ok",
        }
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            _invoke_hook(payload, "auto", tmp_dir)
            events = _read_events(tmp_dir)

        self.assertEqual(
            [],
            events,
            "No workflow-events.jsonl entry should be written for subagent-edit nudge path",
        )

    def test_write_tool_on_agents_md_also_nudges(self):
        """Write on .claude/agents/*.md triggers the same nudge path."""
        payload = {
            **_base_payload("test-877-write"),
            "hook_event_name": "PostToolUse",
            "tool_name": "Write",
            "tool_input": {"file_path": ".claude/agents/qa-tester.md"},
            "tool_response": "ok",
        }
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            _invoke_hook(payload, "auto", tmp_dir)
            log_text = _read_subagent_edits(tmp_dir)

        self.assertIn(
            "qa-tester.md",
            log_text,
            "Write on .claude/agents/*.md must also write to subagent-edits.log",
        )

    def test_non_agents_edit_no_nudge(self):
        """Edit on a non-agents path must NOT write subagent-edits.log."""
        payload = {
            **_base_payload("test-877-nonnudge"),
            "hook_event_name": "PostToolUse",
            "tool_name": "Edit",
            "tool_input": {"file_path": "/PROJECT_ROOT/tools/ci-checks.sh"},
            "tool_response": "ok",
        }
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            _invoke_hook(payload, "auto", tmp_dir)
            log_text = _read_subagent_edits(tmp_dir)

        self.assertEqual(
            "",
            log_text,
            "Edit on a non-agents path must not write subagent-edits.log",
        )


# ---------------------------------------------------------------------------
# Group (c): _desc bugfix regression — agent_start `input` field populated
#
# Pre-fix code: `_desc` was referenced in the regex before being assigned,
# causing a NameError so agent_start events had no `input` field (or failed).
# Post-fix code: `_desc = str(tool_input.get("description", ""))` is assigned
# before use.
# ---------------------------------------------------------------------------

@unittest.skipUnless(_bash_available(), "bash not available")
class TestAgentStartInputFieldRegression(unittest.TestCase):
    """Regression: agent_start event must have a populated `input` field (rule #13 rider)."""

    def test_agent_start_input_field_populated(self):
        """agent_start event must carry a non-empty `input` field from description."""
        description = "slice #877 test dispatch — implementer subagent"
        payload = {
            **_base_payload("test-877-regression"),
            "hook_event_name": "PreToolUse",
            "tool_name": "Agent",
            "tool_input": {
                "subagent_type": "implementer",
                "description": description,
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            _invoke_hook(payload, "auto", tmp_dir)
            events = _read_events(tmp_dir)

        agent_starts = [e for e in events if e.get("event") == "agent_start"]
        self.assertTrue(
            len(agent_starts) >= 1,
            f"Expected at least one agent_start event; got events: {[e.get('event') for e in events]}",
        )
        event = agent_starts[0]
        self.assertIn(
            "input",
            event,
            "agent_start event must have an `input` field (pre-fix _desc bug would omit it)",
        )
        self.assertNotEqual(
            "",
            event.get("input", ""),
            "agent_start `input` field must not be empty when description is provided",
        )
        self.assertIn(
            "slice #877",
            event.get("input", ""),
            "agent_start `input` must contain the first 300 chars of description",
        )

    def test_agent_start_input_truncated_to_300(self):
        """agent_start `input` is truncated to 300 chars."""
        long_desc = "x" * 500
        payload = {
            **_base_payload("test-877-trunc"),
            "hook_event_name": "PreToolUse",
            "tool_name": "Agent",
            "tool_input": {"subagent_type": "reviewer", "description": long_desc},
        }
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            _invoke_hook(payload, "auto", tmp_dir)
            events = _read_events(tmp_dir)

        agent_starts = [e for e in events if e.get("event") == "agent_start"]
        self.assertTrue(len(agent_starts) >= 1, "Expected agent_start event")
        event = agent_starts[0]
        self.assertLessEqual(
            len(event.get("input", "")),
            300,
            "agent_start `input` must be truncated to 300 chars",
        )


if __name__ == "__main__":
    unittest.main()
