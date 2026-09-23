---
id: "ADR-0088"
title: "Dashboard frontend retired: append-only logs read on demand are the observability surface"
status: "accepted"
date: "2026-09-23"
scope: "pipeline"
rule_ids: []
supersedes:
  - "ADR-0078 D1"
  - "ADR-0078 D2"
  - "ADR-0080 D1"
  - "ADR-0081 D4"
  - "ADR-0062 D3"
  - "ADR-0068 D1"
  - "ADR-0068 D3"
  - "ADR-0071 D5"
  - "ADR-0034 D4"
  - "ADR-0034 D7"
  - "ADR-0039 D2"
  - "ADR-0047 D2"
  - "ADR-0037 D2"
  - "ADR-0061 D1"
superseded_by: []
---

# 0088 — Dashboard frontend retired: append-only logs read on demand are the observability surface

- **Status:** Accepted
- **Date:** 2026-09-23
- **Supersedes:** ADR-0078 D1, ADR-0078 D2, ADR-0080 D1 (partial), ADR-0081 D4 (partial), ADR-0062 D3 (partial), ADR-0068 D1 (partial), ADR-0068 D3 (partial), ADR-0071 D5 (partial), ADR-0034 D4 (partial), ADR-0034 D7 (partial), ADR-0039 D2 (partial), ADR-0047 D2 (partial), ADR-0037 D2 (partial), ADR-0061 D1 (partial) — each clause is scoped exactly in the Supersession ledger below
- **Extends:**
  - ADR-0083 D3: a check may only FAIL a subject on an invariant it owes. Applied by D6 to checks whose subject is deleted.
  - ADR-0075 D2: the append-only ledger and disposable read-model. Unchanged; D2 below builds the observability surface on top of it.
  - ADR-0047 D3: CHECK 7. Extended by D4 with a single-definition assertion.
  - ADR-0004 D2: bootstrap-mode.
- **Ships with:** PRD "Retire the dashboard frontend — logs + LLM become the observability surface", slice 1 (ADR-0003 D8).

**Bootstrap-mode (ADR-0004 D2):** every decision here takes effect from the merge of its PRD's slice 1. Two of them change enforcement: D3 (CHECK 2 fails closed) and D4 (CHECK 7 allows exactly one roster definition). The PR that ships them runs them in its own CI, so the slice that introduces them is also gated by them. History is not rewritten: `decisions/`, `qa-proof/` and `docs/decision-log/` keep their dashboard-era text as accurate records of what was true when written.

## Context

The project has had a served dashboard since PRD #345: `dashboard/server.py` on `localhost:8765` and `dashboard/index.html`. It was spawned on every SessionStart by `.claude/hooks/dashboard-autostart.sh`, under the tooling-spawn category ADR-0033 D1 created for it. It was supervised by `tools/dashboard-up.ps1`, `tools/dashboard-up.sh` and `tools/restart-dashboard.sh`. ADR-0078 made the run-board its landing view. ADR-0080 then cut it down to that one tab plus a health strip. ADR-0080 also considered deleting the frontend entirely and rejected that on four grounds: 16 coupled test files, the PIP-022 mandate, the qa-tester browser-proof contract, and the `/api/meta` consumers.

On 2026-09-23 the operator overruled that boundary directly: "delete the frontend on http://localhost:8765/. I am not using it at all. Delete this whole idea of having frontend for checking the data … I would rather use logs and just check it using llm or something like that." The same directive dropped the replacement the plan had scheduled, a single status CLI (Block 5). So this ADR does not replace one UI with another. It retires the idea of a UI.

Removal is not a single deletion, because the serving layer had picked up jobs that CI, hooks and prompts depend on. Each needs a new home or needs to be retired honestly:

1. **The critic roster.** `KNOWN_CRITICS` lives at `server.py:374` and CI CHECK 7 parses it as text. Two private copies are held in step only by comments (`discovery.py:42`, `health.py:224`; issues #1257 and #1465).
2. **The README generator entrypoint.** `server.py --generate-readme` is called by the pre-commit hook, by CHECK 2, by reviewer R-DOCS-CURRENT and by `/ship` step 5g. CHECK 2 and the pre-commit hook both skip silently when that file is missing.
3. **Proof routing.** The route table maps `dashboard/**` to the browser proof class. After the deletion every surviving file under `dashboard/` is a command-line library. Keeping the mapping would demand DOM screenshots of a UI that no longer exists, and PROOF-INTEGRITY would hold RELEASE-READY condition (c).
4. **Probes and checks whose subject is gone:**
   - STALE-SERVER, which today FAILs on SReality's separate copy squatting port 8765 (incident #1184, closed).
   - DEAD-ROUTES.
   - The `session-start.sh` dashboard probe.
   - The `/api/meta` smoke in `/ship`.
   - The qa-tester's server start/restart steps.
5. **The always-loaded rule set.** PIP-022 tells every session the run-board is live. DOC-001 names the old generator entrypoint. VER-002 names `dashboard/*` as the browser UI.

## Decisions

### D1 — No served dashboard: no process, route, page, autostart, probe or launcher remains

The following are deleted:
- `dashboard/server.py` (HTTP handler, routes, `webbrowser` launch, server identity) and `dashboard/index.html`.
- `.claude/hooks/dashboard-autostart.sh` and its SessionStart registration.
- The three launcher scripts.
- `lib-root.sh::dashboard_probe_identity()`. `MAIN_ROOT` and `LOG_DIR` stay.
- The `DASH_FRESH` / `Dashboard:` field in `session-start.sh`'s injected context.
- `/ship`'s dashboard pre-step and its post-merge `/api/meta` smoke steps.
- The qa-tester's dashboard-specific server start/restart/handshake steps.

Library modules are removed wherever the import graph shows no remaining non-test importer: `dashboard/events.py` (already orphaned) and `dashboard/workitems.py`. Every module that CI, `tools/`, a hook, or another surviving module imports stays, and stays under the directory name `dashboard/`. Renaming the directory is deferred to the planned `pipeline/` move so it happens once. Nothing replaces the served surface: no UI, no headless API, no status CLI.

**Enforcement:**
- **The cut:** the one-time sweep in the PRD's criteria 1, 2 and 20 (zero serving code, zero references outside history directories). CI CHECK 1 and CHECK 27 re-derive the hook set from `settings.json` and `.claude/hooks/*` on every PR.
- **Re-growth:** no new standing check is added. Re-introducing a hook-spawned server needs an ADR-0033 D1 amendment (the category's own admission rule). `.claude/settings.json` and `.claude/hooks/**` are guardrail paths whose promotion waits for the operator (ADR-0070 D4). The existing mechanisms already gate the only way back in, so a new check would duplicate them (parsimony). This clause is **(advisory)** beyond those two gates.
- **Shadow guarded against:** *convenience-viewer re-growth*. A route here and a probe there, until sessions once more depend on a process nobody runs. The 2026-08-20 foreign-listener incident (#1184) showed what that dependence costs.

### D2 — Observability is the append-only logs under `.claude/logs/`, read on demand by an LLM session, indexed by one short doc

`docs/observability.md` (≤ 80 lines) is the single index.
- **Per log:** for each append-only log a tracked writer emits (`hook-fires.jsonl`, `workflow-events.jsonl` with its rotations and side-streams, `trace-v3.jsonl`, `drain/<run-id>.jsonl`, `subagent-edits.log`) it gives the writer, the question the log answers, and the existing query helper, if any. The helpers are `python3 tools/trace.py path --pr <n>`, `python3 dashboard/tracestore.py running|runboard`, and `python3 dashboard/health.py --check <id>`.
- **Log root:** how to resolve it from a linked worktree (`git rev-parse --git-common-dir`).
- **Liveness rule:** the lesson of slice #1054, which previously lived only inside the deleted `_build_status()`. On resumed sessions `workflow-events.jsonl` can sit idle for days while `hook-fires.jsonl` beacons keep flowing, so liveness is judged from the beacon stream, never from one log alone.
- **Always-loaded footprint:** CLAUDE.md §4's "Workflow dashboard" row is replaced by exactly one row pointing at the doc. The existing trace-ledger and drain-ledger rows stay, and the doc points to them rather than repeating them (rule #9).

`_build_status()` is not moved. Once the server is gone it has no consumer, and its only hard-won content is the prose rule above.

**Enforcement: (advisory).**
- **Why not deterministic:** keeping the doc current as new log families appear is a prose obligation. The repo has no registry of log writers for a check to compare against, and building one would be new machinery for an index that an LLM reads. This decision rejects that on parsimony.
- **Existing backstop:** `codebase-critic`'s per-PRD semantic doc-currency pass (PIP-012 (1)) is the judgment check for a PRD that adds a log.
- **What landing proves:** PRD criteria 21–24 check the doc's shape once, and its production check (e) proves the surface answers a real question.
- **Shadow guarded against:** *index rot*. A new log ships without an entry, and an LLM session answering from the doc confidently misses it.

### D3 — The README generator entrypoint is `python3 dashboard/readme_gen.py`, and CHECK 2 fails closed when the generator is missing

`dashboard/readme_gen.py`, which already holds `generate_readme()` and `render_pipeline_mermaid()`, gains a `__main__` block. Every caller is retargeted in the same PR that deletes `server.py`: `.githooks/pre-commit`, CI CHECK 2, reviewer R-DOCS-CURRENT, `/ship` step 5g, CLAUDE.md's README row, and DOC-001's statement string.

CHECK 2's missing-generator branch changes from `SKIP` to FAIL. Its python3-missing soft-degrade, which every check shares, is unchanged. The pre-commit hook keeps its fail-open local policy (ADR-0034 D6 stands). CHECK 2 and R-DOCS-CURRENT (ADR-0034 D5) remain the gates of record. README-as-build-artifact (ADR-0034 D4's substance) is untouched. Only the command that produces it moves.

**Enforcement:**
- **Mechanism:** CHECK 2 itself. This modifies an existing check's degrade branch rather than adding a mechanism (parsimony).
- **Shadow guarded against:** *silent under-verification*. A moved or deleted generator turns the README gate into a `SKIP` that CI reports as green.
- **The trap in concrete terms:**
  - Deleting `server.py` does not make the repo impossible to commit to. The pre-commit hook warns and exits 0 (`.githooks/pre-commit:99-104`), and that early exit also skips the advisory secrets gate after it.
  - CHECK 2 prints `SKIP` (`tools/ci-checks.sh:43-61`).
  - So README currency would stop being verified while every signal stayed green. That is why the retarget and the fail-closed branch land together.

### D4 — The critic roster has exactly one definition, `dashboard/_constants.py`, and CHECK 7 enforces that

`KNOWN_CRITICS` moves to a new `dashboard/_constants.py`. The module imports nothing, so no sibling can ever form an import cycle through it (the home #1465 proposed). `discovery.py` and `health.py` import it in place of their private copies, and the redundant count comment is dropped (#1257).

CHECK 7(a) parses the new file. It also gains one assertion: exactly one `KNOWN_CRITICS` / `_KNOWN_CRITICS` definition exists under `dashboard/` and `tools/`.

This is forced by D1: the canonical copy's file is deleted, so a new home must be chosen anyway, and choosing one of the three while keeping the other two would only rename the triplication.

**Enforcement:**
- **Mechanism:** CHECK 7(a), extended rather than joined by a new check (the ADR-0046 D1 parsimony principle, applied to CI mechanisms). The roster's accuracy was already CHECK 7's job (ADR-0047 D3); only its single-sourcing is new.
- **Shadow guarded against:** *comment-synchronised copies*. Three literals kept equal only by comments and by a human remembering to list all of them in an ADR ledger. That is the risk ADR-0081's own Propagation had to hand-enumerate when the roster went from 7 to 6.
- **What it closes:** #1257 and #1465, and the residual copies #1458 names. #1458's workflow-disposition lesson is out of scope.

### D5 — `dashboard/**` leaves the browser proof class and routes as command-run; the browser route stays for browser-reachable UIs

The changed-path route table maps `dashboard/**` to command-run (output plus exit codes), in both the qa-tester prompt table and `health.py`'s `_ROUTE_TABLE`. After D1, nothing under `dashboard/` is browser-reachable.

The browser proof class, its live and headless drivers (ADR-0074 D1) and `*.html → browser` stay for host projects that ship a UI. The browser-reachable-UI → drive-the-running-app rule is unchanged; only its sole in-repo instance is gone.

The change must land no later than the first slice that touches `dashboard/**`, meaning slice 1. The classifier re-scores PRs against the current table at evaluation time. Left unchanged, every slice of this PRD would count as browser-route. PROOF-INTEGRITY would then FAIL them for lacking DOM `inner_text:` evidence that cannot exist, and RELEASE-READY condition (c) would hold promotion.

**Enforcement:**
- **Mechanism:** the route table remains the single routing authority (ADR-0061 D1 stands in substance). PROOF-PRESENCE and PROOF-INTEGRITY consume `_ROUTE_TABLE` on every evaluation.
- **Parity gap, kept advisory:** keeping the qa-tester table in step with `_ROUTE_TABLE` is not mechanically checked. That gap pre-dates this ADR, and closing it is not this ADR's theme.
- **Shadow guarded against:** *phantom proof mandate*. A proof class demanded for a surface that no longer exists, producing permanent PROVISIONAL verdicts or a false promotion hold.

### D6 — Checks, probes and hook subjects whose subject is deleted are retired, not left to WARN forever

STALE-SERVER and DEAD-ROUTES leave the health registry, along with their group-map entries. Under ADR-0083 D3 a check may only FAIL a subject on an invariant that subject owes, and after D1 no process or route owes one. STALE-SERVER in particular FAILs today on a foreign listener, which is by definition not this repo's subject.

The hook-integrity subject sets need no code change. HOOK-INTEGRITY, STREAM-LIVENESS and CI CHECK 27 already derive their subjects from `.claude/settings.json` and the `.claude/hooks/*` glob (ADR-0083 D3's "defined, not assumed" subject set), so `dashboard-autostart` drops out when its file and registration go. Old beacons carrying that label are no longer a subject and are not scored. Test fixtures that borrowed the name for a synthetic session-scoped stream are renamed.

**Enforcement:**
- **Mechanism:** ADR-0083 D3 (VER-009) is the governing invariant. `python3 dashboard/health.py --list` is the observable (PRD criterion 13). No new mechanism.
- **Shadow guarded against:** *orphan checks*. Rows that keep reporting on a deleted subject, first as noise and then as false FAILs on whatever process happens to hold the port.

## Supersession ledger (clause-exact)

"Partial" means only the named clause falls. Every other clause of that decision stands. Partially superseded ADR files are not edited and keep `superseded_by: []` (the per-decision precedent recorded in `tools/gen_rules.py`'s baseline comment). Only ADR-0078 is superseded in full.

**Superseded in full:**
- **ADR-0078 D1:** the run-board landing view, `/api/runboard`, and its reader-only and production-check clauses. The subject is deleted (D1).
- **ADR-0078 D2:** the deferred desktop shell and its "adopting the shell later is a wrapping exercise" migration path. There is no board to wrap, and the operator withdrew the end-state.
- **Mechanics:** ADR-0078's frontmatter flips to `status: "superseded"` and `superseded_by: ["ADR-0088"]`, and its Status line flips. This is the legal mechanical metadata flip. As a result PIP-022 leaves the active rule set.

**Partial:**
- **ADR-0080 D1.** Falls: the served-UI clauses:
  - the dashboard UI is the run-board plus a thin health strip;
  - the `/api/meta`, `/api/runboard` and health-strip-payload members of its preserved-surfaces clause (README generation, `tracestore.py` and `collector.py` in that clause stand);
  - enforcement items (a) DEAD-ROUTES stays PASS and (c) the qa-tester screenshots `/`.

  Stands:
  - the four-module deletion and enforcement (b);
  - its supersession of ADR-0075 D6 and the HOSTED-CI-REAL verdict-presence gate (enforcement (a0), today's CI CHECK 23);
  - enforcement (d), suite green with subject tests deleted alongside their subjects;
  - the retirement of the architecture view in favour of `_repo-map.md`.
- **ADR-0081 D4.** Falls: absorbed step 1 (the dashboard-check pre-step) and the "dashboard-autostart invocation" grep anchor in its enforcement clause. Stands: steps 2–3 (step 3's command relocates under D3 here), `/ship` as the single lifecycle orchestrator, and the remaining anchors.
- **ADR-0062 D3.** Falls: the "plus a dashboard smoke (the `/api/meta` SHA handshake)" clause. Stands: the green pointer, `main_green` / `develop_green` recording, and the suspect-set revert. D3 had already been narrowed by ADR-0079 D1, which removed its `ci-checks.sh` re-run.
- **ADR-0068 D1.** Falls: the "dead API surface count" registry check (DEAD-ROUTES). Stands: the other hygiene checks and log rotation.
- **ADR-0068 D3.** Falls: "dashboard freshness" in the injected-context list. Stands: the rest of the hook and its measurement.
- **ADR-0071 D5.** Falls: the stale-server auto-restart bullet and STALE-SERVER's registration. Stands: UTC beacons, HOOK-LIVENESS and RULE-COVERAGE.
- **ADR-0034 D4.** Falls: the bullet that says the dashboard renders the same generator output. Stands: README as a build artifact.
- **ADR-0034 D7.** Falls: the clause naming `dashboard/server.py --generate-readme` as the generator. Stands: the generator is a tooling artifact, non-runtime, with no LLM calls.
- **ADR-0039 D2.** Falls: its dashboard-topology bullet and its naming of `dashboard/server.py --generate-readme`. Stands: the README mermaid is generated from the single spec (`pipeline_spec.py`).
- **ADR-0047 D2.** Falls: the bullet rendering the dashboard's pipeline mermaid via `/api/pipeline`. Stands: every other bullet. The first bullet's substance (render the README mermaid from the spec) is unchanged; the function it names already lives in `readme_gen.py`.
- **ADR-0037 D2.** Falls: the parenthetical binding of the browser route to `dashboard/*`. Stands: routing by change type, all four routes, and browser-reachable-UI → drive-the-running-app.
- **ADR-0061 D1.** Falls: the example row `dashboard/** → browser`. Stands: the table as single routing authority, the union rule, and every other row.

## Left standing, explicitly (not superseded)

- **ADR-0033 D1–D6.** The tooling-spawn hook category and its four criteria stand, and HOK-006 / HOK-007 stay active. After this ADR no in-repo hook uses the category; a host project may, through D1's own ADR-amendment rule. D4 (`dashboard/*` is non-runtime for R-LOC) keeps governing the surviving modules.
- **ADR-0034 D5, D6, D10.** R-DOCS-CURRENT, the fail-open pre-commit catch, and the generator-vs-spawn distinction stay. D10's distinction remains true; its spawn side simply has no instance.
- **ADR-0061 D2.** `ENV:` validation for browser routes names the `/api/meta` identity contract. With no in-repo browser class after D5 it is dormant here. Making it generic for host UIs is left to the host-universality work.
- **ADR-0058 D4.** Sandbox teardown stays: any agent that starts a server kills it.
- **ADR-0058 D5.** The isolation group is a `dashboard/health.py` registry group and remains readable via `--check ISOLATION-GROUP`.
- **ADR-0050 D2.** Its driver procedure navigates `localhost:8765` "(or the declared URL)"; only the declared-URL branch remains.
- **ADR-0070 D5.** PROOF-INTEGRITY stays. With no in-repo browser-route PRs it reports an honest no-data WARN, which RELEASE-READY (c) already treats as pass.
- **ADR-0075 D2.** The ledger and read-model stay; `tracestore.py` and its CLI survive.
- **ADR-0080 D2.** The batch-plan retirement and PIP-024 stay. PIP-024's rendered statement drops its board-panel clause because the board it described is gone; nothing that decision decided is reversed.
- **ADR-0081 D2** (the roster of 6) and **ADR-0047 D3** (CHECK 7) stand. Only the roster's parse target moves (D4).
- **ADR-0054 D4.** Environment freshness stays. Its dashboard-restart wording is an example.
- **ADR-0039 D1.** The single pipeline spec stays.

## Consequences

- **Every session gets lighter.** No process spawn at SessionStart, no port probe, no foreign-listener class. Port 8765 is no longer this repo's concern; SReality's separate copy may keep it.
- **Capabilities lost, accepted knowingly:**
  - The glanceable now/recent view. The equivalent reads are `python3 dashboard/tracestore.py runboard` and an LLM reading `trace-v3.jsonl`.
  - The health strip. The replacement is `python3 dashboard/health.py --check <id>` / `--list`.
  - The session-start "Dashboard:" line.
  - The desktop-shell end-state.
- **Lighter tests and CI:** about 23 coupled test files are deleted with their subjects or narrowed, and two registry rows retire. RELEASE-READY's six conditions are unchanged.
- **Not removed, and captured as follow-ups:** library-level serving shims inside surviving modules (`health.serve_health()`, `tracestore.serve_trace_runs()` / `serve_runboard()` and their warm caches). They have surviving test consumers, and module-level removal was the rule applied here. Also captured: the pre-commit hook's early `exit 0` on a missing generator, which also skips its advisory secrets gate.
- **PROOF-PRESENCE re-scoring:** it evaluates its recent window with the current table, so it will re-score older dashboard-era PRs as command-run. Some may lack `exit=` tokens (a transient WARN; it feeds no promotion condition). The PRD leaves grandfathering them as an open question.
- **Rendered rules:** PIP-022 leaves. PIP-024, DOC-001 and VER-002 keep their ids with corrected statements. `RULE_IDS_BASELINE` drops by one. ADR-0088 adds no rule_ids: D2's obligation is advisory, and D3–D6 run on existing checks.
- **Operator ack:** promotion of this batch touches guardrail paths and needs `.claude/PROMOTE_OK` (ADR-0070 D4).

## Alternatives considered

- **A single status CLI** (`tools/status.py --json`, plan Block 5), or keeping the server headless (API only). Rejected by the operator on 2026-09-23 in favour of logs + LLM. The existing `tools/trace.py`, `tracestore.py` and `health.py` CLIs already answer every question the board did. A wrapper would be a second surface to maintain.
- **Move `_build_status()` to a parking module** (the prior draft's `dashboard/status.py`). Rejected (YAGNI): it has no consumer. Its one lesson is kept as prose where the LLM reader needs it (D2).
- **Keep `KNOWN_CRITICS` in `readme_gen.py`** (the prior draft) or in `discovery.py`. Rejected. `readme_gen.py` does not use the roster, and either choice keeps two private copies alive. `discovery.py` would work mechanically, but `_constants.py` imports nothing, so no sibling can form a cycle through it, and it is the home the open issue proposed.
- **Leave `dashboard/** → browser` and let those PRs go PROVISIONAL.** Rejected: that is a permanent phantom mandate, and PROOF-INTEGRITY FAILs would hold promotion (D5).
- **Keep STALE-SERVER as a port-squatter detector.** Rejected under ADR-0083 D3. It would FAIL on a process this repo does not own and does not run.
- **Make the pre-commit hook fail closed too.** Rejected: ADR-0034 D6 deliberately keeps the local layer fail-open, and CHECK 2 plus R-DOCS-CURRENT are the gates of record.
- **Rename `dashboard/` now.** Deferred: the planned `pipeline/` move would make it a second rename.
- **Delete only `index.html` and keep the server for its CLI entrypoints.** Rejected: that keeps the whole spawn/probe/squatter apparatus to serve two functions that belong in libraries.

## Propagation

Hits for every superseded ADR number across the runtime prompt surface (`.claude/agents/`, `.claude/skills/`, `.claude/settings.json`), re-derived at `origin/develop` c221167, with a per-file disposition. Line numbers are approximate and are re-derived at implementation.

- **ADR-0078, ADR-0068, ADR-0071, ADR-0039, ADR-0047:** zero hits on the runtime surface.
- **ADR-0080:** `.claude/agents/reviewer.md` (:310, R-FIXTURE). **update-in-this-wave**: drop the "Exemption: `dashboard/server.py` reading `.claude/logs/`" sentence, since its subject is deleted. The same paragraph's ADR-0080 D2 citation (batch-plan pruning) is **grandfather**: it cites a clause that stands.
- **ADR-0081:**
  - `.claude/skills/ship/SKILL.md`: **update-in-this-wave**. Delete "Pre-step — Ensure dashboard running" (D4 step 1), and retarget 5g's command (D4 step 3, about :331) to `python3 dashboard/readme_gen.py`. The citations at :33, :235 and :453 are **grandfather** (D4's surviving clauses).
  - `.claude/agents/backlog-critic.md` (:14), `.claude/agents/qa-tester.md` (:33, D1), `.claude/skills/grill-me/SKILL.md` (:53), `.claude/skills/qa-plan/SKILL.md` (:8, :66): **grandfather**. They cite D1/D2/D3, which stand.
- **ADR-0062:**
  - `.claude/skills/ship/SKILL.md`: **update-in-this-wave**. Delete step 1 (`/api/meta` SHA smoke) in 5c (about :306–307) and 5f (about :315), and delete 5f item 5's "SHA-smoke failure" wording. The D3 citation at about :310 and the :79 citation are **grandfather**.
  - `.claude/agents/reviewer.md` (:310 `main_green` exemption, :459 D1, :483 D2, :485): **grandfather**. Those clauses stand.
- **ADR-0034:**
  - `.claude/agents/reviewer.md`: **update-in-this-wave**. R-DOCS-CURRENT's command and message (about :286, :292) move to `python3 dashboard/readme_gen.py`. The :273 (D5) and :298 (D9) citations are **grandfather**.
  - `.claude/skills/ship/SKILL.md` (:15, :235, :247): **grandfather** (D2/D3).
- **ADR-0037:**
  - `.claude/agents/qa-tester.md`: **update-in-this-wave**. The route table row (about :268) and the multi-glob example (about :278) change. The dashboard-specific browser steps go: the example URL (about :60), the live L1 handshake and the 8765 noise filter (about :301–311), the headless `/api/meta` preamble and the server start/restart (about :331–344), assertion (F) (about :486), and the dashboard mention in the `ENV:` field (about :537). The generic browser route stays and navigates to the URL the Production check line declares. The D1/D2/D3 substance citations at :3, :16, :22, :112, :208, :284, :430, :515, :551 and :572 are **grandfather**.
  - `.claude/agents/prd-critic.md`: **update-in-this-wave**. The PC-PRODUCTION-CHECK example (about :192) and PC-LIVE-FEED's "dashboard data from server.py" example (about :198) are replaced with non-dashboard examples. The D4 citations at :175, :179, :186, :190 and :283 are **grandfather**.
  - `.claude/skills/to-prd/SKILL.md`: **update-in-this-wave**. The Production-check example (about :54) is replaced with a non-dashboard example.
  - `.claude/skills/qa-plan/SKILL.md` (:3, :10, :34, :65) and `.claude/skills/ship/SKILL.md` (:339, :348, :371, :376, :402, :412, :444): **grandfather** (D1/D3/D5 stand).
- **ADR-0061:**
  - `.claude/agents/qa-tester.md`: **update-in-this-wave** for the route table only, as above. The D2/D3/D4/D5 citations are **grandfather**.
  - `.claude/skills/ship/SKILL.md` (:352, :361, :362, :367–370): **grandfather-with-reason**. The D2/D5 clauses stand, and the browser-route `ENV:` validation is dormant in-repo after D5 (its host generalisation is out of scope).

Beyond the runtime surface (edits and grandfathers both listed, following the ADR-0080/0081 pattern):

- **`tools/gen_rules.py` statement strings**, edited and regenerated. CHECK 17 gates their freshness; the per-D-ID channel gap remains #1194.
  - **DOC-001** → "`README.md` is a build artifact: `python3 dashboard/readme_gen.py` reads `README.template.md` + filesystem, writes `README.md`; the file MUST NOT be hand-edited — always regenerate (ADR-0034 D4; generator entrypoint relocated by ADR-0088 D3)."
  - **PIP-024** → its board-panel clause is dropped: "… (zero recorded spans, verified); the verb returns only via a new ADR gated on two `captured` issues … (ADR-0080 D2; its board-panel clause lapsed when ADR-0088 retired the board)."
  - **VER-002** → "`qa-tester` in production-verify mode auto-routes by change type: browser-reachable UI → browser route; hooks/settings → synthetic-payload fire + log assertion; skills/tools/`dashboard/**` → command run + output assertion; docs/ADRs → static grep (ADR-0037 D2, as amended by ADR-0088 D5)."
  - **PIP-022's dict entry** stays but no longer renders (the SLI-004/005 precedent after #1464).
  - **`RULE_IDS_BASELINE`:** 92 → 91 (PIP(29) → PIP(28)), measured from the baseline on `develop` at slice 1's base.
  - **Docstring (:5)** is reworded.
- **`decisions/README.md`:**
  - ADR-0088's own row.
  - ADR-0078's Status becomes "Superseded in full by ADR-0088", the phrase DOCS-11 parses.
  - "D<n> (partially) superseded by ADR-0088" annotations on the rows for 0034, 0037, 0039, 0047, 0061, 0062, 0068, 0071, 0080 and 0081, so DOCS-8 can police the `- **Supersedes:**` line above.
- **`CLAUDE.md` §4:** the "Workflow dashboard" row (cites ADR-0078 D1 / ADR-0080 D1) is **replaced** by one "Observability" row pointing at `docs/observability.md`. The README row's command moves to the new entrypoint.
- **Regenerated:** `README.md`, `.claude/generated/_global.md`, `.claude/rules/docs.md`, and `.claude/generated/_repo-map.md` (its `dashboard/` description comes from `tools/gen_repo_map.py`, which is edited).
- **Comment-level citations of ADR-0078 D1** in `dashboard/tracestore.py` (about :97, :528, :541) and `tests/test_runboard_staleness_1173.py` (about :110): **reworded** to history in the doc sweep. They are code comments, not authority claims on the runtime surface.
- **Grandfathered as history:** every `decisions/`, `qa-proof/` and `docs/decision-log/` mention.

## References

- Operator directive, 2026-09-23 (verbatim in Context). Plan Blocks 4, 5 (dropped) and 6 (the `pipeline/` move).
- ADR-0078 (D1, D2 superseded in full). ADR-0080 (D1 partial; D2 stands).
- ADR-0033 (D1 category and D4 non-runtime, both stand). ADR-0034 (D4/D7 partial; D5/D6/D10 stand).
- ADR-0037 D2 and ADR-0061 D1 (partial; D2 stands). ADR-0039 D2 and ADR-0047 D2 (partial). ADR-0062 D3 (partial). ADR-0068 D1/D3 (partial). ADR-0071 D5 (partial). ADR-0081 D4 (partial).
- ADR-0083 D3, ADR-0075 D2, ADR-0070 D4/D5, ADR-0054 D6, ADR-0046 D1, ADR-0056 D2, ADR-0064 D2, ADR-0004 D2, ADR-0003 D8.
- Issues: #1257 and #1465 (roster triplication, closed by D4). #1458 (root-cause capture whose residual D4 resolves). #1459 (the prior draft's round-3 stop). #1184 and #1054 (closed incident and slice, cited as history). #1194 (gen_rules per-D-ID gap, open). PR #1476 (open; adds PIP-030; merge-order note in the PRD).
