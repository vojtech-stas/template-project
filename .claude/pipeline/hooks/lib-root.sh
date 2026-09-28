#!/bin/bash
# lib-root.sh — resolve the MAIN repo root via git-common-dir (the proven
# pattern from log-event.sh).  Source this file; it sets MAIN_ROOT and
# LOG_DIR, then mkdir -p's the logs directory.
#
# Works in both the main worktree and any linked worktree:
#   git rev-parse --path-format=absolute --git-common-dir
#   → <main-repo>/.git   in all cases
#   → dirname → <main-repo>
#
# SOFT-DEGRADE: if git resolution fails, fall back to $CLAUDE_PROJECT_DIR.
# Never exits non-zero.  Caller gets a writable LOG_DIR or the fallback.

REPO_ROOT="${CLAUDE_PROJECT_DIR:-.}"
_COMMON=$(git -C "$REPO_ROOT" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)
if [ -n "$_COMMON" ]; then
  MAIN_ROOT=$(dirname "$_COMMON")
else
  MAIN_ROOT="$REPO_ROOT"
fi
[ -d "$MAIN_ROOT" ] || MAIN_ROOT="$REPO_ROOT"

LOG_DIR="$MAIN_ROOT/.claude/logs"
mkdir -p "$LOG_DIR" 2>/dev/null || true
