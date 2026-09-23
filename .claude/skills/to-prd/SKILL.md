---
name: to-prd
description: Turn the current conversation context into a PRD and publish it to the project issue tracker. Use when user wants to create a PRD from the current context.
---

# /to-prd — PRD authoring with embedded critic loop

Synthesizes the current conversation (typically a recently-settled `/grill-me` session) into a PRD using the **6-section template** defined below, optionally drafts a macro-ADR alongside, runs `prd-critic` (+ `adr-critic` in parallel under a shared round counter when an ADR is drafted) in a ≤3-round APPROVE/BLOCK loop, and publishes via `gh issue create` only on joint-APPROVE. Synthesis only — does NOT interview the user (that's `/grill-me`).

Full role synthesis (joint-APPROVE gate rationale, shared-counter invariant, downstream consumers): this file. Pipeline context: CLAUDE.md §3 (Hierarchy + workflow conventions). Vocabulary: prd, adr, joint-approve-gate (see CLAUDE.md glossary).

## Process

1. **Explore the repo** for current state and domain vocabulary; respect existing ADRs in the area you're touching.

2. **Decide if a macro-ADR is warranted** per the [`decisions/README.md`](../../../decisions/README.md) heuristic — write one iff at least one is true: the decision was hard (real trade-offs surfaced); it constrains future work; a future maintainer would ask "why this way?" without it. If yes, draft the ADR markdown in parallel using `decisions/README.md` conventions (Status / Date / Context / Decisions / Consequences / Alternatives considered; optional Open-questions-deferred / Future-direction / References); number it `NNNN-<kebab-slug>.md` as the next unused integer. Both PRD and ADR(s) go to the critic(s) in the same round and ship together in slice 1 per [ADR-0003](../../../decisions/0003-autonomous-pipeline-with-critics.md) D8. If no → skip; trivial features don't get ADRs.

3. **Draft the PRD** using the 6-section template below.

4. **Invoke the critic(s)**, stating the round number explicitly (start at round 1):
   - **Always:** invoke `prd-critic` with the draft PRD (and any drafted ADRs) inline.
   - **When a macro-ADR was drafted:** ALSO invoke `adr-critic` in parallel — one invocation per drafted ADR. Per [ADR-0004](../../../decisions/0004-bypass-prevention.md) D1, `adr-critic`'s verdict gates ADR publication exactly the way `prd-critic`'s verdict gates PRD publication.

5. **Critic loop (≤3 rounds, joint-APPROVE gate per [ADR-0004](../../../decisions/0004-bypass-prevention.md) D1).** Both critics share a **single round number** (Option A — shared counter). If either returns BLOCK on round N, revise per each blocking critic's itemized findings (PRD findings → revise PRD; ADR findings → revise ADR), increment the shared round, re-invoke BOTH (even if one already APPROVED — the re-revision may have invalidated its prior verdict). Round-3 escalation triggers when EITHER critic BLOCKs on round 3. When revising, sweep the whole flagged defect class — not just the named instance — per CLAUDE.md rule #19.
   - On **joint APPROVE** (both APPROVE in the same round, OR `prd-critic` APPROVE with no ADR drafted) → step 6.
   - On **either-critic BLOCK with `ROUND == 3`** (or `ESCALATE: needs-human` in either verdict) → STOP. Do NOT post the PRD. Do NOT commit the ADR. Per I5 escalation, apply `needs-human` to the draft tracking artifact (or the posted PRD if already posted) and post a summary comment with both verdicts attached.

6. **Publish** (only after joint APPROVE):
   - PRD → `gh issue create --label prd`. Title format: `PRD: <one-line feature summary>`.
   - Any drafted ADR(s) → write to `decisions/NNNN-<slug>.md`. Ship in slice 1's PR per [ADR-0003](../../../decisions/0003-autonomous-pipeline-with-critics.md) D8 — NOT separately posted as issues.
   - Append the **pipeline metadata footer** to the PRD body: `> **Pipeline metadata** — Approved by prd-critic round <N>/3.` — extended to `; adr-critic round <N>/3 (ADR-NNNN)` when an ADR was drafted. Round numbers match by construction (shared counter).

## The 6-section PRD template (canonical definition)

This skill is the canonical home of the template; `CLAUDE.md` links here and does not restate it.

<prd-template>

## 1. Problem

Who is hurting, how, and why now. One paragraph. Concrete language; no "we should improve X" framings. Anchor in the grill-session insight that motivated the PRD.

## 2. Goal / Success criteria

The single observable outcome of shipping this PRD, plus a checklist of mechanically verifiable criteria. Each criterion must be checkable at merge (file exists with shape X, command Y produces output Z) — not by subjective judgment. End-to-end quality validation belongs in the QA-plan handoff, not these criteria.

**Criterion grammar (EARS-shaped, per ADR-0066 D1):** Number each criterion. Use one of two forms:

- **WHEN/SHALL form** (behavioral): begin with a `WHEN` or `WHERE` trigger context, then name exactly one observable behavior with `SHALL`. Example: `WHEN /api/health is called, the response SHALL contain a RESIDUAL-RATIO entry with result PASS or WARN.`
- **`Verifiable:` form** (non-behavioral): for doc-presence, parity, grep-count, or perf-budget assertions that have no natural trigger, use an explicit `Verifiable:` annotation naming the check command. Example: `Verifiable: grep -c 'PC-EARS' .claude/agents/prd-critic.md ≥ 1`

`prd-critic` BLOCKs on trigger-less criteria or multi-behavior criteria outside the `Verifiable:` hatch (PC-EARS rule, bind-forward per ADR-0004 D2). Both forms are equally valid; match the form to the criterion's nature.

**Production check:** <what to exercise in the live running context + expected result — e.g., "run `python3 dashboard/health.py --check RELEASE-READY`; assert first line matches `^(PASS|WARN): RELEASE-READY`". For non-runnable features: "N/A — docs-only, static: grep -c '<term>' <file> ≥ 1". Required; a missing or vague entry BLOCKs the PRD at prd-critic (PC-PRODUCTION-CHECK, ADR-0037 D4).>

## 3. Non-goals / Out of scope

A bulleted list of things deliberately NOT in this PRD, each with a one-line reason. Empty or TBD non-goals are not acceptable — a PRD without explicit non-goals will drift.

## 4. Appetite / Constraints

Slice budget (e.g., 5–7 slices), time appetite (e.g., 2–3 sessions), per-slice LoC cap, dependency stance (no new external deps unless justified), backward-compatibility commitments. Must be consistent with the Solution sketch in §5.

## 5. Solution sketch

Coarse module shape — what files get added/edited and the role each plays. Walking-skeleton-first: name what slice 1 must be (the thinnest end-to-end vertical), and call out any locked-in decisions that close earlier open questions. Do NOT enumerate the full slice list — the slicer subagent owns that.

## 6. Rabbit-holes & Open questions

Two sub-sections:
- **Rabbit-holes (don't chase)** — explicit traps the implementer must avoid. Pull from the grill session and referenced ADRs.
- **Open questions** — genuinely unresolved questions. Do not silently answer them; flag them for the slicer or implementer to surface again.

</prd-template>

## Rubric self-check (before invoking the critic[s])

Run a quick self-pass against the critics' rubrics to shorten the loop: [`prd-critic`](../../agents/prd-critic.md) (all PRDs) + [`adr-critic`](../../agents/adr-critic.md) (only when a macro-ADR was drafted in step 2). The critics enforce the same checks; this is just pre-emptive hygiene. Both rubrics live in the respective agent files.

## What this skill deliberately does NOT do

- It does NOT interview the user (that's `/grill-me`).
- It does NOT enumerate the slice list (that's `slicer` + `to-issues`).
- It does NOT enforce the PRD template via JSON schema or YAML frontmatter — `prd-critic` checks section presence by prompt (PRD #3 §6 rabbit-hole).
- It does NOT spawn a separate `/to-adr` skill — ADR drafting happens inline here (PRD #3 §5 and PRD #15 §3 locked-in decision).
- It does NOT post a PRD or commit an ADR until BOTH critics APPROVE in the same round; either-BLOCK loops under the shared round counter; round-3 BLOCK on either critic → I5 escalation; no silent publish.

## References

- Full role synthesis (invocation contract, edges): this file.
- [ADR-0003](../../../decisions/0003-autonomous-pipeline-with-critics.md) — D2 (critic loop pattern), D6 (skill vs subagent), D8 (ADR placement — macro-ADRs drafted alongside the PRD; this is what makes the dual-critic dance necessary).
- [ADR-0004](../../../decisions/0004-bypass-prevention.md) — D1 (adr-critic exists; mirror of prd-critic's contract); D2 (bootstrap-mode policy).
- [`.claude/agents/prd-critic.md`](../../agents/prd-critic.md), [`.claude/agents/adr-critic.md`](../../agents/adr-critic.md) — the critics this skill invokes.
- [`decisions/README.md`](../../../decisions/README.md) — ADR conventions and the "When to write an ADR" heuristic.
- [ADR-0066](../../../decisions/0066-upstream-spec-contract.md) D1 — EARS-shaped criteria grammar + `Verifiable:` escape hatch; the prd-critic PC-EARS rule the §2 template must satisfy.
- Sibling: [`.claude/skills/ship/SKILL.md`](../ship/SKILL.md) — orchestrator that chains this skill into the autonomous pipeline.
