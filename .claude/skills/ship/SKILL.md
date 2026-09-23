---
name: ship
description: Run the full autonomous lifecycle - for one feature, or for the whole open queue. Assess how concrete the input is and grill first when it is still vague, then PRD authoring, slice decomposition, per-slice implementation, auto-merge, and the production-verify gate. Use when the user says "ship it", "/ship", "turn this into a PRD and slices", or otherwise asks to hand a feature idea off to the autonomous pipeline; pre-grilled input is welcome but not required. Also use when the user says "drain the queue", "/ship drain", "deploy all open PRDs", "drain plan", "drain N items", or otherwise asks to work the whole open backlog rather than one feature - that enters the queue-drain entry mode, which assembles and triages the open queue, escalates operator-owed items by label-and-continue, and records the run in a resumable ledger.
---

# /ship — autonomous pipeline orchestrator

Chains `conditional /grill-me → /to-prd → prd-critic (+ adr-critic) → /to-issues → slicer → slicer-critic → gh issue create → implementer (DAG-aware parallel) → reviewer (auto-merge) → regenerate-docs → qa-tester (production-verify)` so the human only needs one command per feature: `/ship` settles the *what* itself — grilling first when the input is still vague (step 1) — then drives it through PRD authoring, slice decomposition, per-slice implementation, auto-merge, doc regeneration, and the production-verify gate.

Full role synthesis (chain rationale, forward-block semantics, terminal-state collection): this file. Stage-by-stage operational logic (what each hook does, hook contract, "what the pipeline deliberately does NOT do"): CLAUDE.md §3 (Hierarchy + workflow conventions). Vocabulary: prd, slice, joint-approve-gate, walking-skeleton (see CLAUDE.md glossary).

## When NOT to use this skill

- For trivial one-line fixes — use the `hotfix/<thing>` lane (I3).
- When the user wants to interrogate a design without committing to build it — invoke `/grill-me` on its own; it remains individually invocable ([ADR-0034](../../../decisions/0034-build-orchestrator-and-generated-docs.md) D2), whereas `/ship` carries whatever its own step-1 grill settles straight on into PRD authoring and implementation.

## Conduct

**Run-to-done.** When multiple goals or PRDs are queued — by the run's own decomposition (a multi-slice PRD), or by the user naming several features in one invocation — execute them consecutively to completion (merged + production-verified + closed) without pausing between goals. One wrap-up report at the end covers all goals. A request to work the *whole open queue* ("ship everything in the backlog", "drain the queue") is no longer a loose reading of this paragraph: it is a distinct entry mode with its own assembly, triage, escalation and ledger obligations — see **Queue-drain entry mode** below, which owns that case entirely. Run-to-done is the semantics both modes share; the drain adds what run-to-done alone never specified (which items are the run's, which need the operator, and how the run survives being killed). Stops mid-run are reserved exclusively for:
- Destructive or irreversible operations (branch deletion, ref rewrites, force-push) — confirm with the user before proceeding.
- Genuine user-only scope forks — when a design decision cannot be resolved from the grilled context and a wrong choice would require rework; name the specific fork and stop only for that decision.
- Round-3 strict-stops (rule #19 / I5) — a `needs-human` escalation after three BLOCK rounds; this overrides the run-to-done drive unconditionally.

**Reroute-when-blocked.** On a blocked path — an unregistered agent type, an unavailable tool, or stray environment state — the orchestrator reroutes before stopping:
1. **Substitute**: if a registered subagent type is unavailable, dispatch `general-purpose` with the role file loaded inline (per the `qa-tester` precedent for unregistered environments).
2. **Repair**: fix environment state (kill stray servers, run `bash tools/worktree-guard.sh branch-restore` to restore a drifted worktree, clean stale lock files) and retry.
3. **Re-decompose**: if the blocked path is a design dead-end, re-run the slicer on the affected PRD with updated context.

In all three cases, capture the root cause per rule #13 (symptom + root cause + proposed workflow change as a `captured`-labeled issue) before continuing. Stopping without rerouting is a last resort, not a first response.

## Queue-drain entry mode

Per [ADR-0085](../../../decisions/0085-queue-drain-mode.md). This is an **entry mode on `/ship`**, not a second orchestrator ([ADR-0081](../../../decisions/0081-post-audit-dead-weight-retirements.md) D4): every stage below is the ordinary pipeline, driven over a whole queue instead of one feature. Plain `/ship <feature>` is unaffected when no trigger is present.

### QD1. Trigger detection and sub-forms

Enter this mode on the normative trigger set — **"drain the queue"**, **"/ship drain"**, **"deploy all open PRDs"** — or an unambiguous paraphrase of them. Two bounded sub-forms:

- **Plan-only** ("drain plan") — assemble, triage, build lanes, write the ledger, then **stop**. Zero dispatches, zero GitHub mutations: no labels applied, no issues created, no comments posted, no PRs touched. The cheap real-data inspection surface; its zero-mutation property is independently witnessed by `trace-v3.jsonl` gaining no new `dispatch` spans across the run window.
- **Bounded** ("drain N item(s)") — the full lifecycle, limited to the first N queue items in lane order. The smoke-test form; `N=1` is the walking-skeleton vehicle.

Absent either qualifier, the drain is unbounded and runs to queue exhaustion or a park.

### QD2. Queue assembly

Re-count the queue **live at every run start** — never carry a figure over from a previous run or from a document. Assemble from open `prd`-, `slice`-, `backlog`- and `captured`-labeled issues plus open **non-draft** PRs. Write the `run_start` record with the resulting snapshot before anything else happens; that record is what makes the run's own claims about its queue checkable afterwards.

### QD3. Triage — three buckets, one litmus

Classify every queue item by the grill litmus: *would a reasonable senior engineer need to ask the owner before doing this?*

| Bucket | Meaning | Action |
|---|---|---|
| **autonomous** | the default — the answer is no | proceeds through the pipeline unattended |
| **operator-owed** | a genuine design fork, or a destructive/irreversible call | apply `needs-human-check`, record it, **keep going** |
| **class-ack** | a coherent bulk-closure class (e.g. obsolete-by-supersession captures) | ONE class-level escalation on the same surface — never per-item asks, never a unilateral class closure |

Each item gets one `triaged` record naming its bucket and carrying its **lane** as a field. There is no `lane` record kind — the kind set is closed.

Escalations are titled with a **`Drain triage:`** prefix so the `needs-human-check` queue, which also holds QA residuals ([ADR-0040](../../../decisions/0040-qa-human-residual-model.md) D1/D2), stays sortable for the operator.

### QD4. Escalation is label-and-continue, on a two-label split

- **At triage** → `needs-human-check`. Deliberately NOT `needs-human`: [ADR-0070](../../../decisions/0070-two-tier-autonomous-delivery.md) D2 condition (e) makes RELEASE-READY require zero open `needs-human` items, so parking ordinary design forks there would freeze `main` promotion for the whole drain.
- **At a round-3 critic BLOCK** → `needs-human` **and** a summary comment on the item's parent context (the parent PRD issue, or the item itself when parentless). This is the I5 strict-stop ([ADR-0048](../../../decisions/0048-critic-loop-r3-policy-and-complete-class-revision.md) D1); it holds the promotion gate on purpose, and it stops **that item only** — unrelated lanes continue.

Both write an `escalated` record carrying `label` and `label_applied: true` **at the moment the label is applied** — that recorded evidence, not a later GitHub query, is what the DRAIN-LEDGER row checks. **The run never blocks on a human answer**, so the mode holds with or without a live operator.

### QD5. Per-item routing

- **prd** → `/to-issues` when it has **no open slices** (rule #16 — slices are minted only there). When it already has open slices, *those slices* are the queue items and the PRD is represented by them; it is never enqueued a second time alongside them, or the same work would be routed twice. It still counts in `run_start.counts.prd`, which snapshots the open queue rather than the items in flight (QD2).
- **slice** → implement it through the existing stage 4/5 (implementer → reviewer → merge) exactly as a normal run does.
- **backlog** → the standard PRD pipeline, one feature-sized deliverable per PRD (PIP-002). Related items combine into one PRD only when they are genuinely fragments of one feature; bundling unrelated items is a smell `prd-critic` gates. Slices are minted **only** through `/to-issues` (rule #16).
- **captured** → the captured→backlog autopilot FIRST ([ADR-0008](../../../decisions/0008-workflow-autolog-bootstrap-and-naming.md) D2/D3): run `backlog-critic` on it; APPROVE → it proceeds as backlog work; **BLOCK → it stays captured and undrained**, at most folded into a class-ack. Never implement a BLOCKed capture.
- **open PR** → carry it to a terminal state through the guarded verbs.

### QD6. Lanes and concurrency

Predict file overlap coarsely from the paths named in issue bodies and titles. Overlapping items **serialize into one lane**; independent lanes run in parallel in isolated worktrees (I4a / [ADR-0036](../../../decisions/0036-worktree-isolation-all-dispatches.md) D1). **Unknown overlap serializes** — a wrong independence guess costs a merge conflict; a wrong serialization guess costs only time. **At most 3 items are in flight concurrently** (open `item_start` records minus their `item_done`); the DRAIN-LEDGER row FAILs a ledger that exceeds it. Merges stay serialized through the PR gate ([ADR-0062](../../../decisions/0062-merge-integrity-green-main.md) D2). Pace GitHub mutations across the run.

### QD7. Fix-in-run

A discovery made mid-run whose remedy fits the trivial lane is **appended to this run's queue**, not filed and forgotten. This protocol changes only **where** a trivial fix lands, never **what** qualifies as one: I3's definition is untouched.

**1 — Qualify.** Apply I3's own test to the **remedy**, not to the discovery: a two-line grep fix found while reading a 200-line defect still qualifies. Uncertain → it does not qualify. Non-qualifying discoveries capture as before (rule #11), and a workflow mistake owes its root-cause capture (rule #13) whether or not a code remedy lands here — fix-in-run covers the remedy, never the capture obligation.

**2 — Record at discovery**, before starting the work: append `fix_queued`. A fix discovered but unrecorded is exactly the loss this protocol exists to prevent. Its `item` is the fix's own handle for the whole run — there is no issue number yet and may never be one — and **every later record about that fix repeats that handle verbatim**: the DRAIN-LEDGER parity assertion matches on it, so a renamed handle reads as an unlanded fix. Form the handle as `fix:<kebab-slug>` `(advisory — the row matches handles, it does not parse their shape; a colliding or unreadable handle in a real run is the evidence trigger to mechanize the form)`.

**3 — Land it in-run.** One fix per PR: branch `hotfix/<short-summary>` from `develop`, label it `trivial`, open with `tools/pipe/pr-open`, and merge with `tools/pipe/pr-merge` — the reviewer's trivial fast-path is still the gate (the verb refuses a PR with no `VERDICT: APPROVE` comment), and merges stay serialized with the rest of the run (QD6). When the discovery is a **code defect**, the regression rider is unchanged: a test that fails before the fix and passes after, committed before it ([ADR-0067](../../../decisions/0067-regression-memory.md) D2/D3). On merge, append `fixed_in_run` with the handle and the PR number.

Fix work emits **no `item_start`/`item_done`** — a fix is not a queue item and has no `triaged` record, so an `item_start` for one is a DRAIN-LEDGER FAIL, and it does not consume a concurrency slot.

**4 — Or defer it, visibly.** If the run reaches its terminal record with the fix unlanded, capture it **then** as a `captured`-labeled issue and append a **second** `fix_queued` record carrying the same handle plus `captured_ref` (e.g. `"issue:1400"`) — the ledger is append-only, so a second record is how a field arrives late. Both steps happen **before** `run_end`; a fix that reaches the terminal with neither `fixed_in_run` nor `captured_ref` FAILs DRAIN-LEDGER, which is the point: no silent loss. On a park it is instead named in the `parked` record's remaining list (QD9).

### QD8. Ledger write protocol

Append one JSON object per line to `.claude/logs/drain/<run-id>.jsonl`, resolved against the **git-common-dir root** so every worktree shares one ledger ([ADR-0075](../../../decisions/0075-trace-core-fork-decisions.md) D2). The kind set is **closed** — adding one requires a superseding ADR:

| Kind | Required fields beyond `kind` | Written when |
|---|---|---|
| `run_start` | `counts` (`prd`/`slice`/`backlog`/`captured`), `open_prs` | first, before any other action |
| `triaged` | `item`, `bucket`, `lane` | once per queue item |
| `item_start` / `item_done` | `item` | item enters / leaves flight |
| `escalated` | `item`, `label`, `label_applied` | at the moment the label is applied |
| `fix_queued` | `item` (+ `captured_ref` when deferred) | trivial-lane discovery queued |
| `fixed_in_run` | `item`, `pr` | its hotfix PR merges |
| `parked` | `remaining` (non-empty, in resume order) | quota cut or operator stop |
| `resumed` | — | a later session picks the run up |
| `run_end` | — | queue exhausted or bound reached |

Every `item_start` MUST follow a `triaged` record for that item. This ledger is **not** new trace-v3 kinds: `tools/trace.py`'s enum (PIP-015) and the Run-board contract (PIP-022) are untouched, which is what leaves trace-v3 free to act as the plan-only form's independent dispatch witness.

**Park/resume** is a protocol of its own — QD9 owns the trigger, the `parked` record's composition, and how a later run picks the file up.

**Self-check.** After `run_end` (or `parked`), run `python dashboard/health.py --check DRAIN-LEDGER`; it validates the newest ledger offline and prints `PASS: DRAIN-LEDGER — …`. Report its verdict in the wrap-up.

### QD9. Park and resume

A drain run that cannot finish must **stop recorded, not stop silently** ([ADR-0085](../../../decisions/0085-queue-drain-mode.md) D4). The ledger is the run's durable state across session death and quota cuts; the park record is what makes a killed run resumable instead of a queue the next session re-derives from memory.

**1 — When to park.** Park on an operator stop, or when the run judges a quota cut close enough that starting another item would strand it half-done `(advisory — no quota-remaining API exists, so the trigger is orchestrator judgment; the checkable obligation is the parked record and its shape, never the detection heuristic. Evidence trigger: a run that dies mid-item leaving no parked record.)`

**2 — Write `parked` before stopping.** It is the run's last action, not the next run's plan: write it while the session can still write. Its `remaining` list names, in resume order:

- every queue item the run still owes work — `triaged`, not yet `item_done`, and not escalated past (an operator-owed item was labeled and left behind by design, so it is not remaining work). Items in flight belong on the list; their work restarts from live state, not from wherever it was interrupted.
- every `fix_queued` handle (QD7) with no `fixed_in_run` and no `captured_ref`. **A park is the fix-in-run protocol's second terminal**: a queued fix that is neither landed, captured, nor named here is exactly the silent loss QD7 exists to prevent, and DRAIN-LEDGER FAILs it at `parked` precisely as it does at `run_end`.

An empty `remaining` is a DRAIN-LEDGER FAIL: a park with nothing left to do is a `run_end`, and the two terminals are not interchangeable. That the list is *complete* and its order sensible stays judgment `(advisory — the row checks that a park names something and that no unlanded fix escapes, both offline; it cannot know what the queue still held. Evidence trigger: a resumed run discovering an item the park should have named.)`

**3 — Resuming.** A later run resumes the most recent ledger whose terminal record is `parked` — a ledger ending in `run_end` is finished, and one with no terminal belongs to a run that died without parking, whose items return through ordinary queue assembly (QD2) like any other open issue. Append `resumed`, then work `remaining` in the recorded order. Resume does **not** open a new ledger; the run continues in its own file, which is why `resumed` is a kind at all. A resumed run that parks again writes a fresh `parked` record with the shortened list, under these same rules.

The ledger supplies **intent, never truth**. It says which items this run had and in what order; that is the whole of what it is trusted for. Before touching any listed item, re-derive its live state (`gh issue view`, `gh pr view`, `git log`) — labels move, PRs merge and issues close while a run is down, and live state remains the source of truth (CAP-002 / [ADR-0006](../../../decisions/0006-backlog-and-session-continuity.md) D2, which the ledger supplements rather than replaces). An item the list names but live state shows already done is dropped, not re-run `(advisory — reconciling list against live state is judgment; evidence trigger: a resumed run redoing work that had already landed.)` **A dropped item is closed with `item_done` before the run proceeds** — dropping takes it out of `remaining`, `item_done` takes it out of the *flight set*, and an `item_start` frozen open by the park would otherwise hold one of QD6's three concurrency slots for the rest of the run and FAIL DRAIN-LEDGER on a run that never had more than three items in flight.

### QD10. Invariants the drain never overrides

No triage verdict relaxes any of these: the drain **never** creates `.claude/PROMOTE_OK` ([ADR-0070](../../../decisions/0070-two-tier-autonomous-delivery.md) D4 — guardrail promotions wait for the human); a round-3 BLOCK strict-stops that item; destructive or irreversible operations confirm with the operator first; every dispatch passes `isolation: "worktree"` and a result without `worktreePath` is a dispatch failure ([ADR-0058](../../../decisions/0058-worktree-isolation-as-asserted-interface.md) D1/D2); slice issues are created only through the slicer flow.

## Whole-repo macro audit — session-scoped background spawn (ADR-0051 D1–D4)

Before the implementation pipeline begins, fire a once-per-session whole-repo audit via the existing `codebase-critic` in whole-repo mode. This runs **concurrently** and **never gates** any `/ship` stage — it is a background reflection tool only (ADR-0051 D3).

### 0a. Once-per-session guard

**Session ID source:** `$CLAUDE_CODE_SESSION_ID` env var (available in every Claude Code session — the simplest reliable mechanism). Note: ADR-0051 D4 originally sketched keying the marker on the workflow-event-log session ID (per [ADR-0016](../../../decisions/0016-workflow-event-log-jsonl.md)); the env var is the implemented simplification (both are valid; PR #572 added `session_id` to the event log). If the env var is absent or empty, fall back to the date string `$(date +%Y-%m-%d)` as a coarser once-per-day guard.

1. Derive the marker path: `MARKER="$CLAUDE_PROJECT_DIR/.claude/logs/.macro-audit-${CLAUDE_CODE_SESSION_ID:-$(date +%Y-%m-%d)}"`.
2. Check: `if [ -f "$MARKER" ]; then` → log "whole-repo audit skipped: already ran this session" and skip to step 0b. **Do NOT dispatch again.**
3. If absent: write the marker (`touch "$MARKER"` or equivalent) then proceed to step 0b.

Note: `.claude/logs/` is already directory-level gitignored (per ADR-0015 D4 / ADR-0016 D4) — do NOT add a redundant `.gitignore` pattern for the marker file.

### 0b. Background spawn

If the guard passed (marker was absent and is now written):

Dispatch `codebase-critic` via the `Agent` tool with **`run_in_background: true`**. Pass exactly:
```
WHOLE_REPO: true
```
No `BASE_REF`, `HEAD_REF`, or PRD number — whole-repo mode requires none (per `.claude/agents/codebase-critic.md` WHOLE-REPO MODE contract).

The dispatch returns immediately to the main agent. **Continue to step 1 without waiting for the background run to complete.** The `/ship` pipeline stages (1–7) proceed normally in parallel.

### 0c. Harvest-on-completion

When the background `codebase-critic` run completes, the main agent receives a completion notification. At that point — which may arrive mid-pipeline or after step 7:

1. **Parse the GENERATOR trailer** from the codebase-critic output. Extract `FINDINGS_COUNT` and the `ARTIFACTS` field (comma-separated finding titles).
2. **If `FINDINGS_COUNT` is 0:** log "whole-repo audit: 0 findings — no issues to capture" and proceed.
3. **If `FINDINGS_COUNT` ≥ 1:** for each finding (a `[WR-<CLASS>] <title>` block from the output):
   - File a `captured`-labeled GitHub issue:
     - **Title:** `"[whole-repo audit] <short title from finding>"` — ≤70 chars.
     - **Body (rule #11/#13 shape):**
       ```
       ## Symptom
       <one sentence: what the cross-subsystem drift or duplication looks like>

       ## Root cause
       <one sentence: why it exists — typically "cross-PRD drift not caught by single-PRD diff review">

       ## Proposed
       <the finding's Description field — the concrete proposed fix or refactoring>

       Affected: <comma-separated file paths from the finding>
       Source: whole-repo audit (codebase-critic WHOLE_REPO mode, ADR-0051 D3)
       ```
   - Run `/promote-to-backlog <N>` (per ADR-0008 D3 / rule #11) to triage the issue into the backlog autopilot.
4. **Surface a one-line summary** in the `/ship` final report (step 7): `"whole-repo audit: <N> findings captured (#<issue-numbers, comma-separated>)"`. If the background run had not yet completed by step 7, note `"whole-repo audit: in-flight (harvest pending)"` — the harvest runs when the notification arrives, even post-report.
5. **If the background run errors or times out:** log the failure as a `captured`-labeled GitHub issue (title: `"whole-repo audit background run failed"`, body: symptom + error excerpt) and continue — the `/ship` pipeline is unaffected (ADR-0051 D3).

---

## Step-by-step procedure

### Step 0 — Live-feed self-check (capture honesty gate)

Before the pipeline begins, verify the capture layer is alive for this session.
Read the tail of `workflow-events.jsonl` (last 200 lines) and count events whose
`session_id` matches `$CLAUDE_CODE_SESSION_ID` and whose `ts` is >= the current
session-start time. Use python3 (rule #12-compatible — reads a log file, does not
invoke hooks; per PRD #668 §2 orchestrator-level honesty):

```bash
python3 - <<'PY'
import os
log = os.environ.get("CLAUDE_PROJECT_DIR","") + "/.claude/logs/workflow-events.jsonl"
sid = os.environ.get("CLAUDE_CODE_SESSION_ID","")
try:
    lines = open(log, encoding="utf-8", errors="replace").readlines()[-200:]
    fresh = sum(1 for l in lines if sid and sid in l)
    print(fresh)
except Exception:
    print(0)
PY
```

**If count == 0:** state verbatim: "capture is dead (resumed-session class)" and
stamp this run `capture=dead`. Downstream verification CANNOT claim live hook-fire
evidence for this run; note it explicitly in the step 7 final report.

**If count > 0:** log "capture alive: N fresh events" and proceed.

1. **Confirm grilled context.** Scan history for a settled design (typically a recent `/grill-me` session). If context is thin, design with sensible defaults and record every defaulted decision in the PRD draft — `prd-critic` and `adr-critic` are the safety net that audits those decisions before the PRD posts. Proceed without stopping unless a fork is genuinely user-only (i.e. a design choice where a wrong guess would require rework that cannot be corrected by later slices — name the specific fork and stop only for that decision).

   **Assess + grill (conditional, ADR-0034 D3 — rehosted from `/build` step 2 per [ADR-0081](../../../decisions/0081-post-audit-dead-weight-retirements.md) D4).**

   Auto-assess the input concreteness. Ask: **"Is there enough here to write a mechanically-verifiable PRD §2 without more questions?"**

   Concreteness signals (any combination suffices):
   - A GitHub issue number with a symptom + root-cause + proposed-fix body
   - A structured spec with acceptance criteria
   - A grilled conversation with all major design questions resolved
   - A CLAUDE.md slice body or equivalent with "What ships" and "Acceptance criteria"

   **If vague** (open idea, vague noun-phrase, no acceptance criteria visible): announce `"Input is vague — invoking /grill-me"` then invoke [`.claude/skills/grill-me/SKILL.md`](../grill-me/SKILL.md). Wait for the grill to complete. If the user stops the grill early without a settled design, STOP with `RESULT: STOPPED, REASON: grill incomplete — re-run /ship when design is settled`.

   **If concrete**: announce `"Input is concrete — skipping grill, proceeding to step 2"` and proceed immediately to step 2. **Do NOT ask a blocking "grill or skip?" confirmation question** (ADR-0034 D3 / 5A).

2. **Stage 2 — `/to-prd`.** Invoke [`.claude/skills/to-prd/SKILL.md`](../to-prd/SKILL.md) unchanged. It runs `prd-critic` (+ `adr-critic` under shared round counter when a macro-ADR is drafted) internally per [ADR-0004](../../../decisions/0004-bypass-prevention.md) D1's joint-APPROVE gate, then publishes via `gh issue create`. Capture the PRD issue number.

3. **Stage 2.5 verification.** Verify `to-prd` reported APPROVE — the posted PRD body should end with `> **Pipeline metadata** — Approved by prd-critic round <N>/3.` (extended with `; adr-critic round <N>/3 (ADR-NNNN)` when an ADR was drafted). On round-3 BLOCK or `ESCALATE: needs-human` from either critic, STOP — do NOT proceed to stage 3. Surface findings and recommend re-grilling. Macro-ADRs ship as files in slice 1's PR per [ADR-0003](../../../decisions/0003-autonomous-pipeline-with-critics.md) D8, not as separate issues.

4. **Stage 3 — `/to-issues`.** Invoke [`.claude/skills/to-issues/SKILL.md`](../to-issues/SKILL.md) unchanged with the PRD issue number from step 2. Internally it invokes `slicer` (one well-justified decomposition per [ADR-0044](../../../decisions/0044-slicer-simplification-single-decomposition.md) D1) and `slicer-critic` (standard APPROVE/BLOCK + ≤3-round iterate per ADR-0044 D2). On BLOCK, surface and STOP — do NOT post slices. On APPROVE, capture the slice issue numbers in dependency order.

5. **Stage 4 — implementer + reviewer (DAG-aware parallel batches).** Per [ADR-0010](../../../decisions/0010-implementer-subagent-auto-pipeline.md) D2/D3/D4:

   **BLIND-REVIEW dispatch template (ADR-0060 D1 — applies to ALL critic dispatches in this stage):**
   Every dispatch to a critic (`reviewer`, `codebase-critic`, `slicer-critic`, `prd-critic`, `adr-critic`) MUST use the artifact-ref-only prefix format. Do NOT include the generator's GENERATOR-trailer narrative, self-assessment text, or success claims. The canonical dispatch shape is:
   ```
   BLIND-REVIEW <artifact-ref>
   Rubric: <agent-name>.md
   Round: <N>
   ```
   Admissible: PR number, branch name, slice issue number, changed-file list, round context, the generator's `CONCERNS:` field (doubts only). Inadmissible: any characterization of correctness from the generator narrative. Per [ADR-0060](../../../decisions/0060-blind-dispatch-contract.md) D1 (bootstrap-mode: binds forward from this ship-skill merge).

   **Write restrictions in dispatch briefs (ALL dispatches in this stage — critics and generators alike).** Phrase every restriction in terms of the rule it paraphrases: rule #21 forbids fixture/synthetic data in the production logs, not writes to `.claude/logs/*` as such; "read-only" means no repo-artifact edits. A brief MUST NOT ban a sanctioned write the dispatched agent's own contract mandates — the two known instances being a critic's verdict posting on the artifact under review (per [ADR-0054](../../../decisions/0054-critic-output-contracts-and-trailer-standard.md) D1) and a pipe verb's span emission (per [ADR-0076](../../../decisions/0076-guarded-verb-pipeline-engine.md) D1).

   ### Amendment halt-and-regate protocol (ADR-0066 D3)

   **After the first implementer dispatch for a PRD, its body is frozen.** Requirement changes land exclusively as append-only `## AMENDMENT <n>` comments on the PRD issue, each declaring `ADDED/MODIFIED/REMOVED` against the numbered §2 criteria (e.g. `MODIFIED §2 #3: ...`). A PRD body edit after first dispatch — without a matching AMENDMENT comment — is a silent-drift violation counted by the SILENT-DRIFT registry row.

   **Detecting an amendment:** Before each dispatch batch, check for new `## AMENDMENT <n>` comments posted since the previous batch:
   ```bash
   gh issue view <PRD_NUMBER> --json comments -q '.comments[].body' | grep -c '^## AMENDMENT'
   ```
   If the amendment count has increased since the last batch:
   1. **Halt** all not-yet-started dispatches (leave `pending` set paused).
   2. **Dispatch `prd-critic` in delta mode** (pass the AMENDMENT comment body, the original PRD context, and `DELTA_MODE: true`). prd-critic reviews ONLY the delta — the ADDED/MODIFIED/REMOVED criteria — applying PC-EARS to new/modified criteria.
   3. **Dispatch `slicer-critic` for SC-COVERAGE re-check** against the amended criterion set (pass the amendment comment, current slice list, and existing `Covers:` lines).
   4. Both must APPROVE. On either BLOCK: apply `needs-human` to the PRD, post findings, forward-block pending slices per 5d.
   5. On dual APPROVE: update the internal §2 reference set with the delta, resume the `pending` dispatches.

   **In-flight dispatches** (already in `in_flight`) finish against their original contract. At review, the reviewer reconciles: if the in-flight slice's scope predates the amendment, flag the delta as a follow-up capture; do not BLOCK for pre-amendment work.

   - **5a. Build the DAG.** Parse each slice's `## Depends on` (slicer-critic-verified per [ADR-0003](../../../decisions/0003-autonomous-pipeline-with-critics.md) D3). Topologically sort; ties broken by issue number ascending. On parse failure or cycle, STOP with `RESULT: INVALID_INPUT` in the trailer.

   - **5a-fix. Blind test-author pre-dispatch for fix-type slices (ADR-0067 D2).** Before dispatching the implementer for any slice, check whether the slice is fix-type: branch name matches `fix/*` OR the slice issue carries a `root-cause` label:
     ```bash
     gh issue view <slice_number> --json labels --jq '.labels[].name' | grep root-cause
     ```
     **If fix-type:** dispatch a `general-purpose` subagent (with `isolation: "worktree"`) BEFORE the implementer. Pass only:
     - The slice issue number and its title/summary
     - The defect description from the `root-cause` capture or slice body's "What ships" section
     - This instruction: "Write a failing regression test in `tests/` that demonstrates the defect described. Commit it with `test(regression): <short description>`. The test MUST fail on the current codebase — do NOT fix the defect. Return `RESULT: SUCCESS` when the failing test is committed and pushed to the PR branch."
     The blind test-author's ONLY deliverable is the failing test committed to the PR branch. It MUST NOT make the test pass. Verify via `RESULT: SUCCESS` trailer. Then dispatch the implementer — whose job is to make the failing test pass.
     **If NOT fix-type:** skip this step; proceed directly to 5b.
     **On blind-test-author BLOCKED or INVALID_INPUT:** surface the failure; forward-block the slice per 5d (a fix cannot be implemented without the test).

   - **5b. Dispatch loop.** Maintain four sets — `pending`, `in_flight`, `merged`, `blocked`. Each iteration: compute the **ready batch** (every `pending` slice whose deps are all in `merged` AND has no dep in `blocked`); slices with a `blocked` dep move directly to `blocked` (forward-block). **Guarded-verb invocation (PRD #1127 walking skeleton, ADR-0076 D1):** for each slice in the ready batch, run `python tools/pipe/dispatch <slice>` immediately BEFORE invoking its `implementer` `Agent` call; a non-zero exit (precondition refusal — closed/nonexistent slice, missing provenance, wrong assignee, or an already-active dispatch span) means do NOT dispatch that slice — forward-block it per 5d instead. **Dispatch the ready batch in parallel** by invoking the [`implementer`](../../agents/implementer.md) subagent via the `Agent` tool with `subagent_type: "implementer"` (fallback `general-purpose` with the implementer prompt loaded inline) for each slice; move each to `in_flight`.

     **Every `implementer` `Agent` call MUST pass `isolation: "worktree"` regardless of batch size** — even a single-slice dispatch's `git checkout -b` pollutes the shared session tree if run without isolation (per **[ADR-0036](../../../decisions/0036-worktree-isolation-all-dispatches.md) D1**, superseding ADR-0035 D1's batch-size-≥2 condition). **Every `reviewer` `Agent` call MUST also pass `isolation: "worktree"`** — the reviewer's `git fetch`/`diff`/`python tools/pipe/pr-merge` (internally `gh pr merge --squash --auto`; no `--delete-branch` — that flag was removed from the wrapper per PR #1104, `worktree-guard prune` owns post-merge branch cleanup instead) then run in a fresh tree that never has the to-be-merged branch checked out, eliminating the "branch deletion failed — worktree conflict" class and stale-base false-positives (per **[ADR-0036](../../../decisions/0036-worktree-isolation-all-dispatches.md) D2**). The parallel-batch origin of this isolation mechanism is [ADR-0035](../../../decisions/0035-worktree-isolation-parallel-dispatch.md) D1; ADR-0036 extends it unconditionally. **Before each implementer/reviewer `Agent` dispatch, capture `EXPECTED=$(git rev-parse --abbrev-ref HEAD)`; after the dispatch returns, run `bash tools/worktree-guard.sh branch-restore "$EXPECTED"` to ff-restore the orchestrator's worktree if it drifted** (per [ADR-0041](../../../decisions/0041-origin-main-source-of-truth.md) D1). **Missing `worktreePath` in a dispatch result = dispatch failure (ADR-0058 D1):** after every `Agent` dispatch, check the harness-reported `worktreePath` field in the result; if absent, treat as a failed dispatch regardless of the agent's trailer — re-dispatch with the same slice, and capture the occurrence per rule #13. Await batch completion; handle outcomes per 5c. Loop until `pending` and `in_flight` are both empty.
   - **5c. Per-slice outcome.** Immediately when the implementer subagent returns (any `RESULT`), run `python tools/pipe/dispatch --end <slice> --result <RESULT-value>` to record the matching `dispatch_end` span before evaluating the outcome below (ADR-0076 D1). `RESULT: SUCCESS` → check whether this is the **last open slice** of its PRD (per [ADR-0046](../../../decisions/0046-codebase-critic-and-parsimony-reframe.md) D3): run `gh issue list --repo <owner>/<repo> --state open --label slice --json number,title` and filter to sub-issues of the PRD; if zero other open `slice`-labeled sub-issues remain on the PRD, dispatch **`codebase-critic`** (subagent, `isolation: "worktree"`, read-only as to repo artifacts — its mandated `gh issue comment` verdict posting on the PRD issue is part of its contract, not an optional write; see [`.claude/agents/codebase-critic.md`](../../agents/codebase-critic.md) "Mandatory output-contract posting — per-PRD mode only") on the cumulative PRD change **before** dispatching the reviewer. Pass the PRD number, the PRD base commit (the merge-base between `origin/develop` and the first slice's branch at dispatch time — use `git merge-base origin/develop <first-slice-branch>`, or the SHA captured at stage 4 start), and the current HEAD of the last-slice branch. Sequence: implement → **`codebase-critic`** → `reviewer` → merge (per ADR-0046 D3; the reviewer **remains the sole merge gate** per [ADR-0002](../../../decisions/0002-autonomous-merge-policy.md)). **`codebase-critic` BLOCK** → iterate up to ≤3 rounds: re-dispatch the implementer (isolated) with the BLOCK findings; implementer fixes and pushes; re-run `codebase-critic`. Round-3 BLOCK: apply `needs-human` to the PRD issue + post a summary comment on the PRD (I5 surface); forward-block per 5d. **`codebase-critic` RECOMMEND** findings → create `captured`-labeled issues + fire `/promote-to-backlog <N>` per the autopilot (rule #11); non-blocking; proceed to reviewer immediately. If this is NOT the last open slice (other `slice`-labeled sub-issues of the PRD remain open), skip the `codebase-critic` dispatch and proceed directly to the reviewer.

**Concurrent reviewer dispatch (ADR-0077 D2 — reworks the strictly-serialized CI-then-review sequencing of the former pre-review CI gate; the #869 recurring-format-BLOCK-class protection from PRD #1075 criterion 6 is preserved, relocated).** Dispatch the reviewer immediately — right after `codebase-critic` clears above, or immediately after implementer `SUCCESS` when no `codebase-critic` dispatch applies. Do NOT wait for the PR's `ci` check to reach a terminal state first: the reviewer's read/verify rubric is independent of CI status, and `ci` runs concurrently in GitHub Actions while the reviewer reads the diff. The reviewer itself owns the terminal-`ci` poll and the CHECK-3 format-fail intercept immediately before any merge attempt (see [`.claude/agents/reviewer.md`](../../agents/reviewer.md) "Pre-merge CI-terminal gate") — on a format-class CI failure the reviewer's own verdict flips to `BLOCK` with the same corrective message as before, and the standard 5d / implementer-fix-loop round-trip handles it via the reviewer's own round-cap (no separate orchestrator-tracked counter is needed). If `gh` is unavailable/unauthenticated, the reviewer soft-degrades the same as before.

→ reviewer takes over per [ADR-0002](../../../decisions/0002-autonomous-merge-policy.md) (auto-merge on APPROVE via `python tools/pipe/pr-merge <PR>`, internally `gh pr merge --squash --auto` — no `--delete-branch`, that flag was removed from the wrapper per PR #1104; round-3 BLOCK applies `needs-human` and forward-blocks per 5d). **Merge-collection serialization (ADR-0062 D2):** When multiple sibling PRs in the same batch are simultaneously APPROVE-ready, reviewer dispatches run in parallel BUT the merge step itself MUST serialize — do not trigger two concurrent `gh pr merge` calls. Merges execute one at a time in completion order (first APPROVE received merges first; the next waits until the preceding merge + CI loop finishes). This guarantees every squash lands on the exact main it was CI-tested against (the not-rocket-science invariant) without hosted merge-queue infrastructure. On reviewer APPROVE+merge → `merged`; run `bash tools/worktree-guard.sh root-sync` to ff-sync the root repo to `origin/develop` so the main checkout — whose hooks, settings, and logs every session uses — reflects `develop` (per [ADR-0041](../../../decisions/0041-origin-main-source-of-truth.md) D3); then run `bash tools/worktree-guard.sh prune` to remove any local worktrees whose remote branch has been deleted (squash-merged; `worktree-guard prune` — not a `gh pr merge --delete-branch` flag — owns this cleanup), keeping the tree list clean across waves. **Post-merge green-develop step (ADR-0062 D3, narrowed by [ADR-0079](../../../decisions/0079-recorded-ci-trust-and-hook-diet.md) D1):** After each merge + root-sync, run the post-merge verification on actual merged develop:
   1. `develop_green` recording is chained automatically from `tools/pipe/pr-merge`'s own confirmed-merge success path (PRD #1127 criterion 4 / slice #1134, ADR-0076 D1) — internally `tools/pipe/record-green` trusts the recorded GitHub `ci` conclusion for the exact merged sha instead of re-running the local suite (ADR-0079 D1 supersedes the standalone always-run `bash tools/ci-checks.sh` mandate previously run as a separate step here); its stdout/stderr names the outcome (recorded / evaluation failed / budget exhausted); no separate invocation is needed here.
   2. On failure: the suspect set = squash commits since the last `develop_green` event (≤600 LoC slices make bisect degenerate); revert via the trivial lane (`hotfix/<short-desc>` branch); do NOT mark the PRD done until green.
   Per [ADR-0062](../../../decisions/0062-merge-integrity-green-main.md) D3 (bootstrap-mode: binds forward from this ship-skill merge) and [ADR-0079](../../../decisions/0079-recorded-ci-trust-and-hook-diet.md) D1 (recorded-CI trust; the redundant standalone local re-run is deleted). On reviewer round-3 BLOCK or implementer `RESULT: BLOCKED` / `RESULT: INVALID_INPUT` → forward-block per 5d. **`RESULT: CONFUSION` from implementer or qa-tester (per ADR-0059 D3):** do NOT guess or pick an option on the agent's behalf. Route: (A) if the conflict can be resolved from the grilled context or PRD body, re-dispatch with an explicit resolution and record `"CONFUSION resolved: <option chosen> — reason: <one sentence>"` in the dispatch trail; (B) if it requires user judgment, apply `needs-human` to the slice, post the CONFUSION reason + options on the slice issue, and forward-block per 5d with `REASON: CONFUSION — needs design clarification`. Never silently pick an option without recording the choice; resolution route A or B must be logged in the dispatch trail.
   - **5d. Forward-block** (per [ADR-0010](../../../decisions/0010-implementer-subagent-auto-pipeline.md) D4). Apply `needs-human` to the failed slice; move transitive-downstream slices from `pending` → `blocked`; post one summary comment per failure event on the parent PRD (mirrors reviewer's I5 surface). **In-flight parallel siblings finish normally** — do NOT cancel. **Slices with other unmet deps proceed normally** through their natural batches; failure is locally contained to the failed slice's downstream cone.
   - **5e. Terminal-state collection.** Capture each `PR_URL` from SUCCESS slices (merged or under-review), the `blocked` set, and the snapshot of `in_flight` at the moment the FIRST failure was observed.

   - **5f. Green-develop checkpoint → RELEASE-READY → auto-promote (ADR-0070 D2/D3, slice #838; ADR-0079 D1 narrows the standalone-ci-checks step below).** After all slices in the current batch are in `merged`, run the post-merge green-develop verification step (mirrors the green-main step at step 5c-4, with target `develop`):
     1. Evaluate the RELEASE-READY gate — its own condition (a)/(b) already trust the recorded GitHub `ci` conclusion for this exact sha instead of re-running the local suite (per [ADR-0079](../../../decisions/0079-recorded-ci-trust-and-hook-diet.md) D1, superseding the standalone `bash tools/ci-checks.sh` mandate previously run as a separate step here):
        ```bash
        python3 dashboard/health.py --check RELEASE-READY
        ```
        Parse the `verdict` field from the JSON output.
     2. **If `verdict == "true"`** (all six conditions hold):
        - **PROMOTE_OK sentinel check:** Before calling `promote.sh`, verify that the current promotion batch does NOT touch guardrail-machinery paths (`.github/workflows/**`, `.claude/settings.json`, `.claude/hooks/**`, `tools/ci-checks.sh`, `.githooks/**`, `*-critic.md`, or `tools/promote.sh` itself). If it does, `promote.sh` requires `.claude/PROMOTE_OK` to exist (human-ack sentinel per ADR-0070 D4). If the file is absent and guardrail paths are touched, log `"RELEASE-READY true but PROMOTE_OK sentinel absent — human ack required for guardrail-machinery promotion"` and skip promotion; do NOT run `promote.sh`. The human creates `.claude/PROMOTE_OK` (via `touch .claude/PROMOTE_OK`) to unblock; `promote.sh` removes it after successful promotion.
        - Run `bash tools/promote.sh` to fast-forward `main` to `develop` HEAD and append the `promotion` event. The script performs its own RELEASE-READY pre-flight guard and emits: `INFO: promotion event appended — sha=<sha>`.
        - Log: `"green-develop checkpoint PASS + RELEASE-READY true → auto-promoted main to <sha>"`.
     3. **If `verdict != "true"`** (gate held):
        - Log: `"green-develop checkpoint PASS but RELEASE-READY held: <first_failing_condition>. Main NOT advanced. Develop continues independently."`.
        - Do NOT run `promote.sh`. Continue to step 6.
     4. On a RELEASE-READY evaluation error (green-develop step fails): revert via trivial lane; do NOT mark PRD done until green-develop is clean.
     **Note on condition (e):** `needs-human` open items commonly hold the gate (e.g. during a wave's own slices). This is correct and honest — the gate reports the true state; promotion waits. The develop integration branch continues to accept PRs normally while the gate is held.

   - **5g. Regenerate docs (rehosted from `/build` step 4 per [ADR-0081](../../../decisions/0081-post-audit-dead-weight-retirements.md) D4).** Run the doc-generator as a subprocess so the PRs arrive doc-current:

     ```bash
     python "${CLAUDE_PROJECT_DIR}/dashboard/readme_gen.py"
     ```

     If the generator exits non-zero, emit a warning with the error output and continue — doc-regeneration failure is a soft error at this step (the reviewer's `R-DOCS-CURRENT` rule is the hard gate). If it exits zero, confirm `README.md` updated (note byte count).

6. **Production-verify gate (MANDATORY — per ADR-0037 D1).**

   Dispatch `qa-tester` in production-verify mode with `isolation: "worktree"` (ADR-0036):

   Input to pass:
   - The full PRD body (fetch via `gh issue view <PRD_NUMBER> --json body`)
   - The "Production check:" line extracted from PRD §2
   - A merged diff summary (changed-path globs from the implementation PRs in step 5)

   **Result handling (loop per ADR-0037 D5):** up to 3 rounds.

   **PASS** (`PRODUCTION_VERIFY: PASS`):
   - Extract `PROOF:`, `ROUTE:`, `ARTIFACTS:`, `PROOF_SOURCE:`, and `ENV:` from qa-tester's trailer.
   - **Artifact-existence assertion (ADR-0061 D5 — orchestrator-enforced):** for each path in `ARTIFACTS:`, stat the file before accepting PASS:
     ```bash
     for artifact_path in <paths from ARTIFACTS: field>; do
       if [ ! -f "$artifact_path" ]; then
         echo "GATE FAIL: ARTIFACTS path does not exist: $artifact_path — verdict invalid"
         # treat as FAIL, re-dispatch (up to 3 rounds)
       fi
     done
     ```
     A `PRODUCTION_VERIFY: PASS` with a missing ARTIFACTS path is **verdict-invalid** — treat as a FAIL and loop (re-dispatch qa-tester). A missing artifact means the proof cannot be verified, per ADR-0061 D5 (issue #777 class).
   - **PROOF_SOURCE validation (ADR-0061 D2):** validate `PROOF_SOURCE: <sid>@<ts>` before accepting PASS:
     1. Extract `sid` from `PROOF_SOURCE:` field.
     2. Assert `sid` exists in `.claude/logs/workflow-events.jsonl` (grep for the sid in the event window).
     3. Assert `sid` is NOT fixture-patterned: must not match `sess-test-*`, `fixture-*`, or `synthetic-*`.
     4. If validation fails → verdict invalid → treat as FAIL, re-dispatch.
   - **ENV validation for browser routes (ADR-0061 D2, host-generic — this template no longer serves an `/api/meta`-style identity endpoint per ADR-0088 D1/D5):** when `ROUTE: browser`, validate `ENV: <sha>@<started_at>`:
     1. Extract `sha` from `ENV:` field and confirm it equals the merged HEAD sha for this PR.
     2. Confirm by a host-appropriate mechanism that the served app is running the merged code (e.g. an app-specific version/health endpoint or build-info banner the host project exposes) — this template itself serves nothing to check against; a host project that adds a browser-reachable UI supplies its own freshness signal here.
     3. If `sha` mismatches, or the host-appropriate freshness signal reports stale/absent, → verdict invalid → treat as FAIL, re-dispatch.
   - **Block-on-missing-proof check (per ADR-0037 D3 — orchestrator-enforced blocking):** assert `PROOF:` is non-empty for the routed change type. If `PRODUCTION_VERIFY: PASS` but `PROOF:` is empty or absent, the feature is **NOT done** — treat this as a gate failure and block:
     - `browser` route: `PROOF:` MUST contain a screenshot path (`.png` or `.jpg`) AND an `inner_text:` excerpt. A UI/browser change with no screenshot proof is not 'done'. Block with: `"PRODUCTION_VERIFY claimed PASS but PROOF: absent for browser route — screenshot proof required; not marking done."`
     - `hook-fire` route: `PROOF:` MUST contain `exit=` AND a `log:` field whose content IS a pasted verbatim beacon line matching `"status":\s*"(ok|ERROR)"` (or "log: N/A" if not declared) — a description of the beacon does not satisfy this (ADR-0083 D4(a)). A hook change with no pasted-beacon-line proof is not 'done'.
     - `command-run` route: `PROOF:` MUST contain `exit=` AND `output:`. A skill/tool change with no output excerpt proof is not 'done'.
     - `static-check` route: `PROOF:` MUST contain `grep count=`. A static assertion with no grep-count proof is not 'done'.
     - If `PROOF:` is non-empty and matches the expected shape for the route, proceed to proof-posting. If proof-absent block occurs, log the failure and loop: re-dispatch `qa-tester` (round incremented); treat as a FAIL for the loop counter (up to 3 rounds per ADR-0037 D5).
   - Surface the proof: print `PROOF:` + `ROUTE:` + `ASSERTIONS_CHECKED:` from qa-tester's trailer.
   - Record the verdict via the sanctioned wrapper (repoint target, PRD #1075 criterion 1 rider / slice #1086 — previously unrecorded outside qa-tester's own trailer): `python tools/pipe/qa-verify --verdict PASS --route <ROUTE value> --pr <pr-number> --prd <PRD_NUMBER>`.
   - Log: `"Production gate PASS (round <N>): feature verified."`
   - **Proof-posting (per ADR-0049 D3 — orchestrator owns commit + comment; qa-tester stays read-only; scope formalized by ADR-0084):**
     If qa-tester's `ARTIFACTS` trailer contains a proof image path (a `.png` or `.jpg` file), commit it to the PR branch and post a PR comment so the reviewer and user see the rendered proof inline:
     ```bash
     # <prd-num>  = PRD issue number (from stage 2's captured PRD number)
     # <proof>    = the local path returned in qa-tester's ARTIFACTS field
     # <branch>   = the PR branch name (from implementer's BRANCH_NAME trailer)
     # <pr-num>   = PR number (from implementer's PR_URL trailer)
     # <slug>     = basename of the proof file (e.g. "architecture-live.png")

     git add qa-proof/<prd-num>/<slug>
     git commit -m "chore(qa): add visual proof for PRD #<prd-num>"
     git push

     # Post an inline PR comment with the rendered image.
     # raw.githubusercontent.com renders images directly in PR comments (unlike the
     # blob/?raw=true form which serves the file but does not render inline).
     # Derive the slug from origin so the URL is correct in any derived repo,
     # not just the template (issue #649).
     REPO_SLUG=$(gh repo view --json nameWithOwner -q .nameWithOwner)
     gh pr comment <pr-num> --body "![qa-proof](https://raw.githubusercontent.com/${REPO_SLUG}/<branch>/qa-proof/<prd-num>/<slug>)"
     ```
     If no proof image path is present (command-run or static-check route), skip the commit/comment and continue.
   - **SendUserFile at wrap-up (per CLAUDE.md rule #20 + ADR-0037 D3):** After proof-posting, send each committed proof artifact to the user in chat alongside the verified claim (step 7 narrative). This runs in main-agent context, so `SendUserFile` is available. At step 7, for each feature's proof:
     - **browser route:** `SendUserFile qa-proof/<prd-num>/<slug>` with caption `"PRD #<prd-num> — production-verified: [the PRD's Production check: line]"`.
     - **hook-fire / command-run / static-check routes:** no file to send; print the `PROOF:` string inline beside the verified claim in the step 7 narrative.
   - **Visible-surface screenshot floor (run-to-done complement):** Regardless of proof route, if the shipped work has ANY user-visible surface — a dashboard tab, panel, graph, or report view — the wrap-up MUST capture a post-merge production screenshot from a fresh (restarted/live) environment per rule #20's freshness clause and `SendUserFile` it with a claim-tied caption. The route-scoped SendUserFile items above are the per-route minimum; this floor-raises them for visual work. Non-visual work: send the nearest visible artifact if one exists (e.g. a CLI output excerpt as an inline code block), else state honestly "no visual surface — nearest artifact: [quoted output]". Do NOT send a screenshot of a stale or pre-merge environment; restart the relevant server/dashboard before capturing.
   - Proceed to step 7.

   **FAIL** (and `round < 3`):
   - Surface the failure proof (REASON + PROOF + ASSERTIONS_CHECKED).
   - Re-dispatch the implementer (isolated, ADR-0036) with the proof: `"Production gate FAIL (round <N>): <reason>. Proof: <proof>. Fix and push."` The implementer opens a new PR; reviewer merges it. Re-run step 5 (re-ship) → re-run step 6 (gate).

   **FAIL on round 3** (escalation per ADR-0037 D5 / I5):
   - `gh issue edit <PRD_NUMBER> --add-label needs-human`
   - `gh issue comment <PRD_NUMBER> --body "Production gate FAIL after 3 rounds. Blocked. Proof: <REASON> | <PROOF> | Assertions: <ASSERTIONS_CHECKED>. Human review required."`
   - Return `RESULT: BLOCKED` in the /ship trailer.

   **INVALID_INPUT from qa-tester:** surface the reason and STOP — do not loop.

7. **Report back.** Print the PRD URL, slice URLs, merged/open implementation PR URLs, any forward-block summary, and the production-verify proof. Free-form narrative; not itself a canonical template per PRD #28 §6 OQ#2. End with the canonical GENERATOR trailer as a fenced block (schema per ADR-0005 D1c):

   ```
   RESULT: SUCCESS | STOPPED | INVALID_INPUT | BLOCKED
   REASON: <one sentence>
   ARTIFACTS: <PRD URL>, <slice URLs comma-separated>
   SLICE_COUNT: <N>
   IMPLEMENTATION_PRS: <comma-separated PR URLs from implementer invocations; empty if pipeline halted before stage 4>
   BLOCKED_SLICES: <comma-separated slice numbers in the `blocked` set per 5d; empty if no failures>
   IN_FLIGHT_AT_FAILURE: <comma-separated slice numbers in `in_flight` at the moment of FIRST failure per 5e; empty if no failures>
   PRODUCTION_VERIFY: <PASS | FAIL | not-reached>
   ```

   `SLICE_COUNT` / `IMPLEMENTATION_PRS` / `BLOCKED_SLICES` / `IN_FLIGHT_AT_FAILURE` / `PRODUCTION_VERIFY` are per-agent extensions appended after `ARTIFACTS` so human triage and post-run audits find every stuck slice without re-parsing. `PRODUCTION_VERIFY: not-reached` when the pipeline halted before step 6. On `STOPPED` / `INVALID_INPUT`, `ARTIFACTS` may be partial or empty; the extensions are `0` / empty.

## References

- Full role synthesis (invocation contract, edges): this file. Pipeline stages synthesis: CLAUDE.md §3 (Hierarchy + workflow conventions).
- [ADR-0003](../../../decisions/0003-autonomous-pipeline-with-critics.md) — D2 (5-stage pipeline), D4 (no human gates between stages; closed end-to-end by ADR-0010), D7 (`/ship` orchestrator skill, lightweight v1), D8 (ADR placement at slice 1).
- [ADR-0010](../../../decisions/0010-implementer-subagent-auto-pipeline.md) — D2 (auto-invoke implementer), D3 (DAG-aware parallel batching), D4 (forward-block failure handling), D5 (sequential walking-skeleton baseline).
- [ADR-0036](../../../decisions/0036-worktree-isolation-all-dispatches.md) — D1 (every implementer dispatch isolated regardless of batch size, superseding ADR-0035 D1's batch-size-≥2 condition); D2 (every reviewer dispatch also isolated); D3 (dispatched subagents never mutate the orchestrator's session worktree or root repo).
- [ADR-0041](../../../decisions/0041-origin-main-source-of-truth.md) — D1 (post-dispatch leak-guard: capture branch, restore after each dispatch via `branch-restore`); D3 (root ff-sync after merge via `root-sync`; orchestrator carve-out to ADR-0036 D3).
- [ADR-0035](../../../decisions/0035-worktree-isolation-parallel-dispatch.md) — D1 (superseded by ADR-0036 D1 for the batch-size condition; parallel-batch race origin of the isolation mechanism); D2 (isolation lives in orchestrator; implementer unchanged).
- [ADR-0056](../../../decisions/0056-no-rule-without-a-check.md) — rule #23's mechanized-enforcement requirement; the format-vs-other classifier lives in the testable [`tools/ci-failure-kind.sh`](../../../tools/ci-failure-kind.sh) helper (regression-tested in [`tests/test_ci_failure_kind_1084.py`](../../../tests/test_ci_failure_kind_1084.py)) rather than inline SKILL.md prose, after a reviewer BLOCK (PR #1087 round 1) proved a naive `grep "CHECK 3"` misclassifies every CI failure as the format class.
- [ADR-0077](../../../decisions/0077-ceremony-overhead-reduction.md) — D1 (R-LOC cap raised 300→600 LoC; slicer targets 3-5 slices/PRD); D2 (reviewer dispatch made concurrent with CI instead of strictly-serialized after it — supersedes this file's former pre-review CI gate; the #869 format-BLOCK-class protection relocates into [`reviewer.md`](../../agents/reviewer.md)'s own pre-merge gate, closing the same PRD #1075 criterion 6 with one fewer orchestrator-tracked round counter).
- [ADR-0037](../../../decisions/0037-production-verification-gate.md) — D1 (mandatory blocking gate per feature), D3 (orchestrator-enforced; qa-tester is the generator, /ship is the enforcer), D5 (failure loop ≤3 rounds + needs-human escalation), D6 (bootstrap-mode).
- [ADR-0002](../../../decisions/0002-autonomous-merge-policy.md) — reviewer auto-merge on APPROVE; the handoff target after implementer SUCCESS.
- [ADR-0076](../../../decisions/0076-guarded-verb-pipeline-engine.md) — D1 (verbs are the sole sanctioned path for mechanical pipeline transitions); step 5b/5c's `python tools/pipe/dispatch <slice>` / `--end` calls are the walking-skeleton repoint (slice #1129).
- [ADR-0085](../../../decisions/0085-queue-drain-mode.md) — D1 (queue-drain is an entry mode on `/ship`, never a second orchestrator; plan-only and bounded sub-forms), D2 (reasonable-engineer triage litmus; label-and-continue escalation on `needs-human-check`), D4 (a durable per-run drain ledger, separate from trace-v3), D5 (fix-in-run: trivial-lane discoveries land inside the drain run).
- [ADR-0051](../../../decisions/0051-whole-repo-macro-audit-cadence.md) — D1 (whole-repo macro-audit cadence: auto-launches at `/ship` start, once per session, non-blocking), D2 (mechanism is `codebase-critic` whole-repo mode — no new critic), D3 (background dispatch + harvest-on-completion), D4 (once-per-session marker guard).
- Sibling skills the chain calls: [`.claude/skills/to-prd/SKILL.md`](../to-prd/SKILL.md), [`.claude/skills/to-issues/SKILL.md`](../to-issues/SKILL.md). Subagent dispatched at stage 4: [`.claude/agents/implementer.md`](../../agents/implementer.md). Subagent dispatched at step 6: [`.claude/agents/qa-tester.md`](../../agents/qa-tester.md) in production-verify mode.

## Local vocabulary

Per [ADR-0014](../../../decisions/0014-skill-local-vocabulary-and-auto-fold.md) D1. Entries that pass the [ADR-0012](../../../decisions/0012-glossary-consolidation-single-tier.md) D2 citation threshold are candidates for CLAUDE.md §4; promote them by hand in a normal reviewer-gated PR (the automated fold path was retired per [ADR-0081](../../../decisions/0081-post-audit-dead-weight-retirements.md) D2).

- **pipeline metadata footer** — the one-line `> **Pipeline metadata** — Approved by prd-critic round <N>/3...` audit trailer that `/to-prd` appends to every posted PRD body so `/ship` and downstream critics can mechanically verify upstream APPROVE without re-running the loop.
  - *Scope:* (a) project jargon coined here
  - *Authority:* `ADR-0003 D8`
  - *See also:* `/to-prd`; `/ship`; prd-critic
