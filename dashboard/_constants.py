"""
dashboard/_constants.py — single-source project-wide constants.

Import-nothing by design (ADR-0088 D4): with zero imports of its own, no
sibling module under dashboard/ can ever form an import cycle through this
file. Moved here from the retired HTTP server module (ADR-0088 D1), which
was the sole prior canonical home; discovery.py and health.py previously
held their own private copies (issues #1257, #1465) kept in step only by
comments. CI CHECK 7(a) parses KNOWN_CRITICS from this file and asserts it
is the only definition under dashboard/ and tools/.

ADR-0089 D4: `GRANDFATHER_UNTIL` + `grandfathered()` replace this repo's PR
numbers and commit shas as health-check bind-forward anchors. A PR/PRD
number resets to 1 in a project built from this template, and a commit sha
does not exist at all in a copy without history; a merge instant is
per-repo history-agnostic and keeps the module's import-nothing property
(the predicate is a plain string compare over normalized UTC ISO-8601
instants of identical width).

Exports:
    KNOWN_CRITICS — the closed set of critic-subagent stems (each has a
    matching .claude/agents/<stem>.md file).
    GRANDFATHER_UNTIL — check id -> normalized UTC 'YYYY-MM-DDTHH:MM:SSZ'
    instant. A subject (PR/PRD) merged/closed at or before the instant is
    grandfathered; anything later is scored.
    grandfathered(check_id, merged_at) — the predicate.
"""

KNOWN_CRITICS = {
    "reviewer",
    "prd-critic",
    "adr-critic",
    "slicer-critic",
    "backlog-critic",
    "codebase-critic",
}

# ADR-0089 D4: each instant is chosen so the anchored check's verdict is
# unchanged from the PR-number/sha rule it replaces (measured by the
# slicer-critic and re-verified by tests/test_grandfather_set_equality_1514.py
# over every merged PR of this repository). Values are this repository's own
# measured history — they carry no meaning in a project built from this
# template, where every PR merges after every instant here (ADR-0089 D4).
GRANDFATHER_UNTIL = {
    # Replaces comparison.py's former PR-number CI-gate cutoff (pr_number
    # < 711). PR #710's mergedAt (last grandfathered PR under the old rule).
    "CI-GATE": "2026-06-11T09:03:09Z",
    # Replaces health.py's former PR-number PROOF-PRESENCE cutoff (number
    # > 788 scored). PR #788's mergedAt (last grandfathered PR under the
    # old rule).
    "PROOF-PRESENCE": "2026-06-12T09:40:43Z",
    # Replaces health.py's former PR-number PROOF-INTEGRITY cutoff (number
    # <= 839 grandfathered). #830's mergedAt (last grandfathered
    # browser-route PR under the old rule; #839 itself was not a
    # browser-route PR).
    "PROOF-INTEGRITY": "2026-06-16T10:46:59Z",
    # Replaces health.py's former commit-sha RECORD-VS-GH anchor plus its
    # hand-kept single-PR-number exception set. PR #1089's own mergedAt
    # makes the exception unnecessary: #1089 (the walking-skeleton PR that
    # first created the span emitter) is now grandfathered outright rather
    # than carved out by number.
    "RECORD-VS-GH": "2026-08-02T02:11:00Z",
    # Replaces health.py's former shared commit-sha ADR-0076 anchor, used by
    # all three reconcilers below (one anchor instant, per ADR-0076's
    # binding paragraph). The anchor commit's own committer-date, normalized
    # to UTC (2026-08-02T16:41:57+02:00 -> 2026-08-02T14:41:57Z).
    "SLICE-VS-PR": "2026-08-02T14:41:57Z",
    "MERGED-WITHOUT-VERDICT": "2026-08-02T14:41:57Z",
    "CLOSED-PRD-VS-QA": "2026-08-02T14:41:57Z",
    # Replaces health.py's former PRD-issue-number SILENT-DRIFT cutoff
    # (prd_num < 799). Keyed on the PRD's own createdAt (the check already
    # reads this field). Instant is 1 second before issue #799's own
    # createdAt (2026-06-12T11:45:26Z), so #799 itself, if it were a PRD,
    # would NOT be grandfathered — matching the old strict `<` comparison.
    "SILENT-DRIFT": "2026-06-12T11:45:25Z",
    # Replaces health.py's former slice/PR-number TEST-ORDERING cutoff
    # (closing-slice number < 816, falling back to the PR's own number when
    # no closing slice is known). Keyed on the PR's own mergedAt: gh's
    # closingIssuesReferences shape carries no createdAt (only id, number,
    # repository, url), so the closing slice's createdAt is not available
    # without an extra per-issue fetch this check does not otherwise make;
    # the PR's own mergedAt is the uniform, already-available substitute for
    # BOTH the old primary and fallback branches. Instant is 1 second before
    # issue #816's own createdAt (2026-06-12T15:16:01Z) — verified against
    # every one of this repository's 82 real merged fix/* PRs: zero
    # mismatches against the old number-based rule.
    "TEST-ORDERING": "2026-06-12T15:16:00Z",
}


def grandfathered(check_id: str, merged_at: str) -> bool:
    """True when `merged_at` is at or before `check_id`'s GRANDFATHER_UNTIL
    instant (ADR-0089 D4): the subject is grandfathered (never scored).

    A falsy/missing `merged_at` is never grandfathered — an unknown merge
    time is scored, not silently exempted. An unknown `check_id` (absent
    from GRANDFATHER_UNTIL) is also never grandfathered — there is no
    amnesty window for a check this table does not name.

    String compare only, to keep this module's import-nothing property: safe
    because every value here, and every `merged_at`/`closedAt` gh supplies,
    is a normalized UTC ISO-8601 'YYYY-MM-DDTHH:MM:SSZ' instant of identical
    fixed width, so lexicographic order matches chronological order.
    """
    if not merged_at:
        return False
    cutoff = GRANDFATHER_UNTIL.get(check_id)
    if not cutoff:
        return False
    return merged_at <= cutoff
