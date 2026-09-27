---
id: ADR-0086
status: accepted
date: "2026-09-19"
supersedes:
  - "ADR-0004 D3 (add issue-bound codex branch prefix only)"
  - "ADR-0027 D1/D2/D3 (OpenAI dispatch model selection only)"
  - "ADR-0036 D1/D2 (OpenAI isolation mechanism only)"
  - "ADR-0058 D1 (OpenAI isolation evidence only)"
  - "ADR-0061 D2 (OpenAI session-source lookup on all routes only)"
superseded_by: []
scope: pipeline
rule_ids:
  - PIP-030
---
# ADR-0086: Additive OpenAI adapter over the shared workflow

Status: Accepted. Joint critic approval is specification approval;
enforcement activation is D6 below.
Date: 2026-09-19

Approval history: the original cycle ended at round 3 with PRD BLOCK and ADR
APPROVE. The owner authorized correction of the dashboard screenshot exemption
and a renewed independent review cycle. Both critics approved renewed round 1/3;
slicer-critic approved slicing round 2. The original strict stop remains part of
the record. This acceptance does not assert implementation review or production
verification. See PRD #1436 and its approved comments 5737315117 and 5738032032.

## Context

The owner requests that the template work in Codex/ChatGPT without breaking
Claude. Canonical procedures currently live in CLAUDE.md, generated rules,
.claude/skills and .claude/agents; the runtime also assumes Claude-specific
automatic hooks and harness fields. Moving this fleet now would conflict with
concurrent work and make a compatibility patch hard to review.

## Decisions

### D1 - One workflow source, committed OpenAI entrypoints

Retain the existing canonical files. Add AGENTS.md, an OpenAI execution guide
and .agents/skills routers with normal name/description frontmatter. Routers
reference the source procedure, not a copied body. A deterministic parity check
compares exact generated routers and their complete source inventory. Rule
resolution explicitly loads global and matching area sources; unresolved input
or missing required sources fails rather than silently discarding rules.
PIP-030 states: OpenAI runs use the shared canonical workflow through the
OpenAI adapter, preserving gates and truthful platform evidence; adapter drift
or missing required capability prevents a completion claim.
Enforcement: deterministic router/source parity and resolver tests in existing
CI. Claude discovery does not cover OpenAI entrypoints, so thin adapters are
necessary; copied procedure bodies are not. Shadow: silently divergent workflows.

### D2 - Map host primitives, preserve independent judgment

Only the main agent orchestrates role dispatch. A native worker receives the
canonical role body plus this platform mapping, not Claude's tool/model
frontmatter as executable configuration. Inherit the host model unless the user
requests another available model. Attribute commits to the actual agent, never
to Claude when Claude did not author them. ADR-0001 D12 has general hard rules,
not a literal Claude authorship mandate; that wording is in live COM-002 and the
implementer prompt. Amend those sources with an OpenAI-only truthful-attribution
clause, preserving existing Claude attribution. ADR-0027 D1/D2/D3's mandatory
Claude model aliases and no-inheritance rule remain for Claude, but OpenAI hosts
inherit their configured model unless the user requests an available override.
Do not guess equivalence between vendor model names. Review criteria, round-3 stops, slice provenance,
guarded transitions and human-only promotion acknowledgement are unchanged.

For OpenAI hosts without a native worktreePath result, the orchestrator creates
an isolated Git worktree and independently verifies its absolute top-level,
Git common directory and branch before mutation and after completion. A worker
assertion alone is insufficient. Missing/ambiguous isolation refuses mutation.
Also verify worktree registration in that common repository and the expected
starting revision; a different checkout of an unrelated repository does not pass.
This is a narrow OpenAI replacement for the native isolation argument in
ADR-0036 D1/D2 and native result evidence in ADR-0058 D1. ADR-0036 D3's invariant
and ADR-0058 D2's before-write assertion remain, with stronger independent checks.
No permission to use the root/shared tree or invent a harness field is introduced.
Claude's native mechanism and result checks remain unchanged.

Worker branches use `codex/<type>/<issue-number>-<kebab-slug>`, an additive case in
.githooks/pre-commit under ADR-0004 D3. Preserve every existing conventional
branch case and main-branch denial. No hook bypass is part of bootstrap.
`<type>` uses the same closed conventional type set as the current branch gate.
It is not optional and cannot be inferred from free prose: `codex/fix/42-example`
must select fix-type checks without a root-cause label. A small shared classifier
in tools/workflow_branch.py normalizes the optional `codex/` prefix and returns
the conventional kind and issue ID, with an explicit invalid/unknown outcome.
It preserves existing conventional branch semantics; unknown Codex kinds cannot
silently qualify for a release or trivial lane. The OpenAI dispatcher consumes
the classifier, ship's blind-test-author selection and reviewer's R-PROVE use its
fix result OR the existing root-cause label, and dashboard/health.py's
TEST-ORDERING selector includes the same Codex fix class. Adapt the two existing
health hotfix/trivial selectors using the same normalized kind while retaining
their existing label behavior. No regression evidence is waived for Codex fixes.
Sweep branch-type/issue-number consumers before implementation review; native
OpenAI spec checks use this grammar, not the Claude-only raw branch pattern.
Enforcement: independent worktree checks before dispatch/after completion,
branch-gate regression tests, and reviewer inspection of actual authorship and
worker IDs. Existing Claude-only harness assertions cannot express these host
primitives. Shadow: shared-tree pollution or invented identity disguised as parity.
Branch tests must cover ordinary fix and codex/fix with no labels, both with
root-cause labels, non-fix branches, malformed/missing types, and both hotfix
forms. Integration tests prove the Codex fix remains in the TEST-ORDERING subject
set and cannot skip the blind test-author/R-PROVE gate. The first slice ships
the classifier and dependent gate changes with the branch addition, not later.

### D3 - Truthful session and verification evidence

OpenAI sessions record a separate explicit startup receipt containing platform,
real host task/session ID, UTC timestamp, repository identity, worktree and HEAD.
No transcripts, credentials or private user data are recorded by this adapter.
The receipt is not a hook beacon and cannot prove that automatic capture works.
The pipe boundary uses CLAUDE_SESSION_ID as a legacy environment-variable name
containing the same namespaced real OpenAI ID; no shared pipe behavior changes.

For OpenAI checks, the checker validates receipt identity against the current
controller's observed host task/worker IDs, real command outputs, SHA and required
artifact fields, rather than requiring that ID in Claude workflow-events.jsonl.
Self-declared JSON alone does not prove host provenance. The live exercise has
an explicit start boundary; accepted events must follow it, match the tested SHA
and be at most 30 minutes old. Missing/stale sources fail; tooling unavailability
remains PROVISIONAL, never a weaker-route PASS. The controller passes an explicit
durable root proof directory, not a worker's disposable top-level fallback.

tools/openai_workflow.py qa-verify validates the complete independent qa-tester
verdict and union of route proofs BEFORE invoking tools/pipe/qa-verify with PASS.
Its prd-close entrypoint revalidates the current relevant proof and existing
closure prerequisites before invoking tools/pipe/prd-close. Validation failure
must cause a nonzero exit and ZERO downstream PASS records or issue-close calls.
Negative integration tests assert both non-actions, not just a checker message.
Only the adapter's validated path is allowed for OpenAI verification transitions;
native PreToolUse denies raw bypass attempts when trusted and active (D4).
Hooks are guardrails, not a security boundary against a user controlling the repo.

The ADR-0061 D2 source substitution applies to ALL OpenAI routes, and ONLY to
the session identity lookup that previously required Claude workflow-events:

- Command/static: real host receipt plus captured command output, exit status,
  static assertion result and artifact bytes tied to the tested revision.
- Native hook: the same real host identity plus actual Codex event-dispatch
  evidence, happy-path and induced-failure outcomes. A manually invoked handler
  or hand-authored event is a fixture and never satisfies this class.
- Browser: the same real host identity plus fresh real browser interaction,
  required screenshot/DOM artifacts and the existing /api/meta SHA/start-time
  handshake. A session receipt does not replace browser or environment evidence.
- Mixed paths: the conjunction of every class from the authoritative route
  table, not a selected easier class; missing evidence names the failing class.

The qa-tester's
single route table gains AGENTS.md/.agents instruction and .codex config/hook
paths. Adapter tests must assert the same route union rather than establish a
second contradictory authority. This is a scoped OpenAI source alternative to
ADR-0061 D2; D1/D3/D4/D5's routing, no-downgrade and artifact duties remain.
The existing verbs are not changed, and independent qa-tester judgment remains.
Existing verbs alone accept declared PASS without validating this provenance;
the thin adapter fills that gap, not a new pipeline engine. Shadow: verification
theater, vanished artifacts and raw transitions that bypass proof validation.

### D4 - Capabilities and distribution are explicit

Preflight observes prerequisites without installing software or writing global
settings. Add native .codex/hooks.json and minimal event adapters for session
receipts and load-bearing transition guards using documented Codex inputs and
outputs, including commandWindows support and git-root script resolution.
Do not execute Claude hooks on forged lifecycle events or blindly copy output
schemas: unsupported Codex fields may make a hook fail without blocking a tool.
Keep native Codex evidence in its own namespace, including source event, host
session, time and outcome; fixtures are never live hook evidence.

The per-hook capability inventory distinguishes native equivalents, explicit
procedures and unsupported duties. Preflight must label absent/untrusted/disabled
or unobserved hooks unverified; merely seeing a config file is not activation.
The user reviews/trusts hooks through the host's normal flow. The adapter never
sets trust, installs global hooks, bypasses trust or grants permissions itself.
No fragile ordering between concurrent matching handlers is assumed. Native
hook tests cover allowed, denied and error outcomes; live production evidence
must additionally prove actual host dispatch and refusal of a harmless prohibited
canary before attempting a real guarded transition. Missing mandatory capabilities
yield an honest handoff, not silent unguarded delivery.
A plain ChatGPT conversation without repository/shell/worker access can explore,
draft and hand off; it cannot assert tests, independent review, merge or capture.
Normal repository cloning carries all repo-local adapters. Account-wide plugins
and downstream-template updates are separate distribution work, not installers
required to start using this checkout.
Enforcement: config/schema and handler contract tests plus observed native event
evidence in production verification. Claude hooks cannot prove execution in a
different runtime. Shadow: advertised automation that never runs. The inventory
documents hook coverage limits, including interactive stdin paths not rechecked.

### D5 - Enforcement and unchanged Claude behavior

The normal test suite includes source/adapter parity, resolver failures,
preflight negatives, independent isolation assertions and session/proof refusal
cases. CI already runs tests/; use that gate instead of a second CI framework.
Generator output is checked by tests. Independent isolation, proof validation,
route requirements and guarded transitions are mandatory, NOT advisory. Human
judgment in critic/reviewer roles remains enforced by their independent verdicts;
no static checker claims to prove that judgment. The fresh-worker delivery
exercise is a required PRD production gate, not optional docs.
No Claude settings/hooks or existing guarded verb behavior changes in this PRD.
Compare the whole existing test suite at the recorded base and candidate under
identical prerequisites and isolated fixture logs; permit no added failures,
errors or skips. New adapter tests must pass without skips. Existing CI discovers
these tests; a second CI engine is unnecessary. Shadow: compatibility that only
passes its own small tests while regressing the incumbent workflow.

### D6 - Forward binding and bootstrap evidence

Per ADR-0004 D2, new enforcement binds forward from the implementing slice's
merge; already-running pre-merge Claude sessions and historical evidence are
grandfathered and are not retroactively reinterpreted as Codex runs.
Slice 1 ships the minimal resolver/router, branch exception, independently checked
isolation and proof-gated delivery path together with this ADR. Before those
helpers exist, the controller records the real native worker IDs, independent
git worktree membership/root/revision assertions and real review/QA artifacts
explicitly; it never invents fields unavailable from the host. After the helper
is implemented, validate that same first slice using the candidate helper before
accepting its guarded QA result. Execute its candidate branch gate normally,
with independent review; never skip hooks to get the first commit through.
This narrow bootstrap addresses missing mechanisms, not independent-review or
approval waivers. All existing applicable gates remain in force.

The first slice must demonstrate real implementation, independent review and
guarded delivery; later slices widen skill discovery and native hook coverage.
Parity binds to the implemented inventory in slice 1 and to the complete source
inventory when that coverage ships. Native hook enforcement binds when its slice
merges AND the host has normally trusted the hooks; missing trust must remain
explicit and prevents a full-production claim. No grandfathering can satisfy the
PRD's final real native-hook or end-to-end requirements. Before PRD closure, rerun
the entire fresh-session production check against the integrated revision.
Enforcement: slicer/reviewer verify activation boundaries in slice criteria and
qa-tester verifies the final live run; no extra bootstrap mechanism. Shadow:
permanent exemptions hidden behind a one-time migration.

## Propagation

Add AGENTS.md, .agents/skills/**, .codex/hooks.json, docs/openai-workflow.md and a
ChatGPT handoff guide; implement tools/openai_workflow.py, native event adapters
and router generation plus focused tests.
Register PIP-030 in tools/gen_rules.py and regenerate its global output. Link
the adapter and this ADR from current documentation sources and decisions index.
Update tools/gen_rules.py COM-002 and ISO-001/ISO-002/ISO-004 with narrow OpenAI
clauses and regenerate affected output; do not opportunistically repair existing
unrelated rule defects. Update CLAUDE.md's live provider-specific references and
.githooks/pre-commit's branch pattern narrowly. README.template.md and tools/README.md
link the adapter; generated README/rules are regenerated. Old ADR bodies remain
immutable; update only the current decisions index to point to this partial ADR.

Runtime prompt scan: `rg -l 'ADR-(0004|0027|0036|0058|0061)' .claude/agents .claude/skills .claude/settings.json`.
All matching files at ade5f20 have explicit dispositions:

- .claude/agents/implementer.md: update-in-this-wave; platform mapping pointer,
  truthful attribution, issue-bound codex branch and isolated before-write check.
- .claude/agents/reviewer.md: update-in-this-wave; OpenAI isolation/evidence path
  and actual PR base (develop under current delivery policy), not stale main;
  R-PROVE recognizes codex/fix via the shared branch classifier.
- .claude/agents/qa-tester.md: update-in-this-wave; authoritative route additions,
  OpenAI proof source, durable artifacts and mandatory adapter gate.
- .claude/skills/ship/SKILL.md: update-in-this-wave; scoped OpenAI dispatch,
  receipts, validation-before-transition and native capability/trust handling;
  blind test-author selection recognizes codex/fix before implementation.
- .claude/agents/adr-critic.md: grandfather-with-reason: references describe the
  unchanged critic/propagation/bootstrap rubric, not platform dispatch behavior.
- .claude/agents/backlog-critic.md: grandfather-with-reason: unchanged independent
  backlog review; native OpenAI model/tool mapping is supplied by the caller.
- .claude/agents/codebase-critic.md: grandfather-with-reason: unchanged audit
  criteria; native OpenAI model/tool mapping is supplied by the caller.
- .claude/agents/prd-critic.md: grandfather-with-reason: unchanged PRD review and
  joint-loop rules; native model/tool mapping is supplied by the caller.
- .claude/agents/slicer-critic.md: grandfather-with-reason: unchanged decomposition
  criteria; native model/tool mapping is supplied by the caller.
- .claude/skills/to-prd/SKILL.md: grandfather-with-reason: same joint critic loop
  and publication gate; OpenAI skill router supplies native dispatch mapping.

Repeat the scan on the implementation base and disposition any new hit before
merge. Nonmatching agent prompts retain canonical bodies/frontmatter; OpenAI's
wrapper maps the host-specific invocation contract instead of duplicating roles.
Additional executable consumers: tools/workflow_branch.py (new shared branch
classifier), dashboard/health.py (TEST-ORDERING and hotfix/trivial selectors),
.githooks/pre-commit (typed Codex grammar), and tools/openai_workflow.py (dispatch
classification) are update-in-this-wave, all in slice 1. Existing Claude hook
scripts are not invoked as OpenAI handlers and remain unchanged. Document that
Claude's legacy edit hook still expects its conventional branch grammar; a
cross-host handoff creates a corresponding conventional branch from the reviewed
commit in a separate worktree, rather than pretending that hook recognizes the
new namespace. All previous Claude branch cases retain their behavior.

## Consequences

Existing paths and Claude behavior remain stable, and updating a shared skill
does not require rewriting its OpenAI procedure. The .claude directory name
remains historical. Native hook coverage and user trust vary by OpenAI host;
plain chat remains a planning/handoff environment. These limits are visible.

## Alternatives considered

- Copy entire workflow bodies into a second platform tree: rejected for drift.
- Move every canonical file into a new shared package now: rejected for needless
  merge conflict and regression risk during concurrent Claude development.
- Rely only on CLAUDE.md fallback or a symlink: rejected because it does not
  solve discovery, tool mapping, provenance or portable Windows checkout.
- Disable gates for OpenAI or fabricate Claude events: rejected because apparent
  parity would destroy the workflow's safety and evidence guarantees.

## References

- Existing ADR-0001, ADR-0004, ADR-0027, ADR-0032, ADR-0036, ADR-0058, ADR-0061,
  ADR-0070 and ADR-0076.
- Official OpenAI AGENTS.md and skill documentation, checked 2026-09-19;
  implementation should cite working official URLs in the user guide.
- https://learn.chatgpt.com/docs/hooks (native events, trust, schemas and limits).
- https://learn.chatgpt.com/docs/build-skills (discovery and skill structure).
