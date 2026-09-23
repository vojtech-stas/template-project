"""
dashboard/collector.py — GitHub artifact trail collector (ADR-0053 D1/D4).

Stdlib-only; shells to the `gh` CLI directly via `subprocess.run`.
Reconstructs a complete PRD run trail from GitHub artifacts:
  PRD issue → sub-issues (slices) → PRs (via closingIssuesReferences) →
  PR comments (reviewer verdicts) → merge events + timestamps.

Cache: .claude/logs/trail-cache/prd-<n>.json
  - Closed PRD (closedAt non-null) → cache forever (immutable run)
  - Open PRD → cache for TTL_OPEN_S seconds

Retry: 0s / 2s / 8s backoff on 401/5xx/timeout.
  Sustained failure → collector_status: "auth_dead"

CLI: python dashboard/collector.py --prd N [--compare]
     python dashboard/collector.py --rollup [--last N] [--compare]
"""

import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_THIS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _THIS_DIR.parent
_CACHE_DIR = _REPO_ROOT / ".claude" / "logs" / "trail-cache"
_PIPELINE_CONFIG_PY = _REPO_ROOT / "tools" / "pipeline_config.py"


def _load_pipeline_config():
    """S1-c: located relative to THIS file's own path (_REPO_ROOT above is
    derived from Path(__file__)), never via cwd or `git rev-parse
    --show-toplevel` of the caller's cwd repo. Called fresh at CALL time
    (S2-ct) by every site below — never resolved at module-import time, so
    a malformed conf can never break `import collector` itself."""
    spec = importlib.util.spec_from_file_location(
        "pipeline_config", _PIPELINE_CONFIG_PY
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
TTL_OPEN_S = 60          # seconds before an open-PRD cache entry is stale
RETRY_DELAYS = [0, 2, 8]  # seconds between retry attempts

# ---------------------------------------------------------------------------
# Runtime repo-slug derivation
# ---------------------------------------------------------------------------
_repo_slug_cache: str | None = None


def _repo_slug() -> str | None:
    """Return the GitHub repo slug (owner/name) for this clone.

    Precedence:
    1. ``gh repo view --json nameWithOwner`` — most authoritative.
    2. Parse ``git remote get-url origin`` for SSH/HTTPS github.com remotes.
    3. ``DASH_REPO_SLUG`` environment variable — explicit override.
    4. Return None — caller must treat this as a collector error state.

    Result is module-level cached after the first successful call so that the
    ~0.3 s ``gh repo view`` overhead only incurs on the first background cycle.
    Assumption: single github.com ``origin`` remote (documented non-goal for
    multi-remote/GHE setups; see PRD #753 §3).
    """
    global _repo_slug_cache
    if _repo_slug_cache is not None:
        return _repo_slug_cache

    # 1. gh repo view
    try:
        result = subprocess.run(
            ["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            cwd=str(_REPO_ROOT),
            stdin=subprocess.DEVNULL,
        )
        if result.returncode == 0:
            slug = result.stdout.strip()
            if "/" in slug:
                _repo_slug_cache = slug
                return _repo_slug_cache
    except Exception:
        pass

    # 2. git remote get-url origin — parse SSH and HTTPS github.com forms
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            cwd=str(_REPO_ROOT),
            stdin=subprocess.DEVNULL,
        )
        if result.returncode == 0:
            url = result.stdout.strip()
            # SSH: git@github.com:owner/name.git
            m = re.match(r"git@github\.com:([^/]+/[^/]+?)(?:\.git)?$", url)
            if m:
                _repo_slug_cache = m.group(1)
                return _repo_slug_cache
            # HTTPS: https://github.com/owner/name[.git]
            m = re.match(r"https://github\.com/([^/]+/[^/]+?)(?:\.git)?$", url)
            if m:
                _repo_slug_cache = m.group(1)
                return _repo_slug_cache
    except Exception:
        pass

    # 3. DASH_REPO_SLUG env override
    env_slug = os.environ.get("DASH_REPO_SLUG", "").strip()
    if env_slug and "/" in env_slug:
        _repo_slug_cache = env_slug
        return _repo_slug_cache

    # 4. Total failure — return None; collector reports its error state
    return None


def _gql_query_for_slug(slug: str | None) -> str | None:
    """Return the GQL query string with the runtime-derived repo owner/name.

    Returns None if the slug cannot be derived (triggers collector error state).
    """
    if slug is None:
        return None
    owner, _, name = slug.partition("/")
    if not owner or not name:
        return None
    return _GQL_QUERY_TEMPLATE.replace("__OWNER__", owner).replace("__NAME__", name)


# GraphQL query — one batched call per PRD (design-verified ~0.8s/PRD).
# Owner and name are substituted at call time from _repo_slug().
_GQL_QUERY_TEMPLATE = """\
query($n:Int!){
  repository(owner:"__OWNER__",name:"__NAME__"){
    issue(number:$n){
      title
      createdAt
      closedAt
      labels(first:10){nodes{name}}
      comments(first:50){nodes{body createdAt author{login}}}
      subIssues(first:30){
        nodes{
          number
          title
          createdAt
          closedAt
          labels(first:10){nodes{name}}
          assignees(first:5){nodes{login}}
          comments(first:30){nodes{body createdAt}}
          timelineItems(itemTypes:[CLOSED_EVENT,LABELED_EVENT],first:20){
            nodes{
              __typename
              ... on ClosedEvent{
                createdAt
                closer{
                  __typename
                  ... on PullRequest{number}
                  ... on Commit{abbreviatedOid}
                }
              }
              ... on LabeledEvent{label{name} createdAt}
            }
          }
        }
      }
    }
  }
}
"""

# ---------------------------------------------------------------------------
# CRITIC trailer parse helpers
# ---------------------------------------------------------------------------
_VERDICT_RE = re.compile(
    r'```[^\n]*\n(.*?)```',
    re.DOTALL,
)
_VERDICT_FIELD_RE = re.compile(r'^VERDICT:\s*(APPROVE|BLOCK)\b', re.MULTILINE | re.IGNORECASE)
_ROUND_FIELD_RE = re.compile(r'^ROUND:\s*(\d+)\b', re.MULTILINE)
_CRITIC_FIELD_RE = re.compile(r'^CRITIC:\s*(\S+)', re.MULTILINE)


def parse_critic_field(comment_body: str) -> str | None:
    """Extract the CRITIC: <name> value from a fenced trailer block.

    Searches all fenced blocks in the comment body; returns the first
    CRITIC: value found, or None if absent (pre-v2 / unattributed verdict).

    Tolerant of truncation: if a fenced block is truncated (no closing ```),
    the regex still searches the partial block text.

    Returns:
        str  — the agent name, e.g. "reviewer", "prd-critic"
        None — no CRITIC field found (caller treats as "unattributed")
    """
    # Try fenced blocks first (canonical location per ADR-0059 D1)
    for block_m in _VERDICT_RE.finditer(comment_body):
        block = block_m.group(1)
        cm = _CRITIC_FIELD_RE.search(block)
        if cm:
            return cm.group(1).strip()
    # Fallback: partial/truncated block — search from last ``` onward
    last_fence = comment_body.rfind("```")
    if last_fence != -1:
        tail = comment_body[last_fence + 3:]
        cm = _CRITIC_FIELD_RE.search(tail)
        if cm:
            return cm.group(1).strip()
    # Bare CRITIC: line outside any fence (degraded format)
    cm = _CRITIC_FIELD_RE.search(comment_body)
    if cm:
        return cm.group(1).strip()
    return None


def _parse_verdict_comment(body: str) -> dict | None:
    """Extract VERDICT/ROUND/CRITIC from a reviewer comment body.

    Returns {"verdict": "APPROVE"|"BLOCK", "round": int|None,
             "round_inferred": bool, "critic": str|None}
    or None if no VERDICT field found.

    Tolerant: accepts VERDICT in a fenced block OR bare in the comment body.
    Missing ROUND → None (caller infers from position).
    Missing CRITIC → None (pre-v2 / unattributed; caller buckets as "unattributed").
    """
    critic = parse_critic_field(body)
    # Try fenced blocks first (canonical trailer format)
    for block_m in _VERDICT_RE.finditer(body):
        block = block_m.group(1)
        vm = _VERDICT_FIELD_RE.search(block)
        if vm:
            verdict = vm.group(1).upper()
            rm = _ROUND_FIELD_RE.search(block)
            return {
                "verdict": verdict,
                "round": int(rm.group(1)) if rm else None,
                "round_inferred": rm is None,
                "critic": critic,
            }
    # Fallback: bare VERDICT in body (e.g. older format)
    vm = _VERDICT_FIELD_RE.search(body)
    if vm:
        verdict = vm.group(1).upper()
        rm = _ROUND_FIELD_RE.search(body)
        return {
            "verdict": verdict,
            "round": int(rm.group(1)) if rm else None,
            "round_inferred": rm is None,
            "critic": critic,
        }
    return None


def _infer_rounds(verdicts: list[dict]) -> list[dict]:
    """Fill in inferred round numbers for verdicts missing explicit ROUND."""
    result = []
    inferred_counter = 1
    for v in verdicts:
        v = dict(v)
        if v.get("round") is None:
            v["round"] = inferred_counter
            v["round_inferred"] = True
        else:
            inferred_counter = v["round"]
        inferred_counter += 1
        result.append(v)
    return result


# ---------------------------------------------------------------------------
# gh CLI runner with retry
# ---------------------------------------------------------------------------
def _run_gh(args: list[str], timeout: int = 30) -> tuple[str | None, str]:
    """Run a gh command; return (stdout, error_class).

    error_class: "" (success) | "transient" | "auth_dead" | "timeout"

    Retry schedule: RETRY_DELAYS (0s, 2s, 8s).
    auth_dead: all retries exhausted with non-zero exit.
    """
    last_error = "unknown"
    for i, delay in enumerate(RETRY_DELAYS):
        if delay > 0:
            time.sleep(delay)
        try:
            result = subprocess.run(
                ["gh"] + args,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                cwd=str(_REPO_ROOT),
                stdin=subprocess.DEVNULL,
            )
            if result.returncode == 0:
                return result.stdout, ("transient" if i > 0 else "")
            # Non-zero exit — check stderr for auth issues
            stderr = result.stderr.lower()
            if "401" in stderr or "authentication" in stderr or "not logged in" in stderr:
                last_error = "auth_dead"
            else:
                last_error = "transient"
        except subprocess.TimeoutExpired:
            last_error = "timeout"
        except FileNotFoundError:
            return None, "auth_dead"  # gh not installed
        except Exception as e:
            last_error = f"transient:{e}"
    return None, last_error


def _gh_graphql(query: str, variables: dict, timeout: int = 30) -> tuple[dict | None, str]:
    """Run a GraphQL query via gh api graphql. Returns (data_dict, error_class)."""
    args = ["api", "graphql", "-f", f"query={query}"]
    for k, v in variables.items():
        args += ["-F", f"{k}={v}"]
    stdout, err = _run_gh(args, timeout=timeout)
    if stdout is None:
        return None, err
    try:
        obj = json.loads(stdout)
    except Exception:
        return None, "transient:bad_json"
    if "errors" in obj:
        return None, f"transient:graphql_errors:{obj['errors']}"
    return obj.get("data"), ""


def _gh_pr_view(pr_number: int, timeout: int = 20) -> tuple[dict | None, str]:
    """Fetch PR details via gh pr view. Returns (pr_dict, error_class).

    Fetches statusCheckRollup for merged_without_ci detection (slice #767).
    Older PRs that predate the gh API field may return an empty list — that is
    handled gracefully in _build_pr_trail (bootstrap-mode grandfathering).
    """
    args = [
        "pr", "view", str(pr_number),
        "--json",
        "number,createdAt,mergedAt,headRefName,body,comments,"
        "closingIssuesReferences,statusCheckRollup",
    ]
    stdout, err = _run_gh(args, timeout=timeout)
    if stdout is None:
        return None, err
    try:
        return json.loads(stdout), ""
    except Exception:
        return None, "transient:bad_json"


# ---------------------------------------------------------------------------
# Two-tier (develop-base) PR discovery helpers
# ---------------------------------------------------------------------------
_CLOSES_RE = re.compile(
    r"(?:Closes|Fixes|Resolves)\s+#(\d+)", re.IGNORECASE
)


def _parse_closes_from_body(body: str) -> list[int]:
    """Extract issue numbers from 'Closes #N' patterns in a PR body.

    Fallback for integration-branch-base PRs (ADR-0089 D1) where
    closingIssuesReferences is empty.
    """
    if not body:
        return []
    return [int(m) for m in _CLOSES_RE.findall(body)]


def _discover_develop_pr_slice_links(
    slice_numbers: set[int],
    limit: int = 200,
) -> dict[int, int]:
    """Find slice→PR mapping for integration-branch-base (or
    promoted-to-release) merged PRs (ADR-0089 D1).

    GitHub only populates closingIssuesReferences (and triggers ClosedEvent
    on the issue) when a PR is merged into the *default* branch.  PRs merged
    to the configured integration branch leave both fields empty, breaking
    trail correlation.

    Strategy (two-pass, fix #1020):
      Pass 1: scan --base <integration> merged PRs (the original path).
      Pass 2: if any slices remain uncovered AND the integration-branch scan
              returned data, scan --base <release> merged PRs as fallback.
              This covers the promoted-to-release scenario: once the
              integration branch is squash-merged to the release branch,
              `gh pr list --base <integration>` no longer shows the original
              feature PRs (they are already on the release branch), but
              `--base <release>` promotion PR body often does NOT include
              the original Closes #N.  A broader no-base-filter scan is
              used as the final fallback to catch all cases.
      Each pass: parse 'Closes #N' from each body.

    Args:
        slice_numbers: set of issue numbers that are known slices of this PRD.
        limit: max PRs to scan per pass (default 200; capped to avoid slow calls).

    Returns:
        {slice_issue_number: pr_number} for any match found.
    """
    if not slice_numbers:
        return {}

    _pr_list_fields = "number,body,closingIssuesReferences,mergedAt"
    _cap = str(min(limit, 200))

    def _scan_pr_list(extra_args: list[str]) -> list[dict]:
        """Run gh pr list with extra_args; return parsed list or []."""
        stdout, _err = _run_gh(
            ["pr", "list", "--state", "merged", "--limit", _cap,
             "--json", _pr_list_fields] + extra_args,
            timeout=30,
        )
        if stdout is None:
            return []
        try:
            return json.loads(stdout)
        except Exception:
            return []

    def _extract_mapping(prs: list[dict], remaining: set[int]) -> dict[int, int]:
        """Parse Closes #N from each PR body; return {slice_num: pr_num}."""
        found: dict[int, int] = {}
        for pr in prs:
            pr_num = pr.get("number")
            if not pr_num:
                continue
            # Prefer closingIssuesReferences (populated for main-base merges)
            cir = pr.get("closingIssuesReferences") or []
            if cir:
                for ref in cir:
                    ref_num = ref.get("number")
                    if ref_num and ref_num in remaining and ref_num not in found:
                        found[ref_num] = pr_num
                continue
            # Fallback: parse Closes #N from PR body
            body = pr.get("body") or ""
            for issue_num in _parse_closes_from_body(body):
                if issue_num in remaining and issue_num not in found:
                    found[issue_num] = pr_num
        return found

    mapping: dict[int, int] = {}
    pipeline_config = _load_pipeline_config()
    integration = pipeline_config.integration_branch()
    release = pipeline_config.release_branch()

    # Pass 1: integration-branch-base scan (original path, ADR-0089 D1)
    develop_prs = _scan_pr_list(["--base", integration])
    if develop_prs:
        mapping.update(_extract_mapping(develop_prs, slice_numbers - set(mapping)))

    # Pass 2: release-base fallback (covers promoted-to-release scenario, fix #1020)
    uncovered = slice_numbers - set(mapping)
    if uncovered:
        main_prs = _scan_pr_list(["--base", release])
        if main_prs:
            mapping.update(_extract_mapping(main_prs, uncovered))

    # Pass 3: no-base-filter scan (final fallback; catches any remaining edge cases)
    uncovered = slice_numbers - set(mapping)
    if uncovered:
        all_prs = _scan_pr_list([])
        if all_prs:
            mapping.update(_extract_mapping(all_prs, uncovered))

    return mapping


# ---------------------------------------------------------------------------
# Trail building
# ---------------------------------------------------------------------------
def _build_slice_trail(slice_node: dict, prd_number: int) -> dict:
    """Build the slice portion of the trail from the GraphQL sub-issue node."""
    slice_num = slice_node["number"]
    slice_title = slice_node.get("title", "")
    created_at = slice_node.get("createdAt", "")
    closed_at = slice_node.get("closedAt")
    labels = [n["name"] for n in (slice_node.get("labels") or {}).get("nodes", [])]

    # Find closing PR via ClosedEvent.closer
    closing_pr_number = None
    closed_event_at = None
    timeline_nodes = (slice_node.get("timelineItems") or {}).get("nodes", [])
    for event in timeline_nodes:
        if event.get("__typename") == "ClosedEvent":
            closer = event.get("closer") or {}
            if closer.get("__typename") == "PullRequest":
                closing_pr_number = closer.get("number")
                closed_event_at = event.get("createdAt")
                break

    # Assignees: used by E-SLICEISSUE-IMPL evaluator
    assignees = [
        n.get("login", "") for n in (slice_node.get("assignees") or {}).get("nodes", [])
        if n.get("login")
    ]

    return {
        "number": slice_num,
        "title": slice_title,
        "prd_number": prd_number,
        "labels": labels,
        "created_at": created_at,
        "closed_at": closed_at,
        "closed_event_at": closed_event_at,
        "closing_pr_number": closing_pr_number,
        "assignees": assignees,
        # Raw comments kept for debugging; verdict parsing happens in PR fetch
        "comment_count": len((slice_node.get("comments") or {}).get("nodes", [])),
    }


def _build_pr_trail(pr_number: int) -> tuple[dict | None, str]:
    """Fetch and parse a PR's trail data. Returns (pr_trail, error_class)."""
    pr_data, err = _gh_pr_view(pr_number)
    if pr_data is None:
        return None, err

    # Parse reviewer verdicts from comments
    raw_comments = pr_data.get("comments") or []
    verdicts: list[dict] = []
    for comment in raw_comments:
        body = comment.get("body", "")
        parsed = _parse_verdict_comment(body)
        if parsed is not None:
            parsed["created_at"] = comment.get("createdAt", "")
            verdicts.append(parsed)

    # Infer round numbers for verdicts missing explicit ROUND
    verdicts = _infer_rounds(verdicts)

    # Determine closes: prefer closingIssuesReferences; fall back to parsing
    # "Closes #N" from body (GitHub only populates closingIssuesReferences for
    # PRs merged to the default branch — develop-base merges leave it empty).
    closing_issues = [
        i.get("number") for i in (pr_data.get("closingIssuesReferences") or [])
        if i.get("number")
    ]
    if not closing_issues:
        body_for_closes = pr_data.get("body", "") or ""
        closing_issues = _parse_closes_from_body(body_for_closes)

    # Parse trivial label from PR labels if present (PR JSON doesn't include labels
    # via this endpoint — check body for trivial mention as heuristic)
    body = pr_data.get("body", "")
    is_trivial = bool(re.search(r'\btrivial\b', body, re.IGNORECASE))

    merged_at = pr_data.get("mergedAt")
    last_verdict = verdicts[-1] if verdicts else None
    reviewed_before_merge = (
        last_verdict is not None
        and last_verdict["verdict"] == "APPROVE"
        and merged_at is not None
    )

    # statusCheckRollup — list of check-run objects; absent on old PRs → graceful default [].
    # Shape: [{"__typename":"CheckRun","name":str,"conclusion":str|None,"status":str,...}]
    status_check_rollup = pr_data.get("statusCheckRollup") or []

    return {
        "number": pr_number,
        "created_at": pr_data.get("createdAt", ""),
        "merged_at": merged_at,
        "head_ref": pr_data.get("headRefName", ""),
        "closing_issues": closing_issues,
        "verdicts": verdicts,
        "verdict_count": len(verdicts),
        "last_verdict": last_verdict,
        "reviewed_before_merge": reviewed_before_merge,
        "is_trivial": is_trivial,
        "body_excerpt": body[:200],
        "status_check_rollup": status_check_rollup,
    }, ""


def collect_trail(prd_number: int) -> dict:
    """Collect the full trail for a PRD. Returns a trail dict.

    Shape:
        {
          "prd_number": N,
          "prd_title": str,
          "prd_created_at": str,
          "prd_closed_at": str|None,
          "prd_labels": [str,...],
          "slices": [{...}, ...],
          "prs": {<pr_number>: {...}, ...},
          "collector_status": "" | "transient" | "auth_dead",
          "wall_time_s": float|None,   # prd closed_at - created_at
        }
    """
    # Step 1: Derive repo slug and build the GraphQL query
    slug = _repo_slug()
    gql = _gql_query_for_slug(slug)
    if gql is None:
        return {
            "prd_number": prd_number,
            "collector_status": "auth_dead",
            "error": (
                "repo slug derivation failed: gh repo view unavailable, "
                "git remote origin not a github.com remote, and "
                "DASH_REPO_SLUG env var not set"
            ),
        }

    # Step 1b: GraphQL batch query
    gql_data, err = _gh_graphql(gql, {"n": prd_number})
    if gql_data is None:
        return {
            "prd_number": prd_number,
            "collector_status": err,
            "error": f"GraphQL fetch failed: {err}",
        }

    issue = (gql_data.get("repository") or {}).get("issue") or {}
    if not issue:
        return {
            "prd_number": prd_number,
            "collector_status": "transient:no_issue",
            "error": f"Issue #{prd_number} not found",
        }

    prd_title = issue.get("title", "")
    prd_created_at = issue.get("createdAt", "")
    prd_closed_at = issue.get("closedAt")
    prd_labels = [n["name"] for n in (issue.get("labels") or {}).get("nodes", [])]

    # Parse prd-level critic verdict comments (for E-PRDCRITIC-APPROVE etc.)
    prd_comment_nodes = (issue.get("comments") or {}).get("nodes", [])
    prd_verdicts: list[dict] = []
    for comment in prd_comment_nodes:
        body = comment.get("body", "")
        parsed = _parse_verdict_comment(body)
        if parsed is not None:
            parsed["created_at"] = comment.get("createdAt", "")
            parsed["author"] = (comment.get("author") or {}).get("login", "")
            prd_verdicts.append(parsed)
    prd_verdicts = _infer_rounds(prd_verdicts)

    # Step 2: Build slice trail entries
    sub_issues_nodes = (issue.get("subIssues") or {}).get("nodes", [])
    slices = []
    candidate_pr_numbers: set[int] = set()
    for node in sub_issues_nodes:
        st = _build_slice_trail(node, prd_number)
        slices.append(st)
        if st["closing_pr_number"]:
            candidate_pr_numbers.add(st["closing_pr_number"])

    # Step 2b: Two-tier develop-base fallback — if any slices lack a
    # closing_pr_number (GitHub doesn't auto-close on develop-base merges),
    # scan merged develop-base PRs for "Closes #N" bodies to fill the gap.
    uncovered = {s["number"] for s in slices if not s.get("closing_pr_number")}
    if uncovered:
        develop_links = _discover_develop_pr_slice_links(uncovered)
        for s in slices:
            if s["number"] in develop_links and not s.get("closing_pr_number"):
                s["closing_pr_number"] = develop_links[s["number"]]
                candidate_pr_numbers.add(develop_links[s["number"]])

    # Step 3: Fetch each candidate PR
    prs: dict[int, dict] = {}
    overall_pr_err = ""
    for pr_num in sorted(candidate_pr_numbers):
        pr_trail, pr_err = _build_pr_trail(pr_num)
        if pr_trail is not None:
            prs[pr_num] = pr_trail
        else:
            overall_pr_err = pr_err

    # Step 4: Compute wall time
    wall_time_s = None
    if prd_created_at and prd_closed_at:
        try:
            from datetime import datetime, timezone

            def _parse_dt(s):
                # GitHub uses Z suffix (UTC)
                s = s.replace("Z", "+00:00")
                return datetime.fromisoformat(s)

            t0 = _parse_dt(prd_created_at)
            t1 = _parse_dt(prd_closed_at)
            wall_time_s = (t1 - t0).total_seconds()
        except Exception:
            pass

    collector_status = overall_pr_err or ""

    return {
        "prd_number": prd_number,
        "prd_title": prd_title,
        "prd_created_at": prd_created_at,
        "prd_closed_at": prd_closed_at,
        "prd_labels": prd_labels,
        "prd_verdicts": prd_verdicts,
        "slices": slices,
        "prs": {str(k): v for k, v in prs.items()},
        "collector_status": collector_status,
        "wall_time_s": wall_time_s,
    }


# ---------------------------------------------------------------------------
# Cache layer
# ---------------------------------------------------------------------------
def _cache_path(prd_number: int) -> Path:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return _CACHE_DIR / f"prd-{prd_number}.json"


def _load_cache(prd_number: int) -> dict | None:
    """Return cached trail or None if missing/stale."""
    path = _cache_path(prd_number)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None

    cached_closed_at = data.get("closed_at")
    # Closed PRD → cache forever
    if cached_closed_at:
        return data.get("trail")

    # Open PRD → check TTL
    fetched_at = data.get("fetched_at", 0)
    if (time.time() - fetched_at) < TTL_OPEN_S:
        return data.get("trail")
    return None


def _save_cache(prd_number: int, trail: dict) -> None:
    """Persist trail to cache file."""
    path = _cache_path(prd_number)
    wrapper = {
        "fetched_at": time.time(),
        "closed_at": trail.get("prd_closed_at"),
        "trail": trail,
    }
    try:
        path.write_text(json.dumps(wrapper, indent=2), encoding="utf-8")
    except Exception:
        pass  # cache write failure is non-fatal


def get_trail(prd_number: int, force_refresh: bool = False) -> dict:
    """Get trail for a PRD (cache-first).

    Returns the trail dict. Never raises.
    On persistent failure: returns a trail with collector_status != "".
    """
    if not force_refresh:
        cached = _load_cache(prd_number)
        if cached is not None:
            cached["_from_cache"] = True
            return cached

    trail = collect_trail(prd_number)
    # Only cache if the call itself succeeded (even partial)
    if "error" not in trail or trail.get("prd_title"):
        _save_cache(prd_number, trail)
    return trail


# ---------------------------------------------------------------------------
# Rollup helpers: closed PRD list + recent PRs (for unreviewed-merge scanning)
# ---------------------------------------------------------------------------

def get_closed_prd_numbers(last_n: int = 10) -> list[int]:
    """Return the last N closed PRD issue numbers (most-recent-closed first).

    Uses gh issue list CLI directly (no API-server wrapper).
    Returns [] on any error.
    """
    try:
        result = subprocess.run(
            [
                "gh", "issue", "list",
                "--label", "prd",
                "--state", "closed",
                "--limit", str(max(last_n, 10)),
                "--json", "number",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            cwd=str(_REPO_ROOT),
            stdin=subprocess.DEVNULL,
        )
        if result.returncode != 0:
            return []
        items = json.loads(result.stdout)
        return [item["number"] for item in items[:last_n]]
    except Exception:
        return []


def get_recent_merged_prs(limit: int = 30) -> list[dict]:
    """Return recent merged PRs with number, mergedAt, head ref, and labels.

    Used by rollup to detect unreviewed merges that are NOT linked to a PRD trail
    (e.g., hotfix-lane PRs like #650 that close non-slice issues).
    Returns [] on any error.
    """
    try:
        stdout, err = _run_gh([
            "pr", "list",
            "--state", "merged",
            "--limit", str(limit),
            "--json", "number,mergedAt,headRefName,labels,title",
        ])
        if stdout is None:
            return []
        return json.loads(stdout)
    except Exception:
        return []


def get_pr_verdict_count(pr_number: int) -> int:
    """Return the number of reviewer verdict comments on a PR.

    Lightweight check: fetches PR comments and counts VERDICT: lines.
    Returns -1 on error (treated as unknown, not flagged as violation).
    """
    pr_trail, err = _build_pr_trail(pr_number)
    if pr_trail is None:
        return -1
    return pr_trail.get("verdict_count", 0)


def rollup(last_n: int = 10, compare_fn=None, spec=None) -> dict:
    """Aggregate per-edge confirmation counts and violations across last N closed PRDs.

    Args:
        last_n:     number of closed PRDs to scan (default 10)
        compare_fn: the compare() function from comparison module (injected to avoid circular)
        spec:       SPEC dict (injected)

    Returns:
        {
          "prd_numbers":  [N, ...],          # PRDs included in rollup
          "edge_counts":  {E-*: {confirmed:N, total:N, ...}},
          "never_confirmed_required": [E-*, ...],  # required:always never confirmed
          "violations":   [{type, detail, pr_number?, ...}],  # all violations
          "unreviewed_prs": [{number, merged_at, title}],     # global PR scan
          "run_results":  {N: run_pass},
          "window_size":  N,
          "runtime_counts": {E-*: {confirmed:N, unobserved:N, not_observable:N,
                                    not_exercised:N, total:N}},  # runtime-tier rollup
        }
    """
    prd_numbers = get_closed_prd_numbers(last_n)

    edge_counts: dict[str, dict] = {}
    runtime_counts: dict[str, dict] = {}
    all_violations: list[dict] = []
    run_results: dict[int, bool] = {}

    # Per-PRD comparison
    if compare_fn is not None and spec is not None:
        github_edges = [
            e for e in spec.get("edges", []) if e.get("evidence") == "github"
        ]
        runtime_edges = [
            e for e in spec.get("edges", []) if e.get("evidence") == "runtime"
        ]
        for eid in [e["id"] for e in github_edges]:
            edge_counts[eid] = {
                "confirmed": 0,
                "total": 0,
                "required": next(
                    (e["required"] for e in github_edges if e["id"] == eid), "conditional"
                ),
            }
        for eid in [e["id"] for e in runtime_edges]:
            runtime_counts[eid] = {
                "confirmed": 0,
                "unobserved": 0,
                "not_observable": 0,
                "not_exercised": 0,
                "total": 0,
                "required": next(
                    (e["required"] for e in runtime_edges if e["id"] == eid), "conditional"
                ),
            }

        for prd_num in prd_numbers:
            trail = get_trail(prd_num)
            if trail.get("collector_status") == "auth_dead":
                continue
            report = compare_fn(spec, trail)
            run_results[prd_num] = report.get("run_pass", False)
            edges = report.get("edges", {})
            for eid, einfo in edges.items():
                if eid not in edge_counts:
                    continue
                state = einfo.get("state", "not-evaluated")
                edge_counts[eid]["total"] += 1
                if state == "confirmed":
                    edge_counts[eid]["confirmed"] += 1
            # Collect per-PRD violations
            for v in report.get("violations", []):
                v2 = dict(v)
                v2["prd_number"] = prd_num
                all_violations.append(v2)
            # Collect per-PRD runtime observation counts
            rt_obs = report.get("runtime_edges", {})
            for eid, rtinfo in rt_obs.items():
                if eid not in runtime_counts:
                    continue
                state = rtinfo.get("state", "")
                runtime_counts[eid]["total"] += 1
                if state == "runtime-confirmed":
                    runtime_counts[eid]["confirmed"] += 1
                elif state == "runtime-unobserved":
                    runtime_counts[eid]["unobserved"] += 1
                elif state == "not-observable":
                    runtime_counts[eid]["not_observable"] += 1
                elif state == "not-exercised":
                    runtime_counts[eid]["not_exercised"] += 1

    # Global PR scan: detect unreviewed merges not caught by per-PRD comparison
    # (hotfix-lane PRs, PRs closing non-slice issues, etc.)
    recent_prs = get_recent_merged_prs(limit=50)
    known_pr_numbers: set[int] = set()
    for prd_num in prd_numbers:
        # Collect PR numbers already covered by the PRD trails
        trail = get_trail(prd_num)
        for pr_key in trail.get("prs", {}).keys():
            try:
                known_pr_numbers.add(int(pr_key))
            except (ValueError, TypeError):
                pass

    unreviewed_prs: list[dict] = []
    for pr in recent_prs:
        pr_num = pr.get("number")
        if not pr_num:
            continue
        # Check labels: trivial label means it's expected to have no verdict
        labels = [lb.get("name", "") for lb in (pr.get("labels") or [])]
        is_trivial = "trivial" in labels
        if is_trivial:
            continue
        # Fetch verdict count for PRs not already in our trails
        if pr_num in known_pr_numbers:
            # Already checked via per-PRD comparison; skip to avoid duplicate
            continue
        verdict_count = get_pr_verdict_count(pr_num)
        if verdict_count == 0 and pr.get("mergedAt"):
            unreviewed_prs.append({
                "number": pr_num,
                "merged_at": pr.get("mergedAt", ""),
                "title": pr.get("title", ""),
                "head_ref": pr.get("headRefName", ""),
                "detail": (
                    f"PR #{pr_num} merged at {pr.get('mergedAt','?')} "
                    f"with zero reviewer verdicts and no trivial label"
                ),
            })
            all_violations.append({
                "type": "unreviewed_merge",
                "pr_number": pr_num,
                "merged_at": pr.get("mergedAt", ""),
                "prd_number": None,
                "detail": (
                    f"PR #{pr_num} merged at {pr.get('mergedAt','?')} "
                    f"with zero reviewer verdicts and no trivial label"
                ),
            })

    # Identify required:always edges never confirmed across the window
    never_confirmed_required: list[str] = []
    for eid, counts in edge_counts.items():
        if counts["required"] == "always" and counts["total"] > 0 and counts["confirmed"] == 0:
            never_confirmed_required.append(eid)

    return {
        "prd_numbers": prd_numbers,
        "edge_counts": edge_counts,
        "never_confirmed_required": never_confirmed_required,
        "violations": all_violations,
        "unreviewed_prs": unreviewed_prs,
        "run_results": {str(k): v for k, v in run_results.items()},
        "window_size": last_n,
        "runtime_counts": runtime_counts,
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
def _format_duration(s: float) -> str:
    if s < 60:
        return f"{s:.0f}s"
    m = int(s) // 60
    sec = int(s) % 60
    if m < 60:
        return f"{m}m{sec:02d}s"
    h = m // 60
    m2 = m % 60
    return f"{h}h{m2:02d}m{sec:02d}s"


def _cli_print_trail(trail: dict, compare: bool = False) -> None:
    """Print a human-readable trail summary to stdout."""
    if "error" in trail and not trail.get("prd_title"):
        print(f"ERROR: {trail['error']}")
        print(f"collector_status: {trail.get('collector_status', 'unknown')}")
        return

    from_cache = trail.get("_from_cache", False)
    cache_note = " [CACHE HIT]" if from_cache else ""
    print(f"PRD #{trail['prd_number']}: {trail['prd_title']}{cache_note}")
    print(f"  created_at: {trail.get('prd_created_at', 'n/a')}")
    print(f"  closed_at:  {trail.get('prd_closed_at', 'open')}")
    wt = trail.get("wall_time_s")
    if wt is not None:
        print(f"  wall_time:  {_format_duration(wt)}")
    print(f"  labels:     {', '.join(trail.get('prd_labels', []))}")
    print(f"  slices:     {len(trail.get('slices', []))}")
    slices = trail.get("slices", [])
    for sl in slices:
        pr_num = sl.get("closing_pr_number")
        pr_note = f" -> PR #{pr_num}" if pr_num else " (no closing PR found)"
        print(f"    slice #{sl['number']}: {sl['title'][:60]}{pr_note}")
    print(f"  PRs:        {len(trail.get('prs', {}))}")
    prs = trail.get("prs", {})
    for pr_key, pr in sorted(prs.items(), key=lambda x: int(x[0])):
        verdicts = pr.get("verdicts", [])
        verdict_summary = []
        for v in verdicts:
            r = v.get("round", "?")
            vt = v.get("verdict", "?")
            ri = " (inferred)" if v.get("round_inferred") else ""
            verdict_summary.append(f"R{r}:{vt}{ri}")
        v_str = ", ".join(verdict_summary) if verdict_summary else "no verdicts"
        merged = pr.get("merged_at", "not merged")
        trivial = " [trivial]" if pr.get("is_trivial") else ""
        print(
            f"    PR #{pr['number']}: "
            f"verdicts=[{v_str}] merged={merged}{trivial}"
        )
    if trail.get("collector_status"):
        print(f"  collector_status: {trail['collector_status']}")

    if compare:
        _cli_compare(trail)


def _cli_compare(trail: dict) -> None:
    """Run comparison engine and print results."""
    # Import comparison module (same directory)
    _insert_dashboard_path()
    from comparison import compare, get_spec_for_compare  # noqa: PLC0415
    spec = get_spec_for_compare()
    report = compare(spec, trail)
    print("\n--- Comparison report ---")
    print(f"  run_pass: {report['run_pass']}")
    edges = report.get("edges", {})
    for edge_id, edge_report in sorted(edges.items()):
        state = edge_report.get("state", "?")
        detail = edge_report.get("detail", "")
        detail_str = f" ({detail})" if detail else ""
        print(f"  {edge_id}: {state}{detail_str}")
    violations = report.get("violations", [])
    if violations:
        print(f"  violations ({len(violations)}):")
        for v in violations:
            print(f"    - {v['type']}: {v['detail']}")
    else:
        print("  violations: none")


def _insert_dashboard_path() -> None:
    """Ensure dashboard/ is on sys.path for sibling imports."""
    dashboard_dir = str(Path(__file__).resolve().parent)
    if dashboard_dir not in sys.path:
        sys.path.insert(0, dashboard_dir)


def _cli_print_rollup(rollup_result: dict) -> None:
    """Print a human-readable rollup summary to stdout."""
    prd_numbers = rollup_result.get("prd_numbers", [])
    window = rollup_result.get("window_size", 10)
    run_results = rollup_result.get("run_results", {})
    edge_counts = rollup_result.get("edge_counts", {})
    never_confirmed = rollup_result.get("never_confirmed_required", [])
    violations = rollup_result.get("violations", [])
    unreviewed_prs = rollup_result.get("unreviewed_prs", [])

    print(f"\n--- Rollup: last {window} closed PRDs ---")
    print(f"  PRDs scanned: {prd_numbers}")
    pass_count = sum(1 for v in run_results.values() if v)
    fail_count = len(run_results) - pass_count
    print(f"  Run results:  PASS={pass_count} FAIL={fail_count}")

    if run_results:
        for prd_str, passed in sorted(run_results.items(), key=lambda x: int(x[0])):
            status = "PASS" if passed else "FAIL"
            print(f"    PRD #{prd_str}: {status}")

    if edge_counts:
        print(f"\n  Per-edge confirmation counts (github-tier only):")
        for eid, counts in sorted(edge_counts.items()):
            confirmed = counts.get("confirmed", 0)
            total = counts.get("total", 0)
            req = counts.get("required", "conditional")
            req_mark = " [required:always]" if req == "always" else ""
            never_mark = " *** NEVER CONFIRMED ***" if eid in never_confirmed else ""
            print(f"    {eid:<40} {confirmed}/{total}{req_mark}{never_mark}")

    if never_confirmed:
        print(f"\n  WARNING: {len(never_confirmed)} required:always edge(s) never confirmed:")
        for eid in never_confirmed:
            print(f"    {eid} — never exercised across {len(prd_numbers)} PRDs; spec may be aspirational")

    if violations:
        print(f"\n  Violations ({len(violations)}) across rollup window:")
        for v in violations:
            prd_note = f" [PRD #{v['prd_number']}]" if v.get("prd_number") else ""
            print(f"    [{v['type']}]{prd_note}: {v['detail']}")
    else:
        print("\n  Violations: none")

    if unreviewed_prs:
        print(f"\n  Unreviewed merges (global PR scan, outside PRD trails):")
        for pr in unreviewed_prs:
            print(f"    PR #{pr['number']} merged={pr['merged_at']} — {pr['title'][:60]}")
    else:
        print("  Unreviewed merges (global scan): none")


if __name__ == "__main__":
    import argparse

    _insert_dashboard_path()

    parser = argparse.ArgumentParser(
        description="Collect GitHub artifact trail for a PRD"
    )
    parser.add_argument("--prd", type=int, default=None, help="PRD issue number")
    parser.add_argument(
        "--compare", action="store_true", help="Run comparison engine after collecting"
    )
    parser.add_argument(
        "--refresh", action="store_true", help="Force refresh (ignore cache)"
    )
    parser.add_argument(
        "--rollup", action="store_true", help="Repo rollup: aggregate last N closed PRDs"
    )
    parser.add_argument(
        "--last", type=int, default=10, help="Number of closed PRDs for rollup (default 10)"
    )
    args = parser.parse_args()

    if args.rollup:
        # Rollup mode: inject comparison module to avoid circular imports
        from comparison import compare, get_spec_for_compare  # noqa: PLC0415
        spec = get_spec_for_compare()
        result = rollup(last_n=args.last, compare_fn=compare, spec=spec)
        _cli_print_rollup(result)
        # Exit 0 if no unreviewed merges; 1 if any found (for CI-style use)
        sys.exit(0)
    elif args.prd:
        trail = get_trail(args.prd, force_refresh=args.refresh)
        _cli_print_trail(trail, compare=args.compare)

        # Exit 0 if collection succeeded; 1 if auth_dead
        status = trail.get("collector_status", "")
        if status == "auth_dead":
            print("\nERROR: gh authentication failed. Run: gh auth status", file=sys.stderr)
            sys.exit(1)
        sys.exit(0)
    else:
        parser.print_help()
        sys.exit(1)
