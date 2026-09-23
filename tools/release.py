#!/usr/bin/env python3
"""
tools/release.py — release-mode primitives for `/ship release <version>`
(PRD #1501 walking skeleton, slice #1506 / ADR-0090 D1-D4).

Four subcommands:

  freeze <V> --next <W> --features <n>,...|none [--classes <json>]
    Freezes a version's scope: every open non-residual `bug` plus the
    owner's named features (with their open sub-issues) go to <V>; every
    other open non-residual `feature` goes to <W>. Refuses, before any
    GitHub mutation, when the feature list is missing, any open
    non-residual issue carries neither or both class labels, or a listed
    number does not name a non-residual `feature` issue (ADR-0090 D1).

  lanes <V> [--evidence <sweep.json>] [--priority <file>]
    Prints the lane plan for <V>'s open `bug` issues as JSON: bugs that
    share a cited path (directly or through a chain) share one lane; a
    bug with no cited path runs `exclusive`. No split yet (slice 2 adds
    the per-lane bug-limit split) (ADR-0090 D3).

  packet --sha <sha> <n>...
    Prints the lane packet for the given bug numbers at `<sha>`: the sha,
    then each bug's `path:line` / `path:start-end` refs with a ±20-line
    excerpt read from that exact git blob, and its `Check:` line or
    `CHECK: MISSING`. Reads refs and checks from trusted authors only
    (issue body always counts; a comment counts only when its
    `author_association` is `OWNER`, `MEMBER` or `COLLABORATOR`), and
    resolves the check with the same resolver `verify` runs.
    `build_packet()` is the library entry point `tools/pipe/dispatch
    --lane` imports directly.

  verify <n>...
    Runs each bug's check (the issue's own `Check:` line, or else the
    `Check #<n>:` line of its most recently merged `lane` PR that closes
    it on a whole `Closes #<n>` line), with one layer of surrounding
    backticks stripped, and prints exactly one `PASS|FAIL|MISSING #<n>`
    line per issue; exits 0 iff every line is PASS. `--reopen` is
    deferred to slice 2 (SPIDR fallback, A10).

Stdlib only. No literal integration/release branch names (C1) — every
branch reference resolves through `tools/pipeline_config.py`.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))

TRUSTED_ASSOCIATIONS = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})

# A backtick-quoted `path:line` or `path:start-end` ref.
_REF_RE = re.compile(r"`([\w./\\-]+):(\d+)(?:-(\d+))?`")
# Check lines are case-sensitive, so the packet's own `CHECK: MISSING`
# line is never read as a command. `[ \t]*` (never `\s*`) keeps an empty
# check line from reading the next line as its command.
_CHECK_LINE_RE = re.compile(r"^Check:[ \t]*(.+)$", re.MULTILINE)
_PR_CHECK_LINE_RE_TMPL = r"^Check #{n}:[ \t]*(.+)$"
# A lane PR closes a bug only on a whole `Closes #<n>` line: the anchored
# form tools/pipe/pr-merge closes on merge. Prose `closes #<n>` never counts.
_CLOSES_LINE_RE_TMPL = r"^Closes #{n}\s*$"


def _gh():
    return shutil.which("gh") or "gh"


def _run_gh(args):
    return subprocess.run(
        [_gh()] + args, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


def _write_stdout_utf8(text):
    """Write `text` to stdout as UTF-8 bytes whatever the console or pipe
    encoding: a cp1252 stdout (Windows) cannot encode e.g. U+2192, and a
    packet carries whatever its issues and excerpts carry. A failed write
    raises OSError/ValueError for the caller to handle."""
    sys.stdout.flush()
    buf = getattr(sys.stdout, "buffer", None)
    if buf is None:
        sys.stdout.write(text)
        sys.stdout.flush()
        return
    buf.write(text.encode("utf-8", errors="replace"))
    buf.flush()


def _remote_owner_repo():
    """Resolve owner/repo from `git remote get-url origin` (mirrors the
    pattern already used by tools/pipe/dispatch and tools/pipe/pr-merge)."""
    try:
        url = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
        ).stdout.strip()
    except Exception:
        return None
    m = re.search(r"[:/]([^/]+)/([^/.]+?)(?:\.git)?$", url)
    if not m:
        return None
    return m.group(1), m.group(2)


def _gh_api_json(path, method="GET", fields=None, paginate=False):
    """`gh api <path>` (optionally `-X <method>`, `-f k=v`, `--paginate`),
    parsed as JSON. Returns the parsed value (list/dict), or None on any
    gh failure or unparseable output. `--paginate` concatenates each
    page's top-level JSON value back-to-back on stdout; this decodes the
    stream and flattens list-shaped pages into one list."""
    args = ["api", path, "-X", method]
    if paginate:
        args += ["--paginate"]
    for k, v in (fields or {}).items():
        args += ["-f", f"{k}={v}"]
    res = _run_gh(args)
    if res.returncode != 0:
        return None
    out = res.stdout.strip()
    if not out:
        return None
    if paginate:
        items = []
        decoder = json.JSONDecoder()
        idx = 0
        while idx < len(out):
            sub = out[idx:].lstrip()
            if not sub:
                break
            skip = len(out) - len(sub)
            try:
                obj, end = decoder.raw_decode(sub)
            except json.JSONDecodeError:
                break
            idx = skip + end
            if isinstance(obj, list):
                items.extend(obj)
            else:
                items.append(obj)
        return items
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------
# Issue reads shared across all four subcommands
# ---------------------------------------------------------------------------

def _fetch_issue_json(owner, repo, num):
    return _gh_api_json(f"repos/{owner}/{repo}/issues/{num}")


def _fetch_open_issues(owner, repo):
    """Every open issue, unfiltered (C4: no --label), with pull requests
    (which the issues API also returns) excluded. Query params ride `-f`
    fields, never an inline `?...&...` path string — `&` is a command
    separator to a Windows `.bat`-wrapped `gh`, so an inline query string
    silently truncates the request there."""
    data = _gh_api_json(
        f"repos/{owner}/{repo}/issues",
        fields={"state": "open", "per_page": "100"},
        paginate=True,
    )
    if data is None:
        return None
    return [i for i in data if "pull_request" not in i]


def _fetch_sub_issues(owner, repo, num):
    return _gh_api_json(f"repos/{owner}/{repo}/issues/{num}/sub_issues", paginate=True)


def _fetch_comments(owner, repo, num):
    return _gh_api_json(
        f"repos/{owner}/{repo}/issues/{num}/comments",
        fields={"per_page": "100"},
        paginate=True,
    )


def _issue_labels(issue):
    return {l.get("name") for l in (issue.get("labels") or []) if isinstance(l, dict)}


def _issue_class(issue):
    labels = _issue_labels(issue)
    has_bug = "bug" in labels
    has_feature = "feature" in labels
    if has_bug and not has_feature:
        return "bug"
    if has_feature and not has_bug:
        return "feature"
    return None  # neither, or both


def _is_residual(issue):
    labels = _issue_labels(issue)
    return "needs-human-check" in labels and "bug" not in labels


def _trusted_texts(owner, repo, issue):
    """Issue body (always trusted) plus every comment whose
    `author_association` is OWNER, MEMBER or COLLABORATOR (criterion 23)."""
    texts = [issue.get("body") or ""]
    comments = _fetch_comments(owner, repo, issue["number"]) or []
    for c in comments:
        if c.get("author_association") in TRUSTED_ASSOCIATIONS:
            texts.append(c.get("body") or "")
    return texts


def _extract_refs(text):
    """{path, ...} cited as backtick-quoted `path:line` refs in `text`."""
    if not text:
        return set()
    return {m.group(1) for m in _REF_RE.finditer(text)}


def _extract_path_line_refs(text):
    """[(path, start, end), ...] cited as backtick-quoted `path:line`
    (end == start) or `path:start-end` refs."""
    if not text:
        return []
    refs = []
    for m in _REF_RE.finditer(text):
        a = int(m.group(2))
        b = int(m.group(3)) if m.group(3) else a
        refs.append((m.group(1), min(a, b), max(a, b)))
    return refs


def _unwrap_check(raw):
    """Strip whitespace, then one layer of surrounding markdown backticks:
    `cmd` or ``cmd`` becomes cmd. A value whose delimiter run recurs inside
    (`a` and `b`) is two code spans, not one, so it stays verbatim."""
    value = raw.strip()
    m = re.fullmatch(r"(`+)(.*)\1", value, re.DOTALL)
    if m and m.group(1) not in m.group(2):
        value = m.group(2).strip()
    return value


def _first_check(pattern, text):
    for m in pattern.finditer(text or ""):
        value = _unwrap_check(m.group(1))
        if value:
            return value
    return None


def _find_check(texts):
    for t in texts:
        check = _first_check(_CHECK_LINE_RE, t)
        if check:
            return check
    return None


# ---------------------------------------------------------------------------
# freeze
# ---------------------------------------------------------------------------

def _fetch_milestones(owner, repo):
    return _gh_api_json(
        f"repos/{owner}/{repo}/milestones",
        fields={"state": "all", "per_page": "100"},
        paginate=True,
    )


def _ensure_milestone(owner, repo, title):
    """Resolve <title>'s milestone number, creating it if absent."""
    milestones = _fetch_milestones(owner, repo)
    if milestones is None:
        return None
    for m in milestones:
        if m.get("title") == title:
            return m.get("number")
    created = _gh_api_json(
        f"repos/{owner}/{repo}/milestones", method="POST", fields={"title": title}
    )
    if created is None:
        return None
    return created.get("number")


def _set_issue_milestone(owner, repo, num, milestone_num):
    _run_gh([
        "api", f"repos/{owner}/{repo}/issues/{num}",
        "-X", "PATCH", "-F", f"milestone={milestone_num}",
    ])


def _apply_class_label(owner, repo, num, cls):
    _run_gh(["issue", "edit", str(num), "--add-label", cls, "--repo", f"{owner}/{repo}"])


def _cmd_freeze(args):
    owner_repo = _remote_owner_repo()
    if not owner_repo:
        print("release.py freeze: refused — could not resolve owner/repo from origin", file=sys.stderr)
        return 1
    owner, repo = owner_repo

    if args.features is None:
        print("release.py freeze: refused — missing --features <n>,...|none", file=sys.stderr)
        return 1

    classes_map = {}
    if args.classes:
        try:
            raw = json.loads(args.classes)
            classes_map = {str(k): v for k, v in raw.items()}
        except (json.JSONDecodeError, AttributeError, TypeError):
            print("release.py freeze: refused — --classes is not valid JSON", file=sys.stderr)
            return 1

    issues = _fetch_open_issues(owner, repo)
    if issues is None:
        print("release.py freeze: refused — could not list open issues via gh", file=sys.stderr)
        return 1
    by_number = {str(i["number"]): i for i in issues}

    # Apply --classes to unclassified issues only (never overwrite an
    # existing class); record which ones need a live label call.
    to_label = []
    effective_class = {}
    for num_str, issue in by_number.items():
        cls = _issue_class(issue)
        if cls is None:
            mapped = classes_map.get(num_str)
            if mapped in ("bug", "feature"):
                cls = mapped
                to_label.append((num_str, cls))
        effective_class[num_str] = cls

    feature_numbers = []
    if args.features.strip().lower() != "none":
        feature_numbers = [n.strip() for n in args.features.split(",") if n.strip()]

    # --- Refusal checks (before ANY GitHub mutation) ---
    unclassified = sorted(
        (n for n, i in by_number.items() if not _is_residual(i) and effective_class.get(n) is None),
        key=int,
    )
    if unclassified:
        print(
            "release.py freeze: refused — unclassified open issue(s): "
            + ", ".join(f"#{n}" for n in unclassified),
            file=sys.stderr,
        )
        return 1

    bad_features = []
    for n in feature_numbers:
        issue = by_number.get(n)
        if issue is None or _is_residual(issue) or effective_class.get(n) != "feature":
            bad_features.append(n)
    if bad_features:
        print(
            "release.py freeze: refused — listed number(s) do not name a "
            "non-residual feature issue: " + ", ".join(f"#{n}" for n in bad_features),
            file=sys.stderr,
        )
        return 1

    # --- Past every refusal: apply deferred class labels. ---
    for num_str, cls in to_label:
        _apply_class_label(owner, repo, num_str, cls)

    # --- Resolve or create <V> and <W>. ---
    v_num = _ensure_milestone(owner, repo, args.version)
    w_num = _ensure_milestone(owner, repo, args.next_version)
    if v_num is None or w_num is None:
        print("release.py freeze: refused — could not resolve/create a milestone", file=sys.stderr)
        return 1

    # --- Admitted features: listed + their open sub-issues. ---
    admitted = set(feature_numbers)
    for n in feature_numbers:
        for sub in (_fetch_sub_issues(owner, repo, n) or []):
            if sub.get("state") == "open":
                sub_num = str(sub["number"])
                admitted.add(sub_num)
                by_number.setdefault(sub_num, sub)

    moved = 0
    for num_str, issue in sorted(by_number.items(), key=lambda kv: int(kv[0])):
        if _is_residual(issue):
            continue
        cls = effective_class.get(num_str, _issue_class(issue))
        if cls == "bug" or num_str in admitted:
            target = v_num
        elif cls == "feature":
            target = w_num
        else:
            continue  # unclassified admitted-sub-issue edge case: skip, never silently guess

        cur_ms = (issue.get("milestone") or {}).get("number")
        if cur_ms == target:
            continue
        if cur_ms is not None and cur_ms not in (v_num, w_num):
            continue  # a placement in any other milestone stands (criterion 11)
        _set_issue_milestone(owner, repo, num_str, target)
        moved += 1

    print(f"release.py freeze: {args.version}=#{v_num} {args.next_version}=#{w_num} moved={moved}")
    return 0


# ---------------------------------------------------------------------------
# lanes
# ---------------------------------------------------------------------------

def _slugify(path):
    base = os.path.basename(path)
    base = re.sub(r"[^A-Za-z0-9]+", "-", base).strip("-").lower()
    return base or "misc"


def _load_priority(path):
    """A priority file: a JSON list of issue numbers (highest priority
    first), or one issue number per line. Returns {number_str: rank}."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return {str(n): idx for idx, n in enumerate(data)}
    except json.JSONDecodeError:
        pass
    ranks = {}
    idx = 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        ranks[line] = idx
        idx += 1
    return ranks


def _cmd_lanes(args):
    owner_repo = _remote_owner_repo()
    if not owner_repo:
        print("release.py lanes: refused — could not resolve owner/repo from origin", file=sys.stderr)
        return 1
    owner, repo = owner_repo

    evidence = {}
    if args.evidence:
        try:
            evidence = json.loads(Path(args.evidence).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"release.py lanes: refused — could not read --evidence file: {exc}", file=sys.stderr)
            return 1

    issues = _fetch_open_issues(owner, repo)
    if issues is None:
        print("release.py lanes: refused — could not list open issues via gh", file=sys.stderr)
        return 1

    bugs = [
        i for i in issues
        if "bug" in _issue_labels(i)
        and not _is_residual(i)
        and (i.get("milestone") or {}).get("title") == args.version
    ]

    issue_paths = {}
    for issue in bugs:
        num = str(issue["number"])
        if num in evidence:
            paths = {str(p) for p in evidence[num]}
        else:
            paths = set()
            for t in _trusted_texts(owner, repo, issue):
                paths |= _extract_refs(t)
        issue_paths[num] = paths

    # Union-find: bugs sharing a path (directly or through a chain) share a
    # group; a bug with no cited path forms its own exclusive singleton.
    parent = {num: num for num in issue_paths}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    path_owner = {}
    for num, paths in issue_paths.items():
        for p in paths:
            if p in path_owner:
                union(num, path_owner[p])
            else:
                path_owner[p] = num

    groups = {}
    for num in issue_paths:
        groups.setdefault(find(num), []).append(num)

    priority_rank = _load_priority(args.priority) if args.priority else {}

    lanes = []
    for members in groups.values():
        members_sorted = sorted(members, key=int)
        paths = sorted(set().union(*(issue_paths[m] for m in members_sorted)))
        exclusive = len(paths) == 0
        lowest = members_sorted[0]
        slug = _slugify(paths[0]) if paths else "misc"
        lane_id = f"fix/{lowest}-lane-{slug}"
        lanes.append({
            "lane": lane_id,
            "group": None if exclusive else lane_id,
            "exclusive": exclusive,
            "issues": members_sorted,
            "paths": paths,
        })

    def rank(lane):
        ranks = [priority_rank.get(m) for m in lane["issues"]]
        ranks = [r for r in ranks if r is not None]
        if ranks:
            return (0, min(ranks))
        return (1, int(lane["issues"][0]))

    lanes.sort(key=rank)
    print(json.dumps(lanes, indent=2))
    return 0


# ---------------------------------------------------------------------------
# packet (library function + thin CLI wrapper)
# ---------------------------------------------------------------------------

def _read_blob_excerpt(sha, path, line, window=20, repo_root=None, end_line=None):
    """`git show <sha>:<path>`, then the excerpt from `window` lines before
    `line` to `window` lines after `end_line` (default `line`; 1-based).
    Reads the exact git blob at `sha` — never the working tree —
    so the excerpt reflects that sha even if the tree has since moved on.
    Decoded as UTF-8, never the host locale, so the excerpt is the text at
    that sha (criterion 21) and the Edit tool's before-text match holds."""
    res = subprocess.run(
        ["git", "show", f"{sha}:{path}"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=repo_root,
    )
    if res.returncode != 0:
        return None
    lines = res.stdout.splitlines()
    last = line if end_line is None else max(line, end_line)
    start = max(0, line - 1 - window)
    end = min(len(lines), last - 1 + window + 1)
    return "\n".join(lines[start:end])


def build_packet(owner, repo, issue_numbers, sha, repo_root=None):
    """Library entry point `tools/pipe/dispatch --lane` imports directly.
    Reads refs and checks from trusted authors only (issue body always
    counts; comments only when trusted — criterion 23), excerpts ±20 lines
    from the exact `sha` blob, and resolves each check through
    `_resolve_check`, the same resolver `verify` runs, falling back to
    `CHECK: MISSING`."""
    parts = [f"SHA: {sha}"]
    for num in issue_numbers:
        issue = _fetch_issue_json(owner, repo, num)
        texts = _trusted_texts(owner, repo, issue) if issue else []
        refs = []
        for t in texts:
            refs.extend(_extract_path_line_refs(t))
        parts.append(f"\n#### Bug #{num}")
        if not refs:
            parts.append("(no cited path:line refs)")
        for path, start, end in refs:
            excerpt = _read_blob_excerpt(sha, path, start, repo_root=repo_root, end_line=end)
            parts.append(f"{path}:{start}" if end == start else f"{path}:{start}-{end}")
            parts.append("```")
            parts.append(excerpt if excerpt is not None else "(excerpt unavailable)")
            parts.append("```")
        check = _resolve_check(owner, repo, num, issue=issue, texts=texts)
        parts.append(f"Check: {check}" if check else "CHECK: MISSING")
    return "\n".join(parts) + "\n"


def _cmd_packet(args):
    owner_repo = _remote_owner_repo()
    if not owner_repo:
        print("release.py packet: refused — could not resolve owner/repo from origin", file=sys.stderr)
        return 1
    owner, repo = owner_repo
    _write_stdout_utf8(build_packet(owner, repo, args.issues, args.sha))
    return 0


# ---------------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------------

def _find_lane_pr_for_issue(owner, repo, num):
    """The MOST RECENTLY MERGED (`mergedAt`) `lane`-labeled PR with a whole
    `Closes #<num>` line in its body. A bug closed, reopened and closed
    again by a second lane PR therefore reads the newer PR's check, never
    the first search hit. Found by search, never the (deferred, criterion
    27) closing comment, which keeps the SPIDR fallback available
    (constraint 6). Labels are filtered here, never via `--label` (C4).
    Goes through `_run_gh`, so the PR bodies decode as UTF-8."""
    res = _run_gh(
        ["pr", "list", "--repo", f"{owner}/{repo}", "--state", "merged",
         "--search", f"Closes #{num} in:body",
         "--json", "number,body,mergedAt,labels", "--limit", "100"],
    )
    if res.returncode != 0:
        return None
    try:
        data = json.loads(res.stdout)
    except json.JSONDecodeError:
        return None
    closes = re.compile(_CLOSES_LINE_RE_TMPL.format(n=num), re.MULTILINE)
    lane_prs = [
        pr for pr in (data if isinstance(data, list) else [])
        if isinstance(pr, dict)
        and "lane" in _issue_labels(pr)
        and closes.search(pr.get("body") or "")
    ]
    if not lane_prs:
        return None
    return max(lane_prs, key=lambda pr: pr.get("mergedAt") or "")


def _resolve_check(owner, repo, num, issue=None, texts=None):
    """The ONE check resolver `verify` and the packet share: the issue's
    own trusted `Check:` line when one exists; otherwise the `Check #<n>:`
    line of its most recently merged lane PR (criterion 33). One layer of
    surrounding backticks is stripped. `texts` (the issue's trusted texts)
    skips the re-fetch when the caller already holds them."""
    if texts is None:
        if issue is None:
            issue = _fetch_issue_json(owner, repo, num)
        texts = _trusted_texts(owner, repo, issue) if issue else []
    check = _find_check(texts)
    if check:
        return check
    pr = _find_lane_pr_for_issue(owner, repo, num)
    if pr:
        pat = re.compile(_PR_CHECK_LINE_RE_TMPL.format(n=num), re.MULTILINE)
        return _first_check(pat, pr.get("body") or "")
    return None


def _cmd_verify(args):
    owner_repo = _remote_owner_repo()
    if not owner_repo:
        print("release.py verify: refused — could not resolve owner/repo from origin", file=sys.stderr)
        return 1
    owner, repo = owner_repo

    all_pass = True
    for num in args.issues:
        check = _resolve_check(owner, repo, num)
        if check is None:
            print(f"MISSING #{num}")
            all_pass = False
            continue
        res = subprocess.run(
            check, shell=True, capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if res.returncode == 0:
            print(f"PASS #{num}")
        else:
            print(f"FAIL #{num}")
            all_pass = False
    return 0 if all_pass else 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None):
    argv = list(argv if argv is not None else sys.argv[1:])
    parser = argparse.ArgumentParser(prog="release.py")
    sub = parser.add_subparsers(dest="command", required=True)

    p_freeze = sub.add_parser("freeze")
    p_freeze.add_argument("version")
    p_freeze.add_argument("--next", dest="next_version", required=True)
    p_freeze.add_argument("--features", default=None)
    p_freeze.add_argument("--classes", default=None)
    p_freeze.set_defaults(func=_cmd_freeze)

    p_lanes = sub.add_parser("lanes")
    p_lanes.add_argument("version")
    p_lanes.add_argument("--evidence", default=None)
    p_lanes.add_argument("--priority", default=None)
    p_lanes.set_defaults(func=_cmd_lanes)

    p_packet = sub.add_parser("packet")
    p_packet.add_argument("issues", nargs="+")
    p_packet.add_argument("--sha", required=True)
    p_packet.set_defaults(func=_cmd_packet)

    p_verify = sub.add_parser("verify")
    p_verify.add_argument("issues", nargs="+")
    p_verify.set_defaults(func=_cmd_verify)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
