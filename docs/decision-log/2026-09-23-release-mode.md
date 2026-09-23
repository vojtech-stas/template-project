# 2026-09-23 — release mode (v1.0 as a frozen milestone; bug lanes; fixes in the run)

**Context.** On 2026-09-23 the owner grilled what "v1.0" means for this template and how the pipeline reaches it. Measured that day at `develop` `2980962`: 195 open issues (about 175 bugs, about 13 features, 5 QA residuals, and the 5-issue Codex PRD in flight), no milestone (`gh api 'repos/{owner}/{repo}/milestones?state=all'` prints `[]`), and no issue carrying a class label (`bug` exists on 0 open issues; `feature` does not exist). The full PRD pipeline costs about 8M tokens for a 4-slice PRD against about 0.2M for a trivial fix, and ADR-0085 D3 caps a drain at 3 items in flight — too slow and too costly to drain about 175 bugs. Rule #13 (ADR-0024 D1) was also refilling the queue it should shrink: 96 of the 195 open issues carried `root-cause`. The grill settled ten forks; they are recorded below verbatim and encoded in [ADR-0090](../../decisions/0090-release-mode.md).

## Decisions

**Q1 — A.** v1.0 means every open bug is fixed. Features wait for v1.1. A finish line anyone can check beats a curated subset.
**Q2 — B.** Your new project starts after v1.0 is finished, on a clean template.
**Q3 — A.** v1.0 also includes installable (Block 1) and upgradeable (Block 6, subtree plus `git subtree pull`). The new project starts on v1.0 and later receives v1.1 with one command instead of a manual port.
**Q4 — A.** A bug found mid-work is fixed in the same run. It becomes an issue only if it truly can't be fixed then, and that issue blocks the current version's tag.
**Q5 — A.** Rule #13 keeps its lesson but changes its output. Symptom, root cause and fix go in the fixing PR, not a new issue. This needs an ADR and a check.
**Q6 — A.** A bug is anything that breaks the system's own promise (docs, rules, ADRs), doc drift included. Agents label `bug` or `feature` at capture, and you can flip any label on the card.
**Q7 — A.** A Sonnet swarm builds in wide, non-overlapping file lanes (roughly 10–15 at once). Every PR still gets a strong isolated reviewer and CI before merge. No switch of this session's model.
**Q8 — A.** Write the release mode into the rules first (PRD plus ADR through the critics, about half a day), then drain.
**Q9 — A.** It becomes `/ship release <version>` on top of the queue-drain machinery, and it travels with the template.
**Q10 — A.** QA residuals and policy questions go on your human-check card and don't block v1.0.

### What follows from these decisions

- **D502** (the owner's earlier "top ~30 as `next`, park the rest" answer) no longer fits: Q1 puts every bug in v1.0, so nothing gets parked. The ranking becomes the drain's priority order. It also delivers its 6 themed clusters, starting with #1492 (pre-commit skips the secrets scan) and #1251 (a promotion guardrail check that can never fail).
- **Block 2** (gh-fetch honesty) is bug-class, so it's in v1.0. Its rewrite and the Block 1 rewrite are being drafted alongside this PRD.
- **The Codex PRD #1436 and slices #1441–#1443** are features, so they're v1.1. #1476 stays the owner's per D462.
- **v1.0's order:** first the release-mode PRD, alongside the Block 1 and Block 2 rewrites. Then Block 6. Then `/ship release v1.0` drains the rest. Finally the owner runs `promote.sh` and the tag, because the platform refuses those to the agent.
- **Nothing newly deferred surfaced in the grill.** Everything raised is either in the plan above or already tracked (#1479 ADR numbering, #1073 GitHub mutation throttling), so no new captured issues.

### Not chosen

**Not chosen:** switching this session to Sonnet (it costs more than it saves), Sonnet reviewers, a separate `/release` orchestrator, running the swarm before the rules allow it, and blocking v1.0 on the owner's answers.

**Outcome.** Encoded as ADR-0090 (five decisions D1–D5, rules PIP-033/PIP-034, six amended statements) with the mode's prose in the `/ship` skill (`### QD11. Release mode`), `.claude/agents/implementer.md`'s `## Lane mode`, `.claude/agents/reviewer.md`'s lane legs, and `tools/release.py`. Enforcement lives in `RELEASE-GATE`, `dispatch --lane`, `pr-merge`'s lane legs, and DRAIN-LEDGER's release mode.

**Pointers.** [ADR-0090](../../decisions/0090-release-mode.md); PRD #1501; slice #1506. Slicer-critic round 2/3 APPROVE amendments A1–A11 are folded into the slice bodies, not this record. Deferred siblings: headless/scheduled release runs (#1320), mutation-pacing evidence (#1073), ADR-number collision tooling (#1479).
