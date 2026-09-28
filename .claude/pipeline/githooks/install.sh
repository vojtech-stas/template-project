#!/bin/sh
# .claude/pipeline/githooks/install.sh
#
# Purpose:
#   One-shot, idempotent setup for project-claude's tracked git hooks.
#   Points this clone's git at the in-repo `.claude/pipeline/githooks/` directory so that
#   `.claude/pipeline/githooks/pre-commit` and `.claude/pipeline/githooks/commit-msg` run on every commit.
#
# When to run:
#   Once per fresh clone (or worktree, if hook config doesn't carry over).
#   Re-running is safe — the operation is idempotent.
#
# What it does:
#   git config core.hooksPath .claude/pipeline/githooks
#
#   This is the single effective operation. After it runs, `git commit`
#   invokes `.claude/pipeline/githooks/pre-commit` and `.claude/pipeline/githooks/commit-msg` instead of the
#   default `.git/hooks/`.
#
# See:
#   - decisions/0004-bypass-prevention.md (D3 layer 1)
#   - .claude/pipeline/githooks/pre-commit
#   - .claude/pipeline/githooks/commit-msg (slice #1041)

set -eu

# Anchor at the repo root so the relative path is correct regardless of
# where the user invokes this script from.
repo_root=$(git rev-parse --show-toplevel 2>/dev/null) || {
    echo "install.sh: ERROR: not inside a git repository." >&2
    exit 1
}

cd "$repo_root"

git config core.hooksPath .claude/pipeline/githooks

# Best-effort: ensure the hook is executable in the working tree. Git
# tracks the executable bit in the index (set via `git update-index
# --chmod=+x`), but a fresh clone on a POSIX filesystem will already have
# the bit. On Windows the bit is ignored by the filesystem but git still
# honours the index entry when invoking the hook.
if [ -f .claude/pipeline/githooks/pre-commit ] && [ ! -x .claude/pipeline/githooks/pre-commit ]; then
    chmod +x .claude/pipeline/githooks/pre-commit 2>/dev/null || \
        echo "install.sh: warning: could not chmod +x .claude/pipeline/githooks/pre-commit (likely Windows filesystem; git index bit still applies)." >&2
fi

if [ -f .claude/pipeline/githooks/commit-msg ] && [ ! -x .claude/pipeline/githooks/commit-msg ]; then
    chmod +x .claude/pipeline/githooks/commit-msg 2>/dev/null || \
        echo "install.sh: warning: could not chmod +x .claude/pipeline/githooks/commit-msg (likely Windows filesystem; git index bit still applies)." >&2
fi

echo "install.sh: ok — git hooks directory is now '.claude/pipeline/githooks/' (core.hooksPath set)."
echo "install.sh: active hook(s): .claude/pipeline/githooks/pre-commit, .claude/pipeline/githooks/commit-msg"
