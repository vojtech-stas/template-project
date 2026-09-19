# OpenAI entrypoint

Read [CLAUDE.md](CLAUDE.md), [.claude/generated/_global.md](.claude/generated/_global.md),
[the repo map](.claude/generated/_repo-map.md), and
[the OpenAI execution contract](docs/openai-workflow.md) explicitly.
Claude imports and area-rule autoloading are not OpenAI instruction loading.

Before edits, run python tools/openai_workflow.py instructions --path <changed-path>
for every changed path and read the returned canonical area sources.
Missing sources or invalid paths are a refusal, never permission to omit rules.

Use [.agents/skills/ship/SKILL.md](.agents/skills/ship/SKILL.md) to enter the shared
ship procedure. D6 inventory is ship only; complete discovery is pending #1442.
Only the controller dispatches independent native workers. It verifies isolation
before mutation and after completion; every worker also asserts before writes.
Use typed issue-bound Codex branches and truthful observed native identities.
QA and closure use the proof-gated adapter entrypoints. Native hooks remain
unverified until their later slice and normal host trust; do not fabricate events.
