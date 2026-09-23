---
id: "ADR-0089"
title: "Per-repo pipeline identity: configured branch roles and repo-agnostic history anchors"
status: "accepted"
date: "2026-09-23"
scope: "pipeline"
rule_ids:
  - "PIP-031"
  - "PIP-032"
supersedes:
  - "ADR-0070 D1"
  - "ADR-0004 D3"
  - "ADR-0041 D2"
  - "ADR-0045 D3"
superseded_by: []
---

# 0089 — Per-repo pipeline identity: configured branch roles and repo-agnostic history anchors

- **Status:** Accepted
- **Date:** 2026-09-23
- **Supersedes:** ADR-0070 D1 (partial), ADR-0004 D3 (partial), ADR-0041 D2 (partial), ADR-0045 D3 (partial). Each clause is scoped exactly in the Supersession ledger below.
- **Extends:**
  - ADR-0008 D6: its scope list gains the integration-branch creation step and a second protection target, the integration branch. Its R1+R2 bullet on `main` reads through D1 as the release-branch PUT (ADR-0089 D2).
  - ADR-0042 D2, as already extended by ADR-0070 D1: the R4 targets are both configured branches, the integration branch and the release branch (ADR-0089 D2).
  - ADR-0058 D3, as already extended by ADR-0070 (its ff-sync target): the guard's ff-sync target is the configured integration branch, and its hard-align path uses the configured release branch (ADR-0089 D1). Clause (b)'s `prune` reference is not extended here; it is a pre-existing divergence (see Left standing).
  - ADR-0062 D3, as already extended by ADR-0070: the green-pointer lag is measured against the configured integration branch (ADR-0089 D1).
  - ADR-0083 D3: its corollary that a check's subject set is defined, not assumed. ADR-0089 D4 applies it to grandfather anchors.
  - ADR-0004 D2: bootstrap-mode.
- **Ships with:** PRD "Make the pipeline installable on a new project: configured branch roles, bootstrapped integration branch, repo-agnostic anchors", slice 1 (ADR-0003 D8).

**Bootstrap-mode (ADR-0004 D2):**
- Each decision binds from the merge of the slice that implements it.
- CHECK 29 has two arms:
  - Arm (a) lands in the slice that brings its count to zero.
  - Arm (b) lands in the anchor slice.
  - Each arm gates its own PR.
- Nothing is retroactive. `decisions/`, `qa-proof/` and `docs/decision-log/` keep their text as accurate records of the time they were written.

## Context

The operator is about to start a new project with this repository as its template (plan Q7: "universal: no hardcoded branch, slug or path"). The operator restarted this work from scratch on 2026-09-23 (D460; escalation #1446). Porting onto an existing host such as WEDLY stays deferred (D381). The pipeline was written for exactly one repository, and its executable layer compiles in that repository's identity. Measured on `develop` at 2980962:

| Identity carried as a literal | Where | Measured |
|---|---|---|
| Branch names in git/gh operations | `tools/`, `dashboard/`, `.claude/hooks/`, `bootstrap.sh`, `.githooks/pre-commit` | 49 operation lines in 12 files, plus 47 message lines |
| `origin/main` as the agents' base | six `.claude/agents/*.md` prompts | 35 lines |
| `origin/develop` command tokens | `.claude/skills/ship/SKILL.md` | 2 lines |
| History anchors | `dashboard/health.py`, `dashboard/comparison.py` | 7 constants feeding 7 checks: PR numbers 788, 839, 711 (711 defined twice), shas `0d8e6d0` and `f271843`, exception `{"1089"}` |
| Repo slug | live tooling | 0. It is derived at runtime by `collector._repo_slug()`, `bootstrap.sh resolve_origin_slug()` and gh `{owner}/{repo}` |

Four consequences for a repository that is not this one:

1. **No integration branch.**
   - A template copy has only its default branch, and `bootstrap.sh` never creates one.
   - Step 5 then protects a branch that does not exist and reports the failure as a skip.
   - Nothing provisions ADR-0075 D5's gate (1) on the release branch. Step 5 protects only `develop`, and this repository's `main` protection (R1+R2 plus the required `ci` check, `enforce_admins` off, the same payload step 5 puts on `develop`) is a hand-set server setting that no tracked file applies.
   - Every verb that resolves `origin/develop` fails.
   - CI CHECK 3 degrades to `VACUOUS` rather than failing.
2. **Wrong base.**
   - `implementer.md` cuts slices from `origin/main` (#1293).
   - `reviewer.md` diffs against `origin/main` and asserts `baseRefName == "main"` (#1438).
   - prd-critic and adr-critic check ADR existence on `origin/main` (#1394). That branch is 41 commits behind `develop` today.
   - `tools/pipe/pr-open` passes no base at all, so a PR lands on GitHub's default branch, which is the promotion-gated one (#1143, PR #1141).
   - ADR-0070's own Consequences announced this migration for "guards/CI/dashboard/skills". The agent prompts were never swept.
3. **Fixed names.** A project cannot choose other names for the two roles ADR-0070 D1 defines.
4. **Inherited amnesty (#1292).**
   - PR numbers restart at 1, so PROOF-PRESENCE, the only enforcer of rules #15 and #20, grandfathers a new project's first 788 PRs.
   - In a copy without history, the four sha-anchored checks can only say "could not resolve … anchor".

The theme of this ADR is one sentence. This repository's identity — its branch names and its history anchors — must not be compiled into the pipeline's executable layer, because the pipeline now runs in repositories that are not this one.

## Decisions

### D1 — Branch roles are per-repo configuration, resolved in one place

The two roles are ADR-0070 D1's:
- **integration:** the autonomous PR-merge target.
- **release:** advanced only by promotion.

**Where the names live.** The names live in a tracked `.claude/pipeline.conf` (`integration_branch=`, `release_branch=`). This repository's file holds `develop` and `main`. The file sits under `.claude/`, outside the `pipeline/` subtree the packaging work plans. A host's value therefore survives upgrades.

**One parser.** `tools/pipeline_config.py` is the only parser:
- It is an importable Python module, used by `dashboard/`, the Python hooks and `tools/pipe/*`.
- It is also a CLI, `python3 tools/pipeline_config.py integration|release`, used by bash and by prompt commands.
- It reads the config of the repository the caller operates in.
- An absent file resolves to `develop`/`main`. Those defaults live nowhere else.
- A present but malformed file (unknown key, empty value, a name `git check-ref-format --branch` rejects, or both roles equal) is refused with a non-zero exit and file:line.

**How sites use it.**
- Every executable site, and every command in an agent or skill prompt, names a branch only through the resolver.
- Each site keeps the role its code uses today: a site that uses `develop` resolves integration; a site that uses `main` resolves release.
- No caller carries its own fallback literal.
- A resolver failure inside a hook takes that hook's existing error path (HOK-008 ERROR beacon; the guards' existing fail-open policy). No new policy is introduced.

This supersedes only ADR-0070 D1's naming clause; see the ledger.

**Subject set of CHECK 29 arm (a).** This is defined, not assumed (ADR-0083 D3's corollary).
- **Included:** tracked files under `tools/`, `dashboard/`, `.claude/hooks/` and `.githooks/`, plus `bootstrap.sh`, `.claude/agents/*.md` and `.claude/skills/*/SKILL.md`.
- **Excluded:**
  - `*.md` under `tools/` and `dashboard/`. These are operator docs that describe this repo's configured values.
  - `tools/gen_rules.py`. It renders accepted decision text verbatim.
  - The resolver itself.
  - Comment-only lines in code files.
- **Token shapes** (`b` ∈ {develop, main}):
  - `origin/b`
  - `origin b`
  - `refs/heads/b`
  - `branches/b`
  - `--base b`
  - a quoted `"b"` or `'b'`
  - a `:b` refspec destination
  - `checkout|rev-parse|reset --hard [-B x] b`

**Enforcement:**
- **Mechanism:** CI CHECK 29 (REPO-IDENTITY-LITERALS) arm (a). It FAILs naming file:line. At 2980962 it would report 121 lines in 21 files, and it lands when the sweep reaches 0. The resolver's own contract is covered by a regression test for defaults, refusal and the CLI.
- **Parsimony:**
  - CHECK 25 (VER-011) scans every tracked file for machine-local path detail through one path+substring allowlist. Branch literals need a different subject set, because docs legitimately name the configured defaults. Folding them in would change VER-011's contract.
  - BRANCH-TOPOLOGY observes live refs, not source.
  - The reviewer judges each PR, and its own prompt carried the literal (#1438).
  - No existing mechanism caught #1143, #1293 or #1438.
- **Shadow guarded against:** *literal re-growth.* A new tool or prompt line names `develop` or `main`. It works here, but in a repository configured otherwise it fails, targets the promotion-gated branch, or passes vacuously, as CHECK 3's empty range does today.
- **Rule id:** PIP-031, shared with D3.

### D2 — `bootstrap.sh` creates the integration branch when it is absent, and protects both branches

**Creation step.** A new step runs before protection:
- It resolves both roles.
- If origin lacks the integration branch but has the release branch, it creates the integration branch at the release tip: `git push origin refs/remotes/origin/<release>:refs/heads/<integration>`, never forced.
- If the integration branch exists, the step skips. This is ADR-0008 D6's check-first idempotency.
- If the release branch is absent too, it warns, naming it, and skips. It never invents a base.

**Protection step.** Step 5 makes one PUT per role, on the origin slug resolved at runtime:
- **Payload:** both PUTs carry the same payload, R1+R2 plus the R4 payload of ADR-0042 D2 / ADR-0076 D3 (`required_status_checks` requiring the `ci` status context), with `enforce_admins` false.
- **Integration:** that payload, to `branches/<integration>/protection`.
- **Release:** that payload, to `branches/<release>/protection`. Because admins are not enforced, an admin running `tools/promote.sh` can still fast-forward the branch, exactly as today. This realises ADR-0075 D5's gate (1) on every host that runs bootstrap. It is exactly this repository's hand-set `main` protection (PR required, required `ci` check, no force-push, no deletion, `enforce_admins` off), so a new host gets this repository's shape.
- If origin lacks the release branch, bootstrap warns, naming it, and skips that PUT. It never invents the branch.

**Relation to earlier decisions:**
- This supersedes, in part, ADR-0004 D3's layer-2 slug literal.
- It extends ADR-0008 D6's scope list with branch creation and with protection of the integration branch. That list's R1+R2 bullet on `main` reads through D1 as the release PUT, which D2 keeps.
- It extends ADR-0042 D2 as ADR-0070 D1 already extended it, and also applies D2's R4 payload to the release branch, which is ADR-0042 D2's original target, `main`, read through D1.
- It realises ADR-0075 D5's gate (1), branch protection on `main` rejecting direct pushes, read through D1 as the release branch. ADR-0070 D1's promotion gate still governs how that branch advances.

**Enforcement:**
- **Mechanism:** a regression test runs `bootstrap.sh` against a local bare origin with a `gh` shim that records each call's arguments and `--input` payload. It asserts three things: the branch is created at the release tip; the only protection PUT paths are the integration branch's and the release branch's, each carrying the R4 `ci` payload with `enforce_admins` false; and a second run exits 0 with the branch unmoved. This test replaces the static literal test `tests/test_bootstrap_branch_protection_1294.py`. CHECK 29 arm (a) guards step 5's literals. The PRD's production check repeats the test with `trunk`/`release`.
- **Parsimony:** no existing mechanism executes bootstrap. The only test reads its source text. CHECK 29 catches a literal, but not a missing creation step, a missing PUT or a wrong payload.
- **Shadow guarded against:** *phantom protection.* Protection aimed at a branch that does not exist, which GitHub refuses and bootstrap prints as a routine skip; protection aimed at only one of the two branches; or a PUT missing its R4 floor, which on the release branch would also strip this repository's `main` of its `ci` check on a re-run. Each leaves either a branch without its `ci` floor (ADR-0076 D3 on the merge target, ADR-0042 D2 on the release branch) or the release branch open to direct pushes (ADR-0075 D5 gate (1)).

### D3 — Agents branch, diff, verify and open PRs against the integration branch

**`implementer.md`:** its branch point, CHECK-3 range and rebase target are `origin/<integration>`.

**`reviewer.md`:**
- Its verify-base is `origin/<integration>` after an explicit fetch. This applies to every diff and log range: R-SCOPE, R-CONV-COMMITS, R-META, R-LOC, R-DOCS-CURRENT and R-PROVE.
- R-NO-MAIN keeps its name and now requires `baseRefName` to equal the integration branch, with a head that is neither role. A PR based elsewhere is BLOCKed.

**`slicer-critic.md` and `codebase-critic.md`:** their verify-base and base-ref are `origin/<integration>`.

**`prd-critic.md` and `adr-critic.md`:**
- Their ADR-existence oracle reads `gh api ".../contents/decisions/<f>.md?ref=<integration>"`. ADRs land on the integration branch in slice 1 of their PRD (ADR-0003 D8).
- Their stale-worktree mitigation (use the remote, not local `decisions/`) is unchanged.

**`ship/SKILL.md`:** its two `origin/develop` command tokens resolve the role.

**`tools/pipe/pr-open`:** appends `--base <integration>` when its caller passes no `--base`/`-B`. An explicit base wins.

**Issues closed:** #1143, #1293, #1438 and #1394.

**Relation to earlier decisions:** this supersedes, in part, ADR-0041 D2's named ref and ADR-0045 D3's parenthetical ref.

**Enforcement:**
- **Mechanism:**
  - CHECK 29 arm (a). The prompts are inside its subject set, so every `origin/main` token must go, and a new one FAILs.
  - A pr-open regression test covers both the default and an explicit base.
  - R-NO-MAIN is the per-PR backstop against a caller that passes a wrong explicit base.
- **Parsimony:**
  - ADR-0041 D2 already demanded one consistent verification base, but it named the ref, and no mechanism reads prompts for it.
  - ADR-0070's migration covered tools, CI and dashboard, not agents.
- **Shadow guarded against:** *stale-base drift.* The generator and the critics disagree with the branch that PRs actually target:
  - slices are cut from a lagging base;
  - scope, LoC and commit-range checks run over unrelated unpromoted commits;
  - a PR opened without a base lands on the promotion-gated branch (PR #1141);
  - ADRs merged to the integration branch are reported "not present".
- **Rule id:** PIP-031, shared with D1.

### D4 — History anchors are instants, never this repository's PR numbers or commit shas

**Table and predicate.**
- `dashboard/_constants.py` gains `GRANDFATHER_UNTIL`, which maps each check id to a normalized UTC `YYYY-MM-DDTHH:MM:SSZ` instant.
- It also gains `grandfathered(check_id, merged_at)`. A subject merged at or before the instant is grandfathered; any later subject is scored.
- The predicate is a string compare, so the module keeps its import-nothing property (ADR-0088 D4).

**What it replaces:**
- `_PROOF_PRESENCE_BOOTSTRAP_PR = 788`
- `_PROOF_INTEGRITY_BOOTSTRAP_PR = 839`
- `_CI_GATE_BOOTSTRAP_PR = 711` in `comparison.py`, plus its unused duplicate in `health.py`
- `_RECORD_VS_GH_ANCHOR_SHA = "0d8e6d0"` together with `_RECORD_VS_GH_WINDOW_EXCEPTIONS = {"1089"}`
- `_ADR_0076_ANCHOR_SHA = "f271843"`

**How the instants are chosen.**
- Each instant keeps this repository's verdicts unchanged. The PRD verifies this with an A/B comparison.
- For example, RECORD-VS-GH's instant is #1089's merge time, 2026-08-02T02:11:00Z. That makes the hand-kept exception unnecessary.
- Every PR of a new project merges after every instant, so each check binds from that project's PR #1. A copy without history never runs `git show` on a foreign sha.
- This is ADR-0004 D2's own semantics: bind forward from a merge, and a merge is an instant.

**Enforcement:**
- **Mechanism:**
  - CHECK 29 arm (b) FAILs on a module-level constant under `dashboard/` or `tools/` when two things hold. Its name contains `BOOTSTRAP`, `ANCHOR`, `GRANDFATHER` or `WINDOW_EXCEPTION`. Its value is an integer ≥ 1, a 7–40-character hex string, or a set or list of numbers.
  - `0` stays legal: `_META_TRIPWIRE_BOOTSTRAP_PROMOTION = 0` is a count that a new project also starts at, not an identity.
  - A predicate regression test covers the table and `grandfathered()`.
- **Parsimony:**
  - VER-009 states the principle but has no mechanism.
  - RULE-COVERAGE counts enforcers, not the data they have.
  - Nothing reads anchor constants.
- **Shadow guarded against:** *inherited amnesty.* A project built from the template inherits this repository's thresholds. Its proof gates grandfather its own first ~800 PRs while RULE-COVERAGE reports rules #15 and #20 covered, or its sha-anchored checks sit on "could not resolve anchor" forever.
- **Advisory residue:** an anchor stored under a name outside the four keywords escapes arm (b). That part is **(advisory)**; codebase-critic's per-PRD drift pass (PIP-012) is the judgment backstop.
- **Rule id:** PIP-032.

## Supersession ledger (clause-exact)

Each entry below is partial: only the named clause falls, and every other clause of that decision stands. The superseded ADR files are not edited and keep `superseded_by: []`. This follows the per-decision precedent that ADR-0088 and the `tools/gen_rules.py` baseline comment record. No rule_id drops. `decisions/README.md` annotates each row with "D<n> partially superseded by ADR-0089".

- **ADR-0070 D1.**
  - **Falls:** the naming clause, i.e. `develop` and `main` as fixed branch names.
  - **Stands:** the two roles, PR routing to the integration branch, release reached only by promotion, rule #4's substance, protection on the integration branch, and bootstrap-mode.
  - This repository's configured names are exactly `develop`/`main`, so every sentence of D1 stays true here.
- **ADR-0004 D3.**
  - **Falls:** the literal slug `vojtech-stas/project-claude` in layer 2's target and PUT path. The new target is the origin slug resolved at runtime.
  - **Stands:** the three-layer stack, layer 1's regex and fail-open policy, the meanings of R1/R2, layer 2's branch, and layer 3.
  - Layer 1's `main` ban and layer 2's `main` target read as the release branch under ADR-0089 D1, and D2 still PUTs R1+R2 there. D2 also extends layer 2 to the configured integration branch, where ADR-0070 D1 had already placed protection without naming this D-ID.
- **ADR-0041 D2.**
  - **Falls:** the ref it names, `origin/main`. The new ref is the integration branch's remote-tracking ref.
  - **Stands:** remote ref after an explicit fetch, never a local ref or HEAD, consistency across critics and AC-checks, and soft-degrade.
- **ADR-0045 D3.**
  - **Falls:** the parenthetical "(origin/main, per the existing stale-worktree mitigation)". The new ref is the integration branch.
  - **Stands:** the citation-ledger pre-step and its feed into AC-SUPERSEDES-BY-D-ID and AC-CROSS-ADR-CONSISTENCY.

## Left standing, explicitly

**Read through D1, not superseded.** These are true verbatim under this repository's config. In a repository configured otherwise, `develop` means integration and `main` means release.
- ADR-0001 D12, hard rule 2 ("Never push directly to `main`").
- ADR-0023 D4 and ADR-0076 D4 (the push guard).
- ADR-0076 D3 (the floor on `develop`).
- ADR-0070 D2–D4.
- ADR-0075 D3 and D5.
- ADR-0077 D2.
- ADR-0085 D2.

Their rendered rules HOK-004, PIP-016 and PIP-017 keep their text.

**Pre-existing divergences, carried unchanged and not decided here.** There are seven, found by sweeping the `### D<n>` sections of every ADR not superseded in full for `main`, `develop` and this repository's slug.

In the first five, the decision text names `main`, while the code has used `develop` since ADR-0070's migration. ADR-0089 D1 already covers each of them operationally: the site keeps the role its code uses today, which is integration. Each is therefore a documentary gap, not a functional one.
- ADR-0023 D2: the SessionStart divergence ref. Its rendered HOK-005 already misstates the code.
- ADR-0041 D1: the restore ref. Its semantics were already superseded by ADR-0058 D3.
- ADR-0041 D3: the root ff-sync ref.
- ADR-0058 D3: clause (b)'s `prune` reference, which reclaims a worktree whose branch is "0-ahead of main". `tools/worktree-guard.sh` (:39, :236–240) tests 0-ahead of `origin/develop`. ADR-0070's `Extends:` entry for ADR-0058 D3 names only its ff-sync target, and the rendered ISO-006 names no branch.
- ADR-0075 D4: the root checkout attaches to `main`, while root-sync keeps it on `develop`.

In the other two, the decision text names this repository's former slug, `vojtech-stas/project-claude`, while the repository now resolves as `vojtech-stas/template-project`. ADR-0089 D1 does not cover them, because it resolves branch roles only. They are still documentary gaps, not functional ones: live tooling derives the slug at runtime (see Context), and GitHub redirects the old name.
- ADR-0001 D1: the clone command future projects start from.
- ADR-0001 D3: where the repository lives. ADR-0004 D5's erratum D5a records that ADR-0001 D3 is still in force, although the index row calls it partially superseded.

This ADR changes only the spelling at the five branch sites, not the role, and it touches no slug. Reconciling the root-checkout entries (ADR-0023 D2, ADR-0041 D1 and D3, ADR-0075 D4) needs a decision about which branch the root checkout tracks. Reconciling the other three needs their text restated by a superseding decision. None of this is this ADR's theme. All seven are filed as one `captured` follow-up.

**Descriptive or historical mentions.** These are left as written:
- ADR-0009 D3, ADR-0034 D5 and ADR-0062 D2: rationale prose about work reaching `main`.
- ADR-0036 D2: quotes the reviewer's `origin/main` diff as the reason reviewer dispatch is isolated. The decision is the isolation, not the base.
- ADR-0012 D4/D5 and ADR-0081 D3/D4: verification provenance of the form "at origin/main <sha>".
- ADR-0071 D4 and ADR-0072 D1/D3: activation history.

## Consequences

- **The template installs.** A new project on the default names gets `develop` created, and both `develop` and `main` protected, by `bootstrap.sh`. Other names take one edited file.
- **Bootstrap re-run in this repository** (D2): a no-op for protection. Both PUTs carry the payload `develop` and `main` already hold, so the `ci` check stays on both.
- **Behavior changes in this repository's pipeline**, all from D3:
  1. The implementer cuts slices from `origin/develop`.
  2. The reviewer, slicer-critic and codebase-critic ranges shrink to a PR's own commits instead of every unpromoted commit.
  3. R-NO-MAIN BLOCKs a PR not based on `develop`.
  4. The ADR-existence oracle sees `develop`.
  5. `pr-open` defaults to `--base develop`.

  Everything else resolves to the same names as today.
- **Anchors:** D4 keeps this repository's verdicts, and a new project's proof gates bind from its PR #1.
- **Rendered rules:** PIP-031 and PIP-032 are added, and `RULE_IDS_BASELINE` rises by 2 (91 → 93 on 2980962).
- **Operator ack:** slices touch guardrail paths (`.claude/hooks/**`, `.githooks/**`, `tools/ci-checks.sh`, `*-critic.md`, `tools/promote.sh`). Promotion therefore waits for `.claude/PROMOTE_OK` (ADR-0070 D4).
- **Accepted costs:**
  - Every future tool and prompt command calls the resolver.
  - A host that already has PR history, which is not this repository's case, will need an adoption instant. That is deferred with the WEDLY port (D381).
- **Merge order with PR #1476** (ADR-0086, which adds PIP-030): it adds 3 subject-set literal lines, and whichever of the two lands second sweeps them.

## Alternatives considered

- **Auto-detect the names** (`origin/HEAD`, heuristics). Rejected. This repository's default branch is `main` while its integration branch is `develop`, so the heuristic is wrong on its home repo. A value asked for once and recorded is honest; a guess is not (ADR-0083).
- **`git config` keys or environment variables.** Rejected. They are local, not cloned, and not present in a GitHub Actions checkout. Every fresh clone and every CI run would silently fall back to the defaults.
- **JSON or YAML config.** Rejected. Bash callers would need `jq`. Bootstrap installs it only best-effort, and the hook hot path is kept to a single spawn (ADR-0079 D3's diet), so it must stay free of it. `key=value` plus one Python parser keeps a single parser. The Python hooks import the parser in-process and add no spawn.
- **Two parsers (bash `sed` plus Python).** Rejected. Copies that drift apart are the class ADR-0088 D4 closed for the critic roster.
- **Keep the literals and make hosts rename their branches.** Rejected. It contradicts Q7.
- **Unify the branch config with the repo slug in one host-identity file (#1447).** Rejected, because the two differ in kind:
  - The slug is derived from the git remote at runtime, so it matches the remote by construction. 0 live literals were measured.
  - A stored copy would be a second source of truth that goes stale on a rename, as this repository's local `origin` did after the 2026-09-16 rename.
  - Branch roles are policy and cannot be derived, so they need configuration.
- **A bootstrap-written adoption anchor** (#1292's `.claude/bootstrap-anchor`). Deferred. Bootstrap cannot tell whether it runs in this repository or a new one without a slug literal, and a new project needs no grandfathering at all. Instants from this repository's history are correct everywhere. Only a host with prior PRs needs an adoption anchor, and that comes with Block 6.
- **Anchor on the first commit's date.** Rejected. It changes this repository's grandfathered sets, because the first commit predates every check, and so it changes live verdicts here.
- **Fix only `pr-open` and leave the prompts on `origin/main`.** Rejected. #1293 and #1438 show that the generator and the critics disagree with the base independently of the wrapper.
- **Extend CHECK 25 with a "Class C".** Rejected; see D1's parsimony note.

## Propagation

Runtime-surface hits (`.claude/agents/`, `.claude/skills/`, `.claude/settings.json`) for each superseded ADR number, at `develop` 2980962. `.claude/settings.json` has zero hits for all four.

- **ADR-0070:**
  - `.claude/agents/reviewer.md` (:92 D4, :310 D2, :352 D4, :354 D4): **grandfather-with-reason.** These cite D2/D4, which stand.
  - `.claude/skills/ship/SKILL.md` (:64 D2, :136 D4, :303 D2/D3, :310 D4): **grandfather-with-reason.** D2–D4 stand. The file is separately edited in this wave at :292/:296, for command tokens under ADR-0089 D1/D3.
- **ADR-0004:** each file below is **grandfather-with-reason**. D1, D2 and D5 are untouched. The single D3 cite (reviewer.md:273) refers to the enforcement stack's structure as R-DOCS-CURRENT extended it, and that stands.
  - `.claude/agents/adr-critic.md` (:10, :231, :256 D1; :114, :117, :120, :122, :126, :150, :174, :242 D2; :96, :124 D5; :242 bare)
  - `.claude/agents/backlog-critic.md` (:110 D1, :122 D2)
  - `.claude/agents/codebase-critic.md` (:12, :299, :394 D1; :381 D2)
  - `.claude/agents/prd-critic.md` (:146, :169 D2; :279 D1)
  - `.claude/agents/reviewer.md` (:170, :337 D2; :273 D3)
  - `.claude/agents/slicer-critic.md` (:198 D2)
  - `.claude/skills/ship/SKILL.md` (:239 D1)
  - `.claude/skills/to-prd/SKILL.md` (:22, :24 D1; :52 D2; :92 bare reference)
- **ADR-0041:**
  - `.claude/agents/reviewer.md` (:105, :160, :282 D2): **update-in-this-wave.** The base becomes the integration branch, and the cite reads "ADR-0041 D2 as amended by ADR-0089 D3".
  - `.claude/agents/slicer-critic.md` (:30 D2): **update-in-this-wave**, the same change.
  - `.claude/skills/ship/SKILL.md` (:291 D1, :296 D3, :428 D1/D3 reference): **grandfather-with-reason.** D1/D3 are not superseded here (see the pre-existing divergence list).
- **ADR-0045:**
  - `.claude/agents/adr-critic.md` (:176) and `.claude/agents/implementer.md` (:37): **grandfather-with-reason.** Both cite rule #18 (D1), which stands.
  - adr-critic.md's oracle lines (:46, :90, :92, :226) are updated under ADR-0089 D3 but do not cite ADR-0045.

**Beyond the runtime surface:**
- **`decisions/README.md`:** the 0089 row, plus partial-supersession annotations on the rows for 0004, 0041, 0045 and 0070.
- **`tools/gen_rules.py`:**
  - **PIP-031** → "Pipeline executables (`tools/`, `dashboard/`, `.claude/hooks/`, `.githooks/`, `bootstrap.sh`) and agent/skill prompt commands name the integration or release branch only through `tools/pipeline_config.py`, which reads the tracked `.claude/pipeline.conf` (defaults `develop`/`main`). Agents branch from, diff against, verify on and open PRs to the integration branch. CI CHECK 29 fails on a literal branch token in that defined subject set (ADR-0089 D1/D3)."
  - **PIP-032** → "A health-check grandfather anchor is an ISO-8601 UTC instant in `dashboard/_constants.py` `GRANDFATHER_UNTIL`, compared against merge time through `grandfathered()`. It is never a PR/issue number or commit sha of this repo. CI CHECK 29 fails on a numeric or sha-valued anchor constant (ADR-0089 D4)."
  - `RULE_IDS_BASELINE` goes 91 → 93, with the breakdown PIP(28) → PIP(30). If PR #1476 lands first, it goes 92 → 94. The rule is always "baseline at `BASE` + 2".
  - The baseline comment block.
  - Regenerate `.claude/generated/_global.md`.
- **Tests:**
  - `tests/test_bootstrap_branch_protection_1294.py` asserts the literal `branches/develop/protection` and the absence of `branches/main/protection`. It is replaced by D2's behavioral test, which asserts both PUTs, each with the `ci` payload and `enforce_admins` false. Its second assertion is reversed on purpose: #1294's defect was `develop` left unprotected, which the integration PUT still covers.
  - Tests that build fixture repositories with `develop`/`main` keep passing on the defaults.
- **Cascade docs:**
  - `tools/README.md` (the worktree-guard section still says `origin/main`).
  - `README.template.md` (the bootstrap section; :22 and :206 say bootstrap protects only `develop`), then regenerate `README.md`.
  - `dashboard/README.md` (the two-tier paragraph).
- **Grandfathered as history:** every `decisions/`, `qa-proof/` and `docs/decision-log/` mention.

## References

- **Operator decisions:** plan Q7 (universal), D460 (restart; #1446 closed), D381 (no WEDLY port yet). Plan Blocks 1, 3 and 6.
- **Superseded in part:** ADR-0070 D1, ADR-0004 D3, ADR-0041 D2, ADR-0045 D3.
- **Extended:** ADR-0042 D2 (its R4 payload now on both branches), ADR-0058 D3, ADR-0062 D3 (each as already extended by ADR-0070); ADR-0008 D6; ADR-0083 D3; ADR-0004 D2.
- **Also cited:** ADR-0001 D1/D3, ADR-0003 D8, ADR-0004 D5, ADR-0023 D2/D4, ADR-0036 D2, ADR-0041 D1/D3, ADR-0046 D1, ADR-0056 D1/D2, ADR-0064 D1, ADR-0070 D2–D4, ADR-0075 D4/D5, ADR-0076 D3/D4, ADR-0079 D3, ADR-0084 D1, ADR-0088 D4.
- **Issues:**
  - Closed by this work: #1143, #1293, #1438, #1394, #1292.
  - Rejected with reason: #1447.
  - #1446 (the prior draft's round-3 stop) and #1479 (the 0086 collision).
  - PR #1476 (PIP-030; merge-order note).
- **Verification note for reviewers:** ADR-0083, ADR-0084, ADR-0085 and ADR-0088 exist on `origin/develop` (2980962). `origin/main` (f7378de) lags, which is the #1394 defect that ADR-0089 D3 closes, so verify cited headings on `origin/develop`.
