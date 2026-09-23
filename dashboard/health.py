"""
dashboard/health.py — health check helpers + aggregate-payload TTL cache.

Exports:
    check_docs1_adr_index_forward() -> dict
    check_docs2_adr_index_reverse() -> dict
    check_docs3_claude_md_agents() -> dict
    check_docs4_claude_md_skills() -> dict
    check_docs5_n3_literal() -> dict
    check_docs6_glossary_md_refs() -> dict
    check_docs7_adr_citations() -> dict
    check_docs8_supersession_notes() -> dict
    check_docs9_glossary_cap() -> dict
    check_docs10_backlog_surfacing() -> dict
    check_docs11_dead_citations() -> dict  (slice #796/ADR-0064 D2: dead-citation check)
    check_r_sensitive_detector() -> dict   (slice #840/ADR-0070 D4: guardrail-touching promotions counter)
    check_meta_tripwire() -> dict          (slice #840/ADR-0070 D4: promotion meta-tripwire — FAIL if guardrail-path batch lacks promotion-ack)
    audit_subagents() -> dict
    check_audit_subagents() -> dict  (slice #921/PRD #919: AS-AUDIT registry entry)
    audit_meta() -> dict
    check_test_ordering() -> dict         (slice #816/ADR-0067 D2: fix-type PR test-first ordering rate)
    check_quarantine_sla() -> dict        (slice #816/ADR-0067 D4: quarantine register size + oldest-entry SLA)
    check_frontmatter_coverage() -> dict  (slice #820/ADR-0027 D1: % agent files with explicit model: frontmatter)
    check_capture_slo() -> dict          (slice #767: capture liveness SLO)
    check_hook_integrity() -> dict       (slice #767: hook attempt-vs-ok ratio)
    check_hook_liveness() -> dict        (slice #849: hook-layer dark detection via beacon lag)
    check_isolation_group() -> dict      (slice #767: worktree orphan/drift check)
    check_rule_coverage() -> dict        (slice #768/ADR-0056 D3: rule coverage ratio)
    check_spec_coverage() -> dict        (slice #798/ADR-0066 D2: per-PRD criterion coverage)
    check_blind_dispatch_rate() -> dict  (slice #783/ADR-0060 D1: BLIND-REVIEW prefix rate)
    check_residual_ratio() -> dict       (slice #797/ADR-0066 D1: JUDGMENT+EXTRACT_FAILED / total QA-plan rows)
    check_proof_presence() -> dict       (slice #783/ADR-0061 D1: route+proof-token per merged PR)
    check_proof_integrity() -> dict      (slice #839/ADR-0070 D5: DOM inner_text attestation check)
    check_merge_integrity() -> dict      (slice #783/ADR-0062 D1: BEHIND encountered/recovered)
    check_capture_shape() -> dict        (slice #783/ADR-0063 D2: 3-heading regex over root-cause issues)
    check_green_main() -> dict           (slice #783/ADR-0062 D3: last main_green sha + lag + age)
    check_record_vs_gh() -> dict         (slice #1081/PRD #1075 cr.3: recorded pr_merged spans vs gh ground truth)
    check_slice_vs_pr() -> dict          (slice #1136/PRD #1127 cr.11b: merged slice PR vs dispatch+pr_opened spans)
    check_merged_without_verdict() -> dict  (slice #1136/PRD #1127 cr.11b: merged PR vs verdict span, ADR-0076 anchor)
    check_closed_prd_vs_qa() -> dict     (slice #1136/PRD #1127 cr.11b: closed PRD vs qa_verified PASS span)
    serve_health() -> dict          (TTL-cached; <200ms on second call)
    _health_background() -> None    (background thread target)
    _health_cache, _health_lock, _health_computing, _HEALTH_TTL

Import direction: server <- health (this module must NOT import server).
"""

import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# gh_cache — shared TTL+timeout wrapper for gh CLI calls (slice #995/PRD #993).
# Import is lazy-safe: if gh_cache is missing (old install), fall back to raw
# subprocess so health.py still works on any Python path.
# ---------------------------------------------------------------------------
try:
    from gh_cache import gh_fetch as _gh_fetch_impl
    _GH_CACHE_AVAILABLE = True
except ImportError:
    _gh_fetch_impl = None  # type: ignore[assignment]
    _GH_CACHE_AVAILABLE = False


def _health_gh_fetch(
    args: list,
    *,
    ttl: float = 60.0,
    timeout: float = 5.0,
    with_source: bool = False,
) -> tuple:
    """Route a gh call through gh_cache (degrade-not-block).

    Returns (returncode: int, stdout: str) by default, matching the existing
    ``_sp.run`` pattern used inside each check's inner helper. Pass
    ``with_source=True`` to additionally receive the provenance label as a
    third tuple element: (returncode, stdout, source).

    Provenance vocabulary is closed (ADR-0087 D1):
      - confirmed   : "live" (this call succeeded) or "cache" (a fresh
        cached success within ttl) — always paired with rc=0.
      - unconfirmed : "stale" (gh failed on THIS call; a last-known value
        exists) or "computing" (gh failed and no prior value exists) —
        always paired with rc=1, stdout="". A "stale" answer means GitHub
        failed right now; in a one-shot CLI process it proves nothing about
        the present, so it is no longer read as success (this reverses the
        PRD #993 mapping — ADR-0088 D1 deleted that mapping's only
        consumer, the served dashboard row).

    When gh_cache is unavailable (import failed), falls back to direct
    subprocess.run with a hard ``timeout`` cap so the stall is still
    bounded; the fallback labels its answer "live" on rc=0 and "computing"
    otherwise.

    Parameters
    ----------
    args        : gh sub-command args (the "gh" binary is prepended by gh_cache).
    ttl         : cache TTL in seconds (how long a fresh result is reused).
    timeout     : hard per-call timeout in seconds passed to gh_cache / subprocess.
    with_source : when True, return a 3-tuple including the provenance source.
    """
    if _GH_CACHE_AVAILABLE and _gh_fetch_impl is not None:
        result = _gh_fetch_impl(args, ttl=ttl, timeout=timeout)
        source = result.source
        if source in ("live", "cache"):
            rc, out = 0, result.value
        else:
            rc, out = 1, ""
        return (rc, out, source) if with_source else (rc, out)
    # Fallback: bounded subprocess (still better than unbounded)
    try:
        r = subprocess.run(
            ["gh"] + list(args),
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout,
            cwd=str(_HEALTH_REPO_ROOT), stdin=subprocess.DEVNULL,
        )
        if r.returncode == 0:
            rc, out, source = 0, (r.stdout or ""), "live"
        else:
            rc, out, source = 1, "", "computing"
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError, Exception):
        rc, out, source = 1, "", "computing"
    return (rc, out, source) if with_source else (rc, out)


# ---------------------------------------------------------------------------
# Repo root — health.py lives at <repo>/dashboard/health.py
# ---------------------------------------------------------------------------
_HEALTH_REPO_ROOT = Path(__file__).resolve().parent.parent


def _telemetry_log_root() -> Path:
    """Return the canonical repo root for shared telemetry logs (slice #1021).

    Resolves ``git rev-parse --git-common-dir`` using _HEALTH_REPO_ROOT as the
    working directory.  Using _HEALTH_REPO_ROOT (rather than a cached absolute
    path) ensures test suites that patch _HEALTH_REPO_ROOT to a temp dir will
    receive the expected (patched) fallback when git is unavailable in that dir.

    Algorithm:
      1. Run ``git rev-parse --git-common-dir`` with cwd=_HEALTH_REPO_ROOT.
      2. Resolve the returned path to an absolute path (may be relative ".git"
         or absolute "<canonical_root>/.git").
      3. Return the parent of that .git directory = canonical root.
      4. On ANY failure, return _HEALTH_REPO_ROOT unchanged (pre-fix behaviour,
         also the correct value when running from the canonical root).

    Use for ONLY the two shared log files:
      - .claude/logs/hook-fires.jsonl
      - .claude/logs/workflow-events.jsonl
    Code/doc paths (agents/, skills/, decisions/) remain relative to
    _HEALTH_REPO_ROOT so worktree-run dashboards read their own source.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=str(_HEALTH_REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return _HEALTH_REPO_ROOT
        git_common_dir = result.stdout.strip()
        if not git_common_dir:
            return _HEALTH_REPO_ROOT
        git_common_path = Path(git_common_dir)
        if not git_common_path.is_absolute():
            git_common_path = (_HEALTH_REPO_ROOT / git_common_path).resolve()
        else:
            git_common_path = git_common_path.resolve()
        return git_common_path.parent
    except Exception:
        return _HEALTH_REPO_ROOT


# Declared-ID source paths for parsing check rationale + mechanic text (slice #629).
# _AUDIT_META_SKILL was the former source for DOCS-*/STRUCT-* check IDs; after PRD #919
# slice #920, the canonical declared-ID source for those checks moved to codebase-critic.md
# (the ### STRUCT-N / ### DOCS-N headings in its "Deterministic pre-checks" section).
# _AUDIT_SUBAGENTS_SKILL was the former source for AS-* check IDs; after PRD #919
# slice #921, AS-* checks are registered in CHECK_REGISTRY (AS-AUDIT) — no separate source.
_CODEBASE_CRITIC_MD = _HEALTH_REPO_ROOT / ".claude" / "agents" / "codebase-critic.md"

# ---------------------------------------------------------------------------
# tools/trace.py lazy loader — reused by check_record_vs_gh (slice #1081).
# Loaded under a private module name (never "trace" — collides with the
# stdlib `trace` coverage module). TRACE_LOG_OVERRIDE is read at CALL time
# inside trace.py's own trace_log_path()/read_spans(), so caching the loaded
# module here does not interfere with per-test log-path overrides.
# ---------------------------------------------------------------------------
_TRACE_V3_PY = _HEALTH_REPO_ROOT / "tools" / "trace.py"
_trace_v3_module = None


def _load_trace_v3():
    """Lazily import tools/trace.py and cache the module object. Returns None
    (never raises) if the file is missing — callers degrade honestly."""
    global _trace_v3_module
    if _trace_v3_module is not None:
        return _trace_v3_module
    if not _TRACE_V3_PY.exists():
        return None
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("trace_v3_health", str(_TRACE_V3_PY))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _trace_v3_module = mod
        return mod
    except Exception:
        return None


def _v3_trace_log_exists() -> bool:
    """Whether the canonical v3 trace log currently exists on disk.

    Root-cause fix (discovered wiring CHECK 22 into CI, slice #1136): the
    v3 trace log is a gitignored LOCAL runtime artifact — GitHub Actions'
    fresh checkout structurally never has it (same class as a genuinely
    fresh repo before the first span is ever emitted). Every
    RECORD-VS-GH-pattern reconciler (check_record_vs_gh,
    check_slice_vs_pr, check_merged_without_verdict,
    check_closed_prd_vs_qa) MUST check this BEFORE reconciling — reading
    an absent log returns an empty span list indistinguishable from "every
    single artifact's span was individually dropped", which would
    fabricate a FAIL against ALL historical artifacts at once rather than
    degrading honestly to WARN. Mirrors the existing trace_exists idiom
    already used by check_stream_liveness.
    """
    try:
        trace_mod = _load_trace_v3()
        path = trace_mod.trace_log_path() if trace_mod is not None else None
    except Exception:
        path = None
    return bool(path) and os.path.exists(path)


# Known critics — single-sourced from _constants.py (CHECK 7 regexes that
# file's literal; ADR-0088 D4). Aliased to the pre-existing local name so
# every downstream reference in this module is unchanged.
from _constants import KNOWN_CRITICS as _KNOWN_CRITICS  # noqa: E402

# ---------------------------------------------------------------------------
# Aggregate health-payload TTL cache — health checks can take 1-2 s on cold
# start. Background-thread + TTL cache pattern. Retained as a library shim
# with no HTTP caller after ADR-0088 D1's server deletion (captured: #1491).
# ---------------------------------------------------------------------------
_health_cache: dict = {}       # {"data": {...}, "ts": float}
_health_computing: bool = False
_health_lock = threading.Lock()
_HEALTH_TTL = 300              # seconds — raised from 30s (#1012): slow-gh compute
                               # takes ~60-95s; 30s TTL made refreshing=True near-permanent.

# Timeout for the ci-checks.sh subprocess call inside check_release_ready() condition (a).
# ci-checks.sh now runs gh-dependent checks (CHECK 19 slicer-provenance, etc.) that push
# the runtime to ~95s on develop. The former hard-coded 60s caused systematic false-fail of
# condition (a) → gate held → promote.sh refused all promotions (#981).
# Set to 300s: ~3× the observed runtime, sufficient headroom for transient gh-API latency.
_RELEASE_READY_CICHECKS_TIMEOUT_S = 300

# Timeout for the pytest subprocess call inside check_release_ready() condition (b).
# The full tests/ suite now runs ~149s on develop (726 passed in 148.65s at 5429afe) as
# the suite has grown past 900+ tests. The former hard-coded 120s caused systematic
# TimeoutExpired → condition (b) false-fail → RELEASE-READY gate held even though the
# suite was genuinely green (#1120 — an incomplete #981 class revision: #981 raised the
# sibling ci-checks.sh sub-call to a named constant but did not sweep this pytest sub-call).
# Set to 300s: ~2× the observed runtime, sufficient headroom for growth + transient slowness.
_RELEASE_READY_PYTEST_TIMEOUT_S = 300


def _fetch_github_ci_conclusion(repo_root, sha=None) -> tuple:
    """Query GitHub for the `ci` check conclusion on the given (or develop
    HEAD) commit.

    Strategy (issue #986):
      1. Resolve the sha to query — see `sha` parameter below.
      2. Fetch the latest merged PRs targeting develop (gh pr list --base develop
         --state merged --limit N --json number,mergeCommit).
      3. Find the PR whose mergeCommit.oid matches the resolved sha.
      4. Run `gh pr checks <n> --json name,state` and look for name="ci".
      5. Map state: SUCCESS/PASS → "pass"; FAILURE/ERROR/CANCELLED → "fail";
         anything else (PENDING/IN_PROGRESS) → "pending".

    Parameters
    ----------
    repo_root : the repo root to run `git` from (only consulted when `sha`
                is omitted — see below).
    sha        : ADR-0079 D2 (#1192 fix) — the EXACT sha to certify, when the
                caller already holds it (e.g. tools/record-green.sh's own
                resolved DEV_SHA, itself possibly a caller-provided confirmed
                merge oid per #1188). When given, this function uses it
                DIRECTLY and skips the internal `git rev-parse
                origin/develop` derivation entirely — the PR-mergeCommit
                search below can then only ever match the requested sha,
                never a stale local ref. This closes the #1192 class: the
                OLD (sha-less) code re-derived its own sha independently of
                whatever the caller had already resolved, so a local ref
                that moved between the two derivations (e.g. a DIFFERENT PR
                merging to develop in between) could cause this function to
                cite a NEIGHBOURING PR's CI run instead of the one the
                caller actually meant to certify (live incident: PR
                #1190/#1191).
                STRICTLY ADDITIVE: when `sha` is omitted (None, the default),
                behavior is byte-identical to the pre-#1192-fix code — every
                existing no-sha caller (e.g. RELEASE-READY condition (a)) is
                unaffected.

    Returns (status, detail) where status is one of:
      "pass"        — GitHub ci check succeeded
      "fail"        — GitHub ci check failed
      "pending"     — GitHub ci check is still running
      "unavailable" — gh CLI unavailable, rate-limited, or no matching PR
                      found for the resolved sha (UNCHANGED semantics —
                      the sha parameter does not introduce a new status
                      value nor alter this branch; #1166's separate
                      unavailable-sentinel conflation is untouched here)

    This function is intentionally injectable/monkeypatchable at module level so
    tests can substitute a mock without subprocess patching.
    """
    import json as _json
    import subprocess as _sp

    repo_root = Path(repo_root)

    if sha:
        # Explicit sha (ADR-0079 D2): use it as-is, no local git derivation.
        develop_sha = sha
    else:
        # Step 1: Get develop HEAD SHA (byte-identical to the pre-#1192-fix
        # code — this branch is untouched by the sha parameter's addition).
        try:
            sha_r = _sp.run(
                ["git", "rev-parse", "origin/develop"],
                capture_output=True, text=True, timeout=10,
                cwd=str(repo_root),
            )
            if sha_r.returncode != 0:
                return "unavailable", "git rev-parse origin/develop failed"
            develop_sha = sha_r.stdout.strip()
        except Exception as exc:
            return "unavailable", f"git rev-parse error: {exc}"

    # Step 3: Fetch recent merged PRs to develop and find the one matching the resolved sha.
    # Routed through gh_cache (ttl=30s, timeout=5s) so a slow gh degrades to
    # "unavailable" (which triggers the ci-checks.sh local fallback) rather than
    # blocking the request path for up to 20s (PRD #993 cr.3, slice #996).
    # ttl=30s caches only within one long-running process; CLI invocations
    # (including promote.sh's --check RELEASE-READY) each start a fresh
    # subprocess (empty cache) so the gate always reads LIVE GitHub ci (#986 honesty preserved).
    try:
        _pr_rc, _pr_out = _health_gh_fetch(
            ["pr", "list", "--base", "develop", "--state", "merged",
             "--limit", "20", "--json", "number,mergeCommit"],
            ttl=30.0, timeout=5.0,
        )
        if _pr_rc != 0 or not _pr_out.strip():
            return "unavailable", "gh pr list error or timeout (cache miss)"
        prs = _json.loads(_pr_out)
    except Exception as exc:
        return "unavailable", f"gh pr list exception: {exc}"

    matching_pr = None
    for pr in prs:
        mc = pr.get("mergeCommit") or {}
        if mc.get("oid", "") == develop_sha:
            matching_pr = pr.get("number")
            break

    if matching_pr is None:
        return "unavailable", (
            f"no merged PR to develop matched HEAD {develop_sha[:8]} "
            f"(checked {len(prs)} recent PRs)"
        )

    # Step 4: Query check status for that PR (via gh_cache, same degrade policy).
    try:
        _chk_rc, _chk_out = _health_gh_fetch(
            ["pr", "checks", str(matching_pr), "--json", "name,state"],
            ttl=30.0, timeout=5.0,
        )
        if _chk_rc != 0 or not _chk_out.strip():
            return "unavailable", (
                f"gh pr checks {matching_pr} timeout or error"
            )
        checks = _json.loads(_chk_out)
    except Exception as exc:
        return "unavailable", f"gh pr checks exception: {exc}"

    # Step 5: Find the `ci` check and map its state.
    ci_entry = next(
        (c for c in checks if (c.get("name") or "").lower() == "ci"),
        None,
    )
    if ci_entry is None:
        return "unavailable", (
            f"no 'ci' check found for PR #{matching_pr} "
            f"(checks present: {[c.get('name') for c in checks]})"
        )

    state = (ci_entry.get("state") or "").upper()
    if state in ("SUCCESS", "PASS"):
        return "pass", f"GitHub ci=pass (PR #{matching_pr})"
    if state in ("FAILURE", "FAILED", "ERROR", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED"):
        return "fail", f"GitHub ci={state.lower()} (PR #{matching_pr})"
    if state in ("PENDING", "IN_PROGRESS", "QUEUED", "WAITING"):
        return "pending", f"GitHub ci={state.lower()} (PR #{matching_pr})"
    # Unknown state — treat as unavailable to avoid false-green.
    return "unavailable", f"GitHub ci state unknown: {state!r} (PR #{matching_pr})"


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def _description_from_docstring(fn) -> str:
    """Return the first paragraph of fn's docstring (slice #966 / PRD #957).

    Replaces the former _parse_skill_rationale SKILL.md grep (regression #955:
    PRD #919 deleted the SKILL.md files that grep relied on, causing every
    DOCS-*/AS-* row to show "rationale unavailable — see SKILL.md").

    Returns the first non-blank paragraph of fn.__doc__, collapsing internal
    newlines to spaces.  Returns "" for None or undocumented functions.
    """
    if fn is None:
        return ""
    doc = fn.__doc__
    if not doc:
        return getattr(fn, "__name__", "")
    lines = doc.expandtabs().splitlines()
    # Strip leading blank lines
    while lines and not lines[0].strip():
        lines.pop(0)
    # Collect until first blank line
    para_lines: list = []
    for line in lines:
        if not line.strip():
            break
        para_lines.append(line.strip())
    para = " ".join(para_lines).strip()
    return para if para else getattr(fn, "__name__", "")


def _read_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def _tracked_files(root: Path, pathspec: str) -> "list[Path] | None":
    """Return a list of Path objects for files tracked by git under root/pathspec.

    Runs ``git -C <root> ls-files <pathspec>`` and converts each output line to an
    absolute Path.

    Return values:
    - list (possibly empty) — git is available and the repo is valid; the list
      contains exactly the tracked files matching pathspec (empty list = no tracked
      files match, NOT a git failure).
    - None — git is unavailable, the directory is not a git repo, or the command
      failed; callers should fall back to the legacy fs-glob path.

    This is the enumeration primitive for issue #926: all filesystem-reading health
    checks that enumerate candidates via Path.glob / rglob must use this helper so
    untracked / stale on-disk files are ignored.  File *contents* are still read from
    disk (the working-tree content of a tracked file matches the commit for CI
    purposes); only the ENUMERATION is gated on the committed tree.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "ls-files", pathspec],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            # git failed (e.g. not a git repo) — signal fallback with None
            return None
        paths = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if line:
                paths.append(root / line)
        return paths
    except Exception:
        # git not installed or subprocess error — signal fallback with None
        return None


def _grep_count(pattern: str, text: str, flags=re.MULTILINE) -> int:
    return len(re.findall(pattern, text, flags))


def _grep_fixed(literal: str, text: str) -> bool:
    return literal in text


# ---------------------------------------------------------------------------
# DOCS checks
# ---------------------------------------------------------------------------

def check_docs1_adr_index_forward() -> dict:
    """DOCS-1: every link in decisions/README.md resolves to an existing file."""
    readme = _HEALTH_REPO_ROOT / "decisions" / "README.md"
    if not readme.exists():
        return {"id": "DOCS-1", "result": "FAIL", "detail": "decisions/README.md missing"}
    text = _read_file(readme)
    refs = re.findall(r'\(?([0-9]{4}-[a-z0-9-]+\.md)\)?', text)
    missing = []
    for ref in set(refs):
        if not (_HEALTH_REPO_ROOT / "decisions" / ref).exists():
            missing.append(ref)
    if missing:
        return {"id": "DOCS-1", "result": "FAIL", "detail": f"Dangling refs: {missing}"}
    return {"id": "DOCS-1", "result": "PASS", "detail": ""}


def check_docs2_adr_index_reverse() -> dict:
    """DOCS-2: every decisions/NNNN-*.md is in decisions/README.md."""
    readme = _HEALTH_REPO_ROOT / "decisions" / "README.md"
    if not readme.exists():
        return {"id": "DOCS-2", "result": "FAIL", "detail": "decisions/README.md missing"}
    text = _read_file(readme)
    # Enumerate only git-tracked ADR files (issue #926: skip untracked on-disk files).
    tracked = _tracked_files(_HEALTH_REPO_ROOT, "decisions/[0-9]*.md")
    if tracked is None:
        # Fallback: git unavailable; use filesystem glob.
        tracked = sorted((_HEALTH_REPO_ROOT / "decisions").glob("[0-9]*.md"))
    missing = []
    for f in sorted(tracked):
        if f.name not in text:
            missing.append(f.name)
    if missing:
        return {"id": "DOCS-2", "result": "FAIL", "detail": f"Not indexed: {missing}"}
    return {"id": "DOCS-2", "result": "PASS", "detail": ""}


def check_docs3_claude_md_agents() -> dict:
    """DOCS-3: every .claude/agents/*.md ref in CLAUDE.md Map exists."""
    claude_md = _HEALTH_REPO_ROOT / "CLAUDE.md"
    if not claude_md.exists():
        return {"id": "DOCS-3", "result": "FAIL", "detail": "CLAUDE.md missing"}
    text = _read_file(claude_md)
    refs = re.findall(r'\.claude/agents/([a-z-]+\.md)', text)
    missing = []
    for ref in set(refs):
        if not (_HEALTH_REPO_ROOT / ".claude" / "agents" / ref).exists():
            missing.append(ref)
    if missing:
        return {"id": "DOCS-3", "result": "FAIL", "detail": f"Missing agents: {missing}"}
    return {"id": "DOCS-3", "result": "PASS", "detail": ""}


def check_docs4_claude_md_skills() -> dict:
    """DOCS-4: every .claude/skills/*/SKILL.md ref in CLAUDE.md Map exists."""
    claude_md = _HEALTH_REPO_ROOT / "CLAUDE.md"
    if not claude_md.exists():
        return {"id": "DOCS-4", "result": "FAIL", "detail": "CLAUDE.md missing"}
    text = _read_file(claude_md)
    refs = re.findall(r'\.claude/skills/([a-z-]+)/SKILL\.md', text)
    missing = []
    for ref in set(refs):
        if not (_HEALTH_REPO_ROOT / ".claude" / "skills" / ref / "SKILL.md").exists():
            missing.append(ref)
    if missing:
        return {"id": "DOCS-4", "result": "FAIL", "detail": f"Missing skills: {missing}"}
    return {"id": "DOCS-4", "result": "PASS", "detail": ""}


def check_docs5_n3_literal() -> dict:
    """DOCS-5: no bare N=3 in README.md without adjacent ADR-0013."""
    readme = _HEALTH_REPO_ROOT / "README.md"
    if not readme.exists():
        return {"id": "DOCS-5", "result": "PASS", "detail": "README.md missing (skip)"}
    lines = _read_file(readme).splitlines()
    offenders = []
    for i, line in enumerate(lines):
        if "N=3" in line:
            ctx_start = max(0, i - 2)
            ctx_end = min(len(lines), i + 3)
            ctx = "\n".join(lines[ctx_start:ctx_end])
            if "ADR-0013" not in ctx:
                offenders.append(f"L{i+1}: {line.strip()}")
    if offenders:
        return {"id": "DOCS-5", "result": "FAIL", "detail": f"Bare N=3: {offenders}"}
    return {"id": "DOCS-5", "result": "PASS", "detail": ""}


def check_docs6_glossary_md_refs() -> dict:
    """DOCS-6: no GLOSSARY.md refs outside the 2-file allowlist + decisions/.

    codebase-critic.md is allowlisted because its absorbed DOCS-6 check heading
    text ("### DOCS-6 — no `GLOSSARY.md` references...") contains "GLOSSARY.md"
    as documentation, not as an active reference. audit-meta/SKILL.md was
    formerly allowlisted for the same reason; it was deleted by PRD #919 slice #920.
    """
    allowlist = {
        ".claude/skills/grill-me/SKILL.md",
        ".claude/agents/codebase-critic.md",
    }
    offenders = []
    # Enumerate only git-tracked md files (issue #926: skip untracked on-disk files).
    tracked_md = _tracked_files(_HEALTH_REPO_ROOT, "*.md")
    if tracked_md is None:
        # Fallback: git unavailable — use filesystem rglob.
        tracked_md = list(_HEALTH_REPO_ROOT.rglob("*.md"))
    for md_file in tracked_md:
        rel = str(md_file.relative_to(_HEALTH_REPO_ROOT)).replace("\\", "/")
        # Skip .git, worktrees, tool-results, decisions/, .claude/logs/
        if any(skip in rel for skip in [".git/", "worktrees/", "tool-results/", "decisions/", ".claude/logs/"]):
            continue
        if rel in allowlist:
            continue
        try:
            if "GLOSSARY.md" in md_file.read_text(encoding="utf-8", errors="replace"):
                offenders.append(rel)
        except Exception:
            pass
    if offenders:
        return {"id": "DOCS-6", "result": "FAIL", "detail": f"GLOSSARY.md refs: {offenders}"}
    return {"id": "DOCS-6", "result": "PASS", "detail": ""}


def check_docs7_adr_citations() -> dict:
    """DOCS-7: every [ADR-NNNN](decisions/NNNN-*.md) citation resolves."""
    fake_slugs = re.compile(
        r'decisions/00\d{2}-(old-name|fictional|fictional-adr|new-adr|new-decision)\.md'
    )
    offenders = []
    # Enumerate only git-tracked md files (issue #926: skip untracked on-disk files).
    tracked_md = _tracked_files(_HEALTH_REPO_ROOT, "*.md")
    if tracked_md is None:
        # Fallback: git unavailable — use filesystem rglob.
        tracked_md = list(_HEALTH_REPO_ROOT.rglob("*.md"))
    for md_file in tracked_md:
        rel = str(md_file.relative_to(_HEALTH_REPO_ROOT)).replace("\\", "/")
        if ".git/" in rel or "worktrees/" in rel or ".claude/logs/" in rel:
            continue
        try:
            text = md_file.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for target in re.findall(r'decisions/[0-9]{4}-[a-z0-9-]+\.md', text):
            if fake_slugs.match(target):
                continue
            if not (_HEALTH_REPO_ROOT / target).exists():
                offenders.append(f"{rel} -> {target}")
    if offenders:
        return {"id": "DOCS-7", "result": "FAIL", "detail": f"Dangling ADR citations: {offenders[:5]}"}
    return {"id": "DOCS-7", "result": "PASS", "detail": ""}


def check_docs8_supersession_notes() -> dict:
    """DOCS-8 (WARN): decisions/README.md Status column has superseded-by annotations (per-pair)."""
    readme = _HEALTH_REPO_ROOT / "decisions" / "README.md"
    if not readme.exists():
        return {"id": "DOCS-8", "result": "WARN", "detail": "decisions/README.md missing"}
    readme_lines = _read_file(readme).splitlines()
    missing_annotations = []
    # Enumerate only git-tracked ADR files (issue #926: skip untracked on-disk files).
    adr_files = _tracked_files(_HEALTH_REPO_ROOT, "decisions/[0-9]*.md")
    if adr_files is None:
        # Fallback: git unavailable; use filesystem glob.
        adr_files = sorted((_HEALTH_REPO_ROOT / "decisions").glob("[0-9]*.md"))
    for adr_file in sorted(adr_files):
        try:
            adr_text = _read_file(adr_file)
            for match in re.finditer(r'^- \*\*Supersedes:\*\*\s*(.+)$', adr_text, re.MULTILINE):
                superseded_ref = match.group(1).strip()
                # Skip "Supersedes: none" — explicitly declares no supersession
                if re.match(r'none\.?\s', superseded_ref, re.IGNORECASE) or \
                        superseded_ref.lower().startswith('none'):
                    continue
                # Strip negated-prose clauses ("Does NOT supersede X") before
                # extracting IDs so negation doesn't produce false positives.
                # Example: "... Does NOT supersede ADR-0001 or ADR-0002 (frozen)"
                pos = re.search(r'\bdoes\s+not\s+supersede\b', superseded_ref, re.IGNORECASE)
                if pos:
                    superseded_ref = superseded_ref[:pos.start()]
                superseded_ids = re.findall(r'ADR-(\d{4})', superseded_ref)
                if not superseded_ids:
                    superseded_ids = re.findall(r'\b(\d{4})\b', superseded_ref)
                for sid in superseded_ids:
                    row_found = False
                    row_has_annotation = False
                    for line in readme_lines:
                        # Match the CANONICAL row for this ADR: starts with "| [NNNN]"
                        # not just any row that links to ADR-NNNN (which would false-positive
                        # on other ADRs that reference the superseded ADR in their annotations).
                        if line.startswith("|") and re.match(
                            r'^\|\s*\[' + re.escape(sid) + r'\]', line
                        ):
                            row_found = True
                            if "superseded by" in line.lower():
                                row_has_annotation = True
                            break
                    if row_found and not row_has_annotation:
                        missing_annotations.append(
                            f"{adr_file.name} supersedes ADR-{sid}: missing 'superseded by' in README row"
                        )
        except Exception:
            pass
    if missing_annotations:
        return {"id": "DOCS-8", "result": "WARN", "detail": f"Missing annotations: {missing_annotations[:3]}"}
    return {"id": "DOCS-8", "result": "PASS", "detail": ""}


def check_docs9_glossary_cap() -> dict:
    """DOCS-9 (WARN): CLAUDE.md glossary entry count <= 35."""
    claude_md = _HEALTH_REPO_ROOT / "CLAUDE.md"
    if not claude_md.exists():
        return {"id": "DOCS-9", "result": "WARN", "detail": "CLAUDE.md missing"}
    text = _read_file(claude_md)
    lines = text.splitlines()
    in_glossary = False
    count = 0
    for line in lines:
        # Match the actual H3 heading: "### Glossary (key terms)"
        if re.match(r'^### Glossary', line):
            in_glossary = True
            continue
        if in_glossary and re.match(r'^### ', line):
            break
        if in_glossary and re.match(r'^- \*\*', line):
            count += 1
    if count > 35:
        return {"id": "DOCS-9", "result": "WARN", "detail": f"Glossary has {count} entries (cap 35)"}
    return {"id": "DOCS-9", "result": "PASS", "detail": f"{count} entries"}


def check_docs10_backlog_surfacing() -> dict:
    """DOCS-10: no backlog-label surfacing idiom in agents/skills (except allowlist).

    codebase-critic.md is allowlisted because its absorbed DOCS-10 check heading
    text ("### DOCS-10 — no `backlog`-labeled prose...") contains the pattern as
    documentation of the check, not as an instruction to skip the backlog-critic
    gate. audit-meta/SKILL.md was formerly allowlisted for the same reason; it
    was deleted by PRD #919 slice #920. audit-subagents/SKILL.md was similarly
    allowlisted for AS-ALL-4 check text; it was deleted by PRD #919 slice #921.
    """
    allowlist = {"backlog-critic.md", "promote-to-backlog",
                 "codebase-critic.md"}
    pattern = re.compile(r'(`backlog`-labeled|--label backlog)')
    offenders = []
    # Enumerate only git-tracked agent/skill files (issue #926: skip untracked decoys).
    for pathspec in [".claude/agents/*.md", ".claude/skills/**/*.md"]:
        tracked = _tracked_files(_HEALTH_REPO_ROOT, pathspec)
        if tracked is None:
            # Fallback: git unavailable — use filesystem rglob for the corresponding dir.
            parts = pathspec.split("/")
            base = _HEALTH_REPO_ROOT / parts[0] / parts[1]
            tracked = list(base.rglob("*.md")) if base.is_dir() else []
        for md_file in tracked:
            rel = str(md_file.relative_to(_HEALTH_REPO_ROOT)).replace("\\", "/")
            if any(skip in rel for skip in allowlist):
                continue
            try:
                text = md_file.read_text(encoding="utf-8", errors="replace")
                if pattern.search(text):
                    offenders.append(rel)
            except Exception:
                pass
    if offenders:
        return {"id": "DOCS-10", "result": "FAIL", "detail": f"Backlog-label drift: {offenders}"}
    return {"id": "DOCS-10", "result": "PASS", "detail": ""}


# ---------------------------------------------------------------------------
# DOCS-11 — dead-citation check (ADR-0064 D2)
# ---------------------------------------------------------------------------

# Seeded allowlist: frozenset of (relative_path_posix, adr_number_str) pairs
# that are intentional/historical citations; the superseder need not appear
# on the same line.  Each entry is documented below with a one-line reason.
_DOCS11_ALLOWLIST: frozenset = frozenset({
    # prd-critic.md: "per ADR-0031 D10" appears inside a rubric example string
    # demonstrating how a PRD author would cite an ADR in a non-goal entry.
    # This is illustrative/historical text, not live authority governing behavior.
    (".claude/agents/prd-critic.md", "0031"),
    # slicer-critic.md: "ADR-0031 — T3 thin-prompt migration" in the References
    # section is a historical migration provenance note.  slicer-critic is owned
    # by slices 3–5 of PRD #794; edits are deferred to avoid cross-slice conflicts.
    (".claude/agents/slicer-critic.md", "0031"),
})


def _fully_superseded_adrs() -> dict:
    """Return {adr_number_str: superseding_adr_str} for fully-superseded ADRs.

    Parses decisions/README.md index table rows.  An ADR qualifies when its
    Status column contains "superseded entirely" or "Superseded in full"
    (case-insensitive), indicating the entire ADR (all Decisions) is superseded.
    Returns an empty dict if the file is missing or unparseable.
    """
    readme = _HEALTH_REPO_ROOT / "decisions" / "README.md"
    result: dict = {}
    try:
        text = readme.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return result
    row_pat = re.compile(
        r'^\|\s*\[?(\d{4})\]?[^|]*\|[^|]+\|\s*(.*?)\s*\|?\s*$',
        re.MULTILINE,
    )
    superseded_pat = re.compile(
        r'(?:superseded entirely|Superseded in full)\s+by\s+\[?ADR-(0\d{3})\]?',
        re.IGNORECASE,
    )
    for m in row_pat.finditer(text):
        adr_num = m.group(1)
        status = m.group(2)
        sm = superseded_pat.search(status)
        if sm:
            result[adr_num] = sm.group(1)  # superseding ADR number (4-digit str)
    return result


_SUPERSEDED_ADRS: dict = _fully_superseded_adrs()


def check_docs11_dead_citations() -> dict:
    """DOCS-11: no dead citations of fully-superseded ADRs in .claude/ runtime prompts.

    Implements ADR-0064 D2.

    Scans .claude/agents/*.md, .claude/skills/*/SKILL.md, and .claude/settings.json
    for citations of ADR numbers that are fully superseded in decisions/README.md
    (status contains "superseded entirely" or "Superseded in full"), unless:
      (a) the citing line also names the superseding ADR, OR
      (b) the (file, adr_number) pair appears in _DOCS11_ALLOWLIST.

    Reports offenders as "file:line cites ADR-NNNN (superseded by ADR-MMMM)".
    PASS when offender list is empty.
    """
    if not _SUPERSEDED_ADRS:
        return {
            "id": "DOCS-11",
            "result": "WARN",
            "detail": "could not parse superseded ADRs from decisions/README.md",
        }

    offenders = []
    settings_file = _HEALTH_REPO_ROOT / ".claude" / "settings.json"
    # Enumerate only git-tracked agent/skill files (issue #926: skip untracked decoys).
    files_to_scan: list = []
    for pathspec in [".claude/agents/*.md", ".claude/skills/**/*.md"]:
        tracked = _tracked_files(_HEALTH_REPO_ROOT, pathspec)
        if tracked is None:
            # Fallback: git unavailable — use filesystem rglob.
            parts = pathspec.split("/")
            base = _HEALTH_REPO_ROOT / parts[0] / parts[1]
            tracked = sorted(base.rglob("*.md")) if base.is_dir() else []
        files_to_scan.extend(sorted(tracked))
    if settings_file.exists():
        files_to_scan.append(settings_file)

    for file_path in files_to_scan:
        rel = str(file_path.relative_to(_HEALTH_REPO_ROOT)).replace("\\", "/")
        try:
            text = file_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            all_adrs_on_line = set(re.findall(r'ADR-(\d{4})', line))
            for dead_num, superseder_num in _SUPERSEDED_ADRS.items():
                if dead_num not in all_adrs_on_line:
                    continue
                if (rel, dead_num) in _DOCS11_ALLOWLIST:
                    continue
                if superseder_num in all_adrs_on_line:
                    continue
                offenders.append(
                    f"{rel}:{lineno} cites ADR-{dead_num} "
                    f"(superseded by ADR-{superseder_num})"
                )

    if offenders:
        return {
            "id": "DOCS-11",
            "result": "FAIL",
            "detail": f"{len(offenders)} dead citation(s): {offenders[:5]}",
            "offenders": offenders,
        }
    return {
        "id": "DOCS-11",
        "result": "PASS",
        "detail": (
            f"no dead citations; "
            f"{len(_SUPERSEDED_ADRS)} fully-superseded ADRs checked"
        ),
    }


# ---------------------------------------------------------------------------
# R-SENSITIVE-DETECTOR + META-TRIPWIRE (ADR-0070 D4 / slice #840)
#
# ADR-0070 D4 supersedes ADR-0064 D4: the human tripwire moves from per-PR
# enforcement-path ack (blocking the autonomous develop flow) to per-promotion
# guardrail-machinery ack (blocking only main advancement for self-modifying
# batches).  R-SENSITIVE-DETECTOR is repurposed to count guardrail-touching
# promotions and their ack status.  META-TRIPWIRE is the new blocking check
# wired into RELEASE-READY condition (f).
# ---------------------------------------------------------------------------

# Guardrail-machinery path set per ADR-0070 D4 (superset of ADR-0064 D4):
#   ADR-0064 D4 enforcement paths +
#   .claude/agents/*-critic.md +
#   release-gate definition (dashboard/health.py RELEASE-READY check + promote.sh) +
#   branch-protection tooling
_GUARDRAIL_PATHS: tuple = (
    # ADR-0064 D4 enforcement layer
    ".github/workflows/",
    ".claude/settings.json",
    ".claude/hooks/",
    "tools/ci-checks.sh",
    ".githooks/",
    # Critic agent prompts
    ".claude/agents/reviewer.md",
    ".claude/agents/prd-critic.md",
    ".claude/agents/adr-critic.md",
    ".claude/agents/slicer-critic.md",
    ".claude/agents/backlog-critic.md",
    ".claude/agents/codebase-critic.md",
    # Release-gate definition (the check + promotion tooling)
    "dashboard/health.py",
    "tools/promote.sh",
)

# ACK signal: label name or body keyword on a promotion record.
_PROMOTION_ACK_LABEL = "promotion-ack"

# Bootstrap cutoff: slice #840 is the implementing merge.
_META_TRIPWIRE_BOOTSTRAP_PROMOTION = 0  # day-one: all promotions post-implementation


def _is_guardrail_path(path: str) -> bool:
    """Return True if the given file path is in the guardrail-machinery set."""
    for gp in _GUARDRAIL_PATHS:
        if gp.endswith("/"):
            if path.startswith(gp):
                return True
        else:
            if path == gp:
                return True
    # Critic-md pattern: .claude/agents/*-critic.md (any critic name)
    if re.match(r'^\.claude/agents/[^/]+-critic\.md$', path):
        return True
    return False


def _read_promotion_events() -> list[dict]:
    """Read promotion events from workflow-events.jsonl.

    Returns a list of dicts with at least {"sha": str, "ts": str} keys,
    sorted chronologically (oldest first).  Returns [] if the file is absent
    or contains no promotion events.
    """
    events_log = _telemetry_log_root() / ".claude" / "logs" / "workflow-events.jsonl"
    if not events_log.exists():
        return []
    promotions = []
    try:
        for line in events_log.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                import json as _json
                evt = _json.loads(line)
            except Exception:
                continue
            if evt.get("event") == "promotion":
                promotions.append(evt)
    except Exception:
        pass
    return promotions


def _resolve_promote_ok_sentinel() -> Path:
    """Resolve the canonical PROMOTE_OK sentinel path (ADR-0070 D4 / #1124).

    Mirrors tools/promote.sh's LOGROOT resolution: the sentinel lives at
    <canonical-root>/.claude/PROMOTE_OK, where canonical-root is the
    git-common-dir parent (see _telemetry_log_root()) — the same shared
    root promote.sh uses so the sentinel is visible across worktrees.
    """
    return _telemetry_log_root() / ".claude" / "PROMOTE_OK"


def check_meta_tripwire() -> dict:
    """META-TRIPWIRE: guardrail-machinery promotion gate (ADR-0070 D4).

    A promotion batch is guardrail-touching if any commit since the last
    promotion modifies the guardrail-machinery set (ADR-0064 D4 enforcement
    paths + .claude/agents/*-critic.md + RELEASE-READY check + promote.sh +
    branch-protection config).

    A guardrail-touching batch PASSES this check if EITHER:
      (1) the .claude/PROMOTE_OK sentinel CURRENTLY exists at the canonical
          root — this is the current-batch ack: promote.sh's own step 0a
          already verified an operator created it for THIS promotion
          attempt (#1124 — the sentinel's presence at gate-check time is
          the truthful ack signal; promote.sh only removes it after the
          gate passes and the push completes); or
      (2) the last promotion record carries a promotion-ack (label/body
          keyword or "ack": true) — preserved as the retrospective
          historical-bypass detector.

    Otherwise it FAILs.

    Honest day-one: if no promotions have occurred yet, there is nothing to
    check — returns WARN (no-data) rather than spurious PASS or FAIL.

    Test injection: set env var _META_TRIPWIRE_RESULT_OVERRIDE to PASS|FAIL|WARN
    to bypass the real check (used by tests and RELEASE-READY injection tests).
    """
    # Test injection — honours _META_TRIPWIRE_RESULT_OVERRIDE env var.
    override = os.environ.get("_META_TRIPWIRE_RESULT_OVERRIDE", "").strip().upper()
    if override in {"PASS", "FAIL", "WARN"}:
        detail_map = {
            "PASS": (
                "meta-tripwire: PASS (injected via _META_TRIPWIRE_RESULT_OVERRIDE); "
                "guardrail-touching batch has promotion-ack or batch is clean"
            ),
            "FAIL": (
                "meta-tripwire: FAIL (injected via _META_TRIPWIRE_RESULT_OVERRIDE); "
                "guardrail-path change in unpromoted batch lacks promotion-ack"
            ),
            "WARN": (
                "meta-tripwire: WARN (injected via _META_TRIPWIRE_RESULT_OVERRIDE); "
                "no promotion data"
            ),
        }
        return {
            "id": "META-TRIPWIRE",
            "result": override,
            "detail": detail_map[override],
        }

    # Read promotion events to determine the unpromoted batch.
    promotions = _read_promotion_events()

    if not promotions:
        # Day-one: no promotions yet — nothing to tripwire.
        return {
            "id": "META-TRIPWIRE",
            "result": "WARN",
            "detail": (
                "meta-tripwire: no promotion events found in workflow-events.jsonl; "
                "honest day-one — guardrail check deferred until first promotion runs; "
                "ADR-0070 D4"
            ),
        }

    # Get the last promotion sha.
    last_promotion = promotions[-1]
    last_sha = last_promotion.get("sha", "")

    if not last_sha:
        return {
            "id": "META-TRIPWIRE",
            "result": "WARN",
            "detail": "meta-tripwire: last promotion event missing sha; cannot evaluate",
        }

    # Check if last promotion had a promotion-ack.
    last_ack = (
        _PROMOTION_ACK_LABEL in last_promotion.get("labels", [])
        or _PROMOTION_ACK_LABEL in (last_promotion.get("body", "") or "")
        or last_promotion.get("ack") is True
    )

    # Find commits in unpromoted batch (develop HEAD .. last_sha is the promoted range).
    # Unpromoted = commits since last promotion.
    try:
        result = subprocess.run(
            ["git", "log", "--name-only", "--format=COMMIT:%H",
             f"{last_sha}..HEAD"],
            capture_output=True, text=True, timeout=15,
            cwd=str(_HEALTH_REPO_ROOT),
        )
        if result.returncode != 0:
            return {
                "id": "META-TRIPWIRE",
                "result": "WARN",
                "detail": (
                    f"meta-tripwire: git log failed (exit={result.returncode}); "
                    f"cannot evaluate unpromoted batch"
                ),
            }
        batch_output = result.stdout
    except Exception as exc:
        return {
            "id": "META-TRIPWIRE",
            "result": "WARN",
            "detail": f"meta-tripwire: git log error: {exc}",
        }

    # Parse commit/file list from git log --name-only output.
    guardrail_files_found: list[str] = []
    for line in batch_output.splitlines():
        line = line.strip()
        if not line or line.startswith("COMMIT:"):
            continue
        if _is_guardrail_path(line):
            guardrail_files_found.append(line)

    if not guardrail_files_found:
        return {
            "id": "META-TRIPWIRE",
            "result": "PASS",
            "detail": (
                f"meta-tripwire: PASS — unpromoted batch since {last_sha[:8]} "
                f"touches no guardrail-machinery paths (ADR-0070 D4)"
            ),
        }

    # Guardrail paths found in batch — check for a current sentinel or a
    # retrospective ack.
    unique_guardrail = sorted(set(guardrail_files_found))[:5]

    # (1) Current-batch ack: the PROMOTE_OK sentinel exists RIGHT NOW at the
    # canonical root.  This is checked first because it's the only signal
    # that can ever be true for the batch actually in flight (#1124 —
    # promote.sh's writer only records the retrospective ack AFTER a
    # promotion completes, so the very first guardrail batch after any
    # promotion has no retrospective ack yet; the sentinel is the truthful
    # current-batch signal).
    sentinel_path = _resolve_promote_ok_sentinel()
    if sentinel_path.exists():
        return {
            "id": "META-TRIPWIRE",
            "result": "PASS",
            "detail": (
                f"meta-tripwire: PASS — guardrail-touching batch acked by present "
                f"PROMOTE_OK sentinel ({sentinel_path}); files: {unique_guardrail}; "
                f"ADR-0070 D4 / #1124"
            ),
        }

    # (2) Retrospective ack: the last promotion record itself carries an ack
    # marker — preserved as historical-bypass detection.
    if last_ack:
        return {
            "id": "META-TRIPWIRE",
            "result": "PASS",
            "detail": (
                f"meta-tripwire: PASS — guardrail-touching batch has promotion-ack; "
                f"files: {unique_guardrail}; ADR-0070 D4"
            ),
        }

    # Guardrail paths touched + no sentinel + no ack → FAIL.
    return {
        "id": "META-TRIPWIRE",
        "result": "FAIL",
        "detail": (
            f"meta-tripwire: FAIL — unpromoted batch touches guardrail-machinery "
            f"path(s) without promotion-ack; add 'promotion-ack' label or keyword "
            f"to the promotion record before promoting; "
            f"guardrail files: {unique_guardrail}; ADR-0070 D4"
        ),
        "guardrail_files": unique_guardrail,
    }


def check_r_sensitive_detector() -> dict:
    """R-SENSITIVE-DETECTOR: guardrail-touching promotions + ack status (advisory).

    Repurposed per ADR-0070 D4 (slice #840): the old per-PR enforcement-path
    human-ack counter is retired.  This detector now counts guardrail-touching
    promotions (promotions whose batch modified the guardrail-machinery set per
    ADR-0070 D4) and their ack status.

    Honest day-one: 0 promotions → 0 guardrail-touching promotions.
    Always returns WARN — advisory only; not a blocking gate.
    The blocking check is META-TRIPWIRE (wired into RELEASE-READY condition (f)).
    """
    promotions = _read_promotion_events()

    if not promotions:
        return {
            "id": "R-SENSITIVE-DETECTOR",
            "result": "WARN",
            "detail": (
                "guardrail-touching promotions: 0 — no promotion events yet "
                "(honest day-one); advisory only; meta-tripwire is the blocking gate "
                "(ADR-0070 D4; ADR-0071 D3)"
            ),
            "guardrail_touching_count": 0,
            "acked_count": 0,
        }

    guardrail_touching: list[dict] = []
    for promo in promotions:
        sha = promo.get("sha", "")
        if not sha:
            continue
        # Check commits in this promotion batch.
        # For simplicity, we check if the promotion's sha itself was a guardrail
        # commit by looking at the diff from the prior sha.
        # Full per-promotion batch scan is expensive; approximate with the
        # event's recorded files if available, else mark as unknown.
        files_in_promo = promo.get("files", [])
        if files_in_promo:
            guardrail_files = [f for f in files_in_promo if _is_guardrail_path(f)]
            if guardrail_files:
                acked = (
                    _PROMOTION_ACK_LABEL in promo.get("labels", [])
                    or _PROMOTION_ACK_LABEL in (promo.get("body", "") or "")
                    or promo.get("ack") is True
                )
                guardrail_touching.append({
                    "sha": sha[:8],
                    "ts": promo.get("ts", ""),
                    "acked": acked,
                    "files": guardrail_files[:3],
                })

    gt_count = len(guardrail_touching)
    acked_count = sum(1 for p in guardrail_touching if p["acked"])

    detail = (
        f"guardrail-touching promotions: {gt_count} "
        f"({acked_count} with promotion-ack, {gt_count - acked_count} pending); "
        f"advisory only — meta-tripwire is the blocking gate "
        f"(ADR-0070 D4; ADR-0071 D3 — R-SENSITIVE per-PR rule retired)"
    )
    return {
        "id": "R-SENSITIVE-DETECTOR",
        "result": "WARN",
        "detail": detail,
        "guardrail_touching_count": gt_count,
        "acked_count": acked_count,
    }


# ---------------------------------------------------------------------------
# AS-* checks
# ---------------------------------------------------------------------------

def _check_as_all_1(path: Path) -> dict:
    """AS-ALL-1: frontmatter name/description/tools/model."""
    text = _read_file(path)
    count = len(re.findall(r'^(name|description|tools|model):', text, re.MULTILINE))
    result = "PASS" if count >= 4 else "FAIL"
    return {"id": "AS-ALL-1", "result": result, "detail": f"field count={count}"}


def _check_as_all_2(path: Path) -> dict:
    """AS-ALL-2: Tool boundaries section heading."""
    text = _read_file(path)
    ok = bool(re.search(r'^#+\s*Tool boundaries', text, re.MULTILINE))
    return {"id": "AS-ALL-2", "result": "PASS" if ok else "FAIL", "detail": ""}


def _check_as_all_3(path: Path) -> dict:
    """AS-ALL-3: cross-reference section heading."""
    text = _read_file(path)
    ok = bool(re.search(r'^#+\s*.*(References|Related|See also|Cross-refs)', text,
                        re.MULTILINE | re.IGNORECASE))
    return {"id": "AS-ALL-3", "result": "PASS" if ok else "FAIL", "detail": ""}


def _check_as_all_4(path: Path) -> dict:
    """AS-ALL-4: no backlog-label surfacing idiom (backlog-critic + codebase-critic excluded).

    codebase-critic.md is excluded (PRD #919 slice #921 / CI promotion) for the
    same reason as in DOCS-10: its "### DOCS-10 —" heading text documents the check
    pattern as metadata, not as an instruction to bypass the backlog-critic gate.
    """
    if path.name in ("backlog-critic.md", "codebase-critic.md"):
        return {"id": "AS-ALL-4", "result": "N/A", "detail": "excluded"}
    text = _read_file(path)
    has_drift = bool(re.search(r'(`backlog`-labeled|--label backlog)', text))
    return {"id": "AS-ALL-4", "result": "FAIL" if has_drift else "PASS", "detail": ""}


def _check_as_all_5(path: Path) -> dict:
    """AS-ALL-5: Mandatory reading order OR When invoked section."""
    text = _read_file(path)
    ok = bool(re.search(r'^#+\s*(Mandatory reading order|When invoked)', text, re.MULTILINE))
    return {"id": "AS-ALL-5", "result": "PASS" if ok else "FAIL", "detail": ""}


def _check_as_crit_1(path: Path) -> dict:
    """AS-CRIT-1: Default conservative literal."""
    ok = "Default conservative" in _read_file(path)
    return {"id": "AS-CRIT-1", "result": "PASS" if ok else "FAIL", "detail": ""}


def _check_as_crit_2(path: Path) -> dict:
    """AS-CRIT-2: paranoid OR Adversarial.*mindset (backlog-critic excluded).

    Pattern broadened (PRD #919 slice #921 / CI promotion): accepts
    "Adversarial mindset", "Adversarial-SRE mindset", etc. — any form
    of "Adversarial" followed within 30 chars by "mindset" — alongside
    the original "paranoid" literal.
    """
    if path.name == "backlog-critic.md":
        return {"id": "AS-CRIT-2", "result": "N/A", "detail": "excluded"}
    text = _read_file(path)
    ok = bool(re.search(r'(paranoid|Adversarial.{0,30}mindset)', text))
    return {"id": "AS-CRIT-2", "result": "PASS" if ok else "FAIL", "detail": ""}


def _check_as_crit_3(path: Path) -> dict:
    """AS-CRIT-3: VERDICT, REASON, ROUND documented in critic body.

    Aligned with tools/ci-checks.sh CHECK 10: bare-string grep (no colon
    required) so fenced-block examples AND backtick-quoted key names both pass.
    backlog-critic omits ROUND by design (fires once; no multi-round loop) —
    its ROUND check is N/A, matching its documented Output format section.
    """
    text = _read_file(path)
    has_verdict = "VERDICT" in text
    has_reason = "REASON" in text
    # backlog-critic documents "ROUND: is omitted" by design — N/A for ROUND
    if path.name == "backlog-critic.md":
        ok = has_verdict and has_reason
        if ok:
            return {"id": "AS-CRIT-3", "result": "PASS",
                    "detail": "ROUND N/A (single-fire; no multi-round loop)"}
        missing = [k for k, v in [("VERDICT", has_verdict), ("REASON", has_reason)] if not v]
        return {"id": "AS-CRIT-3", "result": "FAIL",
                "detail": f"missing: {', '.join(missing)}"}
    has_round = "ROUND" in text
    ok = has_verdict and has_reason and has_round
    missing = [k for k, v in [("VERDICT", has_verdict), ("REASON", has_reason),
                               ("ROUND", has_round)] if not v]
    return {"id": "AS-CRIT-3", "result": "PASS" if ok else "FAIL",
            "detail": "" if ok else f"missing: {', '.join(missing)}"}


def _check_as_crit_4(path: Path) -> dict:
    """AS-CRIT-4: documentation-contract check.

    Verifies the critic documents its verdict-body output contract by delegation:
    (a) an Output-format section heading is present AND
    (b) an ADR-0005 citation is present.
    Both required; any absent → FAIL.
    """
    text = _read_file(path)
    has_output_format = bool(re.search(r'^#+\s*Output format', text, re.MULTILINE))
    has_adr0005 = "ADR-0005" in text
    ok = has_output_format and has_adr0005
    missing = []
    if not has_output_format:
        missing.append("Output format heading")
    if not has_adr0005:
        missing.append("ADR-0005 citation")
    return {"id": "AS-CRIT-4", "result": "PASS" if ok else "FAIL",
            "detail": "" if ok else f"missing: {', '.join(missing)}"}


def _check_as_gen_1(path: Path) -> dict:
    """AS-GEN-1: RESULT: REASON: ARTIFACTS: in generator body."""
    text = _read_file(path)
    ok = "RESULT:" in text and "REASON:" in text and "ARTIFACTS:" in text
    return {"id": "AS-GEN-1", "result": "PASS" if ok else "FAIL", "detail": ""}


def _is_critic(stem: str, path: Path) -> bool:
    return stem in _KNOWN_CRITICS or stem == "reviewer" or stem.endswith("-critic")


def _enrich_checks(checks: list) -> list:
    """Add description field to each check dict from its function docstring (slice #966).

    Replaces the former SKILL.md-grep approach (_parse_skill_rationale, slice #629)
    which broke when PRD #919 deleted the SKILL.md files (regression #955).
    Now uses CHECK_REGISTRY lookup + _description_from_docstring to source the
    description directly from the check function's __doc__ first paragraph.

    Mutates each dict in-place and returns the list for convenience.
    """
    for c in checks:
        check_id = c.get("id", "")
        fn = CHECK_REGISTRY.get(check_id) if check_id else None
        c["description"] = _description_from_docstring(fn) or check_id
    return checks


def _attach_descriptions(checks: list) -> list:
    """Add description field to check dicts that don't already have one (slice #966).

    Used for non-DOCS/AS groups (substrate, verification, registry, hygiene,
    promotion) where _enrich_checks is not called. Sources from CHECK_REGISTRY
    + _description_from_docstring. Falls back to the check id string.

    Mutates each dict in-place and returns the list for convenience.
    """
    for c in checks:
        check_id = c.get("id", "")
        fn = CHECK_REGISTRY.get(check_id) if check_id else None
        if "description" not in c:
            c["description"] = _description_from_docstring(fn) or check_id
    return checks


# ---------------------------------------------------------------------------
# data_state classifier (slice #967 / PRD #957 §2 #4)
#
# Every check result carries a data_state ∈ {"pass", "actionable", "no-data"}.
# "no-data" is assigned ONLY when the check's detail carries an explicit
# absent-source / day-one / no-events marker from _NO_DATA_MARKERS.
# A PASS check → "pass".  A non-PASS check that doesn't match any marker →
# "actionable" (honest default — never mask a genuine issue as no-data).
# ---------------------------------------------------------------------------

# Explicit absent-source / day-one / no-events substrings.
# These must match ONLY on details that signal a missing feed/log, not on
# real threshold-breach details (e.g. "Glossary has 36 entries" is actionable).
# When in doubt, leave out of this list → stays "actionable" (safe default).
_NO_DATA_MARKERS: tuple = (
    "not found",           # log/file absent (hook-fires.jsonl, workflow-events.jsonl, etc.)
    "log absent",          # explicit log-absent phrasing
    "no events in window", # CAPTURE-SLO: no events in rolling window
    "no sessions found",   # CAPTURE-SLO: no sessions in events log
    "no promotion events", # META-TRIPWIRE / R-SENSITIVE-DETECTOR day-one
    "0 — no promotion",    # R-SENSITIVE-DETECTOR day-one phrasing
    "no agent_start events",  # BLIND-RATE: pre-migration expected
    "pre-migration — expected",  # BLIND-RATE: migration not yet applied
    "never have fired",    # HOOK-LIVENESS: log may never have fired
    "no closed PRDs",      # CRITIC-HEALTH / trail: no historical data
    "no PRD-labeled issues",  # SPEC-COVERAGE: no PRDs yet
    "no attempt beacons",  # HOOK-INTEGRITY: no beacons in rolling window
    "day-one",             # any explicit day-one label in detail
    "honest day-one",      # META-TRIPWIRE: honest day-one — no promotions yet
    "capture not live",    # CAPTURE-SLO explicit absent phrase
    "gh API unavailable",  # SPEC-COVERAGE / RESIDUAL-RATIO: network absent
)


def _classify_data_state(result: str, detail: str) -> str:
    """Return data_state ∈ {'pass', 'actionable', 'no-data'} for a check result.

    Rules (slice #967 / PRD #957 §2 #4):
    - result == 'PASS' → 'pass' (no threshold breach)
    - result != 'PASS' AND detail contains an _NO_DATA_MARKERS substring → 'no-data'
    - result != 'PASS' otherwise → 'actionable' (genuine breach — never masked)

    Honesty guard: when ambiguous → 'actionable' is the safe default.
    """
    if result == "PASS":
        return "pass"
    lower_detail = (detail or "").lower()
    for marker in _NO_DATA_MARKERS:
        if marker.lower() in lower_detail:
            return "no-data"
    return "actionable"


def _attach_data_state(checks: list) -> list:
    """Add data_state field to each check dict (slice #967 / PRD #957 §2 #4).

    Mutates each dict in-place and returns the list for convenience.
    Applied after all result/detail fields are set.
    """
    for c in checks:
        if "data_state" not in c:
            c["data_state"] = _classify_data_state(
                c.get("result", ""), c.get("detail", "")
            )
    return checks


# Human-readable group labels for each check ID (slice #931 / PRD #927 §2 #10).
# Maps check ID → section group header shown in the Health tab.
# Groups: "Docs in sync" | "Rules enforced" | "Hooks live" | "No drift" |
#         "Verification integrity" | "Release gates" | "Session hygiene"
_CHECK_GROUP_MAP: dict = {
    # Docs in sync — audit-meta: ADR index, CLAUDE.md refs, glossary, citations
    "DOCS-1": "Docs in sync",
    "DOCS-2": "Docs in sync",
    "DOCS-3": "Docs in sync",
    "DOCS-4": "Docs in sync",
    "DOCS-5": "Docs in sync",
    "DOCS-6": "Docs in sync",
    "DOCS-7": "Docs in sync",
    "DOCS-8": "Docs in sync",
    "DOCS-9": "Docs in sync",
    "DOCS-10": "Docs in sync",
    "DOCS-11": "Docs in sync",
    # Rules enforced — rule-coverage, registry parity, critic performance
    "RULE-COVERAGE": "Rules enforced",
    "PARITY": "Rules enforced",
    "CRITIC-HEALTH": "Rules enforced",
    "SPEC-COVERAGE": "Rules enforced",
    # Hooks live — hook integrity + liveness
    "HOOK-INTEGRITY": "Hooks live",
    "HOOK-LIVENESS": "Hooks live",
    # No drift — isolation, capture-SLO, silent-drift, test health
    "CAPTURE-SLO": "No drift",
    "ISOLATION-GROUP": "No drift",
    "SILENT-DRIFT": "No drift",
    "STALE-BRANCHES": "No drift",
    "TESTS-COLLECTED": "No drift",
    "TEST-ORDERING": "No drift",
    "QUARANTINE-SLA": "No drift",
    # Verification integrity — proof-presence, merge-integrity, capture-shape
    "BLIND-RATE": "Verification integrity",
    "RESIDUAL-RATIO": "Verification integrity",
    "PROOF-PRESENCE": "Verification integrity",
    "PROOF-INTEGRITY": "Verification integrity",
    "MERGE-INTEGRITY": "Verification integrity",
    "CAPTURE-SHAPE": "Verification integrity",
    "GREEN-MAIN": "Verification integrity",
    "DRAIN-LEDGER": "Verification integrity",
    # Release gates — promotion topology, lag, release-readiness
    "BRANCH-TOPOLOGY": "Release gates",
    "PROMOTION-LAG": "Release gates",
    "RELEASE-READY": "Release gates",
    "R-SENSITIVE-DETECTOR": "Release gates",
    "META-TRIPWIRE": "Release gates",
    # Session hygiene — log rotation, untracked files, required labels
    "UNTRACKED-SIZE": "Session hygiene",
    "LOG-ROTATION": "Session hygiene",
    "REQUIRED-LABELS": "Session hygiene",
    "SESSION-INJECTION": "Session hygiene",
}


def _enrich_group(checks: list) -> list:
    """Add 'group' field to each check dict using _CHECK_GROUP_MAP (slice #931).

    Mutates each dict in-place and returns the list for convenience.
    Checks not in the map get group = "Other".
    """
    for c in checks:
        check_id = c.get("id", "")
        c["group"] = _CHECK_GROUP_MAP.get(check_id, "Other")
    return checks


# ---------------------------------------------------------------------------
# Purpose-group taxonomy (slice #968 / PRD #957 §2 #5)
#
# PURPOSE_GROUP_MAP: check ID → human-readable purpose heading used by the
# purpose-grouped Health tab view (≥5 headings per §2 #5 AC).
#
# Groups: "Docs in sync" | "Rules enforced" | "Telemetry live" |
#         "Verification integrity" | "Isolation/hygiene" | "Release gates"
#
# Registered-but-UI-invisible checks (explicitly excluded from this map):
#   BRANCH-TOPOLOGY    — registered but dormant (ADR-0071 D4); no surface value
#   FRONTMATTER-COVERAGE — registered but not returned by _build_health_data
#   META-TRIPWIRE      — sub-signal wired into RELEASE-READY gate; not surfaced
#   RELEASE-READY      — gate-only check; only surfaces via promotion panel
# These 4 remain accessible via `python dashboard/health.py --check <ID>`.
#
# AS-AUDIT and CRITIC-HEALTH have bespoke rendering; they are NOT in this map.
# ---------------------------------------------------------------------------
PURPOSE_GROUP_MAP: dict = {
    # --- Docs in sync ---
    # ADR index, CLAUDE.md refs, glossary, citation health
    "DOCS-1":  "Docs in sync",
    "DOCS-2":  "Docs in sync",
    "DOCS-3":  "Docs in sync",
    "DOCS-4":  "Docs in sync",
    "DOCS-5":  "Docs in sync",
    "DOCS-6":  "Docs in sync",
    "DOCS-7":  "Docs in sync",
    "DOCS-8":  "Docs in sync",
    "DOCS-9":  "Docs in sync",
    "DOCS-10": "Docs in sync",
    "DOCS-11": "Docs in sync",
    "SILENT-DRIFT": "Docs in sync",
    # --- Rules enforced ---
    # Rule coverage, registry parity, spec coverage
    "RULE-COVERAGE":      "Rules enforced",
    "PARITY":             "Rules enforced",
    "SPEC-COVERAGE":      "Rules enforced",
    "BLIND-RATE":         "Rules enforced",
    "TEST-ORDERING":      "Rules enforced",
    "QUARANTINE-SLA":     "Rules enforced",
    # --- Telemetry live ---
    # The three hook checks render as ONE composite "Telemetry live" row (§2 #6).
    # They are listed here so _attach_purpose_group marks them correctly, even
    # though the composite builder replaces them in the purpose-groups payload.
    "CAPTURE-SLO":    "Telemetry live",
    "HOOK-INTEGRITY": "Telemetry live",
    "HOOK-LIVENESS":  "Telemetry live",
    "STREAM-LIVENESS": "Telemetry live",
    # --- Verification integrity ---
    # Proof-presence, merge-integrity, capture-shape, green-main
    "PROOF-PRESENCE":  "Verification integrity",
    "PROOF-INTEGRITY": "Verification integrity",
    "MERGE-INTEGRITY": "Verification integrity",
    "CAPTURE-SHAPE":   "Verification integrity",
    "GREEN-MAIN":      "Verification integrity",
    "RECORD-VS-GH":    "Verification integrity",
    "RESIDUAL-RATIO":  "Verification integrity",
    "SLICE-VS-PR":              "Verification integrity",
    "MERGED-WITHOUT-VERDICT":   "Verification integrity",
    "CLOSED-PRD-VS-QA":         "Verification integrity",
    # --- Isolation/hygiene ---
    # Worktree orphans, log rotation, untracked files, session injection
    "ISOLATION-GROUP":   "Isolation/hygiene",
    "UNTRACKED-SIZE":    "Isolation/hygiene",
    "LOG-ROTATION":      "Isolation/hygiene",
    "REQUIRED-LABELS":   "Isolation/hygiene",
    "SESSION-INJECTION": "Isolation/hygiene",
    "STALE-BRANCHES":    "Isolation/hygiene",
    "TESTS-COLLECTED":   "Isolation/hygiene",
    "DEPLOY-HANDSHAKE":  "Isolation/hygiene",
    # --- Release gates ---
    # Promotion lag, R-SENSITIVE-DETECTOR advisory
    "PROMOTION-LAG":        "Release gates",
    "R-SENSITIVE-DETECTOR": "Release gates",
    # NOTE: BRANCH-TOPOLOGY, FRONTMATTER-COVERAGE, META-TRIPWIRE, RELEASE-READY
    # are intentionally NOT in this map (registered-but-UI-invisible; see above).
}

# The ordered list of purpose-group headings for consistent rendering order.
PURPOSE_GROUP_ORDER: list = [
    "Docs in sync",
    "Rules enforced",
    "Telemetry live",
    "Verification integrity",
    "Isolation/hygiene",
    "Release gates",
]


def _attach_purpose_group(checks: list) -> list:
    """Add 'purpose_group' field to each check dict using PURPOSE_GROUP_MAP.

    Mutates each dict in-place and returns the list for convenience.
    Checks not in the map get purpose_group = None (excluded from grouped view).
    Called by _build_health_data (slice #968 / PRD #957 §2 #5).
    """
    for c in checks:
        check_id = c.get("id", "")
        c["purpose_group"] = PURPOSE_GROUP_MAP.get(check_id)
    return checks


# ---------------------------------------------------------------------------
# what_to_do extractor (slice #968 / PRD #957 §2 #7)
# ---------------------------------------------------------------------------

def _what_to_do_from_docstring(fn) -> str:
    """Extract a 'what to do' action string from fn's docstring (slice #968).

    Strategy (in order):
    1. Look for a line starting with "WARN:" or "FAIL:" (semantics block).
    2. Look for a second non-blank paragraph.
    3. Return a short honest default: "Review the detail field above."

    Returns a string ≤ 120 chars.
    """
    if fn is None:
        return "Review the detail field above."
    doc = fn.__doc__
    if not doc:
        return "Review the detail field above."
    lines = doc.expandtabs().splitlines()

    # Strategy 1: find a "WARN:" or "FAIL:" line
    for line in lines:
        stripped = line.strip()
        if re.match(r'^(WARN|FAIL)\s*:', stripped, re.IGNORECASE):
            action = re.sub(r'^(WARN|FAIL)\s*:\s*', '', stripped, flags=re.IGNORECASE).strip()
            if action:
                return action[:120]

    # Strategy 2: extract the second non-blank paragraph
    paragraphs: list = []
    current: list = []
    for line in lines:
        if not line.strip():
            if current:
                paragraphs.append(" ".join(ln.strip() for ln in current if ln.strip()))
                current = []
        else:
            current.append(line)
    if current:
        paragraphs.append(" ".join(ln.strip() for ln in current if ln.strip()))

    if len(paragraphs) >= 2:
        second = paragraphs[1].strip()
        if second:
            second = re.sub(r'^(WARN|FAIL)\s*:\s*', '', second, flags=re.IGNORECASE).strip()
            return second[:120]

    return "Review the detail field above."


def _attach_what_to_do(checks: list) -> list:
    """Add 'what_to_do' field to ACTIONABLE check dicts (slice #968 / PRD #957 §2 #7).

    Only populated for checks with data_state == 'actionable' (genuine breaches).
    Pass/no-data checks get what_to_do = "" (empty — no action needed).
    Sources from CHECK_REGISTRY function docstring via _what_to_do_from_docstring.
    Mutates each dict in-place and returns the list for convenience.
    """
    for c in checks:
        if c.get("data_state") == "actionable":
            check_id = c.get("id", "")
            fn = CHECK_REGISTRY.get(check_id) if check_id else None
            c["what_to_do"] = _what_to_do_from_docstring(fn)
        else:
            c["what_to_do"] = ""
    return checks


# ---------------------------------------------------------------------------
# Hook-trio composite builder (slice #968 / PRD #957 §2 #6)
#
# CAPTURE-SLO + HOOK-INTEGRITY + HOOK-LIVENESS render as ONE "Telemetry live"
# composite row on the Health tab.  Rollup = worst actionable sub-signal;
# a no-data sub-signal does NOT downgrade the composite.
# ---------------------------------------------------------------------------

def _build_hook_trio_composite(
    capture_slo: dict,
    hook_integrity: dict,
    hook_liveness: dict,
) -> dict:
    """Build hook-trio composite check dict (slice #968 / PRD #957 §2 #6).

    Rollup = worst actionable sub-signal; no-data sub-signals do NOT downgrade.
    Sub-signals stored under 'sub_signals' key for drill-down in the UI.
    """
    sub_signals = [capture_slo, hook_integrity, hook_liveness]

    # Collect actionable sub-signal results (no-data excluded from rollup)
    actionable_results = [
        s["result"]
        for s in sub_signals
        if s.get("data_state") == "actionable"
    ]

    if "FAIL" in actionable_results:
        rollup = "FAIL"
    elif "WARN" in actionable_results:
        rollup = "WARN"
    elif actionable_results:
        rollup = "PASS"
    else:
        # All sub-signals are no-data or pass
        all_pass = all(s["result"] == "PASS" for s in sub_signals)
        rollup = "PASS" if all_pass else "WARN"

    actionable_ids = [s["id"] for s in sub_signals if s.get("data_state") == "actionable"]
    nodata_ids = [s["id"] for s in sub_signals if s.get("data_state") == "no-data"]
    detail_parts = []
    if actionable_ids:
        detail_parts.append(f"actionable: {', '.join(actionable_ids)}")
    if nodata_ids:
        detail_parts.append(f"no-data (excluded): {', '.join(nodata_ids)}")
    detail = "; ".join(detail_parts) or "all sub-signals pass or no-data"

    ds = "pass" if rollup == "PASS" else ("no-data" if not actionable_results else "actionable")
    what_to_do_str = (
        "Check sub-signals: CAPTURE-SLO (sessions with live events), "
        "HOOK-INTEGRITY (attempt-vs-ok ratio), HOOK-LIVENESS (beacon lag). "
        "See .claude/hooks/ and .claude/logs/hook-fires.jsonl."
    ) if rollup != "PASS" else ""
    return {
        "id": "TELEMETRY-LIVE",
        "result": rollup,
        "data_state": ds,
        "detail": detail,
        "description": (
            "Hook-trio composite: CAPTURE-SLO + HOOK-INTEGRITY + HOOK-LIVENESS. "
            "Rollup = worst actionable sub-signal; no-data excluded. PRD #957 §2 #6."
        ),
        "purpose_group": "Telemetry live",
        "what_to_do": what_to_do_str,
        "is_composite": True,
        "sub_signals": [
            {
                "id": s["id"],
                "result": s["result"],
                "data_state": s.get("data_state", ""),
                "detail": s.get("detail", ""),
            }
            for s in sub_signals
        ],
    }


def audit_subagents() -> dict:
    agents_dir = _HEALTH_REPO_ROOT / ".claude" / "agents"
    results = {}
    if not agents_dir.exists():
        return results
    # Enumerate only git-tracked agent files (issue #926: skip untracked decoys).
    tracked = _tracked_files(_HEALTH_REPO_ROOT, ".claude/agents/*.md")
    if tracked is None:
        # Fallback: git unavailable — use filesystem glob.
        tracked = sorted(agents_dir.glob("*.md"))
    for agent_md in sorted(tracked):
        stem = agent_md.stem
        is_crit = _is_critic(stem, agent_md)
        checks = [
            _check_as_all_1(agent_md),
            _check_as_all_2(agent_md),
            _check_as_all_3(agent_md),
            _check_as_all_4(agent_md),
            _check_as_all_5(agent_md),
        ]
        if is_crit:
            checks += [
                _check_as_crit_1(agent_md),
                _check_as_crit_2(agent_md),
                _check_as_crit_3(agent_md),
                _check_as_crit_4(agent_md),
            ]
        else:
            checks.append(_check_as_gen_1(agent_md))
        results[stem] = {
            "type": "critic" if is_crit else "generator",
            "checks": _enrich_checks(checks),
        }
    return results


def check_audit_subagents() -> dict:
    """AS-AUDIT: aggregate zero-arg wrapper over all AS-* subagent-prompt checks.

    Runs audit_subagents() across all .claude/agents/*.md files and aggregates
    per-file, per-check results into a single registry-compatible verdict:
      FAIL  — any individual check returned FAIL
      WARN  — any individual check returned WARN (and none FAIL)
      PASS  — all checks passed or N/A

    This function is the CHECK_REGISTRY entry for AS-AUDIT (PRD #919 slice #921):
    the audit-subagents skill was retired; its checks now run automatically in CI
    via `python3 dashboard/health.py --check AS-AUDIT`.

    Rationale: subagent prompts drift silently between slices. The AS-* checks
    (frontmatter, tool-boundaries, cross-ref section, surfacing convention,
    entry-protocol, CRITIC trailer, generator trailer, etc.) are the mechanical
    drift-detector per ADR-0011 D4. Running them in CI ensures every PR is
    checked, not just when the operator remembers to invoke a skill.
    """
    results = audit_subagents()
    fail_ids = []
    warn_ids = []
    for stem, entry in results.items():
        for c in entry.get("checks", []):
            r = c.get("result", "")
            cid = c.get("id", "")
            label = f"{stem}/{cid}"
            if r == "FAIL":
                fail_ids.append(label)
            elif r == "WARN":
                warn_ids.append(label)

    if fail_ids:
        detail = f"FAIL: {fail_ids}"
        if warn_ids:
            detail += f"; WARN: {warn_ids}"
        return {"id": "AS-AUDIT", "result": "FAIL", "detail": detail}
    if warn_ids:
        return {"id": "AS-AUDIT", "result": "WARN",
                "detail": f"WARN: {warn_ids}"}
    agent_count = len(results)
    return {"id": "AS-AUDIT", "result": "PASS",
            "detail": f"all checks passed across {agent_count} agent file(s)"}


def audit_meta() -> dict:
    checks = [
        check_docs1_adr_index_forward(),
        check_docs2_adr_index_reverse(),
        check_docs3_claude_md_agents(),
        check_docs4_claude_md_skills(),
        check_docs5_n3_literal(),
        check_docs6_glossary_md_refs(),
        check_docs7_adr_citations(),
        check_docs8_supersession_notes(),
        check_docs9_glossary_cap(),
        check_docs10_backlog_surfacing(),
        check_docs11_dead_citations(),
        check_r_sensitive_detector(),
    ]
    return {"checks": _enrich_checks(checks)}


# ---------------------------------------------------------------------------
# Substrate health checks (slice #767)
# ---------------------------------------------------------------------------

# Boundary-only event types that do NOT count as "live" capture (PRD #763 §2 cr.7)
_BOUNDARY_EVENTS = frozenset({"session_start", "session_stop"})

# Window size for capture SLO: last N sessions with any event in workflow-events.jsonl
_CAPTURE_SLO_WINDOW = 20

# ADR-0042: bootstrap cutoff for merged_without_ci — PRs merged before this PR are
# grandfathered (CI gate did not exist yet).  PR #711 was the first one under ADR-0042.
_CI_GATE_BOOTSTRAP_PR = 711


def check_capture_slo() -> dict:
    """CAPTURE-SLO: sessions with ≥1 non-boundary event / total, last N sessions.

    Reads workflow-events.jsonl (read-only).  Red when fewer than 50% of sessions
    in the last _CAPTURE_SLO_WINDOW have a non-boundary event (i.e. hooks are mostly dead).

    Returns per-session liveness detail.
    """
    events_log = _telemetry_log_root() / ".claude" / "logs" / "workflow-events.jsonl"
    if not events_log.exists():
        return {
            "id": "CAPTURE-SLO",
            "result": "WARN",
            "detail": "workflow-events.jsonl not found",
        }

    try:
        import json as _json
        sessions: dict[str, set] = {}  # session_id → set of non-boundary event types
        with events_log.open(encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    obj = _json.loads(raw)
                except Exception:
                    continue
                sid = obj.get("session_id", "")
                ev = obj.get("event", "")
                if not sid:
                    continue
                if sid not in sessions:
                    sessions[sid] = set()
                if ev and ev not in _BOUNDARY_EVENTS:
                    sessions[sid].add(ev)
    except Exception as exc:
        return {"id": "CAPTURE-SLO", "result": "WARN",
                "detail": f"read error: {exc}"}

    if not sessions:
        return {"id": "CAPTURE-SLO", "result": "WARN",
                "detail": "no sessions found in workflow-events.jsonl"}

    # Take the last N sessions (by insertion order in dict — Python 3.7+)
    window = list(sessions.items())[-_CAPTURE_SLO_WINDOW:]
    total = len(window)
    live_count = sum(1 for _sid, evs in window if evs)
    boundary_only = total - live_count
    ratio = live_count / total if total > 0 else 0.0

    # Per-session liveness summary (most recent first, capped at 10 for detail string)
    per_session_notes = []
    for sid, evs in reversed(window[-10:]):
        tag = "live" if evs else "boundary-only"
        per_session_notes.append(f"{sid[:8]}:{tag}")

    detail = (
        f"{live_count}/{total} live in last {_CAPTURE_SLO_WINDOW}-session window "
        f"(SLO {ratio*100:.0f}%) | "
        + ", ".join(per_session_notes)
    )

    # Red when <50% live
    result = "PASS" if ratio >= 0.50 else "FAIL"
    return {"id": "CAPTURE-SLO", "result": result, "detail": detail}


# Rolling window for HOOK-INTEGRITY — only beacons within this many days are counted.
# Beacons older than this are ignored so pre-fix historical drift never causes a
# permanent FAIL.  Dark-hook detection for hooks silent beyond this window is
# HOOK-LIVENESS's responsibility, not this check's.
_HOOK_INTEGRITY_WINDOW_DAYS = 7


def _hook_integrity_subjects(settings_path: Path) -> dict:
    """Map every beacon `hook` label HOOK-INTEGRITY scores to its subject label.

    The subject set is the hook scripts registered in `.claude/settings.json`,
    mirroring discover_hooks()'s own key selection: an `auto`-mode registration
    is scored under the argv label `auto` — `log-tool-event.sh`'s stream
    identity — with every runtime-derived terminal key
    `discovery._auto_mode_derived_keys(event, matcher)` can produce folded onto
    it; any other registration is scored under its own literal event-type
    argument or filename stem.  A label absent from the returned mapping is not
    a subject and is not scored.

    Returns {} (never raises) when the registration set cannot be read — the
    caller degrades to WARN rather than scoring against an assumed subject set.
    """
    import json as _json
    try:
        _insert_dashboard_sys_path()
        from discovery import (  # noqa: PLC0415
            _auto_mode_derived_keys,
            _event_type_from_cmd,
            _read_hook_name,
        )
    except Exception:
        return {}
    if not settings_path.exists():
        return {}
    subjects: dict = {}
    try:
        data = _json.loads(settings_path.read_text(encoding="utf-8"))
        for event, entries in data.get("hooks", {}).items():
            for entry in entries:
                matcher = entry.get("matcher", "")
                for hook in entry.get("hooks", []):
                    cmd = hook.get("command", "")
                    event_type_arg = _event_type_from_cmd(cmd)
                    if event_type_arg == "auto":
                        subjects["auto"] = "auto"
                        for key in _auto_mode_derived_keys(event, matcher):
                            subjects.setdefault(key, "auto")
                    elif event_type_arg:
                        subjects.setdefault(event_type_arg, event_type_arg)
                    else:
                        stem = _read_hook_name(cmd)
                        if stem:
                            subjects.setdefault(stem, stem)
    except Exception:
        return {}
    return subjects


def check_hook_integrity() -> dict:
    """HOOK-INTEGRITY: attempt-vs-ok beacon ratio per hook + ERROR beacon count.

    Reads hook-fires.jsonl (read-only).  Uses a rolling window of
    _HOOK_INTEGRITY_WINDOW_DAYS so stale pre-fix history never causes a
    permanent FAIL.

    Invariant (ADR-0083 D3 and its corollary — *a check's subject set is
    defined, not assumed, and every registered subject is scored under exactly
    one named label*): the subjects are the hook scripts registered in
    `.claude/settings.json` plus their declared stream identities, resolved by
    _hook_integrity_subjects().  A record whose `hook` label is outside that set
    — a runtime-derived event-type key standing alone, or any other label — owes
    this check nothing and is scored by neither term of the verdict: it enters
    no ratio, no drift entry, and no ERROR count.  `auto` is
    `log-tool-event.sh`'s stream identity, so its attempts and the derived
    terminals that pair with them fold onto one label and a complete pair is not
    drift.

    Semantics:
    - subject with recent attempts but missing ok → FAIL (genuine drift)
    - subject with NO recent beacons → not counted (dark-detection = HOOK-LIVENESS)
    - all recent attempts have matching ok → PASS
    - ERROR beacons attributed to a subject in any window → FAIL, and the detail
      names the subject each was attributed to
    """
    import json as _json
    from datetime import datetime, timezone, timedelta

    subjects = _hook_integrity_subjects(_HEALTH_REPO_ROOT / ".claude" / "settings.json")
    if not subjects:
        return {
            "id": "HOOK-INTEGRITY",
            "result": "WARN",
            "detail": (
                "hook subject set unreadable (.claude/settings.json) — not scoring; "
                "per ADR-0083 D3 a check with no defined subject set asserts nothing"
            ),
        }

    fires_log = _telemetry_log_root() / ".claude" / "logs" / "hook-fires.jsonl"
    if not fires_log.exists():
        return {
            "id": "HOOK-INTEGRITY",
            "result": "WARN",
            "detail": "hook-fires.jsonl not found",
        }

    window_cutoff = datetime.now(timezone.utc) - timedelta(days=_HOOK_INTEGRITY_WINDOW_DAYS)

    try:
        attempts: dict[str, int] = {}
        oks: dict[str, int] = {}
        errors: dict[str, int] = {}
        with fires_log.open(encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    obj = _json.loads(raw)
                except Exception:
                    continue
                hook = obj.get("hook", "")
                status = obj.get("status", "")
                if not hook:
                    continue
                # Registered-subject fold: score the record under the registered
                # hook (or declared stream identity) it belongs to, and skip any
                # label that owes this check nothing (ADR-0083 D3 corollary).
                subject = subjects.get(hook)
                if subject is None:
                    continue
                # Rolling-window filter: skip beacons older than _HOOK_INTEGRITY_WINDOW_DAYS.
                ts_str = obj.get("ts", "")
                if ts_str:
                    try:
                        beacon_dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                        if beacon_dt < window_cutoff:
                            continue  # outside rolling window — skip
                    except Exception:
                        pass  # unparseable ts: include conservatively
                if status == "attempt":
                    attempts[subject] = attempts.get(subject, 0) + 1
                elif status == "ok":
                    oks[subject] = oks.get(subject, 0) + 1
                elif status in ("ERROR", "error"):
                    errors[subject] = errors.get(subject, 0) + 1
    except Exception as exc:
        return {"id": "HOOK-INTEGRITY", "result": "WARN",
                "detail": f"read error: {exc}"}

    # If no beacons at all in the rolling window, defer to HOOK-LIVENESS.
    if not attempts and not errors:
        return {
            "id": "HOOK-INTEGRITY",
            "result": "WARN",
            "detail": (
                f"no attempt beacons in last {_HOOK_INTEGRITY_WINDOW_DAYS}d window; "
                f"dark-detection deferred to HOOK-LIVENESS"
            ),
        }

    # Per-subject ratios (only subjects that have attempt beacons in window)
    drift_hooks = []
    ratio_parts = []
    for subject, att in sorted(attempts.items()):
        ok = oks.get(subject, 0)
        ratio_parts.append(f"{subject}:{ok}/{att}")
        if ok < att:
            drift_hooks.append(f"{subject}({ok}/{att})")

    detail_parts = [f"window={_HOOK_INTEGRITY_WINDOW_DAYS}d"]
    if ratio_parts:
        detail_parts.append("ratios: " + ", ".join(ratio_parts))
    if errors:
        # Name the subject each ERROR was attributed to — a verdict term a
        # reader cannot trace to a subject is the defect D3 names.
        attributed = ", ".join(f"{s}:{n}" for s, n in sorted(errors.items()))
        detail_parts.append(f"ERROR beacons: {attributed}")
    if drift_hooks:
        detail_parts.append(f"drift: {', '.join(drift_hooks)}")

    detail = " | ".join(detail_parts)
    result = "FAIL" if (drift_hooks or errors) else "PASS"
    return {"id": "HOOK-INTEGRITY", "result": result, "detail": detail}


def check_isolation_group() -> dict:
    """ISOLATION-GROUP: orphaned worktree dirs, prune drift, escaped dispatches.

    Checks:
    1. Dirs under .claude/worktrees/ that are NOT registered in `git worktree list`
       (orphaned — agent-* dirs left behind after the worktree was removed).
    2. Worktrees that are 0-ahead + clean relative to origin/main (prune drift —
       they could be pruned).

    Read-only: never removes anything; only reports.
    """
    worktrees_dir = _HEALTH_REPO_ROOT / ".claude" / "worktrees"
    if not worktrees_dir.exists():
        return {
            "id": "ISOLATION-GROUP",
            "result": "PASS",
            "detail": ".claude/worktrees/ does not exist (no dispatches yet)",
        }

    # Get registered worktrees from git
    try:
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            cwd=str(_HEALTH_REPO_ROOT),
        )
        registered_paths: set[str] = set()
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                if line.startswith("worktree "):
                    wt_path = line[len("worktree "):].strip()
                    # Normalize separators + case so forward-slash (git porcelain)
                    # and backslash (Path.iterdir on Windows) compare equal (B1).
                    registered_paths.add(
                        os.path.normcase(os.path.normpath(wt_path))
                    )
    except Exception as exc:
        return {"id": "ISOLATION-GROUP", "result": "WARN",
                "detail": f"git worktree list failed: {exc}"}

    # Scan dirs under .claude/worktrees/
    orphaned = []
    prune_drift = []
    try:
        dirs = sorted(d for d in worktrees_dir.iterdir() if d.is_dir())
    except Exception as exc:
        return {"id": "ISOLATION-GROUP", "result": "WARN",
                "detail": f"scan failed: {exc}"}

    for d in dirs:
        # Normalize both sides: Path.iterdir yields backslash paths on Windows
        # while git porcelain yields forward slashes; normcase+normpath unifies both.
        path_norm = os.path.normcase(os.path.normpath(str(d)))
        registered = path_norm in registered_paths
        if not registered:
            orphaned.append(d.name)
            continue

        # Check prune-drift: 0-ahead and clean
        try:
            ahead = subprocess.run(
                ["git", "rev-list", "--count", "origin/main..HEAD"],
                capture_output=True, text=True, timeout=8, cwd=str(d),
            )
            status = subprocess.run(
                ["git", "status", "--porcelain"],
                capture_output=True, text=True, timeout=8, cwd=str(d),
            )
            if (ahead.returncode == 0 and ahead.stdout.strip() == "0"
                    and status.returncode == 0 and not status.stdout.strip()):
                prune_drift.append(d.name)
        except Exception:
            pass  # skip drift check for this worktree; not an error

    parts = []
    if orphaned:
        parts.append(f"orphaned: {', '.join(orphaned[:5])}")
    if prune_drift:
        parts.append(f"prune-drift: {', '.join(prune_drift[:5])}")

    # Escaped-dispatch note (informational only; not computable from logs alone)
    total_dirs = len(dirs)
    parts.append(f"dirs: {total_dirs}, registered: {len(registered_paths)}")

    result = "FAIL" if orphaned else "WARN" if prune_drift else "PASS"
    detail = " | ".join(parts) if parts else f"dirs: {total_dirs}"
    return {"id": "ISOLATION-GROUP", "result": result, "detail": detail}


# ---------------------------------------------------------------------------
# Rule→enforcer map (slice #851 — make RULE-COVERAGE count real enforcers)
#
# Each entry maps a CLAUDE.md section-1 rule number to one or more enforcers.
# Enforcer strings must be one of:
#   "R-XXX"   — reviewer.md hard-block rule (verified by check_rule_coverage)
#   "SC-XXX"  — slicer-critic.md rubric rule (verified by check_rule_coverage)
#   "CHECK-ID" — a key in CHECK_REGISTRY (verified by check_rule_coverage)
#   "advisory" — rule is explicitly advisory; no mechanical enforcer required
#
# IMPORTANT: check_rule_coverage() VERIFIES each entry against the actual file
# content at runtime — a mapped R-XXX that disappears from reviewer.md causes
# the mapped rule to fall back to unchecked.  The map cannot silently drift.
# ---------------------------------------------------------------------------
RULE_ENFORCER_MAP: dict[int, list[str]] = {
    # #1 YAGNI — reviewer hard-blocks scope drift / YAGNI violations
    1:  ["R-YAGNI", "R-SCOPE"],
    # #2 Walking-skeleton — slicer-critic enforces at decomposition time
    2:  ["SC-WALKING-SKELETON"],
    # #3 Build primitives first — no mechanical check; purely advisory discipline
    3:  ["advisory"],
    # #4 Never push to main — reviewer detects commits directly on main
    4:  ["R-NO-MAIN"],
    # #5 Conventional Commits — reviewer hard-blocks format violations
    5:  ["R-CONV-COMMITS"],
    # #6 git log as changelog — advisory; no mechanical enforcement feasible
    6:  ["advisory"],
    # #8 One PR per slice — reviewer enforces via Closes + LOC cap
    8:  ["R-CLOSES", "R-LOC"],
    # #9 DRY for docs — advisory; codebase-critic catches egregious duplication
    9:  ["advisory"],
    # #10 Main-agent meta-output discipline — advisory; enforced behaviorally
    10: ["advisory"],
    # #11 Surface deferred work as captured issues — CAPTURE-SHAPE health row
    11: ["CAPTURE-SHAPE"],
    # #12 Hooks five authorized categories — HOOK-INTEGRITY health row
    12: ["HOOK-INTEGRITY"],
    # #13 Root-cause workflow capture — CAPTURE-SHAPE (shape) + TEST-ORDERING (regression rider)
    13: ["CAPTURE-SHAPE", "TEST-ORDERING"],
    # #15 Every feature production-verified — PROOF-PRESENCE health row
    15: ["PROOF-PRESENCE"],
    # #16 Slice-decomposition is slicer's job — advisory; no mechanical check
    16: ["advisory"],
    # #17 Skill-vs-subagent litmus — advisory; audit-subagents skill checks shape
    17: ["advisory"],
    # #18 Never cite ADR from memory — advisory; adr-critic catches violations
    18: ["advisory"],
    # #19 Revise the whole flagged class — advisory; round-3 escalation is behavioral
    19: ["advisory"],
    # #20 Proof-per-claim in wrap-up summaries — PROOF-PRESENCE health row
    20: ["PROOF-PRESENCE"],
    # #21 Fixture discipline — reviewer hard-blocks fixture writes to logs
    21: ["R-FIXTURE"],
    # #22 System skeleton — slicer-critic enforces at decomposition time
    22: ["SC-SYSTEM-SKELETON"],
    # #23 No rule without a check — reviewer hard-blocks new rules without enforcement
    23: ["R-RULE-CHECK"],
}


def check_rule_coverage() -> dict:
    """RULE-COVERAGE (WARN): ratio of CLAUDE.md section-1 rules that have a
    verified enforcer (reviewer R- rule, health check ID, or advisory tag).

    Two complementary coverage paths — a rule is "covered" when EITHER holds:

    Path A — inline signal in CLAUDE.md text (legacy heuristic, preserved):
      - "CI grep", "ci-checks", "tools/ci-checks"
      - "hook validation", "pre-commit", ".claude/hooks"
      - "dashboard evaluator", "health check", "trail evaluator"
      - "output-contract", "trailer schema"
      - "reviewer rule", "R-RULE", "(Mechanized by", "(Enforced at", "(enforced by"
      - Named critic rubric pattern matching \b(R|AC|SC|PC)-[A-Z]{2,}

    Path B — verified RULE_ENFORCER_MAP entry (slice #851 addition):
      Each enforcer in RULE_ENFORCER_MAP is verified at runtime:
        - "R-XXX"    → must appear as "### R-XXX" in .claude/agents/reviewer.md
        - "SC-XXX"   → must appear in .claude/agents/slicer-critic.md
        - "advisory" → always counts as covered (explicitly tagged)
        - other      → must be a key in CHECK_REGISTRY

    Pre-existing rules (≤22) are grandfathered per ADR-0008 D8 (bootstrap-mode);
    reported in the ratio but not flagged as newly violating.
    Only rules #23+ are flagged as unchecked-and-untagged.

    Always WARNs (never FAILs) until the wave-3 retrofit pass; per ADR-0056 D3.
    """
    claude_md = _HEALTH_REPO_ROOT / "CLAUDE.md"
    if not claude_md.exists():
        return {"id": "RULE-COVERAGE", "result": "WARN", "detail": "CLAUDE.md missing"}

    text = _read_file(claude_md)

    # Locate section 1: starts at "## 1." and ends at the next "## " heading.
    sec1_m = re.search(r'^## 1\.', text, re.MULTILINE)
    if not sec1_m:
        return {"id": "RULE-COVERAGE", "result": "WARN",
                "detail": "could not locate '## 1.' in CLAUDE.md"}
    next_h2 = re.search(r'^## [^1]', text[sec1_m.end():], re.MULTILINE)
    sec1_end = sec1_m.end() + next_h2.start() if next_h2 else len(text)
    section1 = text[sec1_m.start():sec1_end]

    # Path A: Coverage signals — one match anywhere in the rule's line-block is sufficient.
    _COVERAGE_SIGNALS = (
        "CI grep", "ci-checks", "tools/ci-checks",
        "hook validation", "pre-commit", ".claude/hooks",
        "dashboard evaluator", "health check", "trail evaluator",
        "output-contract", "trailer schema",
        "reviewer rule", "R-RULE", "(Mechanized by", "(Enforced at", "(enforced by",
    )
    # Named critic rubric patterns (R-XXX, AC-XXX, SC-XXX, PC-XXX) — minimum 2 uppercase letters
    _RUBRIC_PAT = re.compile(r'\b(R|AC|SC|PC)-[A-Z]{2,}')

    # Path B: Load side-files for enforcer verification (once per call).
    reviewer_md_path = _HEALTH_REPO_ROOT / ".claude" / "agents" / "reviewer.md"
    slicer_critic_path = _HEALTH_REPO_ROOT / ".claude" / "agents" / "slicer-critic.md"
    reviewer_text = _read_file(reviewer_md_path) if reviewer_md_path.exists() else ""
    slicer_text = _read_file(slicer_critic_path) if slicer_critic_path.exists() else ""

    def _enforcer_verified(enforcer: str) -> bool:
        """Return True if the enforcer string resolves to a real artifact."""
        if enforcer == "advisory":
            return True
        if enforcer.startswith("R-"):
            # Must appear as a ### heading in reviewer.md
            return f"### {enforcer}" in reviewer_text
        if enforcer.startswith("SC-"):
            return enforcer in slicer_text
        if enforcer.startswith(("PC-", "AC-")):
            return True  # prd-critic / adr-critic rules; not read here
        # Otherwise treat as a CHECK_REGISTRY ID (verified after registry is built)
        return enforcer in CHECK_REGISTRY

    def _map_covers(rnum: int) -> tuple[bool, list[str]]:
        """Return (covered_by_map, verified_enforcers) for the given rule number."""
        enforcers = RULE_ENFORCER_MAP.get(rnum, [])
        verified = [e for e in enforcers if _enforcer_verified(e)]
        return bool(verified), verified

    # Parse numbered rule entries.  Each entry may span multiple lines (sub-bullets).
    # Strategy: split on the rule-entry pattern and capture each block.
    rule_entry_pat = re.compile(
        r'^(?P<num>[0-9]+)\.\s+\*\*.*?rule\s+#(?P<rnum>[0-9]+)',
        re.MULTILINE,
    )

    # Collect (rule_number, full_block_text) pairs.
    matches = list(rule_entry_pat.finditer(section1))
    rules = []
    for i, m in enumerate(matches):
        block_start = m.start()
        block_end = matches[i + 1].start() if i + 1 < len(matches) else len(section1)
        block = section1[block_start:block_end]
        rnum = int(m.group("rnum"))
        rules.append((rnum, block))

    total = len(rules)
    if total == 0:
        return {"id": "RULE-COVERAGE", "result": "WARN",
                "detail": "no numbered rules found in section 1"}

    covered_nums = []
    covered_detail: dict[int, str] = {}  # rnum → enforcer description
    unchecked_grandfathered = []
    unchecked_new = []

    _BOOTSTRAP_CUTOFF = 22  # rules ≤22 are grandfathered per ADR-0008 D8

    for rnum, block in rules:
        is_advisory = "(advisory)" in block
        has_signal = any(sig in block for sig in _COVERAGE_SIGNALS)
        has_rubric = bool(_RUBRIC_PAT.search(block))
        inline_covered = is_advisory or has_signal or has_rubric

        map_covered, verified_enforcers = _map_covers(rnum)
        covered = inline_covered or map_covered

        if covered:
            covered_nums.append(rnum)
            if map_covered:
                covered_detail[rnum] = ",".join(verified_enforcers)
            else:
                covered_detail[rnum] = "inline-signal"
        elif rnum <= _BOOTSTRAP_CUTOFF:
            unchecked_grandfathered.append(rnum)
        else:
            unchecked_new.append(rnum)

    covered_count = len(covered_nums)
    ratio_pct = int(covered_count * 100 / total)

    parts = [f"{covered_count}/{total} covered ({ratio_pct}%)"]

    # Emit per-rule enforcer summary for load-bearing mapped rules
    mapped_lines = []
    for rnum in sorted(covered_nums):
        desc = covered_detail.get(rnum, "")
        if desc and desc != "inline-signal":
            mapped_lines.append(f"#{rnum}:{desc}")
    if mapped_lines:
        parts.append("map-enforced: " + " ".join(mapped_lines))

    if unchecked_grandfathered:
        parts.append(f"grandfathered-unchecked: {unchecked_grandfathered}")
    if unchecked_new:
        parts.append(f"NEW unchecked-untagged: {unchecked_new}")

    detail = " | ".join(str(p) for p in parts)
    # Always WARN per ADR-0056 D3 (retrofit cadence owns the FAILs)
    result = "WARN" if (unchecked_grandfathered or unchecked_new) else "PASS"
    return {"id": "RULE-COVERAGE", "result": result, "detail": detail}


# ---------------------------------------------------------------------------
# Spec-coverage check (slice #798 / ADR-0066 D2: SC-COVERAGE health-registry row)
# ---------------------------------------------------------------------------

def check_spec_coverage() -> dict:
    """SPEC-COVERAGE: per-PRD criterion coverage from Covers: §2 #n lines in slice bodies.

    Algorithm (per ADR-0066 D2):
    - For each open+closed PRD-labeled issue, parse the numbered criteria in §2.
    - Find all slice sub-issues (label: slice, body containing "PRD #<N>") and parse
      their "Covers: §2 #n[, #m]" lines.
    - Per-PRD coverage = |cited ∩ §2| / |§2|, with orphan/phantom counts.
    - PRDs with no Covers: lines on any slice (predating the convention) are placed
      in a grandfathered/no-data bucket per ADR-0004 D2 (bind-forward), NOT scored 0%.
    - API-unavailable: honest WARN rather than a silent failure.

    PASS when every post-convention PRD with ≥1 criteria has full coverage (ratio = 1.0).
    WARN when any post-convention PRD is partially covered, or when no PRDs are available.
    FAIL when any post-convention PRD has orphan criteria (criteria with no covering slice).
    """
    import json as _json
    import subprocess as _sp

    def _gh_issue_list(label: str, limit: int = 100) -> list:
        # Routed through gh_cache (ttl=60s, timeout=5s) — PRD #993 cr.3/cr.4, slice #996.
        try:
            rc, out = _health_gh_fetch(
                ["issue", "list", "--label", label,
                 "--state", "all", "--limit", str(limit),
                 "--json", "number,title,body"],
                ttl=60.0, timeout=5.0,
            )
            if rc == 0 and out.strip():
                return _json.loads(out)
        except Exception:
            pass
        return None  # None signals API failure; [] would mean empty list

    # --- 1. Fetch PRD issues ---
    prd_issues = _gh_issue_list("prd", limit=50)
    if prd_issues is None:
        return {"id": "SPEC-COVERAGE", "result": "WARN",
                "detail": "gh API unavailable — cannot compute coverage"}

    if not prd_issues:
        return {"id": "SPEC-COVERAGE", "result": "WARN",
                "detail": "no PRD-labeled issues found"}

    # --- 2. Fetch slice issues ---
    slice_issues = _gh_issue_list("slice", limit=200)
    if slice_issues is None:
        return {"id": "SPEC-COVERAGE", "result": "WARN",
                "detail": "gh API unavailable for slice issues"}

    # --- 3. Parse §2 criteria from each PRD (module-level helper — #1077 fix) ---
    # --- 4. Build PRD → criteria map ---
    prd_criteria = {}
    for issue in prd_issues:
        n = issue["number"]
        criteria = _parse_sec2_criteria(issue.get("body") or "")
        prd_criteria[n] = criteria

    # --- 5. Build PRD → cited union from slice Covers: lines ---
    prd_cited = {n: set() for n in prd_criteria}
    prd_has_covers = {n: False for n in prd_criteria}
    prd_malformed_covers = {n: 0 for n in prd_criteria}  # bare "Covers: #N" hint (#1106)

    for issue in slice_issues:
        body = issue.get("body") or ""
        # Find parent PRD
        pm = _SPEC_PARENT_PRD.search(body)
        if not pm:
            continue
        prd_num = int(pm.group(1))
        if prd_num not in prd_criteria:
            continue
        # Find Covers: line
        cm = _SPEC_COVERS_LINE.search(body)
        if cm:
            prd_has_covers[prd_num] = True
            cited_nums = set(_SPEC_COVERS_NUM.findall(cm.group(1)))
            prd_cited[prd_num] |= cited_nums
        elif _SPEC_COVERS_BARE.search(body):
            # Malformed-form hint (#1106): a bare "Covers: #N" line (missing the
            # "§2" ADR-0066 D2 requires) means this slice silently does NOT
            # count toward coverage — surfaced so it isn't mistaken for
            # legitimate grandfathering (pre-convention: no Covers: line at all).
            prd_malformed_covers[prd_num] += 1

    # --- 6. Compute per-PRD coverage ---
    fully_covered = []
    partial = []     # (prd_num, orphans, phantoms)
    grandfathered = []

    for prd_num, criteria in prd_criteria.items():
        if not criteria:
            # PRD has no numbered §2 criteria — trivially covered
            fully_covered.append(prd_num)
            continue
        if not prd_has_covers[prd_num]:
            # No slice carries a Covers: line → grandfathered/no-data bucket
            grandfathered.append(prd_num)
            continue
        cited = prd_cited[prd_num]
        orphans = criteria - cited        # criteria with no covering slice
        phantoms = cited - criteria       # citations to nonexistent criteria
        if not orphans and not phantoms:
            fully_covered.append(prd_num)
        else:
            partial.append((
                prd_num,
                sorted(orphans, key=_crit_sort_key),
                sorted(phantoms, key=_crit_sort_key),
            ))

    # --- 7. Build summary detail ---
    total_post_conv = len(fully_covered) + len(partial)
    parts = []
    if total_post_conv == 0 and grandfathered:
        parts.append(
            f"all {len(grandfathered)} PRDs grandfathered (no Covers: lines yet)"
        )
    else:
        parts.append(f"{len(fully_covered)}/{total_post_conv} fully covered")
    if partial:
        gap_descs = []
        for prd_num, orphans, phantoms in partial:
            desc = f"PRD#{prd_num}"
            if orphans:
                desc += f" orphans={orphans}"
            if phantoms:
                desc += f" phantoms={phantoms}"
            gap_descs.append(desc)
        parts.append("gaps: " + "; ".join(gap_descs))
    if grandfathered:
        parts.append(f"grandfathered (pre-convention): {sorted(grandfathered)}")
    malformed_hint = {
        n: c for n, c in prd_malformed_covers.items() if c and not prd_has_covers[n]
    }
    if malformed_hint:
        parts.append(
            "malformed 'Covers: #N' (missing '§2', see ADR-0066 D2): "
            + ", ".join(f"PRD#{n}={c}" for n, c in sorted(malformed_hint.items()))
        )

    detail = " | ".join(parts)

    if partial:
        result = "FAIL"
    elif total_post_conv == 0:
        result = "WARN"
    else:
        result = "PASS"

    return {
        "id": "SPEC-COVERAGE",
        "result": result,
        "detail": detail,
        "fully_covered": sorted(fully_covered),
        "partial": partial,
        "grandfathered": sorted(grandfathered),
    }


# ---------------------------------------------------------------------------
# SPEC-COVERAGE regexes + helpers — module-level for direct unit-testability
# (issue #1077 / ADR-0066 D2): the prior digit-only regexes silently dropped
# the trailing letter on lettered sub-criteria (e.g. PRD #1075's `2a.`/`2b.`/
# `10a.`-`10d.`), collapsing distinct criteria onto one digit and producing
# false phantoms/orphans against a PRD with complete semantic coverage.
# Criterion IDs are now strings (e.g. "2", "2a", "10b") — never ints, so "2"
# and "2a" cannot collide as they would under an int model.
# ---------------------------------------------------------------------------
_SPEC_SEC2_START = re.compile(r'^## 2\.', re.MULTILINE)
_SPEC_NEXT_H2 = re.compile(r'^## [^2]', re.MULTILINE)
_SPEC_CRIT_NUM = re.compile(r'^(\d+[a-z]?)\.\s+\S', re.MULTILINE)
_SPEC_PARENT_PRD = re.compile(r'PRD\s+#(\d+)')
_SPEC_COVERS_LINE = re.compile(r'(?m)^Covers:\s+§2\s+(.*)')
_SPEC_COVERS_NUM = re.compile(r'#(\d+[a-z]?)')
_SPEC_COVERS_BARE = re.compile(r'(?m)^Covers:\s+(?!§2\b)#\d')  # malformed-form hint (#1106)


def _parse_sec2_criteria(body: str) -> set:
    """Return the set of §2 criterion-ID strings (e.g. {"1", "2a", "2b"})
    found in a PRD body's §2 section. Empty set if §2 is absent/empty."""
    if not body:
        return set()
    m = _SPEC_SEC2_START.search(body)
    if not m:
        return set()
    nh2 = _SPEC_NEXT_H2.search(body, m.end())
    end = m.end() + nh2.start() if nh2 else len(body)
    sec2 = body[m.start():end]
    return set(_SPEC_CRIT_NUM.findall(sec2))


def _crit_sort_key(cid: str):
    """Sort key for criterion-ID strings: (numeric part, letter suffix) so
    "10a" sorts after "2a" — a bare string sort would put "10a" first."""
    m = re.match(r'(\d+)([a-z]?)', cid)
    if not m:
        return (0, cid)
    return (int(m.group(1)), m.group(2))


# ---------------------------------------------------------------------------
# Critic-health check (slice #779 / ADR-0059 D1 / ADR-0060 D4)
# ---------------------------------------------------------------------------

# Doubt-theater streak threshold: N consecutive first-round APPROVEs triggers amber badge.
# Documented here per slice #779 acceptance-criterion and PRD #778 §5 open question.
_DOUBT_THEATER_N = 10

# Window: last N closed PRDs whose trails are scanned for critic verdicts.
_CRITIC_HEALTH_PRD_WINDOW = 10


def check_critic_health() -> dict:
    """CRITIC-HEALTH: per-critic first-pass APPROVE rate + rounds histogram + doubt-theater streak.

    Reuses the collector's existing gh-fetch/caching seam (get_trail + get_closed_prd_numbers)
    to scan the last _CRITIC_HEALTH_PRD_WINDOW closed PRDs' comment trails.

    Per-critic metrics:
      - first_pass_approve_rate: fraction of final-APPROVE runs where round==1
      - rounds_histogram: {1: N, 2: N, ...} — max_round for each reviewed PR
      - doubt_theater_streak: consecutive first-round APPROVEs at the tail of the
        chronological verdict list (amber >= _DOUBT_THEATER_N, never auto-acted on)

    Pre-v2 verdicts (no CRITIC: field) → "unattributed" bucket.
    Honest design: with no merged post-v2 PRs yet, the unattributed bucket will
    hold all verdicts and named critics will show 0 verdicts — that is correct.

    Returns a substrate-compatible check dict with id="CRITIC-HEALTH" and a
    per-critic breakdown in the "critics" key in the check's result.
    """
    try:
        # Lazy import to avoid circular deps (collector imports nothing from health)
        _insert_dashboard_sys_path()
        from collector import get_closed_prd_numbers, get_trail  # noqa: PLC0415
        from collector import parse_critic_field  # noqa: PLC0415 (re-export)
    except Exception as exc:
        return {
            "id": "CRITIC-HEALTH",
            "result": "WARN",
            "detail": f"collector import failed: {exc}",
            "critics": {},
        }

    prd_numbers = get_closed_prd_numbers(_CRITIC_HEALTH_PRD_WINDOW)
    if not prd_numbers:
        return {
            "id": "CRITIC-HEALTH",
            "result": "WARN",
            "detail": "no closed PRDs found; trail empty",
            "critics": {},
        }

    # Collect all verdict records across the window.
    # Each record: {"critic": str|None, "verdict": "APPROVE"|"BLOCK",
    #               "round": int|None, "created_at": str}
    all_verdicts: list[dict] = []
    auth_dead = False

    for prd_num in prd_numbers:
        trail = get_trail(prd_num)
        if trail.get("collector_status") == "auth_dead":
            auth_dead = True
            continue
        for pr_trail in trail.get("prs", {}).values():
            for v in pr_trail.get("verdicts", []):
                all_verdicts.append(v)
        # Also include PRD-level verdicts (prd-critic, adr-critic rounds)
        for v in trail.get("prd_verdicts", []):
            all_verdicts.append(v)

    if not all_verdicts:
        detail = (
            f"0 verdicts across last {len(prd_numbers)} PRDs "
            f"(auth_dead={auth_dead}); pre-v2 history fully unattributed — expected"
        )
        return {
            "id": "CRITIC-HEALTH",
            "result": "PASS",
            "detail": detail,
            "critics": {"unattributed": {"verdict_count": 0, "first_pass_approve_rate": None,
                                          "rounds_histogram": {}, "doubt_theater_streak": 0}},
        }

    # Group verdicts by critic (None → "unattributed")
    # Per-PR "runs": group by a (prd, pr) key to compute max-round per PR.
    # We don't have that granularity in the flat list, so we approximate:
    # treat each sequence of verdicts per (prd, pr) as one run.
    # In the flat list we have them; just attribute each verdict individually.

    from collections import defaultdict  # noqa: PLC0415
    critic_verdicts: dict[str, list[dict]] = defaultdict(list)
    for v in all_verdicts:
        name = v.get("critic") or "unattributed"
        critic_verdicts[name].append(v)

    critics_out: dict[str, dict] = {}
    for name, verdicts in sorted(critic_verdicts.items()):
        total = len(verdicts)
        # First-pass APPROVE rate: fraction where round==1 and verdict==APPROVE
        r1_approves = sum(
            1 for v in verdicts
            if v.get("verdict") == "APPROVE" and v.get("round") == 1
        )
        # Final verdicts per run proxy: any APPROVE at any round
        approves = sum(1 for v in verdicts if v.get("verdict") == "APPROVE")
        first_pass_rate = round(r1_approves / total, 3) if total > 0 else None

        # Rounds histogram: {round_num: count}
        hist: dict[str, int] = {}
        for v in verdicts:
            r = v.get("round")
            key = str(r) if r is not None else "unknown"
            hist[key] = hist.get(key, 0) + 1

        # Doubt-theater streak: consecutive first-round APPROVEs at tail
        # (most recent last in the list, since they're in insertion order)
        streak = 0
        for v in reversed(verdicts):
            if v.get("verdict") == "APPROVE" and v.get("round") == 1:
                streak += 1
            else:
                break

        critics_out[name] = {
            "verdict_count": total,
            "approve_count": approves,
            "first_pass_approve_rate": first_pass_rate,
            "rounds_histogram": hist,
            "doubt_theater_streak": streak,
            "doubt_theater_amber": streak >= _DOUBT_THEATER_N,
        }

    # Overall result: PASS unless auth_dead or data quality issues
    result = "WARN" if auth_dead else "PASS"
    total_verdicts = len(all_verdicts)
    unattr = len(critic_verdicts.get("unattributed", []))
    named = total_verdicts - unattr
    detail = (
        f"{total_verdicts} verdicts across last {len(prd_numbers)} PRDs "
        f"({named} attributed, {unattr} unattributed); "
        f"doubt-theater threshold N={_DOUBT_THEATER_N}"
    )
    if auth_dead:
        detail += " | WARNING: some PRDs skipped (auth_dead)"

    return {
        "id": "CRITIC-HEALTH",
        "result": result,
        "detail": detail,
        "critics": critics_out,
    }


# ---------------------------------------------------------------------------
# Verification-integrity evaluators (slice #783 / ADR-0060/0061/0062/0063)
# ---------------------------------------------------------------------------

# ADR-0061 D1 route-table glob classes (changed-path → mandatory proof class).
# Used by check_proof_presence to classify PRs by their changed paths.
_ROUTE_TABLE = [
    # (glob_pattern, proof_class)
    # dashboard/** routes command-run, not browser (ADR-0088 D5): after the
    # served dashboard's retirement, every surviving file under dashboard/
    # is a command-line library. *.html stays browser for host UIs.
    ("dashboard/**", "command-run"),
    ("*.html", "browser"),
    (".claude/hooks/**", "hook-fire"),
    (".claude/settings.json", "hook-fire"),
    ("tools/**", "command-run"),
    (".claude/skills/**", "command-run"),
    (".github/workflows/**", "command-run"),
    ("decisions/**", "static"),
    ("docs/**", "static"),
    ("*.md", "static"),
    ("CLAUDE.md", "static"),
    ("bootstrap.sh", "static"),
]

# Proof tokens per route class (ADR-0061 D1 / rule #20).
# These regexes are searched over the PR body + comment trail.
# ADR-0083 D4(a): the hook-fire class is evidence-shaped — matched patterns are
# shapes only a real beacon record or verification transcript produces (a
# beacon's own key/value structure, an exit code with a digit), never the
# ordinary prose used to refer to that evidence (a filename, a route name).
_PROOF_TOKENS: dict[str, list[str]] = {
    "browser":      [r'\.png\b', r'inner_text:', r'screenshot'],
    "hook-fire":    [r'"status"\s*:\s*"(ok|ERROR)"', r'"hook"\s*:\s*"', r'exit=\d'],
    "command-run":  [r'exit=', r'exit code', r'exit\s*0'],
    "static":       [r'grep count=', r'grep -c', r'grep\s+\d+', r'count=\d'],
}

# Window for proof-presence: last N merged non-trivial PRs.
_PROOF_PRESENCE_WINDOW = 10

# Bootstrap cutoff for proof-presence: PRs before this are grandfathered.
# Bind-forward per ADR-0004 D2 — slice #783 is the implementing merge.
_PROOF_PRESENCE_BOOTSTRAP_PR = 788   # last merged PR before this slice


def _classify_route(changed_files: list[str]) -> set[str]:
    """Return the union of proof classes from changed-path globs (ADR-0061 D1)."""
    import fnmatch
    classes: set[str] = set()
    for f in changed_files:
        for pattern, cls in _ROUTE_TABLE:
            if fnmatch.fnmatch(f, pattern) or fnmatch.fnmatch(f.split("/")[-1], pattern):
                classes.add(cls)
                break
    return classes


def _pr_has_proof_token(pr_body: str, comments: list[str], route_classes: set[str]) -> set[str]:
    """Return the subset of route_classes with NO matching proof token.

    Conjunctive per ADR-0061 D1 ("takes the union of proof classes") and
    ADR-0083 D4(b): a PR is compliant only when EVERY class in the route union
    has a matching token, not just one. An empty return set means the PR is
    fully compliant; a non-empty one names exactly which classes are missing
    (ADR-0083 D4(c) — the detail must say what was missed, not just that
    something was).
    """
    search_text = " ".join([pr_body] + comments)
    unsatisfied: set[str] = set()
    for cls in route_classes:
        tokens = _PROOF_TOKENS.get(cls, [])
        if not any(re.search(tok, search_text, re.IGNORECASE) for tok in tokens):
            unsatisfied.add(cls)
    return unsatisfied


def check_blind_dispatch_rate() -> dict:
    """BLIND-RATE: fraction of critic dispatches with ^BLIND-REVIEW prefix.

    Reads workflow-events.jsonl for agent_start events whose input begins with
    'BLIND-REVIEW'. Pre-migration denominator is honest: all agent_start events
    with a non-empty input are counted. Bind-forward per ADR-0060 D5 — pre-merge
    dispatches are grandfathered.

    Returns {"id": "BLIND-RATE", "result": ..., "detail": ...,
             "blind": N, "total": N, "rate": float}
    """
    import json as _json
    events_log = _telemetry_log_root() / ".claude" / "logs" / "workflow-events.jsonl"
    if not events_log.exists():
        return {"id": "BLIND-RATE", "result": "WARN",
                "detail": "workflow-events.jsonl not found", "blind": 0, "total": 0, "rate": None}

    blind = 0
    total = 0
    try:
        with events_log.open(encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    obj = _json.loads(raw)
                except Exception:
                    continue
                if obj.get("event") != "agent_start":
                    continue
                inp = obj.get("input", "") or ""
                if not inp:
                    continue
                total += 1
                if inp.startswith("BLIND-REVIEW"):
                    blind += 1
    except Exception as exc:
        return {"id": "BLIND-RATE", "result": "WARN",
                "detail": f"read error: {exc}", "blind": 0, "total": 0, "rate": None}

    if total == 0:
        return {"id": "BLIND-RATE", "result": "WARN",
                "detail": "no agent_start events with input found (pre-migration — expected)",
                "blind": 0, "total": 0, "rate": None}

    rate = round(blind / total, 3)
    detail = (
        f"{blind}/{total} dispatches carry BLIND-REVIEW prefix "
        f"({rate*100:.0f}%) — pre-migration denominator; bind-forward ADR-0060 D5"
    )
    result = "PASS" if rate >= 1.0 else "WARN"
    return {"id": "BLIND-RATE", "result": result, "detail": detail,
            "blind": blind, "total": total, "rate": rate}


# Minimum QA-plan rows required before ratio is meaningful (honest guard).
# Rationale: a ratio from 1-2 rows (e.g. one plan with 2 criteria total) has
# +-50% sampling noise; the ADR-0066 D1 drop-criterion requires a stable signal.
# 5 rows chosen as a pragmatic floor: below this the check emits WARN/low-sample
# and reports the actual counts without a ratio verdict.
_RESIDUAL_RATIO_MIN_ROWS = 5

# Limit on closed PRDs scanned for QA-plan tables -- avoids excessive gh API calls.
_RESIDUAL_RATIO_PRD_WINDOW = 20


def check_residual_ratio() -> dict:
    """RESIDUAL-RATIO: (JUDGMENT + EXTRACT_FAILED) / total rows across QA-plan tables.

    Reads closed PRD issue comments for '## QA-plan' headings (the qa-plan
    skill persists its plan as a PRD comment per ADR-0020 D4).  Within each
    QA-plan table, scans the second column (the check column) for the literal
    strings 'JUDGMENT' and 'EXTRACT_FAILED'.

    Measurement per ADR-0066 D1 drop-criterion: if the ratio does not fall after
    PC-EARS adoption (this slice's merge), the rule is theater and should be
    dropped.  Bind-forward: only criteria authored post-merge are expected to be
    EARS-shaped; pre-merge plans are honestly included in the denominator.

    Minimum-sample guard: fewer than _RESIDUAL_RATIO_MIN_ROWS total criteria rows
    across all scanned plans -> WARN with 'low-sample' label and raw counts instead
    of a ratio.  This avoids a misleading 0%% or 100%% ratio from 1-2 data points.

    Returns {"id": "RESIDUAL-RATIO", "result": ..., "detail": ...,
             "judgment": N, "extract_failed": N, "total": N, "rate": float|None}
    """
    import json as _json
    import subprocess as _sp

    def _fetch_closed_prds(limit):
        # Routed through gh_cache — PRD #993 cr.3/cr.4, slice #996.
        try:
            rc, out = _health_gh_fetch(
                ["issue", "list", "--label", "prd",
                 "--state", "closed", "--limit", str(limit),
                 "--json", "number"],
                ttl=60.0, timeout=5.0,
            )
            if rc == 0 and out.strip():
                return [item["number"] for item in _json.loads(out)]
        except Exception:
            pass
        return []

    def _fetch_comments(prd_num):
        # Routed through gh_cache — each PRD comment fetch is individually cached.
        try:
            rc, out = _health_gh_fetch(
                ["issue", "view", str(prd_num), "--json", "comments"],
                ttl=60.0, timeout=5.0,
            )
            if rc == 0 and out.strip():
                data = _json.loads(out)
                return [c.get("body", "") for c in data.get("comments", [])]
        except Exception:
            pass
        return []

    # Separator rows like "|---|---|" -- skip these
    _separator_re = re.compile(r'^\|\s*[-:]+\s*\|')

    judgment = 0
    extract_failed = 0
    total = 0
    prds_scanned = 0
    fetch_error = None

    prd_numbers = _fetch_closed_prds(_RESIDUAL_RATIO_PRD_WINDOW)
    if not prd_numbers:
        return {
            "id": "RESIDUAL-RATIO",
            "result": "WARN",
            "detail": (
                "no closed PRDs found -- cannot compute ratio; "
                "bind-forward ADR-0066 D1: ratio expected to fall after PC-EARS adoption"
            ),
            "judgment": 0, "extract_failed": 0, "total": 0, "rate": None,
        }

    for prd_num in prd_numbers:
        comments = _fetch_comments(prd_num)
        if not comments and fetch_error is None:
            fetch_error = "comment fetch failed for PRD #{} (auth or timeout)".format(prd_num)
        prd_has_plan = False
        for body in comments:
            if "## QA-plan" not in body:
                continue
            prd_has_plan = True
            # Parse the table rows within this comment.
            # Table format: | col1 | col2 | col3 |
            # Split on "|" gives: ["", col1, col2, col3, ""]
            # The second column (index 2) is the check/judgment column.
            for line in body.splitlines():
                if not line.startswith("|"):
                    continue
                if _separator_re.match(line):
                    continue
                # Skip the header row
                if "criterion #" in line.lower() or "bash check" in line.lower():
                    continue
                parts = [p.strip() for p in line.split("|")]
                if len(parts) < 4:
                    continue
                check_col = parts[2]
                if not check_col:
                    continue
                total += 1
                if "EXTRACT_FAILED" in check_col:
                    extract_failed += 1
                elif "JUDGMENT" in check_col:
                    judgment += 1
        if prd_has_plan:
            prds_scanned += 1

    if total < _RESIDUAL_RATIO_MIN_ROWS:
        detail = (
            "low-sample: {} criteria rows across {} PRDs with QA-plans "
            "(min={}); judgment={}, extract_failed={}; "
            "ratio not computed -- insufficient data for ADR-0066 D1 drop-criterion signal"
        ).format(total, prds_scanned, _RESIDUAL_RATIO_MIN_ROWS, judgment, extract_failed)
        if fetch_error:
            detail += " | note: {}".format(fetch_error)
        return {
            "id": "RESIDUAL-RATIO",
            "result": "WARN",
            "detail": detail,
            "judgment": judgment, "extract_failed": extract_failed,
            "total": total, "rate": None,
        }

    residual = judgment + extract_failed
    rate = round(residual / total, 3) if total > 0 else None
    rate_pct = "{:.0f}%".format(rate * 100) if rate is not None else "?"
    detail = (
        "{}/{} residual rows ({}) "
        "[judgment={}, extract_failed={}] "
        "across {} PRDs with QA-plans "
        "(bind-forward ADR-0066 D1: ratio should fall after PC-EARS adoption)"
    ).format(residual, total, rate_pct, judgment, extract_failed, prds_scanned)
    if fetch_error:
        detail += " | note: {}".format(fetch_error)

    # WARN always (not FAIL) -- this is a measurement row, not a blocking check.
    # The drop-criterion is a human-reviewed decision, not an automated gate.
    result = "PASS" if rate is not None and rate < 0.30 else "WARN"
    return {
        "id": "RESIDUAL-RATIO",
        "result": result,
        "detail": detail,
        "judgment": judgment, "extract_failed": extract_failed,
        "total": total, "rate": rate,
    }


def check_proof_presence() -> dict:
    """PROOF-PRESENCE: per merged non-trivial PR: route + proof-token presence.

    Classifies each PR's changed files via ADR-0061 D1 route table; greps the
    PR body + comment trail for route-appropriate proof tokens. Computes per-PR
    and rolling rate. Grandfathers PRs <= _PROOF_PRESENCE_BOOTSTRAP_PR.

    Reuses collector's fetch caching (get_trail + get_recent_merged_prs).

    Test injection: set env var _PROOF_PRESENCE_PR_OVERRIDE to a JSON list of
    full PR dicts (keys: number, headRefName, labels, files, body, comments).
    When set, both the recent-PR listing and the per-PR gh fetch are bypassed
    (mirrors check_proof_integrity's _PROOF_INTEGRITY_PR_OVERRIDE — ADR-0083 D4
    Enforcement leg 3 needs this to assert check_proof_presence's detail
    without live network).
    """
    import json as _json

    override_raw = os.environ.get("_PROOF_PRESENCE_PR_OVERRIDE", "")
    _run_gh = None
    if override_raw:
        try:
            prs_all = _json.loads(override_raw)
        except Exception as exc:
            return {"id": "PROOF-PRESENCE", "result": "WARN",
                    "detail": f"_PROOF_PRESENCE_PR_OVERRIDE parse error: {exc}",
                    "rate": None, "window": 0}
    else:
        try:
            _insert_dashboard_sys_path()
            from collector import get_recent_merged_prs  # noqa: PLC0415
            from collector import _run_gh  # noqa: PLC0415
        except Exception as exc:
            return {"id": "PROOF-PRESENCE", "result": "WARN",
                    "detail": f"collector import failed: {exc}", "rate": None, "window": 0}
        prs_all = get_recent_merged_prs(limit=_PROOF_PRESENCE_WINDOW + 5)

    # Filter trivial-lane PRs (heuristic: trivial in headRef or body)
    non_trivial = []
    for pr in prs_all:
        ref = pr.get("headRefName", "")
        labels = [lb.get("name", "") for lb in (pr.get("labels") or [])]
        if "trivial" in labels or ref.startswith("hotfix/"):
            continue
        if pr.get("number", 0) > _PROOF_PRESENCE_BOOTSTRAP_PR:
            non_trivial.append(pr)
        if len(non_trivial) >= _PROOF_PRESENCE_WINDOW:
            break

    if not non_trivial:
        return {"id": "PROOF-PRESENCE", "result": "WARN",
                "detail": f"no non-trivial merged PRs found above bootstrap threshold #{_PROOF_PRESENCE_BOOTSTRAP_PR}",
                "rate": None, "window": 0}

    with_proof = 0
    without_proof = []
    for pr in non_trivial:
        pr_num = pr.get("number", 0)
        if override_raw:
            # Override PRs already carry files/body/comments in full.
            pr_data = pr
        else:
            # Fetch changed files
            stdout, _ = _run_gh(["pr", "view", str(pr_num), "--json",
                                  "files,body,comments"], timeout=20)
            if stdout is None:
                # Cannot verify — count as present (honest: missing data != missing proof)
                with_proof += 1
                continue
            try:
                pr_data = _json.loads(stdout)
            except Exception:
                with_proof += 1
                continue
        changed_files = [f.get("path", "") for f in (pr_data.get("files") or [])]
        route_classes = _classify_route(changed_files)
        if not route_classes:
            # No recognized route → unclassifiable; skip (not a violation)
            with_proof += 1
            continue
        pr_body = pr_data.get("body", "") or ""
        comments = [c.get("body", "") for c in (pr_data.get("comments") or [])]
        unsatisfied_classes = _pr_has_proof_token(pr_body, comments, route_classes)
        if not unsatisfied_classes:
            with_proof += 1
        else:
            # ADR-0083 D4(c): name the unsatisfied class(es), not just the PR.
            without_proof.append(f"{pr_num} ({', '.join(sorted(unsatisfied_classes))})")

    total = len(non_trivial)
    rate = round(with_proof / total, 3) if total > 0 else None
    missing_str = ", ".join(without_proof) if without_proof else "none"
    detail = (
        f"{with_proof}/{total} non-trivial PRs have route-appropriate proof tokens "
        f"(bind-forward >#{ _PROOF_PRESENCE_BOOTSTRAP_PR}); missing: {missing_str}"
    )
    result = "PASS" if not without_proof else "WARN"
    return {"id": "PROOF-PRESENCE", "result": result, "detail": detail,
            "rate": rate, "window": total, "missing_prs": without_proof}


def check_merge_integrity() -> dict:
    """MERGE-INTEGRITY: BEHIND-encountered/recovered counters from PR comment trails.

    Scans the PR comment trails of recent closed PRDs for MERGE_STATUS lines
    containing 'behind-retried' (ADR-0062 D1). Honest zero when no data exists yet.
    """
    try:
        _insert_dashboard_sys_path()
        from collector import get_closed_prd_numbers, get_trail  # noqa: PLC0415
    except Exception as exc:
        return {"id": "MERGE-INTEGRITY", "result": "WARN",
                "detail": f"collector import failed: {exc}", "behind_total": 0}

    prd_numbers = get_closed_prd_numbers(10)
    behind_total = 0
    auth_dead = False
    _behind_re = re.compile(r'behind-retried:\s*(\d+)', re.IGNORECASE)

    for prd_num in prd_numbers:
        trail = get_trail(prd_num)
        if trail.get("collector_status") == "auth_dead":
            auth_dead = True
            continue
        for pr_trail in trail.get("prs", {}).values():
            for verdict in pr_trail.get("verdicts", []):
                # verdicts are parsed from comments; check raw too via any body field
                pass
            # Scan raw PR body excerpt for MERGE_STATUS
            body_exc = pr_trail.get("body_excerpt", "") or ""
            for m in _behind_re.finditer(body_exc):
                behind_total += int(m.group(1))
            for verdict in pr_trail.get("verdicts", []):
                # Verdicts don't carry raw body; best-effort via body_excerpt only
                pass

    detail = (
        f"behind-retried total: {behind_total} "
        f"(from last 10 closed PRDs; honest 0 if no BEHIND races recorded)"
    )
    if auth_dead:
        detail += " | WARNING: some PRDs skipped (auth_dead)"
    result = "WARN" if auth_dead else "PASS"
    return {"id": "MERGE-INTEGRITY", "result": result, "detail": detail,
            "behind_total": behind_total}


def check_capture_shape() -> dict:
    """CAPTURE-SHAPE: shape-conforming fraction of root-cause-labeled issue bodies.

    Checks:
    1. Fraction with all 3 headings: **Symptom:** / **Root cause:** / **Proposed:**
    2. Evidence-presence sub-metric: fraction of conforming issues with a fenced/quoted
       verbatim block in the Symptom section.
    3. Counter of 3-section-shaped captured issues missing the root-cause label
       (surfaced only, never auto-relabeled).

    Per ADR-0063 D1/D2/D3. Bind-forward: pre-ADR-0063 issues grandfathered.
    """
    import json as _json
    import subprocess as _sp

    _heading_re = re.compile(
        r'\*\*Symptom:\*\*.*?\*\*Root cause:\*\*.*?\*\*Proposed:\*\*',
        re.DOTALL,
    )
    _evidence_re = re.compile(r'```|\> ', re.MULTILINE)
    _symptom_block_re = re.compile(
        r'\*\*Symptom:\*\*(.*?)(?=\*\*Root cause:\*\*)', re.DOTALL
    )

    def _fetch_issues(label: str) -> list[dict]:
        # Routed through gh_cache — PRD #993 cr.3/cr.4, slice #996.
        try:
            rc, out = _health_gh_fetch(
                ["issue", "list", "--label", label,
                 "--state", "all", "--limit", "50",
                 "--json", "number,body,labels"],
                ttl=60.0, timeout=5.0,
            )
            if rc == 0 and out.strip():
                return _json.loads(out)
        except Exception:
            pass
        return []

    # Step 1: Check root-cause labeled issues
    root_cause_issues = _fetch_issues("root-cause")
    total_rc = len(root_cause_issues)
    conforming = []
    non_conformers = []
    evidence_present = 0

    for issue in root_cause_issues:
        body = issue.get("body", "") or ""
        num = issue.get("number")
        if _heading_re.search(body):
            conforming.append(num)
            # Check evidence in Symptom section
            sym_m = _symptom_block_re.search(body)
            if sym_m and _evidence_re.search(sym_m.group(1)):
                evidence_present += 1
        else:
            non_conformers.append(num)

    conf_rate = round(len(conforming) / total_rc, 3) if total_rc > 0 else None
    evid_rate = round(evidence_present / len(conforming), 3) if conforming else None

    # Step 2: Unlabeled-candidate counter (captured issues with 3-section shape)
    captured_issues = _fetch_issues("captured")
    unlabeled_candidates = []
    rc_numbers = {i["number"] for i in root_cause_issues}
    for issue in captured_issues:
        if issue["number"] in rc_numbers:
            continue
        body = issue.get("body", "") or ""
        if _heading_re.search(body):
            unlabeled_candidates.append(issue["number"])

    parts = []
    if total_rc == 0:
        parts.append("no root-cause-labeled issues found (bind-forward ADR-0063 D1)")
    else:
        parts.append(f"{len(conforming)}/{total_rc} conforming ({conf_rate*100:.0f}%)")
        if non_conformers:
            parts.append(f"non-conformers: #{', #'.join(str(n) for n in non_conformers)}")
        evid_str = f"{evidence_present}/{len(conforming)}" if conforming else "0/0"
        evid_pct = f" ({evid_rate*100:.0f}%)" if evid_rate is not None else ""
        parts.append(f"evidence-presence: {evid_str}{evid_pct}")
    if unlabeled_candidates:
        parts.append(f"unlabeled-candidates (surfaced only): #{', #'.join(str(n) for n in unlabeled_candidates)}")

    result = "PASS" if (not non_conformers and total_rc > 0) else "WARN"
    return {
        "id": "CAPTURE-SHAPE",
        "result": result,
        "detail": " | ".join(parts),
        "total_root_cause": total_rc,
        "conforming_count": len(conforming),
        "evidence_count": evidence_present,
        "non_conformers": non_conformers,
        "unlabeled_candidates": unlabeled_candidates,
    }


def check_green_main() -> dict:
    """GREEN-MAIN: last develop_green (or backward-compat main_green) sha + lag + age.

    Reads workflow-events.jsonl for the last 'develop_green' event (ADR-0062 D3,
    two-tier migration: slices merge to develop; green gate tracks develop HEAD).
    Falls back to 'main_green' for backward compatibility with pre-migration history
    (avoids a false-WARN window while historical logs still only carry main_green).
    lag = git rev-list <sha>..origin/develop --count
    age = seconds since the event timestamp
    Red on lag > 0 or stale > 24h.
    """
    import json as _json
    events_log = _telemetry_log_root() / ".claude" / "logs" / "workflow-events.jsonl"
    if not events_log.exists():
        return {"id": "GREEN-MAIN", "result": "WARN",
                "detail": "workflow-events.jsonl not found; no develop_green events yet"}

    last_green: dict | None = None
    last_green_compat: dict | None = None  # backward-compat main_green fallback
    try:
        with events_log.open(encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    obj = _json.loads(raw)
                except Exception:
                    continue
                if obj.get("event") == "develop_green":
                    last_green = obj
                elif obj.get("event") == "main_green":
                    last_green_compat = obj
    except Exception as exc:
        return {"id": "GREEN-MAIN", "result": "WARN",
                "detail": f"read error: {exc}"}

    # Prefer develop_green; fall back to main_green for backward compat
    event_used = "develop_green"
    if last_green is None:
        if last_green_compat is None:
            return {"id": "GREEN-MAIN", "result": "WARN",
                    "detail": "no develop_green events found in workflow-events.jsonl"}
        last_green = last_green_compat
        event_used = "main_green"

    sha = last_green.get("sha", "")
    ts_str = last_green.get("ts", "")

    # Compute lag: commits on origin/develop since the green sha
    lag = -1
    try:
        r = subprocess.run(
            ["git", "rev-list", "--count", f"{sha}..origin/develop"],
            capture_output=True, text=True, timeout=10, cwd=str(_HEALTH_REPO_ROOT),
        )
        if r.returncode == 0:
            lag = int(r.stdout.strip())
    except Exception:
        pass

    # Compute age in hours
    age_h: float | None = None
    try:
        from datetime import datetime, timezone
        ts = ts_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(ts)
        now = datetime.now(timezone.utc)
        age_h = round((now - dt).total_seconds() / 3600, 1)
    except Exception:
        pass

    sha_short = sha[:8] if sha else "?"
    age_str = f"{age_h}h ago" if age_h is not None else "age unknown"
    compat_note = f" (compat:{event_used})" if event_used == "main_green" else ""

    if lag > 0:
        result = "FAIL"
        detail = f"GREEN-MAIN lag={lag} commits behind; last green sha={sha_short} ({age_str}){compat_note}"
    elif age_h is not None and age_h > 24:
        result = "WARN"
        detail = f"GREEN-MAIN stale ({age_str}); lag=0; sha={sha_short}{compat_note}"
    else:
        result = "PASS"
        detail = f"sha={sha_short} lag=0 ({age_str}){compat_note}"

    return {"id": "GREEN-MAIN", "result": result, "detail": detail,
            "sha": sha, "lag": lag, "age_hours": age_h}


# ---------------------------------------------------------------------------
# RECORD-VS-GH — recorded pr_merged spans vs gh's merged-PR ground truth
# (PRD #1075 criterion 3, slice #1081).
# ---------------------------------------------------------------------------

# Bind-forward window anchor: the walking-skeleton merge that landed the v3
# trace emitter + pr-open/pr-merge wrappers (slice #1078). Only PRs merged
# AFTER this commit's timestamp are expected to carry a recorded pr_merged
# span — earlier merges predate the recording mechanism entirely and are
# honestly grandfathered (bootstrap-mode, ADR-0004 D2: no retroactive sweep).
_RECORD_VS_GH_ANCHOR_SHA = "0d8e6d0"

# Documented sole in-window exception: PR #1089 IS the walking-skeleton PR
# itself (the commit that first created tools/trace.py + tools/pipe/pr-open
# + tools/pipe/pr-merge). Its gh-reported mergedAt lands a few seconds AFTER
# its own commit's committer-date (verified against real data: anchor commit
# committer-date 2026-08-02T02:10:59Z vs PR #1089 mergedAt 2026-08-02T02:11:00Z)
# so a naive timestamp-only window would misclassify it as "post-window" and
# falsely FAIL it — it structurally could not emit its own span (the wrapper
# CLIs did not exist yet when #1089 merged via the prior raw-gh path). Any
# OTHER post-window merge lacking a span is a real, named FAIL.
_RECORD_VS_GH_WINDOW_EXCEPTIONS = {"1089"}


def check_record_vs_gh() -> dict:
    """RECORD-VS-GH: reconcile recorded pr_merged spans (trace-v3.jsonl) vs
    gh's merged-PR ground truth on develop (PRD #1075 criterion 3 / slice #1081).

    Ground truth: `gh pr list --base develop --state merged --json
    number,mergedAt,mergeCommit`, routed through the existing
    _health_gh_fetch/gh_cache seam (timeout-bounded; degrades honestly to
    'unverifiable — gh unavailable' rather than fabricating PASS/FAIL).

    Bind-forward window (ADR-0004 D2 grandfather): only PRs whose mergedAt is
    strictly AFTER _RECORD_VS_GH_ANCHOR_SHA's commit timestamp (the
    walking-skeleton merge, slice #1078) are expected to carry a recorded
    pr_merged span; PR #1089 (the walking-skeleton PR itself) is the
    documented sole in-window exception (see module constant above).

    Identity matching: PR number is the primary key — spans carry attrs.pr
    (a string); gh's `number` field is compared against it as a string, per
    the slice's instruction ("match on PR number primarily — spans carry
    it"). gh's mergeCommit.oid is fetched alongside but not required for
    PASS/FAIL today.

    Any post-window merged PR lacking a matching pr_merged span produces a
    named FAIL row: "PR #<n> merged <ts> has no pr_merged span".

    Returns dict with id='RECORD-VS-GH', result in {PASS, WARN, FAIL}.
    """
    import json as _json
    from datetime import datetime

    def _parse_ts(s: str):
        return datetime.fromisoformat(s.replace("Z", "+00:00"))

    # --- Step 1: resolve the bind-forward window anchor timestamp ---
    anchor_override = os.environ.get("_RECORD_VS_GH_ANCHOR_TS_OVERRIDE")
    if anchor_override:
        anchor_ts_str = anchor_override
    else:
        try:
            r = subprocess.run(
                ["git", "show", "-s", "--format=%cI", _RECORD_VS_GH_ANCHOR_SHA],
                capture_output=True, text=True, timeout=10,
                cwd=str(_HEALTH_REPO_ROOT),
            )
            anchor_ts_str = r.stdout.strip() if r.returncode == 0 else ""
        except Exception:
            anchor_ts_str = ""

    if not anchor_ts_str:
        return {
            "id": "RECORD-VS-GH", "result": "WARN",
            "detail": (
                "unverifiable — could not resolve walking-skeleton anchor "
                f"commit {_RECORD_VS_GH_ANCHOR_SHA} timestamp"
            ),
        }
    try:
        anchor_dt = _parse_ts(anchor_ts_str)
    except Exception as exc:
        return {
            "id": "RECORD-VS-GH", "result": "WARN",
            "detail": f"unverifiable — anchor timestamp unparsable: {exc}",
        }

    # --- Step 1.5: trace log existence check (root-cause fix, slice #1136) ---
    # A structurally-absent trace log (gitignored local file; ALWAYS absent
    # in a CI fresh checkout) must degrade to WARN, never fabricate a FAIL
    # naming every historical PR as "missing" at once.
    if not _v3_trace_log_exists():
        return {
            "id": "RECORD-VS-GH", "result": "WARN",
            "detail": (
                "unverifiable — trace-v3.jsonl not found in this "
                "environment (gitignored local log; always absent in CI "
                "checkouts)"
            ),
        }

    # --- Step 2: fetch merged PRs on develop, routed through gh_cache ---
    rc, out = _health_gh_fetch(
        ["pr", "list", "--base", "develop", "--state", "merged",
         "--limit", "100", "--json", "number,mergedAt,mergeCommit"],
        ttl=60.0, timeout=5.0,
    )
    if rc != 0 or not out.strip():
        return {
            "id": "RECORD-VS-GH", "result": "WARN",
            "detail": "unverifiable — gh unavailable (timeout or non-zero exit)",
        }
    try:
        prs = _json.loads(out)
    except Exception as exc:
        return {
            "id": "RECORD-VS-GH", "result": "WARN",
            "detail": f"unverifiable — gh unavailable (unparsable output: {exc})",
        }
    if not isinstance(prs, list):
        return {
            "id": "RECORD-VS-GH", "result": "WARN",
            "detail": "unverifiable — gh unavailable (unexpected output shape)",
        }

    # --- Step 3: split into pre-window (grandfathered) vs post-window ---
    post_window = []
    grandfathered = 0
    for pr in prs:
        merged_at = pr.get("mergedAt") or ""
        if not merged_at:
            continue
        try:
            merged_dt = _parse_ts(merged_at)
        except Exception:
            continue
        if merged_dt > anchor_dt:
            post_window.append(pr)
        else:
            grandfathered += 1

    # --- Step 4: read recorded pr_merged spans from the canonical trace log ---
    try:
        trace_mod = _load_trace_v3()
        spans = trace_mod.read_spans() if trace_mod is not None else []
    except Exception:
        spans = []
    recorded_prs = {
        str(s.get("attrs", {}).get("pr"))
        for s in spans
        if s.get("kind") == "pr_merged" and s.get("attrs", {}).get("pr") is not None
    }

    # --- Step 5: reconcile post-window PRs against recorded spans ---
    missing = []
    exceptions_seen = []
    for pr in sorted(post_window, key=lambda p: int(p.get("number", 0) or 0)):
        num = str(pr.get("number"))
        if num in _RECORD_VS_GH_WINDOW_EXCEPTIONS:
            exceptions_seen.append(num)
            continue
        if num not in recorded_prs:
            missing.append((num, pr.get("mergedAt", "")))

    expected = len(post_window) - len(exceptions_seen)
    covered = expected - len(missing)
    exceptions_sorted = sorted(exceptions_seen, key=int)

    if missing:
        first_num, first_ts = missing[0]
        detail = (
            f"PR #{first_num} merged {first_ts} has no pr_merged span "
            f"({covered}/{expected} post-window PRs covered since "
            f"{_RECORD_VS_GH_ANCHOR_SHA} @ {anchor_ts_str}; "
            f"missing={[n for n, _ in missing]}; grandfathered={grandfathered}; "
            f"exceptions={exceptions_sorted})"
        )
        return {
            "id": "RECORD-VS-GH", "result": "FAIL", "detail": detail,
            "missing": [n for n, _ in missing], "covered": covered,
            "expected": expected, "grandfathered": grandfathered,
            "exceptions": exceptions_sorted,
        }

    detail = (
        f"{covered}/{expected} post-window merged PRs covered (since "
        f"{_RECORD_VS_GH_ANCHOR_SHA} @ {anchor_ts_str}) have recorded pr_merged "
        f"spans; grandfathered={grandfathered} pre-window (ADR-0004 D2); "
        f"exceptions={exceptions_sorted} (documented sole in-window)"
    )
    return {
        "id": "RECORD-VS-GH", "result": "PASS", "detail": detail,
        "missing": [], "covered": covered, "expected": expected,
        "grandfathered": grandfathered, "exceptions": exceptions_sorted,
    }


# ---------------------------------------------------------------------------
# ADR-0076 reconciler family — SLICE-VS-PR, MERGED-WITHOUT-VERDICT,
# CLOSED-PRD-VS-QA (PRD #1127 §2 criterion 11b / slice #1136). All three
# share ONE bind-forward anchor per ADR-0076's binding paragraph ("every
# decision below binds FORWARD from the merge of this PRD's slice 1") --
# threaded from that paragraph, never re-derived per-check.
# ---------------------------------------------------------------------------

# The walking-skeleton dispatch-verb slice (#1129, merged as PR #1137,
# commit f271843) -- PRD #1127's own slice 1. Pre-anchor history predates
# the dispatch verb, the verdict span kind, and the closed VALID_KINDS enum
# entirely; the reconcilers below grandfather it honestly rather than
# fabricate retroactive spans (ADR-0004 D2 bootstrap-mode).
_ADR_0076_ANCHOR_SHA = "f271843"


def _resolve_adr_0076_anchor_ts():
    """Resolve the ADR-0076 bind-forward anchor's commit timestamp, honoring
    the shared _ADR_0076_ANCHOR_TS_OVERRIDE test seam (one override for all
    three reconcilers below -- they share exactly one anchor instant).

    Returns (anchor_dt_or_None, anchor_ts_str, error_detail_or_None).
    """
    from datetime import datetime as _dt

    def _parse_ts(s: str):
        return _dt.fromisoformat(s.replace("Z", "+00:00"))

    override = os.environ.get("_ADR_0076_ANCHOR_TS_OVERRIDE")
    if override:
        ts_str = override
    else:
        try:
            r = subprocess.run(
                ["git", "show", "-s", "--format=%cI", _ADR_0076_ANCHOR_SHA],
                capture_output=True, text=True, timeout=10,
                cwd=str(_HEALTH_REPO_ROOT),
            )
            ts_str = r.stdout.strip() if r.returncode == 0 else ""
        except Exception:
            ts_str = ""
    if not ts_str:
        return None, "", (
            f"could not resolve ADR-0076 anchor commit {_ADR_0076_ANCHOR_SHA} timestamp"
        )
    try:
        return _parse_ts(ts_str), ts_str, None
    except Exception as exc:
        return None, ts_str, f"anchor timestamp unparsable: {exc}"


def check_slice_vs_pr() -> dict:
    """SLICE-VS-PR: reconcile merged slice-closing PRs on develop against
    their recorded dispatch + pr_opened v3 spans (PRD #1127 §2 criterion
    11b; ADR-0076 D1 enforcement item (c) -- the #918 hand-created-slice
    class stays caught even when it routes entirely around the verbs).

    Ground truth for "is this a slice PR": GitHub's own
    closingIssuesReferences is empty for every PR here (this repo's default
    branch is main; every slice PR merges to develop, and GitHub only
    auto-populates issue-closing references against the default branch) --
    so this check parses each merged PR's own body for a `Closes #<n>`
    reference (the same regex tools/pipe/pr-open uses to derive its own
    pr_opened span's attrs.slice) and cross-checks the referenced issue
    against the set of slice-labeled issues fetched independently via
    `gh issue list --label slice`. This ground truth is INDEPENDENT of the
    trace spans being reconciled, so a PR that bypassed tools/pipe/dispatch
    or tools/pipe/pr-open entirely is still caught by it.

    Bind-forward window: only PRs merged strictly AFTER the shared
    _ADR_0076_ANCHOR_SHA commit timestamp are expected to carry
    dispatch+pr_opened spans; pre-anchor slice PRs predate the dispatch
    verb and the closed span-kind enum and are honestly grandfathered.

    For each post-anchor PR closing a slice-labeled issue #<s>: missing a
    `dispatch` span with attrs.slice == str(s), or missing a `pr_opened`
    span with attrs.pr == str(PR number), is a named FAIL.

    Returns dict with id='SLICE-VS-PR', result in {PASS, WARN, FAIL}.
    """
    import json as _json
    from datetime import datetime as _dt

    def _parse_ts(s: str):
        return _dt.fromisoformat(s.replace("Z", "+00:00"))

    anchor_dt, anchor_ts_str, anchor_err = _resolve_adr_0076_anchor_ts()
    if anchor_dt is None:
        return {"id": "SLICE-VS-PR", "result": "WARN",
                "detail": f"unverifiable — {anchor_err}"}
    # Root-cause fix (slice #1136, discovered wiring RECORD-VS-GH into CI):
    # a structurally-absent trace log (gitignored local file; CI checkouts
    # never have it) must degrade to WARN, never fabricate a FAIL against
    # every historical slice PR at once.
    if not _v3_trace_log_exists():
        return {"id": "SLICE-VS-PR", "result": "WARN",
                "detail": (
                    "unverifiable — trace-v3.jsonl not found in this "
                    "environment (gitignored local log; always absent in "
                    "CI checkouts)"
                )}

    rc, out = _health_gh_fetch(
        ["pr", "list", "--base", "develop", "--state", "merged",
         "--limit", "100", "--json", "number,mergedAt,body"],
        ttl=60.0, timeout=5.0,
    )
    if rc != 0 or not out.strip():
        return {"id": "SLICE-VS-PR", "result": "WARN",
                "detail": "unverifiable — gh unavailable (timeout or non-zero exit)"}
    try:
        prs = _json.loads(out)
    except Exception as exc:
        return {"id": "SLICE-VS-PR", "result": "WARN",
                "detail": f"unverifiable — gh unavailable (unparsable output: {exc})"}
    if not isinstance(prs, list):
        return {"id": "SLICE-VS-PR", "result": "WARN",
                "detail": "unverifiable — gh unavailable (unexpected output shape)"}

    rc2, out2 = _health_gh_fetch(
        ["issue", "list", "--label", "slice", "--state", "all",
         "--limit", "500", "--json", "number"],
        ttl=60.0, timeout=5.0,
    )
    if rc2 != 0 or not out2.strip():
        return {"id": "SLICE-VS-PR", "result": "WARN",
                "detail": "unverifiable — gh unavailable fetching slice-labeled issues"}
    try:
        slice_issues = _json.loads(out2)
    except Exception as exc:
        return {"id": "SLICE-VS-PR", "result": "WARN",
                "detail": f"unverifiable — slice-issue list unparsable: {exc}"}
    if not isinstance(slice_issues, list):
        return {"id": "SLICE-VS-PR", "result": "WARN",
                "detail": "unverifiable — slice-issue list unexpected shape"}
    slice_numbers = {
        str(i.get("number")) for i in slice_issues if isinstance(i, dict)
    }

    try:
        trace_mod = _load_trace_v3()
        spans = trace_mod.read_spans() if trace_mod is not None else []
    except Exception:
        spans = []
    dispatch_slices = {
        str(s.get("attrs", {}).get("slice"))
        for s in spans
        if s.get("kind") == "dispatch" and s.get("attrs", {}).get("slice") is not None
    }
    pr_opened_prs = {
        str(s.get("attrs", {}).get("pr"))
        for s in spans
        if s.get("kind") == "pr_opened" and s.get("attrs", {}).get("pr") is not None
    }

    close_re = re.compile(r"(?:closes|fixes|resolves)\s+#(\d+)", re.IGNORECASE)

    post_window = []
    grandfathered = 0
    for pr in prs:
        merged_at = pr.get("mergedAt") or ""
        if not merged_at:
            continue
        try:
            merged_dt = _parse_ts(merged_at)
        except Exception:
            continue
        if merged_dt > anchor_dt:
            post_window.append(pr)
        else:
            grandfathered += 1

    checked = 0
    missing = []
    for pr in sorted(post_window, key=lambda p: int(p.get("number", 0) or 0)):
        num = str(pr.get("number"))
        closes = close_re.findall(pr.get("body") or "")
        slice_closed = [c for c in closes if c in slice_numbers]
        if not slice_closed:
            continue  # not a slice-closing PR (PRD-tier/trivial/docs) -- out of scope
        checked += 1
        reasons = []
        for s in slice_closed:
            if s not in dispatch_slices:
                reasons.append(f"no dispatch span for slice #{s}")
        if num not in pr_opened_prs:
            reasons.append(f"no pr_opened span for PR #{num}")
        if reasons:
            missing.append((num, slice_closed, pr.get("mergedAt", ""), "; ".join(reasons)))

    if missing:
        first_pr, first_slices, first_ts, first_reason = missing[0]
        detail = (
            f"PR #{first_pr} (closes slice #{','.join(first_slices)}) merged "
            f"{first_ts} {first_reason} ({checked - len(missing)}/{checked} "
            f"post-anchor slice PRs covered since {_ADR_0076_ANCHOR_SHA} @ "
            f"{anchor_ts_str}; grandfathered={grandfathered})"
        )
        return {
            "id": "SLICE-VS-PR", "result": "FAIL", "detail": detail,
            "missing": [f"{p}(slice #{','.join(s)})" for p, s, _, _ in missing],
            "checked": checked, "grandfathered": grandfathered,
        }

    detail = (
        f"{checked}/{checked} post-anchor slice-closing PRs covered (since "
        f"{_ADR_0076_ANCHOR_SHA} @ {anchor_ts_str}); grandfathered="
        f"{grandfathered} pre-anchor (ADR-0004 D2 bootstrap-mode)"
    )
    return {
        "id": "SLICE-VS-PR", "result": "PASS", "detail": detail,
        "missing": [], "checked": checked, "grandfathered": grandfathered,
    }


def check_merged_without_verdict() -> dict:
    """MERGED-WITHOUT-VERDICT: every PR merged to develop after the ADR-0076
    bind-forward anchor MUST carry a recorded `verdict` v3 span (PRD #1127
    §2 criterion 11b; ADR-0076 D3's merge-time reviewer-verdict assertion).

    tools/pipe/pr-merge only started emitting `verdict` spans once slice
    #1130 (this PRD's own pr-merge verdict-floor extension) landed -- ITSELF
    after this PRD's slice-1 anchor. A post-anchor PR merged before slice
    #1130 landed therefore genuinely has no verdict span; this reconciler
    reports that honestly as a named FAIL (a real, permanent gap in the
    recorded chain) rather than quietly widening its own window to hide it
    -- ADR-0076's binding paragraph is explicit that the gap is named, not
    hidden.

    Ground truth: `gh pr list --base develop --state merged --json
    number,mergedAt` (same shape as RECORD-VS-GH). Spans: recorded
    `verdict`-kind spans in the canonical v3 trace log, matched on attrs.pr
    (string).

    Returns dict with id='MERGED-WITHOUT-VERDICT', result in {PASS, WARN, FAIL}.
    """
    import json as _json
    from datetime import datetime as _dt

    def _parse_ts(s: str):
        return _dt.fromisoformat(s.replace("Z", "+00:00"))

    anchor_dt, anchor_ts_str, anchor_err = _resolve_adr_0076_anchor_ts()
    if anchor_dt is None:
        return {"id": "MERGED-WITHOUT-VERDICT", "result": "WARN",
                "detail": f"unverifiable — {anchor_err}"}
    # Root-cause fix (slice #1136, discovered wiring RECORD-VS-GH into CI):
    # a structurally-absent trace log (gitignored local file; CI checkouts
    # never have it) must degrade to WARN, never fabricate a FAIL against
    # every historical merged PR at once.
    if not _v3_trace_log_exists():
        return {"id": "MERGED-WITHOUT-VERDICT", "result": "WARN",
                "detail": (
                    "unverifiable — trace-v3.jsonl not found in this "
                    "environment (gitignored local log; always absent in "
                    "CI checkouts)"
                )}

    rc, out = _health_gh_fetch(
        ["pr", "list", "--base", "develop", "--state", "merged",
         "--limit", "100", "--json", "number,mergedAt"],
        ttl=60.0, timeout=5.0,
    )
    if rc != 0 or not out.strip():
        return {"id": "MERGED-WITHOUT-VERDICT", "result": "WARN",
                "detail": "unverifiable — gh unavailable (timeout or non-zero exit)"}
    try:
        prs = _json.loads(out)
    except Exception as exc:
        return {"id": "MERGED-WITHOUT-VERDICT", "result": "WARN",
                "detail": f"unverifiable — gh unavailable (unparsable output: {exc})"}
    if not isinstance(prs, list):
        return {"id": "MERGED-WITHOUT-VERDICT", "result": "WARN",
                "detail": "unverifiable — gh unavailable (unexpected output shape)"}

    try:
        trace_mod = _load_trace_v3()
        spans = trace_mod.read_spans() if trace_mod is not None else []
    except Exception:
        spans = []
    verdict_prs = {
        str(s.get("attrs", {}).get("pr"))
        for s in spans
        if s.get("kind") == "verdict" and s.get("attrs", {}).get("pr") is not None
    }

    post_window = []
    grandfathered = 0
    for pr in prs:
        merged_at = pr.get("mergedAt") or ""
        if not merged_at:
            continue
        try:
            merged_dt = _parse_ts(merged_at)
        except Exception:
            continue
        if merged_dt > anchor_dt:
            post_window.append(pr)
        else:
            grandfathered += 1

    missing = []
    for pr in sorted(post_window, key=lambda p: int(p.get("number", 0) or 0)):
        num = str(pr.get("number"))
        if num not in verdict_prs:
            missing.append((num, pr.get("mergedAt", "")))

    expected = len(post_window)
    covered = expected - len(missing)

    if missing:
        first_num, first_ts = missing[0]
        detail = (
            f"PR #{first_num} merged {first_ts} has no verdict span "
            f"({covered}/{expected} post-anchor merges covered since "
            f"{_ADR_0076_ANCHOR_SHA} @ {anchor_ts_str}; missing="
            f"{[n for n, _ in missing]}; grandfathered={grandfathered})"
        )
        return {
            "id": "MERGED-WITHOUT-VERDICT", "result": "FAIL", "detail": detail,
            "missing": [n for n, _ in missing], "covered": covered,
            "expected": expected, "grandfathered": grandfathered,
        }

    detail = (
        f"{covered}/{expected} post-anchor merged PRs covered (since "
        f"{_ADR_0076_ANCHOR_SHA} @ {anchor_ts_str}) have recorded verdict "
        f"spans; grandfathered={grandfathered} pre-anchor (ADR-0004 D2)"
    )
    return {
        "id": "MERGED-WITHOUT-VERDICT", "result": "PASS", "detail": detail,
        "missing": [], "covered": covered, "expected": expected,
        "grandfathered": grandfathered,
    }


def check_closed_prd_vs_qa() -> dict:
    """CLOSED-PRD-VS-QA: every prd-labeled issue closed after the ADR-0076
    bind-forward anchor MUST carry a recorded `qa_verified` PASS v3 span
    (PRD #1127 §2 criterion 11b; the production-verification gate, ADR-0037
    D1, cross-checked at the recorded-evidence layer).

    Matching predicate mirrors tools/pipe/prd-close's own precondition check
    (_qa_verified_pass_exists) exactly, rather than re-deriving it: a
    qa_verified span whose attrs.prd == str(prd_number) AND attrs.verdict ==
    'PASS'.

    Ground truth: `gh issue list --label prd --state closed --json
    number,closedAt`.

    Bind-forward window: only PRDs closed strictly AFTER the shared
    _ADR_0076_ANCHOR_SHA commit timestamp are expected to carry a
    qa_verified span (the qa-verify wrapper + prd-close verb both post-date
    this PRD's own slice-1 anchor); pre-anchor closures are honestly
    grandfathered.

    Returns dict with id='CLOSED-PRD-VS-QA', result in {PASS, WARN, FAIL}.
    """
    import json as _json
    from datetime import datetime as _dt

    def _parse_ts(s: str):
        return _dt.fromisoformat(s.replace("Z", "+00:00"))

    anchor_dt, anchor_ts_str, anchor_err = _resolve_adr_0076_anchor_ts()
    if anchor_dt is None:
        return {"id": "CLOSED-PRD-VS-QA", "result": "WARN",
                "detail": f"unverifiable — {anchor_err}"}
    # Root-cause fix (slice #1136, discovered wiring RECORD-VS-GH into CI):
    # a structurally-absent trace log (gitignored local file; CI checkouts
    # never have it) must degrade to WARN, never fabricate a FAIL against
    # every historical closed PRD at once.
    if not _v3_trace_log_exists():
        return {"id": "CLOSED-PRD-VS-QA", "result": "WARN",
                "detail": (
                    "unverifiable — trace-v3.jsonl not found in this "
                    "environment (gitignored local log; always absent in "
                    "CI checkouts)"
                )}

    rc, out = _health_gh_fetch(
        ["issue", "list", "--label", "prd", "--state", "closed",
         "--limit", "200", "--json", "number,closedAt"],
        ttl=60.0, timeout=5.0,
    )
    if rc != 0 or not out.strip():
        return {"id": "CLOSED-PRD-VS-QA", "result": "WARN",
                "detail": "unverifiable — gh unavailable (timeout or non-zero exit)"}
    try:
        prds = _json.loads(out)
    except Exception as exc:
        return {"id": "CLOSED-PRD-VS-QA", "result": "WARN",
                "detail": f"unverifiable — gh unavailable (unparsable output: {exc})"}
    if not isinstance(prds, list):
        return {"id": "CLOSED-PRD-VS-QA", "result": "WARN",
                "detail": "unverifiable — gh unavailable (unexpected output shape)"}

    try:
        trace_mod = _load_trace_v3()
        spans = trace_mod.read_spans() if trace_mod is not None else []
    except Exception:
        spans = []
    # Same predicate as tools/pipe/prd-close's own _qa_verified_pass_exists
    # precondition -- reused here, not re-derived.
    qa_pass_prds = {
        str(s.get("attrs", {}).get("prd"))
        for s in spans
        if s.get("kind") == "qa_verified"
        and s.get("attrs", {}).get("verdict") == "PASS"
        and s.get("attrs", {}).get("prd") is not None
    }

    post_window = []
    grandfathered = 0
    for prd in prds:
        closed_at = prd.get("closedAt") or ""
        if not closed_at:
            continue
        try:
            closed_dt = _parse_ts(closed_at)
        except Exception:
            continue
        if closed_dt > anchor_dt:
            post_window.append(prd)
        else:
            grandfathered += 1

    missing = []
    for prd in sorted(post_window, key=lambda p: int(p.get("number", 0) or 0)):
        num = str(prd.get("number"))
        if num not in qa_pass_prds:
            missing.append((num, prd.get("closedAt", "")))

    expected = len(post_window)
    covered = expected - len(missing)

    if missing:
        first_num, first_ts = missing[0]
        detail = (
            f"PRD #{first_num} closed {first_ts} has no qa_verified PASS "
            f"span ({covered}/{expected} post-anchor closed PRDs covered "
            f"since {_ADR_0076_ANCHOR_SHA} @ {anchor_ts_str}; missing="
            f"{[n for n, _ in missing]}; grandfathered={grandfathered})"
        )
        return {
            "id": "CLOSED-PRD-VS-QA", "result": "FAIL", "detail": detail,
            "missing": [n for n, _ in missing], "covered": covered,
            "expected": expected, "grandfathered": grandfathered,
        }

    detail = (
        f"{covered}/{expected} post-anchor closed prd-labeled issues covered "
        f"(since {_ADR_0076_ANCHOR_SHA} @ {anchor_ts_str}) have a "
        f"qa_verified PASS span; grandfathered={grandfathered} pre-anchor "
        f"(ADR-0004 D2)"
    )
    return {
        "id": "CLOSED-PRD-VS-QA", "result": "PASS", "detail": detail,
        "missing": [], "covered": covered, "expected": expected,
        "grandfathered": grandfathered,
    }


def check_silent_drift() -> dict:
    """SILENT-DRIFT: count PRDs whose body changed post-first-dispatch without an AMENDMENT comment.

    Algorithm (ADR-0066 D3):
    1. Fetch closed + open PRDs (label=prd) via gh issue list.
    2. For each PRD, determine whether a first implementer dispatch has occurred:
       look for the earliest PR comment whose body contains 'implementer' or
       check for any sub-issue (slice) with a closed PR linked via 'Closes #'.
       Heuristic: a PRD is "first-dispatched" when it has ≥1 slice-labeled sub-issue.
    3. For each first-dispatched PRD, check GitHub edit history via
       gh api repos/{owner}/{repo}/issues/{n} (the `updated_at` vs `created_at`
       difference is a proxy; authoritative edit history requires
       gh api /repos/{owner}/{repo}/issues/{n}/timeline which may need extra auth).
    4. Count PRDs where body may have drifted without a matching ## AMENDMENT comment.

    Honest grandfathering (ADR-0004 D2): PRDs created before this check's merge
    (first-merge commit of feat/799-amendment-protocol) cannot be retroactively
    audited — they land in a 'grandfathered' bucket and are excluded from the
    violation count.

    API availability note: GitHub's issue edit history endpoint
    (GET /repos/{owner}/{repo}/issues/{n}/timeline, event='edited') requires
    the `application/vnd.github+json` Accept header and returns edit events only
    when the edit occurred after the PR/issue was indexed. Rate limits and auth
    scope (requires `issues` scope) may block this. Graceful WARN fallback when
    the API is unavailable or rate-limited — the row will show WARN with a
    documented fallback rather than fabricating a value.

    Target: 0 violations (PASS). Any violations: WARN with count + PRD numbers.
    Grandfathered PRDs: always excluded (honest per bootstrap-mode ADR-0004 D2).
    """
    import json as _json
    import subprocess as _sp

    # --- Bootstrap cutoff: the merge commit of feat/799-amendment-protocol ---
    # PRDs created before this slice's merge cannot be audited via edit history
    # (the protocol binds forward from this merge per ADR-0066 D3 + ADR-0004 D2).
    # We use the slice issue number (799) as a proxy: PRDs with issue number < 799
    # are grandfathered. This is approximate but honest and conservative.
    _GRANDFATHERED_BELOW = 799

    def _gh_json(args: list, timeout: int = 20) -> list | dict | None:
        # Routed through gh_cache (ttl=60s, timeout=5s) — PRD #993 cr.3, slice #996.
        try:
            rc, out = _health_gh_fetch(args, ttl=60.0, timeout=5.0)
            if rc != 0 or not out.strip():
                return None
            return _json.loads(out)
        except Exception:
            return None

    # --- Step 1: fetch PRDs ---
    prd_issues = _gh_json([
        "issue", "list", "--label", "prd",
        "--state", "all", "--limit", "50",
        "--json", "number,body,createdAt,updatedAt,comments",
    ])
    if prd_issues is None:
        return {
            "id": "SILENT-DRIFT",
            "result": "WARN",
            "detail": (
                "GitHub API unavailable (auth or rate-limit); "
                "edit-history check skipped. "
                "Fallback: run `gh issue list --label prd` manually and inspect "
                "body edit dates against AMENDMENT comments. "
                "Per ADR-0066 D3 honest-fallback design."
            ),
            "violations": 0,
            "grandfathered": 0,
            "api_available": False,
        }

    violations = []
    grandfathered = []
    auditable_prd_count = 0

    for prd in prd_issues:
        prd_num = prd.get("number", 0)
        created_at = prd.get("createdAt", "")
        updated_at = prd.get("updatedAt", "")
        comments = prd.get("comments", []) or []

        # Grandfathering: PRDs with number < bootstrap cutoff
        if prd_num < _GRANDFATHERED_BELOW:
            grandfathered.append(prd_num)
            continue

        # Check if PRD has been first-dispatched:
        # proxy = has any slice sub-issue (implementer dispatch creates at least 1 slice)
        # We check via sub-issues by looking for slice-labeled issues mentioning this PRD.
        # Simpler heuristic: if updatedAt != createdAt the body MAY have been edited.
        if created_at == updated_at:
            # No updates at all — cannot have drifted
            continue

        auditable_prd_count += 1

        # Check for AMENDMENT comments
        amendment_count = sum(
            1 for c in comments
            if (c.get("body") or "").strip().startswith("## AMENDMENT")
        )

        # Try to get edit history via timeline API
        timeline = _gh_json([
            "api", f"repos/{{owner}}/{{repo}}/issues/{prd_num}/timeline",
            "--paginate", "--jq", "[.[] | select(.event==\"edited\")]",
        ], timeout=15)

        if timeline is None:
            # API unavailable for this PRD — use updatedAt proxy
            # Conservative: if body may have been edited (updated_at != created_at)
            # and no AMENDMENT comment exists, flag as potential violation
            # but only WARN, never fabricate
            if amendment_count == 0:
                violations.append({
                    "prd": prd_num,
                    "reason": "body updated post-creation; no AMENDMENT comment; edit-history API unavailable (proxy only)",
                })
            continue

        # Timeline available — check for 'edited' events after first dispatch
        edit_events = timeline if isinstance(timeline, list) else []
        if edit_events and amendment_count == 0:
            violations.append({
                "prd": prd_num,
                "reason": f"{len(edit_events)} body edit event(s) detected; 0 AMENDMENT comments",
            })

    violation_nums = [v["prd"] for v in violations]
    gran_count = len(grandfathered)

    if not violations:
        detail = (
            f"0 violations ({auditable_prd_count} auditable post-bootstrap PRDs; "
            f"{gran_count} grandfathered pre-#{_GRANDFATHERED_BELOW})"
        )
        result = "PASS"
    else:
        viol_str = ", ".join(f"#{n}" for n in violation_nums)
        detail = (
            f"{len(violations)} violation(s): {viol_str} — "
            f"body updated without AMENDMENT comment "
            f"({auditable_prd_count} auditable; {gran_count} grandfathered pre-#{_GRANDFATHERED_BELOW})"
        )
        result = "WARN"

    return {
        "id": "SILENT-DRIFT",
        "result": result,
        "detail": detail,
        "violations": len(violations),
        "violation_prds": violation_nums,
        "grandfathered": gran_count,
        "api_available": True,
    }


# ---------------------------------------------------------------------------
# TESTS-COLLECTED — regression suite collected-count row (ADR-0067 D1)
# ---------------------------------------------------------------------------


def check_tests_collected() -> dict:
    """TESTS-COLLECTED: count of test items collected in tests/.

    Implements ADR-0067 D1 — the founding memory row: reports how many tests
    are collected in the tests/ suite. PASS when count > 0 (the suite exists
    and is non-empty). FAIL when tests/ exists but no tests are collected.
    WARN when tests/ does not exist.

    Prefers pytest --collect-only -q when pytest is importable; falls back to
    stdlib unittest discovery (python -m unittest discover --collect-only or
    a manual loader) so the health row works on any standard Python install.
    Bind-forward per ADR-0004 D2: pre-suite repos honestly report WARN.
    """
    tests_dir = _HEALTH_REPO_ROOT / "tests"
    if not tests_dir.exists():
        return {
            "id": "TESTS-COLLECTED",
            "result": "WARN",
            "detail": "tests/ directory does not exist (pre-suite: bind-forward ADR-0067 D1)",
        }

    # --- Try pytest first (optional dependency) ---
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", str(tests_dir),
             "--collect-only", "-q", "--no-header"],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(_HEALTH_REPO_ROOT),
        )
        # pytest available — parse output.
        output = (result.stdout or "") + (result.stderr or "")
        # Lines containing "::" are collected test IDs.
        collected_lines = [
            line for line in output.splitlines()
            if "::" in line and not line.startswith("=") and not line.startswith("-")
        ]
        count = len(collected_lines)
        if count == 0:
            import re as _re
            m = _re.search(r'(\d+)\s+(?:test|item)', output)
            if m:
                count = int(m.group(1))

        if count > 0:
            return {
                "id": "TESTS-COLLECTED",
                "result": "PASS",
                "detail": f"{count} test(s) collected in tests/ via pytest (ADR-0067 D1)",
                "count": count,
            }
        else:
            return {
                "id": "TESTS-COLLECTED",
                "result": "FAIL",
                "detail": (
                    "tests/ exists but 0 tests collected via pytest — "
                    "suite must stay non-empty per ADR-0067 D1"
                ),
                "count": 0,
            }

    except FileNotFoundError:
        # pytest not installed — fall through to stdlib unittest discovery.
        pass
    except Exception as exc:
        # Unexpected error from pytest subprocess — fall through.
        _ = exc  # log if needed; continue to stdlib fallback

    # --- Stdlib unittest fallback (no pytest required) ---
    # Use unittest's TestLoader to discover and count tests without running them.
    try:
        import unittest as _unittest
        loader = _unittest.TestLoader()
        suite = loader.discover(str(tests_dir), pattern="test_*.py",
                                top_level_dir=str(_HEALTH_REPO_ROOT))

        def _count_tests(s) -> int:
            """Recursively count leaf TestCase instances in a suite."""
            total = 0
            for item in s:
                if hasattr(item, "__iter__"):
                    total += _count_tests(item)
                else:
                    total += 1
            return total

        count = _count_tests(suite)
        if count > 0:
            return {
                "id": "TESTS-COLLECTED",
                "result": "PASS",
                "detail": f"{count} test(s) collected in tests/ via stdlib unittest (ADR-0067 D1)",
                "count": count,
            }
        else:
            return {
                "id": "TESTS-COLLECTED",
                "result": "FAIL",
                "detail": (
                    "tests/ exists but 0 tests collected via stdlib unittest — "
                    "suite must stay non-empty per ADR-0067 D1"
                ),
                "count": 0,
            }
    except Exception as exc:
        return {
            "id": "TESTS-COLLECTED",
            "result": "WARN",
            "detail": f"test collection failed (pytest unavailable, stdlib discovery error): {exc}",
        }


# ---------------------------------------------------------------------------
# TEST-ORDERING — fix-type PR test-commit-precedes-fix-commit rate (ADR-0067 D2)
# ---------------------------------------------------------------------------


def _test_ordering_classify_commits(commit_shas, sp_module, repo_root):
    """Classify an ordered commit-sha sequence for test-before-fix ordering.

    Returns (first_test_idx, first_fix_idx) — same file-touch classification
    logic used by both the direct-history path and the gh-aware squash path
    (slice #1060). A commit "touches_tests" if any changed file starts with
    tests/; "touches_non_tests" if any changed file does not. A commit that
    touches BOTH counts only toward first_test_idx (mirrors pre-#1060
    behavior for mixed commits) so classification stays stable regardless
    of source (direct history vs gh PR commit list).

    If a commit's diff-tree call fails, that commit is skipped for
    classification purposes; the caller (squash path) verifies commit-oid
    reachability separately (git cat-file) BEFORE calling this helper, so
    an unreachable oid never silently degrades to "no test commit found".
    """
    first_test_idx = None
    first_fix_idx = None
    for idx, sha in enumerate(commit_shas):
        try:
            files_result = sp_module.run(
                ["git", "diff-tree", "--no-commit-id", "-r", "--name-only", sha],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=10,
                cwd=str(repo_root),
            )
            changed = files_result.stdout.splitlines()
        except Exception:
            continue
        touches_tests = any(f.startswith("tests/") for f in changed)
        touches_non_tests = any(not f.startswith("tests/") for f in changed)
        if touches_tests and first_test_idx is None:
            first_test_idx = idx
        if touches_non_tests and not touches_tests and first_fix_idx is None:
            first_fix_idx = idx
    return first_test_idx, first_fix_idx


def check_test_ordering() -> dict:
    """TEST-ORDERING: % of fix-type PRs where test commit precedes fix commit.

    Implements ADR-0067 D2 — bias isolation as git-history sequencing.
    A fix-type PR is one whose branch name matches fix/* (merged or open).

    Algorithm:
    1. Fetch recently merged PRs whose headRefName starts with fix/.
    2. Squash-merge detection (slice #1060 / ADR-0042 D3): the pipeline's
       ONLY merge mode is squash-merge, which collapses a PR's test+fix
       commits into ONE develop commit. A merge commit with exactly one
       parent is a squash (a merge-preserving strategy would have two).
       For such PRs, fetch the PR's ORIGINAL branch commit list via
       `gh pr view N --json commits` (routed through the health gh_cache
       seam) and evaluate ordering on THAT sequence — using the same
       file-touch classification logic — instead of the collapsed
       develop-history commit. This also avoids the direct-history range
       walk picking up unrelated sibling PRs' commits (root-cause of the
       false-negatives on PRs 1045/1047/1049/1051/1055/1058).
       Degrade: if gh is unavailable, or a commit oid gh reports is no
       longer reachable locally, the PR is bucketed 'unverifiable' — never
       silently ordered, never falsely disordered.
    3. Non-squash / single-commit PRs keep the existing direct-history path
       (`git log --name-only` over the PR's merge-commit range).
    4. Report: ordered/<total> with honest grandfathered + unverifiable
       buckets for PRs merged before this check's activation
       (pre-ADR-0067-D2) or whose ordering could not be determined.

    Honest grandfathering (ADR-0004 D2): fix-type PRs merged before the
    R-PROVE reviewer rule merge cannot be held to the ordering standard.
    We grandfather all fix/* PRs with merge number < the R-PROVE slice
    (issue #816). PASS = 100% of post-activation, verifiable PRs conform,
    or no post-activation PRs yet (WARN).
    """
    import json as _json
    import subprocess as _sp

    _GRANDFATHERED_BELOW = 816  # PRs linked to slices < #816 are pre-activation

    def _gh_json(args: list, timeout: int = 20):
        # Routed through gh_cache (ttl=60s, timeout=5s) — PRD #993 cr.3, slice #996.
        try:
            rc, out = _health_gh_fetch(args, ttl=60.0, timeout=5.0)
            if rc != 0 or not out.strip():
                return None
            return _json.loads(out)
        except Exception:
            return None

    # Fetch merged PRs with fix/* head branch
    prs = _gh_json([
        "pr", "list",
        "--state", "merged",
        "--limit", "30",
        "--json", "number,headRefName,mergeCommit,closingIssuesReferences",
    ])
    if prs is None:
        return {
            "id": "TEST-ORDERING",
            "result": "WARN",
            "detail": "GitHub API unavailable; honest fallback — run manually",
            "ordered": 0,
            "total": 0,
            "grandfathered": 0,
            "api_available": False,
        }

    fix_prs = [p for p in prs if (p.get("headRefName") or "").startswith("fix/")]

    grandfathered_count = 0
    ordered = 0
    disordered = []
    unverifiable = []
    post_activation = []

    for pr in fix_prs:
        pr_num = pr.get("number", 0)
        # Grandfather: check if closing slice issue < 816
        closing = pr.get("closingIssuesReferences") or []
        slice_nums = [i.get("number", 0) for i in closing if isinstance(i, dict)]
        is_grandfathered = all(n < _GRANDFATHERED_BELOW for n in slice_nums) if slice_nums else (pr_num < _GRANDFATHERED_BELOW)
        if is_grandfathered:
            grandfathered_count += 1
            continue

        post_activation.append(pr_num)

        # Check ordering: does a test commit precede fix commit?
        merge_commit = (pr.get("mergeCommit") or {}).get("oid", "")
        if not merge_commit:
            # Cannot check — treat as WARN (not FAIL); count but mark unknown
            continue

        # Squash-merge detection: a merge commit with exactly one parent is
        # a squash (a merge-preserving strategy produces two parents).
        is_squash = False
        try:
            parents_result = _sp.run(
                ["git", "show", "-s", "--format=%P", merge_commit],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=10,
                cwd=str(_HEALTH_REPO_ROOT),
            )
            parent_shas = parents_result.stdout.split()
            is_squash = len(parent_shas) <= 1
        except Exception:
            is_squash = False

        commit_shas = None
        squash_unverifiable = False

        if is_squash:
            # gh-aware path: read the PR's ORIGINAL branch commit list.
            gh_commits = _gh_json(["pr", "view", str(pr_num), "--json", "commits"])
            if gh_commits is None or not gh_commits.get("commits"):
                squash_unverifiable = True
            else:
                gh_commit_list = gh_commits["commits"]
                if len(gh_commit_list) <= 1:
                    # Single-commit PR — squash collapse is a no-op; direct
                    # history path below already covers it correctly.
                    is_squash = False
                else:
                    oids = [c.get("oid", "") for c in gh_commit_list]
                    # Verify every commit object is still reachable locally
                    # (gh retains the metadata even after squash, but the
                    # underlying blobs could theoretically be GC'd).
                    all_reachable = True
                    for oid in oids:
                        if not oid:
                            all_reachable = False
                            break
                        try:
                            check_result = _sp.run(
                                ["git", "cat-file", "-t", oid],
                                capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=10,
                                cwd=str(_HEALTH_REPO_ROOT),
                            )
                            if check_result.returncode != 0:
                                all_reachable = False
                                break
                        except Exception:
                            all_reachable = False
                            break
                    if all_reachable:
                        commit_shas = oids
                    else:
                        squash_unverifiable = True

        if squash_unverifiable:
            unverifiable.append(pr_num)
            continue

        if commit_shas is None:
            # Direct-history path (non-squash, or single-commit PR).
            try:
                result = _sp.run(
                    ["git", "log", "--reverse", "--pretty=%H",
                     f"origin/main...{merge_commit}", "--"],
                    capture_output=True, text=True, encoding="utf-8",
                    errors="replace", timeout=15,
                    cwd=str(_HEALTH_REPO_ROOT),
                )
                commit_shas = [l.strip() for l in result.stdout.splitlines() if l.strip()]
            except Exception:
                continue

        if not commit_shas:
            continue

        first_test_idx, first_fix_idx = _test_ordering_classify_commits(
            commit_shas, _sp, _HEALTH_REPO_ROOT,
        )

        if first_test_idx is not None and first_fix_idx is not None:
            if first_test_idx < first_fix_idx:
                ordered += 1
            else:
                disordered.append(pr_num)
        elif first_test_idx is not None:
            # Only test commits — counts as ordered (no fix commit yet / docs-only fix)
            ordered += 1
        # else: no test commit found — counts as disordered (pre-existing:
        # no explicit bucket append here; out of scope for slice #1060,
        # which addresses squash-merge misdetection only).

    total_post = len(post_activation)
    verifiable_total = total_post - len(unverifiable)
    if total_post == 0:
        result_val = "WARN"
        detail = (
            f"no post-activation fix/* PRs found "
            f"(grandfathered: {grandfathered_count}; bind-forward ADR-0067 D2)"
        )
    elif disordered:
        result_val = "WARN"
        detail = (
            f"{ordered}/{verifiable_total} ordered "
            f"(grandfathered: {grandfathered_count}; "
            f"unverifiable: {unverifiable}; "
            f"disordered PRs: {disordered})"
        )
    else:
        result_val = "PASS"
        detail = (
            f"{ordered}/{verifiable_total} fix-type PRs have test-first ordering "
            f"(grandfathered pre-ADR-0067-D2: {grandfathered_count}; "
            f"unverifiable: {unverifiable})"
        )

    return {
        "id": "TEST-ORDERING",
        "result": result_val,
        "detail": detail,
        "ordered": ordered,
        "total": total_post,
        "grandfathered": grandfathered_count,
        "disordered": disordered,
        "unverifiable": unverifiable,
    }


# ---------------------------------------------------------------------------
# QUARANTINE-SLA — quarantine register size + oldest-entry age (ADR-0067 D4)
# ---------------------------------------------------------------------------


def check_quarantine_sla() -> dict:
    """QUARANTINE-SLA: quarantine register size + oldest-entry age.

    Implements ADR-0067 D4 — flaky quarantine with SLA.
    Reads tests/quarantine.txt (blank lines and #-comment lines ignored).
    Entries must carry a [quarantined: YYYY-MM-DD] tag for age tracking.

    PASS: 0 entries, or all entries within 30-day SLA.
    WARN: entries exist but none breach the 30-day SLA.
    FAIL: at least one entry is older than 30 days (SLA breach).
    """
    import datetime as _dt

    quarantine_file = _HEALTH_REPO_ROOT / "tests" / "quarantine.txt"
    if not quarantine_file.exists():
        return {
            "id": "QUARANTINE-SLA",
            "result": "WARN",
            "detail": "tests/quarantine.txt not found (pre-suite: bind-forward ADR-0067 D4)",
            "size": 0,
            "oldest_days": None,
        }

    text = _read_file(quarantine_file)
    # Active entries: non-blank, non-comment lines
    active_lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    size = len(active_lines)

    if size == 0:
        return {
            "id": "QUARANTINE-SLA",
            "result": "PASS",
            "detail": "quarantine register is empty (no quarantined tests)",
            "size": 0,
            "oldest_days": None,
        }

    # Parse [quarantined: YYYY-MM-DD] tags for age check
    _date_re = re.compile(r'\[quarantined:\s*(\d{4}-\d{2}-\d{2})\]')
    today = _dt.date.today()
    sla_days = 30
    breach_entries = []
    oldest_days = None

    for line in active_lines:
        m = _date_re.search(line)
        if m:
            try:
                entry_date = _dt.date.fromisoformat(m.group(1))
                age = (today - entry_date).days
                if oldest_days is None or age > oldest_days:
                    oldest_days = age
                if age > sla_days:
                    breach_entries.append((line.split()[0], age))
            except ValueError:
                pass  # malformed date — skip age check for this entry

    if breach_entries:
        breach_desc = "; ".join(f"{t} ({d}d)" for t, d in breach_entries[:3])
        detail = (
            f"{size} quarantined, {len(breach_entries)} SLA breach(es) >30d: "
            f"{breach_desc}"
        )
        result_val = "FAIL"
    elif oldest_days is not None:
        detail = (
            f"{size} quarantined; oldest {oldest_days}d "
            f"(SLA 30d; all within SLA)"
        )
        result_val = "WARN"
    else:
        detail = (
            f"{size} quarantined; no [quarantined: YYYY-MM-DD] tags found "
            f"— add date tags to entries for SLA tracking"
        )
        result_val = "WARN"

    return {
        "id": "QUARANTINE-SLA",
        "result": result_val,
        "detail": detail,
        "size": size,
        "oldest_days": oldest_days,
        "breach_count": len(breach_entries),
    }


def _insert_dashboard_sys_path() -> None:
    """Ensure dashboard/ is on sys.path for sibling imports."""
    dashboard_dir = str(Path(__file__).resolve().parent)
    if dashboard_dir not in sys.path:
        sys.path.insert(0, dashboard_dir)




def check_frontmatter_coverage() -> dict:
    """FRONTMATTER-COVERAGE: % subagent files with explicit model: frontmatter.

    Implements ADR-0027 D1 standing invariant (every .claude/agents/*.md
    MUST have explicit model: frontmatter). Reports honest current value; PASS=100%.

    PASS when all agent files have explicit model: frontmatter.
    FAIL when any agent file is missing the model: field.
    WARN when the agents directory is missing or unreadable.
    """
    agents_dir = _HEALTH_REPO_ROOT / ".claude" / "agents"
    if not agents_dir.exists():
        return {
            "id": "FRONTMATTER-COVERAGE",
            "result": "WARN",
            "detail": ".claude/agents/ directory not found",
            "covered": 0, "total": 0, "missing": [],
        }

    # Enumerate only git-tracked agent files (issue #926: skip untracked decoys).
    agent_files = _tracked_files(_HEALTH_REPO_ROOT, ".claude/agents/*.md")
    if agent_files is None:
        # Fallback: git unavailable — use filesystem glob.
        agent_files = sorted(agents_dir.glob("*.md"))
    if not agent_files:
        return {
            "id": "FRONTMATTER-COVERAGE",
            "result": "WARN",
            "detail": "no .md files in .claude/agents/",
            "covered": 0, "total": 0, "missing": [],
        }

    missing = []
    _model_re = re.compile(r'^model\s*:', re.MULTILINE)
    for agent_path in sorted(agent_files):
        try:
            text = agent_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            missing.append(agent_path.name)
            continue
        if not _model_re.search(text):
            missing.append(agent_path.name)

    total = len(agent_files)
    covered = total - len(missing)
    pct = int(covered * 100 / total) if total > 0 else 0
    detail = (
        f"{covered}/{total} agent files have explicit model: frontmatter "
        f"({pct}%; ADR-0027 D1 invariant; expect 100%)"
    )
    if missing:
        detail += f"; missing: {missing[:5]}"
    result = "PASS" if not missing else "FAIL"
    return {
        "id": "FRONTMATTER-COVERAGE",
        "result": result,
        "detail": detail,
        "covered": covered,
        "total": total,
        "missing": missing,
    }


# ---------------------------------------------------------------------------
# Hygiene registry checks (ADR-0068 D1) — wave-4 slice #818
# ---------------------------------------------------------------------------

# Threshold: untracked file count under tracked directories before WARN.
_UNTRACKED_SIZE_WARN_COUNT = 50
# Rotation cap in bytes (must match log-tool-event.sh _ROTATION_CAP_BYTES).
_LOG_ROTATION_CAP_BYTES = 5 * 1024 * 1024   # 5 MB
# Stale branch age in days (no PR + inactive > this → stale).
_STALE_BRANCH_DAYS = 14
# Required labels as declared in bootstrap.sh LABELS array.
_REQUIRED_LABELS = [
    "prd", "slice", "backlog", "captured",
    "trivial", "needs-human", "needs-human-check", "root-cause",
]


def check_untracked_size() -> dict:
    """UNTRACKED-SIZE: count + size of untracked files under tracked dirs.

    Implements ADR-0068 D1 — workspace hygiene row. Reports the count of
    untracked files under tracked directories (e.g. qa-proof/).
    WARN when count > _UNTRACKED_SIZE_WARN_COUNT.
    Honest day-one values: pre-existing accumulation is the honest starting
    value, not a FAIL (ADR-0004 D2 bootstrap-mode).
    """
    try:
        result = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=15,
            cwd=str(_HEALTH_REPO_ROOT),
        )
        if result.returncode != 0:
            return {"id": "UNTRACKED-SIZE", "result": "WARN",
                    "detail": "git ls-files failed"}
        files = [f for f in result.stdout.splitlines() if f.strip()]
        count = len(files)
        # Sum sizes
        total_bytes = 0
        for f in files:
            try:
                total_bytes += (_HEALTH_REPO_ROOT / f).stat().st_size
            except Exception:
                pass
        size_mb = round(total_bytes / (1024 * 1024), 2)
        detail = (
            f"{count} untracked file(s) under tracked dirs "
            f"({size_mb} MB total); "
            f"threshold: WARN at >{_UNTRACKED_SIZE_WARN_COUNT} "
            f"(honest day-one ADR-0068 D1)"
        )
        result_str = "WARN" if count > _UNTRACKED_SIZE_WARN_COUNT else "PASS"
        return {"id": "UNTRACKED-SIZE", "result": result_str, "detail": detail,
                "count": count, "size_mb": size_mb}
    except Exception as exc:
        return {"id": "UNTRACKED-SIZE", "result": "WARN",
                "detail": f"check failed: {exc}"}


def check_log_rotation() -> dict:
    """LOG-ROTATION: workflow-events.jsonl size vs the rotation cap.

    Implements ADR-0068 D1. FAIL when the production log file meets or exceeds
    the cap and no rotation archive exists (rotation is broken).
    WARN when the log file exceeds 80% of the cap (proactive notice).
    PASS otherwise.

    Rotation cap: _LOG_ROTATION_CAP_BYTES (5 MB) — must match the cap
    documented in log-tool-event.sh _ROTATION_CAP_BYTES.
    """
    logs_dir = _telemetry_log_root() / ".claude" / "logs"
    events_log = logs_dir / "workflow-events.jsonl"
    if not events_log.exists():
        return {"id": "LOG-ROTATION", "result": "WARN",
                "detail": "workflow-events.jsonl not found (pre-hook setup expected)"}

    try:
        size = events_log.stat().st_size
        size_mb = round(size / (1024 * 1024), 2)
        cap_mb = round(_LOG_ROTATION_CAP_BYTES / (1024 * 1024), 1)

        # Count archive files (workflow-events.YYYYMMDDTHHMMSS.jsonl).
        import glob as _glob
        archives = _glob.glob(
            str(logs_dir / "workflow-events.2*.jsonl")
        )
        archive_count = len(archives)

        if size >= _LOG_ROTATION_CAP_BYTES:
            return {
                "id": "LOG-ROTATION",
                "result": "FAIL",
                "detail": (
                    f"workflow-events.jsonl is {size_mb} MB "
                    f"(cap {cap_mb} MB) — rotation not occurring; "
                    f"archives on disk: {archive_count}"
                ),
                "size_mb": size_mb, "cap_mb": cap_mb,
                "archive_count": archive_count,
            }
        elif size >= 0.8 * _LOG_ROTATION_CAP_BYTES:
            return {
                "id": "LOG-ROTATION",
                "result": "WARN",
                "detail": (
                    f"workflow-events.jsonl at {size_mb} MB "
                    f"(>80% of {cap_mb} MB cap); "
                    f"archives on disk: {archive_count}"
                ),
                "size_mb": size_mb, "cap_mb": cap_mb,
                "archive_count": archive_count,
            }
        return {
            "id": "LOG-ROTATION",
            "result": "PASS",
            "detail": (
                f"{size_mb} MB / {cap_mb} MB cap; "
                f"rotation grip = archive-aside (ADR-0068 D1); "
                f"archives on disk: {archive_count}"
            ),
            "size_mb": size_mb, "cap_mb": cap_mb,
            "archive_count": archive_count,
        }
    except Exception as exc:
        return {"id": "LOG-ROTATION", "result": "WARN",
                "detail": f"check failed: {exc}"}


def check_stale_branches() -> dict:
    """STALE-BRANCHES: remote branches merged or >14 days inactive without PR.

    Implements ADR-0068 D1. Advisory only: detectors report, humans act.
    Bind-forward per ADR-0004 D2: pre-existing branches are honest starting value.
    Needs git access; degrades gracefully on network failure.
    """
    try:
        import datetime as _dt
        import json as _json

        # Fetch remote branch refs + last commit date.
        result = subprocess.run(
            ["git", "for-each-ref",
             "--format=%(refname:short) %(committerdate:iso8601)",
             "refs/remotes/origin"],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=20,
            cwd=str(_HEALTH_REPO_ROOT),
        )
        if result.returncode != 0:
            return {"id": "STALE-BRANCHES", "result": "WARN",
                    "detail": "git for-each-ref failed (network/repo unavailable)"}

        now = _dt.datetime.now(_dt.timezone.utc)
        cutoff = now - _dt.timedelta(days=_STALE_BRANCH_DAYS)

        stale = []
        total = 0
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split(None, 1)
            if len(parts) < 2:
                continue
            ref_name = parts[0]
            # Skip HEAD and main
            if ref_name in ("origin/HEAD", "origin/main"):
                continue
            total += 1
            date_str = parts[1].strip()
            try:
                # Parse iso8601 with timezone offset
                dt = _dt.datetime.fromisoformat(date_str[:25])
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=_dt.timezone.utc)
                if dt < cutoff:
                    age_days = (now - dt).days
                    stale.append(f"{ref_name}({age_days}d)")
            except Exception:
                pass

        if stale:
            return {
                "id": "STALE-BRANCHES",
                "result": "WARN",
                "detail": (
                    f"{len(stale)}/{total} remote branches inactive "
                    f">{_STALE_BRANCH_DAYS}d: {', '.join(stale[:5])}"
                    + (" ..." if len(stale) > 5 else "")
                    + " (detectors-report-humans-act per ADR-0068 D1)"
                ),
                "stale_count": len(stale),
                "total": total,
            }
        return {
            "id": "STALE-BRANCHES",
            "result": "PASS",
            "detail": (
                f"0/{total} remote branches stale "
                f"(threshold >{_STALE_BRANCH_DAYS}d inactive)"
            ),
            "stale_count": 0,
            "total": total,
        }
    except Exception as exc:
        return {"id": "STALE-BRANCHES", "result": "WARN",
                "detail": f"check failed: {exc}"}


def check_required_labels() -> dict:
    """REQUIRED-LABELS: declared labels in bootstrap.sh vs live repo.

    Implements ADR-0068 D1. Checks that every label in _REQUIRED_LABELS
    exists on the live GitHub repo. Missing labels = WARN (bootstrap.sh drift).
    Gracefully degrades when gh CLI is unavailable.
    """
    import json as _json
    # Routed through gh_cache (ttl=60s, timeout=5s) — PRD #993 cr.3, slice #996.
    try:
        rc, out = _health_gh_fetch(
            ["label", "list", "--limit", "200", "--json", "name"],
            ttl=60.0, timeout=5.0,
        )
        if rc != 0 or not out.strip():
            return {"id": "REQUIRED-LABELS", "result": "WARN",
                    "detail": "gh label list unavailable (timeout/auth; degrade expected)"}
        live_labels = {item["name"] for item in _json.loads(out)}
    except Exception as exc:
        return {"id": "REQUIRED-LABELS", "result": "WARN",
                "detail": f"gh unavailable: {exc}"}

    missing = [lb for lb in _REQUIRED_LABELS if lb not in live_labels]
    if missing:
        return {
            "id": "REQUIRED-LABELS",
            "result": "WARN",
            "detail": (
                f"labels missing from live repo: {missing}; "
                f"run bootstrap.sh to create them (ADR-0068 D1)"
            ),
            "missing": missing,
        }
    return {
        "id": "REQUIRED-LABELS",
        "result": "PASS",
        "detail": (
            f"all {len(_REQUIRED_LABELS)} required labels present "
            f"on live repo (ADR-0068 D1)"
        ),
        "missing": [],
    }


def check_session_injection() -> dict:
    """SESSION-INJECTION: one session_context_injected event per session_id.

    Implements ADR-0068 D3. Reads workflow-events.jsonl and counts sessions
    that have a 'session_context_injected' event.

    PASS when all sessions in the last 20-session window have an injection
    event (the hook is live).
    WARN when fewer than 50% have one (hook not yet active / pre-hook sessions
    dominate the window — expected before this slice's deployment).
    Reports resumed-session gap count (sessions without injection).

    Bind-forward per ADR-0004 D2: pre-hook sessions are honest gaps, not FAILs.
    """
    import json as _json
    events_log = _telemetry_log_root() / ".claude" / "logs" / "workflow-events.jsonl"
    if not events_log.exists():
        return {"id": "SESSION-INJECTION", "result": "WARN",
                "detail": "workflow-events.jsonl not found (pre-hook setup expected)"}

    _SESSION_WINDOW = 20

    try:
        sessions: dict[str, set] = {}  # session_id → set of event types seen
        with events_log.open(encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    obj = _json.loads(raw)
                except Exception:
                    continue
                sid = obj.get("session_id", "")
                ev = obj.get("event", "")
                if not sid:
                    continue
                if sid not in sessions:
                    sessions[sid] = set()
                if ev:
                    sessions[sid].add(ev)
    except Exception as exc:
        return {"id": "SESSION-INJECTION", "result": "WARN",
                "detail": f"read error: {exc}"}

    if not sessions:
        return {"id": "SESSION-INJECTION", "result": "WARN",
                "detail": "no sessions found in workflow-events.jsonl"}

    window = list(sessions.items())[-_SESSION_WINDOW:]
    total = len(window)
    with_injection = sum(
        1 for _sid, evts in window
        if "session_context_injected" in evts
    )
    without = total - with_injection
    ratio = with_injection / total if total > 0 else 0.0

    # Per-session summary (most recent first, capped at 8)
    per_session = []
    for sid, evts in reversed(window[-8:]):
        tag = "injected" if "session_context_injected" in evts else "no-injection"
        per_session.append(f"{sid[:8]}:{tag}")

    detail = (
        f"{with_injection}/{total} sessions in last {_SESSION_WINDOW}-session "
        f"window have injection event "
        f"({ratio*100:.0f}%); gaps={without} | "
        + ", ".join(per_session)
    )

    # Pre-hook: WARN (not FAIL) — bind-forward ADR-0004 D2.
    # Once the hook is live, WARN when <50% of the window has injection.
    result = "PASS" if ratio >= 0.50 else "WARN"
    return {
        "id": "SESSION-INJECTION",
        "result": result,
        "detail": detail,
        "with_injection": with_injection,
        "total": total,
        "ratio": round(ratio, 3),
    }


# ---------------------------------------------------------------------------
# Check registry (ADR-0064 D3) — single source of truth for all DOCS-* checks.
#
# Maps check-id string → zero-argument callable returning a dict with at
# minimum {"id": str, "result": "PASS"|"FAIL"|"WARN", "detail": str}.
#
# CLI usage (headless, per ADR-0064 D3):
#   python dashboard/health.py --check <id>   → run one check, print JSON
#   python dashboard/health.py --list          → print registered IDs, one per line
#
# Exit codes:
#   0 — check ran; result is PASS or WARN (non-blocking)
#   1 — check ran; result is FAIL (blocking)
#   2 — unknown check ID or bad arguments
#
# CI consumers (tools/ci-checks.sh) use:
#   python3 dashboard/health.py --check DOCS-7   → replaces bash grep loop
#   python3 dashboard/health.py --check DOCS-1   → replaces bash for-loop
#   python3 dashboard/health.py --check DOCS-2   → replaces bash for-loop
# Verdict-identical: same PASS/FAIL outcomes on the current repo state as the
# bash implementations they replace (the check functions predate the registry).
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Two-tier topology checks (ADR-0070 wave 5 — slice #843 full implementation)
# ---------------------------------------------------------------------------

def _git_sha(ref: str) -> str:
    """Return the SHA for a git ref; empty string on failure."""
    try:
        r = subprocess.run(
            ["git", "rev-parse", ref],
            capture_output=True, text=True, timeout=8,
            cwd=str(_HEALTH_REPO_ROOT),
        )
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def _git_count(range_spec: str) -> int:
    """Return commit count for a git rev-list range; -1 on error."""
    try:
        r = subprocess.run(
            ["git", "rev-list", "--count", range_spec],
            capture_output=True, text=True, timeout=8,
            cwd=str(_HEALTH_REPO_ROOT),
        )
        return int(r.stdout.strip()) if r.returncode == 0 else -1
    except Exception:
        return -1


def check_branch_topology() -> dict:
    """BRANCH-TOPOLOGY: real two-tier develop/main topology assertion (ADR-0070 D1/D3).

    Checks (in order; first failure determines result/detail):
    1. origin/develop exists — FAIL if missing.
    2. origin/main exists — FAIL if missing.
    3. main is an ancestor of develop (fast-forward topology) — WARN if not.
    4. develop is ahead of main by N commits (healthy: N>=0).
    5. Recent merged PRs base develop, not main — WARN if any recent PR has main base.
    6. Branch-protection on develop — WARN (honest, requires API availability).

    Returns PASS when the topology is clean, WARN on advisory issues, FAIL on
    structural breaks. Always emits real data — never the "dormant" stub.

    Extra diagnostic fields on the returned dict (beyond id/result/detail),
    surfaced via `python3 dashboard/health.py --check BRANCH-TOPOLOGY`:
      develop_sha, main_sha, ahead, behind, main_is_ancestor
    """
    import json as _json

    # 1. Check origin/develop exists
    develop_sha = _git_sha("origin/develop")
    if not develop_sha:
        return {
            "id": "BRANCH-TOPOLOGY",
            "result": "FAIL",
            "detail": "origin/develop does not exist — two-tier topology not initialised",
        }

    # 2. Check origin/main exists
    main_sha = _git_sha("origin/main")
    if not main_sha:
        return {
            "id": "BRANCH-TOPOLOGY",
            "result": "FAIL",
            "detail": "origin/main does not exist",
        }

    # 3. Commit counts
    ahead = _git_count(f"origin/main..origin/develop")
    behind = _git_count(f"origin/develop..origin/main")

    # 4. Ancestor check: main should be ancestor of develop (ff-clean)
    try:
        anc = subprocess.run(
            ["git", "merge-base", "--is-ancestor", "origin/main", "origin/develop"],
            capture_output=True, timeout=8, cwd=str(_HEALTH_REPO_ROOT),
        )
        main_is_ancestor = (anc.returncode == 0)
    except Exception:
        main_is_ancestor = False

    # 5. Recent PRs base check via gh CLI (via gh_cache — PRD #993 cr.3, slice #996)
    pr_base_ok = True
    pr_warn_detail = ""
    try:
        _pr5_rc, _pr5_out = _health_gh_fetch(
            ["pr", "list", "--state", "merged", "--limit", "10",
             "--json", "number,baseRefName"],
            ttl=60.0, timeout=5.0,
        )
        if _pr5_rc == 0 and _pr5_out.strip():
            prs = _json.loads(_pr5_out)
            main_based = [p["number"] for p in prs if p.get("baseRefName") == "main"]
            if main_based:
                pr_base_ok = False
                pr_warn_detail = f" | recent PRs with main base: {main_based[:3]}"
    except Exception:
        pass  # gh unavailable — skip PR check, don't WARN for this

    # 6. Branch-protection advisory (via gh_cache — PRD #993 cr.3, slice #996)
    bp_note = ""
    try:
        _bp_rc, _bp_out = _health_gh_fetch(
            ["api", "repos/{owner}/{repo}/branches/develop"],
            ttl=60.0, timeout=5.0,
        )
        if _bp_rc == 0 and _bp_out.strip():
            bp = _json.loads(_bp_out)
            protected = bp.get("protected", False)
            bp_note = f" | branch-protection={'on' if protected else 'off (advisory: enable)'}"
        else:
            bp_note = " | branch-protection: API unavailable (WARN)"
    except Exception:
        bp_note = " | branch-protection: check skipped"

    # Determine result
    ahead_str = str(ahead) if ahead >= 0 else "?"
    behind_str = str(behind) if behind >= 0 else "?"
    base_detail = (
        f"develop ahead of main by {ahead_str}, behind by {behind_str}; "
        f"main-is-ancestor={main_is_ancestor}; "
        f"develop={develop_sha[:8]}, main={main_sha[:8]}"
        f"{pr_warn_detail}{bp_note}"
    )

    if not main_is_ancestor:
        return {
            "id": "BRANCH-TOPOLOGY",
            "result": "WARN",
            "detail": f"main is NOT ancestor of develop (diverged topology); {base_detail}",
            "develop_sha": develop_sha,
            "main_sha": main_sha,
            "ahead": ahead,
            "behind": behind,
            "main_is_ancestor": main_is_ancestor,
        }

    if not pr_base_ok:
        return {
            "id": "BRANCH-TOPOLOGY",
            "result": "WARN",
            "detail": f"recent PRs targeting main (should target develop); {base_detail}",
            "develop_sha": develop_sha,
            "main_sha": main_sha,
            "ahead": ahead,
            "behind": behind,
            "main_is_ancestor": main_is_ancestor,
        }

    return {
        "id": "BRANCH-TOPOLOGY",
        "result": "PASS",
        "detail": base_detail,
        "develop_sha": develop_sha,
        "main_sha": main_sha,
        "ahead": ahead,
        "behind": behind,
        "main_is_ancestor": main_is_ancestor,
    }


def check_promotion_lag() -> dict:
    """PROMOTION-LAG: age of develop HEAD since last promotion to main (ADR-0070 D3).

    Measures how long develop has been ahead of main:
    - 0 commits ahead: no lag (PASS)
    - ahead > 0, last promotion < 24h ago: PASS
    - ahead > 0, last promotion 24h-72h: WARN (promotion due)
    - ahead > 0, last promotion > 72h: WARN (promotion overdue)
    - no promotions yet + ahead > 0: WARN (day-one, honest)

    Reads promotion events from workflow-events.jsonl.
    """
    import json as _json
    import time as _time

    promotions = _read_promotion_events()
    ahead = _git_count("origin/main..origin/develop")
    now = _time.time()

    if ahead == 0:
        last_sha = promotions[-1].get("sha", "")[:8] if promotions else "none"
        return {
            "id": "PROMOTION-LAG",
            "result": "PASS",
            "detail": f"develop == main (0 commits ahead); last promotion sha: {last_sha}",
            "ahead": 0,
            "last_promotion_ts": promotions[-1].get("ts", "") if promotions else None,
            "lag_hours": 0.0,
        }

    if not promotions:
        return {
            "id": "PROMOTION-LAG",
            "result": "WARN",
            "detail": (
                f"develop is {ahead} commit(s) ahead of main; no promotion events yet "
                f"(honest day-one — first promotion pending); ADR-0070 D3"
            ),
            "ahead": ahead,
            "last_promotion_ts": None,
            "lag_hours": None,
        }

    last_promo = promotions[-1]
    last_ts_str = last_promo.get("ts", "")
    lag_hours: float = 0.0
    try:
        from datetime import datetime, timezone
        last_dt = datetime.fromisoformat(last_ts_str.replace("Z", "+00:00"))
        lag_hours = round((now - last_dt.timestamp()) / 3600.0, 1)
    except Exception:
        lag_hours = 0.0

    last_sha = last_promo.get("sha", "")[:8]
    detail = (
        f"develop {ahead} commit(s) ahead of main; "
        f"last promotion {lag_hours}h ago (sha={last_sha}, ts={last_ts_str})"
    )

    if lag_hours > 72:
        result = "WARN"
        detail = f"promotion overdue ({lag_hours:.1f}h); {detail}"
    elif lag_hours > 24:
        result = "WARN"
        detail = f"promotion due ({lag_hours:.1f}h); {detail}"
    else:
        result = "PASS"

    return {
        "id": "PROMOTION-LAG",
        "result": result,
        "detail": detail,
        "ahead": ahead,
        "last_promotion_ts": last_ts_str,
        "lag_hours": lag_hours,
    }


def check_release_ready() -> dict:
    """RELEASE-READY: deterministic six-condition promotion gate (ADR-0070 D2).

    Evaluates develop HEAD against six conditions (ADR-0070 D2):
      (a) CI green on develop HEAD — via real GitHub ci conclusion (#986);
          falls back to local tools/ci-checks.sh when gh is unavailable
      (b) full test suite passes (ADR-0067 D1) — GREEN-FAST (#1161): when (a)
          was satisfied by a REAL (non-override) recorded GitHub ci=pass for
          this EXACT sha, that same evidence proves (b) too (tools/ci-checks.sh
          — what GitHub Actions runs as the `ci` check — already runs
          `pytest tests/` as a required sub-check, REG-001), so NO local
          pytest re-run happens. Local pytest remains the fallback whenever
          there is no recorded ci run for the sha (fresh clone / un-pushed sha)
      (c) latest production-verify PASS — wired to PROOF-INTEGRITY check (slice #839)
      (d) green-develop streak intact — no failing checkpoint since last promotion
          (uses main_green events in workflow-events.jsonl as the green-develop proxy
          until a full green-develop event stream is landed by migration slices)
      (e) zero open needs-human items — gh issue list --label needs-human
      (f) guardrail-path batch check — wired to check_meta_tripwire() (slice #840 / ADR-0070 D4)

    Returns:
      result="PASS", verdict="true" — all six conditions hold
      result="WARN", verdict="false", first_failing_condition="<a-f>" — gate held

    Exit code semantics (CLI): always 0 when the check ran — even when the gate is
    held.  A held gate is an honest WARN, not a FAIL.  Only genuine check errors
    (subprocess failures, import errors) emit WARN with an error detail.

    Test injection — env vars override individual condition results:
      _RELEASE_READY_CI_RESULT           PASS|FAIL  (bypasses ci-checks.sh)
      _RELEASE_READY_TESTS_RESULT        PASS|FAIL  (bypasses pytest)
      _RELEASE_READY_PROOF_INTEGRITY_RESULT  PASS|WARN|FAIL  (bypasses check_proof_integrity)
      _RELEASE_READY_STREAK_RESULT       PASS|FAIL  (bypasses event-log streak check)
      _RELEASE_READY_NEEDS_HUMAN_COUNT   <int>      (bypasses gh issue list)
      _META_TRIPWIRE_RESULT_OVERRIDE     PASS|FAIL|WARN  (bypasses check_meta_tripwire for (f))
      _RELEASE_READY_FORCE_FAIL          1          (forces verdict false; for promote.sh guard tests)
    """
    import json as _json

    # Hard-fail override for testing promote.sh guard logic.
    if os.environ.get("_RELEASE_READY_FORCE_FAIL", "").strip() == "1":
        return {
            "id": "RELEASE-READY",
            "result": "WARN",
            "verdict": "false",
            "detail": "gate held: forced fail via _RELEASE_READY_FORCE_FAIL (test injection)",
            "first_failing_condition": "test-override",
        }

    # -----------------------------------------------------------------------
    # (a) CI green on develop HEAD — prefer real GitHub ci conclusion (#986)
    #
    # Strategy: query GitHub for the `ci` check conclusion on develop's HEAD
    # via _fetch_github_ci_conclusion().  This ensures condition (a) reflects
    # the ACTUAL GitHub-Actions state, not a local ci-checks.sh run that may
    # diverge (e.g. local pytest installed but GitHub CI has no pytest).
    #
    # Fallback: if gh is unavailable or no matching PR found, run local
    # ci-checks.sh — but label the detail "(local fallback — no GitHub ci
    # run found)" so the source is unambiguous in the CLI output.
    # -----------------------------------------------------------------------
    ci_override = os.environ.get("_RELEASE_READY_CI_RESULT", "").strip().upper()
    # gh_status is populated ONLY on the real (non-override) gh-query path
    # below; it stays None when ci_override short-circuits the live query.
    # Condition (b) reuses it (#1161) to decide whether a recorded GitHub
    # ci=pass for this exact sha already proves the suite green, avoiding a
    # duplicate local pytest run.
    gh_status = None
    if ci_override:
        ci_pass = (ci_override == "PASS")
        ci_detail = f"CI result (injected): {ci_override}"
    else:
        gh_status, gh_detail = _fetch_github_ci_conclusion(_HEALTH_REPO_ROOT)
        if gh_status == "pass":
            ci_pass = True
            ci_detail = gh_detail  # e.g. "GitHub ci=pass (PR #983)"
        elif gh_status == "fail":
            ci_pass = False
            ci_detail = gh_detail  # e.g. "GitHub ci=failure (PR #983)"
        elif gh_status == "pending":
            # CI is still running — treat as not yet green (gate held).
            ci_pass = False
            ci_detail = f"{gh_detail} (local fallback — GitHub ci still pending)"
        else:
            # "unavailable" — fall back to local ci-checks.sh run.
            try:
                ci_result = subprocess.run(
                    ["bash", str(_HEALTH_REPO_ROOT / "tools" / "ci-checks.sh")],
                    capture_output=True, text=True,
                    timeout=_RELEASE_READY_CICHECKS_TIMEOUT_S,
                    cwd=str(_HEALTH_REPO_ROOT),
                )
                ci_pass = (ci_result.returncode == 0)
                ci_detail = (
                    f"ci-checks.sh exit={ci_result.returncode} "
                    f"(local fallback — no GitHub ci run found: {gh_detail})"
                )
            except Exception as exc:
                ci_pass = False
                ci_detail = (
                    f"ci-checks.sh error: {exc} "
                    f"(local fallback — no GitHub ci run found: {gh_detail})"
                )

    if not ci_pass:
        return {
            "id": "RELEASE-READY",
            "result": "WARN",
            "verdict": "false",
            "detail": f"gate held: condition (a) CI not green — {ci_detail}",
            "first_failing_condition": "a",
        }

    # -----------------------------------------------------------------------
    # (b) Full test suite passes (ADR-0067 D1)
    #
    # GREEN-FAST (#1161 — kills the #1122 budget-bump class at the root):
    # condition (a)'s recorded GitHub `ci` conclusion for this EXACT
    # develop-HEAD sha already proves the full pytest suite green whenever it
    # is a REAL (non-override) ci=pass — tools/ci-checks.sh (what GitHub
    # Actions runs as the `ci` check, REG-001) itself runs `pytest tests/` as
    # a required sub-check. Re-running the suite locally after already having
    # that proof is pure duplication (~25-35 min/slice measured). So: reuse
    # gh_status from condition (a) — when it is "pass", (b) is proven by that
    # SAME evidence, no local pytest re-run. Local pytest remains the
    # fallback whenever there is no recorded ci run for the sha (gh_status is
    # None — an explicit _RELEASE_READY_CI_RESULT override was used — or
    # "unavailable" — local-fallback-pass in condition (a), fresh clone /
    # un-pushed sha). _RELEASE_READY_TESTS_RESULT is honored FIRST,
    # unconditionally — the existing test-injection seam is never silently
    # shadowed by this fast path.
    # -----------------------------------------------------------------------
    tests_override = os.environ.get("_RELEASE_READY_TESTS_RESULT", "").strip().upper()
    if tests_override:
        tests_pass = (tests_override == "PASS")
        tests_detail = f"test suite result (injected): {tests_override}"
    elif gh_status == "pass":
        tests_pass = True
        tests_detail = (
            f"proven by recorded GitHub ci=pass for this exact sha (same "
            f"evidence as condition (a): {ci_detail}) — no local pytest "
            f"re-run (#1161)"
        )
    else:
        tests_dir = _HEALTH_REPO_ROOT / "tests"
        if not tests_dir.exists():
            tests_pass = False
            tests_detail = "tests/ directory not found"
        else:
            try:
                t_result = subprocess.run(
                    [sys.executable, "-m", "pytest", str(tests_dir), "-q",
                     "--no-header", "--tb=no"],
                    capture_output=True, text=True,
                    timeout=_RELEASE_READY_PYTEST_TIMEOUT_S,
                    cwd=str(_HEALTH_REPO_ROOT),
                )
                tests_pass = (t_result.returncode == 0)
                # Extract summary line from pytest output
                out_lines = (t_result.stdout or "").splitlines()
                summary = next(
                    (l for l in reversed(out_lines) if "passed" in l or "failed" in l),
                    f"exit={t_result.returncode}",
                )
                tests_detail = f"pytest: {summary.strip()}"
            except Exception as exc:
                tests_pass = False
                tests_detail = f"pytest error: {exc}"

    if not tests_pass:
        return {
            "id": "RELEASE-READY",
            "result": "WARN",
            "verdict": "false",
            "detail": f"gate held: condition (b) test suite not green — {tests_detail}",
            "first_failing_condition": "b",
        }

    # -----------------------------------------------------------------------
    # (c) Latest production-verify PASS — wired to PROOF-INTEGRITY check
    # Condition (c) degrades gracefully: PROOF-INTEGRITY WARN = no-data (pass);
    # PROOF-INTEGRITY FAIL = condition (c) fails.
    # -----------------------------------------------------------------------
    proof_override = os.environ.get("_RELEASE_READY_PROOF_INTEGRITY_RESULT", "").strip().upper()
    if proof_override:
        proof_result = proof_override
        proof_detail = f"PROOF-INTEGRITY result (injected): {proof_override}"
    else:
        try:
            pi = check_proof_integrity()
            proof_result = pi.get("result", "WARN")
            proof_detail = pi.get("detail", "")
        except Exception as exc:
            proof_result = "WARN"
            proof_detail = f"PROOF-INTEGRITY check error: {exc}"

    if proof_result == "FAIL":
        return {
            "id": "RELEASE-READY",
            "result": "WARN",
            "verdict": "false",
            "detail": f"gate held: condition (c) DOM-attestation failed — {proof_detail}",
            "first_failing_condition": "c",
        }
    # WARN = no qualifying data yet (honest no-data); treat as pass for (c).

    # -----------------------------------------------------------------------
    # (d) Green-develop streak intact
    # Green-develop streak: no RED checkpoint since the last promotion event.
    # Uses main_green events in workflow-events.jsonl as the proxy until
    # green-develop event stream lands in the migration slices.
    # A streak FAIL means there has been a red merge since last green event.
    # -----------------------------------------------------------------------
    streak_override = os.environ.get("_RELEASE_READY_STREAK_RESULT", "").strip().upper()
    if streak_override:
        streak_pass = (streak_override == "PASS")
        streak_detail = f"streak result (injected): {streak_override}"
    else:
        # Proxy: use check_green_main() — if it returns FAIL, streak is broken.
        try:
            gm = check_green_main()
            gm_result = gm.get("result", "WARN")
            if gm_result == "FAIL":
                streak_pass = False
                streak_detail = gm.get("detail", "green-main check FAIL")
            else:
                streak_pass = True
                streak_detail = gm.get("detail", "ok")
        except Exception as exc:
            streak_pass = True  # cannot determine → pass optimistically
            streak_detail = f"streak check error (pass optimistically): {exc}"

    if not streak_pass:
        return {
            "id": "RELEASE-READY",
            "result": "WARN",
            "verdict": "false",
            "detail": f"gate held: condition (d) green-develop streak broken — {streak_detail}",
            "first_failing_condition": "d",
        }

    # -----------------------------------------------------------------------
    # (e) A confirmed zero open needs-human items — issues AND pull requests
    # (ADR-0087 D3: the gate holds unless the count is CONFIRMED — an
    # unconfirmed source, an unparsable payload, an exception, or a
    # non-integer injection all hold it now; a "treat as 0" default used to
    # let an unreachable GitHub read as a clean queue. ADR-0087 D4: the
    # count spans issues and pull requests — CLAUDE.md I5 puts the
    # `needs-human` label on PRs too, but only `gh issue list` was queried.)
    # -----------------------------------------------------------------------
    nh_override = os.environ.get("_RELEASE_READY_NEEDS_HUMAN_COUNT", "").strip()
    if nh_override:
        try:
            nh_count = int(nh_override)
            nh_confirmed = True
            nh_detail = f"needs-human count (injected): {nh_count}"
        except ValueError:
            nh_count = 0
            nh_confirmed = False
            nh_detail = (
                "needs-human count unconfirmed (source=test-injection: "
                f"_RELEASE_READY_NEEDS_HUMAN_COUNT={nh_override!r} is not an integer)"
            )
    else:
        # Routed through gh_cache (ttl=30s, timeout=5s) — PRD #993 cr.3, slice #996.
        # Short TTL so stale cached counts don't hold the gate on the wrong value.
        # Two legs, issues and PRs; both must be a CONFIRMED, parseable JSON
        # list before the sum counts as observed (ADR-0087 D2's QUERY-HONESTY
        # attestation, once slice 2 wires it, is consumed transparently here
        # via the seam's source label — no edit needed at this call site).
        nh_confirmed = True
        nh_count = 0
        nh_legs = []
        for _leg_label, _leg_args in (
            ("issues", ["issue", "list", "--label", "needs-human",
                        "--state", "open", "--json", "number"]),
            ("PRs", ["pr", "list", "--label", "needs-human",
                     "--state", "open", "--json", "number"]),
        ):
            try:
                _leg_rc, _leg_out, _leg_source = _health_gh_fetch(
                    _leg_args, ttl=30.0, timeout=5.0, with_source=True,
                )
            except Exception as exc:
                nh_confirmed = False
                nh_legs.append(f"{_leg_label} check error: {exc}")
                continue
            if _leg_rc != 0:
                nh_confirmed = False
                nh_legs.append(f"{_leg_label} unconfirmed (source={_leg_source})")
                continue
            try:
                _leg_items = _json.loads(_leg_out) if _leg_out.strip() else []
                if not isinstance(_leg_items, list):
                    raise ValueError("payload is not a JSON list")
            except Exception as exc:
                nh_confirmed = False
                nh_legs.append(
                    f"{_leg_label} unconfirmed (source={_leg_source}: "
                    f"unparsable payload: {exc})"
                )
                continue
            nh_count += len(_leg_items)
            nh_legs.append(f"{_leg_label}={len(_leg_items)} (source={_leg_source})")

        if nh_confirmed:
            nh_detail = f"needs-human open: {nh_count} [{'; '.join(nh_legs)}]"
        else:
            nh_detail = "; ".join(nh_legs)

    if not nh_confirmed:
        return {
            "id": "RELEASE-READY",
            "result": "WARN",
            "verdict": "false",
            "detail": (
                f"gate held: condition (e) needs-human count unconfirmed — {nh_detail}"
            ),
            "first_failing_condition": "e",
        }

    if nh_count > 0:
        return {
            "id": "RELEASE-READY",
            "result": "WARN",
            "verdict": "false",
            "detail": (
                f"gate held: condition (e) {nh_count} open needs-human item(s) — "
                f"resolve before promoting; {nh_detail}"
            ),
            "first_failing_condition": "e",
        }

    # -----------------------------------------------------------------------
    # (f) Guardrail-path batch check — wired to check_meta_tripwire() (slice #840)
    # -----------------------------------------------------------------------
    mt_override = os.environ.get("_META_TRIPWIRE_RESULT_OVERRIDE", "").strip().upper()
    if mt_override in {"PASS", "FAIL", "WARN"}:
        mt_result_val = mt_override
        mt_detail = f"meta-tripwire result (injected): {mt_override}"
    else:
        try:
            mt = check_meta_tripwire()
            mt_result_val = mt.get("result", "WARN")
            mt_detail = mt.get("detail", "")
        except Exception as exc:
            mt_result_val = "WARN"
            mt_detail = f"meta-tripwire check error: {exc}"

    if mt_result_val == "FAIL":
        return {
            "id": "RELEASE-READY",
            "result": "WARN",
            "verdict": "false",
            "detail": f"gate held: condition (f) guardrail-path tripwire — {mt_detail}",
            "first_failing_condition": "f",
        }
    # WARN = no promotion data yet (day-one honest); treat as pass for (f).
    condition_f_note = f"meta-tripwire: {mt_result_val.lower()} — {mt_detail}"

    # -----------------------------------------------------------------------
    # All conditions pass — gate is open.
    # -----------------------------------------------------------------------
    return {
        "id": "RELEASE-READY",
        "result": "PASS",
        "verdict": "true",
        "detail": (
            f"gate open: (a) CI green [{ci_detail}], (b) tests pass, "
            "(c) proof-integrity ok, "
            f"(d) streak intact, (e) zero needs-human, (f) guardrail-tripwire {mt_result_val.lower()}"
        ),
        "first_failing_condition": "",
        "condition_a": ci_detail,
        "condition_f": condition_f_note,
    }


# ---------------------------------------------------------------------------
# PROOF-INTEGRITY check (slice #839 / ADR-0070 D5)
#
# Validates that browser-route proof artifacts are genuinely DOM-attested,
# NOT API-layer-only.  The #811/#833 class shipped because the API-layer proof
# passed while the rendered DOM was empty; this check closes that gap.
#
# Sub-checks per ADR-0070 D5:
#   (1) Browser-route PRs: the claimed proof string must appear in a captured
#       rendered-DOM inner_text assertion (inner_text: <text> token in body or
#       comments).  A proof with only a .png screenshot or an API JSON blob but
#       no inner_text: line FAILS.
#   (2) All routes: PROOF_SOURCE must name a live non-fixture session
#       (must not contain "fixture" per rule #21).
#   (3) All routes: ENV: field must be non-empty (sha freshness attestation).
#
# Test injection: set _PROOF_INTEGRITY_PR_OVERRIDE to a JSON array of PR dicts
# (each with keys: number, headRefName, labels, files, body, comments).
# When set, the network fetch is bypassed entirely.
# ---------------------------------------------------------------------------

# Bootstrap cutoff — PRs at or below this number are grandfathered.
# Bind-forward per ADR-0004 D2; slice #839 is the implementing merge.
_PROOF_INTEGRITY_BOOTSTRAP_PR = 839

# Regex tokens that indicate DOM inner_text attestation in a PR body/comment.
_INNER_TEXT_RE = re.compile(r'inner_text\s*:', re.IGNORECASE)

# PROOF_SOURCE fixture-marker: presence of "fixture" anywhere in the value.
_FIXTURE_SOURCE_RE = re.compile(r'PROOF_SOURCE\s*:\s*([^\n]+)', re.IGNORECASE)

# ENV field: must be non-empty after the colon.
_ENV_FIELD_RE = re.compile(r'\bENV\s*:\s*(\S+)', re.IGNORECASE)


def _pr_has_inner_text_attestation(pr_body: str, comments: list[str]) -> bool:
    """Return True if inner_text: appears in the PR body or any comment."""
    if _INNER_TEXT_RE.search(pr_body):
        return True
    for comment in comments:
        if _INNER_TEXT_RE.search(comment):
            return True
    return False


def _proof_source_is_fixture(pr_body: str, comments: list[str]) -> bool:
    """Return True if any PROOF_SOURCE: line contains 'fixture' (rule #21)."""
    all_text = pr_body + "\n" + "\n".join(comments)
    for m in _FIXTURE_SOURCE_RE.finditer(all_text):
        value = m.group(1).strip()
        if "fixture" in value.lower():
            return True
    return False


def _env_field_is_empty(pr_body: str, comments: list[str]) -> bool:
    """Return True when ENV: is present but has no non-whitespace value."""
    all_text = pr_body + "\n" + "\n".join(comments)
    # Look for ENV: lines — bare "ENV:" or "ENV: " with nothing after it.
    env_bare_re = re.compile(r'^\s*ENV\s*:\s*$', re.IGNORECASE | re.MULTILINE)
    if env_bare_re.search(all_text):
        return True
    return False


def check_proof_integrity() -> dict:
    """PROOF-INTEGRITY: validate DOM-attestation of browser-route proof artifacts.

    Per ADR-0070 D5: for browser-route proof, asserts the claimed string appears
    in captured rendered-DOM inner_text (NOT API JSON — the #811/#833 class
    shipped because API-layer proof passed while the DOM was empty).

    Sub-checks (applied to each qualifying browser-route PR):
      (1) inner_text: token present in body or comments (DOM-attested)
      (2) PROOF_SOURCE does not contain 'fixture' (rule #21 live-source rule)
      (3) ENV: field is non-empty (sha freshness attestation)

    Honest day-one: evaluates over recent merged non-trivial browser-route PRs.
    Grandfathers PRs <= _PROOF_INTEGRITY_BOOTSTRAP_PR.

    WARN when no qualifying browser-route PRs found (no data yet).
    FAIL when any PR fails a sub-check (genuine DOM-attestation violation).
    PASS when all evaluated PRs pass all sub-checks.

    Test injection: set env var _PROOF_INTEGRITY_PR_OVERRIDE to a JSON list of
    PR dicts (keys: number, headRefName, labels, files, body, comments).
    """
    import json as _json

    # --- Test-injection path ---
    override_raw = os.environ.get("_PROOF_INTEGRITY_PR_OVERRIDE", "")
    if override_raw:
        try:
            all_prs = _json.loads(override_raw)
        except Exception as exc:
            return {
                "id": "PROOF-INTEGRITY",
                "result": "WARN",
                "detail": f"_PROOF_INTEGRITY_PR_OVERRIDE parse error: {exc}",
            }
    else:
        # --- Production path: fetch recent merged PRs via collector ---
        try:
            _insert_dashboard_sys_path()
            from collector import get_recent_merged_prs  # noqa: PLC0415
        except Exception as exc:
            return {
                "id": "PROOF-INTEGRITY",
                "result": "WARN",
                "detail": f"collector import failed: {exc}",
            }
        all_prs = get_recent_merged_prs(limit=_PROOF_PRESENCE_WINDOW + 5)

    # --- Filter to qualifying PRs ---
    # Skip trivial-lane; skip grandfathered; keep only browser-route PRs.
    browser_prs = []
    for pr in all_prs:
        ref = pr.get("headRefName", "")
        labels = [lb.get("name", "") for lb in (pr.get("labels") or [])]
        if "trivial" in labels or ref.startswith("hotfix/"):
            continue
        if pr.get("number", 0) <= _PROOF_INTEGRITY_BOOTSTRAP_PR:
            continue
        # Determine route: only evaluate browser-route PRs.
        changed_files = [f.get("path", "") for f in (pr.get("files") or [])]
        route_classes = _classify_route(changed_files)
        if "browser" not in route_classes:
            continue
        browser_prs.append(pr)

    if not browser_prs:
        return {
            "id": "PROOF-INTEGRITY",
            "result": "WARN",
            "detail": (
                f"no qualifying browser-route PRs found above bootstrap "
                f"threshold #{_PROOF_INTEGRITY_BOOTSTRAP_PR} — honest no-data"
            ),
        }

    # --- Evaluate each PR against the three sub-checks ---
    violations: list[str] = []
    passed = 0

    for pr in browser_prs:
        pr_num = pr.get("number", "?")
        pr_body = pr.get("body", "") or ""
        comments = [c.get("body", "") for c in (pr.get("comments") or [])]

        # Sub-check (1): browser route requires inner_text: attestation
        if not _pr_has_inner_text_attestation(pr_body, comments):
            violations.append(
                f"PR #{pr_num}: browser-route proof lacks inner_text: "
                f"attestation (API-only or screenshot-only proof — #811/#833 class)"
            )
            continue

        # Sub-check (2): PROOF_SOURCE must not be fixture-tagged (rule #21)
        if _proof_source_is_fixture(pr_body, comments):
            violations.append(
                f"PR #{pr_num}: PROOF_SOURCE contains 'fixture' "
                f"(live non-fixture session required per rule #21)"
            )
            continue

        # Sub-check (3): ENV: field must be non-empty
        if _env_field_is_empty(pr_body, comments):
            violations.append(
                f"PR #{pr_num}: ENV: field is empty "
                f"(sha freshness attestation required per ADR-0070 D5)"
            )
            continue

        passed += 1

    total = len(browser_prs)
    if violations:
        detail = (
            f"{len(violations)}/{total} browser-route PRs fail DOM-attestation: "
            + "; ".join(violations[:3])
            + (" [truncated]" if len(violations) > 3 else "")
        )
        return {"id": "PROOF-INTEGRITY", "result": "FAIL", "detail": detail,
                "passed": passed, "failed": len(violations), "total": total}

    detail = (
        f"{passed}/{total} browser-route PRs pass DOM-attestation "
        f"(inner_text:-attested, live PROOF_SOURCE, non-empty ENV)"
    )
    return {"id": "PROOF-INTEGRITY", "result": "PASS", "detail": detail,
            "passed": passed, "failed": 0, "total": total}


# ---------------------------------------------------------------------------
# HOOK-LIVENESS check (slice #849) — detects silent total-dark of hook layer.
# ---------------------------------------------------------------------------

# Named constant: delta threshold in minutes beyond which the hook layer is
# considered dark. (Module constant so tests can mirror it without importing.)
_HOOK_LIVENESS_DARK_MINUTES = 60


def check_hook_liveness() -> dict:
    """HOOK-LIVENESS: detect when the hook layer has gone silently dark.

    Compares the newest beacon timestamp in hook-fires.jsonl against the
    newest activity timestamp (workflow-events.jsonl OR latest git commit
    author-time, whichever is newer).

    If activity_ts - beacon_ts > _HOOK_LIVENESS_DARK_MINUTES → FAIL.
    Idle repos where both are old produce a small delta → PASS (no false alarm).

    Supports _HOOK_LIVENESS_FIRES_OVERRIDE / _HOOK_LIVENESS_EVENTS_OVERRIDE /
    _HOOK_LIVENESS_GIT_OVERRIDE env vars for test injection.

    Returns:
        {"id": "HOOK-LIVENESS", "result": "PASS"|"WARN"|"FAIL", "detail": ...}
    """
    import json as _json
    from datetime import datetime as _dt, timezone as _tz

    def _parse_ts(ts_str: str) -> float:
        """Parse ISO-8601 timestamp to unix float; return 0.0 on error."""
        if not ts_str:
            return 0.0
        try:
            return _dt.fromisoformat(ts_str.replace("Z", "+00:00")).timestamp()
        except Exception:
            return 0.0

    # --- 1. Newest beacon in hook-fires.jsonl ---
    fires_override = os.environ.get("_HOOK_LIVENESS_FIRES_OVERRIDE", "")
    if fires_override:
        fires_log = Path(fires_override)
    else:
        fires_log = _telemetry_log_root() / ".claude" / "logs" / "hook-fires.jsonl"

    if not fires_log.exists():
        return {
            "id": "HOOK-LIVENESS",
            "result": "WARN",
            "detail": "hook-fires.jsonl not found — hook layer may never have fired",
        }

    beacon_ts: float = 0.0
    try:
        with fires_log.open(encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    obj = _json.loads(raw)
                except Exception:
                    continue
                ts_val = _parse_ts(obj.get("ts", ""))
                if ts_val > beacon_ts:
                    beacon_ts = ts_val
    except Exception as exc:
        return {"id": "HOOK-LIVENESS", "result": "WARN",
                "detail": f"read error on hook-fires.jsonl: {exc}"}

    if beacon_ts == 0.0:
        return {
            "id": "HOOK-LIVENESS",
            "result": "WARN",
            "detail": "hook-fires.jsonl exists but contains no parseable beacon timestamps",
        }

    # --- 2. Newest activity: max(workflow-events.jsonl, git commit time) ---
    events_override = os.environ.get("_HOOK_LIVENESS_EVENTS_OVERRIDE", "")
    if events_override:
        events_log = Path(events_override)
    else:
        events_log = _telemetry_log_root() / ".claude" / "logs" / "workflow-events.jsonl"

    events_ts: float = 0.0
    if events_log.exists():
        try:
            with events_log.open(encoding="utf-8", errors="replace") as fh:
                for raw in fh:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        obj = _json.loads(raw)
                    except Exception:
                        continue
                    ts_val = _parse_ts(obj.get("ts", ""))
                    if ts_val > events_ts:
                        events_ts = ts_val
        except Exception:
            pass

    # Git commit author-time (fallback if no events log or env override)
    git_override = os.environ.get("_HOOK_LIVENESS_GIT_OVERRIDE", "")
    git_ts: float = 0.0
    if git_override:
        try:
            raw_ts = Path(git_override).read_text(encoding="utf-8").strip()
            git_ts = _parse_ts(raw_ts)
        except Exception:
            pass
    else:
        try:
            r = subprocess.run(
                ["git", "-C", str(_HEALTH_REPO_ROOT), "log", "-1", "--format=%cI"],
                capture_output=True, text=True, timeout=10,
            )
            git_ts = _parse_ts(r.stdout.strip()) if r.returncode == 0 else 0.0
        except Exception:
            git_ts = 0.0

    activity_ts = max(events_ts, git_ts)

    if activity_ts == 0.0:
        return {
            "id": "HOOK-LIVENESS",
            "result": "WARN",
            "detail": (
                f"could not determine activity timestamp "
                f"(events_ts={events_ts:.0f}, git_ts={git_ts:.0f}); "
                "check skipped"
            ),
        }

    # --- 3. Compare ---
    delta_minutes = (activity_ts - beacon_ts) / 60.0

    beacon_iso = _dt.fromtimestamp(beacon_ts, tz=_tz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    activity_iso = _dt.fromtimestamp(activity_ts, tz=_tz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    if delta_minutes > _HOOK_LIVENESS_DARK_MINUTES:
        return {
            "id": "HOOK-LIVENESS",
            "result": "FAIL",
            "detail": (
                f"hook layer appears dark: newest beacon {beacon_iso} is "
                f"{delta_minutes:.0f} min behind live activity {activity_iso} "
                f"(threshold {_HOOK_LIVENESS_DARK_MINUTES} min)"
            ),
        }

    return {
        "id": "HOOK-LIVENESS",
        "result": "PASS",
        "detail": (
            f"newest beacon {beacon_iso}; activity {activity_iso}; "
            f"delta {delta_minutes:.1f} min (threshold {_HOOK_LIVENESS_DARK_MINUTES} min)"
        ),
    }


# ---------------------------------------------------------------------------
# STREAM-LIVENESS — per-registered-stream dark detection (PRD #1075 criterion 8,
# slice #1085). Complements (does NOT replace) HOOK-LIVENESS and HOOK-INTEGRITY,
# read in full before writing this:
#   - HOOK-LIVENESS compares the SINGLE newest beacon across the whole hook
#     layer against activity — one healthy stream masks every dead sibling
#     (aggregate-newest-beacon blindness).
#   - HOOK-INTEGRITY skips any stream with zero recent attempt beacons
#     entirely ("no attempt beacons ... deferred to HOOK-LIVENESS") — but
#     HOOK-LIVENESS never looks per-stream either, so a stream that goes
#     fully dark is invisible to BOTH checks (circular deferral).
# STREAM-LIVENESS closes that gap: enumerate every REGISTERED stream (each
# distinct telemetry key discoverable from .claude/settings.json — mirroring
# discovery.py's AUTO-MODE _auto_mode_derived_keys aggregation plus the
# filename-stem fallback for hooks that beacon directly, e.g. "session-start"
# — PLUS the trace-v3 span stream) and check EACH one against its OWN
# last-fired timestamp. A stream with no fresh beacon in its window is a
# NAMED FAIL for that stream alone; live siblings still PASS.
# ---------------------------------------------------------------------------

_STREAM_LIVENESS_DARK_MINUTES = 60  # always-on window (unchanged; mirrors
    # _HOOK_LIVENESS_DARK_MINUTES)

# Per-stream cadence classes (root-cause fix, issue #1107): a uniform 60m
# window mathematically FAILs any once-per-session or trigger-driven stream
# whose own natural cadence is sparser than the window — that is a false
# alarm, not a real outage. Two narrower classes carve out of the default
# "always-on" bucket:
#   - "session-scoped": streams registered under the SessionStart hook event
#     (session-start.sh today; any future once-per-session hook is picked up
#     automatically since classification keys off the settings.json event
#     name, not a hardcoded stream-name list). Alive
#     iff the stream's own last beacon is within
#     _STREAM_LIVENESS_SESSION_SKEW_MINUTES of the NEWEST beacon among all
#     session-scoped streams (the "newest observed session" cluster) — never
#     against wall-clock "now". A stream that lags the newest cluster by more
#     than the skew tolerance means a newer session demonstrably started
#     without it beaconing: the real hooks-go-dark outage class stays FAIL.
#   - "on-demand": an explicit allow-list of trigger-driven streams
#     (_STREAM_LIVENESS_ON_DEMAND_STREAMS) that only fire on a specific,
#     inherently-sparse user action — grill_qa on AskUserQuestion, skill_invoke
#     on a Skill-tool invocation; both named explicitly in issue #1107's
#     proposed design ("on-demand (grill_qa, skill_invoke)"). Silence alone is
#     informational ("idle (on-demand)"), never a FAIL — the check has no
#     independent evidence a trigger occurred without its beacon, so it does
#     not fabricate a FAIL it cannot prove.
# "always-on" (the default; unchanged behavior) keeps the uniform 60m window.
_STREAM_LIVENESS_SESSION_SKEW_MINUTES = 10
_STREAM_LIVENESS_ON_DEMAND_STREAMS = frozenset({"grill_qa", "skill_invoke"})

_STREAM_CADENCE_SESSION_SCOPED = "session-scoped"
_STREAM_CADENCE_ON_DEMAND = "on-demand"
_STREAM_CADENCE_ALWAYS_ON = "always-on"


def _stream_liveness_registered_streams(settings_path: Path) -> dict:
    """Enumerate every distinct stream telemetry key registered in
    .claude/settings.json's hook configs, mirroring discover_hooks()'s own
    key-selection: literal event-type arg > AUTO-MODE derived-key set >
    filename-stem fallback (for hooks that beacon directly, bypassing
    log-tool-event.sh).

    Returns {stream_name: cadence_class} — cadence_class is one of
    _STREAM_CADENCE_SESSION_SCOPED / _STREAM_CADENCE_ON_DEMAND /
    _STREAM_CADENCE_ALWAYS_ON (see module comment above, issue #1107).
    Returns an empty dict (never raises) on any failure — callers degrade to
    WARN rather than fabricate a stream list.
    """
    import json as _json
    try:
        _insert_dashboard_sys_path()
        from discovery import (  # noqa: PLC0415
            _auto_mode_derived_keys,
            _event_type_from_cmd,
            _read_hook_name,
        )
    except Exception:
        return {}
    if not settings_path.exists():
        return {}

    def _classify(name: str, event: str) -> str:
        if event == "SessionStart":
            return _STREAM_CADENCE_SESSION_SCOPED
        if name in _STREAM_LIVENESS_ON_DEMAND_STREAMS:
            return _STREAM_CADENCE_ON_DEMAND
        return _STREAM_CADENCE_ALWAYS_ON

    streams: dict = {}
    try:
        data = _json.loads(settings_path.read_text(encoding="utf-8"))
        for event, entries in data.get("hooks", {}).items():
            for entry in entries:
                matcher = entry.get("matcher", "")
                for hook in entry.get("hooks", []):
                    cmd = hook.get("command", "")
                    event_type_arg = _event_type_from_cmd(cmd)
                    if event_type_arg == "auto":
                        for key in _auto_mode_derived_keys(event, matcher):
                            streams.setdefault(key, _classify(key, event))
                    elif event_type_arg:
                        streams.setdefault(event_type_arg, _classify(event_type_arg, event))
                    else:
                        clean_name = _read_hook_name(cmd)
                        if clean_name:
                            streams.setdefault(clean_name, _classify(clean_name, event))
    except Exception:
        return {}
    return streams


# Generous window for v3 span kinds classified "always-on" below (PRD #1127
# §2 criterion 10 / slice #1136). These verbs fire roughly once per
# slice-PR cycle -- multiple times on an active shipping day, but
# legitimately silent for many hours (nights, non-shipping days) without
# that silence meaning the verb has gone dark. The 60m hook-cadence window
# would false-FAIL the very next quiet morning; reusing it here (rather
# than widening it globally) keeps the hook-stream semantics unchanged
# while giving v3 kinds a window that matches their own real cadence.
_STREAM_LIVENESS_V3_DARK_MINUTES = 24 * 60  # 24h


def _stream_liveness_v3_kind_classes() -> dict:
    """Explode the single aggregate "trace-v3" stream into one row per
    registered v3 span kind (PRD #1127 §2 criterion 10 / ADR-0076 D2).

    Denominator: tools/trace.py's VALID_KINDS closed enum -- imported, never
    a copied list, so a kind added there is picked up here automatically.

    Cadence classification, from this PRD's own real observed cadence data
    (see PR #1136's body for the exact per-kind figures):
      - always-on (v3, generous _STREAM_LIVENESS_V3_DARK_MINUTES window):
        pr_opened, pr_merged, verdict, dispatch, dispatch_end -- fire on
        (roughly) every slice-PR cycle. Never-fired still FAILs (a verb that
        has never once been invoked is a fault, not mere sparseness) --
        same "always-on" semantics hook streams already use, just judged
        against a wider clock.
      - on-demand: qa_verified, develop_green, promotion, batch_planned --
        genuinely sparse/on-demand verbs. Dead-feed honesty rule: a kind
        that has NEVER fired yet (batch_planned only just landed this PRD)
        reads as pending/idle, not FAIL -- the existing on-demand bucket
        already treats never-fired as idle, so this is a direct reuse, not
        a new mechanism.

    Returns {f"v3:{kind}": cadence_class for kind in VALID_KINDS}. On any
    failure to import tools/trace.py's VALID_KINDS, degrades to the single
    pre-explosion aggregate key {"trace-v3": always-on} rather than
    silently dropping v3 liveness coverage entirely.
    """
    trace_mod = _load_trace_v3()
    if trace_mod is None or not hasattr(trace_mod, "VALID_KINDS"):
        return {"trace-v3": _STREAM_CADENCE_ALWAYS_ON}
    always_on_kinds = {"pr_opened", "pr_merged", "verdict", "dispatch", "dispatch_end"}
    classes: dict = {}
    for kind in sorted(trace_mod.VALID_KINDS):
        name = f"v3:{kind}"
        classes[name] = (
            _STREAM_CADENCE_ALWAYS_ON if kind in always_on_kinds
            else _STREAM_CADENCE_ON_DEMAND
        )
    return classes


def check_stream_liveness() -> dict:
    """STREAM-LIVENESS: see module comment above (PRD #1075 criterion 8;
    cadence classes per issue #1107; per-kind v3 explosion per PRD #1127
    §2 criterion 10 / slice #1136 -- see _stream_liveness_v3_kind_classes).

    Env overrides (test seam, mirrors check_hook_liveness's override pattern):
      _STREAM_LIVENESS_SETTINGS_OVERRIDE — path to a synthetic settings.json
      _STREAM_LIVENESS_FIRES_OVERRIDE    — path to a synthetic hook-fires.jsonl
      _STREAM_LIVENESS_TRACE_OVERRIDE    — path to a synthetic trace-v3.jsonl
      _STREAM_LIVENESS_NOW_OVERRIDE      — ISO-8601 ts to use as "now"

    Three cadence classes (see _stream_liveness_registered_streams):
      - always-on: PASS iff beaconed within _STREAM_LIVENESS_DARK_MINUTES of
        "now" (unchanged behavior).
      - session-scoped: PASS iff the stream's own last beacon is within
        _STREAM_LIVENESS_SESSION_SKEW_MINUTES of the newest beacon among ALL
        session-scoped streams — never fails purely for being chronologically
        old; FAILs only when a newer session-scoped beacon exists elsewhere
        (a newer session demonstrably started) and this stream missed it.
      - on-demand: silence alone is reported as idle (informational, listed
        separately in "idle_streams"/detail) — never a FAIL, since the check
        has no independent evidence a trigger occurred without its beacon.
    "never-fired" always-on/session-scoped streams still FAIL (no beacon
    evidence at all remains a fault, not mere sparseness).
    WARN when settings.json can't be parsed, no streams are discoverable, or
    neither hook-fires.jsonl nor trace-v3.jsonl exists yet (nothing to assert).
    """
    import json as _json
    from datetime import datetime as _dt, timezone as _tz

    def _parse_ts(ts_str: str) -> float:
        if not ts_str:
            return 0.0
        try:
            return _dt.fromisoformat(ts_str.replace("Z", "+00:00")).timestamp()
        except Exception:
            return 0.0

    settings_override = os.environ.get("_STREAM_LIVENESS_SETTINGS_OVERRIDE", "")
    settings_path = (
        Path(settings_override) if settings_override
        else _HEALTH_REPO_ROOT / ".claude" / "settings.json"
    )
    fires_override = os.environ.get("_STREAM_LIVENESS_FIRES_OVERRIDE", "")
    fires_log = (
        Path(fires_override) if fires_override
        else _telemetry_log_root() / ".claude" / "logs" / "hook-fires.jsonl"
    )
    trace_override = os.environ.get("_STREAM_LIVENESS_TRACE_OVERRIDE", "")
    now_override = os.environ.get("_STREAM_LIVENESS_NOW_OVERRIDE", "")
    now_ts = _parse_ts(now_override) if now_override else _dt.now(_tz.utc).timestamp()

    stream_classes = _stream_liveness_registered_streams(settings_path)
    if not stream_classes:
        return {
            "id": "STREAM-LIVENESS", "result": "WARN",
            "detail": f"no registered streams discoverable from {settings_path}",
        }
    # Per-kind explosion of the v3 span stream (PRD #1127 §2 criterion 10):
    # replaces the single aggregate "trace-v3" row with one row per
    # registered VALID_KINDS member.
    stream_classes.update(_stream_liveness_v3_kind_classes())

    # --- last-fired per hook stream, from hook-fires.jsonl ---
    last_fired: dict = {}
    fires_exists = fires_log.exists()
    if fires_exists:
        try:
            with fires_log.open(encoding="utf-8", errors="replace") as fh:
                for raw in fh:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        obj = _json.loads(raw)
                    except Exception:
                        continue
                    hook = obj.get("hook", "")
                    if not hook:
                        continue
                    ts_val = _parse_ts(obj.get("ts", ""))
                    if ts_val > last_fired.get(hook, 0.0):
                        last_fired[hook] = ts_val
        except Exception:
            pass

    # --- last-fired per v3 span kind, from the canonical trace log (PRD
    # #1127 §2 criterion 10: explode the single aggregate "trace-v3" row
    # into one row per registered kind). Also tracks the old aggregate max
    # for the fallback "trace-v3" key (used only when tools/trace.py's
    # VALID_KINDS enum could not be imported; see
    # _stream_liveness_v3_kind_classes()). ---
    if trace_override:
        trace_path = trace_override
    else:
        try:
            trace_mod = _load_trace_v3()
            trace_path = trace_mod.trace_log_path() if trace_mod is not None else None
        except Exception:
            trace_path = None
    trace_exists = bool(trace_path) and os.path.exists(trace_path)
    trace_ts = 0.0  # aggregate max -- fallback-mode "trace-v3" key only
    kind_last_fired: dict = {}
    if trace_exists:
        try:
            with open(trace_path, encoding="utf-8", errors="replace") as fh:
                for raw in fh:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        obj = _json.loads(raw)
                    except Exception:
                        continue
                    ts_val = _parse_ts(obj.get("ts", ""))
                    if ts_val > trace_ts:
                        trace_ts = ts_val
                    kind = obj.get("kind")
                    if kind and ts_val > kind_last_fired.get(kind, 0.0):
                        kind_last_fired[kind] = ts_val
        except Exception:
            pass
    if "trace-v3" in stream_classes:
        last_fired["trace-v3"] = trace_ts
    for _v3_name in stream_classes:
        if _v3_name.startswith("v3:"):
            last_fired[_v3_name] = kind_last_fired.get(_v3_name[3:], 0.0)

    if not fires_exists and not trace_exists:
        return {
            "id": "STREAM-LIVENESS", "result": "WARN",
            "detail": "no beacon data available yet (hook-fires.jsonl and trace-v3.jsonl both absent)",
        }

    # Newest observed session-start cluster: the max last-fired timestamp
    # among ALL session-scoped streams. A lone/synchronized session-scoped
    # stream trivially matches its own value (delta=0) regardless of its
    # absolute age — the false-FAIL this check exists to fix. A stream that
    # lags this reference by more than the skew tolerance means some OTHER
    # session-scoped stream demonstrably beaconed more recently — the real
    # outage class (a newer session started without this stream firing).
    session_scoped_names = [
        n for n, c in stream_classes.items() if c == _STREAM_CADENCE_SESSION_SCOPED
    ]
    session_reference_ts = max(
        (last_fired.get(n, 0.0) for n in session_scoped_names), default=0.0
    )
    skew_minutes = _STREAM_LIVENESS_SESSION_SKEW_MINUTES

    fail_streams = []
    pass_streams = []
    idle_streams = []  # on-demand silence — informational only, never a FAIL
    for name in sorted(stream_classes.keys()):
        cadence = stream_classes[name]
        ts_val = last_fired.get(name, 0.0)

        if cadence == _STREAM_CADENCE_SESSION_SCOPED:
            if ts_val == 0.0:
                fail_streams.append(f"{name}(never-fired)[session]")
                continue
            age_min = (now_ts - ts_val) / 60.0
            skew_min = (session_reference_ts - ts_val) / 60.0
            if skew_min > skew_minutes:
                fail_streams.append(
                    f"{name}({age_min:.0f}m)[session,missed newest session +{skew_min:.0f}m]"
                )
            else:
                pass_streams.append(f"{name}[session,{age_min:.0f}m]")
            continue

        if cadence == _STREAM_CADENCE_ON_DEMAND:
            if ts_val == 0.0:
                idle_streams.append(f"{name}(never-fired)[on-demand]")
            else:
                age_min = (now_ts - ts_val) / 60.0
                if age_min > _STREAM_LIVENESS_DARK_MINUTES:
                    idle_streams.append(f"{name}({age_min:.0f}m)[on-demand]")
                else:
                    pass_streams.append(f"{name}[on-demand,{age_min:.0f}m]")
            continue

        # always-on (default; unchanged behavior for hook streams). v3 kinds
        # (name prefix "v3:") use the generous _STREAM_LIVENESS_V3_DARK_MINUTES
        # per-merge-cadence window instead of the 60m hook-cadence window --
        # same always-on/never-fired-still-FAILs semantics, wider clock.
        if ts_val == 0.0:
            fail_streams.append(f"{name}(never-fired)")
            continue
        window = (
            _STREAM_LIVENESS_V3_DARK_MINUTES if name.startswith("v3:")
            else _STREAM_LIVENESS_DARK_MINUTES
        )
        delta_min = (now_ts - ts_val) / 60.0
        if delta_min > window:
            fail_streams.append(f"{name}({delta_min:.0f}m)")
        else:
            pass_streams.append(name)

    detail_parts = [
        f"window={_STREAM_LIVENESS_DARK_MINUTES}m",
        f"v3_window={_STREAM_LIVENESS_V3_DARK_MINUTES}m",
    ]
    if pass_streams:
        detail_parts.append(f"live: {', '.join(pass_streams)}")
    if idle_streams:
        detail_parts.append(f"idle (on-demand): {', '.join(idle_streams)}")
    if fail_streams:
        detail_parts.append(f"dark: {', '.join(fail_streams)}")

    return {
        "id": "STREAM-LIVENESS",
        "result": "FAIL" if fail_streams else "PASS",
        "detail": " | ".join(detail_parts),
        "streams": sorted(stream_classes.keys()),
        "stream_classes": dict(stream_classes),
        "fail_streams": fail_streams,
        "idle_streams": idle_streams,
    }


# ---------------------------------------------------------------------------
# DEPLOY-HANDSHAKE — health-row wrapper over tools/deploy-handshake.sh's
# NON-MUTATING --check-only leg (PRD #1075 slice #1085).
# DRY: zero duplicated hash-comparison logic — the shell script owns the
# single source of truth for the running-vs-deployed content-hash comparison
# (the same code path the session-start LOUD-WARN and CI CHECK 21 self-test
# use); this check only shells out and parses the script's structured
# STATUS:/detail: output. --check-only never blocks (always exits 0) so a
# real MISMATCH surfaces as a named FAIL row here instead of a subprocess error.
# ---------------------------------------------------------------------------

_DEPLOY_HANDSHAKE_STATUS_RE = re.compile(r'^STATUS:\s*(\w+)', re.MULTILINE)
_DEPLOY_HANDSHAKE_DETAIL_RE = re.compile(r'^detail:\s*(.*)', re.MULTILINE)


def check_deploy_handshake() -> dict:
    """DEPLOY-HANDSHAKE: shells to `tools/deploy-handshake.sh --check-only`
    and surfaces its structured PASS/FAIL + detail as a health row.
    """
    script = _HEALTH_REPO_ROOT / "tools" / "deploy-handshake.sh"
    if not script.exists():
        return {"id": "DEPLOY-HANDSHAKE", "result": "WARN",
                "detail": "tools/deploy-handshake.sh not found"}
    try:
        result = subprocess.run(
            ["bash", str(script), "--check-only"],
            capture_output=True, text=True, timeout=15,
            cwd=str(_HEALTH_REPO_ROOT),
        )
    except Exception as exc:
        return {"id": "DEPLOY-HANDSHAKE", "result": "WARN",
                "detail": f"deploy-handshake.sh --check-only invocation error: {exc}"}

    out = result.stdout.strip()
    status_m = _DEPLOY_HANDSHAKE_STATUS_RE.search(out)
    detail_m = _DEPLOY_HANDSHAKE_DETAIL_RE.search(out)
    status = status_m.group(1) if status_m else None
    detail_text = detail_m.group(1).strip() if detail_m else ""
    if not detail_text:
        detail_text = out or result.stderr.strip()

    if status == "PASS":
        return {"id": "DEPLOY-HANDSHAKE", "result": "PASS", "detail": detail_text}
    if status == "FAIL":
        return {"id": "DEPLOY-HANDSHAKE", "result": "FAIL", "detail": detail_text}
    return {
        "id": "DEPLOY-HANDSHAKE", "result": "WARN",
        "detail": (
            f"unparsable --check-only output (exit={result.returncode}): "
            f"{detail_text[:300]}"
        ),
    }


# ---------------------------------------------------------------------------
# DRAIN-LEDGER (ADR-0085 D6 / PRD #1326 §2 criterion 14 — slice #1329)
#
# Validates the newest `/ship` queue-drain run ledger.  Evaluated from the
# LEDGER FILE ALONE — it never queries GitHub.  That is the whole point: a
# live-parity predicate (escalated ⇒ currently-open labeled artifact) inverts
# on the operator's success path, which is the class ADR-0083 D3 names — a
# check may only assert an invariant its subjects are contractually obliged
# to satisfy, and current GitHub label state is not one a historical ledger
# owes.  Consequence: no network-degradation mode exists here (contrast
# check_record_vs_gh's "unverifiable — gh unavailable" path).
# ---------------------------------------------------------------------------

# Closed record-kind set (ADR-0085 D4).  Adding a kind requires a superseding
# ADR — an unknown kind is FAIL condition 1, never a silent pass-through.
_DRAIN_KINDS = frozenset({
    "run_start", "triaged", "item_start", "item_done", "escalated",
    "fix_queued", "fixed_in_run", "parked", "resumed", "run_end",
})

# Per-kind required fields beyond the universal `kind` (FAIL condition 2).
# `run_start`'s list encodes PRD #1326 §2 criterion 3's pinned field names.
_DRAIN_REQUIRED_FIELDS: dict = {
    "run_start":    ["counts", "open_prs"],
    "triaged":      ["item", "bucket", "lane"],
    "item_start":   ["item"],
    "item_done":    ["item"],
    "escalated":    ["item", "label", "label_applied"],
    "fix_queued":   ["item"],
    "fixed_in_run": ["item", "pr"],
    "parked":       ["remaining"],
    "resumed":      [],
    "run_end":      [],
}

# The subset of the required fields above that the row consumes as an
# IDENTITY — a set member, a dict key, or a closed-set lookup.  A non-string
# value there is not merely odd: it makes the record's identity undecidable
# and, unguarded, raises `TypeError: unhashable type` mid-check, replacing the
# row's verdict with a traceback and an empty stdout — so the CI check that
# delegates here reds with nothing naming the offending record.  Validating
# them is part of implementing conditions 2-6, exactly as the `remaining`
# shape guard is, not an eighth condition.
#
# Required fields absent from this table — `run_start`'s `open_prs`,
# `triaged`'s `bucket` and `lane`, `fixed_in_run`'s `pr` — are checked for
# presence only and never reach such an operation, so the row is owed nothing
# about their shape (VER-009 / ADR-0083 D3).  `run_start`'s `counts` and
# `parked`'s `remaining` carry their own shape guards inline, below.
#
# `escalated`'s `item` is the one entry held on a SECOND rationale rather
# than the hash one: the sequence pass only FORMATS it, into the two
# escalated failures below, where it is the sole thing naming WHICH item
# escalated.  Unvalidated it fails two ways — it garbles that finding, or,
# paired with a well-formed `label`, it raises nothing at all and the row
# certifies an item id no resume path can read.  Validating it buys the
# named line number instead; stating that here is the discipline VER-009
# asks of the guard itself (#1356).
_DRAIN_IDENTITY_FIELDS: dict = {
    "triaged":      ("item",),
    "item_start":   ("item",),
    "item_done":    ("item",),
    "escalated":    ("item", "label"),
    "fix_queued":   ("item",),
    "fixed_in_run": ("item",),
}

_DRAIN_RUN_START_COUNTS = ("prd", "slice", "backlog", "captured")
_DRAIN_ESCALATION_LABELS = frozenset({"needs-human-check", "needs-human"})
_DRAIN_CONCURRENCY_CAP = 3


def _drain_repr(value: object, limit: int = 60) -> str:
    """Bounded `repr` of a malformed ledger value for a FAIL message.

    Naming the offending shape IS the observation (VER-009); pasting all of
    an arbitrarily large one crowds the four other findings the row's detail
    string carries.  Same restraint the non-string-entry guard already shows
    by quoting only `bad[0]` — applied to the value's size, not just count.
    """
    text = repr(value)
    return text if len(text) <= limit else text[:limit] + "…(truncated)"


def _drain_ledger_dir(explicit: str | None = None) -> Path:
    """Resolve the drain-ledger directory.

    Injection order (slicer-critic finding 5 / rule #21): explicit argument →
    `DRAIN_LEDGER_DIR_OVERRIDE` env var → default.  The default copies the
    git-common-dir resolution `tools/trace.py` uses for trace-v3, so every
    worktree of the repo shares ONE ledger directory rather than writing a
    private one per worktree.  Tests always inject; nothing under
    `.claude/logs/` is ever written by a test.
    """
    if explicit:
        return Path(explicit)
    env = os.environ.get("DRAIN_LEDGER_DIR_OVERRIDE")
    if env:
        return Path(env)
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            capture_output=True, text=True, timeout=10, check=True,
        ).stdout.strip()
        root = Path(os.path.dirname(os.path.abspath(out)))
    except Exception:
        root = _HEALTH_REPO_ROOT
    return root / ".claude" / "logs" / "drain"


def check_drain_ledger(ledger_dir: str | None = None) -> dict:
    """DRAIN-LEDGER: the newest queue-drain run ledger is well-formed.

    Implements ADR-0085 D6 / PRD #1326 §2 criterion 14 in full — the seven
    FAIL conditions below, decidable offline from the ledger file alone.
    Their fields are read strictly: a value whose shape violates the
    documented record schema — a `remaining` that is not a list of item ids,
    or an identity field (`kind`, `item`, `escalated`'s `label`) that is not
    a non-empty string — is a named FAIL too, never read vacuously and never
    allowed to reach the set/dict operation that would raise `TypeError:
    unhashable type` and hand the caller a traceback in place of a verdict.
    That strictness is part of implementing these conditions, not an eighth
    one:

      1. a record kind outside the closed set (or malformed JSONL)
      2. missing required fields (run_start's count fields included)
      3. an `escalated` record missing its recorded applied-label evidence
         (`label` in {needs-human-check, needs-human} + `label_applied: true`)
      4. an `item_start` without a prior `triaged` record for that item
      5. more than 3 concurrently-open `item_start` records
      6. a `fix_queued` still unresolved at the run's terminal record — no
         `fixed_in_run`, no `captured_ref`, and (when the terminal is
         `parked`) not named in its remaining-items list.  The parity window
         closes at `run_end` OR `parked` because a run that parks and dies
         never writes `run_end`, and a fix must not escape through that gap
         (ADR-0085 D4/D5, applying the adr-critic's park-path finding).
         Because the ledger is append-only, a later `fix_queued` record for
         the same item is the sanctioned way to attach a `captured_ref`.
      7. a `parked` record with an empty remaining-items list

    No ledger present → WARN (a drain may simply never have run here).

    What to do on FAIL: read the named record in the ledger file and fix the
    emitting protocol in `.claude/skills/ship/SKILL.md`'s Queue-drain entry
    mode — the ledger is the run's durable state, and a malformed one means a
    resumed session cannot trust it.
    """
    import json as _json

    ledger_root = _drain_ledger_dir(ledger_dir)
    if not ledger_root.is_dir():
        return {"id": "DRAIN-LEDGER", "result": "WARN",
                "detail": f"no drain ledger directory at {ledger_root} — "
                          "no drain run has been recorded in this environment"}

    files = sorted(ledger_root.glob("*.jsonl"))
    if not files:
        return {"id": "DRAIN-LEDGER", "result": "WARN",
                "detail": f"no *.jsonl ledger in {ledger_root} — "
                          "no drain run has been recorded in this environment"}

    # Newest run = most recently modified, name as deterministic tiebreak.
    newest = max(files, key=lambda p: (p.stat().st_mtime, p.name))

    try:
        raw = newest.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {"id": "DRAIN-LEDGER", "result": "FAIL",
                "detail": f"{newest.name}: unreadable ({exc})"}

    failures: list[str] = []
    records: list[dict] = []

    # --- conditions 1 + 2: parse, kind membership, required fields ---
    for lineno, line in enumerate(raw.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            rec = _json.loads(line)
        except ValueError as exc:
            failures.append(f"line {lineno}: malformed JSON ({exc})")
            continue
        if not isinstance(rec, dict):
            failures.append(f"line {lineno}: record is not a JSON object")
            continue
        kind = rec.get("kind")
        # `isinstance` first: a structured `kind` would otherwise be hashed
        # against the closed set and raise instead of being reported.
        if not isinstance(kind, str) or kind not in _DRAIN_KINDS:
            failures.append(
                f"line {lineno}: record kind {_drain_repr(kind)} is outside "
                "the closed set (ADR-0085 D4)"
            )
            continue
        missing = [f for f in _DRAIN_REQUIRED_FIELDS[kind] if f not in rec]
        if missing:
            failures.append(
                f"line {lineno}: {kind} record missing required field(s) "
                f"{', '.join(missing)}"
            )
            continue
        bad_ids = [
            f for f in _DRAIN_IDENTITY_FIELDS.get(kind, ())
            if not (isinstance(rec[f], str) and rec[f].strip())
        ]
        if bad_ids:
            failures.append(
                f"line {lineno}: {kind} record identity field(s) "
                + ", ".join(f"`{f}` = {_drain_repr(rec[f])}" for f in bad_ids)
                + " must be a non-empty string"
            )
            continue
        if kind == "run_start":
            counts = rec.get("counts")
            if not isinstance(counts, dict):
                failures.append(f"line {lineno}: run_start `counts` is not an object")
                continue
            missing_counts = [c for c in _DRAIN_RUN_START_COUNTS if c not in counts]
            if missing_counts:
                failures.append(
                    f"line {lineno}: run_start `counts` missing "
                    f"{', '.join(missing_counts)}"
                )
                continue
        records.append(rec)

    # --- conditions 3-7: sequence semantics over the well-formed records ---
    triaged_items: set = set()
    # `open_items` is a SET of item ids, so a repeated `item_start` for one
    # already-open item counts once — the cap bounds distinct items in flight,
    # which is what D3 bounds, and QD9's park/resume freezes an item's single
    # `item_start` open across a park rather than re-emitting it.  A
    # duplicate-lifecycle FAIL is deliberately absent — ADR-0085 D6's
    # enumerated FAIL set does not include one (#1334 is the standing record;
    # revisit on a real ledger that double-starts one item).
    open_items: set = set()
    max_concurrent = 0
    fix_queued: dict = {}      # item -> True when a captured_ref was recorded
    fixed_items: set = set()
    reported_unresolved: set = set()   # report each escaping fix once, not per terminal
    terminal_seen = False

    for rec in records:
        kind = rec["kind"]
        item = rec.get("item")

        if kind == "escalated":
            label = rec.get("label")
            if label not in _DRAIN_ESCALATION_LABELS:
                failures.append(
                    f"escalated record for {_drain_repr(item)} carries label "
                    f"{_drain_repr(label)}; expected needs-human-check or "
                    "needs-human"
                )
            if rec.get("label_applied") is not True:
                failures.append(
                    f"escalated record for {_drain_repr(item)} lacks "
                    "`label_applied: true` write-time evidence"
                )

        elif kind == "triaged":
            triaged_items.add(item)

        elif kind == "item_start":
            if item not in triaged_items:
                failures.append(
                    f"item_start for {_drain_repr(item)} has no prior "
                    "triaged record"
                )
            open_items.add(item)
            max_concurrent = max(max_concurrent, len(open_items))

        elif kind == "item_done":
            open_items.discard(item)

        elif kind == "fix_queued":
            if rec.get("captured_ref"):
                fix_queued[item] = True
            else:
                fix_queued.setdefault(item, False)

        elif kind == "fixed_in_run":
            fixed_items.add(item)

        elif kind in ("parked", "run_end"):
            remaining = rec.get("remaining") if kind == "parked" else None
            if kind == "parked" and not remaining:
                failures.append(
                    "parked record carries an empty remaining-items list; a "
                    "park must name the resume order (ADR-0085 D4)"
                )
            if isinstance(remaining, list):
                bad = [e for e in remaining if not isinstance(e, str)]
                if bad:
                    failures.append(
                        f"parked record carries non-string remaining entries "
                        f"(first: {_drain_repr(bad[0])}); must hold item ids"
                    )
                remaining = [e for e in remaining if isinstance(e, str)]
            elif kind == "parked" and remaining:
                failures.append(
                    f"parked record carries a non-list remaining field "
                    f"({_drain_repr(remaining)}); remaining must hold item ids"
                )
                # One defect, one finding: the parity comparison below would
                # run against an empty set and convict every queued fix on a
                # value this record never made readable (VER-009).
                terminal_seen = True   # defensive only; the FAIL above wins
                continue
            remaining_set = set(remaining) if isinstance(remaining, list) else set()
            for fq_item, has_ref in fix_queued.items():
                if fq_item in fixed_items or has_ref or fq_item in remaining_set:
                    continue
                if fq_item in reported_unresolved:
                    continue
                reported_unresolved.add(fq_item)
                failures.append(
                    f"fix_queued {_drain_repr(fq_item)} reached the {kind} "
                    "terminal with no fixed_in_run, no captured_ref"
                    + (", and no place in the remaining list" if kind == "parked" else "")
                )
            terminal_seen = True

    if max_concurrent > _DRAIN_CONCURRENCY_CAP:
        failures.append(
            f"{max_concurrent} distinct items concurrently in flight; the "
            f"cap is {_DRAIN_CONCURRENCY_CAP} (ADR-0085 D3)"
        )

    if failures:
        shown = "; ".join(failures[:5])
        more = f" (+{len(failures) - 5} more)" if len(failures) > 5 else ""
        return {"id": "DRAIN-LEDGER", "result": "FAIL",
                "detail": f"{newest.name}: {shown}{more}"}

    terminal_note = "" if terminal_seen else ", run still in flight"
    return {
        "id": "DRAIN-LEDGER", "result": "PASS",
        "detail": (
            f"{newest.name}: {len(records)} records valid, "
            f"{len(triaged_items)} triaged, peak concurrency "
            f"{max_concurrent}/{_DRAIN_CONCURRENCY_CAP}{terminal_note}"
        ),
    }


CHECK_REGISTRY: dict[str, callable] = {
    "DOCS-1":  check_docs1_adr_index_forward,
    "DOCS-2":  check_docs2_adr_index_reverse,
    "DOCS-3":  check_docs3_claude_md_agents,
    "DOCS-4":  check_docs4_claude_md_skills,
    "DOCS-5":  check_docs5_n3_literal,
    "DOCS-6":  check_docs6_glossary_md_refs,
    "DOCS-7":  check_docs7_adr_citations,
    "DOCS-8":  check_docs8_supersession_notes,
    "DOCS-9":  check_docs9_glossary_cap,
    "DOCS-10": check_docs10_backlog_surfacing,
    "DOCS-11": check_docs11_dead_citations,
    "R-SENSITIVE-DETECTOR": check_r_sensitive_detector,
    # Substrate checks
    "CAPTURE-SLO":     check_capture_slo,
    "HOOK-INTEGRITY":  check_hook_integrity,
    "HOOK-LIVENESS":   check_hook_liveness,
    "STREAM-LIVENESS": check_stream_liveness,
    "ISOLATION-GROUP": check_isolation_group,
    "RULE-COVERAGE":   check_rule_coverage,
    "SPEC-COVERAGE":   check_spec_coverage,
    "DEPLOY-HANDSHAKE": check_deploy_handshake,
    # Registry-closure fix (PRD #1075 slice #1085 / ADR-0064 D3): function
    # existed since slice #779 but was never added to the registry.
    "CRITIC-HEALTH":   check_critic_health,
    # Memory checks (ADR-0067 wave 4)
    "TESTS-COLLECTED":  check_tests_collected,
    "TEST-ORDERING":    check_test_ordering,
    "QUARANTINE-SLA":   check_quarantine_sla,
    # Hygiene checks (ADR-0068 D1/D3 wave 4 slice #818)
    "UNTRACKED-SIZE":    check_untracked_size,
    "LOG-ROTATION":      check_log_rotation,
    "STALE-BRANCHES":    check_stale_branches,
    "REQUIRED-LABELS":   check_required_labels,
    "SESSION-INJECTION": check_session_injection,
    # model-frontmatter invariant check (ADR-0027 D1; fleet-economics removed per ADR-0071 D2)
    "FRONTMATTER-COVERAGE": check_frontmatter_coverage,
    # Verification-integrity checks (require network/collector)
    "BLIND-RATE":      check_blind_dispatch_rate,
    "RESIDUAL-RATIO":  check_residual_ratio,
    "MERGE-INTEGRITY": check_merge_integrity,
    "CAPTURE-SHAPE":   check_capture_shape,
    "GREEN-MAIN":      check_green_main,
    "RECORD-VS-GH":    check_record_vs_gh,
    # ADR-0076 reconciler family (PRD #1127 §2 criterion 11b / slice #1136)
    "SLICE-VS-PR":            check_slice_vs_pr,
    "MERGED-WITHOUT-VERDICT": check_merged_without_verdict,
    "CLOSED-PRD-VS-QA":       check_closed_prd_vs_qa,
    "PROOF-PRESENCE":  check_proof_presence,
    "SILENT-DRIFT":    check_silent_drift,
    # Two-tier topology (ADR-0070 wave 5 — slice #843 full implementation)
    "BRANCH-TOPOLOGY":  check_branch_topology,
    "PROMOTION-LAG":    check_promotion_lag,
    "RELEASE-READY":    check_release_ready,
    # DOM-attestation integrity (ADR-0070 D5 — slice #839)
    "PROOF-INTEGRITY": check_proof_integrity,
    # Guardrail-machinery promotion meta-tripwire (ADR-0070 D4 — slice #840)
    "META-TRIPWIRE": check_meta_tripwire,
    # Audit-subagents aggregate check (PRD #919 slice #921 — replaces /audit-subagents skill)
    "AS-AUDIT": check_audit_subagents,
    # Queue-drain run-ledger integrity (ADR-0085 D6 — PRD #1326 slice #1329)
    "DRAIN-LEDGER": check_drain_ledger,
}


def check_parity() -> dict:
    """PARITY: registry IDs == declared IDs == CI-consumed IDs.

    Implements ADR-0064 D3 standing parity alarm.

    Three ID sets are compared:
    1. Registry IDs: keys of CHECK_REGISTRY (the single-source implementation).
    2. Declared IDs: DOCS-*/STRUCT-* IDs extracted from ### <id> — headings in
       their canonical declared-ID source:
       - DOCS-*/STRUCT-*: codebase-critic.md "Deterministic pre-checks" section
         (moved from audit-meta/SKILL.md by PRD #919 slice #920).
       - AS-*: AS-AUDIT is registered directly in CHECK_REGISTRY (PRD #919
         slice #921 retired audit-subagents/SKILL.md); no separate declared-ID
         source is scanned for AS-* (the individual AS-ALL/CRIT/GEN check IDs
         are internal helpers, not declared IDs in the registry sense).
    3. CI-consumed IDs: IDs extracted from python3 dashboard/health.py invocations
       in tools/ci-checks.sh (lines matching --check <ID> or --list patterns).

    The check is honest about what it can and cannot measure today:
    - Declared = the ### headings that look like "### DOCS-N — " or
      "### STRUCT-N — " in codebase-critic.md.
    - CI-consumed = `--check <ID>` arguments in ci-checks.sh.  Post-migration
      CHECK 4/5 use registry calls; the set grows as later slices add more.
    - Registry IDs are the authoritative set (per ADR-0064 D3).

    Deferred-gap acknowledgement: STRUCT-* declared IDs are not individually
    registered because they run grouped via audit_meta() inside the
    codebase-critic per-PRD pass (PRD #919 slice #920); individual
    registration is deferred to a later slice.
    These known-deferred IDs are excluded from skill_gaps to avoid spurious
    WARN. The orphan-ci check (FAIL trigger) is unaffected — it catches any
    CI invocation of a check the registry does not have, regardless of deferral.

    PASS when CI-consumed ⊆ registry AND no non-deferred skill_gaps.
    WARN when non-deferred skill_gaps exist (gap to close in later slices).
    FAIL on orphan CI-consumed IDs (CI calls a check the registry does not have).

    PARITY: <registry_count> registered, <skill_count> declared,
            <ci_count> CI-consumed; orphan-ci=[] skill-gaps=[]
    """
    # IDs declared in source files but legitimately not individually registered
    # because they run grouped (STRUCT-*) via codebase-critic per-PRD pass.
    _DEFERRED_GAPS: set = set()
    # STRUCT-1..10: run via audit_meta() group call in codebase-critic per-PRD pass.
    for i in range(1, 11):
        _DEFERRED_GAPS.add(f"STRUCT-{i}")

    # --- 1. Registry IDs ---
    registry_ids = set(CHECK_REGISTRY.keys())

    # --- 2. Declared IDs ---
    # Parse ### <id> — headings from canonical declared-ID sources.
    # DOCS-*/STRUCT-* declared in codebase-critic.md (post slice #920).
    # AS-AUDIT is registered directly in CHECK_REGISTRY; no separate source file.
    _skill_id_pat = re.compile(
        r"^###\s+((?:DOCS|STRUCT)-[A-Z0-9_-]+)\s+—",
        re.MULTILINE,
    )
    skill_ids: set = set()
    # Only read codebase-critic.md if it is git-tracked (issue #926: avoid reading
    # untracked/stale on-disk decoys that would produce spurious skill_gaps).
    # _tracked_files returns None on git-failure (fallback: read it anyway),
    # [] when git works but the file is untracked (skip it),
    # [path] when git works and the file is tracked (read it).
    _cbc_rel = str(_CODEBASE_CRITIC_MD.relative_to(_HEALTH_REPO_ROOT)).replace("\\", "/")
    _tracked_cbc = _tracked_files(_HEALTH_REPO_ROOT, _cbc_rel)
    _read_cbc = (_tracked_cbc is None) or (len(_tracked_cbc) > 0)
    if _read_cbc:
        try:
            text = _CODEBASE_CRITIC_MD.read_text(encoding="utf-8", errors="replace")
            for m in _skill_id_pat.finditer(text):
                skill_ids.add(m.group(1))
        except Exception:
            pass

    # --- 3. CI-consumed IDs ---
    # Scan tools/ci-checks.sh for: --check <ID> patterns.
    ci_checks_path = _HEALTH_REPO_ROOT / "tools" / "ci-checks.sh"
    _ci_id_pat = re.compile(r"--check\s+([A-Z][A-Z0-9_-]+)", re.MULTILINE)
    ci_ids: set = set()
    try:
        ci_text = ci_checks_path.read_text(encoding="utf-8", errors="replace")
        for m in _ci_id_pat.finditer(ci_text):
            ci_ids.add(m.group(1))
    except Exception:
        pass

    # --- Compute diffs ---
    orphan_ci = sorted(ci_ids - registry_ids)   # CI calls non-existent registry check
    all_skill_gaps = skill_ids - registry_ids    # declared IDs not in registry
    # Exclude known-deferred IDs (STRUCT-* group-registered)
    skill_gaps = sorted(
        gid for gid in all_skill_gaps
        if gid not in _DEFERRED_GAPS
    )
    deferred = sorted(gid for gid in all_skill_gaps
                      if gid in _DEFERRED_GAPS)

    r_count = len(registry_ids)
    s_count = len(skill_ids)
    c_count = len(ci_ids)

    detail = (
        f"{r_count} registered, {s_count} declared, {c_count} CI-consumed; "
        f"orphan-ci={orphan_ci}; skill-gaps={skill_gaps}; deferred={deferred}"
    )

    base = {
        "id": "PARITY",
        "registry_ids": sorted(registry_ids),
        "skill_ids": sorted(skill_ids),
        "ci_ids": sorted(ci_ids),
        "orphan_ci": orphan_ci,
        "skill_gaps": skill_gaps,
        "deferred": deferred,
    }
    if orphan_ci:
        return {**base, "result": "FAIL", "detail": detail}
    if skill_gaps:
        return {**base, "result": "WARN", "detail": detail}
    return {**base, "result": "PASS", "detail": detail}


# Register PARITY into the registry after defining it (self-referential).
CHECK_REGISTRY["PARITY"] = check_parity


def _build_health_data() -> dict:
    """Build the full aggregate health-check payload synchronously.

    Called from the background thread; never from an HTTP handler.

    Slice annotations:
    - _enrich_group: 'group' field (slice #931 / PRD #927 §2 #10)
    - _attach_descriptions: 'description' field (slice #966 / PRD #957)
    - _attach_data_state: 'data_state' field (slice #967 / PRD #957 §2 #4)
    - _attach_purpose_group: 'purpose_group' field (slice #968 / PRD #957 §2 #5)
    - _attach_what_to_do: 'what_to_do' field for actionable checks (slice #968 §2 #7)
    - _build_hook_trio_composite: hook-trio composite row (slice #968 §2 #6)
    """
    audit = audit_meta()
    _enrich_group(audit["checks"])
    _attach_data_state(audit["checks"])
    _attach_purpose_group(audit["checks"])
    _attach_what_to_do(audit["checks"])

    # Run the three hook checks individually (needed for composite + substrate)
    slo_result   = check_capture_slo()
    integ_result = check_hook_integrity()
    live_result  = check_hook_liveness()
    for r in [slo_result, integ_result, live_result]:
        _attach_data_state([r])
        _attach_descriptions([r])
        _enrich_group([r])
        _attach_purpose_group([r])
        _attach_what_to_do([r])

    def _enrich_all(checks):
        return _attach_what_to_do(_attach_purpose_group(_attach_data_state(
            _attach_descriptions(_enrich_group(checks)))))

    substrate_checks = _enrich_all([
        slo_result,
        integ_result,
        live_result,
        check_stream_liveness(),
        check_isolation_group(),
        check_rule_coverage(),
        check_spec_coverage(),
        check_critic_health(),
        check_tests_collected(),
        check_test_ordering(),
        check_quarantine_sla(),
    ])
    verification_checks = _enrich_all([
        check_blind_dispatch_rate(),
        check_residual_ratio(),
        check_proof_presence(),
        check_proof_integrity(),
        check_merge_integrity(),
        check_capture_shape(),
        check_green_main(),
        check_silent_drift(),
    ])
    registry_checks = _enrich_all([
        check_parity(),
    ])
    hygiene_checks = _enrich_all([
        check_untracked_size(),
        check_log_rotation(),
        check_stale_branches(),
        check_required_labels(),
        check_session_injection(),
        check_deploy_handshake(),
    ])
    promotion_checks = _enrich_all([
        check_branch_topology(),
        check_promotion_lag(),
        check_release_ready(),
        check_r_sensitive_detector(),
        check_meta_tripwire(),
    ])

    # Hook-trio composite (slice #968 §2 #6): one "Telemetry live" row
    hook_trio_composite = _build_hook_trio_composite(slo_result, integ_result, live_result)

    # Build the purpose-grouped payload: all checks by purpose_group, with the
    # hook-trio appearing as a single composite in "Telemetry live" instead of 3 rows.
    # The 4 registered-but-UI-invisible checks (BRANCH-TOPOLOGY, FRONTMATTER-COVERAGE,
    # META-TRIPWIRE, RELEASE-READY) are excluded (purpose_group == None for them).
    _all_flat_checks = (
        list(audit["checks"])
        + substrate_checks
        + verification_checks
        + registry_checks
        + hygiene_checks
        + promotion_checks
    )
    # Deduplicate by id (substrateMeta may have individual hook checks; keep first)
    _seen_ids: set = set()
    _deduped: list = []
    for chk in _all_flat_checks:
        cid = chk.get("id", "")
        if cid not in _seen_ids:
            _seen_ids.add(cid)
            _deduped.append(chk)

    # Build purpose_groups: replace the three hook checks with the composite
    _hook_ids = {"CAPTURE-SLO", "HOOK-INTEGRITY", "HOOK-LIVENESS"}
    purpose_groups: dict = {}
    for group in PURPOSE_GROUP_ORDER:
        purpose_groups[group] = []

    hook_trio_inserted = False
    for chk in _deduped:
        pg = chk.get("purpose_group")
        if pg is None:
            continue  # excluded from purpose-group view
        if chk.get("id") in _hook_ids:
            if not hook_trio_inserted:
                purpose_groups.setdefault("Telemetry live", []).append(hook_trio_composite)
                hook_trio_inserted = True
            # individual hook checks are NOT added — the composite replaces them
            continue
        purpose_groups.setdefault(pg, []).append(chk)

    return {
        "auditMeta": audit,
        "auditSubagents": audit_subagents(),
        "substrateMeta": {"checks": substrate_checks},
        "verificationIntegrity": {"checks": verification_checks},
        "registryIntegrity": {"checks": registry_checks},
        "hygieneIntegrity": {"checks": hygiene_checks},
        "promotionIntegrity": {"checks": promotion_checks},
        # Purpose-groups view (slice #968 / PRD #957 §2 #5):
        # Each group is a list of check dicts with purpose_group == group name.
        # Telemetry live group has the hook-trio composite (not 3 separate rows).
        "purposeGroups": purpose_groups,
        "purposeGroupOrder": PURPOSE_GROUP_ORDER,
        "hookTrioComposite": hook_trio_composite,
    }


def _health_background() -> None:
    """Compute health data in a background thread and cache the result."""
    global _health_computing
    try:
        result = _build_health_data()
        with _health_lock:
            _health_cache["data"] = result
            _health_cache["ts"] = time.time()
    except Exception as e:
        with _health_lock:
            _health_cache["data"] = {
                "error": str(e),
                "auditMeta": {"checks": []},
                "auditSubagents": {},
                "substrateMeta": {"checks": []},
                "verificationIntegrity": {"checks": []},
            }
            _health_cache["ts"] = time.time()
    finally:
        with _health_lock:
            _health_computing = False


def serve_health() -> tuple:
    """Return (payload_dict, is_fresh: bool).

    Stale-while-revalidate: if a previous payload exists, return it immediately
    (with "refreshing":true while a rebuild is in flight).
    {"status":"computing"} only when no payload has ever been built.
    Kicks off a background thread on cache miss or TTL expiry.
    Returns (data_dict, started_background: bool).
    """
    import threading as _threading
    global _health_computing
    with _health_lock:
        cached = _health_cache.get("data")
        now = time.time()
        ts = _health_cache.get("ts", 0)
        expired = (now - ts) >= _HEALTH_TTL
        if cached is not None and not expired:
            return cached, False
        if cached is not None and expired:
            payload = dict(cached)
            payload["refreshing"] = True
            if not _health_computing:
                _health_computing = True
                t = _threading.Thread(target=_health_background, daemon=True)
                t.start()
            return payload, False
        # No payload yet — bootstrap case
        if _health_computing:
            return {"status": "computing"}, True
        _health_computing = True
    t = _threading.Thread(target=_health_background, daemon=True)
    t.start()
    return {"status": "computing"}, True


# ---------------------------------------------------------------------------
# CLI entry point (ADR-0064 D3 registry CLI)
#
#   python dashboard/health.py --check <id>   run one check; print verdict
#   python dashboard/health.py --list          list registered IDs
#
# Exit codes: 0 = PASS/WARN, 1 = FAIL, 2 = unknown ID / bad args.
# Output: one line per result, human-readable.  Consumed by ci-checks.sh.
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse as _argparse
    import json as _json

    _parser = _argparse.ArgumentParser(
        description="health.py check registry CLI (ADR-0064 D3)",
        prog="python dashboard/health.py",
    )
    _group = _parser.add_mutually_exclusive_group(required=True)
    _group.add_argument(
        "--check", metavar="ID",
        help="run a single check by ID and print its verdict",
    )
    _group.add_argument(
        "--list", action="store_true",
        help="list all registered check IDs, one per line",
    )
    _args = _parser.parse_args()

    if _args.list:
        for _id in sorted(CHECK_REGISTRY.keys()):
            print(_id)
        sys.exit(0)

    # --check <id>
    _check_id = _args.check
    if _check_id not in CHECK_REGISTRY:
        print(f"ERROR: unknown check ID '{_check_id}'", file=sys.stderr)
        print(f"Use --list to see available IDs.", file=sys.stderr)
        sys.exit(2)

    _result = CHECK_REGISTRY[_check_id]()
    _verdict = _result.get("result", "UNKNOWN")
    _detail = _result.get("detail", "")
    _line = f"{_verdict}: {_check_id}"
    if _detail:
        _line += f" — {_detail}"
    print(_line)

    # Exit 1 on FAIL; 0 on PASS or WARN (CI can choose to treat WARN as passing)
    sys.exit(1 if _verdict == "FAIL" else 0)
