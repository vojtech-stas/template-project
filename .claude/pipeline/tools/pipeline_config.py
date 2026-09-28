#!/usr/bin/env python3
"""
.claude/pipeline/tools/pipeline_config.py — the one parser for this repo's
branch-role config AND per-repo package identity (ADR-0089 D1, extended by
ADR-0092 D1/D5).

Two roles (ADR-0070 D1, read through ADR-0089 D1's naming clause):
  integration  — the autonomous PR-merge target.
  release      — advanced only by promotion.

One optional identity key (ADR-0092 D1/D5):
  package_source — the git remote/path a host installed its package from.
                   Absent in the home repository; always present in a host
                   (`install` writes it). Its presence is what `mode()`
                   reads to tell home from host.

Where the names live: a tracked `.claude/pipeline.conf` (`key=value`, `#`
comments) relative to the repository the CALLER operates in — this file
stays OUTSIDE the package (ADR-0092 D1), at the same repo-relative path in
the home repository and in every host. An absent file resolves to
`develop`/`main` for the two branch roles, and no `package_source` (home
mode) — those defaults live nowhere else. A present but malformed file
(unknown key, empty value, a branch name `git check-ref-format --branch`
would reject, or both roles equal) is refused with file:line.

Two roots (ADR-0092 D1 "Roots"), both importable and on the CLI:
  repo_root(start_dir=None)    -> the repository root
                                   (`git rev-parse --show-toplevel` of
                                   `start_dir`, found without a subprocess).
  package_root(start_dir=None) -> `<repo_root>/.claude/pipeline`.
A file locates either root ONLY through this module — never from its own
`__file__`/`BASH_SOURCE` location (ADR-0092 D1 self-location arm). A file
MAY still reach its own package siblings from its own location (a hook's
`../tools/`, a module's own `sys.path` insertion) — that is a different
question from "where is the repository/package root".

Importable API (used by hooks and `tools/pipe/*` in-process — S1-a):
  integration_branch(start_dir=None) -> str
  release_branch(start_dir=None) -> str
  package_source(start_dir=None) -> str | None
  mode(start_dir=None) -> "home" | "host"
  repo_root(start_dir=None) -> pathlib.Path
  package_root(start_dir=None) -> pathlib.Path
  Every query defaults `start_dir` to the process cwd, and starts no
  subprocess on ANY path — conf present, conf absent, cwd outside a git
  repo, or the malformed-conf refusal. Repo-root discovery is a pure-Python
  upward walk for a `.git` entry (file or directory — this also covers
  linked worktrees), matching what `git rev-parse --show-toplevel` of the
  cwd would answer without ever invoking it.

CLI (used by bash callers and prompt commands):
  python3 .claude/pipeline/tools/pipeline_config.py integration|release|mode|repo-root|package-root

Outside any git repo, the resolver answers the defaults with exit 0
(absent-file semantics: no repo means no config) — S1-d. `mode`, `repo-root`
and `package-root` outside a git repo answer "home", the cwd, and
`<cwd>/.claude/pipeline` respectively (repo-root's own absent-repo fallback).

Callers locate this module relative to their OWN file (S1-c), never via
`$REPO_ROOT/tools/...` or `$(git rev-parse --show-toplevel)/tools/...` of
the cwd repo: Bash via `$(dirname "${BASH_SOURCE[0]}")`, `.githooks/`
scripts via `$(dirname "$0")`, Python via `Path(__file__).resolve()` (or
the `os.path` equivalent this module itself uses below). This module is
excluded from the self-location arm's own scan (ADR-0092 D1) because it IS
the roots resolver every other file must use instead of its own location.
"""
import os
import sys
from pathlib import Path

_DEFAULTS = {
    "integration_branch": "develop",
    "release_branch": "main",
}
# package_source has no default — its absence IS home mode (ADR-0092 D5).
_OPTIONAL_KEYS = frozenset({"package_source"})
_VALID_KEYS = frozenset(_DEFAULTS) | _OPTIONAL_KEYS
_CONF_RELATIVE_PATH = (".claude", "pipeline.conf")
_PACKAGE_RELATIVE_PATH = (".claude", "pipeline")

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
    both role keys filled in (defaults for anything the file doesn't set)
    and `package_source` set to None unless the file names one. Raises
    PipelineConfigError, message-prefixed with `file:line`, on any of the
    four malformed shapes: unknown key, empty value, an invalid branch
    name (branch-role keys only), or both roles resolving to the same
    branch.
    """
    conf_path = repo_root.joinpath(*_CONF_RELATIVE_PATH)
    values = dict(_DEFAULTS)
    values["package_source"] = None
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
        if key in _DEFAULTS and not _is_valid_ref_name(value):
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


def repo_root(start_dir=None) -> Path:
    """The repository root of `start_dir` (default: cwd), or `start_dir`
    itself (resolved) when it is outside any git repo (ADR-0092 D1)."""
    start = Path(start_dir) if start_dir is not None else Path(os.getcwd())
    found = _find_repo_root(start)
    return found if found is not None else start.resolve()


def package_root(start_dir=None) -> Path:
    """`<repo_root>/.claude/pipeline` (ADR-0092 D1)."""
    return repo_root(start_dir).joinpath(*_PACKAGE_RELATIVE_PATH)


def package_source(start_dir=None) -> str | None:
    """The URL/path a host installed its package from, or None in the
    home repository (ADR-0092 D1/D5)."""
    start = Path(start_dir) if start_dir is not None else Path(os.getcwd())
    root = _find_repo_root(start)
    if root is None:
        return None
    return _read_conf(root)["package_source"]


def mode(start_dir=None) -> str:
    """"host" iff `.claude/pipeline.conf` names a `package_source`;
    otherwise "home" (ADR-0092 D5). The home repository never has a
    `package_source`, and `install` always writes one."""
    return "host" if package_source(start_dir) else "home"


def main(argv=None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    _SIMPLE = {
        "integration": lambda: integration_branch(),
        "release": lambda: release_branch(),
        "mode": lambda: mode(),
        "repo-root": lambda: str(repo_root()),
        "package-root": lambda: str(package_root()),
    }
    if len(argv) != 1 or argv[0] not in _SIMPLE:
        print(
            "usage: pipeline_config.py integration|release|mode|repo-root|package-root",
            file=sys.stderr,
        )
        return 2
    try:
        print(_SIMPLE[argv[0]]())
    except PipelineConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
