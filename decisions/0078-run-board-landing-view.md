---
id: "ADR-0078"
title: "Run-board as the dashboard's landing view; desktop shell deferred"
status: "superseded"
date: "2026-08-04"
scope: "pipeline"
rule_ids:
  - "PIP-022"
supersedes: []
superseded_by:
  - "ADR-0088"
---

# 0078 — Run-board as the dashboard's landing view; desktop shell deferred

Status: Accepted
Date: 2026-08-04
**Extends:** ADR-0075 D2 (append-only JSONL ledger + disposable SQLite read-model — the board is a reader over that read-model), ADR-0076 D1 (guarded verbs execute a side effect atomic with an emitted span) and ADR-0076 D2 (the closed kind enum naming `dispatch`/`dispatch_end`/`batch_planned`), ADR-0004 D2 (bootstrap-mode)

**Bootstrap-mode (ADR-0004 D2):** this decision binds FORWARD from the merge of its PRD's slice 1. Pre-anchor history is grandfathered and stays absent rather than reconstructed: runs predating the ledger's `dispatch`/`batch_planned` spans never appear on the board, and the board labels that absence instead of inferring past activity from git or gh.

## Context

The recorded-trace core (ADR-0075) and the guarded-verb engine (ADR-0076) made the pipeline's transitions observable: dispatches, merges, verdicts, greens and promotions are spans in one canonical ledger, and `dashboard/tracestore.py` answers "which dispatches have no terminal span" as an indexed query — verified live during the engine's own construction, where the ledger showed four concurrent dispatches and cleared each as it returned. No surface answered the operator's stated first question on opening the dashboard: what is running now, what runs next. That answer sat behind a CLI while the landing view showed the architecture graph.

A second motivation — that the process serving the board is not reliably alive — was investigated during this ADR's own gate and deliberately removed from its scope. The investigation's finding is worth recording because it inverted the initial assumption: `tools/dashboard-up.ps1` already launches the dashboard detached (surviving session exit), already detects staleness by comparing `/api/meta`'s sha against `git rev-parse origin/develop`, and already kills stale or unresponsive listeners, with regression coverage. The genuine gaps are narrower than "no supervision" — the launcher is only invoked at SessionStart, and four components declare the port independently — and the operator split that work into its own PRD (issue #1169) rather than couple it to the board. This ADR therefore decides the board and the shell posture only.

## Decisions

### D1 — The run-board is the dashboard's landing view, rendered strictly from recorded spans

`/api/runboard` serves `now` (dispatch spans with no terminal `dispatch_end`), `next` (the latest `batch_planned` ready-set minus what is already running) and `recent` (most recently terminated chains) from the SQLite read-model; the dashboard renders these as the first view at `/`, with the architecture view one click away. Every row states its data source and freshness. The board NEVER infers, backfills, or reconstructs state the ledger does not hold: an absent `batch_planned` yields an explicitly-marked empty `next`, and an unreadable ledger yields an honest empty state naming what it tried to read.

**Enforcement (rule #23), named inline:** (a) the parent PRD's production check fails outright if any rendered row cannot be traced to a canonical-ledger span, if the board renders normally while the ledger is unreadable, or if the ledger holds no `dispatch`/`batch_planned` span in the verification window — that last precondition exists because a dead upstream emitter and a genuinely quiet pipeline render identically, so an empty board is never accepted as proof (ADR-0054 D6); (b) ADR-0075 D2's fold-only write discipline keeps the view read-only by construction — the board has no write path to acquire. **Parsimony delta:** no existing mechanism answers "what is running now" in the dashboard — `check_stale_server()` reports server freshness, the Firing tab reports per-PR chains after the fact, and the tracestore query exists only at a CLI; this is the first surface reading live-dispatch state into the operator's first view.

### D2 — The desktop shell is deferred, and the board is built so deferring costs nothing

The operator's stated end-state is a background desktop application with native presence. That is deferred rather than built now: inserting a packaging toolchain while the enforcement core is still settling would trade a settled phase for an unsettled one, and the stability argument that most favoured building it early turned out to be a supervision-cadence gap (issue #1169) addressable without any shell. The deferral is made cheap by construction: the board ships as plain HTML served by the existing dashboard, which is exactly what a Tauri or pywebview shell would host, so adopting the shell later is a wrapping exercise rather than a rewrite. No board work performed under this ADR is discarded when the shell arrives.

**Enforcement (rule #23):** none required — this decision defers work and constrains how the deferred-to state is reached; it introduces no new obligation an agent could violate. Recorded as a decision so the deferral is a documented choice with a stated migration path, not silent drift. **Parsimony delta:** no existing ADR records the shell posture; ADR-0077 governs cadence, not surface.

## Consequences

- The operator's first question is answered by the first thing they see, sourced from the same ledger the reconcilers check — board and enforcement layer cannot silently disagree.
- "Running now" doubles as the duplicate-dispatch mutex's display surface: the query that refuses a second dispatch (ADR-0076 D1) is what the board shows, so the operator sees exactly what the engine enforces.
- The board is only as good as the emission discipline behind it: a verb that stops being invoked shows as absence, which STREAM-LIVENESS's per-kind rows name — the failure is visible rather than silent.
- Demoting the architecture view to one click trades map discoverability for live-state currency; the map is unchanged and remains linked.
- Two distinct liveness axes exist and only one is checked here. **Ledger-content liveness** (did the emitters write spans at all) is enforced by D1's precondition. **Serving-process liveness** (is the HTTP server itself alive and fresh) is out of scope — the board can render an accurate "nothing running" while the process serving it is stale or dead; that residual is owned by #1169 and named rather than hidden.
- Deferring the shell keeps a known gap (no native background presence or notifications) open by choice, with a migration path made cheap rather than free.
- Cascade surface (advisory): `dashboard/index.html` (landing tab), `dashboard/server.py` (new route), `dashboard/tracestore.py` (board query), and the dashboard README's view inventory.

## Alternatives considered

- **Run-board as a non-default tab:** identical build cost, but leaves the operator's stated first question one click behind the architecture view. Rejected on the stated need.
- **Standalone board app or TUI:** decouples the board from the large `index.html`, but adds a second surface to serve and supervise for zero added capability. Rejected on parsimony.
- **Build the desktop shell now instead of the web board:** delivers the end-state sooner, but inserts a packaging toolchain mid-phase, and the stability motivation for it proved to be a cadence gap addressable without it. Deferred with an explicit, cheap migration path (D2).
- **Bundle supervision into this decision:** the original draft's approach; rejected after the gate established that `tools/dashboard-up.ps1` already supervises and that the residual gaps (SessionStart-only invocation, four-way port disagreement) are a different, narrower problem deserving its own grill (#1169). Coupling them would have shipped a board blocked on unrelated findings.
- **Reconstruct board state from gh when the ledger is thin:** would make the board look fuller sooner, but reintroduces exactly the reconstructed-not-recorded failure the trace core was built to end. Rejected; absence is rendered as absence.

## References

- ADR-0075 (recorded-trace core: D2 ledger + disposable read-model the board queries)
- ADR-0076 (guarded-verb engine: D1 verbs execute a side effect atomic with an emitted span; D2's closed kind enum names `dispatch`/`dispatch_end`/`batch_planned`)
- ADR-0077 (ceremony-reduction cadence this PRD is planned under)
- Operator fork decision F4 (board placement + supervise-now-shell-later), locked 2026-08-02/03; split decision recorded on escalation #1168
- Issue #1169 (dashboard restart cadence + port unification — the split-out supervision work)
- Incident classes: #1116 (Windows path dialects)
