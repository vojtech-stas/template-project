---
name: promote-to-backlog
description: Run the captured→backlog autopilot on a single `captured`-labeled GitHub issue. Invoked INLINE by whatever agent (subagent, skill, or main Claude) just wrote the capture via `gh issue create --label captured`, per ADR-0008 D3. Calls `backlog-critic`; on APPROVE swaps labels `captured` → `backlog` and posts the verdict as an audit-trail comment; on BLOCK posts the verdict and leaves the captured label in place.
---

This skill is the autopilot half of the captured→backlog mechanism (the critic half is [`backlog-critic`](../../agents/backlog-critic.md)). Per [ADR-0008](../../../decisions/0008-workflow-autolog-bootstrap-and-naming.md) D2 and D3, it runs **inline in the same agent context** that just created the captured-labeled issue — there is no daemon, no webhook, no GitHub Action.

**Shape note.** This skill is **non-interactive** and takes the issue number as its single argument. It performs a label swap (not a code change) so it does **not** open a PR. The agent that invoked it stays in control of the surrounding workflow; this skill just runs the autopilot beat.

## Invocation

```
/promote-to-backlog <issue-number>
```

Example: `/promote-to-backlog 73` after `gh issue create --label captured --label bug --title "..." --body "..."` returned issue #73.

**Class label at creation ([ADR-0090](../../../decisions/0090-release-mode.md) D1).** The agent writing the capture gives it exactly one class label in the same `gh issue create`: `--label bug` when the capture records something that breaks the system's own promise (docs, rules, ADRs and doc drift included), otherwise `--label feature`. This skill never adds, removes or swaps a class label; the label swap below leaves it in place. `tools/release.py freeze` refuses while any open non-residual issue lacks one.

If the issue number is missing, return `INVALID_INPUT: no issue number supplied` and stop.

## Process

1. **Verify the issue is in scope.** Run `gh issue view <N> --json number,title,labels,body,state`. If the issue does not exist, return `INVALID_INPUT: issue #<N> not found`. If it isn't labeled `captured`, return `INVALID_INPUT: issue #<N> is not labeled captured — this skill only operates on captured-tier items`. If the issue is closed, return `INVALID_INPUT: issue #<N> is closed`.

2. **Invoke the critic.** Call the [`backlog-critic`](../../agents/backlog-critic.md) subagent via the `Agent` tool with `subagent_type: "backlog-critic"`, passing the issue number. The critic reads the body, runs the rubric ([ADR-0008](../../../decisions/0008-workflow-autolog-bootstrap-and-naming.md) D4: actionable / scoped / not duplicate / clear), and returns a canonical verdict + CRITIC trailer per [ADR-0005](../../../decisions/0005-output-shape-and-slicing-methodology.md) D1.

   The critic fires **once** per item — no ≤3-round revision loop (per [ADR-0008](../../../decisions/0008-workflow-autolog-bootstrap-and-naming.md) D2 autopilot semantics). Do not re-prompt the critic. Do not "fix" the captured body on BLOCK and re-invoke.

3. **Act on the verdict.**

   - **On `VERDICT: APPROVE`** — swap the labels and post the verdict as a comment for the audit trail:

     ```bash
     gh issue edit <N> --remove-label captured --add-label backlog
     gh issue comment <N> --body "<full verdict markdown including the CRITIC trailer>"
     ```

     The issue is now in the curated backlog tier and will surface in `gh issue list --label backlog`.

   - **On `VERDICT: BLOCK`** — post the verdict as a comment, leave the `captured` label in place:

     ```bash
     gh issue comment <N> --body "<full verdict markdown including the CRITIC trailer>"
     ```

     Do NOT close the issue. Do NOT relabel. The user reviews the captured tier on whatever cadence they prefer; per-item options are (a) cull (close as won't-promote), (b) rescue (manually `gh issue edit --remove-label captured --add-label backlog`), or (c) restructure-and-recapture (close, write a sharper capture, let the autopilot re-evaluate).

4. **Return the GENERATOR trailer** per [ADR-0005](../../../decisions/0005-output-shape-and-slicing-methodology.md) D1c so the calling agent can parse the outcome:

   ```
   RESULT: SUCCESS | STOPPED | INVALID_INPUT
   REASON: <one sentence>
   ARTIFACTS: <issue URL>
   ISSUE: #<N>
   VERDICT: APPROVE | BLOCK | n/a
   PROMOTED: yes | no
   ```

   `RESULT: SUCCESS` covers both APPROVE-and-promoted and BLOCK-and-left-in-captured — the autopilot ran to completion in both cases. `RESULT: STOPPED` is reserved for unexpected runtime errors (e.g., `gh` auth failure, network error mid-swap). `RESULT: INVALID_INPUT` is reserved for the step-1 input checks.

## What this skill deliberately does NOT do

- It does NOT open a PR. The promotion is a label-swap operation on an existing issue, not a code change.
- It does NOT run a ≤3-round critic loop. Per [ADR-0008](../../../decisions/0008-workflow-autolog-bootstrap-and-naming.md) D2, the autopilot fires once per item; BLOCK leaves the item in the captured tier for lazy user review, it does not trigger a revise-and-retry cycle.
- It does NOT auto-cull or auto-close BLOCKed captures. The captured tier IS the safety net — the user reviews and decides per-item.
- It does NOT process captures that lack the `captured` label. If a future workflow wants to fire `backlog-critic` against backlog items or arbitrary issues, that's a different skill.
- It does NOT rewrite the captured body on BLOCK. The body is data the capturing agent already chose to write; revision belongs in a future fresh capture, not in this skill.
- It does NOT apply the `needs-human` label. In autopilot mode there is no I5-style escalation; the user-rescue path *is* the escalation surface (per [ADR-0008](../../../decisions/0008-workflow-autolog-bootstrap-and-naming.md) D2).

## References

- Full role synthesis (single-fire rationale, edges): this file.
- [ADR-0008](../../../decisions/0008-workflow-autolog-bootstrap-and-naming.md) — D1 (two-tier architecture); D2 (autopilot semantics this skill implements); D3 (inline-firing convention); D4 (rubric the critic enforces); D8 (bootstrap-mode acknowledgment).
- [`.claude/agents/backlog-critic.md`](../../agents/backlog-critic.md) — the critic this skill invokes.
