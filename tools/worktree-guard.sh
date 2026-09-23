#!/bin/bash
# worktree-guard.sh — post-dispatch worktree leak-guard + root ff-sync (ADR-0058 D3).
#
# CONTRACT: Orchestrator-invoked via Bash after each isolated Agent dispatch.
#   NOT a Claude Code event hook (per ADR-0015). Three modes (mode as $1):
#
#   Both branch roles (integration, release) are resolved per-invocation via
#   tools/pipeline_config.py (ADR-0089 D1) — never hardcoded. This repo's
#   configured values are develop/main; the examples below use those names,
#   but every git operation below actually runs against the resolved role.
#
#   branch-restore <expected-branch>
#     git fetch origin <integration> (soft-degrade on failure); if the current
#     worktree drifted off <expected-branch> AND the tree is clean, attempts
#     ff-restore via `git checkout -B <expected> origin/<integration>`.
#     FF-ONLY (ADR-0058 D3): if the current HEAD is NOT an ancestor of
#     origin/<integration> (local commits exist), exits NON-ZERO with a
#     divergence message — does NOT force-reset. The silent reset --hard
#     behaviour is RETIRED.
#     No-op (exit 0) if tree is dirty or already on the correct branch.
#     Exits NON-ZERO if an unrepaired violation (diverged branch) remains.
#
#   root-sync
#     Resolves the root repo via `git --git-common-dir` → dirname (same pattern as
#     .claude/hooks/log-event.sh). If the root tree is clean, ff-syncs to
#     origin/<integration>: `git -C <root> checkout <integration> &&
#     git -C <root> merge --ff-only origin/<integration>`.
#     STRICT: ff-only, clean-only, non-zero on failure. Never reset/force/non-ff.
#     Implements ADR-0041 D3 carve-out: orchestrator MAY ff-sync root post-merge.
#     Per ADR-0070 D1 (as amended by ADR-0089 D1): the integration branch is
#     the configured role; the release branch advances only by promotion.
#     Exits NON-ZERO if an unrepaired violation (cannot ff-sync) remains.
#
#   prune
#     Removes landed and no-PR-reclaimable dispatch worktrees to prevent unbounded
#     accumulation.
#     SAFETY: only removes worktrees whose path basename starts with "agent-"
#     (the harness dispatch-tree prefix per ADR-0036). This is the PRIMARY safety
#     boundary — it makes it IMPOSSIBLE to remove the root repo, an orchestrator
#     session tree (e.g. fervent-colden-*), or any other non-dispatch tree.
#     SECONDARY guard (landed path): branch must be LANDED — defined as having a
#     MERGED PR AND no OPEN PR (via gh pr list --state merged/open). Correctly
#     handles squash-merged branches whose remote ref persists.
#     NO-PR RECLAMATION (ADR-0058 D3): a dispatch worktree with NO PR of any kind
#     (no open, no merged) is reclaimed when ALL THREE conditions hold:
#       1. Working tree is clean (no uncommitted changes, no untracked files)
#       2. Branch is 0-ahead of origin/<integration> (no local commits beyond it)
#       3. Age threshold: worktree directory mtime > 24 hours ago
#     This prevents accumulation of agent worktrees abandoned before opening a PR.
#     The age threshold avoids racing a dispatch in progress.
#     Requires gh; soft-degrades if gh is absent.
#     LOCK HANDLING: locked worktrees only force-removed when locking pid is dead;
#     alive pid or unparseable reason → skip (soft-degrade).
#     BRANCH CLEANUP: after removing a worktree, deletes the local branch and the
#     lingering remote branch if it still exists on origin.
#     DEPTH guards: skip the current worktree; skip the root worktree.
#     Exits NON-ZERO if any unremovable violation remains after the sweep.
#
# VIOLATION SEMANTICS (ADR-0058 D3): subcommands exit NON-ZERO on unrepaired
# violations. The old "exit 0 always" soft-degrade contract for branch-restore
# and prune violations is RETIRED — the orchestrator must detect guard failures.

MODE="$1"

# Resolve both configured branch roles once per invocation (ADR-0089 D1).
# Located relative to THIS script's own path (S1-c) — never via $REPO_ROOT
# or $(git rev-parse --show-toplevel) of the cwd repo, so this resolves
# correctly even when the cwd is a foreign repo with no tools/ directory
# of its own. The release role drives branch-restore's hard-align path;
# the integration role drives ff-sync (branch-restore/root-sync) and
# prune's zero-ahead check.
_WG_TOOLS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INTEGRATION_BRANCH="$(python3 "$_WG_TOOLS_DIR/pipeline_config.py" integration)" || {
  echo "ERROR: worktree-guard: cannot resolve the integration branch (tools/pipeline_config.py failed)" >&2
  exit 1
}
RELEASE_BRANCH="$(python3 "$_WG_TOOLS_DIR/pipeline_config.py" release)" || {
  echo "ERROR: worktree-guard: cannot resolve the release branch (tools/pipeline_config.py failed)" >&2
  exit 1
}

case "$MODE" in
  branch-restore)
    EXPECTED="$2"
    if [ -z "$EXPECTED" ]; then
      exit 0
    fi

    # RELEASE HARD-ALIGN (#950): local release-role branch must always equal
    # origin's, because it is never developed locally — it carries no
    # local-unique commits. When branch-restore targets the release branch,
    # hard-align unconditionally (fetch + reset --hard) regardless of
    # whether local is ahead, behind, or diverged. This makes the root
    # worktree robust to the fix-on-PR-branch dispatch pattern that can
    # fast-forward the local release branch onto an un-merged feature commit
    # (ADR-0041: the remote release ref is source of truth; ADR-0058 D3
    # refinement: release-specific hard-align; ADR-0089 D1: role resolved,
    # not literal).
    if [ "$EXPECTED" = "$RELEASE_BRANCH" ]; then
      git fetch origin "$RELEASE_BRANCH" 2>/dev/null || {
        echo "ERROR: branch-restore: git fetch origin ${RELEASE_BRANCH} failed" >&2
        exit 1
      }
      LOCAL_RELEASE=$(git rev-parse "$RELEASE_BRANCH" 2>/dev/null) || true
      REMOTE_RELEASE=$(git rev-parse "origin/$RELEASE_BRANCH" 2>/dev/null) || {
        echo "ERROR: branch-restore: cannot resolve origin/${RELEASE_BRANCH} after fetch" >&2
        exit 1
      }
      if [ "$LOCAL_RELEASE" = "$REMOTE_RELEASE" ]; then
        # Already aligned — no-op.
        exit 0
      fi
      # Hard-align: the release branch is never ahead of origin by design.
      git checkout "$RELEASE_BRANCH" 2>/dev/null || true
      git reset --hard "origin/$RELEASE_BRANCH" 2>/dev/null || {
        echo "ERROR: branch-restore: git reset --hard origin/${RELEASE_BRANCH} failed" >&2
        exit 1
      }
      exit 0
    fi

    git fetch origin "$INTEGRATION_BRANCH" 2>/dev/null || true

    CURRENT=$(git rev-parse --abbrev-ref HEAD 2>/dev/null) || exit 0
    if [ "$CURRENT" = "$EXPECTED" ]; then
      exit 0
    fi

    # Only restore when the tree is clean (no uncommitted orchestrator work).
    DIRTY=$(git status --porcelain --untracked-files=no 2>/dev/null)
    if [ -n "$DIRTY" ]; then
      # Dirty tree — cannot safely restore; not a guard violation.
      exit 0
    fi

    # FF-ONLY CHECK (ADR-0058 D3): refuse non-ff restore.
    # Check if current HEAD is an ancestor of origin/<integration>.
    # If not, local commits exist that would be lost by a force-reset.
    if ! git merge-base --is-ancestor HEAD "origin/$INTEGRATION_BRANCH" 2>/dev/null; then
      DIVERGED_SHA=$(git rev-parse --short HEAD 2>/dev/null)
      ORIGIN_SHA=$(git rev-parse --short "origin/$INTEGRATION_BRANCH" 2>/dev/null)
      echo "ERROR: branch-restore: HEAD ${DIVERGED_SHA} is not an ancestor of origin/${INTEGRATION_BRANCH} ${ORIGIN_SHA} — branch '${CURRENT}' has diverged (expected '${EXPECTED}'). Silent force-reset RETIRED per ADR-0058 D3. Manual intervention required." >&2
      exit 1
    fi

    # Safe to ff-restore: HEAD is an ancestor of origin/<integration>.
    git checkout -B "$EXPECTED" "origin/$INTEGRATION_BRANCH" 2>/dev/null || {
      echo "ERROR: branch-restore: git checkout -B '$EXPECTED' origin/${INTEGRATION_BRANCH} failed" >&2
      exit 1
    }
    exit 0
    ;;

  root-sync)
    # Resolve the root repo via git --git-common-dir (canonical pattern from log-event.sh).
    ROOT="${CLAUDE_PROJECT_DIR:-.}"
    COMMON=$(git -C "$ROOT" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)
    if [ -n "$COMMON" ]; then
      MAIN=$(dirname "$COMMON")
    else
      MAIN="$ROOT"
    fi
    [ -d "$MAIN" ] || exit 0

    # Only ff-sync when the root tree is clean.
    DIRTY=$(git -C "$MAIN" status --porcelain --untracked-files=no 2>/dev/null)
    if [ -n "$DIRTY" ]; then
      echo "WARNING: root-sync: root tree is dirty; skipping ff-sync" >&2
      exit 1
    fi

    git -C "$MAIN" fetch origin "$INTEGRATION_BRANCH" 2>/dev/null || {
      echo "WARNING: root-sync: fetch failed; skipping ff-sync" >&2
      exit 0
    }
    git -C "$MAIN" checkout "$INTEGRATION_BRANCH" 2>/dev/null || {
      echo "ERROR: root-sync: checkout ${INTEGRATION_BRANCH} failed" >&2
      exit 1
    }
    git -C "$MAIN" merge --ff-only "origin/$INTEGRATION_BRANCH" 2>/dev/null || {
      echo "ERROR: root-sync: merge --ff-only failed; root repo has diverged from origin/${INTEGRATION_BRANCH}" >&2
      exit 1
    }
    exit 0
    ;;

  prune)
    # is_branch_landed <branch>
    #   Returns 0 (true) iff the branch has a MERGED PR AND no OPEN PR.
    #   Soft-degrade: if gh is missing or network fails → returns 1 (not landed).
    #   A branch with NO PR or an OPEN PR is NEVER considered landed.
    is_branch_landed() {
      local br="$1"
      # Require gh — hard dependency for merged-PR detection.
      if ! command -v gh >/dev/null 2>&1; then
        return 1  # gh absent → soft-degrade: treat as not landed
      fi
      # Count merged PRs for this branch head.
      local merged_count
      merged_count=$(gh pr list --head "$br" --state merged --json number -q 'length' 2>/dev/null) || return 1
      # Network error or empty output → soft-degrade.
      if [ -z "$merged_count" ]; then
        return 1
      fi
      # No merged PR → not landed.
      if [ "$merged_count" -eq 0 ] 2>/dev/null; then
        return 1
      fi
      # Count open PRs — a branch with an open PR is NEVER pruned.
      local open_count
      open_count=$(gh pr list --head "$br" --state open --json number -q 'length' 2>/dev/null) || return 1
      if [ -z "$open_count" ]; then
        return 1
      fi
      if [ "$open_count" -gt 0 ] 2>/dev/null; then
        return 1
      fi
      # merged > 0 AND open == 0 → landed.
      return 0
    }

    # is_branch_no_pr <branch>
    #   Returns 0 (true) iff branch has NO PR at all (no open, no merged).
    #   Requires gh; returns 1 on any error or gh absence.
    is_branch_no_pr() {
      local br="$1"
      if ! command -v gh >/dev/null 2>&1; then
        return 1
      fi
      local open_count merged_count
      open_count=$(gh pr list --head "$br" --state open --json number -q 'length' 2>/dev/null) || return 1
      merged_count=$(gh pr list --head "$br" --state merged --json number -q 'length' 2>/dev/null) || return 1
      if [ -z "$open_count" ] || [ -z "$merged_count" ]; then
        return 1
      fi
      if [ "$open_count" -eq 0 ] 2>/dev/null && [ "$merged_count" -eq 0 ] 2>/dev/null; then
        return 0
      fi
      return 1
    }

    # is_worktree_clean <path>
    #   Returns 0 (true) iff tree has no uncommitted changes AND no untracked files
    #   (covers the #746/#673 untracked-file leak shape per ADR-0058 D3).
    is_worktree_clean() {
      local wt="$1"
      local dirty
      dirty=$(git -C "$wt" status --porcelain --untracked-files=no 2>/dev/null)
      if [ -n "$dirty" ]; then
        return 1
      fi
      local untracked
      untracked=$(git -C "$wt" status --short --untracked-files=normal 2>/dev/null | grep -c '^??' 2>/dev/null || echo 0)
      if [ "$untracked" -gt 0 ] 2>/dev/null; then
        return 1
      fi
      return 0
    }

    # is_branch_zero_ahead <path>
    #   Returns 0 (true) iff branch at <path> has 0 commits ahead of the
    #   configured integration branch's origin ref.
    is_branch_zero_ahead() {
      local wt="$1"
      local ahead
      ahead=$(git -C "$wt" rev-list --count "origin/${INTEGRATION_BRANCH}..HEAD" 2>/dev/null) || return 1
      if [ -z "$ahead" ]; then
        return 1
      fi
      if [ "$ahead" -eq 0 ] 2>/dev/null; then
        return 0
      fi
      return 1
    }

    # is_worktree_aged <path>
    #   Returns 0 (true) iff worktree directory mtime > 24 hours ago.
    #   Uses python3 for portable mtime check (no GNU stat -c required).
    is_worktree_aged() {
      local wt="$1"
      python3 - "$wt" <<'PY' 2>/dev/null
import sys, os, time
wt = sys.argv[1]
try:
    mtime = os.path.getmtime(wt)
    age_hours = (time.time() - mtime) / 3600
    sys.exit(0 if age_hours > 24 else 1)
except Exception:
    sys.exit(1)
PY
    }

    # Resolve the current worktree path (to implement skip-current guard).
    CURRENT_TREE=$(git rev-parse --show-toplevel 2>/dev/null) || CURRENT_TREE=""

    # Resolve the root repo (same pattern as root-sync above).
    ROOT="${CLAUDE_PROJECT_DIR:-.}"
    COMMON=$(git -C "$ROOT" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)
    if [ -n "$COMMON" ]; then
      ROOT_TREE=$(dirname "$COMMON")
    else
      ROOT_TREE="$ROOT"
    fi

    # Fetch origin so remote state is current for branch-cleanup checks.
    git fetch origin 2>/dev/null || true

    # Soft-degrade if gh is absent — merged-PR and no-PR detection both require it.
    if ! command -v gh >/dev/null 2>&1; then
      git worktree prune 2>/dev/null || true
      exit 0
    fi

    # Track violations: worktrees that should be removed but could not be.
    VIOLATIONS=0

    # Iterate worktrees via porcelain output, parsing worktree + branch lines.
    WORKTREE_PATH=""
    WORKTREE_BRANCH=""
    WORKTREE_LOCKED=""
    WORKTREE_LOCK_REASON=""
    while IFS= read -r line; do
      case "$line" in
        "worktree "*)
          WORKTREE_PATH="${line#worktree }"
          WORKTREE_BRANCH=""
          WORKTREE_LOCKED=""
          WORKTREE_LOCK_REASON=""
          ;;
        "branch refs/heads/"*)
          WORKTREE_BRANCH="${line#branch refs/heads/}"
          ;;
        "locked"*)
          WORKTREE_LOCKED="1"
          WORKTREE_LOCK_REASON="${line#locked}"
          WORKTREE_LOCK_REASON="${WORKTREE_LOCK_REASON# }"
          ;;
        "")
          # End of stanza — evaluate this worktree.
          if [ -n "$WORKTREE_PATH" ]; then
            # PRIMARY SAFETY GUARD: only consider trees whose basename starts with "agent-".
            # This makes it IMPOSSIBLE to remove the root repo, any orchestrator session
            # tree (e.g. fervent-colden-*), or any other non-dispatch tree, regardless of
            # whether their branch is present on origin.
            BASENAME=$(basename "$WORKTREE_PATH")
            case "$BASENAME" in
              agent-*) ;;  # dispatch tree — continue to remaining checks
              *)
                WORKTREE_PATH=""
                WORKTREE_BRANCH=""
                WORKTREE_LOCKED=""
                WORKTREE_LOCK_REASON=""
                continue
                ;;  # not a dispatch tree — skip unconditionally
            esac

            # DEPTH GUARD 1: skip the current worktree.
            if [ "$WORKTREE_PATH" = "$CURRENT_TREE" ]; then
              WORKTREE_PATH=""
              WORKTREE_BRANCH=""
              WORKTREE_LOCKED=""
              WORKTREE_LOCK_REASON=""
              continue
            fi

            # DEPTH GUARD 2: skip the root repo worktree.
            if [ "$WORKTREE_PATH" = "$ROOT_TREE" ]; then
              WORKTREE_PATH=""
              WORKTREE_BRANCH=""
              WORKTREE_LOCKED=""
              WORKTREE_LOCK_REASON=""
              continue
            fi

            # When no branch (detached HEAD), leave it.
            if [ -z "$WORKTREE_BRANCH" ]; then
              WORKTREE_PATH=""
              WORKTREE_LOCKED=""
              WORKTREE_LOCK_REASON=""
              continue
            fi

            # GATE-RUNNING MARKER CHECK (issue #951): skip worktrees with an
            # in-flight gate sentinel BEFORE any removal-eligibility check.
            # A qa-tester or long-running task creates .gate-running at the
            # worktree root on start and removes it on finish. Presence means a
            # live process may be rooted there — removing the worktree would
            # silently kill it. This guard fires regardless of landed/no-PR
            # status so it catches even landed worktrees still running a gate.
            if [ -f "$WORKTREE_PATH/.gate-running" ]; then
              echo "prune: skipped: gate-running marker present in '$WORKTREE_PATH'" >&2
              WORKTREE_PATH=""
              WORKTREE_BRANCH=""
              WORKTREE_LOCKED=""
              WORKTREE_LOCK_REASON=""
              continue
            fi

            # Determine removal eligibility via two paths:
            # PATH A: landed (merged PR + no open PR).
            # PATH B: no-PR reclamation — clean + 0-ahead + aged (ADR-0058 D3).
            SHOULD_REMOVE=0

            if is_branch_landed "$WORKTREE_BRANCH"; then
              SHOULD_REMOVE=1
            fi

            if [ "$SHOULD_REMOVE" -eq 0 ]; then
              if is_branch_no_pr "$WORKTREE_BRANCH"; then
                if is_worktree_clean "$WORKTREE_PATH" && \
                   is_branch_zero_ahead "$WORKTREE_PATH" && \
                   is_worktree_aged "$WORKTREE_PATH"; then
                  SHOULD_REMOVE=1
                fi
              fi
            fi

            if [ "$SHOULD_REMOVE" -eq 1 ]; then
              # LOCK HANDLING: if worktree is locked, check if the locking pid is alive.
              # Only override the lock if the pid is confirmed dead; skip (soft-degrade)
              # if the pid is alive or cannot be parsed.
              REMOVED=0
              if [ -n "$WORKTREE_LOCKED" ]; then
                # Try to parse a numeric pid from the lock reason (e.g. "locked by pid 12345").
                LOCK_PID=$(printf '%s' "$WORKTREE_LOCK_REASON" | grep -oE '[0-9]+' | head -1)
                if [ -n "$LOCK_PID" ]; then
                  if kill -0 "$LOCK_PID" 2>/dev/null; then
                    # Pid is alive — skip this worktree (soft-degrade).
                    WORKTREE_PATH=""
                    WORKTREE_BRANCH=""
                    WORKTREE_LOCKED=""
                    WORKTREE_LOCK_REASON=""
                    continue
                  fi
                  # Pid is dead → stale lock; safe to force-remove.
                  if git worktree remove -f -f "$WORKTREE_PATH" 2>/dev/null; then
                    REMOVED=1
                  fi
                else
                  # Cannot parse a pid → soft-degrade: skip this worktree.
                  WORKTREE_PATH=""
                  WORKTREE_BRANCH=""
                  WORKTREE_LOCKED=""
                  WORKTREE_LOCK_REASON=""
                  continue
                fi
              else
                # Not locked — standard force-remove.
                if git worktree remove --force "$WORKTREE_PATH" 2>/dev/null; then
                  REMOVED=1
                fi
              fi

              if [ "$REMOVED" -eq 1 ]; then
                # BRANCH CLEANUP: delete local branch + lingering remote branch.
                # git branch -D: ignore error if branch is checked out elsewhere.
                git branch -D "$WORKTREE_BRANCH" 2>/dev/null || true
                # Only push --delete if the remote ref still exists.
                if git ls-remote --exit-code origin "$WORKTREE_BRANCH" >/dev/null 2>&1; then
                  git push origin --delete "$WORKTREE_BRANCH" 2>/dev/null || true
                fi
              else
                echo "WARNING: prune: could not remove worktree '$WORKTREE_PATH'" >&2
                VIOLATIONS=$((VIOLATIONS + 1))
              fi
            fi
          fi
          WORKTREE_PATH=""
          WORKTREE_BRANCH=""
          WORKTREE_LOCKED=""
          WORKTREE_LOCK_REASON=""
          ;;
      esac
    done < <(git worktree list --porcelain 2>/dev/null; echo "")

    # Clear stale dir-gone worktree refs.
    git worktree prune 2>/dev/null || true

    if [ "$VIOLATIONS" -gt 0 ]; then
      echo "ERROR: prune: $VIOLATIONS worktree(s) could not be removed" >&2
      exit 1
    fi
    exit 0
    ;;

  *)
    # Unknown mode — exit non-zero per ADR-0058 D3 (guards never fail silently).
    echo "ERROR: worktree-guard: unknown mode '$MODE'; valid: branch-restore, root-sync, prune" >&2
    exit 1
    ;;
esac
