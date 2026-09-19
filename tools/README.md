# tools/

Scripts for CI/release enforcement and post-dispatch worktree enforcement.
Intended for use by humans and subagents.

**Note:** not all tools in this directory are advisory or read-only. `worktree-guard.sh`
is an enforcement tool that deletes worktrees, removes local branches, and deletes
remote branches — it modifies repository state. `ci-checks.sh` is advisory/read-only.

---

## worktree-guard.sh

For the separate OpenAI adapter entrypoint, see [openai_workflow.py](openai_workflow.py)
and its [execution/evidence contract](../docs/openai-workflow.md). It resolves
canonical instructions, reports observed prerequisites, validates independent
isolation/identity and fresh proof, then delegates to unchanged guarded verbs.
`gen_openai_skills.py --check` checks the declared ship-only D6 router inventory;
`workflow_branch.py` is the shared conventional/Codex kind-and-issue classifier.
These helpers add no installer, native hook coverage or pipeline engine.

Post-dispatch worktree leak-guard and root ff-sync. **Enforcement tool** — modifies
repository state (deletes worktrees, local branches, and remote branches). Invoked by
the `/ship` orchestrator after each isolated `implementer` or `reviewer` dispatch
(per ADR-0058 D3 + [ADR-0041](../decisions/0041-origin-main-source-of-truth.md) D1/D3).

### Subcommands

**`branch-restore <expected-branch>`** — checks whether the current worktree drifted
off `<expected-branch>` and restores it via ff-only checkout of `origin/main`. If the
current HEAD has diverged from `origin/main` (local commits exist), exits **non-zero**
with a divergence message — the old silent force-reset is retired (ADR-0058 D3). A
dirty tree is a no-op (safe exit 0 — orchestrator's own work is left untouched).

**`root-sync`** — ff-syncs the root repo to `origin/main` after a successful merge.
Non-zero on dirty tree or ff failure.

**`prune`** — removes landed dispatch worktrees (`agent-*` prefix only) to prevent
unbounded accumulation. Two reclamation paths:
1. **Landed path:** branch has a merged PR + no open PR.
2. **No-PR reclamation path (ADR-0058 D3):** worktree with no PR at all is reclaimed
   when clean + 0-ahead-of-main + older than 24 hours (avoids racing in-flight
   dispatches).
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
