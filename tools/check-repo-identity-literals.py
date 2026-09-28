#!/usr/bin/env python3
"""
tools/check-repo-identity-literals.py — CI CHECK 29 (REPO-IDENTITY-LITERALS)
arm (a): a branch-name literal in the ADR-0089 D1 subject set (PRD #1500
slice #1513).

ADR-0089 D1 requires every pipeline executable and every command in an agent
or skill prompt to name a branch role only through `tools/pipeline_config.py`
— never as a literal `develop`/`main` token. This check enforces that
mechanically over the subject set D1 defines.

Subject set (tracked files):
  - `tools/**`, `dashboard/**`, `.claude/hooks/**`, `.githooks/**`
  - `bootstrap.sh`
  - `.claude/agents/*.md`
  - `.claude/skills/*/SKILL.md`

Excluded from the subject set:
  - `*.md` under `tools/` and `dashboard/` (operator docs describing this
    repo's configured values)
  - `tools/gen_rules.py` (renders accepted decision text verbatim)
  - `tools/pipeline_config.py` (the resolver itself)

Within a subject file, a comment-only line (first non-blank character `#` or
`//`) is exempt in a non-`.md` file. `.md` prompt files have NO comment
exemption — a literal inside a fenced code block still counts.

Token shapes flagged (`b` is either configured branch-role default):
  `origin/b`, `origin b`, `refs/heads/b`, `branches/b`, `--base b`,
  a quoted `"b"`/`'b'`, a `:b` refspec destination, or
  `checkout|rev-parse|reset --hard [-B x] b`.

The two branch names are read from `tools/pipeline_config.py`'s own
`_DEFAULTS` mapping (never spelled as a literal in this file's source) — the
one place ADR-0089 D1 says the defaults live. This is deliberate self-check
hygiene (S3-self): this script's own source is itself inside the subject
set, and a bare quoted default would flag itself.

Exit codes:
  0 — zero violations in the subject set
  1 — one or more violations found (each printed as a FAIL line naming
      file:line, per the `^FAIL: .*CHECK 29.*<file>` shape)

Usage:
  python3 tools/check-repo-identity-literals.py [--root <path>]

CI integration: tools/ci-checks.sh CHECK 29 calls this script directly.
"""

import argparse
import importlib.util
import os
import re
import subprocess
import sys

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PIPELINE_CONFIG_PY = os.path.join(_THIS_DIR, "pipeline_config.py")

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


def _load_pipeline_config():
    # S1-c: located relative to THIS file, never via $REPO_ROOT or
    # `git rev-parse --show-toplevel` of the cwd repo.
    spec = importlib.util.spec_from_file_location(
        "pipeline_config", _PIPELINE_CONFIG_PY
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def configured_defaults():
    """The two branch-role default names, read from the resolver's own
    `_DEFAULTS` mapping — never spelled as a literal here (S3-self)."""
    return sorted(set(_load_pipeline_config()._DEFAULTS.values()))


def build_pattern(branch_names):
    b = "|".join(re.escape(x) for x in branch_names)
    return re.compile(
        rf"origin/({b})\b"
        rf"|\borigin ({b})\b"
        rf"|refs/heads/({b})\b"
        rf"|branches/({b})\b"
        rf"|--base ({b})\b"
        rf'|"({b})"'
        rf"|'({b})'"
        rf"|(?<![\w/]):({b})\b"
        rf"|\b(?:checkout|rev-parse|reset --hard)(?: -B \S+)? ({b})\b"
    )


def is_subject_file(relpath: str) -> bool:
    p = relpath.replace("\\", "/")
    if p == "bootstrap.sh":
        return True
    if (p.startswith("tools/") or p.startswith("dashboard/")
            or p.startswith(".claude/hooks/") or p.startswith(".githooks/")):
        return True
    if p.startswith(".claude/agents/") and p.endswith(".md"):
        return True
    if re.match(r"^\.claude/skills/[^/]+/SKILL\.md$", p):
        return True
    return False


def is_excluded(relpath: str) -> bool:
    p = relpath.replace("\\", "/")
    if p.endswith(".md") and (p.startswith("tools/") or p.startswith("dashboard/")):
        return True
    if p in ("tools/gen_rules.py", "tools/pipeline_config.py"):
        return True
    return False


def is_comment_only_line(line: str, is_md: bool) -> bool:
    if is_md:
        return False
    stripped = line.strip()
    return stripped.startswith("#") or stripped.startswith("//")


def find_violations_in_text(relpath: str, text: str, pattern):
    is_md = relpath.replace("\\", "/").endswith(".md")
    violations = []
    for lineno, line in enumerate(text.split("\n"), start=1):
        if is_comment_only_line(line, is_md):
            continue
        if pattern.search(line):
            violations.append((lineno, line))
    return violations


def list_tracked_subject_files(root: str):
    try:
        result = subprocess.run(
            ["git", "-C", root, "ls-files"],
            capture_output=True, text=True, check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    files = result.stdout.splitlines()
    return sorted(
        f for f in files if is_subject_file(f) and not is_excluded(f)
    )


def scan_repo(root: str, pattern):
    files = list_tracked_subject_files(root)
    if files is None:
        return None
    all_violations = []
    for relpath in files:
        abspath = os.path.join(root, relpath)
        try:
            with open(abspath, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            continue
        for lineno, line in find_violations_in_text(relpath, text, pattern):
            all_violations.append((relpath, lineno, line))
    return all_violations


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    args = parser.parse_args(argv)

    branch_names = configured_defaults()
    pattern = build_pattern(branch_names)

    violations = scan_repo(args.root, pattern)
    if violations is None:
        print("SKIP: CHECK 29 (REPO-IDENTITY-LITERALS) — git not available (soft-degrade)")
        return 0

    if not violations:
        print(
            "PASS: CHECK 29 (REPO-IDENTITY-LITERALS) — 0 branch-name literals "
            "in the ADR-0089 D1 subject set"
        )
        return 0

    for relpath, lineno, line in violations:
        print(
            f"FAIL: CHECK 29 (REPO-IDENTITY-LITERALS) — {relpath}:{lineno}: "
            f"{line.strip()[:120]}",
            file=sys.stderr,
        )
    print(
        f"FAIL: CHECK 29 (REPO-IDENTITY-LITERALS) — {len(violations)} branch-name "
        "literal(s) in the ADR-0089 D1 subject set",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
