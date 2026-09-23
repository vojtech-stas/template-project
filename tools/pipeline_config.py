#!/usr/bin/env python3
"""
tools/pipeline_config.py — the one parser for this repo's branch-role config
(ADR-0089 D1).

Two roles (ADR-0070 D1, read through ADR-0089 D1's naming clause):
  integration  — the autonomous PR-merge target.
  release      — advanced only by promotion.

Where the names live: a tracked `.claude/pipeline.conf` (`key=value`, `#`
comments) relative to the repository the CALLER operates in. An absent file
resolves to `develop`/`main` — those defaults live nowhere else. A present
but malformed file (unknown key, empty value, a name `git check-ref-format
--branch` would reject, or both roles equal) is refused with file:line.

Importable API (used by hooks and `tools/pipe/*` in-process — S1-a):
  integration_branch(start_dir=None) -> str
  release_branch(start_dir=None) -> str
  Both default `start_dir` to the process cwd, and start no subprocess on
  ANY path — conf present, conf absent, cwd outside a git repo, or the
  malformed-conf refusal. Repo-root discovery is a pure-Python upward walk
  for a `.git` entry (file or directory — this also covers linked
  worktrees), matching what `git rev-parse --show-toplevel` of the cwd
  would answer without ever invoking it.

CLI (used by bash callers and prompt commands):
  python3 tools/pipeline_config.py integration|release

Outside any git repo, the resolver answers the defaults with exit 0
(absent-file semantics: no repo means no config) — S1-d.

Callers locate this module relative to their OWN file (S1-c), never via
`$REPO_ROOT/tools/...` or `$(git rev-parse --show-toplevel)/tools/...` of
the cwd repo: Bash via `$(dirname "${BASH_SOURCE[0]}")`, `.githooks/`
scripts via `$(dirname "$0")`, Python via `Path(__file__).resolve()` (or
the `os.path` equivalent this module itself uses below).
"""
import os
import sys
from pathlib import Path

_DEFAULTS = {
    "integration_branch": "develop",
    "release_branch": "main",
}
_VALID_KEYS = frozenset(_DEFAULTS)
_CONF_RELATIVE_PATH = (".claude", "pipeline.conf")

# git-check-ref-format --branch's forbidden characters (anywhere in the
# name): space, tilde, caret, colon, question-mark, asterisk, open-bracket,
# backslash. Verified against real git 2.54.0 for every name in slice
# #1511's S1-a differential list, including the round-3 addendum.
_FORBIDDEN_CHARS = frozenset(" ~^:?*[\\")


class PipelineConfigError(Exception):
    """Raised when `.claude/pipeline.conf` is present but malformed."""


def _is_valid_ref_name(name: str) -> bool:
    """
    Pure-Python equivalent of `git check-ref-format --branch <name>`
    (one-level branch names allowed), spawning no subprocess.

    Encodes the standard git ref-name rules (git-check-ref-format(1)):
    no leading hyphen (branch-mode only, avoids CLI-option confusion),
    no leading/trailing/doubled slash, no `..`, no `@{`, no trailing dot,
    no control chars or the forbidden-char set above, and per path
    component: not empty, not starting with `.`, not ending `.lock`.
    """
    if not name:
        return False
    if name.startswith("-"):
        return False
    if name.startswith("/") or name.endswith("/"):
        return False
    if "//" in name:
        return False
    if ".." in name:
        return False
    if "@{" in name:
        return False
    if name.endswith("."):
        return False
    for ch in name:
        if ord(ch) < 0x20 or ord(ch) == 0x7F:
            return False
        if ch in _FORBIDDEN_CHARS:
            return False
    for component in name.split("/"):
        if component == "":
            return False
        if component.startswith("."):
            return False
        if component.endswith(".lock"):
            return False
    return True


def _find_repo_root(start: Path) -> Path | None:
    """
    Walk upward from `start` looking for a `.git` entry (a directory for a
    normal checkout, or a FILE for a linked worktree — both satisfied by a
    plain existence check, so worktrees resolve exactly like the primary
    checkout). Returns the directory containing it, or None if the walk
    reaches the filesystem root with no `.git` found (S1-d: outside any
    git repo). Never starts a subprocess (S1-a) — this is the pure-Python
    stand-in for `git rev-parse --show-toplevel` of `start`.
    """
    cur = start.resolve()
    while True:
        if (cur / ".git").exists():
            return cur
        parent = cur.parent
        if parent == cur:
            return None
        cur = parent


def _read_conf(repo_root: Path) -> dict:
    """
    Parse `.claude/pipeline.conf` under `repo_root`. Returns a dict with
    both role keys filled in (defaults for anything the file doesn't set).
    Raises PipelineConfigError, message-prefixed with `file:line`, on any
    of the four malformed shapes: unknown key, empty value, an invalid
    branch name, or both roles resolving to the same branch.
    """
    conf_path = repo_root.joinpath(*_CONF_RELATIVE_PATH)
    values = dict(_DEFAULTS)
    if not conf_path.exists():
        return values

    text = conf_path.read_text(encoding="utf-8")
    set_at_line: dict[str, int] = {}
    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise PipelineConfigError(
                f"{conf_path}:{lineno}: malformed pipeline.conf: "
                f"expected key=value, got {raw_line!r}"
            )
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if key not in _VALID_KEYS:
            raise PipelineConfigError(
                f"{conf_path}:{lineno}: malformed pipeline.conf: "
                f"unknown key {key!r}"
            )
        if not value:
            raise PipelineConfigError(
                f"{conf_path}:{lineno}: malformed pipeline.conf: "
                f"empty value for {key!r}"
            )
        if not _is_valid_ref_name(value):
            raise PipelineConfigError(
                f"{conf_path}:{lineno}: malformed pipeline.conf: "
                f"{value!r} is not a valid branch name for {key!r}"
            )
        values[key] = value
        set_at_line[key] = lineno

    if values["integration_branch"] == values["release_branch"]:
        lineno = max(set_at_line.values(), default=len(text.splitlines()))
        raise PipelineConfigError(
            f"{conf_path}:{lineno}: malformed pipeline.conf: "
            f"integration_branch and release_branch must not both be "
            f"{values['integration_branch']!r}"
        )
    return values


def _resolve(role: str, start_dir=None) -> str:
    start = Path(start_dir) if start_dir is not None else Path(os.getcwd())
    repo_root = _find_repo_root(start)
    if repo_root is None:
        # S1-d: outside any git repo, answer the defaults.
        return _DEFAULTS[role]
    return _read_conf(repo_root)[role]


def integration_branch(start_dir=None) -> str:
    """The autonomous PR-merge target (ADR-0070 D1), resolved for the
    repository `start_dir` (default: cwd) operates in."""
    return _resolve("integration_branch", start_dir)


def release_branch(start_dir=None) -> str:
    """The branch advanced only by promotion (ADR-0070 D1), resolved for
    the repository `start_dir` (default: cwd) operates in."""
    return _resolve("release_branch", start_dir)


def main(argv=None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    if len(argv) != 1 or argv[0] not in ("integration", "release"):
        print(
            "usage: pipeline_config.py integration|release",
            file=sys.stderr,
        )
        return 2
    role = "integration_branch" if argv[0] == "integration" else "release_branch"
    try:
        print(_resolve(role))
    except PipelineConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
