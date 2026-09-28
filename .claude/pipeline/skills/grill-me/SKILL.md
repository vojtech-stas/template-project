---
name: grill-me
description: Interview the user relentlessly about a plan or design until reaching shared understanding, resolving each branch of the decision tree. Use when user wants to stress-test a plan, get grilled on their design, or mentions "grill me".
---

Interview me relentlessly about every aspect of this plan until we reach a shared understanding. Walk down each branch of the design tree, resolving dependencies between decisions one-by-one. For each question, provide your recommended answer.

## Opening step — whole-queue survey + triage (grilled 2026-06-30)

Before Q1, a grill session covers the ENTIRE open work queue, not just the one initiative the user named. Do this once, at the start of every grill session:

1. **Pull the open queue.** Run `gh issue list` across the relevant labels — `backlog`, `captured`, open `slice`s, and any open root-cause issues (`gh issue list --label root-cause --state open`) — plus whatever specific item prompted this grill.
2. **Triage every item into exactly one of two buckets:**
   - **`just-do`** — clear bugs, hygiene, docs fixes, mechanical refactors: no design decision needed, safe to execute autonomously without grilling. An item belongs here only if a reasonable engineer would not need to ask the user anything to proceed.
   - **`needs-decision`** — genuine design/policy forks: new-feature shape, retire-vs-rework calls, anything with more than one defensible approach, or anything that changes a contract/interface/user-facing behavior. An item belongs here if answering it wrong would require redoing the work.
3. **Emit the triage table before Q1** — a plain table (columns: issue #, title, bucket, one-line reason) so the user sees the full queue landscape up front, before any question is asked.

## Grill every needs-decision fork — relentlessly, not just one

The per-question mechanics below (AskUserQuestion, `Q<n> — topic` headers, options BEST→LEAST with `(Recommended)`, PRO/CON per option, one small batch per call) are unchanged. What's new: apply them to **every** `needs-decision` item from the triage table, not only the single initiative that prompted the session.

- Walk the dependency tree across items, not just within one item — if two forks are related (e.g., one supersedes another, or a decision on #A constrains the options on #B), resolve the upstream one first and let it narrow the downstream question.
- Keep numbering stable and global across the whole session (`Q1`, `Q2`, ... `Qn`) even as you move between different needs-decision issues, so any answer can be referenced by its `<question><option>` shorthand.
- Do not stop after the first fork is resolved — continue through the full needs-decision set until every fork in the triage table has an answer. Only the `just-do` items are left un-grilled, since they're headed for autonomous execution rather than a decision.

## How to ask each question (clickable options)

Use the **AskUserQuestion** tool for every decision point so the options are clickable. For each question:

1. **Number the question** — put `Q<n> — <topic>` in the `header` (e.g., `Q3 — Auth method`). Keep numbering stable so I can refer to a choice as `<question><option>` shorthand (e.g., "3B", "4C").
2. **Give 2–4 options, sorted BEST → LEAST-preferable** (most-recommended option first). The tool labels them A/B/C/D in order.
3. **Mark the top option `(Recommended)`** in its `label`.
4. **Each option's `description` states its PRO and its CON** (and the key trade-off vs the alternatives) so I can compare at a glance — not just what the option is.
5. Resolve one decision (or a small batch of tightly-related decisions) per `AskUserQuestion` call; walk the dependency tree so my earlier answers constrain the later questions.
6. Always include your own recommendation and the reasoning; if a question can be answered by exploring the codebase, explore the codebase instead of asking.

## What NOT to grill — scope boundary

The grill resolves **design decisions, acceptance criteria, and appetite** (the substance + rough size of WHAT to build). It does **NOT** decide **slice decomposition** — how many slices, where boundaries fall, or the walking-skeleton cut. That is owned by the `slicer` + `slicer-critic` (per [ADR-0013](../../../../decisions/0013-slicer-n3-contract-refined.md), [ADR-0005](../../../../decisions/0005-output-shape-and-slicing-methodology.md) D3). Never ask the user "how should we slice this?" / "how many slices?" — finish the design grill, then `/to-issues` hands the PRD to the slicer to decompose.

## Optional doc-path argument (per [ADR-0007](../../../../decisions/0007-vocabulary-glossary-and-grill-me-extension.md) D6)

`/grill-me <path>` reads `<path>` before asking Q1. Use this when the grill is about an existing spec, PRD, or external doc that should anchor the questions. Single optional local path only — not multi-doc, not config-file, not URL.

- If `<path>` exists and is a readable file → `Read` it in full before Q1; reference its content throughout the grill.
- If `<path>` does not exist or is not readable → report the error in one line (`"path '<X>' not found — proceeding in no-arg mode"`) and continue with the no-arg flow. Do NOT abort.
- If no argument is supplied → existing no-arg behavior is unchanged (full backward compatibility per [ADR-0007](../../../../decisions/0007-vocabulary-glossary-and-grill-me-extension.md) D6).

## Glossary read mechanism (per [ADR-0012](../../../../decisions/0012-glossary-consolidation-single-tier.md) D1)

Project vocabulary lives in a **single tier**: the `## Glossary (key terms)` section inside `CLAUDE.md`. It is **auto-loaded** by the runtime on every session, so you already have it when the grill starts — use these terms with their project-narrowed meanings without re-deriving them. (The former repo-root long-tail file was retired by [ADR-0012](../../../../decisions/0012-glossary-consolidation-single-tier.md) D1; there is no second tier to read on demand.)

If an unfamiliar term comes up during the grill (yours or the user's) and it is missing from that section but looks glossary-worthy per [ADR-0007](../../../../decisions/0007-vocabulary-glossary-and-grill-me-extension.md) D3 (categories a/b/c), inline a one-line suggestion: *"Heads up: '<X>' looks glossary-worthy — worth proposing as a CLAUDE.md §4 entry in a normal PR, or capturing as an issue."* Glossary additions ride ordinary reviewer-gated PRs per [ADR-0081](../../../../decisions/0081-post-audit-dead-weight-retirements.md) D2. This surfacing is discretionary per [ADR-0007](../../../../decisions/0007-vocabulary-glossary-and-grill-me-extension.md) D4, mirroring [ADR-0006](../../../../decisions/0006-backlog-and-session-continuity.md) D4's backlog surfacing pattern.

**End-of-session captured-tier sweep.** Per [ADR-0008](../../../../decisions/0008-workflow-autolog-bootstrap-and-naming.md) D8 + [ADR-0009](../../../../decisions/0009-discipline-tightening.md) D2 (originating from [ADR-0006](../../../../decisions/0006-backlog-and-session-continuity.md) D4 write-convention pattern), at the end of each grill session, the agent MUST review items that surfaced but were deferred (out of scope, "we should also do X", deferred per ADR Future-direction, etc.) and create a `captured`-labeled GitHub Issue for each, then immediately invoke `/promote-to-backlog <N>` per [ADR-0008](../../../../decisions/0008-workflow-autolog-bootstrap-and-naming.md) D3 inline-firing convention so the autopilot's `backlog-critic` triages the item. Use `gh issue create --label captured --title "..." --body "..."`. The body briefly captures the item, the grill context where it surfaced, and optionally a link to the motivating ADR section.

## Handoff summary (grilled 2026-06-30)

When every needs-decision fork from the opening triage has an answer, close the session with a single handoff summary — this is the session's final output, after the captured-tier sweep above has run:

1. **Restate the triage table** (just-do vs needs-decision, unchanged from the opening step, so the reader has the full queue in one place).
2. **List every fork-decision** made during the session, keyed by its stable `Q<n>` number, with the chosen option and the one-line reason.
3. **Frame it as ready to feed an autonomous `/ship` run** — the just-do items plus the now-decided needs-decision items are both actionable; the summary is the input an autonomous run consumes. When the settled items are a queue rather than one feature, name the real entry point: `/ship`'s **Queue-drain entry mode** ([ADR-0085](../../../../decisions/0085-queue-drain-mode.md) D1), whose "drain plan" sub-form replays this triage against the live queue before anything is dispatched.
4. **Do NOT auto-launch the build.** The operator reviews the handoff summary and decides when (and whether) to kick off the run — that review checkpoint stays with the operator, it is not automated away by this skill.
