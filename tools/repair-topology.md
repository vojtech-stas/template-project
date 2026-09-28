# Root topology repair — operator/orchestrator procedure

**⛔ NEVER run by a dispatched implementer/reviewer subagent.** Per
[ADR-0036](../decisions/0036-worktree-isolation-all-dispatches.md) D3, a
worktree-isolated dispatch never mutates the root repo. This procedure
touches the ROOT checkout directly (the shared `.git` all worktrees point
at) — it is executed by the **orchestrator** (main agent, human-driven
session) at production-verify time, never inside an isolated dispatch.

## When to run this

`tools/deploy-handshake.sh` (run with no arguments, from the root checkout or
any worktree — it resolves the root via `git-common-dir`'s parent
automatically) reports a `DEPLOY-GAP DETECTED` banner and exits non-zero.
Common triggers per PRD #1075 criterion 4:

- The root checkout's `HEAD` is **detached** (no branch to compare against —
  this IS the deploy-gap state the PRD's forensic audit found: the #879 hook
  consolidation shipped six weeks ago and never executed once because the
  root checkout sat detached at an old sha).
- `core.hooksPath` is not `.claude/pipeline/githooks` (`.claude/pipeline/githooks/install.sh` was never run,
  or ran against the wrong checkout).
- The on-disk content of `.claude/pipeline/hooks/` or `.claude/settings.json` differs
  from what is committed on the branch the root checkout's `HEAD` names.

## Repair steps

1. **Verify a clean tracked tree at the root.** From the root checkout:
   ```
   git status --porcelain
   ```
   If this is non-empty, STOP — do not proceed until the tree is clean or
   the uncommitted changes are consciously handled (stash/commit/discard by
   the operator's own judgment). This repair NEVER runs against a dirty tree.

2. **Attach to `main`, fast-forward only.**
   ```
   git checkout main && git pull --ff-only origin main
   ```
   `--ff-only` refuses to proceed if `main` has diverged locally — that is
   the correct, safe failure mode; a real divergence needs human judgment,
   not an automated force-move.

3. **Install `.claude/pipeline/githooks` as `core.hooksPath`.**
   ```
   bash .claude/pipeline/githooks/install.sh
   ```

4. **Re-run the handshake — it MUST pass.**
   ```
   bash tools/deploy-handshake.sh
   ```
   Exit 0 confirms: `HEAD` resolves to `refs/heads/main` (attached, not
   detached), `core.hooksPath` is `.claude/pipeline/githooks`, and the running
   `.claude/pipeline/hooks/` + `.claude/settings.json` content matches what is
   committed on `main`. If it still fails, do not consider the repair done —
   diagnose the reported mismatch before closing out.

5. **Confirm attachment explicitly** (belt-and-suspenders, matches PRD #1075
   criterion 4's verification clause):
   ```
   git symbolic-ref -q HEAD
   ```
   Must print `refs/heads/main`. A non-zero exit or empty output means `HEAD`
   is still detached — the repair did not take; do not proceed.

## Rollback

If the repair needs to be undone (e.g., `main` moved to content that turns
out to be broken), roll back with:

```
git checkout main && git reset --hard <old-sha>
```

**This form is mandatory — never `git checkout <old-sha>` on its own.** A
bare `git checkout <old-sha>` re-detaches `HEAD`, recreating the exact
deploy-gap class this PRD closes. `git checkout main && git reset --hard
<old-sha>` moves `main` itself back to `<old-sha>` while `HEAD` **stays
attached** to `refs/heads/main` throughout. Confirm post-rollback:

```
git symbolic-ref -q HEAD   # must still print refs/heads/main
bash tools/deploy-handshake.sh   # must still pass (or report the expected
                                  # pre-existing mismatch you rolled back to)
```

## What this slice does NOT do

This slice (#1079) ships the handshake script, the session-start loud-warn
wiring, the CI self-test backstop, and this document — it does **not**
execute the repair above against the real root checkout. That execution is
explicitly reserved for the orchestrator's production-verify step, per the
slice body's own scope boundary and per rule #10 (main-agent meta-output
discipline doesn't apply to git-topology operations, but the isolation
boundary in ADR-0036 D3 does: a dispatched agent's worktree is its exclusive
write domain, and the root repo is out of bounds).
