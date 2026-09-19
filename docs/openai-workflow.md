# OpenAI workflow execution

Status: ADR-0086 D6, first slice only. The committed entrypoint and ship router
share the canonical workflow. Native hooks (#1441), complete skill discovery
(#1442), and full fresh-clone readiness/PRD closure verification are pending.
Specification approval is not implementation review or production PASS.

## Start and resolve instructions

Open a repository-capable Codex task at the Git root. Read [AGENTS.md](../AGENTS.md),
then its canonical sources. Existing prerequisites are Python, Git, GitHub CLI
authentication and Bash. This adapter installs nothing and changes no account,
PATH, execution policy, hook trust or promotion acknowledgement.

    python tools/openai_workflow.py instructions --path tools/openai_workflow.py
    python tools/gen_openai_skills.py --check
    python tools/openai_workflow.py preflight

The first command returns the global sources plus isolation rules. Resolution
uses gen_rules.SCOPE_TARGET and SCOPE_PATHS; Markdown also loads docs rules.
OpenAI additions map AGENTS.md and .agents/** to isolation, and skill routers
to slicing. New/deleted relative paths work without requiring the changed file
to exist. Both separator spellings are accepted; absolute/traversing paths,
missing sources and malformed generated headers refuse. No browser starts.

Preflight without current controller observations exits 2 with a named handoff.
With observations it may report bounded D6 readiness, never full_prd_ready.
The ship-only parity check says explicitly that complete discovery is pending.
Normal clones carry the router; no installer, symlink or regeneration is needed.

The [official instruction discovery contract](https://learn.chatgpt.com/docs/agent-configuration/agents-md)
and [official skill contract](https://learn.chatgpt.com/docs/build-skills) describe
host discovery; the controller must still observe its current capabilities.

## Native role mapping

| Canonical operation | OpenAI execution |
|---|---|
| Read / Glob / Grep | Available native file/search tools; read canonical bodies explicitly |
| Edit / Write | Native patch tool, limited to the worker's assigned worktree |
| Bash | Native shell with explicit cwd and captured output/exit |
| Agent | Controller-only native worker dispatch and host result retrieval |
| Claude model frontmatter | Not native configuration; inherit host model unless user overrides |
| AskUserQuestion | Host question surface in controller context when needed |
| Browser proof | Observed CUA or available Playwright; real clicks, screenshot bytes, rendered text, console results |
| SendUserFile | Host artifact attachment or local file link; no invented delivery API |

Implementer, reviewer and qa-tester read their existing .claude/agents bodies with
this mapping. The reviewer stays blind and independent; qa-tester regenerates
its proofs. The same critic rubrics, joint approval, original review history,
three-round strict stops and human-only promotion apply. Unavailable workers
produce a bounded handoff, not self-review. Only the controller dispatches.
The complete nine-role/six-skill capability inventory belongs to #1442.

A connected CUA surface can provide screenshot, innerText/domSnapshot and
dev.logs evidence. No GIF API is assumed or required on OpenAI. Browser-tool
readiness (including a throwaway real-click smoke test) proves tooling only;
it never supplies production QA evidence for an actual slice.

## Identity and isolation

Use codex/<type>/<issue>-<slug>; type is from the existing closed branch set.
tools/workflow_branch.py returns kind, issue, validity and requires_regression.
Classification preserves conventional fix/ and hotfix/ prefix selection while
legality still requires an issue number. Invalid Codex input never gains a kind.
Ship and R-PROVE select fixes OR the existing root-cause label. Health retains
its existing label behavior and historical activation boundary.

Controller and worker checks are independent. Before dispatching mutation the
controller captures absolute top-level, common Git directory, matching registered
worktree entry, branch, starting SHA and clean status. Compare them to the intended
repository and both controller/shared roots. Wrong/shared/unregistered/ambiguous
trees refuse. The worker repeats before-write assertions. After completion the
controller repeats the observations and retains existing branch-restore guards.
No worker-populated worktreePath field proves native isolation.

For the first slice, the approved read-only positive probe and actual shared-root
refusal precede implementation. The controller records native dispatch identities
and Git results explicitly until the helper exists. Refresh observations older
than 30 minutes before their acceptance. Validate the same real slice with the
candidate helper, including the real shared-root and missing-proof refusals.
Do not reopen tools/pipe/dispatch to obtain demonstration data.

### Controller observation format

The controller supplies an explicit durable proof root outside all disposable
worktrees, and a private observations JSON file. Artifact references are objects
with path (relative to that root) and sha256 (digest of exact bytes).
Host item references have thread_id, turn_id and item_id. For a long-running
turn they additionally name clock_item_id: a preceding native completed command
in that same turn which printed only the actual current UTC ISO timestamp (for
example datetime.now(timezone.utc).isoformat()). Capture that clock BEFORE the
actions, not when exporting older evidence. Its fresh output anchors the window;
export time or a worker-authored timestamp alone cannot refresh old actions.

Observations has schema=1, platform=codex, provenance=controller-observed-native,
controller_id (actual host ID), repository (owner/name), started_at (UTC),
shared_roots (absolute controller/root paths), and:

- expected: worktree, common, branch, start_sha, head and slice.
- host_records: artifact references to actual, unmodified native read_thread
  responses (schemaVersion=1; thread.id/kind; turns with items). Preserve complete
  command output, not truncated summaries. Native record acquisition is the
  controller's duty; the adapter has no universal host-discovery API.
- workers: id, role, dispatch and dispatch_witness for each observed worker.
  dispatch references the captured native tool result, containing controller_id
  and native_tool_result.agent_id (or result.agent_id). dispatch_witness identifies
  the controller's completed native command that hashed those captured bytes;
  its output is exactly the hex SHA-256 plus an optional newline.
- git: top, common, registry, branch, head, status item references. These are
  controller native commands executed with the worker cwd and the corresponding
  Git query, with exact output matching fresh local queries.

The six queries are rev-parse --show-toplevel; rev-parse --path-format=absolute
--git-common-dir; worktree list --porcelain; branch --show-current; rev-parse HEAD;
status --porcelain=v1. The native turn start or preceding clock command must fall
inside the exercise and 30-minute acceptance window. Final results require completed turns; completed command items
in an active controller turn are observable without inventing a completion time.
Completed turns also require fresh completion times. Ambiguous IDs, truncated outputs or missing records
refuse. A record export/digest is a correspondence check, not cryptographic proof
that arbitrary user-written JSON originated at the host. The trust input is the
controller's independently retrieved native record; never accept a worker's
replacement export or self-declared receipt as that source.

    python tools/openai_workflow.py isolation --before --observations <observations.json> --proof-root <durable-root> --controller-id <actual-controller-id>
    python tools/openai_workflow.py session --worker-id <actual-worker-id> --observations <observations.json> --proof-root <durable-root> --controller-id <actual-controller-id>
    python tools/openai_workflow.py spec --observations <observations.json> --proof-root <durable-root> --controller-id <actual-controller-id>

Session prints a separate explicit startup receipt; the controller preserves its
exact bytes. It is not a hook beacon. Before mutation, head must equal start_sha
and the issue-bound branch must match. For verification, freshly observed detached
HEAD is allowed; its expected branch is empty and registration must say detached.
No receipt falls back to a date or invented orchestrator ID.

The unchanged guarded verbs receive CLAUDE_SESSION_ID=codex:<actual-caller-id>.
That variable is a legacy attribution alias, not a claim Claude fired hooks.
The adapter consolidates PATH case-insensitively and sets process-local
PYTHONUTF8=1 / PYTHONIOENCODING=utf-8 for subprocesses. Windows callers that inherit
both Path and PATH must similarly normalize the child environment before Bash/Git
or tests; never change the global environment. Record the actual test environment
separately: live UTF-8 requirements do not silently redefine a historical baseline.

## Proof-gated completion

The [qa-tester route table](../.claude/agents/qa-tester.md) is authoritative.
The adapter reads it at runtime; health's new-path mirror is regression-tested.
AGENTS.md/.agents/** require command-run AND static. Slice #1440 also changes
dashboard/health.py, so browser is required too. A caller cannot choose a weaker
route. Native-hook classes are not implemented in this D6 inventory and refuse.

The private bundle identifies repository, slice, pr, prd, tested_sha, scope
(slice or prd), changed_paths, qa_id, reviewer_id and reviewed_sha (the reviewed
PR head, distinct from the tested merge SHA). review_head references the
reviewer's native git rev-parse HEAD command; a changed live PR head refuses.
It references receipt,
verdict, review (artifacts) and qa_result/review_result (native final-message
items, type=agentMessage and phase=final_answer in the observed read_thread schema).
The independent native final text must exactly equal the artifact bytes.
Review must APPROVE; the complete canonical QA trailer must be SUCCESS/PASS,
contain all required fields and have no unresolved assertion. PROOF_SOURCE uses
the QA receipt; ENV identifies the tested SHA and UTC environment start.
ARTIFACTS lists the bundle's portable artifact names, comma-separated.

Each proofs entry has class, head, command and artifacts. head is the qa-tester's
native Git HEAD command; command is its completed native evidence command, both
from the verified worktree. Artifact bytes are digest-checked:

- command-run: artifacts.output exactly matches captured host command output.
- static: same output contract plus the actual static assertion's grep count=N.
- browser: artifacts.screenshot (PNG/JPEG), rendered (UTF-8 visible text), meta
  (actual /api/meta JSON), console (JSON list). Capture real Run-board and
  health-strip Refresh clicks and #health-strip-content; rendered proof identifies
  the actual slice and PR. The evidence command prints the captured manifest:
  tested_sha, interaction=real-browser, declared_behavior=PASS, actions containing
  runboard-refresh and health-strip-refresh, selectors containing
  #health-strip-content, artifacts mapping those names to their byte digests.
  This command preserves the qa-tester's actual CUA/Playwright results; it does
  not manufacture clicks. Retain native browser action records for review.
  controller_witness references a separate controller command retaining the same
  manifest after independently inspecting those native actions and bytes. A QA
  manifest alone cannot satisfy browser proof; no GIF API is required.
  Meta must match SHA, stale=false, with fresh started_at; console must be empty.
  Existing unrelated health failures stay visible in rendered evidence.

The controller keeps original exports private and publishes sanitized, portable
proofs. Tests fabricate structurally equivalent records only in isolated temporary
directories; those fixtures are never live provenance or production logs.

    python tools/openai_workflow.py qa-verify --bundle <bundle.json> --observations <observations.json> --proof-root <durable-root> --controller-id <actual-controller-id>
    python tools/openai_workflow.py prd-close --bundle <bundle.json> --observations <observations.json> --proof-root <durable-root> --controller-id <actual-controller-id>

Validation occurs before delegation: identity, independence, actual bytes, complete
verdict, timestamp/window, current revision, all required route classes, actual
merged PR/base/files/issue binding and repository must agree. The verified
checkout must be clean: matching HEAD alone cannot certify uncommitted edits. Invalid evidence
exits nonzero with ZERO downstream PASS/close calls. Valid calls pass argv and
the real controller alias to unchanged guarded verbs and propagate their exit.
A slice result omits the parent PRD from the PASS recording so it cannot become
a PRD-closure credential. Both PRD-scoped verification and closure require all
current children closed. PRD observations add expected.prd_base, independently
recorded at PRD start, and changed_paths covers its cumulative diff to tested_sha,
not merely the last slice. A possibly truncated GitHub comparison refuses.
prd-close also requires the existing recorded-PASS prerequisite. #1436 remains open until
the full integrated production checks and all children are complete.

## Delivery and overlap

Generate/stage docs explicitly, commit with normal hooks and truthful authorship,
run CI after the final commit, push the assigned branch, then use
tools/pipe/pr-open --base develop. If core.hooksPath is unset, use the per-command
git -c core.hooksPath=.githooks commit; never bypass hooks or alter shared config.
Only the independent reviewer invokes pr-merge after its APPROVE and required CI.
Closes references targeting develop do not prove issue closure (#1232).

Before overlapping merge, controller and reviewer refresh PR #1435 state, head,
base, merge result and complete files, plus this PR and develop. Serialize the
actual landing order, reconcile qa-tester.md (and dashboard/README.md if needed),
regenerate outputs and review the resulting head. Changes invalidate old clearance.
Preserve its DASH_OPEN_BROWSER opt-in behavior: unset/empty does not open; any
nonempty value, including 0, opts in. Do not copy its server patch. The #1440
health classifier adds no dashboard operational interface, so dashboard/README.md
is a conditional overlap target, not an unconditional edit.

Claude's unchanged edit hook still expects conventional branch grammar. A
cross-host continuation creates the corresponding conventional issue branch
from the reviewed commit in a separate registered worktree. It never renames
or moves the active Codex worker and never pretends the legacy hook knows codex/.

The ADR-0086 propagation scan dispositions remain authoritative: implementer,
reviewer, qa-tester and ship are updated here. Existing critic bodies and to-prd
retain their unchanged rubric/loop semantics, with native invocation supplied by
the caller. Worktree-guard patterns select harness trees rather than branch kinds.
Repeat runtime-reference and branch/issue-consumer scans before review; new hits
require explicit disposition, not automatic cross-repo rewriting.

## Verification and limitations

    python -m unittest tests.test_openai_workflow tests.test_openai_skills -v
    python tools/gen_rules.py --check
    python tools/gen_repo_map.py --check
    python tools/gen_openai_skills.py --check

Run focused branch/health/proof/pipe regressions with separate fixture logs.
For baseline comparison use the exact supplied Python/pytest prerequisites,
arguments and environment, distinct candidate logs/tmp/proof paths, and retain
XML plus complete output/exits. Compare test IDs: no added failures/errors/skips,
no missing existing tests, and all new tests pass without skips. The supplied
ade5f205 baseline is 890 passed, 28 failed, 3 errors, 4 skipped, plus 197 subtests;
it is not green. Non-regression does not override required CI failures.

Plain ChatGPT without repository, shell or independent workers can prepare a
handoff with approved issue/spec links, expected repo/branch/SHA and missing
capabilities. It cannot claim implementation, test execution, review, merge or
capture. Native hooks absent or untrusted remain unverified; full closure cannot
use bootstrap as a permanent exemption.
