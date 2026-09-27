---
id: "ADR-0091"
title: "Subagent context is read from the hook payload's agent_id"
status: "accepted"
date: "2026-09-23"
scope: "hooks"
rule_ids:
  - "HOK-010"
supersedes:
  - "ADR-0023 D3"
  - "ADR-0029 D3"
superseded_by: []
---

# 0091 — Subagent context is read from the hook payload's `agent_id`

**Status:** Accepted. Ships with the release-mode lane PR that fixes bug #1546 (ADR-0090 D3/D4).
**Date:** 2026-09-23

**Supersedes (per-decision, both PARTIAL):**
- **ADR-0023 D3.** Only step 1's detection clause falls: "if env var `CLAUDE_AGENT_TYPE` is set (subagent context per docs.claude.com)". Step 1's outcome stands: a subagent write is allowed with no emit. Steps 2 and 3 stand: the allowlist and the rule-#10 `"ask"`.
- **ADR-0029 D3.** Only the first bullet's detection clause falls: "if `CLAUDE_AGENT_TYPE` set (subagent context)". The skip itself stands, and so do the two soft-degrade bullets.

Both ADRs keep `superseded_by: []`, per the per-decision convention `tools/gen_rules.py` records for ADR-0083, ADR-0084, ADR-0089 and ADR-0090. Neither ADR file is edited.

**Extends:**
- **ADR-0076 D4, clause (2).** Clause (2) detects subagent context "by the same discriminator the ADR-0023 D3 meta-output hook already uses". That wording still holds, since both hooks now use D1's discriminator. Its referent changes from the environment variable to the payload field. No text of ADR-0076 D4 is superseded.
- **ADR-0057 D1.** The discriminator is read from the full-payload python3 parse that D1(c) already requires, and each hook keeps its one-attempt, one-terminal beacon shape.
- **ADR-0067 D2/D3.** The regression test was committed before the fix.
- **ADR-0004 D2 (bootstrap-mode).** HOK-010 binds forward from this ADR's merge.

## Context

**The defect (bug #1546).** Three hooks decided "am I running inside a subagent?" by reading the environment variable `CLAUDE_AGENT_TYPE`, at these sites on the integration branch at `eb6907d`:
- `.claude/hooks/pre-tool-bash-classify.py:228`. This feeds ADR-0076 D4 clause (2), the PIP-017 deny of `tools/promote.sh` from a subagent context.
- `.claude/hooks/pre-tool-edit.sh:97`. This is ADR-0023 D3 step 1, the subagent skip of the rule-#10 gate.
- `.claude/hooks/stop-reviewer-gate.sh:86`. This is ADR-0029 D3, the subagent skip of the stop gate.

Claude Code never sets that variable, so all three checks were dead code:
- The promote deny never fired, even for a literal `bash tools/promote.sh` issued by a subagent. That includes the 2026-09-23 incident (#1532/#1533).
- Every subagent Edit/Write took the main-thread `ask` path.
- The stop gate never skipped a subagent.

The tests hid the defect. They set or stripped the variable themselves, for example `tests/test_deny_guard_mechanical_1133.py`'s `extra_env={"CLAUDE_AGENT_TYPE": "implementer"}`. So they passed against a condition production never reaches.

**Why it happened.** ADR-0023 D3 took the variable from a reading of the sub-agents documentation and recorded the doubt as OQ-1: "`CLAUDE_AGENT_TYPE` env var reliability. Implementer verifies via dogfood". Its Consequences named the fallback: "always `"ask"`". Nobody closed OQ-1. `pre-tool-edit.sh:96` still carried the fallback comment ("if CLAUDE_AGENT_TYPE unreliable on dogfood, comment out this block"). ADR-0029 D3 and ADR-0076 D4 then reused the same unverified discriminator.

**OQ-1 resolved by observation, 2026-09-23.**
- A Haiku subagent ran `echo "CLAUDE_AGENT_TYPE=${CLAUDE_AGENT_TYPE:-<unset>}"` and printed `CLAUDE_AGENT_TYPE=<unset>` (bug #1546 body).
- The implementer subagent dispatched for this fix ran the same command and printed the same `CLAUDE_AGENT_TYPE=<unset>`. None of the `CLAUDE*` variable names in its environment names an agent type or an agent id.
- The Claude Code environment-variables reference (https://code.claude.com/docs/en/env-vars, fetched 2026-09-23) documents no agent-identifying variable. It has zero matches for `AGENT_TYPE`, `AGENT_ID` or `agent_id`. The nearest candidates are `CLAUDECODE` and `CLAUDE_CODE_CHILD_SESSION`. The reference documents both as set in every subprocess Claude Code spawns, so neither can separate a subagent from the main thread.

The variable does not exist. The Consequences' "always ask" fallback was therefore what every subagent got.

**The documented discriminator.** The hooks reference (https://code.claude.com/docs/en/hooks, "Common input fields", fetched 2026-09-23) documents two fields in every hook's stdin JSON:
- `agent_id`: "Present only when the hook fires inside a subagent call. Use this to distinguish subagent hook calls from main-thread calls."
- `agent_type`: this field is also present on the main thread of a session started with `--agent`. It therefore cannot tell a subagent from the main thread.

All three hooks already parse the full payload in python3 (ADR-0057 D1(c)). So the correct signal was one field away, inside data the hooks were already reading.

## Decisions

### D1 — A hook detects subagent context only from its stdin payload's `agent_id`

**The rule.** A hook is in subagent context exactly when its stdin payload carries a non-empty `agent_id`: present, not null, and non-empty after trimming. `agent_type` alone never marks a subagent.

**No environment variable.** No environment variable carries this signal. Claude Code sets none, and an undocumented variable that happens to differ between contexts is not adopted either, because an unverified environment discriminator is the exact assumption that failed here.

**Where hooks read it.** A hook reads `agent_id` from the same full-payload parse it already performs, and never from a `head -c` excerpt (HOK-008).

**Fail-safe.** A payload that cannot be parsed is main-thread context. It takes the hook's existing parser-failure path (the ERROR beacon, then fail-open).

**Where this binds.** It binds the three sites above:
- `pre-tool-bash-classify.py` passes `is_subagent(payload)` to `decide()`.
- `pre-tool-edit.sh`'s subagent skip moves after its python3 parse, because the signal now lives in the payload. Its EXIT trap still writes exactly one terminal beacon.
- `stop-reviewer-gate.sh` reads `agent_id` beside `stop_hook_active` and `session_id`, before any `gh` call.

Any future hook that treats a subagent differently uses the same rule.

**Tests.** A test of a subagent path feeds the hook a payload carrying `agent_id`, beside the same payload without it. It never fakes the signal through the environment.

**Enforcement (rule #23).** `tests/test_subagent_discriminator_1546.py` fires each of the three real hooks with and without `agent_id`, and asserts the deny, skip or gate outcome plus the HOK-008 beacon shape. It runs in CI through CHECK 12 (`pytest tests/`). No test under `tests/` and no hook under `.claude/hooks/` names the retired variable, so no test can pass on it. Rule id: **HOK-010**.

## Consequences

### Positive

- **The promote deny now fires as designed.** PIP-017's subagent-context `tools/promote.sh` deny fires for the direct forms the classifier recognizes (`bash tools/promote.sh`, `./tools/promote.sh`).
- **Implementer and reviewer writes skip the gate.** Their tracked-file writes skip the rule-#10 `ask` and the spec-gate, which is ADR-0023 D3's intent. The pipeline's own writes no longer depend on the permission mode to proceed.
- **The stop gate skips a subagent's Stop,** as ADR-0029 D3 intended.
- **This is a prerequisite for #1533 and #1542.** #1533(b) ("refuse in agent context by environment") cannot work as written. `promote.sh` cannot see the hook payload, so it needs its own safeguard. #1542(c)'s backstop needs the same working discriminator.

### Negative / Accepted

- **Unparseable payloads lose the skip.** A subagent Edit whose payload cannot be parsed now takes the ERROR fail-open terminal instead of the skip. The net effect is the same (the call proceeds), and the failure is now visible.
- **The hook still sees only command text.** Indirect invocation such as `python -c "subprocess.run(['bash','tools/promote.sh'])"` passes the classifier in any context. That remains #1533's problem. D1 fixes the discriminator, not the classifier's reach.
- **The field's shape is documented, not observed here.** The `agent_id` field shape comes from the platform documentation. The regression tests use a documented-shape payload, not a captured one. A platform change to the field would surface as the subagent paths going dead again, the same failure mode as before. It would not surface as a false deny on the main thread.

## Alternatives considered

- **Alt-A: Keep an environment discriminator and find a variable that differs.** Rejected. Observation found none that identifies an agent. Any variable that happened to differ would be undocumented, which repeats OQ-1's unverified assumption.
- **Alt-B: Treat `agent_type` as the marker.** Rejected. The main thread of an `--agent` session carries it, so the operator's own `tools/promote.sh` would be denied there, and their tracked-file writes would skip the rule-#10 gate.
- **Alt-C: Delete the three subagent branches, since they never fired.** Rejected. Each still encodes a decision its ADR made on purpose: the promote deny (#880), the pipeline's own writes (ADR-0023 D3), and the stop-loop guard (ADR-0029 D3). Only the detection was wrong.
- **Alt-D: Edit ADR-0023 D3 and ADR-0029 D3 in place.** Rejected by ADR immutability (`decisions/README.md`).

## Open questions deferred

- **Whether the project `Stop` hook fires inside a subagent at all.** The hooks reference says settings-file tool events (`PreToolUse`, `PostToolUse`) fire inside subagents. It says a `Stop` hook in a subagent's own frontmatter becomes `SubagentStop`, and a subagent's completion raises `SubagentStop`. It does not say whether a settings-file `Stop` hook fires inside one. The stop gate's skip is kept either way, as a guard. This ADR asserts nothing about which events fire where.

## References

- Bug #1546: symptom, root cause and the Haiku subagent's environment probe.
- https://code.claude.com/docs/en/hooks, "Common input fields": `agent_id` and `agent_type`.
- https://code.claude.com/docs/en/env-vars: documents no agent-identifying variable.
- ADR-0023 D3 and its OQ-1; ADR-0029 D3; ADR-0076 D4; ADR-0057 D1; ADR-0067 D2/D3; ADR-0090 D3/D4.
