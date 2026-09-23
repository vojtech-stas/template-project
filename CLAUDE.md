# project-claude — agent rules

This file is auto-loaded by Claude Code on every session in this repo. It contains the rules of the road for AI agents working here, plus a map of where things live. Read it first; refer back to it when unsure.

---

## 1. Cross-cutting constraints (apply to every action you take)

> **Generated atomic rules (ADR-0073 D1)** — `tools/gen_rules.py` renders ADR frontmatter into `.claude/generated/_global.md` (always loaded, via the @import at the foot of this file) and into path-scoped `.claude/rules/<scope>.md` files (loaded only while editing matching paths). Never hand-edit either output; run `python tools/gen_rules.py` after any ADR frontmatter change.

1. **YAGNI — rule #1.** Never add code outside the current slice's scope. If you feel the urge to add something "while you're here", STOP and ask the user. (Reviewer's first enforcement job.)
2. **Walking-skeleton mindset — rule #2.** Smallest end-to-end version first; iterate on the weakest stage. Never build a primitive perfectly before the whole pipeline runs.
3. **Build primitives first, orchestrate last — rule #3.** Do not write an orchestrator before the things it orchestrates exist and have been dogfooded.
4. **Two-tier delivery — rule #4.** Agents never push directly to `main`. Agents merge to `develop` via PR; `main` advances ONLY through the deterministic promotion gate (`tools/promote.sh` + `RELEASE-READY`). Branch protection enforces on `develop` (the PR-merge gate); `main` is protected by the promotion gate. The human's only blocking roles: (1) acking guardrail-machinery promotions, (2) grilling future features. **Guardrail-machinery promotion ack:** when a promotion batch touches guardrail paths (`.github/workflows/**`, `.claude/settings.json`, `.claude/hooks/**`, `tools/ci-checks.sh`, `.githooks/**`, `*-critic.md`, or the promotion gate itself), `promote.sh` requires `.claude/PROMOTE_OK` to exist; create it (`touch .claude/PROMOTE_OK`) to ack — `promote.sh` removes it after a successful promotion. Per [ADR-0070](decisions/0070-two-tier-autonomous-delivery.md) D1.
5. **Conventional Commits, tightened — rule #5.** See COM-001, COM-002 in `@.claude/generated/_global.md`. `<type>(<scope>): <subject>` — lowercase subject, ≤72-char cap, `Co-authored-by:` trailer on every agent commit, `Closes #` in PR body not commit subject. (Mechanized by CI CHECK 3; reviewer enforces R-CONV-COMMITS.)
6. **`git log` is the changelog — rule #6.** Don't create a separate CHANGELOG file. Good commit messages do the job.
8. **One PR per slice — rule #8.** One PR per slice (1:1); independent slices may run in parallel; only dependent work serializes. Per [ADR-0036](decisions/0036-worktree-isolation-all-dispatches.md) D1–D3.
9. **DRY for docs — rule #9.** Don't duplicate info. Link/point to where the canonical version lives.
10. **Main-agent meta-output discipline — rule #10.** Main agent never hand-authors ANY tracked file — all edits flow through the PRD/slice/PR pipeline via `/to-prd`, `/to-issues`, `/ship`, an implementer Agent invocation, the trivial-lane (I3), or any other reviewer-gated PR channel. Per [ADR-0009](decisions/0009-discipline-tightening.md) D1 (supersedes [ADR-0004](decisions/0004-bypass-prevention.md) D4's enumerated-path scope).

### Capture discipline

11. **Surface deferred work as captured issues — rule #11.** Every deferred or follow-up item becomes a `captured`-labeled issue; `backlog-critic` filters `captured` → `backlog`. Details: CAP-001..004. (CAPTURE-SHAPE health row.)
13. **Root-cause workflow capture — rule #13.** Every workflow mistake becomes a `captured`+`root-cause` issue naming symptom, root cause and proposed workflow change — never a symptom-only fix. Details: CAP-005..008. (CAPTURE-SHAPE health row.) **Regression rider (ADR-0067 D3):** when a root-cause capture documents a CODE defect, the fixing PR MUST include a regression test that fails before and passes after the fix, committed in that order. (TEST-ORDERING health row.)

12. **Claude Code hooks have five authorized categories — rule #12.** See `.claude/rules/hooks.md` (HOK-001..009): logging, validation, notification (ADR-0015 D2), tooling-spawn (ADR-0033 D1), context injection (ADR-0057 D4). Hooks may NOT auto-invoke skills or subagents — that hard line is preserved across all five categories. (Mechanized by HOOK-INTEGRITY health row.)

15. **Every feature is production-verified before "done" — rule #15.** `qa-tester` must return `PRODUCTION_VERIFY: PASS` before a feature is complete; the gate is blocking and per-feature, not per-slice. Details: VER-001. (PROOF-PRESENCE health row.)

_(Rule #14 RETIRED per [ADR-0032](decisions/0032-workflow-only-architecture.md) D2. Slot explicitly retired; the separate KB layer no longer exists. Future rules may use #15+.)_

16. **Slice-decomposition is the slicer's job — rule #16.** Slice count, boundaries, and the walking-skeleton cut are owned by `slicer` + `slicer-critic`; the grill/PRD-authoring phase settles design + acceptance criteria then hands off — never decide slicing during grill. Per [ADR-0013](decisions/0013-slicer-n3-contract-refined.md) (decomposition contract) + [ADR-0005](decisions/0005-output-shape-and-slicing-methodology.md) D3 (cascade-doc identification at decomposition time).
17. **Skill-vs-subagent litmus — rule #17.** Subagent = isolated-context + handed-a-task + returns-a-result. Skill = the orchestrator's own interactive/orchestrating/multi-step procedure. Only the main agent dispatches subagents; subagents never dispatch subagents. Per [ADR-0038](decisions/0038-skill-vs-agent-rule.md) D1.
18. **Never cite an ADR decision-ID from memory — rule #18.** See `.claude/rules/docs.md` (DOC-005, DOC-006). Before citing `ADR-NNNN D<n>` in any doc, open the cited ADR and verify the `### D<n>` heading. `decisions/README.md` is the canonical index.
19. **Revise the whole flagged class — rule #19.** On a critic BLOCK, fix the ENTIRE flagged defect class, not just the named instance. Round-3 BLOCK is strict-stop — escalate via `needs-human`. Details: CRI-001, CRI-002.
20. **Proof-per-claim in wrap-up summaries — rule #20.** Every "done/verified" claim carries a route-appropriate proof artifact (browser → screenshot + `inner_text:`; hook-fire → log line with `exit=`; command-run → output excerpt with `exit=`; static → `grep count=`), plus its **data source** (real session/PRD/PR id + timestamp, never fixture-patterned) and **environment freshness**. A claim without a checkable artifact is not a valid "done". The orchestrator surfaces each artifact via `SendUserFile` at wrap-up. Per ADR-0037 D3 + ADR-0054 D4; routing detail in VER-002. (PROOF-PRESENCE health row.)
21. **Fixture discipline — rule #21.** Fixture/synthetic data NEVER enters production data stores (`.claude/logs/*`); evidence derived from fixture-tagged data is INVALID. Details: CRI-004. (Reviewer rule R-FIXTURE.)
22. **System skeleton — rule #22.** A feature implementing stage N of a multi-PRD pipeline must demonstrate, in slice 1, one REAL datum traversing stages 1..N in production. Per-PRD walking-skeleton discipline is necessary but not sufficient — the system-level pipeline must be walked end-to-end with real data before any downstream stage ships. Per [ADR-0054](decisions/0054-critic-output-contracts-and-trailer-standard.md) D6. (Enforced at decomposition time by slicer-critic SC-SYSTEM-SKELETON; at PRD gate by prd-critic PC-LIVE-FEED.)
23. **No rule without a check — rule #23.** Every NEW numbered rule, ordering convention, or orchestrator posting obligation introduced after [ADR-0056](decisions/0056-no-rule-without-a-check.md) MUST ship, in the same PR, with a deterministic enforcement mechanism — an output-contract field, a hook validation, a CI grep (`tools/ci-checks.sh`), a pre-commit check, or a dashboard evaluator (health check or trail evaluator). A rule whose enforcement is genuinely impossible or not yet worth building MUST be tagged `(advisory)` in its text. Untagged + uncheckered new rules are a reviewer BLOCK under R-RULE-CHECK. Binds forward per ADR-0004 D2; per [ADR-0056](decisions/0056-no-rule-without-a-check.md).

---

## 2. Naming

**Commits and branches:** follow Conventional Commits (rule #5 above). Branch names: `<type>/<N>-<kebab-summary>` for slices; `hotfix/<issue#>-<kebab-summary>` for trivial lane (I3) — same `<type>/<N>-<kebab-slug>` shape, since `hotfix` is just another `<type>`.

**Issue titles:** Posted PRDs follow the canonical `PRD: <one-line feature summary>` form. Backlog titles are descriptive only — a short noun phrase, no codename prefixes. Session codenames (PRD-A, PRD-B, …) are conversation shortcuts only and never appear in tracked titles. On promotion `backlog` → `prd`, the title is rewritten into `PRD:` form. Per [ADR-0008](decisions/0008-workflow-autolog-bootstrap-and-naming.md) D5.

---

## 3. Hierarchy + workflow conventions

### PRD → Slice → PR (3-tier)

Per [ADR-0003](decisions/0003-autonomous-pipeline-with-critics.md) D1, the unit-of-delivery hierarchy is exactly three tiers:

- **PRD** — GitHub Issue, label `prd`. One feature-sized deliverable per PRD. Multi-feature PRDs are a smell.
- **Slice** — GitHub sub-issue under the PRD (linked via the native sub-issue mechanism), label `slice`. One INVEST-shaped vertical, fits in one PR.
- **PR** — one merged change, closes exactly one slice via `Closes #<slice-issue>` in the PR body.

**Labels:**
- Use `prd` and `slice` exclusively for hierarchy. **There is no `feature` label** — the PRD plays that role.
- `trivial` for the trivial lane (see I3 below).
- `needs-human` is applied by the reviewer on round-3 BLOCK escalation (see I5 below).

**Milestones** are reserved for **Releases** (groups of merged PRDs). Not in use yet — left empty until the first release ships.

### Workflow improvements I1–I6

These are load-bearing conventions that supplement the cross-cutting rules. Per PRD #3 §4 and [ADR-0003](decisions/0003-autonomous-pipeline-with-critics.md).

- **I1 — Skills know the hierarchy.** `/to-prd` and `/to-issues` produce/consume the 3-tier hierarchy and the `prd`/`slice` labels (delivered by PRD #3 slices 2 and 3).
- **I2 — Slice-grabbing protocol.** The first agent to run `gh issue edit <slice> --add-assignee @me` owns the slice. The reviewer enforces "one assignee per open slice" — if a second agent grabs an already-assigned slice, reviewer BLOCKs the resulting PR.
- **I3 — Trivial lane.** PRs ≤10 LoC of runtime-artifact diff with no behavior change MAY skip PRD/slice *ceremony* — but the branch still hangs off a tracked issue, typically one filed with the `captured` label specifically so the branch has a number, since `.githooks/pre-commit` requires one for every branch type. Branch: `hotfix/<issue#>-<short-summary>`. Add the `trivial` label to the PR; the reviewer fast-paths it.
- **I4 — Slice size cap & staleness.** Slice PRs cap at **≤600 LoC of runtime-artifact diff** (raised from 300 per [ADR-0077](decisions/0077-ceremony-overhead-reduction.md) D1, operator-directed 2026-08-03). The canonical definition of "runtime artifact" lives in [`.claude/agents/reviewer.md`](.claude/agents/reviewer.md) (rule R-LOC) — do not restate it here. A slice issue open >7 days is marked stale by the reviewer.
- **I4a — Subagent dispatch isolation.** Every `implementer` and `reviewer` dispatch MUST pass `isolation: "worktree"` (ADR-0036). A dispatch result missing `worktreePath` is a dispatch failure — re-dispatch (ADR-0058 D1). After each dispatch run `bash tools/worktree-guard.sh branch-restore <expected>`; after a merge run `root-sync` then `prune` (ADR-0058 D3). The guard is ff-only and loud: diverged branches and unrepaired violations exit non-zero.

- **I5 — Escalation surface.** On round-3 BLOCK, the reviewer applies the `needs-human` label to the PR AND posts a comment on the parent PRD issue summarizing the stuck slice. Humans run `gh pr list --label needs-human` at session start to find what's waiting on them.
- **I6 — Drift detection: three layers.** (1) Per-PR mechanical — `tools/ci-checks.sh` on every PR. (2) Per-PRD judgment — `codebase-critic` fires once at the PRD's closing slice, before that slice's reviewer pass, over the cumulative diff. (3) Whole-repo background — `codebase-critic` in `WHOLE_REPO: true` mode, dispatched non-blocking at `/ship` start once per session; findings become `captured` issues. Rubric for both modes: [`.claude/agents/codebase-critic.md`](.claude/agents/codebase-critic.md). Isolation drift surfaces via the guard's non-zero exits (I4a) + the Health isolation group.

### Prescribed linear flow (slicer mandatory)

The canonical delivery flow for every feature is:

**`/grill-me` (grill-heavy)** → **`/to-prd` (prd-critic gate)** → **`/to-issues` (slicer + slicer-critic — MANDATORY)** → **`/ship` batch**

The slicer step is **mandatory and non-bypassable**. Slices are NEVER hand-created via raw `gh issue create` outside `/to-issues` — doing so bypasses the slicer-critic gate and violates rule #16. This flow is enforced by the slicer-provenance guard: every slice issue from `/to-issues` carries a `Slicer-provenance:` trailer; `tools/check-slicer-provenance.py` (CI CHECK 19) flags any open slice lacking it. This convention ships with its enforcement in the same PR (rule #23).

**Second entry point — the queue drain.** `/ship`'s **Queue-drain entry mode** enters this same flow from the other end: instead of one feature, it assembles the whole open queue, triages each item, and feeds each one into the flow above at its correct stage (`captured` → the captured→backlog autopilot; `backlog` → `/to-prd`; `slice` → implement), escalating the operator-owed minority by label-and-continue rather than stopping. It adds no stage and bypasses no gate — the slicer stays mandatory for every slice it produces. Per [ADR-0085](decisions/0085-queue-drain-mode.md) D1/D2; mechanized by the `DRAIN-LEDGER` health row + CI CHECK 24.

### Meta-rule: critic parsimony

The gate on adding a critic is a **parsimony principle**, not a number: minimize critics; each must earn its place against a concern no existing critic's rubric absorbs; adding one requires an ADR making that case explicit. Default disposition for a critic-shaped problem is "extend an existing critic". Per [ADR-0046](decisions/0046-codebase-critic-and-parsimony-reframe.md) D1.

The project currently runs **6 critics**: `reviewer`, `prd-critic`, `adr-critic`, `slicer-critic`, `backlog-critic`, `codebase-critic`. The glossary critic was retired per [ADR-0081](decisions/0081-post-audit-dead-weight-retirements.md) D2 — parsimony in reverse: `reviewer` already absorbs glossary edits on every PR. `codebase-critic` (ADR-0046 D2) earned its place as the first critic providing per-PRD macro judgment over cumulative codebase change — a concern nothing else absorbs.

---

## 4. Map + Glossary

### Map — where things live

_Note: Each skill and subagent embodies its own practice in its own body file (former rule #7, demoted per [ADR-0043](decisions/0043-claude-md-restructure.md) D5). No separate `docs/practices/` folder._

| Thing | Path | Summary |
|---|---|---|
| Skills, subagents, tools, key dirs | — | enumerated in `@.claude/generated/_repo-map.md` (generated); read it for behaviour. Facts it truncates, restated here: qa-tester's browser route prefers live Claude-in-Chrome MCP when connected, else headless Playwright fallback (ADR-0074 D1-D5, ADR-0049 D3 — scope formalized by ADR-0084); codebase-critic's whole-repo mode (ADR-0051); `/ship` = grill → PRD → slices → implement → auto-merge → docs-regen → production-verify. |
| Settings + Claude Code hooks | `.claude/settings.json`, `.claude/hooks/` | per [ADR-0015](decisions/0015-claude-code-hooks-adoption.md); canonical logger `log-tool-event.sh` |
| Workflow event log | `.claude/logs/workflow-events.jsonl` (gitignored) | v2 JSONL workflow events per [ADR-0016](decisions/0016-workflow-event-log-jsonl.md) |
| Pipeline trace ledger | `.claude/logs/trace-v3.jsonl` (gitignored) | canonical v3 spans. `tools/trace.py` appends + queries (`path --pr <n>`); `dashboard/tracestore.py` folds a disposable SQLite read-model — refoldable from the log, never a second source of truth. Closed kind enum: an unknown kind hard-errors. [ADR-0075](decisions/0075-trace-core-fork-decisions.md) D2/D3 |
| Queue-drain run ledger | `.claude/logs/drain/<run-id>.jsonl` (gitignored) | one append-only JSONL per `/ship` queue-drain run, at the git-common-dir root so worktrees share it. Closed record-kind set (`run_start`/`triaged`/`item_start`/`item_done`/`escalated`/`fix_queued`/`fixed_in_run`/`parked`/`resumed`/`run_end`); durable state across session death and quota parks. Validated offline by `python dashboard/health.py --check DRAIN-LEDGER`. Deliberately separate from trace-v3, which stays the independent dispatch witness. [ADR-0085](decisions/0085-queue-drain-mode.md) D4/D6 |
| Guarded pipeline verbs | `tools/pipe/`, `tools/promote.sh` | the ONLY sanctioned path for mechanical transitions (`dispatch`, `pr-open`, `pr-merge`, `qa-verify`, `prd-close`, `record-green`): precondition check → side effect → atomic span; a refused transition exits non-zero and never half-succeeds. Raw `gh pr merge` is denied by hook. PIP-014..018 |
| Two-tier promotion gate | `tools/promote.sh` | ff `main` to `develop` HEAD; requires RELEASE-READY `verdict="true"` AND the `.claude/PROMOTE_OK` human-ack sentinel (create it manually; the script removes it after success). [ADR-0070](decisions/0070-two-tier-autonomous-delivery.md) D2/D3 |
| CI gate | `.github/workflows/ci.yml` → `tools/ci-checks.sh` | job name `ci` is the required status-check context on `develop`; run the script locally before pushing. Several checks delegate to the health registry — e.g. CHECK 22 runs RECORD-VS-GH, CHECK 23 the verdict-presence guard; CHECK 27 enforces the closed hook beacon-status schema (HOK-008, ADR-0083 D1/D2), resolving both literal emit sites and `beacon()` helper call graphs. [ADR-0042](decisions/0042-github-actions-ci-gate-r4.md) D1 |
| Health check registry | `python dashboard/health.py --check <id>` / `--list` | headless run of any registered check; exit 0 on PASS/WARN, 1 on FAIL. `ci-checks.sh` delegates several checks to it, per ADR-0064 D3 |
| Pre-commit hooks | `.githooks/`, `.githooks/install.sh` | workflow enforcement; `core.hooksPath` must be `.githooks` |
| Deploy-gap handshake | `tools/deploy-handshake.sh`, `tools/repair-topology.md` | compares the RUNNING hooks/settings against the DEPLOYED branch; mismatch, detached HEAD, or wrong `core.hooksPath` → loud banner + exit 1 |
| Decisions (ADRs) | `decisions/NNNN-<slug>.md`; index `decisions/README.md` | immutable — supersede rather than edit. Consult the index before citing a D-ID (rule #18) |
| Operator decision log | `docs/decision-log/` | dated per-problem records of operator decisions, append-only |
| In-flight work | GitHub Issues + branches | `gh issue list` ; `git branch` |
| Backlog / captured | `gh issue list --label backlog` / `--label captured` | project board #2; `backlog-critic` filters `captured` → `backlog` |
| Observability | `docs/observability.md` | index of the append-only logs under `.claude/logs/`, read on demand by an LLM session — the served dashboard's replacement. [ADR-0088](decisions/0088-dashboard-frontend-retired.md) D2 |
| README | `README.template.md` → `dashboard/readme_gen.py` | `README.md` is a build artifact — never hand-edit, always regenerate (DOC-001, ADR-0034 D4/D7, entrypoint relocated by ADR-0088 D3) |
| Regression tests | `tests/`, `tests/quarantine.txt` | pytest, wired into CI; flaky tests quarantined within 24 h with a 30-day SLA. [ADR-0067](decisions/0067-regression-memory.md) |
| Fresh-clone setup | `bootstrap.sh` | per [ADR-0008](decisions/0008-workflow-autolog-bootstrap-and-naming.md) D6 |

---

### Glossary (key terms)

Auto-loaded project vocabulary. Soft cap ~35 entries per [ADR-0012](decisions/0012-glossary-consolidation-single-tier.md) D5 (amended by [ADR-0081](decisions/0081-post-audit-dead-weight-retirements.md) D2), checked by DOCS-9. To add a term: edit this section in a normal reviewer-gated PR, keeping the entry shape and scope categories of [ADR-0007](decisions/0007-vocabulary-glossary-and-grill-me-extension.md) D2/D3.

- **ADR** — Architecture Decision Record; immutable, supersession-based numbered file in `decisions/`; never edited, only superseded by a newer ADR.
- **backlog** — curated forward work queue of `backlog`-labeled GitHub Issues + project board #2 Backlog column; filtered from the `captured` tier by `backlog-critic`.
- **bootstrap-mode** — new conventions bind FORWARD from the slice that ships them; no retroactive sweep of existing artifacts; prior state is grandfathered.
- **cascade-doc check** — the slicer's responsibility to identify docs that should update to reflect a new feature even when not strictly required by acceptance criteria; a formal slicer responsibility per ADR-0005 D3.
- **Conventional Commits** — `<type>(<optional scope>): <subject>` commit format; tightened here with lowercase subject, ≤72-char cap, and mandatory `Co-authored-by:` trailer on every agent-authored commit.
- **critic** — adversarial subagent that gates another pipeline stage's output via an APPROVE/BLOCK verdict; never edits the artifact it reviews.
- **CRITIC trailer** — canonical fenced field-schema block (`VERDICT`/`REASON`/`ROUND` + optionals) appended to every critic verdict message for programmatic parsing by the orchestrator.
- **GENERATOR trailer** — canonical fenced field-schema block (`RESULT`/`REASON`/`ARTIFACTS` + per-agent extensions such as `PR_URL`, `BRANCH_NAME`, `SLICE_ISSUE`) appended to every output-emitting generator's response.
- **hamburger method** — Gojko Adzic's vertical-slicing technique; slice 1 of any PRD must cut through every pipeline layer end-to-end rather than building one layer completely before the next.
- **INVEST** — Bill Wake's six-property check (Independent, Negotiable, Valuable, Estimable, Small, Testable) used here as the gate criterion for slice shape; a slice that fails any letter requires a SPIDR split before implementation.
- **joint-APPROVE gate** — when a PRD ships with a macro-ADR draft, BOTH `prd-critic` AND `adr-critic` must APPROVE before `/to-prd` posts the PRD issue and slice issues; either BLOCK halts the pipeline.
- **label-and-continue** — the queue-drain escalation protocol: an item needing the operator is labeled (`needs-human-check` at triage, `needs-human` only at a round-3 strict-stop), recorded in the run ledger, and the run moves on without waiting for an answer (ADR-0085 D2).
- **PRD** — feature-sized Product Requirements Document captured as a GitHub Issue labeled `prd`; top tier of the PRD→Slice→PR hierarchy; one feature-sized deliverable per PRD.
- **R-CLOSES** — reviewer rule 10: every slice PR body must contain `Closes #<n>` pointing to a valid `slice`-labeled open issue; PRs without it are BLOCKed (trivial-lane and prd-only PRs are exempted).
- **R-LOC** — reviewer rule 9: caps slice PR diff at ≤600 LoC of runtime-artifact changes (raised from 300 per ADR-0077 D1; canonical "runtime artifact" definition lives in `.claude/agents/reviewer.md` under R-LOC).
- **R-META** — reviewer rule 11: NEW ADR files added in a PR must show subagent provenance via `Closes #N` to a slice/prd issue OR a `Co-Authored-By: Claude` commit trailer.
- **session** — a single Claude Code conversation window; cross-session continuity is maintained via live state reconstruction from GitHub Issues and git log, not via a formal handoff artifact.
- **slice** — INVEST-shaped vertical sub-issue under a PRD (labeled `slice`), delivered in one PR capped at ≤600 runtime LoC; middle tier of the PRD→Slice→PR hierarchy.
- **SPIDR** — Mike Cohn's 5 slice-split fallbacks (**S**pike, **P**ath, **I**nterface, **D**ata, **R**ules); S (spike/research), I (interface split), and R (rules split) are dominant in this project.
- **subagent** — specialist Claude agent invoked via the `Agent` tool with its own system prompt, restricted tool set, and isolated context window; runs as a sub-process of the main agent.
- **trivial lane** — fast-path workflow (I3) for PRs ≤10 LoC with no behavior change; uses `hotfix/<issue#>-<short-summary>` branch + `trivial` label; skips PRD/slice ceremony and gets a fast-path reviewer check.
- **walking-skeleton** — practice of shipping the smallest end-to-end version of the whole pipeline first, then iterating on the weakest stage; slice 1 of every multi-slice PRD must be a walking-skeleton per SC-WALKING-SKELETON.

- **YAGNI** — "You Aren't Gonna Need It"; rule #1 — never add code or content outside the current slice's scope; the reviewer's first job is to enforce this on every PR.


---

@.claude/generated/_global.md

@.claude/generated/_repo-map.md
