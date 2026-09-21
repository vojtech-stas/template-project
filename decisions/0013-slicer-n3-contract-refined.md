---
id: ADR-0013
status: accepted
supersedes:
  - ADR-0003
superseded_by:
  - "ADR-0044"
scope: slicing
rule_ids:
  - SLI-004
  - SLI-005
---
# ADR-0013: Slicer N=3 contract refined — N=1 reserved for degenerate end-state cases (partial supersession of ADR-0003 D3)

- **Status:** Accepted
- **Date:** 2026-05-20
- **Supersedes:** [ADR-0003](0003-autonomous-pipeline-with-critics.md) D3 — narrowed: "N=3 alternatives at slicer" replaced with "N≥1 alternatives, with N=1 reserved for degenerate cases where the slicer judges all candidate decompositions would have bit-identical post-merge end-state." The single-revision-loop semantics of D3 are preserved unchanged.
- **Extends:** [ADR-0003](0003-autonomous-pipeline-with-critics.md) D2 (5-stage pipeline preserved); D4 (no human gates preserved); D6 (skills vs subagents preserved); D7 (`/ship` orchestrator design preserved); D8 (macro-ADR placement preserved). [ADR-0004](0004-bypass-prevention.md) D2 (bootstrap-mode policy cited in D6 below).

## Context

ADR-0003 D3 (shipped early in the project) locked the slicer's contract at N=3 alternative decompositions per PRD, with the rationale that *"slicing has many valid decompositions and the choice between them is the highest-leverage decision."* The contract has served the project well across most PRDs.

The 2026-05-19/20 session observed **three consecutive cases** (PRD #100, PRD #103, PRD #111) where the slicer correctly emitted N=3 per the contract, but all three alternatives had **bit-identical post-merge end-state** — same files, same LoC, same content. The alternatives differed only in commit ordering inside the squash branch, which `gh pr merge --squash` collapses to a single commit. slicer-critic's verdict on PRD #111 made this explicit:

> *"The N=3 contract is being misused here. All three 'alternatives' produce identical end-state; the variation is purely commit ordering. ... The slicer should have either declared N=1 with explicit rationale or generated genuinely-different shapes."*

The root cause: when a PRD locks single-slice scope AND a fixed file list (typical for small calibration/fix PRDs), there is exactly ONE meaningful decomposition. The slicer dutifully generates N=3 by varying non-meaningful axes (commit ordering, edit grouping inside the squash), but those don't survive merge. The critic then has to score 9 rubric criteria × 3 decompositions × (mostly identical answers), with only criterion 8 (risk front-loading) able to discriminate.

This ADR codifies a surgical refinement: when the slicer judges that all candidate decompositions would be bit-identical post-merge, it may declare N=1 with explicit rationale instead of fabricating cosmetic variation.

**This ADR does NOT supersede the N=3 default for genuinely-open-shape PRDs.** Backlog [#62](https://github.com/vojtech-stas/project-claude/issues/62) proposes the more radical step of dropping N=3 entirely in favor of single-decomposition-with-internal-perspective-prompting; that strategic question remains open and is intentionally not pre-decided here. This ADR is the tactical fix; #62 is the strategic question.

## Decisions

### D1: Slicer self-restraint — N=1 reserved for degenerate end-state

The slicer subagent prompt (`.claude/agents/slicer.md`) gains an explicit "Degenerate N detection" rule:

> **When N alternatives would produce bit-identical post-merge end-state** (same files, same LoC, same content — modulo commit ordering or trivial rewording), **declare N=1 with explicit rationale** citing the constraint that locks the decomposition (e.g., "PRD §4 locks 1 slice + fixed file list; only variation axis is commit-ordering, which `gh pr merge --squash` collapses").

Examples cited in the slicer prompt for grounding: PRD #100 (single-file rubric calibration), PRD #103 (5-file mechanical swap), PRD #111 (6-file consolidation with new ADR). All three were degenerate-N=3 cases.

**Slicer judgment is the mechanism, not a mechanical diff.** The slicer is the expert on whether its candidate alternatives would meaningfully differ. A mechanical end-state diff could be added in a future PRD if false judgments accumulate, but YAGNI here.

### D2: Slicer-critic acceptance of N=1

The slicer-critic subagent prompt (`.claude/agents/slicer-critic.md`) gains an explicit N=1 acceptance clause:

> **N=1 with explicit rationale is a legal input** (per ADR-0013 D1). The critic verifies the rationale (does the PRD genuinely lock the shape?) but does NOT BLOCK on "didn't produce N=3". If rationale is missing or weak, the critic asks for one round of revision OR scores normally on the single decomposition.

This matches the "critics gate, generators draft" pattern: slicer drafts honestly (D1); slicer-critic validates honesty (D2). The single-revision-loop semantics of ADR-0003 D3 are preserved — only the N-default changes.

### D3: Verification of N=1 rationale

When slicer-critic receives N=1, the rationale must answer:
- What PRD section (typically §4 Appetite or §5 Solution sketch) locks the shape?
- What variation axis was considered and rejected as non-meaningful (e.g., commit-ordering inside squash, trivial rewording)?
- Would N=3 have produced genuinely-different alternatives, or only cosmetic variation?

If the rationale is concrete on these three points, accept and score the single decomposition. If vague ("only one way to do it" with no PRD citation), request one revision asking for the explicit rationale.

### D4: N values reserved beyond 1 and 3 (informational)

- **N=1**: degenerate end-state per D1.
- **N=3**: ADR-0003 D3 default for genuinely-open-shape PRDs.
- **N=2**: not currently used. If a "trivial-lane vs PRD-ceremony" binary emerges as a common pattern, a future PRD could reserve N=2; out of scope here.
- **N≥4**: not used. ADR-0003 D3's rationale that N=3 balances exploration with consumer cost remains.

### D5: Cascade-docs

- `decisions/README.md`: new index row for ADR-0013; ADR-0003 row Status updated to *"Accepted (D3 partially superseded by 0013: N=3 default + N=1 degenerate-case carveout)"*.
- `CLAUDE.md`: the "How to create slices/issues from a PRD" pipeline-operational-logic section is updated if it explicitly references N=3; otherwise no edit needed (the slicer.md and slicer-critic.md prompts are the canonical home of the N-mechanism per ADR-0005 D2).
- `README.md`: no update needed (README narrative doesn't enumerate slicer's N).

### D6: Bootstrap-mode acknowledgment (per ADR-0004 D2)

The refined N-contract binds **forward from the slice that ships it**. Past slicer runs (PRDs #100, #103, #111 with degenerate N=3 outputs) are grandfathered — no retroactive reclassification. From slice-1 merge forward:
- Any PRD where the slicer judges all candidates bit-identical may receive N=1 from the slicer.
- slicer-critic accepts N=1 with rationale per D2/D3.
- N=3 remains the default for genuinely-open-shape PRDs per ADR-0003 D3 (the part not superseded).

The 6-critic-cap meta-rule (ADR-0008 D7) is unaffected — no new critic added.

## Consequences

### Positive

- **Slicer output ~50% shorter** for degenerate cases. Critic verdict matrix simpler; criterion 8 (risk front-loading) regains discriminative value for cases where it matters.
- **Honest signal**: when the slicer judges variation cosmetic, the verdict reflects that. Forces the slicer prompt to think about whether alternatives are meaningfully different rather than mechanically generating N=3.
- **Tactical fix without precluding the strategic question**: backlog #62's "drop N=3 entirely" remains open for future strategic grilling. This ADR doesn't pre-decide #62.
- **Composable with future improvements**: if false judgments accumulate, a mechanical end-state-diff check can be added without re-deciding the contract shape.

### Negative / Accepted

- **Slicer judgment may be inaccurate**: a slicer that lazily declares N=1 when N=3 would have produced meaningful alternatives degrades exploration. Mitigation: D3 requires concrete rationale; slicer-critic asks for revision if vague. Bias should favor producing N=3 unless certain.
- **Adds a new edge case to slicer-critic's input contract**: must handle both N=1 and N=3. Mitigation: D2's acceptance clause is small (~15 LoC); the existing 9-criterion rubric works for N=1 (just score the single decomposition).
- **Doesn't address backlog #62's broader concern**: that N=3 from a single agent context produces "bounded diversity" because alternatives share assumptions. That's a strategic question for a future PRD; tactical fix here doesn't supersede it.

## Alternatives considered

- **Alt-A: Don't change anything; accept the degenerate cases.** Rejected — three consecutive cases is reliable enough signal that the fix pays for itself. Slicer-critic's PRD #111 verdict explicitly called the pattern out as misuse.
- **Alt-B: Drop N=3 entirely (backlog #62's proposal).** Rejected as too radical for this PRD — there's no evidence yet that single-decomposition is BETTER for genuinely-open-shape PRDs; the surgical fix here addresses the observed pain without precluding the strategic question.
- **Alt-C: Add a mechanical end-state-diff check in the slicer.** Rejected as YAGNI — slicer judgment is sufficient for the observed pattern. Add the mechanical check later if false judgments accumulate.
- **Alt-D: Add the detection only in slicer-critic (collapse-after-the-fact).** Rejected as wasteful — if the slicer knows the alternatives are bit-identical, it shouldn't emit N=3 in the first place. Self-restraint at the source is the right default.
- **Alt-E: Add the detection only in slicer (no slicer-critic acceptance clause).** Rejected — slicer-critic's existing contract would FAIL on "didn't produce N=3", causing a contract violation. Both edits are needed.
- **Alt-F: Ship without an ADR (treat as implementation refinement of ADR-0003 D3).** Rejected because ADR-0003 D3 literally locks N=3; relaxing it materially changes the contract, which is a partial supersession per `decisions/README.md` immutability+supersession convention.

## Open questions deferred

- **Slicer-judgment reliability**: if false N=1 declarations accumulate, add a mechanical diff check in a future PRD.
- **N=2 reservation**: not currently used; if a trivial-lane-vs-PRD-ceremony binary emerges as a common pattern, future PRD may add.
- **#62 disposition**: stays open as strategic question. A future grill may decide to ship #62 (drop N=3 entirely, supersede this ADR's D1/D2 in turn) or close as subsumed by this ADR's tactical fix.

## Future direction

- **Mechanical end-state-diff check** in the slicer — implement only if false N=1 judgments accumulate post-merge.
- **#62 radical simplification** — orthogonal strategic question; this ADR doesn't preclude.
- **Slicer-critic criterion 10** — a future PRD could add an explicit rubric criterion "Is N=1 rationale concrete?" if D3's verification proves insufficient.

## References

- [ADR-0003](0003-autonomous-pipeline-with-critics.md) D3 (the partially-superseded N=3 contract); D2/D6/D7/D8 (preserved, see Extends header).
- [ADR-0004](0004-bypass-prevention.md) D2 — bootstrap-mode policy cited by D6.
- [ADR-0005](0005-output-shape-and-slicing-methodology.md) D2 — slicing methodology depth; slicer.md is the canonical home of the N-mechanism per D2.
- [ADR-0008](0008-workflow-autolog-bootstrap-and-naming.md) D7 — 6-critic-cap meta-rule (honored — no new critic added).
- `.claude/agents/slicer.md` — the file being edited for D1.
- `.claude/agents/slicer-critic.md` — the file being edited for D2.
- Backlog [#114](https://github.com/vojtech-stas/project-claude/issues/114) — the captured item this ADR was synthesized from.
- Backlog [#62](https://github.com/vojtech-stas/project-claude/issues/62) — the radical "drop N=3" strategic question this ADR explicitly does NOT preclude.
- PR #102 (PRD #100 ship); PR #105 (PRD #103 ship); PR #113 (PRD #111 ship) — the three observed degenerate-N=3 cases.
- slicer-critic verdict on PRD #111 — the surfacing analysis.
