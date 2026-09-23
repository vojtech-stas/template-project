---
id: "ADR-0087"
title: "gh-fetch provenance: an unconfirmed GitHub answer never reads as a clean one"
status: "accepted"
date: "2026-09-23"
scope: "verification"
rule_ids: []
supersedes: []
superseded_by: []
---

# 0087 — gh-fetch provenance: an unconfirmed GitHub answer never reads as a clean one

**Status:** Accepted on joint APPROVE (prd-critic + adr-critic, ADR-0004 D1); ships in the companion PRD's slice 1 (ADR-0003 D8).
**Date:** 2026-09-23

**Extends:** ADR-0070 D2. The six-condition set of `RELEASE-READY` is unchanged. This ADR fixes what evidence satisfies condition (e) (D3), and makes (e) count every `needs-human` item that D2 names, pull requests included (D4). ADR-0070's own Enforcement (rule #23) paragraph names D2's shadow as "a gate reporting ready on incomplete conditions", and this ADR closes one live instance of it. **Also extends** ADR-0083 D3: D2 below states the invariant `QUERY-HONESTY` fails on, so the check fails only on an owed invariant. **Also extends** ADR-0064 D3: `QUERY-HONESTY` is a row in the one check registry. **Supersedes:** nothing.

**Baseline.** Every line number, count and quoted output below was derived at `origin/develop` = `2980962` on 2026-09-23. ADR-0070 D1 makes `develop` the integration branch that slices branch from. `origin/main` (`f7378de`) lags 41 commits behind, and its `decisions/` ends at 0081. ADR-0083 and ADR-0088, both cited here, are accepted on `develop` but not yet promoted, so a default-branch `gh api` lookup returns 404 for them. That is a promotion-lag artifact, not a missing file.

**Number.** The operator directed 0087 on 2026-09-23. ADR-0086 is claimed by open PR #1476's draft, and ADR-0088 is the accepted dashboard-retirement decision cited below. If this ADR lands before PR #1476, `decisions/README.md` gains a gap note for 0086, following the existing `> **Note on …**` entries there. If another draft lands at 0087 first, the implementer re-derives the next unused integer at landing time. Nothing below depends on the specific integer.

## Context

**The defect, reproduced on 2026-09-23 at `2980962`.** `tools/promote.sh:73` advances `main` only when `python3 dashboard/health.py --check RELEASE-READY` prints a line matching `^PASS: RELEASE-READY`. In the test below, conditions (a)–(d) and (f) were pinned through the check's documented test seams (`health.py:5431-5438`), and `gh`'s directory was removed from `PATH`. The check printed:

```
PASS: RELEASE-READY — gate open: … (d) streak intact, (e) zero needs-human, (f) guardrail-tripwire pass
```

GitHub could not be asked, and the gate reported a confirmed zero. The code says so itself. Condition (e) (`health.py:5644-5686`) sets `nh_count = 0` on three paths:
- `:5668-5671`, under the comment `# gh unavailable or timeout → treat as 0 to avoid false holds`;
- `:5672-5674`, when any exception occurs;
- `:5652-5654`, when the test injection is not an integer.

**Where the provenance is lost.** `dashboard/gh_cache.py::gh_fetch()` (`:92-182`) labels every answer with `GhResult.source`, one of `live`, `cache`, `stale` or `computing` (`:35-54`). The labelling is correct. `gh_fetch`'s only consumer is `dashboard/health.py::_health_gh_fetch()` (`:69-106`), and that function collapses the label into a bare `(rc, out)` at `:93-95`:
- a `stale` answer (gh failed on this call and the value is a last-known one) comes back as success, `rc 0`;
- `computing` becomes `rc 1, ""`.

Every health check reaches GitHub through this function: 17 textual call sites, 12 registered checks plus the condition-(a) helper `_fetch_github_ci_conclusion` (`:255`). No ADR decided the stale-as-success mapping. It was PRD #993's design (slices #995/#996) so that a served dashboard row could keep rendering a last-known value inside a long-running process. ADR-0088 D1 deleted that served dashboard. Every surviving consumer runs as a one-shot CLI process, and there "stale" can only mean that GitHub just failed.

**Provenance alone is not enough: the label-filtered path can be wrong while GitHub answers successfully.** This repository was renamed on 2026-09-16. A clone whose `origin` still names the old slug gets correct answers from REST. Every label-filtered listing, however, comes back empty and reports success. The same resolution is reproducible today with `GH_REPO=vojtech-stas/project-claude`:
- `gh issue list --label prd --state all` returns **0** issues;
- `gh api repos/{owner}/{repo}/issues?labels=prd&state=all` returns **136**;
- with the current slug, both return 136;
- `gh pr list --label trivial` returns 0 against 50;
- unfiltered `gh pr list` is unaffected (20 against 20).

Such an answer arrives with `source=live`, so no provenance label can catch it. `health.py` sends seven label-filtered queries (arguments at `:2470`, `:2973`, `:3269`, `:3746`, `:4007`, `:4142`, `:5660`). Here is what four of their checks print under the old slug, compared with the current one:

| Check | Current slug | Old slug |
|---|---|---|
| `SLICE-VS-PR` | FAIL | PASS `0/0 … covered` |
| `CLOSED-PRD-VS-QA` | FAIL | PASS `0/0 … covered` |
| `SILENT-DRIFT` | PASS over 27 PRDs | PASS over `0 auditable` |
| `SPEC-COVERAGE` | PASS | WARN `no PRD-labeled issues found` |

Condition (e) sends the seventh query, so it reads zero under the old slug too.

**Audit of the whole class, all 17 call sites at `2980962`.** The class is a caller that reports a state it did not observe. It has four members.

Two of them map "could not confirm" to a **clean verdict**:
- condition (e) above;
- `BRANCH-TOPOLOGY` step 5 (`:5244-5261`, captured as #1448): `pr_base_ok = True` survives a failed fetch, and the row printed `PASS: BRANCH-TOPOLOGY … branch-protection: API unavailable (WARN)` with `gh` absent.

Two more map it to an **empty list and then assert an absence**:
- `CAPTURE-SHAPE`'s `_fetch_issues` (`:3265-3279`). With `gh` absent it prints `WARN: CAPTURE-SHAPE — no root-cause-labeled issues found`.
- `RESIDUAL-RATIO`'s `_fetch_closed_prds` (`:2969-2982`) and `_fetch_comments` (`:2984-2997`). It prints `no closed PRDs found`, and it computes its ratio over whichever PRDs' comments happened to load.

Every other caller's unavailable branch already says `unavailable` or `unverifiable`.

**Condition (e) also counts only part of what it gates on.** ADR-0070 D2's condition (e) is "zero open `needs-human` items", and CLAUDE.md I5 puts that label on pull requests. But (e) queries `gh issue list` (`:5659-5663`), which excludes pull requests. So the gate asserts a zero over a set it never asked about. There are no open `needs-human` pull requests today, so there is no live instance, only an unobserved one.

The existing degrade test, `tests/test_gh_cache_health_firing_996.py`, asserts a bare WARN for five callers under a `computing` seam, and never checks their detail. It asserts no verdict for `BRANCH-TOPOLOGY` (`:170-180`) and does not cover `RELEASE-READY` at all. The same file asserts the stale-as-success mapping (`:222-247`).

**The rest of `RELEASE-READY`'s GitHub surface, stated so that nothing is silently out of scope:**
- Condition (a) falls back to running `tools/ci-checks.sh` locally when GitHub is unavailable (`:5486-5505`). It reports that run's own exit status, labelled `local fallback`. That is substitute evidence, not a default.
- Condition (c) reads PRs through `collector.py`'s own gh runner, not the seam. It returns the same no-data WARN whether GitHub is up or down. `collector.py:815` requests no `files` field, and after ADR-0088 D5 no tracked `*.html` remains to route a PR to the browser class (`git ls-files '*.html'` = 0). So an outage adds no false green there that is not already present. That vacuity is a separate defect.
- Conditions (d) and (f) read only git and local logs.

**Why an ADR.** These changes do three things. They reverse a standing contract that callers rely on (stale as success). They introduce a provenance vocabulary that every future caller of the seam inherits. And they make one registry row a precondition of every label-filtered answer. A future maintainer would ask why a healthy-looking `--check SLICE-VS-PR` reads WARN "unavailable" while `gh` works, and this record answers that.

## Decisions

### D1 — The health seam reports provenance, and only a confirmed answer reads as success

`_health_gh_fetch()` remains the single function through which `dashboard/health.py` reaches `gh`, and it keeps its default `(rc, out)` shape. It gains a keyword, `with_source=True`, which returns `(rc, out, source)`. The provenance vocabulary is closed:
- **Confirmed:** `live` (this call succeeded) and `cache` (a fresh cached success within the caller's TTL).
- **Unconfirmed:** `stale` and `computing`, which are `GhResult`'s failure sources, and `unverified`, which only D2's attestation produces.

`rc` is 0 exactly when the source is confirmed. An unconfirmed answer returns `rc 1` with an empty payload, so no caller can parse an unconfirmed value by accident. The import-fallback branch (`:96-106`) labels its answer `live` when gh exits 0 and `computing` otherwise. `dashboard/gh_cache.py` is unchanged: its labels are already correct, and `unverified` is a label of the seam, not of `GhResult`.

- **Mechanism (rule #23, ADR-0056 D1).** A regression test in `tests/`, run by CI's pytest step per ADR-0067 D1. It pins `rc`, payload and label for every source and for both branches of the seam. Per ADR-0067 D2 it is committed before the fix and fails against `2980962` (stale currently yields `rc 0`). The contrary assertion at `tests/test_gh_cache_health_firing_996.py:222-247` is rewritten to this contract in the same slice. **How this check can fail:** map any unconfirmed label to `rc 0`, or drop the label from the three-tuple, and it reddens.
- **Parsimony (ADR-0056 D2, clause (b)).** No producer is missing. `gh_fetch` already emits the label, and the loss happens at the seam. No check and no test pins the seam's mapping; the one test that touches it pins the opposite. `tools/check-verdict-presence.py` has a tri-state gh contract (`:146-167`), but that contract is private to one CI script, and `health.py` never uses it.
- **Shadow (ADR-0056 D2, clause (c)).** *The confident empty*: a GitHub that could not be asked, handed to callers as though it had answered. A future audit tests it by forcing each source through the seam and checking that only `live` and `cache` come back as success.
- **Bootstrap-mode (ADR-0004 D2).** Binds forward from the merge of the slice that ships it. Health-check results computed before that merge are not re-judged.

### D2 — The label-filtered query path is attested against REST before any caller may read it as confirmed (`QUERY-HONESTY`)

A new registered check, `CHECK_REGISTRY["QUERY-HONESTY"]` (ADR-0064 D3), compares two paths to the same canary. The canary is the label `prd` across all states. Every promotion presupposes a shipped PRD, and issues are never deleted, so this set is non-empty wherever the gate can matter.
- **Label path:** `gh issue list --label prd --state all --limit 1000 --json number`. This is the same subcommand shape the seven callers use.
- **REST path:** `gh api "repos/{owner}/{repo}/issues?labels=prd&state=all&per_page=100" --paginate`, with items carrying `pull_request` excluded.

Both paths resolve the repository through `gh`'s own resolution, in the same working directory and environment as the callers. No slug is written into code.

The verdicts:
- **PASS** exactly when both answers are confirmed, the REST count is greater than 0, and the two counts are equal.
- **FAIL** when both answers are confirmed and the counts differ. The detail carries `label=<n> rest=<n>`.
- **WARN** otherwise: either answer is unconfirmed, the REST count is 0 (agreement on an empty set proves nothing), or the label path returned its full `--limit`.

This decision states the invariant the label path owes: it agrees with REST on the canary. So the FAIL is on an owed invariant in the sense of ADR-0083 D3.

**The seam consumes the attestation.** Before `_health_gh_fetch()` returns any call whose arguments contain `--label` as confirmed, it requires this attestation to PASS. The attestation is memoized per process for its own queries' cache lifetime. Otherwise the seam returns `rc 1` with source `unverified`. The attestation's own label query bypasses the attestation.

The seven label-filtered callers therefore take the unavailable branch they already implement, with no edit to any of them. That makes the seam the single choke point under all seven, as the approved plan's Block 2 requires. That plan named `gh_cache.gh_fetch()` as the choke point. Re-derived on `develop`, the provenance is lost one layer up, in the seam, so the choke point is placed there (see Alternatives).

- **Mechanism (rule #23, ADR-0056 D1).** The registry row is itself a deterministic evaluator; ADR-0056 D1 lists "dashboard evaluator (health check …)". Regression tests in `tests/` drive four cases with patched seam answers: PASS, FAIL, WARN on an empty canary, and WARN when unavailable. They also assert that a `--label` call through the seam comes back `unverified` under a failing attestation and `live` under a passing one. **How this check can fail:** read a desynced empty list as confirmed, PASS on `0 == 0`, or let a `--label` call bypass the attestation, and a test reddens.
- **Parsimony (ADR-0056 D2, clause (b)).** D1 cannot see this failure, because a desynced label query succeeds and is labelled `live`. `RECORD-VS-GH` compares recorded spans with gh and trusts gh's answer. `REQUIRED-LABELS` checks that labels exist, not that filtering by them works. `SLICE-VS-PR`, `CLOSED-PRD-VS-QA` and `SILENT-DRIFT` consume the label path and are its measured victims. No existing mechanism compares two query paths.
- **Shadow (ADR-0056 D2, clause (c)).** *The vacuous green*: an integrity row that passes `0/0` because its input query silently returned nothing. It was measured above for `SLICE-VS-PR` and `CLOSED-PRD-VS-QA`. A future audit tests it by re-running the label-filtered rows under a slug whose label path is known to be empty and checking that none of them PASS.
- **Bootstrap-mode (ADR-0004 D2).** Binds forward from the merge of the slice that registers it. It does not re-audit health runs that predate it.

### D3 — No seam caller reports a state it did not observe; RELEASE-READY condition (e) is satisfied only by a confirmed zero

The class is every caller of the seam whose unconfirmed branch yields PASS, opens the gate, or states an absence as though it had been observed. By the audit above it has exactly four members, and all four are fixed.

- **Condition (e)** holds the gate (WARN, `verdict` false, `first_failing_condition` `"e"`) unless the needs-human count is confirmed. Confirmed means a confirmed source *and* a payload that parses as a JSON list. So an unconfirmed source, an unparsable payload, an exception, and a non-integer injection all hold. The detail follows ADR-0083 D5's advisory: it states the observation and the states consistent with it, and does not guess at a single cause. Two examples:
  - `needs-human count unconfirmed (source=computing: GitHub unreachable, unauthenticated, rate-limited or timed out)`;
  - `… (source=unverified: label-filtered path failed QUERY-HONESTY)`.

  A valid integer in `_RELEASE_READY_NEEDS_HUMAN_COUNT` still replaces condition (e) wholesale, with no `gh` call. Every existing injection-based test keeps its verdict.
- **`BRANCH-TOPOLOGY` step 5** reports WARN, naming `recent-PR base check unconfirmed (source=<label>)`, instead of leaving `pr_base_ok` true. This closes #1448.
- **`CAPTURE-SHAPE` and `RESIDUAL-RATIO`** report WARN, naming `unconfirmed (source=<label>)`, whenever any of their fetches is unconfirmed. `RESIDUAL-RATIO` then does not compute a ratio from partial comment data. A *confirmed* empty list keeps its existing "no … found" wording, because that absence was observed.

ADR-0070 D2's conditions are unchanged. The only change is that "zero open `needs-human` items" can no longer be asserted without having counted them.

- **Mechanism (rule #23, ADR-0056 D1).**
  - *Per instance:* regression tests, committed before their fixes per ADR-0067 D2. They cover all six non-confirmed paths of condition (e), the unconfirmed path of step 5, and the unconfirmed fetches of `CAPTURE-SHAPE` and `RESIDUAL-RATIO`. Each fails against `2980962`. They also pin the `unconfirmed` and `source=<label>` tokens in the detail.
  - *For the class:* a registry-wide invariant test. With every seam answer forced to `computing`, every registered check whose body calls `_health_gh_fetch`, and that actually consulted it, must return a verdict other than PASS. Its detail must also contain `unavailable`, `unconfirmed` or `unverifiable`.
    - It finds its subjects in `CHECK_REGISTRY` by source inspection, and it fails itself if it finds fewer than the 12 present at `2980962`, so it cannot pass on an empty filter.
    - It collects every offender and fails once, naming each.
    - Simulated against `2980962`, it names exactly `BRANCH-TOPOLOGY`, `CAPTURE-SHAPE`, `RELEASE-READY` and `RESIDUAL-RATIO`, the four class members.
    - A future caller that reports an unobserved state reddens CI without anyone having to notice it.
  - **How these checks can fail:** restore any `nh_count = 0` default, restore `pr_base_ok = True` on failure, map an unconfirmed fetch to a "no … found" detail, or add a caller that PASSes on nothing, and a test reddens.
- **Parsimony (ADR-0056 D2, clause (b)).**
  - `RELEASE-READY` is ADR-0070 D2's enforcement, and it is the defect site. ADR-0070 D2 names the conditions, not the evidence standard for each one.
  - ADR-0083 D3's reviewer rubric rule asks a new or tightened check to name the contract clause it enforces. That is authority for what the check asserts. Nothing sets a standard for passing on absent evidence.
  - `tools/promote.sh`'s `.claude/PROMOTE_OK` sentinel (`:51-56`) is a human acknowledgement. It does not re-derive GitHub's reachability.
  - The one degrade test that exists covers neither fail-open site. For the two absence-asserting checks it checks the verdict, never the detail.
- **Shadow (ADR-0056 D2, clause (c)).** *The outage that looks like a clean queue*: a promotion gate that opens, or a row that reports "none found", because GitHub could not be asked. A future audit tests it by running every registered gh-reading row with `gh` removed from `PATH` and checking that none reads PASS or claims an absence.
- **Bootstrap-mode (ADR-0004 D2).** Binds forward from the merge of the slice that ships each instance. No past promotion or health result is re-judged. The next `RELEASE-READY` evaluation after merge applies the new rule to whatever `develop` HEAD is then. The registry-wide invariant applies to every check registered after its merge, and every existing check satisfies it at merge.

### D4 — RELEASE-READY condition (e) counts every open `needs-human` item: issues and pull requests

Condition (e) adds a second query through the seam: `gh pr list --label needs-human --state open --json number`. Being label-filtered, it is subject to D2's attestation. The count is the open issues plus the open pull requests, and the gate holds when that sum is greater than 0. If either answer is unconfirmed, the gate holds under D3.

The held detail reports the total as today (`condition (e) <n> open needs-human item(s)`). This extends ADR-0070 D2: its condition reads "items" without restriction, and the code had counted a subset. A valid integer in `_RELEASE_READY_NEEDS_HUMAN_COUNT` still replaces both queries.

- **Mechanism (rule #23, ADR-0056 D1).** A regression test, committed before its fix per ADR-0067 D2. A seam reporting one open `needs-human` pull request and zero issues must hold the gate with `first_failing_condition` `"e"`. Against `2980962` the check returns `PASS` with an empty `first_failing_condition` (verified). **How this check can fail:** drop the pull-request query, or subtract it from the sum, and the test reddens.
- **Parsimony (ADR-0056 D2, clause (b)).** The I5 escalation surface labels the pull request and comments on the parent PRD. Nothing mechanical reads that label on pull requests:
  - `RELEASE-READY` is the only gate that consumes `needs-human`, and it asks about issues only.
  - `/ship`'s triage uses `needs-human-check`, a different label.
  - The operator's `gh pr list --label needs-human` at session start is a human habit, not a check.
- **Shadow (ADR-0056 D2, clause (c)).** *The escalation the gate cannot see*: a pull request strict-stopped at round 3 while the gate promotes past it. A future audit tests it by labelling a throwaway pull request `needs-human` and checking that `RELEASE-READY` holds on condition (e).
- **Bootstrap-mode (ADR-0004 D2).** Binds forward from the merge of the slice that ships it. There are no open `needs-human` pull requests at `2980962`, so nothing held today changes state.

## Consequences

- **More holds during real outages.** `RELEASE-READY` will hold during genuine GitHub outages, auth expiry, rate limits and slug desync, where it used to open. That is the intended effect. Per ADR-0070 D3, a held gate is recorded with its failing condition and re-evaluated at the next green-develop checkpoint.
- **This change needs the operator's acknowledgement to reach `main`.** ADR-0070 D4 lists "the release-gate definition (the `RELEASE-READY` check + promotion tooling)" in the guardrail-machinery set, and `dashboard/health.py` sits in `_GUARDRAIL_PATHS` (`health.py:894-910`). So the promotion batch carrying this change holds for the operator's explicit acknowledgement. Separately, `tools/promote.sh` today requires `.claude/PROMOTE_OK` for every promotion (`:51-56`). Neither fact is changed here, and no new human-blocking step is added.
- **Six label-filtered rows read WARN "unavailable" or "unconfirmed" while the label path is unattested.** Examples are `SPEC-COVERAGE` and `SLICE-VS-PR`. Before, they read whatever an empty list implied, including PASS and "none found".
- **Cost:** every process that makes a label-filtered call now makes about four extra `gh` calls, once. Condition (e) makes one more call, for pull requests. `QUERY-HONESTY` compares counts at two instants, so a `prd` issue created between its two queries produces a transient FAIL. That transient FAIL holds the gate, and a re-run clears it. Retrying is rejected below.
- **A fresh host holds its gate until its first PRD exists.** Until then, condition (e) cannot be attested and holds. No promotion can matter before a PRD has shipped.
- **Test churn:** `tests/test_gh_cache_health_firing_996.py:222-247` changes meaning. Stale is now unconfirmed, because the served-row consumer it protected was deleted by ADR-0088 D1.
- **Rule layer:** none added. `rule_ids` is empty, so `tools/gen_rules.py`'s `RULE_IDS_BASELINE` (91 at `2980962`) is untouched by this ADR. These obligations bind one function and one check, and each one's enforcement is named above. An always-loaded rule would put its cost on every session for a concern that arises only when editing `dashboard/health.py`.

## Alternatives considered

- **Let a `stale` count proceed with a "(stale)" marker.** This was the round-3 draft's choice. Rejected: `stale` means GitHub failed on this very call. In a one-shot CLI process it is reachable only by re-fetching the same query after its TTL, so it proves nothing about the present, and it adds a second passing path to a fail-closed gate.
- **Count needs-human items through REST instead of attesting the label path.** Rejected. It fixes one of seven callers and leaves the measured `SLICE-VS-PR` and `CLOSED-PRD-VS-QA` false greens in place. It also forks query semantics, because the REST issues endpoint includes pull requests.
- **Put the fix inside `gh_cache.gh_fetch()`, the plan's literal choke point.** Rejected after re-derivation. `gh_fetch` already reports provenance correctly, and the loss happens in its only consumer. Running the canary inside a generic cache would recurse, since the canary's own queries pass through it, and would change `GhResult`'s documented four-value vocabulary.
- **Fix each of the seven label-filtered callers in place.** Rejected. That is seven edits to reach what one seam edit reaches, and any future label-filtered caller would still be unprotected.
- **Add a seventh `RELEASE-READY` condition, "(g) QUERY-HONESTY PASS".** Rejected. It changes ADR-0070 D2's condition set, which would require superseding it. Condition (e) already holds on `unverified` through the seam, with the condition set untouched.
- **Retry, or lengthen the timeout, before failing closed.** Rejected. A retry does not distinguish an outage from a blip. A desynced label query returns success immediately, so a retry would never fire for it.
- **Rely on the operator's `.claude/PROMOTE_OK` acknowledgement to notice an outage.** Rejected. A mechanical gate exists so that the operator does not have to re-derive GitHub's reachability by hand at promotion time.
- **Wire `QUERY-HONESTY` into `tools/ci-checks.sh`.** Rejected. CI resolves the repository from its own workflow context, so the stale-remote desync this canary exists for does not occur there. A GitHub-side divergence would redden every unrelated PR, and the check's real consumer, the seam, already runs it wherever label-filtered answers are read.
- **Leave #1448, `CAPTURE-SHAPE`, `RESIDUAL-RATIO` and the pull-request gap as separate captures.** Rejected. The operator's rule of 2026-09-23 is that a bug found during a run is fixed in that run, not filed. All four are members of this ADR's class, and the D3 invariant test cannot go green unless the whole class is fixed.

## Propagation

Not triggered, because this ADR supersedes nothing. For completeness, two runtime prompts describe condition (e): `.claude/skills/ship/SKILL.md:64` and `:317`. Both say "`needs-human` items" without restricting it to issues, which is consistent with D4. Disposition: no edit.

## Open questions deferred

- **`PROOF-INTEGRITY`'s vacuity.** Condition (c) never inspects a PR, as shown in Context. Repairing it is a `PROOF-INTEGRITY` redesign after ADR-0088 D5, and the orchestrator is filing it separately.

## References

- ADR-0070 D2 (the gate and its condition (e)), D3 (a held gate is recorded and re-evaluated), D4 (guardrail-machinery promotions wait for the human); ADR-0070's Enforcement (rule #23) paragraph (D2's shadow).
- ADR-0083 D3 (a check may only assert an owed invariant, and its permanently-green mirror), D5 (advisory: report the observation and the consistent states).
- ADR-0088 D1 (served dashboard deleted, which removes stale-as-success's consumer), D5 (`dashboard/**` leaves the browser class, which is why condition (c) is vacuous).
- ADR-0064 D3 (health.py as the check registry); ADR-0056 D1/D2 (rule #23; the mechanism, parsimony and shadow test); ADR-0067 D1/D2 (tests in CI; test commit precedes fix commit); ADR-0004 D1/D2 (joint gate; bootstrap-mode); ADR-0003 D8 (macro-ADR ships in slice 1).
- Issues: #1449 (the round-3 escalation this restarts, per operator decision D460 = option c, 2026-09-23), #1448 (the `BRANCH-TOPOLOGY` sibling, closed by D3), PRD #993 (origin of the stale-as-success mapping), PR #1476 (also edits `dashboard/health.py`).
