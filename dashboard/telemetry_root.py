"""
dashboard/telemetry_root.py — shared helper for resolving the canonical
telemetry-log root via git-common-dir.

Problem: dashboard modules that need the log root historically computed it
inline as Path(__file__).resolve().parent.parent — i.e. the WORKTREE the
code runs from.  But .claude/logs/* is gitignored and exists ONLY in the
canonical root checkout.  When a dashboard command is run from a worktree
(the develop-dogfooding path), that computation reads an absent log and
falsely reports HOOKS DARK / no events.

Fix: resolve shared telemetry-log paths via `git rev-parse --git-common-dir`,
which always returns the canonical .git directory regardless of which worktree
the process runs in.  The parent of that .git dir is the canonical root.

Usage (e.g. in discovery.py):
    from telemetry_root import _telemetry_log_root
    fires_log = _telemetry_log_root() / ".claude" / "logs" / "hook-fires.jsonl"

Only the two shared log files (hook-fires.jsonl, workflow-events.jsonl) should
use this.  Code/doc paths (agents/, skills/, decisions/) must remain relative
to _TELEMETRY_CODE_ROOT so that a dashboard/ command run from a worktree
still reads its own worktree's source artifacts.

Slice: #1021  Root-cause: ADR-0058 D3 / git-common-dir worktree pattern.
"""

import subprocess
from pathlib import Path

# Code root: where this file lives (dashboard/) → parent = repo root of the
# running worktree.  Only used as a fallback when git is unavailable.
_TELEMETRY_CODE_ROOT: Path = Path(__file__).resolve().parent.parent

# Module-level cache — computed once per process.
_TELEMETRY_ROOT_CACHE: Path | None = None


def _telemetry_log_root() -> Path:
    """Return the canonical repo root for reading shared telemetry logs.

    Resolves ``git rev-parse --git-common-dir`` (cwd = _TELEMETRY_CODE_ROOT)
    and returns the parent of the resulting .git directory.  This is the
    canonical root regardless of which worktree the code runs from.

    On ANY failure (git not found, non-repo, non-zero exit), falls back to
    _TELEMETRY_CODE_ROOT so behaviour is identical to the old code when git is
    unavailable.

    Result is cached after the first call.
    """
    global _TELEMETRY_ROOT_CACHE
    if _TELEMETRY_ROOT_CACHE is not None:
        return _TELEMETRY_ROOT_CACHE

    resolved = _resolve_telemetry_log_root()
    _TELEMETRY_ROOT_CACHE = resolved
    return resolved


def _resolve_telemetry_log_root() -> Path:
    """Uncached resolution — separated for testing."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=str(_TELEMETRY_CODE_ROOT),
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return _TELEMETRY_CODE_ROOT
        git_common_dir = result.stdout.strip()
        if not git_common_dir:
            return _TELEMETRY_CODE_ROOT
        # git-common-dir may be:
        #   ".git"            (canonical root — relative path)
        #   "/abs/path/.git"  (worktree — absolute path to shared .git)
        git_common_path = Path(git_common_dir)
        if not git_common_path.is_absolute():
            git_common_path = (_TELEMETRY_CODE_ROOT / git_common_path).resolve()
        else:
            git_common_path = git_common_path.resolve()
        canonical_root = git_common_path.parent
        return canonical_root
    except Exception:
        return _TELEMETRY_CODE_ROOT
