# Observability

The dashboard frontend is retired (ADR-0088). Observability is the
append-only logs under `.claude/logs/`, read on demand by an LLM session.
No served UI, headless API, or status CLI replaces it — this doc is the
index.

**Log root.** Every log below lives at `<log-root>/.claude/logs/`, where
`<log-root>` is the main checkout, resolved from a linked worktree via
`git rev-parse --git-common-dir` (its parent directory is the log root).
Worktrees share one log root.

## The logs

**`hook-fires.jsonl`** — every Claude Code hook's attempt/ok/ERROR beacon
(ADR-0057 D1). Answers: did a given hook fire, and did it complete cleanly
or error. Query: `python3 dashboard/health.py --check HOOK-INTEGRITY`.

**`workflow-events.jsonl`** — v2 tool/agent activity events (skill
invocations, agent dispatches, bash commands), plus its archive-aside
rotations and `.rejects`/`.test` side-streams. Answers: what happened in a
session, in order. Query: `python3 dashboard/health.py --check <id>` for
any registered check that reads it (`--list` for the full set).

**`trace-v3.jsonl`** — the canonical pipeline-verb span ledger (dispatch,
pr_opened, pr_merged, qa_verified, develop_green, promotion, verdict).
Answers: what mechanical pipeline transition happened, and its causal
chain for a given PR. Query: `python3 tools/trace.py path --pr <n>`, or
`python3 dashboard/tracestore.py running|runboard` for its SQLite
read-model (refoldable from the log, never a second source of truth).

**`drain/<run-id>.jsonl`** — one append-only ledger per `/ship`
queue-drain run. Answers: what a drain run triaged, escalated, or landed,
and where a parked run should resume. Query: read the file directly, or
`python3 dashboard/health.py --check DRAIN-LEDGER` to validate the newest
one offline.

**`subagent-edits.log`** — plain-text nudge log: every edit to a
`.claude/agents/*.md` file, appended by the PostToolUse(Edit) hook.
Answers: was a subagent prompt touched this session. Query: read directly
(it is not JSONL).

## Liveness lesson

**#1054:** judge liveness from the beacon stream (`hook-fires.jsonl`), never from `workflow-events.jsonl` alone — a resumed session can leave it idle for days while beacons keep flowing.

## Not logs

`trail-cache/` and `.claude/state/trace.db` are derived caches, refoldable
from the logs above — never read them as a primary source.

## See also

CLAUDE.md's "Pipeline trace ledger" and "Queue-drain run ledger" Map rows
own the canonical descriptions of `trace-v3.jsonl` and
`.claude/logs/drain/` respectively; this doc points to them rather than
repeating them (rule #9).
