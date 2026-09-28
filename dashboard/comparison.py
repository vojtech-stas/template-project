"""
dashboard/comparison.py — per-run comparison engine (ADR-0053 D3).

Pure function compare(SPEC, trail) → report.
No side effects; no I/O; no imports outside stdlib.

Edge states (per-edge):
  confirmed     — predicate evaluated True; carries counts/timestamps/rounds
  missing       — predicate evaluated False AND run demonstrably progressed
                  past the stage (not in-flight; provably skipped/failed)
  not-reached   — run is in-flight OR ended before this stage; NEVER red
  not-exercised — conditional edge whose condition never arose; NEVER red
  unexpected    — evidence in the trail that matches no declared edge

Evidence tiers (ADR-0053 D2):
  github      — evaluated here; states above apply
  runtime     — never compared; all runtime edges return "not-evaluated"
  unmeasurable — never compared; all unmeasurable edges return "not-evaluated"

Violation detectors (first-class outputs):
  unreviewed_merge   — merged PR with zero verdict comments and no trivial label
  missing_closes_slice — PR merged but closingIssuesReferences has no slice
  slice_no_pr        — slice issue closed without a known closing PR
                       (respects NO_PR_EXPECTED annotation)
  prd_closed_open_slices — PRD closed while ≥1 slice is still open
  merged_without_ci  — merged non-trivial PR without SUCCESS 'ci' statusCheckRollup
                       (bootstrap-mode: PRs merged at or before
                       GRANDFATHER_UNTIL['CI-GATE'] grandfathered, ADR-0089 D4)

Run PASS := every required:always github-tier edge is "confirmed"
            AND zero violations.
"""

from __future__ import annotations


def get_spec_for_compare() -> dict:
    """Import and return the spine spec (for use by CLI and server)."""
    import sys
    from pathlib import Path

    dashboard_dir = str(Path(__file__).resolve().parent)
    if dashboard_dir not in sys.path:
        sys.path.insert(0, dashboard_dir)
    from pipeline_spec import get_spec  # noqa: PLC0415
    return get_spec()


# ---------------------------------------------------------------------------
# Runtime observation merge step (ADR-0055 D1/D2)
# ---------------------------------------------------------------------------

def _apply_runtime_observation(report: dict, trail: dict) -> dict:
    """Merge runtime_observer output into the comparison report.

    Adds to report (never modifying run_pass/violations/unexpected per ADR-0055 D2):
      - runtime_edges: {edge_id: {state, detail, evidence, required, ...}}
      - runtime_coverage: {confirmed, unobserved, not_observable, not_exercised,
                           unmeasurable}
      - capture_liveness: bool
      - coverage_strip: {github, runtime, unmeasurable, total, per_state_counts}
                        Summary for the ADR-0055 D5 coverage strip display.

    Existing 'edges' entries for runtime-tier and unmeasurable-tier edges are
    updated from 'not-evaluated' to their observed/unmeasurable state.
    """
    import sys
    from pathlib import Path as _Path

    dashboard_dir = str(_Path(__file__).resolve().parent)
    if dashboard_dir not in sys.path:
        sys.path.insert(0, dashboard_dir)

    try:
        from runtime_observer import observe  # noqa: PLC0415
        obs = observe(trail)
    except Exception as exc:
        # Observation is advisory — never crash the comparison report
        obs = {
            "runtime_edges": {},
            "runtime_coverage": {
                "confirmed": 0, "unobserved": 0,
                "not_observable": 0, "not_exercised": 0,
                "unmeasurable": 0,
            },
            "capture_liveness": False,
            "_observer_error": str(exc),
        }

    # Attach top-level fields
    report["runtime_edges"] = obs["runtime_edges"]
    report["runtime_coverage"] = obs["runtime_coverage"]
    report["capture_liveness"] = obs.get("capture_liveness", False)
    # capture_unavailable: True when NEITHER transcript NOR hook log had events
    # in the PRD window — the Architecture tab renders a "capture dark" banner
    # instead of silent grey edges (PRD #956 slice 3 AC #7).
    report["capture_unavailable"] = obs.get("capture_unavailable", False)

    # Update the main edges dict: replace not-evaluated entries for runtime/unmeasurable edges
    for eid, rt_entry in obs["runtime_edges"].items():
        if eid in report["edges"]:
            # Preserve evidence/required from SPEC; update state + detail
            report["edges"][eid]["state"] = rt_entry["state"]
            report["edges"][eid]["detail"] = rt_entry["detail"]
            if "event_evidence" in rt_entry:
                report["edges"][eid]["event_evidence"] = rt_entry["event_evidence"]

    # Build coverage strip (ADR-0055 D5):
    # 43 declared = 17 github + 24 runtime + 2 unmeasurable
    # (E-USER-AUDITSUBAGENTS + E-AUDITSUBAGENTS-REVIEWER removed PRD #919 slice #921)
    # Per-state counts over ALL edges (github + runtime + unmeasurable)
    all_edges = report.get("edges", {})
    per_state: dict[str, int] = {}
    for einfo in all_edges.values():
        st = einfo.get("state", "not-evaluated")
        per_state[st] = per_state.get(st, 0) + 1

    github_count = sum(
        1 for einfo in all_edges.values() if einfo.get("evidence") == "github"
    )
    runtime_count = sum(
        1 for einfo in all_edges.values() if einfo.get("evidence") == "runtime"
    )
    unmeasurable_count = sum(
        1 for einfo in all_edges.values() if einfo.get("evidence") == "unmeasurable"
    )
    not_evaluated_count = per_state.get("not-evaluated", 0)

    report["coverage_strip"] = {
        "total_declared": len(all_edges),
        "github": github_count,
        "runtime": runtime_count,
        "unmeasurable_by_design": unmeasurable_count,
        "not_evaluated": not_evaluated_count,
        "per_state_counts": per_state,
        "zero_not_evaluated": not_evaluated_count == 0,
    }

    return report


# ---------------------------------------------------------------------------
# Predicate evaluators — one per github-tier SPEC edge
#
# Naming: _eval_<edge_id_without_dashes_lowercased>
# Each evaluator: (trail: dict) -> (state: str, detail: str)
# ---------------------------------------------------------------------------

# ---- Helpers ----------------------------------------------------------------

def _prd_progressed_past_prd_creation(trail: dict) -> bool:
    """True if the run demonstrably reached slice-creation or later."""
    return bool(trail.get("slices") or trail.get("prd_closed_at"))


def _prd_progressed_past_slicing(trail: dict) -> bool:
    """True if the run demonstrably progressed to implementation (PRs or merged)."""
    return bool(trail.get("prs") or trail.get("prd_closed_at"))


def _prd_progressed_past_implementation(trail: dict) -> bool:
    """True if the run demonstrably reached merge or PRD closure."""
    prs = trail.get("prs", {})
    any_merged = any(pr.get("merged_at") for pr in prs.values())
    return bool(any_merged or trail.get("prd_closed_at"))


# ---- Stage 2: prd-critic / adr-critic / slicer gates ----------------------

def _eval_prdcritic_approve(trail: dict) -> tuple[str, str]:
    """E-PRDCRITIC-APPROVE: prd-critic APPROVE comment on PRD issue.

    Distinguishes two non-confirmed outcomes per ADR-0053 D6:
    - not-exercised: PRD was manually posted (no critic gate invoked); the
      conditional automated-pipeline path was not triggered.  NEVER red.
    - missing: critic gate WAS invoked (some BLOCK found) but no APPROVE.

    For historical/manually-posted PRDs (the common case), no prd-critic
    comment means the automated gate was not used → not-exercised, not missing.
    Bootstrap-mode: this convention binds forward from when critics post to PRD
    issues; pre-convention history is not retroactively penalized.
    """
    prd_verdicts = trail.get("prd_verdicts", [])
    prd_critic_approves = [
        v for v in prd_verdicts
        if v.get("verdict") == "APPROVE"
    ]
    if prd_critic_approves:
        return "confirmed", "prd-critic APPROVE verdict found on PRD issue"

    # No verdict on PRD issue
    if not _prd_progressed_past_prd_creation(trail):
        return "not-reached", "run has not progressed past PRD creation yet"

    # Check if the critic gate was ever invoked (any verdict = BLOCK or APPROVE)
    any_verdict = bool(prd_verdicts)
    if any_verdict:
        # Gate was used but only produced BLOCKs with no final APPROVE — missing
        return "missing", (
            "prd-critic gate invoked but no APPROVE verdict found on PRD issue"
        )

    # No verdicts at all — PRD was manually posted; critic gate not invoked.
    # This is the common case for historical PRDs (bootstrap-mode / not-exercised).
    return "not-exercised", (
        "no prd-critic verdict comment on PRD issue — "
        "PRD was manually posted (automated critic gate not invoked)"
    )


def _eval_adrcritic_approve(trail: dict) -> tuple[str, str]:
    """E-ADRCRITIC-APPROVE: adr-critic APPROVE comment on PRD issue (conditional)."""
    prd_verdicts = trail.get("prd_verdicts", [])
    # Check if any verdict mentions adr-critic specifically, or any APPROVE exists
    adr_critic_approves = [
        v for v in prd_verdicts
        if v.get("verdict") == "APPROVE" and "adr" in v.get("author", "").lower()
    ]
    if adr_critic_approves:
        return "confirmed", "adr-critic APPROVE verdict found on PRD issue"
    # Cannot determine without explicit adr-critic comment; this edge is conditional
    return "not-exercised", "no adr-critic verdict comment found (conditional — only when PRD has macro-ADR)"


def _eval_prdcritic_block(trail: dict) -> tuple[str, str]:
    """E-PRDCRITIC-BLOCK: prd-critic BLOCK verdict on PRD issue (conditional)."""
    prd_verdicts = trail.get("prd_verdicts", [])
    block_verdicts = [v for v in prd_verdicts if v.get("verdict") == "BLOCK"]
    if block_verdicts:
        return "confirmed", f"{len(block_verdicts)} BLOCK verdict(s) found on PRD issue"
    return "not-exercised", "no BLOCK verdicts on PRD issue"


# ---- Stage 2: slicing ------------------------------------------------------

def _eval_prd_has_slice(trail: dict) -> tuple[str, str]:
    """E-PRD-SLICE: PRD has ≥1 sub-issue (slice)."""
    slices = trail.get("slices", [])
    if slices:
        return "confirmed", f"{len(slices)} slice(s)"
    # If PRD is open and has no slices, it's not-reached (could be in-flight)
    if not trail.get("prd_closed_at"):
        return "not-reached", "PRD open, no slices yet"
    return "missing", "PRD closed with no sub-issues found"


# ---- Stage 3: implementation -----------------------------------------------

def _eval_sliceissue_impl(trail: dict) -> tuple[str, str]:
    """E-SLICEISSUE-IMPL: slice issues have assignees (implementer claimed them).

    Checks that at least one slice has assignees in the trail (I2 claim protocol).
    Per bootstrap-mode: slices without assignees may predate the I2 discipline
    or be manually dispatched → not-exercised rather than missing.
    """
    slices = trail.get("slices", [])
    if not slices:
        if not trail.get("prd_closed_at"):
            return "not-reached", "no slices yet"
        return "missing", "no slices found in trail"

    assigned = [s for s in slices if s.get("assignees")]
    total = len(slices)
    if assigned:
        return "confirmed", f"{len(assigned)}/{total} slice(s) have assignees"
    # No assignees: distinguish I2-era vs pre-I2 (bootstrap-mode)
    # If run progressed to PRs/merge, the implementer did work — just no I2 assignee evidence.
    # This is not-exercised (I2 claim protocol not used) rather than missing (failure).
    if _prd_progressed_past_slicing(trail):
        return "not-exercised", (
            f"{total} slice(s) found but none have assignees "
            f"(I2 claim protocol not used — manual dispatch or pre-I2 era)"
        )
    return "not-reached", "slices exist but no PRs yet (implementation not started)"


def _eval_slice_closed_by_pr(trail: dict) -> tuple[str, str]:
    """E-SLICE-PR: ≥1 slice closed via closingIssuesReferences on a PR."""
    slices = trail.get("slices", [])
    if not slices:
        return "not-reached", "no slices"

    confirmed_count = sum(1 for s in slices if s.get("closing_pr_number"))
    total = len(slices)

    if confirmed_count == total:
        return "confirmed", f"{confirmed_count}/{total} slices have closing PR"
    if confirmed_count > 0:
        # Some slices have PRs — partially confirmed; count as confirmed if any
        # In-flight slices may still be open
        open_without_pr = [
            s for s in slices
            if not s.get("closing_pr_number") and not s.get("closed_at")
        ]
        if open_without_pr:
            return "confirmed", (
                f"{confirmed_count}/{total} slices have closing PR; "
                f"{len(open_without_pr)} open (in-flight)"
            )
        return "missing", (
            f"{confirmed_count}/{total} slices have closing PR; "
            f"{total - confirmed_count} closed without PR link"
        )
    # No slices have PRs
    if not trail.get("prd_closed_at"):
        return "not-reached", "PRD open, slices have no PR links yet"
    return "missing", "no slice-to-PR links found (closingIssuesReferences empty)"


def _eval_pr_has_verdict(trail: dict) -> tuple[str, str]:
    """E-PR-REVIEW: ≥1 PR has a reviewer verdict comment."""
    prs = trail.get("prs", {})
    if not prs:
        # No PRs fetched yet
        if not trail.get("prd_closed_at"):
            return "not-reached", "no PRs fetched; PRD in-flight"
        return "missing", "no PRs found in trail"

    with_verdicts = [
        pr for pr in prs.values() if pr.get("verdict_count", 0) > 0
    ]
    trivial_only = [
        pr for pr in prs.values()
        if pr.get("is_trivial") and pr.get("verdict_count", 0) == 0
    ]
    non_trivial_no_verdict = [
        pr for pr in prs.values()
        if not pr.get("is_trivial") and pr.get("verdict_count", 0) == 0
    ]

    total = len(prs)
    if with_verdicts or trivial_only:
        detail_parts = []
        if with_verdicts:
            detail_parts.append(f"{len(with_verdicts)}/{total} PRs have verdicts")
        if trivial_only:
            detail_parts.append(f"{len(trivial_only)} trivial-lane (no verdict needed)")
        return "confirmed", "; ".join(detail_parts)

    if non_trivial_no_verdict and not trail.get("prd_closed_at"):
        return "not-reached", f"{total} PR(s), no verdicts yet (in-flight)"
    return "missing", f"{total} non-trivial PR(s) with zero verdicts"


def _eval_reviewed_before_merge(trail: dict) -> tuple[str, str]:
    """E-REVIEW-MERGE: PRs merged after an APPROVE verdict."""
    prs = trail.get("prs", {})
    if not prs:
        if not trail.get("prd_closed_at"):
            return "not-reached", "no PRs; PRD in-flight"
        return "missing", "no PRs in trail"

    merged_prs = [pr for pr in prs.values() if pr.get("merged_at")]
    if not merged_prs:
        return "not-reached", "no PRs merged yet"

    approved_before_merge = [
        pr for pr in merged_prs if pr.get("reviewed_before_merge")
    ]
    trivial_merged = [
        pr for pr in merged_prs if pr.get("is_trivial")
    ]

    if len(approved_before_merge) + len(trivial_merged) == len(merged_prs):
        return "confirmed", (
            f"{len(approved_before_merge)} APPROVE-reviewed merge(s)"
            + (f"; {len(trivial_merged)} trivial-lane" if trivial_merged else "")
        )
    unreviewed = [
        pr for pr in merged_prs
        if not pr.get("reviewed_before_merge") and not pr.get("is_trivial")
    ]
    if approved_before_merge:
        return "confirmed", (
            f"{len(approved_before_merge)}/{len(merged_prs)} reviewed; "
            f"{len(unreviewed)} unreviewed (see violations)"
        )
    return "missing", f"{len(merged_prs)} merged PR(s) but none reviewed before merge"


def _eval_prd_closed(trail: dict) -> tuple[str, str]:
    """E-MERGE-CLOSE-PRD: PRD issue closed after all slices merged."""
    closed_at = trail.get("prd_closed_at")
    if closed_at:
        slices = trail.get("slices", [])
        prs = trail.get("prs", {})
        merged_prs = sum(1 for p in prs.values() if p.get("merged_at"))
        return "confirmed", (
            f"PRD closed at {closed_at}; "
            f"{merged_prs} PR(s) merged; {len(slices)} slice(s)"
        )
    return "not-reached", "PRD still open"


def _eval_block_loop(trail: dict) -> tuple[str, str]:
    """E-REVIEW-BLOCK: ≥1 PR has a BLOCK verdict (followed by revision)."""
    prs = trail.get("prs", {})
    block_count = 0
    for pr in prs.values():
        for v in pr.get("verdicts", []):
            if v.get("verdict") == "BLOCK":
                block_count += 1
                break
    if block_count > 0:
        return "confirmed", f"{block_count} PR(s) had BLOCK round(s)"
    return "not-exercised", "no BLOCK verdicts in this run"


def _eval_reviewer_needshuman(trail: dict) -> tuple[str, str]:
    """E-REVIEWER-NEEDSHUMAN: ≥1 PR has needs-human label (round-3 BLOCK escalation)."""
    prs = trail.get("prs", {})
    # We don't have PR labels in the current trail shape; use body heuristic.
    # The PR body mentions 'needs-human' when the label was applied.
    needs_human_count = 0
    for pr in prs.values():
        body = pr.get("body_excerpt", "")
        # Check for needs-human label in body or head ref pattern
        import re
        if re.search(r'\bneeds.human\b', body, re.IGNORECASE):
            needs_human_count += 1
    if needs_human_count > 0:
        return "confirmed", f"{needs_human_count} PR(s) with needs-human indicator"
    return "not-exercised", "no round-3 BLOCK escalation in this run"


def _eval_trivial_lane(trail: dict) -> tuple[str, str]:
    """E-TRIVIAL-LANE: ≥1 PR has trivial label."""
    prs = trail.get("prs", {})
    trivial_prs = [pr for pr in prs.values() if pr.get("is_trivial")]
    if trivial_prs:
        return "confirmed", f"{len(trivial_prs)} trivial-lane PR(s)"
    return "not-exercised", "no trivial-lane PRs in this run"


def _eval_orch_captured(trail: dict) -> tuple[str, str]:
    """E-ORCH-CAPTURED: any agent created a captured-labeled issue (conditional)."""
    # Cannot determine from PRD trail alone (captured issues are separate from slices).
    return "not-exercised", "captured-issue creation not tracked in PRD trail (side-workflow)"


def _eval_backlogcritic_approve(trail: dict) -> tuple[str, str]:
    """E-BACKLOGCRITIC-APPROVE: backlog-critic APPROVE → issue relabeled (conditional)."""
    return "not-exercised", "promote-to-backlog workflow is a side-workflow (not in PRD trail)"


def _eval_backlogcritic_block(trail: dict) -> tuple[str, str]:
    """E-BACKLOGCRITIC-BLOCK: backlog-critic BLOCK → issue stays captured (conditional)."""
    return "not-exercised", "promote-to-backlog workflow is a side-workflow (not in PRD trail)"


# Map edge id → evaluator
# Only github-tier edges are evaluated. Runtime/unmeasurable edges return "not-evaluated".
_EDGE_EVALUATORS: dict[str, callable] = {
    # Stage 2: PRD authoring + critic gates
    "E-PRDCRITIC-APPROVE":      _eval_prdcritic_approve,
    "E-ADRCRITIC-APPROVE":      _eval_adrcritic_approve,
    "E-PRDCRITIC-BLOCK":        _eval_prdcritic_block,
    # Stage 2: slicing
    "E-PRD-SLICE":              _eval_prd_has_slice,
    # Stage 3: implementation
    "E-SLICEISSUE-IMPL":        _eval_sliceissue_impl,
    "E-SLICE-PR":               _eval_slice_closed_by_pr,
    "E-PR-REVIEW":              _eval_pr_has_verdict,
    "E-REVIEW-MERGE":           _eval_reviewed_before_merge,
    "E-MERGE-CLOSE-PRD":        _eval_prd_closed,
    "E-REVIEW-BLOCK":           _eval_block_loop,
    "E-REVIEWER-NEEDSHUMAN":    _eval_reviewer_needshuman,
    "E-TRIVIAL-LANE":           _eval_trivial_lane,
    # Side workflows (conditional; all not-exercised — not in PRD trail)
    "E-ORCH-CAPTURED":          _eval_orch_captured,
    "E-BACKLOGCRITIC-APPROVE":  _eval_backlogcritic_approve,
    "E-BACKLOGCRITIC-BLOCK":    _eval_backlogcritic_block,
}


# ---------------------------------------------------------------------------
# Violation detectors
# ---------------------------------------------------------------------------

def _detect_unreviewed_merges(trail: dict) -> list[dict]:
    """Detect PRs that were merged without any verdict and are not trivial-lane."""
    violations = []
    prs = trail.get("prs", {})
    for pr in prs.values():
        if (
            pr.get("merged_at")
            and pr.get("verdict_count", 0) == 0
            and not pr.get("is_trivial")
        ):
            violations.append({
                "type": "unreviewed_merge",
                "pr_number": pr["number"],
                "merged_at": pr.get("merged_at", ""),
                "detail": (
                    f"PR #{pr['number']} merged at {pr.get('merged_at', '?')} "
                    f"with zero reviewer verdicts and no trivial label"
                ),
            })
    return violations


def _detect_missing_closes_slice(trail: dict) -> list[dict]:
    """Detect PRs that have no closingIssuesReferences pointing to a slice in this PRD."""
    violations = []
    prs = trail.get("prs", {})
    slice_numbers = {s["number"] for s in trail.get("slices", [])}
    for pr in prs.values():
        closing = set(pr.get("closing_issues", []))
        if not closing.intersection(slice_numbers) and pr.get("merged_at"):
            violations.append({
                "type": "missing_closes_slice",
                "pr_number": pr["number"],
                "detail": (
                    f"PR #{pr['number']} merged but no closingIssuesReferences "
                    f"point to a slice in this PRD"
                ),
            })
    return violations


def _detect_slice_no_pr(trail: dict) -> list[dict]:
    """Detect slice issues closed without a known closing PR.

    Honors NO_PR_EXPECTED annotation: if a slice's title contains
    '[NO_PR_EXPECTED]', it is expected to close without a PR.

    Fix #1020 — develop-aware fallback: before emitting a slice_no_pr
    violation, check whether ANY PR in the trail's prs dict lists this
    slice in its closing_issues (populated by _build_pr_trail's body-parse
    fallback).  This covers the case where the collector's
    _discover_develop_pr_slice_links populated the prs dict but did NOT
    back-fill closing_pr_number on the slice (e.g. race / partial trail).
    """
    # Build a reverse index: slice_number → pr_number from prs closing_issues
    slice_covered_by_pr: dict[int, int] = {}
    for pr_key, pr in (trail.get("prs") or {}).items():
        pr_num = pr.get("number")
        if not pr_num:
            try:
                pr_num = int(pr_key)
            except (ValueError, TypeError):
                continue
        for issue_num in (pr.get("closing_issues") or []):
            if issue_num not in slice_covered_by_pr:
                slice_covered_by_pr[issue_num] = pr_num

    violations = []
    for sl in trail.get("slices", []):
        # Check NO_PR_EXPECTED annotation in slice title or labels
        title = sl.get("title", "")
        if "NO_PR_EXPECTED" in title.upper():
            continue
        if not sl.get("closed_at"):
            continue  # not closed; no violation possible
        # Primary check: closing_pr_number set by collector
        if sl.get("closing_pr_number"):
            continue
        # Secondary check: any PR in the trail lists this slice in closing_issues
        # (develop-aware fallback — fix #1020)
        if sl["number"] in slice_covered_by_pr:
            continue
        violations.append({
            "type": "slice_no_pr",
            "slice_number": sl["number"],
            "detail": (
                f"Slice #{sl['number']} closed at {sl.get('closed_at', '?')} "
                f"but no closing PR found via closingIssuesReferences"
            ),
        })
    return violations


def _detect_prd_closed_open_slices(trail: dict) -> list[dict]:
    """Detect PRD closed while ≥1 slice is still open."""
    if not trail.get("prd_closed_at"):
        return []  # PRD still open; no violation
    violations = []
    open_slices = [
        s for s in trail.get("slices", [])
        if not s.get("closed_at")
    ]
    if open_slices:
        slice_nums = [str(s["number"]) for s in open_slices]
        violations.append({
            "type": "prd_closed_open_slices",
            "slice_numbers": [s["number"] for s in open_slices],
            "detail": (
                f"PRD closed at {trail['prd_closed_at']} but "
                f"{len(open_slices)} slice(s) still open: #{', #'.join(slice_nums)}"
            ),
        })
    return violations


def _detect_merged_without_ci(trail: dict) -> list[dict]:
    """Detect merged non-trivial PRs without a SUCCESS 'ci' statusCheckRollup check.

    Bootstrap-mode: PRs merged at or before GRANDFATHER_UNTIL['CI-GATE']
    (dashboard/_constants.py, ADR-0089 D4) predate ADR-0042 and are
    grandfathered (never flagged as a violation). Import is local (not at
    module level) to keep this module's "no imports outside stdlib" pure-
    function property intact for every OTHER function here.

    Graceful defaults:
    - Missing statusCheckRollup (absent key or []) → grandfathered (no data; not a violation).
    - Trivial-lane PRs → excluded (trivial PRs are exempt from CI gate per I3).
    """
    import sys
    from pathlib import Path

    dashboard_dir = str(Path(__file__).resolve().parent)
    if dashboard_dir not in sys.path:
        sys.path.insert(0, dashboard_dir)
    from _constants import grandfathered  # noqa: PLC0415

    violations = []
    prs = trail.get("prs", {})
    for pr in prs.values():
        pr_number = pr.get("number", 0)
        merged_at = pr.get("merged_at")
        if not merged_at:
            continue  # not merged; no violation possible
        if pr.get("is_trivial"):
            continue  # trivial-lane: exempt from CI gate
        if grandfathered("CI-GATE", merged_at):
            continue  # grandfathered: predates ADR-0042

        rollup = pr.get("status_check_rollup")
        if not rollup:
            # No statusCheckRollup data — absent on some older API responses;
            # treat as grandfathered (graceful default, not a violation).
            continue

        # Look for a check named "ci" with conclusion SUCCESS
        ci_success = any(
            c.get("name") == "ci" and c.get("conclusion") == "SUCCESS"
            for c in rollup
        )
        if not ci_success:
            ci_checks = [
                f"{c.get('name')}:{c.get('conclusion')}"
                for c in rollup
                if c.get("name") == "ci"
            ]
            checks_summary = (
                ", ".join(ci_checks) if ci_checks
                else "no 'ci' check in rollup"
            )
            violations.append({
                "type": "merged_without_ci",
                "pr_number": pr_number,
                "merged_at": merged_at,
                "detail": (
                    f"PR #{pr_number} merged at {merged_at} without SUCCESS 'ci' check "
                    f"({checks_summary})"
                ),
            })
    return violations


# ---------------------------------------------------------------------------
# Main compare function
# ---------------------------------------------------------------------------

def compare(spec: dict, trail: dict) -> dict:
    """Pure comparison function: evaluate all SPEC predicates over the trail.

    Args:
        spec: output of pipeline_spec.get_spec()
        trail: output of collector.get_trail()

    Returns:
        {
          "prd_number": N,
          "run_pass": bool,
          "edges": {
              "E-*": {"state": str, "detail": str, "evidence": str, "required": str}
          },
          "violations": [{type, detail, ...}],
          "unexpected": [],
        }

        On failed/not-found collection the return is an error-sentinel:
        {
          "prd_number": N,
          "run_pass": false,
          "error": "<human-readable message>",   # present on any collection failure
          "not_found": true,                      # present when the issue does not exist
          "edges": {}, "violations": [], "unexpected": [],
        }

    Edge states:
      confirmed / missing / not-reached / not-exercised / not-evaluated / unexpected
      runtime-confirmed / runtime-unobserved / not-observable / unmeasurable

    State "not-evaluated" is the initial placeholder for runtime/unmeasurable edges.
    After _apply_runtime_observation(), all runtime-tier edges get runtime states and
    all unmeasurable-tier edges get state="unmeasurable" — no edge stays "not-evaluated".
    "not-evaluated" in the final report is a bug (zero-closure invariant, ADR-0055 D5).

    Run PASS := every required:always github-tier edge is "confirmed" AND zero violations.
    Runtime states never affect run_pass (ADR-0055 D2).
    """
    # --- Guard: failed or nonexistent collection must never render as PASS ---
    # collector.get_trail() signals failure via a non-empty "error" key and/or
    # a missing "prd_title" (which is only present after a successful fetch).
    # "not_found" is set when the issue itself does not exist in GitHub.
    trail_error = trail.get("error")
    trail_title = trail.get("prd_title")
    if trail_error or not trail_title:
        prd_number = trail.get("prd_number")
        collector_status = trail.get("collector_status", "")
        is_not_found = "no_issue" in collector_status or "not found" in (trail_error or "").lower()
        sentinel: dict = {
            "prd_number": prd_number,
            "run_pass": False,
            "error": trail_error or f"Collection failed: collector_status={collector_status!r}",
            "edges": {},
            "violations": [],
            "unexpected": [],
        }
        if is_not_found:
            sentinel["not_found"] = True
        return sentinel

    edges_result: dict[str, dict] = {}
    spec_edges = spec.get("edges", [])

    for edge in spec_edges:
        eid = edge["id"]
        evidence = edge.get("evidence", "github")

        # Non-github tiers are explicitly not evaluated (by design — not a gap)
        if evidence != "github":
            edges_result[eid] = {
                "state": "not-evaluated",
                "detail": f"evidence tier '{evidence}' — not compared against trail by design",
                "evidence": evidence,
                "required": edge.get("required", "always"),
            }
            continue

        evaluator = _EDGE_EVALUATORS.get(eid)
        if evaluator is None:
            # github-tier edge with no evaluator: this should not happen in v2
            # but degrade gracefully rather than crash
            state = "not-evaluated"
            detail = "no evaluator implemented for this edge"
        else:
            try:
                state, detail = evaluator(trail)
            except Exception as exc:
                state = "not-reached"
                detail = f"evaluator error: {exc}"

        edges_result[eid] = {
            "state": state,
            "detail": detail,
            "evidence": evidence,
            "required": edge.get("required", "always"),
        }

    # Violation detection — five detectors (slice #767 adds merged_without_ci)
    violations = (
        _detect_unreviewed_merges(trail)
        + _detect_missing_closes_slice(trail)
        + _detect_slice_no_pr(trail)
        + _detect_prd_closed_open_slices(trail)
        + _detect_merged_without_ci(trail)
    )

    # Run PASS: ALL required:always github-tier edges are "confirmed" OR "not-exercised"
    # (the latter covers bootstrap-mode cases where the convention wasn't in use yet,
    # or where the conditional path was never triggered) AND zero violations.
    # Excluded states:
    #   not-evaluated — non-github tier (never compared by design)
    #   not-exercised — condition not triggered / bootstrap-mode; NEVER red
    #   not-reached   — run still in-flight; NEVER red
    # Only "missing" and "unexpected" count against run_pass for required:always edges.
    #
    # Non-vacuous guard: run_pass requires at least one required:always github-tier edge
    # to have actually evaluated to "confirmed". An all() over an empty iterator returns
    # True, which would produce a spurious PASS for runs with no evaluated edges
    # (e.g. very early PRDs, test PRDs, or partially-failed collections that slipped
    # through the sentinel above). The >=1-confirmed check closes that gap without
    # regressing valid in-flight PRD runs whose non-confirmed edges are "not-reached"
    # or "not-exercised" (those are excluded from the all() filter already).
    _PASS_EXCLUDING = {"not-evaluated", "not-exercised", "not-reached"}
    _github_always_evaluated = [
        e for e in spec_edges
        if (
            e.get("required") == "always"
            and e.get("evidence") == "github"
            and edges_result[e["id"]]["state"] not in _PASS_EXCLUDING
        )
    ]
    _confirmed_count = sum(
        1 for e in _github_always_evaluated
        if edges_result[e["id"]]["state"] == "confirmed"
    )
    run_pass = (
        len(_github_always_evaluated) > 0
        and _confirmed_count == len(_github_always_evaluated)
        and len(violations) == 0
    )

    report = {
        "prd_number": trail.get("prd_number"),
        "run_pass": run_pass,
        "edges": edges_result,
        "violations": violations,
        "unexpected": [],
    }

    # ADR-0055 D1/D2: apply runtime observation pass.
    # Adds runtime_edges, runtime_coverage, capture_liveness.
    # NEVER modifies run_pass, violations, or unexpected.
    report = _apply_runtime_observation(report, trail)

    return report
