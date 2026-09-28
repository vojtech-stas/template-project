# project-claude — home repository

This is the home repository of the pipeline: the product itself lives as one
git subtree at `.claude/pipeline/` (ADR-0092), and this repository is that
package's own source of truth — "host #0" (ADR-0092 D1). The pipeline's own
rules, map and glossary live in `.claude/pipeline/CLAUDE.md`, imported below.

Home-only material stays at this root: `decisions/` (Architecture Decision
Records, immutable — supersede rather than edit; index `decisions/README.md`),
`docs/decision-log/` (dated operator-decision records, append-only), and
`qa-proof/`. ADR-0086's OpenAI entrypoints — [AGENTS.md](AGENTS.md),
`.agents/skills/`, and [docs/openai-workflow.md](docs/openai-workflow.md) —
also stay at this root for v1.0 (ADR-0092 §3 Out of scope); they load the
generated sources and matching area rules using
[the OpenAI contract](docs/openai-workflow.md). No Claude hook event or
Claude-only tool/model is implied there.

@.claude/pipeline/CLAUDE.md
