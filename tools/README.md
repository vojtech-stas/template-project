# tools/

Scripts for CI/release enforcement and post-dispatch worktree enforcement.
Intended for use by humans and subagents.

**Note:** not all tools in this directory are advisory or read-only. `worktree-guard.sh`
is an enforcement tool that deletes worktrees, removes local branches, and deletes
remote branches — it modifies repository state. `ci-checks.sh` is advisory/read-only.

---

## worktree-guard.sh

Post-dispatch worktree leak-guard and root ff-sync. **Enforcement tool** — modifies
repository state (deletes worktrees, local branches, and remote branches). Invoked by
the `/ship` orchestrator after each isolated `implementer` or `reviewer` dispatch
(per ADR-0058 D3 + [ADR-0041](../decisions/0041-origin-main-source-of-truth.md) D1/D3).

Both branch roles (integration, release) are resolved per-invocation via
[`tools/pipeline_config.py`](pipeline_config.py) (per
[ADR-0089](../decisions/0089-per-repo-pipeline-identity.md) D1) — never
hardcoded. This repo's configured values are `develop`/`main`; the
descriptions below use "the integration branch" / "the release branch" for
the resolved role, not a literal.

### Subcommands

**`branch-restore <expected-branch>`** — checks whether the current worktree drifted
off `<expected-branch>` and restores it via ff-only checkout of `origin/<integration>`.
If the current HEAD has diverged from `origin/<integration>` (local commits exist),
exits **non-zero** with a divergence message — the old silent force-reset is retired
(ADR-0058 D3). A dirty tree is a no-op (safe exit 0 — orchestrator's own work is left
untouched). Restoring to the release branch itself hard-aligns unconditionally instead
(the release branch is never developed locally — see #950).

**`root-sync`** — ff-syncs the root repo to `origin/<integration>` after a successful
merge. Non-zero on dirty tree or ff failure.

**`prune`** — removes landed dispatch worktrees (`agent-*` prefix only) to prevent
unbounded accumulation. Two reclamation paths:
1. **Landed path:** branch has a merged PR + no open PR.
2. **No-PR reclamation path (ADR-0058 D3):** worktree with no PR at all is reclaimed
   when clean + 0-ahead-of-the-integration-branch + older than 24 hours (avoids
   racing in-flight dispatches).
After removing a worktree, deletes the local branch and the remote branch if present.
Exits **non-zero** if any targeted worktree could not be removed.

### Exit codes

All subcommands exit **non-zero on unrepaired violations** (ADR-0058 D3). The
orchestrator detects guard failures via the exit code. Unknown modes also exit non-zero.
Soft-degrade on transient network/fetch errors: root-sync fetch failure exits 0;
prune with gh absent exits 0 with no work done.

### Safety guarantees

Only removes worktrees whose `basename` starts with `agent-` (the harness
dispatch-tree prefix). Skips the current worktree and the root repo worktree. A
worktree with a live-pid lock is never forcibly removed. These guards make it
impossible to remove the root repo, orchestrator session trees, or non-dispatch trees
regardless of their branch state.

---

## release.py

Release-mode primitives for `/ship release <version> lanes <N>`
(per [ADR-0090](../decisions/0090-release-mode.md) D1/D3/D4). Four subcommands:

**`freeze <V> --next <W> --features <list>|none`** — admits every open `bug`
and the owner's listed `feature`s (with their slices) to milestone `<V>`,
moves every other open feature to `<W>`; refuses on any unclassified open
issue, naming it.

**`lanes <V> [--evidence <sweep.json>] [--priority <file>]`** — groups `<V>`'s
lane-bound bugs into disjoint file-path groups; a bug with no cited path runs
exclusively. This slice ships the bounded `N`-lane form only.

**`packet --sha <sha> <n>…`** (its builder is invoked internally by
`tools/pipe/dispatch --lane`) — builds a lane's dispatch brief: the sha, then
per bug its `path:line` or `path:start-end` refs, a ±20-line excerpt at that
sha, and its check (resolved exactly as `verify` resolves it) or
`CHECK: MISSING`, with the check's source on the next line: `(from issue)`
or `(from lane PR #<m>)`. Reads only issue bodies, comments and lane PRs whose
`author_association` is `OWNER`, `MEMBER` or `COLLABORATOR`. Every gh, git and
check subprocess decodes as UTF-8, and the packet is written to stdout as UTF-8
bytes, whatever the host locale; `dispatch --lane` delivers the packet before
it records its `dispatch` span, so a failed write leaves no span. When a gh
read the packet depends on fails, no packet is printed and `dispatch --lane`
refuses with no span.

**`verify <n>…`** — runs each bug's resolved check (the issue's own `Check:`
line, or else the `Check #<n>:` line of its most recently merged `lane` PR
that closes it on a whole `Closes #<n>` line), with one layer of surrounding
backticks stripped, and prints `PASS|FAIL|MISSING #<n>`; exits 0 iff every
line is PASS. The lane PR comes from the issue's REST timeline
(`cross-referenced` events), never the search index, and its check counts only
when the PR's author is trusted as above. A gh read that fails or cannot be
parsed prints `UNCONFIRMED #<n>`, never `MISSING`.

Both branch roles resolve per-invocation via
[`tools/pipeline_config.py`](pipeline_config.py) — never hardcoded, per
[ADR-0089](../decisions/0089-per-repo-pipeline-identity.md) D1.
