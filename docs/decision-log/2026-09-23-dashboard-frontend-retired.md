# 2026-09-23 — the dashboard frontend goes; observability is logs + LLM

**Context.** The plan on file scheduled a single status CLI (`tools/status.py`,
Block 5) as the served dashboard's eventual replacement, and Block 6 as the
later `pipeline/` module move. On 2026-09-23 the operator overruled that
plan directly, quoted verbatim from PRD #1480 §1: "delete the frontend …
Delete this whole idea of having frontend for checking the data … I would
rather use logs and just check it using llm." That directive both answers
D378 (what replaces the served dashboard) and makes D379 (the status-CLI
follow-on work item queued against Block 5) moot in the same stroke — a
status CLI is exactly the "frontend for checking the data" idea the
directive rejects.

## Decisions

**D378 — what replaces the served dashboard at `http://localhost:8765/`.**

- [ ] Option A — keep the server headless (drop the UI, keep `/api/*` and a
  single status CLI reading it) — rejected.
- [ ] Option B — leave the served surface as ADR-0080 left it (run-board +
  thin health strip) and revisit later — rejected.
- [x] **Option C** — chosen: retire the frontend outright. No process, no
  route, no page, no autostart, no probe, no launcher, and no replacement
  UI or status CLI. Observability becomes the append-only logs already
  written under `.claude/logs/`, read on demand by an LLM session, indexed
  by one short doc (`docs/observability.md`). The existing `tools/trace.py`,
  `dashboard/tracestore.py`, and `dashboard/health.py` CLIs already answer
  every question the board did; nothing new is built to wrap them.

**D379 — the planned status-CLI work item (plan Block 5) — moot.**

D378's option C directly rejects a replacement CLI, so the work item Block 5
would have queued has no object left to build. No separate resolution is
recorded; it is superseded in substance by D378.

## Discovered while applying

Retiring the served surface without a same-PR fix silently degrades two
existing gates rather than failing them loudly: CI CHECK 2 (README
regen-clean) `SKIP`s once `dashboard/server.py` is gone instead of FAILing,
and the `dashboard/**` proof-route row would keep demanding a browser
screenshot of a UI that no longer exists. Both are closed in the same slice
that deletes the server (ADR-0088 D3/D5) — this is not a separate decision,
but the reason the walking-skeleton slice bundles the deletion with the
README-generator retarget and the roster/route relocation instead of
splitting them.

**Outcome.** PRD #1480 posted, replacing the draft that stopped at the
round-3 strict-stop on #1459; #1459 is superseded by #1480 and closed
2026-09-23. Companion ADR-0088 ships verbatim in #1480's slice 1 (#1481) per
ADR-0003 D8. Promotion of this batch still needs the operator's
`.claude/PROMOTE_OK` ack (ADR-0070 D4) — guardrail paths are touched.

**Pointers.** #1480 (PRD); #1481 (slice 1, walking skeleton, ships this
directive's mechanical consequences); ADR-0088 (the accepted decision
record); #1459 (superseded, closed); the Decision Inbox card (source of
this directive and D378/D379's numbering).
