---
id: "ADR-0090"
title: "Release mode: a version is a frozen milestone of every open bug plus the owner's named features, bugs drain through script-briefed file lanes, and a defect found in a run is fixed in that run"
status: "accepted"
date: "2026-09-23"
scope: "pipeline"
rule_ids:
  - "PIP-033"
  - "PIP-034"
supersedes:
  - "ADR-0003 D1"
  - "ADR-0024 D1"
  - "ADR-0024 D2"
  - "ADR-0063 D1"
  - "ADR-0085 D1"
  - "ADR-0085 D3"
  - "ADR-0085 D5"
  - "ADR-0085 D6"
superseded_by: []
---

# 0090 — Release mode: frozen versions, bug lanes, fixes in the run

- **Status:** Accepted on joint APPROVE (prd-critic + adr-critic, ADR-0004 D1). Ships in the companion PRD's slice 1 (ADR-0003 D8).
- **Date:** 2026-09-23
- **Supersedes (every entry partial):** ADR-0003 D1, ADR-0024 D1, ADR-0024 D2, ADR-0063 D1, ADR-0085 D1, ADR-0085 D3, ADR-0085 D5, ADR-0085 D6. Each falling clause is scoped in the Supersession ledger below.
- **Extends:** ADR-0076 D1 (the `dispatch` verb gains a lane mode; no new verb) and D3 (`pr-merge`'s assertion gains lane legs); ADR-0054 D2 (a `MODEL:` extension key after the core three); ADR-0063 D2/D3 and ADR-0067 D3 (applied to the fixing PR); ADR-0067 D2 (R-PROVE's trigger and TEST-ORDERING's subjects gain the `root-cause`-labeled PR); ADR-0064 D3 (one new registry row); ADR-0004 D2 (bootstrap-mode).
- **Bootstrap-mode (ADR-0004 D2):** each decision binds forward from the merge of the slice that ships it. Open issues predating D1 are classified at the first freeze. The 96 existing `root-cause` issues stay issues. No closed issue or merged PR is relabeled. CAPTURE-SHAPE's PR leg judges only PRs merged after the committer time of the parent of slice 1's squash commit.
- **Number.** The owner's plan reserved 0090 on 2026-09-23. 0086 is claimed by open PR #1476, 0087 is the companion ADR of posted PRD #1496, 0089 is a sibling draft of this wave, and 0088 is accepted. #1479 tracks the collision class. If 0090 is taken at landing, re-derive the next free integer; if a lower integer is still unlanded, add a gap note in the existing `> **Note on …**` form.
- **Citation baseline.** ADR-0083, ADR-0085 and ADR-0088 are accepted on `develop` (`2980962`), the integration branch under ADR-0070 D1. `origin/main` (`f7378de`) lags by 41 commits, and its `decisions/` ends at 0081. Verify cited headings on `origin/develop`.

## Context

On 2026-09-23 the owner grilled the v1.0 release and settled Q1–Q10 (recorded in `docs/decision-log/2026-09-23-release-mode.md`, committed with this ADR). He also accepted two follow-up designs: four token optimizations, and one fresh agent per file lane with a script-built context packet. Measured that day at `develop` `2980962`:

| Fact | Measured |
|---|---|
| Open issues | 195. The triage: about 175 bugs, about 13 features, 5 QA residuals, and the 5-issue Codex PRD |
| Milestones | none (`[]`) |
| Issues carrying a class | 0. `bug` exists on no open issue, and the `feature` label does not exist |
| Open issues labeled `root-cause` | 96 |
| Full PRD pipeline, PRD #1480 (4 slices) | about 8M tokens |
| Trivial fix, #1477 (implementer + reviewer) | about 0.2M tokens |
| Backlog sweep (153 issues, `file:line` evidence each) | about 38M tokens, about 250k per issue |
| Resumed implementer, #1484 | grew from 373k to 630k tokens over four rounds |
| Reviewer resumed by `SendMessage`, #1488 | lost worktree isolation |

Four gaps follow.
1. **No finish line.** Q1 defines v1.0 as every open bug fixed, and Q3 adds two named features, installable (Block 1) and upgradeable (Block 6). Nothing records a version's scope or a bug/feature distinction. `RELEASE-READY` (ADR-0070 D2) gates each `develop`→`main` promotion and reads neither.
2. **Too much ceremony for the dominant class.** ADR-0085 D1 routes every backlog item through PRD plus slicer. At the measured costs, about 15 full PRDs would take about 120M tokens, against about 60M for batched lane PRs (the grill's estimate).
3. **Too little concurrency, and too much re-reading.** ADR-0085 D3 caps a drain at 3 items. No step hands the sweep's evidence to the fixing agent, and every agent re-sends about 16k always-loaded tokens on each tool call. Resumed agents grow without bound and lose isolation.
4. **The capture rule refills the queue.** Rule #13 (ADR-0024 D1) turns each workflow mistake into a new issue. The next version must drain it, so the discipline meant to prevent recurrence grows the backlog: 96 of 195 open issues are its output.

**Theme.** How the pipeline reaches a version. The scope is frozen and the finish line is mechanical. Bugs, the bulk of the scope, are fixed at the lowest ceremony that still keeps a strong review. A defect found during any run is fixed in that run, so the capture rules stop refilling the queue a version must drain.

## Decisions

### D1 — A version is a frozen milestone of every open bug plus the owner's named features, every issue has one class, and the finish line is zero open bugs and admitted features

- **Classes (Q6).** Every issue carries exactly one class label:
  - `bug`: anything that breaks the system's own promise, including code, docs, rules, ADRs and doc drift;
  - `feature`: anything else.

  The agent that creates an issue applies the class at creation, and a slice takes its PRD's class. Residuals are exempt: open `needs-human-check` issues that do not carry `bug`. They are QA residuals posted from a PROVISIONAL production verification (ADR-0040 D1/D2), and pure policy or preference questions escalated by a drain (ADR-0085 D2). They are owner-owed questions on the human-check card, are never admitted to a milestone, and never hold the tag (Q10). A bug escalated to the owner, because it cannot be fixed in the run or because it waits on his answer, keeps `bug`. It is therefore not a residual, and it holds the tag (Q1, Q4). The owner may flip a class on the human-check card; the flip is an ordinary label edit, and the next `freeze` re-places the issue.
- **Milestones are versions.** A milestone titled `v<major>.<minor>[.<patch>]` holds a version's scope: every open bug, plus the features the owner names for that version. `tools/release.py freeze <V> --next <W> --features <n>,…|none`:
  - refuses, naming the cause, when the list is missing, while any open non-residual issue lacks exactly one class, or while a listed number does not name a non-residual `feature` issue;
  - otherwise admits to `<V>` every open non-residual `bug` (Q1), and every listed open feature with its open sub-issues, meaning a listed PRD's slices (Q3). For v1.0 the list is the Block-1 and Block-6 PRDs, by issue number;
  - gives `<W>` to every other open non-residual `feature` (Q1: features wait), creating either milestone if absent;
  - moves an issue only out of no milestone, `<V>` or `<W>`. A placement in any other milestone stands, a residual is never moved, and a re-run with the same list changes nothing;
  - runs at release start, and again at release end with the same list, to sweep the run's own new captures, slices posted since, and any class the owner flipped.
- **The finish line.** A new registry row, `RELEASE-GATE` (ADR-0064 D3), evaluates the lowest open version milestone; `RELEASE_GATE_VERSION` overrides that choice. It PASSes exactly when GitHub answered and three sets are empty:
  1. open non-residual issues in `<V>`: its bugs and its admitted features, which gate alike;
  2. open non-residual `bug` issues in no milestone, so an unfiled bug cannot slip past;
  3. open non-residual issues without exactly one class.

  Otherwise it WARNs `hold` and names the sets. A bug escalated to the owner stays in set 1 or 2, so it holds (Q4). An unconfirmed `gh` answer reads `unconfirmed`, never PASS. The PASS line names every residual it excluded; residuals never hold the gate (Q10). The row lists issues unfiltered and filters locally, because a label-filtered listing can return an empty success (measured by the sibling gh-fetch provenance PRD).
- **Who acts.** After `RELEASE-GATE` PASS, the owner runs `tools/promote.sh` and creates the tag. Agents do neither, and `.claude/PROMOTE_OK` stays operator-only (ADR-0070 D4).
- **Not a verb.** `freeze` writes no span and moves no PR, slice or PRD through a lifecycle state. Label and milestone edits stay ordinary `gh` calls, as the captured→backlog autopilot's label swap already is. ADR-0076 D1's verb set is unchanged.

**Enforcement.**
- **Mechanism:** the `RELEASE-GATE` row, a dashboard evaluator; `freeze`'s refusal; regression tests for both. REQUIRED-LABELS gains `bug`, `feature` and `lane`.
- **Parsimony:** RELEASE-READY checks six conditions on each promotion. None reads a milestone or a class, and a seventh condition would change ADR-0070 D2's set. REQUIRED-LABELS checks that labels exist, not that issues carry them. No existing check reads milestones.
- **Shadow:** *the moving finish line.* A version is declared done while a bug is unfiled, unclassified, outside its milestone, or escalated to the owner, or while an admitted feature is still open. Audit: file an unmilestoned `bug`, and add `needs-human-check` to a `bug` in `<V>`; `RELEASE-GATE` holds on both.
- **Advisory residue:**
  - Agents never tag `(advisory — no hook denies git tag; ADR-0076 D4 admits a deny form only with an incident and a sanctioned alternative; evidence trigger: an agent-created version tag)`.
  - Classification correctness `(advisory — judgment; evidence trigger: an owner flip on the card)`.

### D2 — `/ship release <version>` is a queue-drain sub-form whose work set is the version's bugs and admitted features

- **Form (Q9).** The trigger is `/ship release <version>`, with an optional bounded qualifier `lanes <N>` (the first N lanes; `lanes 1` is the walking-skeleton vehicle). ADR-0085 D2 and D4 apply unchanged: the triage litmus, the two-label escalation, the hard invariants, and the run ledger with its closed kinds. The ledger's `run_start` gains two fields, `"mode": "release"` and `"version"`. They are fields, not kinds.
- **Work set.** Open non-residual issues in `<V>`, meaning its bugs and admitted features, plus open non-residual `bug` issues in no milestone. Features the owner did not admit are not driven; `freeze` moves them to `<W>`.
- **Routing (optimization 1), first match wins:**
  1. Admitted features, and every `slice` and `prd` issue, ride the ordinary PRD → slicer flow; rule #16 binds. A feature never rides a lane.
  2. `captured` bugs pass through the captured→backlog autopilot first (ADR-0085 D1's gate stands). A BLOCKed one is escalated, never implemented, and it keeps `bug`, so it holds the tag (D1).
  3. A bug whose fix needs a design decision takes the PRD path.
  4. Every other bug rides a lane PR (D3, D4), with no PRD and no slicer.

**Enforcement.**
- **Mechanism:**
  - DRAIN-LEDGER validates the release fields: a release `run_start` without `version` FAILs.
  - `dispatch --lane` refuses any issue that is not an open `bug` in `<V>`, or that carries `slice` or `prd`, so an admitted feature never reaches a lane.
  - Reviewer R-CLOSES refuses a lane PR that closes a non-`bug`, slice or PRD issue.
- **Parsimony:** ADR-0085 D1's routing is prose. CHECK 19 guards slice provenance, not which kind of PR closes which issue.
- **Shadow:** *ceremony creep*, where a two-line bug is dragged through PRD and slicer (#1480 against #1477). Its mirror is *slice smuggling*: slice work closed by a lane PR, past the slicer.
- **Advisory residue:** which bug needs design `(advisory — triage judgment under ADR-0085 D2; evidence trigger: a lane PR strict-stopped at round 3 over a design fork)`.

### D3 — Bugs are fixed in disjoint file lanes by fresh Sonnet builders briefed with a script-built packet; at most 15 lanes fly at once

- **Lanes.** `tools/release.py lanes <V>` groups the lane-bound bugs into file groups by cited path. Paths come from the issue and its trusted comments, plus an optional `--evidence` sweep file. Bugs that share a path, directly or through a chain, share a group.
  - A group larger than the per-lane bug limit splits into sub-lanes. They share its `group` id and run one after another.
  - A bug with no cited path runs exclusively, with no other lane in flight, which keeps ADR-0085 D3's serialize-on-unknown default.
  - `--priority` orders lanes, so the D502 ranking becomes the drain's order.
- **Packets (optimization 2).** At every dispatch, `tools/pipe/dispatch --lane <branch> --milestone <V> --model sonnet <n>…` does the following:
  1. fetches `origin/develop` and checks its preconditions;
  2. appends one `dispatch` span with attrs `lane`, `issues`, `milestone` and `model`;
  3. prints the packet: the sha, then per bug its `path:line` refs, the excerpt around each cited line at that sha, and its `Check:` line or `CHECK: MISSING`.

  The packet reads only issue bodies and comments whose author association is `OWNER`, `MEMBER` or `COLLABORATOR`. No agent writes or edits a packet, so it costs zero model tokens and is fresh at dispatch.
- **Builders.** Each lane round gets one fresh `implementer` dispatch (`model: "sonnet"`, `isolation: "worktree"`), briefed with the packet and implementer.md's Lane mode. There is one implementer for all work kinds (ADR-0010 D1) and no new agent. The branch is `fix/<lowest n>-lane-<slug>`. The builder edits the cited lines. A stale or wrong packet is caught twice: the Edit tool refuses a "before" text that no longer matches, and the reviewer re-derives each fix.
- **Concurrency (Q7).** A release run holds at most 15 distinct lanes in flight. DRAIN-LEDGER counts the `lane` field of in-flight items. Plain drains keep ADR-0085 D3's 3 items, and merges stay serialized (ADR-0062 D2).

**Enforcement.**
- **Mechanism:**
  - DRAIN-LEDGER's release-mode lane cap;
  - `dispatch --lane`'s preconditions and span (a guarded verb, ADR-0076 D1);
  - regression tests for lane disjointness, sub-lane splitting, packet shape, packet freshness and the trusted-author filter.
- **Parsimony:** DRAIN-LEDGER counts items, and a lane holds many, so the item cap would stop a release run at 3 bugs. No existing tool builds a brief: the sweep's evidence sits in a session file that no fixing agent receives. ADR-0085 D3's overlap prediction is judgment.
- **Shadow:** *the exploring swarm*, where builders re-investigate what the sweep already paid for, and *colliding lanes*, where two in-flight lanes edit one file.
- **Advisory residue:**
  - Builders edit rather than explore `(advisory — a prompt's use is unobservable; evidence trigger: an R-SCOPE finding on a lane PR touching paths outside its packet)`.
  - The cap's value, 15, is the top of the owner's 10–15 `(advisory — revisited on evidence, as ADR-0085 D3's cap is)`.

### D4 — A lane PR is gated like a slice PR, reviewed by a strong model, rebuilt fresh every round, and verified by script

- **Shape.** A lane PR carries:
  - label `lane`;
  - one `Closes #<n>` per lane bug;
  - a `Check #<n>: <command>` line for every bug whose issue has no check;
  - `## Scope`, `## Out-of-scope` and `## Verification`, the last with each check's before and after output.

  R-LOC's 600 runtime-LoC cap applies unchanged (ADR-0077 D1). R-PROVE applies, because the branch is `fix/*` (ADR-0067 D2).
- **Review (Q7).** Every round gets a fresh reviewer, dispatched with `model: "opus"`, `isolation: "worktree"` and only the `BLIND-REVIEW <PR>` message (ADR-0060 D1). It never sees the packet, and it re-derives each fix. Its CRITIC trailer carries `MODEL: <id>`, an extension key after the core three (ADR-0054 D2).
- **Rounds (optimization 3).** Every BLOCK is answered by a fresh builder dispatch: the packet is rebuilt from the current `develop`, plus the reviewer's findings. A long transcript is never resumed (#1484, #1488).
- **Merge.** `tools/pipe/pr-merge` keeps its APPROVE assertion (ADR-0076 D3) and adds three lane-only legs:
  1. it refuses when the APPROVE's `MODEL:` is absent or names Sonnet or Haiku;
  2. it refuses when a non-merge commit was authored outside every `dispatch`→`dispatch_end` window of the lane, which is exactly what a resumed builder produces;
  3. on a confirmed merge, it closes each `Closes #<n>` issue. GitHub closes linked issues only on merges into the default branch, and `develop` is not the default branch.
- **After merge (optimization 4).** `tools/release.py verify <n>… --reopen` runs each bug's check on `develop` HEAD and reopens any that FAIL or are MISSING. Lane PRs close bugs, not PRD features, so ADR-0037 D1's per-feature gate does not fire, as for trivial-lane hotfixes. The script is the bug-level evidence, at zero model tokens.

**Enforcement.**
- **Mechanism:**
  - the reviewer's R-CLOSES lane leg and its R-PR-BODY `Check #<n>:` lines (critic rubric);
  - `pr-merge`'s three legs (a guarded verb);
  - CHECK 23's verdict presence;
  - regression tests for `verify`.
- **Parsimony:** `pr-merge` asserts that an APPROVE exists, not who gave it or how the PR was built. R-CLOSES admits only slice, trivial and PRD issues. CHECK 23 counts verdicts. Nothing observes `SendMessage`.
- **Shadow:** *the cheap gate*, where a swarm is judged by the same small model that built it, and *the resumed runner* (#1484, #1488).
- **Advisory residue:**
  - Reviewer freshness per round `(advisory — reviewer dispatches write no span; evidence trigger: a reviewer continued by SendMessage)`.
  - Mechanical checks run as scripts, not agents `(advisory — evidence trigger: a release run dispatching qa-tester to verify a lane)`.

### D5 — A defect found in a run is fixed in that run, and its root-cause record rides the fixing PR

- **Rule #13 keeps its lesson and changes its output (Q5).** Every workflow mistake still gets a symptom, a root cause and the workflow change. The record goes in the body of the PR that lands the change, in the same run. It uses ADR-0063 D2's headings `**Symptom:**`, `**Root cause:**` and `**Proposed:**`, with the Proposed section stating the change this PR makes, and it puts the evidence first (ADR-0063 D3). The PR carries the `root-cause` label. This holds for every run, not only release runs.
- **Only an unfixable mistake becomes an issue.** It is labeled `captured`, `root-cause` and its class. A `bug` gets the version milestone and so holds the tag (D1, Q4).
- **In a release run, a bug found mid-run is fixed in the run whatever its size (Q4):**
  - trivial-lane size: ADR-0085 D5's hotfix protocol;
  - otherwise: appended to the lane that owns its files if that lane has not merged, or else given a new lane. Its branch number is the bug whose work surfaced it.

  It is recorded `fix_queued`, then `fixed_in_run`. An unfixable one becomes an issue plus a `captured_ref`. DRAIN-LEDGER's parity check applies unchanged.
- **The regression rider applies to the fixing PR** (ADR-0067 D3): a code defect's PR includes a test that fails before the fix and passes after.

**Enforcement.**
- **Mechanism:**
  - CAPTURE-SHAPE gains a PR leg. A merged PR whose body has `**Root cause:**` must carry all three headings and the label; non-conformers are named, reported as `pr-records: <conforming>/<total>`.
  - TEST-ORDERING counts `root-cause`-labeled PRs as fix-type.
  - R-PROVE also fires on the PR label.
  - DRAIN-LEDGER's fix-queued parity is unchanged.
  - `RELEASE-GATE` holds on an unfixable bug, whether or not it is escalated to the owner.
- **Parsimony:** CAPTURE-SHAPE reads issues only. TEST-ORDERING reads `fix/*` branches only. R-PROVE reads the branch name and the linked slice's label, never the PR's own label.
- **Shadow:** *the capture treadmill*, where each defect found spawns an issue the next version must drain. At `2980962`, 96 of 195 open issues carry `root-cause`.
- **Advisory residue:** whether a mistake was fixable in-run `(advisory — judgment; evidence trigger: a root-cause issue whose fix later lands as a trivial-lane PR)`.

## Supersession ledger (clause-exact)

Every entry is partial. The superseded files are not edited, and they keep `superseded_by: []`, following the per-decision precedent that the `tools/gen_rules.py` baseline comment records for ADR-0083 and ADR-0084. No rule_id drops.

- **ADR-0003 D1.**
  - **Falls:**
    - "PR — one merged change, closes one slice" as the only PR kind. A lane PR closes one or more `bug` issues and no slice.
    - The implication "Drop the `feature` label… PRD plays that role." `feature` returns as a class label on any issue, not as a tier.
    - "groups of merged PRDs" as a milestone's content. A milestone holds a version's issues: its open bugs and the features the owner admits.
  - **Stands:** three tiers; a PRD is one feature; a slice is one INVEST vertical; no initiative or story tier; milestones reserved for releases.
- **ADR-0024 D1.**
  - **Falls:** "it MUST capture a `captured`-labeled GitHub issue" as the default output. The record goes in the fixing PR, and becomes an issue only when unfixable in the run.
  - **Stands:** the trigger list, the three contents, "the workflow change is the deliverable", and binding every agent.
- **ADR-0024 D2.**
  - **Falls:** "the SAME downstream mechanism" (captured label, inline autopilot, backlog-critic, graveyard) for mistakes fixed in the run, which produce no issue.
  - **Stands:** the division of labor by input trigger, and that mechanism for the unfixable-in-run issue.
- **ADR-0063 D1.**
  - **Falls:** "alongside `captured`", for the PR form.
  - **Stands:** the `root-cause` label and its queryability; never auto-relabel.
- **ADR-0085 D1.**
  - **Falls, for release runs only:** driving the whole assembled queue run-to-done (features the owner did not admit are moved, not driven), and "Backlog-tier items ride the standard PRD pipeline" for bugs without a design fork.
  - **Stands:** an entry mode on `/ship` with triggers (extended), the plan-only and bounded sub-forms, the captured→backlog gate, slicer-only slices, and no new skill, agent or critic.
- **ADR-0085 D3.**
  - **Falls, for release runs only:** the cap of 3 items (now 15 lanes), and overlap predicted by judgment from issue text (now script-built lanes).
  - **Stands:** serialize unknown overlap, serialized merges, advisory pacing, and the cap of 3 for plain drains.
- **ADR-0085 D5.**
  - **Falls, for release runs only:** the trivial-lane-only limit and "discoveries too big for the trivial lane capture as before".
  - **Falls, for all runs:** "workflow mistakes still produce root-cause captures per rule #13 (fix-in-run covers the code remedy, never the capture obligation)".
  - **Stands:** `fix_queued`/`fixed_in_run`, `captured_ref`, parity at both terminals, I3's definition, and plain runs otherwise untouched.
- **ADR-0085 D6.**
  - **Falls, for release-mode ledgers only:** the FAIL condition "more than 3 concurrently-open `item_start` records", which becomes more than 15 distinct lanes.
  - **Stands:** every other FAIL condition, CI CHECK 24, the offline-only rule and the advisory list.
  - **Adds:** a release `run_start` without `version` FAILs.

## Left standing, explicitly

- ADR-0085 D2 and D4. An escalation still takes `needs-human-check` and the run continues past it. The label never blocks promotion; an escalated bug holds only the version tag, through its `bug` class (D1).
- ADR-0024 D3–D7; ADR-0063 D2 and D3; ADR-0067 D2 and D3. Today R-PROVE fires only on a `fix/*` branch or a `root-cause`-labeled slice, and TEST-ORDERING reads only `fix/*` PRs; D5 extends both to a `root-cause`-labeled PR on any branch.
- ADR-0070 D2–D4. RELEASE-READY is unchanged; the tag gate is separate.
- ADR-0076 D1's verb set (dispatch gains a mode, not a sibling), D2's kind enum (no new kind) and D4's deny set.
- ADR-0077 D1.
- ADR-0037 D1. Features are still production-verified, this PRD among them.

## Decision → rule mapping

`tools/gen_rules.py` gains two statements. They become the next free ids at `BASE` if PIP-030–PIP-032 are not the ones taken (#1476 claims PIP-030, the Block 1 draft PIP-031–PIP-032). `RULE_IDS_BASELINE` becomes its value at `BASE` + 2.

- **PIP-033 (D1, D2).** *Every issue carries exactly one class label at creation: `bug` (anything that breaks the system's own promise, docs, rules, ADRs and doc drift included) or `feature`; residuals (`needs-human-check` issues without `bug`: QA residuals and pure policy questions) are exempt and never admitted to a milestone. `/ship release <version>` is a queue-drain sub-form: `tools/release.py freeze <V> --next <W> --features <list>` refuses on an unclassified issue, admits every open bug and the owner's listed features with their slices to `<V>`, and moves every other feature to `<W>`; bugs without a design fork ride lane PRs, not the PRD pipeline, and admitted features ride the PRD → slicer flow. A version is done when `RELEASE-GATE` PASSes (zero open non-residual issues in its milestone, zero open non-residual bugs in none, zero unclassified open issues), so a bug escalated to the owner holds it; only the owner promotes and tags (ADR-0090 D1/D2).*
- **PIP-034 (D3, D4).** *Release-mode bugs are fixed in lane PRs: one fresh Sonnet builder per disjoint file lane, dispatched only through `tools/pipe/dispatch --lane`, whose packet (evidence, excerpts from `origin/develop` at dispatch, check) is the builder's brief; branch `fix/<n>-lane-<slug>`, label `lane`, closing only `bug` issues and never slice or PRD issues; R-LOC 600 applies. `tools/pipe/pr-merge` merges a lane PR only on an APPROVE whose `MODEL:` is not Sonnet or Haiku, with every commit inside a dispatch window of its lane, then closes its bugs. DRAIN-LEDGER caps a release run at 15 lanes in flight, and `tools/release.py verify` checks each bug after merge (ADR-0090 D3/D4).*

Six existing statements gain an amendment suffix:
- **PIP-026:** "…; as amended by ADR-0090 D2: a release run's work set is its version's bugs and admitted features; bugs without a design fork ride lane PRs, and admitted features the PRD → slicer flow."
- **PIP-027:** "…; as amended by ADR-0090 D3: a release run allows 15 lanes in flight."
- **PIP-029:** "…; as amended by ADR-0090 D5: in a release run any in-run-fixable bug lands in the run, and a rule-#13 record rides the fixing PR."
- **CAP-005:** "Every workflow mistake gets a root-cause record — symptom + root cause + proposed fix (rule #13) — in the body of the PR that lands the fix in the same run; only a fix that cannot land in the run becomes a `captured`-labeled root-cause capture (ADR-0024 D1, as amended by ADR-0090 D5)."
- **CAP-006:** "…3-section shape…, in the fixing PR body or the capture (as amended by ADR-0090 D5)."
- **CAP-007:** "Root-cause records carry a `root-cause` label — on the fixing PR, or alongside `captured` on an unfixable-in-run capture (ADR-0063 D1, as amended by ADR-0090 D5)."

## Consequences

- **Throughput.** Up to 15 lanes build at once, against 3 items. Merges still serialize, so the merge queue and ADR-0062 D1's BEHIND retry loop become the bottleneck. That is acceptable, because disjoint lanes rarely conflict.
- **Cost.** About 60M tokens for the v1.0 bugs against about 120M, which is an estimate, not a measurement. The v1.0 run measures it.
- **A wrong brief spreads further.** Tight packets mean less investigation. The blind reviewer's re-derivation (ADR-0060 D1) and the Edit tool's before-text match are the net.
- **Mutation volume rises.** 15 lanes open PRs, comment and close issues, against the #1073 cooldown. Pacing stays advisory.
- **A version can ship with residuals open.** QA residuals and pure policy questions are listed in the PASS line; Q10 chose this.
- **A version cannot ship with an unanswered bug.** A bug escalated to the owner keeps `bug` and holds the tag (Q1, Q4), so an owner answer can gate a version, though never the run.
- **Admitted features gate like bugs.** v1.0 finishes only when the Block-1 and Block-6 PRDs close (Q3). Only the feature list admits a feature, because a re-run of `freeze` moves every unlisted feature out of `<V>`.
- **The `feature` label returns** as a class, not a tier; the hierarchy stays three tiers. CLAUDE.md §3's "There is no `feature` label" is rewritten.
- **Operator acknowledgement.** `reviewer.md` and `dashboard/health.py` are guardrail paths, so this ADR's promotion waits for `.claude/PROMOTE_OK` (ADR-0070 D4).
- **Merge order.** The Block 1 and Block 2 drafts and PR #1476 touch the same files. Whichever lands second rebases. If Block 1's branch resolver lands first, the new tools name branches through it.

## Alternatives considered

- **A separate `/release` orchestrator.** Rejected in the grill. It would recreate the two-orchestrator drift ADR-0081 D4 ended.
- **Switching the session to Sonnet.** Rejected in the grill: it invalidates the prompt cache and costs more than it saves.
- **Sonnet reviewers.** Rejected in the grill. The reviewer is the swarm's only judgment, and a small model judging a small model's work is the cheap gate D4 exists to prevent.
- **A few long-lived runners with persistent context** (the owner's second idea). Rejected in the design message:
  - a runner re-sends its growing history on every step (#1484);
  - a resumed agent loses isolation (#1488);
  - one confused runner contaminates every later task.

  A runner wins only for one large coupled change, which is a PRD anyway.
- **Agent-built packets, or agents reading the codebase.** Rejected. They cost tokens and can be stale; a script at dispatch time is free and fresh.
- **The full PRD path for every bug.** Rejected: at about 8M against about 0.2M per unit, it doubles the cost (optimization 1).
- **A curated v1.0 subset, D502's "top 30 as `next`, park the rest."** Superseded by Q1.
- **Keeping the drain's cap of 3.** Rejected. At 3, the drain covers about 175 bugs slowly. Disjoint lanes and the strong reviewer answer the review-quality worry the cap stood for.
- **Keeping rule-#13 records as issues.** Rejected by Q5; that is the treadmill.
- **The tag gate as a seventh RELEASE-READY condition, or inside `promote.sh`.** Rejected. Promotion is continuous per commit (ADR-0070 D3); a version tag is a separate act, and a seventh condition would change ADR-0070 D2's set.
- **A hook denying `git tag`.** Deferred. ADR-0076 D4's funnel requires an incident.
- **Posting sweep evidence to every issue at freeze.** Rejected: about 150 mutations against the #1073 cooldown. `--evidence` reads the sweep file.
- **Label-filtered queries in `RELEASE-GATE`.** Rejected, because of the label-path empty-success class.

## Propagation

Hits on the runtime surface for each superseded ADR number, at `develop` `2980962` (`grep -rn "ADR-NNNN" .claude/agents/ .claude/skills/ .claude/settings.json`). `.claude/settings.json` has zero hits for all four.

- **ADR-0003** (only D1 partial). **grandfather-with-reason for every file:** none cites D1. Each line cites another ADR-0003 decision, which this ADR does not touch, or names ADR-0003 with no D-number in a historical or illustrative example.
  - `.claude/agents/adr-critic.md`: `:10` D2; `:14` no D-number (historical example); `:76` no D-number (an example `Supersedes:` entry that lacks a D-ID); `:96` no D-number (the historical example of ADR-0003 mis-citing ADR-0001 D3); `:124` no D-number (historical); `:140` D4 (a FAIL example); `:255` D2/D8.
  - `.claude/agents/backlog-critic.md` `:136` D2; `.claude/agents/codebase-critic.md` `:396` D2.
  - `.claude/agents/implementer.md` `:65` D8, `:136` D2/D4/D8.
  - `.claude/agents/prd-critic.md` `:10` D2, `:75` D4, `:278` D2/D8.
  - `.claude/agents/qa-tester.md` `:548` D4 (inside its ADR-0020 D10 entry).
  - `.claude/agents/reviewer.md` `:495`, `:514`, `:515` D4.
  - `.claude/agents/slicer-critic.md` `:294` D3; `.claude/agents/slicer.md` `:137` D3.
  - `.claude/skills/qa-plan/SKILL.md` `:61`, `:64` D4.
  - `.claude/skills/ship/SKILL.md` `:241` D8, `:275` D3, `:425` D2/D4/D7/D8, `:445` D8.
  - `.claude/skills/to-issues/SKILL.md` `:8` D2/D6, `:18` D4, `:76` D4, `:82` D2/D6.
  - `.claude/skills/to-prd/SKILL.md` `:16` D8, `:30` D8, `:91` D2/D6/D8.
- **ADR-0024.**
  - `.claude/agents/qa-tester.md` `:553` (D1 + D3): **grandfather-with-reason.** It cites the three-part shape for QA-residual issues. Residuals stay issues by design (ADR-0040 D2), and D3 stands.
- **ADR-0063.** No hits.
- **ADR-0085.**
  - `.claude/agents/reviewer.md` `:310` (D4, R-FIXTURE's ledger exemption): **grandfather-with-reason.** D4 stands, and release records are the same kinds with added fields.
  - `.claude/skills/grill-me/SKILL.md` `:63` (D1 entry point and "drain plan"): **grandfather-with-reason.** Those clauses stand.
  - `.claude/skills/ship/SKILL.md` `:33` (bare): **update-in-this-wave.** The Release mode section is added under the drain.
  - `.claude/skills/ship/SKILL.md` `:119` (D4): **grandfather-with-reason.**
  - `.claude/skills/ship/SKILL.md` `:435` (References, D1/D2/D4/D5): **update-in-this-wave.** It notes D1 and D5 as superseded in part by ADR-0090.

**Beyond ADR-number cites.** These encode the superseded substance without naming the ADR:
- **`.claude/skills/ship/SKILL.md`:** `:29` (rule #13 to a captured issue), `:79` (QD6 "At most 3 items") and `:85` (QD7 step 1's capture obligation): **update-in-this-wave.**
- **`.claude/agents/reviewer.md`:** R-CLOSES (`:240–249`), R-LOC (`:219–238`), R-PR-BODY (`:188–201`), R-PROVE (`:356–367`) and the trailer template: **update-in-this-wave.**
- **`.claude/agents/implementer.md`:** gains `## Lane mode`.
- **`promote-to-backlog`, `to-prd` and `to-issues` SKILL.md files:** a class label at creation.
- **`CLAUDE.md`:** rule #13 (`:24`); §3's PR tier line (`:59`) and label line (`:62`); the milestone line (`:66`); the queue-drain paragraph (`:89`).
- **`tools/gen_rules.py`:** the two new statements, the six amendments, the baseline and its comment block; regenerate `.claude/generated/_global.md`.
- **`bootstrap.sh`** `LABELS` and **`dashboard/health.py`** `_REQUIRED_LABELS`.
- **`decisions/README.md`:** the 0090 row, and partial-supersession notes on the 0003, 0024, 0063 and 0085 rows.
- **Grandfathered as history:** every mention in `decisions/`, `qa-proof/` and `docs/decision-log/`.

## Open questions deferred

- **The per-lane bug limit (default 10) and the lane cap (15).** Revisit on a lane BLOCKed for size, or on idle lanes against a deep queue.
- **Whether reviewer dispatches should write a span,** so that D4's freshness becomes deterministic for reviewers too.

## References

- **Owner decisions:** Q1–Q10 of 2026-09-23 and the token-optimization and packet-lane messages, in `docs/decision-log/2026-09-23-release-mode.md`; D502 (ranking); D462 (#1476 stays the owner's).
- **Superseded in part:** ADR-0003 D1, ADR-0024 D1/D2, ADR-0063 D1, ADR-0085 D1/D3/D5/D6.
- **Extended:** ADR-0076 D1/D3, ADR-0054 D2, ADR-0063 D2/D3, ADR-0067 D2/D3, ADR-0064 D3, ADR-0004 D2.
- **Also cited:** ADR-0003 D8, ADR-0010 D1, ADR-0037 D1, ADR-0040 D2, ADR-0060 D1, ADR-0062 D1/D2, ADR-0070 D1–D4, ADR-0076 D4, ADR-0077 D1, ADR-0081 D4.
- **Issues:**
  - #1480, #1477, #1484 and #1488 (the measured costs and incidents);
  - #1073 (mutation cooldown), #1320 (headless runs), #1479 (ADR numbering);
  - PR #1476.
