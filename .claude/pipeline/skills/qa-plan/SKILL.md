---
name: qa-plan
description: Writer/orchestrator for QA automation per ADR-0020 + ADR-0040. Takes a PRD number (defaults to the most-recently-merged PRD), LLM-extracts each §2 acceptance criterion into a bash check or JUDGMENT flag, persists the plan as a PRD comment, dispatches qa-tester, collects PROVISIONAL residuals, posts each as a needs-human-check GitHub issue (writer posts, not qa-tester), reports the single top headline, and auto-closes the PRD on machine-PASS alone (ADR-0040 D2 — no longer waits on all-judgment-ACCEPT). Also the production-verify executor dispatched by /ship (step 6) per ADR-0037 D1.
---

# /qa-plan — writer/orchestrator for QA automation

This skill runs in **main-agent context** (so it can call `AskUserQuestion`); it does not modify code. It distills PRD §2 prose into a mechanical plan, hands execution to the [`qa-tester`](../../agents/qa-tester.md) subagent, collects any PROVISIONAL residuals from the executor, and queues them as async `needs-human-check` GitHub issues. Per [ADR-0020](../../../decisions/0020-qa-automation-writer-executor.md) D1 the writer/executor split mirrors slicer + slicer-critic (one plans, one executes). Per [ADR-0040](../../../decisions/0040-qa-human-residual-model.md) D1/D2/D3, the human checkpoint moves from synchronous plan-time `AskUserQuestion` to the async `needs-human-check` queue, which the operator clears on their own cadence (the dedicated clearing skill is retired per [ADR-0081](../../../decisions/0081-post-audit-dead-weight-retirements.md) D1) — the writer's job here is to queue residuals and surface ONE headline, not to render every judgment row synchronously.

**Role as production-verify executor (per [ADR-0037](../../../decisions/0037-production-verification-gate.md) D1):** `/ship` (step 6) dispatches this skill — or qa-tester directly in production-verify mode — as the mandatory production-verification gate after all slices merge. In that role, the input is the PRD body + "Production check:" line + merged diff summary, and the output is a PASS/FAIL proof consumed by the orchestrator for blocking enforcement. This role is additive to, not a replacement for, the PRD-acceptance QA role described above.

## When NOT to use this skill

- Mid-feature, while slices are still open — wait until all `Closes #<slice>` PRs for the PRD have merged.
- For PRDs whose §2 lacks acceptance criteria — push back; ask the user to `/grill-me` the missing criteria first.
- For Tier 2 (semantic) or Tier 3 (UI) QA — both deferred to future PRDs per ADR-0020 D6/D7.

## Process

1. **Read PRD §2.** `gh issue view <N> --json title,body`. Parse the **Goal / Success criteria** section. If the PRD is not in `closed` state with all slice PRs merged, halt with `RESULT: STOPPED` and ask the user to confirm.
2. **LLM-extract the structured plan.** For each numbered criterion in §2, infer either a runnable bash check (the criterion's own "Verifiable: ..." prose often gives it directly) OR mark `JUDGMENT` if subjective. Failed extractions become `EXTRACT_FAILED` per [ADR-0020](../../../decisions/0020-qa-automation-writer-executor.md) D2. Build a 3-column Markdown table: `criterion # | bash check or "JUDGMENT" | expected result`.
3. **Persist the plan as a PRD comment.** `gh issue comment <N> --body-file <tempfile>` containing the table under a `## QA-plan v1 (<YYYY-MM-DD>)` heading. Audit trail + re-runnability per [ADR-0020](../../../decisions/0020-qa-automation-writer-executor.md) D4. Capture the resulting comment URL as the trailer `ARTIFACTS:` value.
4. **Dispatch the qa-tester subagent.** Invoke via `Agent` tool with `subagent_type: "qa-tester"`, passing the plan table inline in the prompt. Receive back the per-criterion verdict table + canonical GENERATOR trailer (`PASS_COUNT`, `FAIL_COUNT`, `JUDGMENT_COUNT`, `EXTRACT_FAILED_COUNT`).
5. **Collect residuals, rank by highest-value, and queue as `needs-human-check` issues (ADR-0040 D2/D3).** Examine the qa-tester trailer for `PROVISIONAL` verdicts (residuals — criteria the machine could not faithfully verify). Apply the **highest-value ranking heuristic** (ADR-0040 D3 default): score each residual as `user-facing surface area × machine-uncertainty depth`:
   - **User-facing surface area** (primary axis): UI/visual behavior visible to the user scores highest; internal-state or plumbing checks score lower. Example: "sidebar renders correctly" > "internal store has expected keys".
   - **Machine-uncertainty depth** (secondary axis, tiebreak within the same surface area tier): if the executor described specific ambiguity (e.g., "snapshot shows element present but content unreadable") rank higher than a clean "couldn't reach" (e.g., eval-only proof, no real-click evidence). Higher uncertainty → higher priority for the human eye.
   - Document the score and ranking rationale for each residual in the run summary so the human can audit the selection.
   - For each residual (in ranked order): `gh issue create --label needs-human-check --title "QA residual: <criterion summary> (PRD #<N>)" --body "PRD: #<N>\n\nCriterion: <criterion text>\n\nWhat to eyeball: <executor's PROVISIONAL detail / what the machine could not settle>\n\nRanked #<position> of <total> residuals by highest-value heuristic (user-facing surface area × machine-uncertainty depth)."` — the **writer** posts these issues; `qa-tester` does not.
   - **Zero residuals** → no `needs-human-check` issue; clean machine-PASS (the human is not bothered).
   - **One or more residuals** → the top-ranked (highest-value) residual is the **"now check this" headline** reported prominently in the run summary. Extras are posted as `needs-human-check` issues but de-emphasized in the run summary (listed as secondary items, not the lead). **NEVER silently drop a residual** — every PROVISIONAL the executor returned must become a `needs-human-check` issue; the human culls them lazily like the `captured` tier, but none may be omitted.
   - JUDGMENT and EXTRACT_FAILED rows that have NO corresponding PROVISIONAL from the executor → still queue as needs-human-check if the executor flagged them as uncertain; else note as "machine could not classify" in the run summary.
6. **Decide PRD disposition (ADR-0040 D2 — machine-PASS closes; FAIL blocks).** Machine-PASS = every criterion is PASS or a queued residual (residuals are async, not blocking). Machine-FAIL = any criterion returned FAIL. Action:
   - **Machine-PASS (zero FAILs; residuals are OK):** `python tools/pipe/prd-close <N> --comment <qa-pass-summary>`. The verb refuses (non-zero, named reason) unless the canonical trace ledger already holds a `qa_verified` PASS span for PRD `<N>`; on that precondition it performs the `gh issue close --reason completed` side effect. PRD auto-closes immediately — does NOT wait for human resolution of `needs-human-check` issues (ADR-0040 D2 supersedes the ADR-0020 D5 all-judgment-ACCEPT wait).
   - **Machine-FAIL (any FAIL):** `AskUserQuestion` with options: (A) accept-FAIL-as-known / (B) reopen-for-fix / (C) cull-as-won't-fix; act on the user's choice (no auto-close on FAIL). The ADR-0037 D5 failure loop still applies — machine FAILs block.

## Tool boundaries

`Read`, `Bash` (gh CLI for issue view/comment/close + any pre-checks), `AskUserQuestion` (judgment-row + FAIL rendering — main-agent-only per ADR-0020 D3, which is why this skill cannot run as a subagent), `Agent` (dispatch qa-tester executor). Explicitly **NOT** `Write` / `Edit` — the writer never modifies tracked files; only the PRD issue comment + PRD state mutate. Plan persistence is via `gh issue comment`, not git.

## Output format for the calling agent

After step 6, emit the canonical **GENERATOR trailer** (per [ADR-0005](../../../decisions/0005-output-shape-and-slicing-methodology.md) D1c) as a fenced code block at the end of the terminal report:

```
RESULT: SUCCESS | STOPPED | INVALID_INPUT
REASON: <one sentence — e.g., "PRD #N auto-closed: 13 PASS + 0 residuals" or "halted: 2 mechanical FAILs">
ARTIFACTS: <URL of the persisted QA-plan comment>
PASS_COUNT: <integer>
FAIL_COUNT: <integer>
PROVISIONAL_COUNT: <integer — residuals queued as needs-human-check issues>
EXTRACT_FAILED_COUNT: <integer>
RESIDUAL_HEADLINE: <URL of the top-ranked needs-human-check issue, or "none">
PRD_DISPOSITION: closed-completed | reopened-for-fix | culled | left-open-pending-fix
```

`PASS_COUNT` / `FAIL_COUNT` / `EXTRACT_FAILED_COUNT` carry forward from the qa-tester subagent's trailer (per [`qa-tester.md`](../../agents/qa-tester.md) output shape). `PROVISIONAL_COUNT` is the count of residuals queued as `needs-human-check` issues. `RESIDUAL_HEADLINE` is the URL of the top-ranked issue (the "now check this" item) or `"none"` if zero residuals. `PRD_DISPOSITION` names the resulting PRD state so `/ship` and post-run audits can correlate without re-fetching.

## References

- Full role synthesis (writer/executor split rationale, edges): this file.
- [ADR-0020](../../../decisions/0020-qa-automation-writer-executor.md) — primary spec. D1 (writer/executor split), D2 (LLM-extract + EXTRACT_FAILED), D4 (plan persisted as PRD comment), D5 (auto-close on all-PASS + all-judgment-ACCEPT), D10 (refines ADR-0003 D4 terminal human checkpoint).
- [`.claude/agents/qa-tester.md`](../../agents/qa-tester.md) — executor subagent dispatched at step 4.
- [ADR-0005](../../../decisions/0005-output-shape-and-slicing-methodology.md) D1c — canonical GENERATOR trailer schema.
- [ADR-0003](../../../decisions/0003-autonomous-pipeline-with-critics.md) D4 — terminal human checkpoint (refined, not removed, per ADR-0020 D10).
- [ADR-0037](../../../decisions/0037-production-verification-gate.md) D1 — mandatory blocking gate; this skill is the production-verify executor dispatched by /ship step 6.
- [ADR-0040](../../../decisions/0040-qa-human-residual-model.md) — D1 (PROVISIONAL as residual signal), D2 (async non-blocking queue; PRD closes on machine-PASS alone — supersedes ADR-0020 D5 all-judgment-ACCEPT clause), D3 (one headline per feature; zero residuals → silent), D4 (writer queues residuals — D4 superseded by [ADR-0081](../../../decisions/0081-post-audit-dead-weight-retirements.md) D1: the dedicated clearing skill is retired, the operator clears the queue via the out-of-repo decide-flow or `gh issue list --label needs-human-check`).
- PRD [#166](https://github.com/vojtech-stas/project-claude/issues/166) — parent PRD; this skill's rewrite is slice [#168](https://github.com/vojtech-stas/project-claude/issues/168).
- PRD [#452](https://github.com/vojtech-stas/project-claude/issues/452) — production-verify gate PRD; this skill gains the production-verify executor role per slice #454.
- PRD [#474](https://github.com/vojtech-stas/project-claude/issues/474) — QA residual model PRD; this skill's step-5/6 rework ships in slice #475.
