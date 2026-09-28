---
id: "ADR-0092"
title: "The pipeline is an installable, upgradeable package: one git subtree at .claude/pipeline/, versioned by tags, found through generated shims"
status: "accepted"
date: "2026-09-23"
scope: "pipeline"
rule_ids:
  - "PIP-035"
  - "PIP-036"
  - "PIP-037"
supersedes:
  - "ADR-0001 D1"
  - "ADR-0001 D12"
  - "ADR-0002 D9-revised"
  - "ADR-0003 D1"
  - "ADR-0042 D3"
superseded_by: []
---

# 0092 — The pipeline is an installable, upgradeable package

- **Status:** Accepted
- **Date:** 2026-09-23
- **Supersedes:**
  - ADR-0001 D1, in full (the clone-as-template reuse model).
  - ADR-0001 D12, in part: hard rule 4 for exactly one machine-generated commit shape, and hard rules 2–3 inside throwaway verification repositories only.
  - ADR-0002 D9-revised, in part (squash-only merging, for upgrade PRs only).
  - ADR-0003 D1, in part ("closes one slice", for upgrade PRs only).
  - ADR-0042 D3, in part (the `--squash` flag of the merge command, for upgrade PRs only).
  - Each clause is scoped exactly in the Supersession ledger below.
- **Extends:**
  - ADR-0089 D1: `.claude/pipeline.conf` gains one key, `package_source`, and the resolver gains three queries, `mode`, `repo-root` and `package-root` (D1, D5). CHECK 29 arm (a)'s subject set is restated at the package paths (D1). The file stays outside the package, as ADR-0089 D1 already placed it.
  - ADR-0090 D1: the version tag the owner creates after `RELEASE-GATE` PASS is `v<VERSION>` on the package's split commit, created by `package.py publish` (D3), and a version milestone maps to exactly one tag name (D3).
  - ADR-0086 D1: its routers keep referencing the source procedure, which is now the package source `.claude/pipeline/skills/<n>/SKILL.md`, never the D2 shim, which is a generated copy. Its rule resolver reads the package's sources plus the root `CLAUDE.md`, and a missing source stays a refusal. The adapter's code and tests move with `tools/` and `tests/` (D1).
  - ADR-0076 D3: `pr-merge`'s reviewer-verdict assertion, which D3 scopes to "a slice PR", also binds `pipeline-upgrade` PRs, and `pr-merge` gains three upgrade-only legs (D4). ADR-0090 D4 extends the same decision for lane PRs.
  - ADR-0064 D3: the PACKAGE-INTEGRITY registry row delegates to its single implementation in `tools/package.py`, because `package.py` must run the same arms in a host where `dashboard/` may be absent (D1).
  - ADR-0015 D1: hooks still live in `.claude/settings.json`; the package-owned entries are generated there from a package manifest (D2).
  - ADR-0073 D1–D3: rules are still rendered from ADR frontmatter by `gen_rules.py`, now into the package, and area rules reach `.claude/rules/` through a shim (D1). Rendering runs at home only; in a host every generator refuses (D5).
  - ADR-0070 D4: the guardrail-machinery set, read through D1's rename map, gains the critic shims and the generated workflow (D2).
  - ADR-0083 D3: its corollary that a check's subject set is defined, not assumed, applied to where each check runs and to subjects a host and the package share (D5), and to CHECK 29 in hosts (D1).
  - ADR-0042 D1, ADR-0067 D1, ADR-0084 D1/D2, ADR-0034 D4–D6 and ADR-0088 D3: each check keeps its substance; D5 assigns it a scope.
  - ADR-0004 D2: bootstrap-mode.
- **Ships with:** PRD "Make the pipeline an installable, upgradeable package", slice 1 (ADR-0003 D8).
- **Drafting note.** ADR-0089 (`b37088c`, #1520), ADR-0090 (`eb6907d`, #1528) and ADR-0086 (`a455795`, #1476) are all in `decisions/` on `develop`. Every D-ID this ADR cites from them (ADR-0086 D1–D6, ADR-0089 D1–D4, ADR-0090 D1–D5), and every other cited heading, was verified against `origin/develop` at `a455795`. The PRD's §4 posting-order precondition is met.

**Bootstrap-mode (ADR-0004 D2):**
- Each decision binds from the merge of the slice that implements it. Earlier slices run under the rules in force at their branch point.
- CI CHECK 30 (PACKAGE-INTEGRITY) lands arm by arm. It is 30 given the PRD's §4 sequencing, because PRD #1500's S1–S4 ship ADR-0089 D1's CHECK 29 first; otherwise it takes the next free number at merge. Each arm gates its own PR from the slice that adds it:
  - currency, pristine, layout, self-location and prompt paths: slice 1. The prompt-path arm binds each pre-move root from the slice that empties it (D1), so it is vacuous for a root until that root moves;
  - strays: the slice that empties the last pre-move root path.
- The R-LOC redefinition (D2) lands in slice 1. That slice is measured under the definition in force at its branch point.
- Nothing is retroactive. `decisions/`, `qa-proof/` and `docs/decision-log/` keep their pre-move paths as accurate records of the time they were written.

## Context

The owner defined v1.0 as installable **and** upgradeable (grill 2026-09-23, Q3). A new project starts on v1.0 and later receives v1.1 with one command instead of a manual port. The owner chose git subtree as the vehicle (Q6). The package must be universal: no hardcoded branch, slug or path of a host (Q7). The plan fixes the shape (plan "Cílový tvar balíčku", Block 6):
- the product moves into one prefix directory, and a host pulls it as `.claude/pipeline/` with `git subtree add/pull --squash`;
- the platform-forced locations get shims: one `@.claude/pipeline/CLAUDE.md` line in the host's CLAUDE.md, a small `.github/workflows/ci.yml`, and `core.hooksPath`;
- the package carries a `VERSION` file, versions are tags, and it ships `install.sh` and `UPGRADING.md`;
- `decisions/`, `docs/decision-log/`, `qa-proof/` and the PRDs stay home.

The theme of this ADR is one sentence. The pipeline must travel as one versioned, git-native unit that a host installs once and upgrades with one command, without the host ever editing it and without the host's own files being rewritten.

### What exists today (measured on `develop` at a455795)

- **The reuse model is clone-and-diverge.** ADR-0001 D1: "Each project owns its own copy and can diverge. Updates … don't auto-propagate." There are 0 tags (`git tag | wc -l`), no `VERSION` file, and `README.template.md:3` promises "a clone-as-template starter".
- **The product is spread over nine root locations.** 209 tracked files:
  - `.claude/agents/` (9), `.claude/skills/` (6), `.claude/hooks/` (8), `.claude/rules/` (4) and `.claude/generated/` (2);
  - `.githooks/` (3), `tools/` (27), `dashboard/` (13) and `tests/` (135: 131 test files, `conftest.py`, `__init__.py`, `quarantine.txt` and `README.md`);
  - `bootstrap.sh` and `docs/observability.md`.

  Home-only material is 115 tracked files: 88 under `decisions/` (87 ADRs plus the index), 15 under `qa-proof/` and 12 under `docs/decision-log/`. Three root files belong to neither set: ADR-0086's OpenAI entrypoints `AGENTS.md`, `.agents/skills/ship/SKILL.md` and `docs/openai-workflow.md`.
- **Paths are compiled in.** Lines naming a product path (`git grep -h -E '(tools/|dashboard/|tests/|\.githooks|\.claude/(hooks|agents|skills|rules|generated)|bootstrap\.sh|docs/observability\.md)' origin/develop -- <location> | wc -l`), which the move invalidates:
  - agents 105, skills 45, hooks 22, `.githooks/` 30;
  - `tools/` 398, `dashboard/` 198, `tests/` 790;
  - `bootstrap.sh` 26, `README.template.md` 23, `CLAUDE.md` 31, `.claude/settings.json` 7, `ci.yml` 3, `docs/observability.md` 6.
- **The CI gate assumes the home repository.** CHECKs 2, 4, 5, 6, 7, 13, 17, 20, 25 and 26 read `decisions/`, `qa-proof/`, the README template, the rendered-rule baseline or a home allowlist. In a host, CHECK 17 would re-render `_global.md` from the host's (empty or foreign) `decisions/` and FAIL.
- **CI degrades silently when a product path is missing.** 17 soft-degrade guards in 16 CHECKs print `SKIP` and still let `ci-checks.sh` exit 0 when the file they test is absent: CHECKs 4, 5, 9, 14, 15, 16, 18, 22, 24a and 28 on `dashboard/health.py`; 17 and 20 on `tools/gen_rules.py`; 20b on `tools/gen_repo_map.py`; 19 on `tools/check-slicer-provenance.py`; 21 on `tools/deploy-handshake.sh`; 23 on `tools/check-verdict-presence.py`; 12 on `tests/` (`tools/ci-checks.sh:121–1716`). A move that forgets to retarget any one of them goes green with that check never run. `ci-checks.sh` tops out at CHECK 28.
- **Code finds the repository root from its own location.** Outside `tests/`, fourteen sites resolve a path from their own file (anchored by symbol; lines at a455795):
  - **Nine Python root bindings**, which the move re-anchors inside the package: `tools/gen_rules.py` `_resolve_repo_root()` (:75), `tools/gen_repo_map.py` `_resolve_repo_root()` (:43), `dashboard/health.py` `_HEALTH_REPO_ROOT` (:456), `dashboard/discovery.py` `_DISCOVERY_REPO_ROOT` (:23), `dashboard/readme_gen.py` `_READMEGEN_REPO_ROOT` (:19) and `_resolve_invoking_repo_root()` (:65), `dashboard/telemetry_root.py` `_TELEMETRY_CODE_ROOT` (:33), `dashboard/collector.py` `_REPO_ROOT` (:33, from `_THIS_DIR`) and `dashboard/tracestore.py` `_REPO_ROOT` (:128, from `_THIS_DIR`).
  - **Three hook climbs to `../../tools`**, which name `.claude/tools/` once the hooks sit one level deeper: `.claude/hooks/session-start.sh` `_SS_TOOLS_DIR` (:54) and `HANDSHAKE_SH` (:97), and `.claude/hooks/pre-tool-bash-classify.py` `_PIPELINE_CONFIG_PY` (:39, `os.path.join(_THIS_DIR, "..", "..", "tools", …)`). The first two landed with PRD #1500's slice 1 and the Python one is not caught by a `ROOT`-name test.
  - **Two `sys.path` insertions**, `dashboard/health.py` (:59–60) and `tools/openai_workflow.py` (:17–18, from #1476), which put `Path(__file__).resolve().parent.parent` on `sys.path` so that `from tools import …` resolves. After the move that directory is the package root, which holds `tools/`, so they stay correct (D1 blesses them).

  In `tests/`, every test file does the same, 131 at a455795: 122 through `Path(__file__)…parent.parent` and 9 through `os.path` (`test_promote_ack_880.py`'s `REPO_ROOT`; five that take `os.path.dirname(_TESTS_DIR)`; and `test_health_grouping_931.py`, `test_purpose_groups_968.py` and `test_no_unguarded_pytest_import_985.py`, which reach a sibling `dashboard/` rather than a repository root). After a move, each root binding resolves inside the package: health rows would read the package's absent `decisions/`, and `session-start.sh`'s `[ -f ]` guard would skip the deploy handshake. The other tools find only a sibling script from their own directory (`_THIS_DIR` in `tools/pipe/*`, `tools/release.py`, `tools/check-verdict-presence.py`; `_CI_TOOLS_DIR` and its peers in the shell tools; `.githooks/pre-commit`'s `$_pc_dir/../tools`) and take the repository root from `git rev-parse --show-toplevel`, the pattern D1's "Roots" section blesses.
- **The OpenAI adapter (ADR-0086, merged with #1476 at a455795) compiles in home paths.** `tools/openai_workflow.py`'s rule resolver requires `CLAUDE.md`, `.claude/generated/_global.md`, `.claude/generated/_repo-map.md`, `docs/openai-workflow.md` and each matching `.claude/rules/<scope>.md` (`GLOBAL_SOURCES`, `instructions()`), reads the route table from `ROUTE_SOURCE = ".claude/agents/qa-tester.md"`, names `tools/openai_workflow.py` in `SCOPE_ADDITIONS`, and delegates to `tools/pipe/qa-verify` and `tools/pipe/prd-close` under the repository root (`delegate()`). ADR-0086 D1 makes a missing source a refusal. `tools/gen_openai_skills.py` builds the router `.agents/skills/ship/SKILL.md` from `.claude/skills/ship/SKILL.md` and links it there. `AGENTS.md` tells Codex to read `.claude/generated/_global.md` and the repo map and to run `python tools/openai_workflow.py`. Lines naming a product path: `openai_workflow.py` 8, `gen_openai_skills.py` 3, `AGENTS.md` 3, the router 3, `docs/openai-workflow.md` 20, `tests/test_openai_skills.py` 5, `tests/test_openai_workflow.py` 23. `.codex/hooks.json`, which ADR-0086 D4 foresees, does not exist yet.
- **What `git subtree --squash` writes.** Measured with git 2.54.0.windows.1 in throwaway repositories: `subtree add --squash`, then `subtree pull --squash` twice.

  ```
  3bbea2d parents=[1c4132b 0c1b004] | chore(pipeline): upgrade to v1.0.2
  0c1b004 parents=[6e8bcad]         | Squashed '.claude/pipeline/' changes from cfc84b5..7e39749
  1c4132b parents=[4ebb6a4 6e8bcad] | chore(pipeline): upgrade to v1.0.1
  6e8bcad parents=[1ba185c]         | Squashed '.claude/pipeline/' changes from 419e3ea..cfc84b5
  4ebb6a4 parents=[5ff535d 1ba185c] | chore(pipeline): install v1.0.0
  1ba185c parents=[]                | Squashed '.claude/pipeline/' content from commit 419e3ea
  5ff535d parents=[]                | chore(host): init
  ```

  - Every squash commit ends with exactly two trailer lines, `git-subtree-dir: .claude/pipeline` and `git-subtree-split: <40-hex>`, and its tree equals its split commit's tree (`tree==split-tree: yes` for all three).
  - Only the install squash is parentless. Each pull squash has one parent, the previous squash. This is `new_squash_commit` in `git-core/git-subtree` (`git commit-tree "$tree" -p "$old"` when an earlier squash exists).
  - `-m` becomes the whole message of the merge commit, which carries no trailer. The squash subject is git-subtree's own, and git-subtree writes it with `git commit-tree`, so no commit-msg hook runs on it.
  - After a push and a fresh `git clone --no-local`, the split commits are absent (`split 7e39749: ABSENT`): a squash commit never links its split commit.
  - A simulated GitHub squash-merge of an upgrade PR corrupts the record in either of two ways. If the squashed message keeps the PR's commit messages (GitHub's default for a multi-commit PR), the newest trailer commit is the squash-merge itself: one parent that carries no trailer, and a whole-repository tree. The next pull parents its new squash on that whole-repository commit. If the message is title-only, the newest trailer commit is the previous squash, `HEAD:.claude/pipeline` no longer equals its tree, and the next pull conflicts: `CONFLICT (content): Merge conflict in .claude/pipeline/VERSION`. In both cases a tree-equality test on the newest trailer commit fails at once.
- **CHECK 3 has no exemption.** It runs `git log --no-merges origin/develop..HEAD` and fails any subject outside Conventional Commits. The squash subject, `Squashed '.claude/pipeline/' changes from …`, fails it.
- **Every PR merges with `--squash --auto`** (ADR-0042 D3; the default `_run_gh(["pr", "merge", pr, "--squash", "--auto"])` call in `tools/pipe/pr-merge`). That is exactly the corruption above. ADR-0090 D4's lane-PR leg runs before that call for PRs labeled `lane`, and does not change the default mode.
- **Every merged PR needs a verdict.** CHECK 23 requires a reviewer `VERDICT: APPROVE` comment on every PR among the last 20 merged, with no exemption list. An upgrade PR merged by hand would fail CI on the next 20 PRs.
- **The owner's machine is Windows.** Measured there: `git config core.symlinks` = `false`; `core.autocrlf` = `true`, set at system scope in `C:/Program Files/Git/etc/gitconfig`; no `.gitattributes`; `git version 2.54.0.windows.1`; `git subtree` is available. The home repository is public on GitHub.

### Platform constraints (verified 2026-09-23 against the Claude Code docs)

| Constraint | Source |
|---|---|
| Project skills are discovered at `.claude/skills/<skill-name>/SKILL.md`. A nested `.claude/skills/` under a subdirectory loads only once Claude reads a file there. No grouping level (`.claude/skills/<dir>/<name>/`) is documented. | https://code.claude.com/docs/en/skills |
| `.claude/agents/` is scanned recursively. Subfolders do not affect identity, which comes from the `name` frontmatter. | https://code.claude.com/docs/en/sub-agents |
| `.claude/rules/**/*.md` is discovered recursively, and `paths:` scopes a rule. `@` imports resolve relative to the importing file and chain up to four hops. An import inside the working directory needs no approval dialog. On Windows, use an import rather than a symlink: git checks a symlink out as a text file unless `core.symlinks` is enabled. | https://code.claude.com/docs/en/memory |
| Hook commands are free command strings with a `${CLAUDE_PROJECT_DIR}` placeholder, so they can point into any subdirectory. Plugin hooks merge with settings hooks. | https://code.claude.com/docs/en/hooks |
| Plugins bundle skills, agents, hooks and `bin/`. A `CLAUDE.md` at the plugin root "is not loaded as project context", and there is no plugin rules directory. A plugin cannot reference files outside its own root. Marketplace plugins are copied into a per-user cache. Plugin agents ignore `hooks`, `mcpServers` and `permissionMode`. | https://code.claude.com/docs/en/plugins-reference |
| Plugin skills are always namespaced: `/plugin-name:skill`, and agents `plugin-name:agent`. | https://code.claude.com/docs/en/plugins |
| A marketplace can be any git repository, pinned by ref or sha, including a `git-subdir` source. `version` pins updates. A project can declare a marketplace through `extraKnownMarketplaces` plus `enabledPlugins`. | https://code.claude.com/docs/en/plugin-marketplaces |
| Third-party marketplaces have auto-update off by default. Since v2.1.195, a plugin from an external source that only project settings enable is not installed until each user installs it. | https://code.claude.com/docs/en/discover-plugins |
| A folder under `.claude/skills/` that holds `.claude-plugin/plugin.json` loads in place as `<name>@skills-dir`. At project scope it needs workspace trust: "running with `-p` isn't enough". It loads only from the primary working directory. | https://code.claude.com/docs/en/plugins-reference |
| In `claude -p` or the SDK in a never-trusted folder, hooks in settings files are **used**. Project `@skills-dir` plugins, `extraKnownMarketplaces`, subagent frontmatter hooks and `permissions.allow` rules are **not used**. Trust is keyed on the repository root, and worktrees use the main checkout's root. | https://code.claude.com/docs/en/permissions ("What runs before you trust a folder") |
| Without `--bare`, `claude -p` "loads the same context an interactive session would", and `--bare` is what skips "hooks, skills, custom commands, subagents, plugins, MCP servers, auto memory, and CLAUDE.md". User-invoked skills work in `-p` when the prompt names `/skill-name`. | https://code.claude.com/docs/en/headless |

What this means for a single subtree at `.claude/pipeline/`:
- It **cannot** be discovered in place: skills, agents and area rules must sit at the fixed locations above. Only the `@` import (CLAUDE.md) and hook commands (settings) can point into it.
- Delivering it therefore takes either generated copies (shims) or a switch of vehicle to a plugin. D2 chooses shims. The plugin, hybrid and skills-dir-plugin forks are argued under Alternatives considered, and the PRD raises the fork as an owner decision.
- No package agent carries frontmatter hooks, and `.claude/settings.json` carries no `permissions.allow` rule (measured), so nothing the package ships is withheld in a never-trusted `claude -p` run. Such a run gets its permission mode from the command line.

## Decisions

### D1 — One package, one prefix: `.claude/pipeline/` in the home repository and in every host

**The package.** Everything a host needs to run the pipeline lives under `.claude/pipeline/`:
- `CLAUDE.md`: the pipeline's rules, map and glossary. It imports `@generated/_global.md` and `@generated/_repo-map.md`, which resolve relative to it.
- `VERSION`: `X.Y.Z`.
- `skills/`, `agents/`, `rules/` (area rules) and `generated/`.
- `hooks/`, including `hooks/settings-hooks.json`, the manifest of package-owned hook entries.
- `githooks/`, `tools/`, `dashboard/` and `tests/`.
- `bootstrap.sh`, `install.sh`, `UPGRADING.md` and `docs/observability.md`.
- A package-local `.gitignore` for `__pycache__/`.

**What stays home or host-side:**
- `decisions/`, `docs/decision-log/`, `qa-proof/`, `README.template.md`, `README.md` and `LICENSE`.
- The per-repo config `.claude/pipeline.conf` (ADR-0089 D1).
- Runtime state: `.claude/logs/`, `.claude/state/`, `.claude/cache/`, `.claude/worktrees/`, `.claude/PROMOTE_OK` and `.claude/settings.local.json`.
- The host-owned root `CLAUDE.md`.
- ADR-0086's OpenAI entrypoints `AGENTS.md`, `.agents/skills/` and `docs/openai-workflow.md`, at the root. Codex discovers the first two only there. In v1.0 they are home-side files that name package paths; delivering them to hosts is v1.1 (#1436).

The package is code and prompts. Configuration and state stay with the repository they describe.

**Same prefix at home.** The home repository keeps the product at the same `.claude/pipeline/` it will occupy in hosts. The home repository is host #0: it reaches its own package through the same shims (D2). One path spelling therefore holds in every repository, so every prompt command (`python3 .claude/pipeline/tools/pipe/pr-merge …`) runs unchanged at home and in hosts. `git subtree split --prefix=.claude/pipeline` yields a commit whose root **is** the package.

**Rename map.**

| Before (home, repo-relative) | After |
|---|---|
| `.claude/skills/<n>/` | `.claude/pipeline/skills/<n>/`; shim at the old path (D2) |
| `.claude/agents/<n>.md` | `.claude/pipeline/agents/<n>.md`; shim `.claude/agents/pipeline/<n>.md` |
| `.claude/rules/<n>.md` | `.claude/pipeline/rules/<n>.md`; shim `.claude/rules/pipeline/<n>.md` |
| `.claude/generated/` | `.claude/pipeline/generated/` (no shim; imported) |
| `.claude/hooks/` | `.claude/pipeline/hooks/`; entries in `.claude/settings.json` (D2) |
| `.githooks/` | `.claude/pipeline/githooks/`; `core.hooksPath` |
| `tools/`, `dashboard/`, `tests/` | `.claude/pipeline/tools/`, `.claude/pipeline/dashboard/`, `.claude/pipeline/tests/` |
| `bootstrap.sh`, `docs/observability.md` | `.claude/pipeline/bootstrap.sh`, `.claude/pipeline/docs/observability.md` |
| root `CLAUDE.md` §1–§4 | `.claude/pipeline/CLAUDE.md`; the root keeps home-only content plus the import line |

Directory names inside the package do not change: `dashboard/` stays `dashboard/`. This decision changes location, not names.

**Read-through.** Every product path named in an ADR accepted before this one, in its rendered rules, and in `docs/decision-log/` reads through this map, and its substance is unchanged. For example, ADR-0023 D7's `.claude/hooks/` reads as `.claude/pipeline/hooks/`, and the `hooks` area rule's `paths:` scope `.claude/hooks/**` (rendered by `gen_rules.py`'s `SCOPE_PATHS`, not by any rule text) as `.claude/pipeline/hooks/**`. Rendered rule texts that name a moved path are re-rendered at the mapped path (Propagation), because hosts load them and have no ADRs to read through. See Left standing.

**Roots.** There are exactly two roots:
- the **repository root**, `git rev-parse --show-toplevel` of the caller's working directory;
- the **package root**, `<repository root>/.claude/pipeline`.

Both come only from `tools/pipeline_config.py`, which gains the queries `repo-root` and `package-root`, importable and on its CLI, beside ADR-0089 D1's branch queries. A file may still find its own package siblings from its own location, because the package moves as one unit: a hook reaches `../tools/`, and a Python module may put its own package's parent directory on `sys.path` so that `from tools import …` resolves (after the move, that directory is the package root, which holds `tools/`). PRD #1500 already has callers find the resolver next to themselves. A file never derives either root from its own location to locate files. Of the sites measured in Context, the nine Python root bindings are migrated onto the resolver, the three hook climbs become package-sibling paths (`../tools/`), and the two `sys.path` insertions stay. Every test file except `conftest.py` takes both roots from `tests/conftest.py`, which takes them from the resolver.

**CHECK 29 in the package.** Read through the map, ADR-0089 D1's arm-(a) subject set is defined as follows (ADR-0083 D3's corollary):
- **Included:** tracked files under `.claude/pipeline/tools/`, `.claude/pipeline/dashboard/`, `.claude/pipeline/hooks/` and `.claude/pipeline/githooks/`, plus `.claude/pipeline/bootstrap.sh`, `.claude/pipeline/install.sh`, `.claude/pipeline/agents/*.md` and `.claude/pipeline/skills/*/SKILL.md`. `install.sh` is new product code that runs git, so it joins.
- **Excluded:** `*.md` under the package's `tools/` and `dashboard/`, `.claude/pipeline/tools/gen_rules.py`, `.claude/pipeline/tools/pipeline_config.py`, and comment-only lines, as in ADR-0089 D1.
- **Never subjects:** the shims (`.claude/skills/<n>/`, `.claude/agents/pipeline/`, `.claude/rules/pipeline/`). Each shim is its source plus D2's two transforms, and the currency arm holds it equal to its source, so its verdict is its source's. Host-owned files under `.claude/skills/` or `.claude/agents/` are never subjects either, because they owe nothing to PIP-031.
- **Scope:** `both` (D5). Its subjects are package files, byte-identical in every host.

**The OpenAI adapter (extends ADR-0086 D1).** ADR-0086's code (`tools/openai_workflow.py`, `tools/gen_openai_skills.py`, `tools/workflow_branch.py`) and tests (`tests/test_openai_*.py`) are product, and they move with their directories. Each compiled-in path is rewritten in the slice that moves what it names, spelled repo-relative with the `.claude/pipeline/` prefix, the one spelling this decision fixes:
- **The router** references the source procedure, as ADR-0086 D1 requires, and that is now `.claude/pipeline/skills/<n>/SKILL.md`. The shim at `.claude/skills/<n>/` is a generated copy (D2), so a router pointing there would reference a copied body. `gen_openai_skills.py` reads the router's name and description from the package source too. The router stays ADR-0086's generated file, currency-gated by ADR-0086's own parity test; it is not a D2 shim.
- **The rule resolver's sources** are the root `CLAUDE.md`, `.claude/pipeline/CLAUDE.md`, `.claude/pipeline/generated/_global.md`, `.claude/pipeline/generated/_repo-map.md`, `docs/openai-workflow.md`, and each matching area rule at `.claude/pipeline/rules/<scope>.md`. Codex follows no `@` import, so the package `CLAUDE.md` is named explicitly, in the resolver and in `AGENTS.md`. A missing source stays a refusal (ADR-0086 D1). The route table is read from `.claude/pipeline/agents/qa-tester.md`.
- **Delegation and scope:** the adapter runs `.claude/pipeline/tools/pipe/qa-verify` and `.claude/pipeline/tools/pipe/prd-close`, and `SCOPE_ADDITIONS` names `.claude/pipeline/tools/openai_workflow.py`.

**Supersedes ADR-0001 D1 in full.** Future projects no longer clone the template and diverge. They install the package and upgrade it (D3).

**Enforcement:**
- **Mechanism:** PACKAGE-INTEGRITY. Its arms are implemented once, in `tools/package.py`. `dashboard/health.py` registers it as one row that delegates to that implementation (extending ADR-0064 D3), and CI CHECK 30 (or the next free number at merge) consumes the row. `package.py check` runs the same arms directly, so a host can run it before and without `dashboard/`. D1 owns four arms:
  - **Layout arm** (both modes). The package's required members exist: `VERSION` matching `^[0-9]+\.[0-9]+\.[0-9]+$`, `CLAUDE.md` and `tools/package.py`. So does every path that a soft-degrade guard in `ci-checks.sh` tests, meaning each file or directory test (`[ -f P ]`, `[ ! -f P ]`, `[ -d P ]`, `[ ! -d P ]`) whose missing-path outcome prints `SKIP:`. The arm reads those paths from `ci-checks.sh` itself, so a new guard is covered without a list to maintain. A guard left on a pre-move path therefore FAILs the arm, naming the CHECK and the path, instead of turning its CHECK into a green `SKIP`.
  - **Stray arm** (home mode). It FAILs, naming the path, in two directions:
    - a tracked file sits at a pre-move product root (`tools/`, `dashboard/`, `tests/`, `.githooks/`, `.claude/hooks/`, `.claude/generated/`, `bootstrap.sh`, `docs/observability.md`), or a tracked file under `.claude/skills/`, `.claude/agents/` or `.claude/rules/` lacks D2's generated marker;
    - home-only material sits inside the package: a tracked path under `.claude/pipeline/decisions/`, `.claude/pipeline/qa-proof/` or `.claude/pipeline/docs/decision-log/`, or a `README.md` or `README.template.md` at the package root.
  - **Prompt-path arm** (both modes). Its subject set is defined (ADR-0083 D3): every file an agent host loads as instructions, or relays to an agent as a directive. Inside the package, in both modes, that is `agents/*.md`, `skills/*/SKILL.md`, `CLAUDE.md`, `generated/*.md`, `rules/*.md`, `hooks/*` (including `hooks/settings-hooks.json`) and `githooks/*`. At the repository root, in home mode only, it is ADR-0086's OpenAI entrypoints `AGENTS.md`, `.agents/skills/*/SKILL.md` and `docs/openai-workflow.md`, which Codex loads as instructions and which exist only at home in v1.0. A pre-move root counts as **moved** once it holds no tracked file (for a single file such as `bootstrap.sh`, once that file is untracked at the root). The arm FAILs on any command-shaped token that names a moved root without the `.claude/pipeline/` prefix; a token naming a root that has not moved yet is still a working path and passes. In a host whose own files use such a root (a host `tools/`, say), the arm is narrower there, never wrong: the same package bytes are checked at home. Command paths are therefore swept in the slice that moves what they name, never earlier. The token shapes are `python3? tools/…`, `bash tools/…`, `tools/<f>.(py|sh|txt)`, `tools/pipe/<verb>`, `dashboard/<f>.py`, `dashboard/fixtures/`, `pytest tests/`, `tests/<f>.(py|txt)` when not followed by `::`, `.githooks/`, `.claude/hooks/`, `.claude/generated/`, `bootstrap.sh` and `docs/observability.md`, each when not preceded by `.claude/pipeline/`. A pytest node id (`tests/<f>.py::<name>`) is package-relative by D5's pinned rootdir and is not a command path.
  - **Self-location arm** (both modes). Over the package's code files (`*.py`, `*.sh`, and the extensionless executables under `tools/pipe/` and `githooks/`), except `tools/pipeline_config.py`, it FAILs naming file:line when:
    - a binding whose name contains `ROOT`, or a function whose name contains `root`, takes its value from `__file__`, `BASH_SOURCE` or `$0`, or from a name bound from one of them in the same file;
    - a path built only from literals onto a file's own directory names no tracked path. The own directory is a shell file's `$SCRIPT_DIR`, `$(dirname "$0")` or `$(dirname "${BASH_SOURCE[0]}")`, or a Python name bound from `__file__` (`os.path.join(_THIS_DIR, "..", "..", "tools", …)`, `_THIS_DIR.parent / "tools" / …`);
    - a file in `tests/` other than `conftest.py` references `__file__`.

    A `sys.path` insertion binds no name, so it is not a subject of the first clause; Roots above blesses it.
- **Parsimony:**
  - CHECK 29 (ADR-0089 D1) scans for branch literals, not path spellings.
  - CHECK 25 (ADR-0084 D1) scans for machine-local absolute paths.
  - CHECK 7 compares generated docs to reality and does not look at where product files sit.
  - CHECK 4 finds dangling `decisions/` links, but only at home, and not command paths.
  - No check today knows where the product lives, which paths CI's own guards depend on, or where a module thinks the repository root is. A stray `tools/x.py`, a guard left on `tests`, or a `parent.parent` root would be invisible to every one of them.
- **Shadow guarded against:** *path re-growth and silent re-anchoring.* A new tool, prompt line or rendered rule lands at, or points into, a pre-move root; a CI guard keeps testing a moved path; or a module finds "the repository" from its own file. Each works in the home checkout until the old path is gone. Then it fails as `command not found` in every host, turns its CHECK into a green `SKIP`, or reads the package's own empty subject set and PASSes on it.
- **Advisory residue:**
  - A file that is home-only in substance, such as prose about this repository's history, can still sit inside the package. The arms check location, not meaning. **(advisory)**; codebase-critic's per-PRD drift pass (PIP-012) is the judgment backstop.
  - A command named inside a tool's own output, such as the remedy a FAIL line prints, is outside the prompt-path arm's subject set. **(advisory)**; it fails loudly when an agent runs it.
  - A climb built from non-literal parts, such as a computed directory name, escapes the arm's second clause. None exists at a455795: the one Python climb, `pre-tool-bash-classify.py`'s `_PIPELINE_CONFIG_PY`, is all literals, so the clause covers it. **(advisory; evidence trigger: a moved module that resolves a sibling directory wrongly)**.
  - A blessed `sys.path` insertion puts the package root first, yet a host's own regular `tools` package (one with `__init__.py`) elsewhere on `sys.path` still wins over the package's namespace `tools`, for example the host root when `python -m` runs from it. **(advisory; evidence trigger: a host whose own `tools/__init__.py` shadows a package import)**.
- **Rule id:** PIP-035, shared with D2.

### D2 — Claude Code, GitHub and git find the package through generated shims, never through files someone edits

**The shim set.** A shim is generated from the package by `python3 .claude/pipeline/tools/package.py refresh`. It is never written by hand.

| Shim | Generated from | Why it must exist |
|---|---|---|
| `.claude/skills/<n>/` | `skills/<n>/` | skills are discovered only at `.claude/skills/<name>/SKILL.md` |
| `.claude/agents/pipeline/<n>.md` | `agents/<n>.md` | agents are discovered only under `.claude/agents/` (recursive) |
| `.claude/rules/pipeline/<n>.md` | `rules/<n>.md` | area rules load only from `.claude/rules/` (recursive); `generated/_global.md` is **not** shimmed, because it is imported (the slice #945 double-load lesson) |
| package-owned entries in `.claude/settings.json` | `hooks/settings-hooks.json` | hooks are configured only in settings files (ADR-0015 D1) |
| `.github/workflows/ci.yml` | a template in the package | GitHub reads workflows only from the root |
| a marked block in `.gitignore` | the package | runtime directories must stay untracked in the host |

Two more pieces of wiring are not shims:
- **The import line.** One line, `@.claude/pipeline/CLAUDE.md`, sits in the root `CLAUDE.md` exactly once.
  - In a host, `install` appends it if it is absent, creating the file if it is missing.
  - At home, `install` refuses (D3). The slice that creates `.claude/pipeline/CLAUDE.md` adds the line once, as an ordinary tracked and reviewed edit of the home root `CLAUDE.md`.
  - No later command ever writes the root `CLAUDE.md` in either mode, and the currency arm requires the line in both.
- **`core.hooksPath`.** It is set to `.claude/pipeline/githooks` by `bootstrap.sh`. It is local git config, so it is not tracked.

**A closed transform set.** A copied shim is its source plus exactly two transforms:
1. a generated marker naming the source path. In a Markdown file it is an HTML comment inserted after the YAML frontmatter. In a YAML file (`ci.yml`) or the `.gitignore` block, it is a `#` comment line: the first line of `ci.yml`, and the opening and closing lines of the `.gitignore` block, of the form `# >>> generated by .claude/pipeline/tools/package.py refresh from <source> >>>`, closed by `# <<< … <<<`.
2. relative Markdown link targets, re-based so that each resolves to the same file it resolves to from the source.

Settings entries are merged. JSON has no comments, so ownership is by content: the package-owned entries are exactly those whose command contains `/.claude/pipeline/hooks/`. `refresh` replaces those and never touches the host's own entries. Comparisons normalize line endings, because `core.autocrlf` is `true` on the owner's machine.

**Collision refusal.** `refresh` and `install` refuse, with a non-zero exit and before writing anything, when a shim path holds a file without the generated marker. Examples are a host skill named `ship` or a host `.github/workflows/ci.yml`. The refusal names the path. A host file is never overwritten.

**Shims in the review and promotion gates:**
- **R-LOC.** Generated shims are not runtime artifacts. Their sources under `.claude/pipeline/{agents,skills,hooks}/` are. The count uses rename and copy detection (`git diff --find-renames --find-copies --numstat` against the verify base), so a moved line is not a new line. `reviewer.md` stays the canonical definition site (ADR-0077 D1) and is updated in slice 1.
- **Guardrail set.** ADR-0070 D4's guardrail-machinery set is the ADR-0064 D4 enforcement paths, plus the critic prompts, the release-gate definition (the `RELEASE-READY` check and the promotion tooling) and the branch-protection config. Read through D1, the paths are `.github/workflows/**`, `.claude/settings.json`, `.claude/pipeline/hooks/**`, `.claude/pipeline/tools/ci-checks.sh`, `.claude/pipeline/githooks/**` and `.claude/pipeline/agents/*-critic.md`; the release-gate definition and the branch-protection config are unchanged in substance. This decision adds the critic shims `.claude/agents/pipeline/*-critic.md`, since they are what Claude Code actually runs.

**Enforcement:**
- **Mechanism:**
  - PACKAGE-INTEGRITY **currency arm** (both modes). The tracked shims equal `refresh`'s output, EOL-normalized, and the root `CLAUDE.md` holds the import line exactly once. The arm FAILs naming each divergent path.
  - The package pre-commit hook runs the same arm and blocks the commit locally, as a fast local catch. This is ADR-0034 D6's defense-in-depth pattern: a pre-commit block that `--no-verify` can bypass, backed by a gate that cannot be bypassed. In ADR-0034 that gate is D5's reviewer rule; here it is CI CHECK 30.
  - A regression test covers the collision refusal and the settings merge.
- **Parsimony:**
  - CHECK 17/20 (ADR-0073 D2/D3) gate the rendered rules and repo map against their generators. They do not know that a skill exists twice.
  - CHECK 2 gates only the README.
  - The reviewer cannot reliably diff two copies of a 500-line prompt by eye.
- **Shadow guarded against:** *silent fork of a prompt.* Someone edits the shim Claude Code loads instead of its source, or edits the source and forgets `refresh`. The session then runs a prompt that exists in no published version, and the next upgrade reverts it silently.
- **Advisory residue:**
  - A path built at runtime inside a prompt, for example by string concatenation, escapes link re-basing. **(advisory)**; the prompt-path arm (D1) covers the command-shaped cases.
  - The package `CLAUDE.md` may also load a second time, as a subdirectory CLAUDE.md, when Claude reads a file under `.claude/pipeline/`. **(advisory until measured)**; see Open questions.
- **Rule id:** PIP-035, shared with D1.

### D3 — A version is a tag on a split commit; install and upgrade are `git subtree --squash` plus a shim refresh

**Version.** `.claude/pipeline/VERSION` holds `X.Y.Z`. A version is published as an annotated tag `vX.Y.Z` on the commit that `git subtree split --prefix=.claude/pipeline <source>` produces. The tag message names the source sha. There is no distribution branch: a host pins an immutable tag.

**Names.** ADR-0090 D1 titles a version milestone `v<major>.<minor>[.<patch>]`. That milestone's tag is `v<major>.<minor>.<patch>`, with a missing patch read as `0`; milestone `v1.0` is tag `v1.0.0` and `VERSION` `1.0.0`. `publish` orders versions only among tags matching `^v[0-9]+\.[0-9]+\.[0-9]+$`, compared numerically per component. Any other `v*` tag is ignored for ordering.

**Refusals.** Every refusal below prints `package.py: refused (<cause>): <detail>` to stderr and exits non-zero before any write. The cause tokens are fixed, so tests and the golden test can assert them.

**Publish** (`python3 .claude/pipeline/tools/package.py publish X.Y.Z`) runs in the home repository only. It refuses when:
- the mode is `host` (D5) — `host-mode`;
- the working tree is dirty — `dirty-tree`;
- `VERSION` is not `X.Y.Z` — `version-mismatch`;
- `vX.Y.Z` exists locally or on origin — `tag-exists`;
- `X.Y.Z` is not greater than the newest version tag — `not-newer`;
- `HEAD` is not reachable from `origin/<release>` (ADR-0089 D1) — `not-promoted`. Only promoted content is published.

It then splits, tags and pushes the tag.

This is the tag ADR-0090 D1 says the owner creates after `RELEASE-GATE` PASS. Publish does not re-evaluate `RELEASE-GATE`; the owner runs it after the gate passes, as ADR-0090 D1 orders. Publish is not a verb: it writes no span and moves no PR, slice or PRD, so ADR-0076 D1's verb set is unchanged.

**Install** (`bash .claude/pipeline/install.sh --source <url> --tag vX.Y.Z [--integration <b> --release <b>]`, run from the host's root). It refuses when the repository already holds `.claude/pipeline/` without a `package_source` (the home repository, `home-repository`), when it holds one with a `package_source` (`already-installed`; upgrade instead), and on a D2 collision (`collision`). Otherwise:
1. It runs `git subtree add --prefix=.claude/pipeline <url> vX.Y.Z --squash -m "<message>"` and re-executes the freshly added copy, so the installed version wires itself. The message is `chore(pipeline): install vX.Y.Z` followed, when an agent runs it, by the trailer COM-002 requires as amended by ADR-0086 D2 (the actual agent's `Co-Authored-By`). git-subtree writes this merge commit with `git commit-tree`, so no commit-msg hook adds or checks the trailer; the tool writes it into the message itself.
2. It writes `package_source=<url>` into `.claude/pipeline.conf`, plus the branch roles if given (ADR-0089 D1).
3. It runs `refresh`, appends the import line, and commits `chore(pipeline): wire vX.Y.Z` on the current branch, with the same trailer rule.
4. It prints the next two steps, push and then `bash .claude/pipeline/bootstrap.sh`, and runs neither. Bootstrap sets labels, `core.hooksPath`, the integration branch and protection (ADR-0089 D2).

The source URL is always an argument, never a literal (Q7). In a real host the push and the bootstrap run are the owner's steps: no agent runs them, and ADR-0001 D12 hard rules 2–3 stand there.

**Upgrade** (`python3 .claude/pipeline/tools/package.py upgrade vX.Y.Z`, run from the host's root) is the one command. It refuses when:
- the mode is `home` — `home-mode`;
- the tree is dirty — `dirty-tree`;
- the current branch is either configured role — `role-branch`;
- the pristine test fails — `not-pristine`;
- `X.Y.Z` is not greater than the installed `VERSION` — `not-newer`.

Then it:
1. runs `git subtree pull --prefix=.claude/pipeline <package_source> vX.Y.Z --squash -m "<message>"`, where the message is `chore(pipeline): upgrade to vX.Y.Z` followed, when an agent runs it, by the trailer COM-002 requires as amended by ADR-0086 D2;
2. starts a **fresh process** of the new version's `refresh` and commits `chore(pipeline): refresh shims for vX.Y.Z`, with the same trailer rule, if anything changed;
3. starts a fresh process of `package.py check`, which must PASS.

It never writes the root `CLAUDE.md`. Git does the merging; the tool writes no merge logic.

**Squash shape.** This is the one definition that the pristine test, CHECK 3's exemption, `pr-merge`'s upgrade legs and R-UPGRADE all use. `package.py` implements it once, and exposes it as `package.py shape <rev>` (exit 0 when squash-shape). A commit C is **squash-shape** when all three hold:
1. **Trailers.** C's message carries the line `git-subtree-dir: .claude/pipeline`, one `git-subtree-split: <40-hex>` line, and no `git-subtree-mainline:` line.
2. **Tree.** C's tree equals its split tree, the tree of the commit its `git-subtree-split:` line names.
3. **Parents.** C has no parent (the install squash), or exactly one parent that is itself squash-shape (an upgrade squash).

A GitHub squash-merge fails clause 3, because its one parent carries no trailer, and clause 2, because its tree is the whole repository (Context).

*Evaluating clause 2 where the split commit is absent.* A squash commit never links its split commit, so a fresh clone or a CI checkout lacks it (Context). `install` and `upgrade` always have it, because git-subtree just fetched it. Where the split commit is absent, clause 2 is evaluated on the tree's own shape instead: the tree has a root `VERSION` and no `.claude` entry. A package root always has that shape, and a whole-repository tree of a host never does, because a host's tree always holds `.claude/pipeline`. The verdict line then says `split tree not local; tree-shape test`, so the weaker observation is named, not passed off as the split comparison. Clause 3 needs no remote object: every parent in the chain is reachable from `HEAD`.

**Pristine** is a tree-equality test. In host mode, the newest commit reachable from `HEAD` whose message carries `git-subtree-dir: .claude/pipeline` must be squash-shape, and `HEAD:.claude/pipeline` must equal its tree. It FAILs in both squash-merge cases in Context. A host therefore never edits its package, and a squash-merged upgrade is detected at once.

**Throwaway verification repositories.** A repository created solely to verify this package is exempt from exactly two rules. That means the PRD's sandbox upstream U and its sandbox hosts, and G, the owner's golden-test repository.
1. Agents may run `publish` there, so the advisory below does not cover its tags.
2. Agents may push to it directly and change it without a PR. ADR-0001 D12 hard rules 2–3 do not bind inside it (Supersession ledger).

The home repository and every real host are never exempt, whatever their state. A throwaway repository holds no milestone and no release, so its rehearsal tags are not ADR-0090 D1's version tag, which stays the owner's act in the repository whose milestone passed. The Bash hook's `:main` refspec deny (`pre-tool-bash-classify.py`, PIP-017) still applies everywhere. Sandbox steps therefore advance a sandbox's `main` with `git update-ref` inside the throwaway bare repository, never with a push.

**Enforcement:**
- **Mechanism:**
  - PACKAGE-INTEGRITY **pristine arm**, host only.
  - Regression tests run each publish, install and upgrade refusal against a local bare upstream and host, asserting the cause token.
  - A predicate test runs `package.py shape` over a real `add`/`pull` chain and over both simulated squash-merges.
  - An end-to-end sandbox test runs publish → install → upgrade and asserts the tree equalities and that the root `CLAUDE.md` blob is unchanged across the upgrade.
- **Parsimony:**
  - ADR-0089 D1 resolves branch roles but records no package identity.
  - ADR-0090's `RELEASE-GATE` decides when a version is done, not what the tag points at.
  - Nothing today records or verifies a version.
- **Shadow guarded against:** *the untraceable install.* A host runs a package that matches no published version (hand-edited, half-pulled, or squash-merged), so neither the owner nor the next upgrade can say what is installed. Or an upgrade rewrites the host's own `CLAUDE.md`.
- **Advisory residue:** outside throwaway verification repositories, agents never run `publish` **(advisory — publish creates a version tag, which ADR-0090 D1 reserves to the owner; no hook denies it, because ADR-0076 D4 admits a deny form only with an incident and a sanctioned alternative; evidence trigger: an agent-created `v*` tag outside a throwaway verification repository)**.
- **Rule id:** PIP-036, shared with D4.

### D4 — An upgrade lands as an upgrade PR, merged with a merge commit

An upgrade reaches the integration branch as one **upgrade PR**. It is not ADR-0090 D4's lane PR: a lane PR carries the label `lane` and closes `bug` issues, while an upgrade PR carries `pipeline-upgrade` and closes one `feature` issue, and `pr-merge` keys its lane-PR leg and its upgrade legs on those different labels. The PR:
- is labeled `pipeline-upgrade`;
- is based on the integration branch, from a head branch that is neither role;
- is opened with `python3 .claude/pipeline/tools/pipe/pr-open`;
- closes exactly one open issue labeled `pipeline-upgrade`, which also carries its ADR-0090 D1 class, `feature`. The branch-name hook requires the issue number, as for the trivial lane (I3).

`bootstrap.sh`'s `LABELS` and `dashboard/health.py`'s `_REQUIRED_LABELS` gain `pipeline-upgrade`, so the label exists on every bootstrapped host and REQUIRED-LABELS reports it missing otherwise.

**The reviewer's R-UPGRADE rubric.** For a PR labeled `pipeline-upgrade`, R-UPGRADE is the whole hard-block rubric. The reviewer APPROVEs exactly when all six conditions below hold, BLOCKs otherwise, and evaluates no other hard-block rule:
1. `python3 .claude/pipeline/tools/package.py check` exits 0 at the PR head.
2. Every changed path is under `.claude/pipeline/` or is a D2 shim, and inside `.claude/settings.json` and `.gitignore` only the package-owned part changed.
3. Over the integration branch, the PR's commits are exactly one squash-shape commit (D3), one merge commit whose second parent is that squash commit, and at most one refresh commit. The merge commit's subject is `chore(pipeline): upgrade to v<X.Y.Z>`, and the refresh commit's is `chore(pipeline): refresh shims for v<X.Y.Z>`. Both subjects are at most 72 characters, and both commits carry the trailer COM-002 requires, as amended by ADR-0086 D2, when an agent wrote them.
4. `VERSION` at the head is greater than at the base.
5. The PR is based on the integration branch, and its head branch is neither role.
6. The body closes exactly one open issue labeled `pipeline-upgrade`.

Every other hard-block rule either judges package content, which was reviewed where it was built, or is restated here: R-SCOPE as condition 2, R-CONV-COMMITS as condition 3, R-NO-MAIN as condition 5 and R-CLOSES as condition 6. Round-3 escalation (I5) and the pre-merge CI-terminal gate (ADR-0077 D2) apply unchanged.

**Merge.** `tools/pipe/pr-merge` keeps ADR-0076 D3's reviewer-verdict assertion for `pipeline-upgrade` PRs. ADR-0076 D3 scopes that assertion to "a slice PR"; this decision extends it to upgrade PRs, as ADR-0090 D4 extends it to lane PRs. It adds three upgrade-only legs:
1. **Mode.** It merges a `pipeline-upgrade` PR with `--merge --auto`, never `--squash`.
2. **Refusals.** It refuses, with a non-zero exit and no span, in two cases:
   - a PR without the label, any of whose commits carries a `git-subtree-dir: .claude/pipeline` line. This reads the commits from the same `gh pr view` call that already supplies the labels, so a PR without the label costs no extra call;
   - a labeled PR that holds no squash-shape commit, evaluated by `package.py shape` on the PR's head commits.
3. **Issue.** On a confirmed merge, it closes the PR's `Closes #<n>` issue. GitHub closes linked issues only on merges into the default branch, and the integration branch is not the default branch (the reason ADR-0090 D4's third leg records).

CHECK 23 therefore still finds a verdict on every merged PR.

**Commit format.** The squash-shape commit is the only commit exempt from ADR-0001 D12 hard rule 4 as COM-001 and COM-002 render it. That covers the Conventional Commits subject format and the agent trailer COM-002 requires (as amended by ADR-0086 D2). git-subtree writes that commit's message, and no hook runs on it (Context). The 72-character cap still applies; its subject is at most 68 characters even with 12-character short shas. CHECK 3 prints one `CHECK 3 EXEMPT: <40-hex sha> subtree squash (ADR-0092 D4)` line for each commit it exempts, so an exemption is observed, not silent. The merge commit and the refresh commit keep every rule.

**Supersedes, in part:**
- ADR-0002 D9-revised: its squash-only merge mode, for upgrade PRs only.
- ADR-0042 D3: its `--squash` flag, for upgrade PRs only.
- ADR-0003 D1: "closes one slice", for upgrade PRs only. An upgrade PR closes one tracked issue, like the trivial lane.
- ADR-0001 D12: hard rule 4, for the squash shape only.

**Enforcement:**
- **Mechanism:**
  - R-UPGRADE, a reviewer rubric rule, with its deterministic core in `package.py check` and `package.py shape`.
  - `pr-merge`'s mode selection, its two refusals and its issue close, each covered by a regression test with a `gh` shim.
  - CHECK 3's exemption, with a test that a non-conforming subject on any other commit still FAILs. That includes a commit that carries both trailer lines but not the squash shape.
  - The pristine arm (D3), which catches a squash merge done outside `pr-merge`.
- **Parsimony:**
  - The trivial lane caps a PR at 10 LoC.
  - R-CLOSES requires a slice.
  - PIP-016 and CHECK 23 require a verdict.
  - `pr-merge` knows only squash.
  - No existing PR path can admit a package delta, and every existing merge path destroys the topology the next upgrade needs.
- **Shadow guarded against:**
  - *the upgrade that breaks the next upgrade:* a squash merge erases the subtree topology (Context);
  - *the smuggled edit:* a hand change rides inside a large mechanical diff;
  - *the verdict hole:* a hand-merged upgrade fails CHECK 23 on the host's next 20 PRs;
  - *the orphan issue:* an upgrade issue left open after its merge, which a host in release mode would hold against its version (ADR-0090 D1).
- **Advisory residue:** a human can still squash-merge an upgrade in the GitHub UI. The pristine arm detects it after the fact, not before. **(advisory)**; `UPGRADING.md` states the recovery.
- **Rule id:** PIP-036, shared with D3.

### D5 — Every check declares where it runs: home, host, or both

**Mode.** `python3 .claude/pipeline/tools/pipeline_config.py mode` prints `host` when `.claude/pipeline.conf` names a `package_source`. Otherwise it prints `home` (ADR-0089 D1's resolver, one new query). The home repository never has a `package_source`, and `install` always writes one.

**Scopes.** Every `CHECK <n>` in `tools/ci-checks.sh` and every step of the package pre-commit hook declares one scope in a single table:
- **`home`:** its subject is home-only material. That covers `decisions/` and its index, rendered rules versus ADR frontmatter and `RULE_IDS_BASELINE`, the rendered repo map, `README.template.md`/`README.md`, `qa-proof/`, a home allowlist, and ADR-0086's root OpenAI entrypoints (`AGENTS.md`, `.agents/`, `docs/openai-workflow.md`), which exist only at home in v1.0. It also covers every check whose subject set is every tracked file, since host hygiene scanning is v1.1.
- **`host`:** it checks the pristine arm.
- **`both`:** everything else.

At a455795, the measured `home` candidates are CHECKs 2, 4, 5, 6, 7, 13, 17, 20, 25 and 26. The slice that lands the table classifies each against this rule. CHECK 29 is `both` (D1). CHECK 30's arms carry their own scopes (D1–D3).

A check outside its scope prints `N/A (home-only)` or `N/A (host-only)`, and never PASS.

**Shared subjects.** Some files are written by both the package and the host: the D2 wiring files `.claude/settings.json`, `.gitignore`, the root `CLAUDE.md` and `.github/workflows/ci.yml`. A check whose subject is such a file scores only the package-owned part, as D2's ownership rule defines it (ADR-0083 D3: a host owes the package nothing about its own entries). At a455795 this touches CHECK 8(c): its cap of 9 hook commands counts only commands containing `/.claude/pipeline/hooks/` (7 today), so a host's own hooks never count against it. CHECK 1 still parses the whole of `.claude/settings.json`. Claude Code refuses an invalid settings file for every entry in it, so that invariant is one every repository owes.

**Generators run at home.** A generator whose output lies inside the package, or is the home README, refuses in host mode with `refused (host-mode)`: `gen_rules.py`, `gen_repo_map.py` and `readme_gen.py`. In a host, re-rendering from the host's `decisions/` would rewrite the package and break pristine. Their currency checks, CHECKs 2, 17 and 20, are `home`. Rules rendered from a host's own ADRs are v1.1.

**Rubric rules.** A reviewer or critic rule whose subject is home-only material states its scope in its prompt. At a455795 that is R-DOCS-CURRENT (ADR-0034 D5; its subject is `README.template.md`), which states `home` in `reviewer.md`. The critics' ADR-existence oracles read the host's own `decisions/` in a host, which is right for the host's ADRs. ADR links inside package prompts are provenance (Consequences). A host ADR whose number collides with a home number that a package prompt cites is an open question.

**Tests.** A test whose subject is home-only material carries `@pytest.mark.home`. There is one selection mechanism: `tests/conftest.py` asks the resolver for the mode and, in host mode, marks each `home` test skipped with the reason `home-only`. CHECK 12 runs the same command in both modes. `tests/conftest.py` provides the two roots, `PKG_ROOT` and `REPO_ROOT`, from the resolver (D1). The pytest rootdir is pinned to the package, so node ids, and with them `tests/quarantine.txt` entries (ADR-0067 D4), keep their `tests/…` form.

**Enforcement:**
- **Mechanism:**
  - A completeness test: every `CHECK <n>` header has exactly one scope entry, and every test file that opens a home-only path carries the marker.
  - A generator test: each of the three generators refuses in a host-mode fixture and writes nothing.
  - A shared-subject test: CHECK 8 PASSes in a host whose own settings add hook commands beyond the cap.
  - The **self-install test**. It copies the working tree's package into a temporary source repository with synthetic history, publishes it there, installs it into a temporary host behind a `gh` shim, and runs host-mode `ci-checks.sh`. That run must exit 0 with one `N/A (home-only)` line per `home` check. A recursion guard keeps the nested run from re-running the suite, and a separate collect-only run asserts the host-mode selection.
- **Parsimony:**
  - ADR-0083 D3 states the principle, but no mechanism assigns subject sets per repository.
  - CHECK 12's quarantine machinery selects tests by node id, not by where they may run.
  - Before this ADR, no check had ever run anywhere but home.
- **Shadow guarded against:** *home coupling leak.* A check, generator or test that reads home-only material runs in a host. It either FAILs the host's first PR, which blocks the golden path, rewrites the package, or PASSes on an empty subject set, which is an unobserved claim (ADR-0083 D3).
- **Advisory residue:**
  - A check scoped `home` that ought to run in hosts is silent, because a missing check produces no signal. **(advisory)**; the reviewer judges each new check's scope entry, and host hygiene scanning is deferred to v1.1.
  - A check added later whose subject is a shared file, and which scores the whole file, is caught only when a host's own entries trip it. **(advisory)**; the shared-subject test covers CHECK 8, the only such check at a455795 besides CHECK 1's platform contract.
- **Rule id:** PIP-037.

## Supersession ledger (clause-exact)

Following the per-decision precedent that ADR-0088 and ADR-0089 record, the superseded ADR files are not edited. They keep `superseded_by: []`, and `decisions/README.md` annotates each row. No rule_id drops: ADR-0001 D1 carries none, and PIP-001, PIP-002, COM-001 and COM-002 stay under partial supersession.

- **ADR-0001 D1, in full.**
  - **Falls:** the clone-as-template reuse model. That is the clone command, "everything travels with the clone", "each project owns its own copy and can diverge", and "updates … don't auto-propagate". The record of slice 1's two-scope `grill-me` install is historical and falls with it.
  - **Replaced by:** D1 and D3.
  - This also retires the pre-existing slug divergence that ADR-0089 listed against ADR-0001 D1: the clone command it names no longer exists.
- **ADR-0001 D12, in part.**
  - **Falls, clause 1:** hard rule 4's "every", for the squash shape defined in D3 only, including the tightening that COM-001 and COM-002 render from it (the Conventional Commits subject format and the agent trailer, COM-002 as amended by ADR-0086 D2). The 72-character subject cap still binds it.
  - **Falls, clause 2:** hard rules 2 and 3, for agent actions inside a throwaway repository created solely to verify this package (D3). That is the PRD's sandbox upstream and hosts, and the owner's golden-test repository.
  - **Stands:** hard rules 1 and 5–7 everywhere. Rule 4 for every other commit, including the install merge commit, the wire commit, the upgrade merge commit and the refresh commit. Rules 2 and 3 for the home repository and every real host, without exception. COM-001's and COM-002's rendered texts stay and read with the one commit-shape exemption.
- **ADR-0002 D9-revised, in part.**
  - **Falls:** "auto-merge (`gh pr merge --squash`)", for `pipeline-upgrade` PRs only. With it goes, for those PRs only, the elaborating same-ADR section "What the reviewer subagent is now authorized to do", whose "NOT authorized to … Merge with any other strategy (`--merge` or `--rebase`)" clause carries no D-ID of its own.
  - **Chain:** ADR-0042 D3 already adjusted D9-revised's command text, adding `--auto`. This ADR's partial supersession of both concerns only the `--squash` flag, for upgrade PRs.
  - **Stands:** APPROVE gates merge, BLOCK returns the PR, round-3 escalation, never merging on judgment, and squash for every other PR. PIP-001's rendered text stays.
- **ADR-0003 D1, in part.**
  - **Falls:** "PR — one merged change, closes one slice", for `pipeline-upgrade` PRs only. They close one tracked issue instead, as CLAUDE.md I3's trivial lane does. This is in addition to ADR-0090's lane-PR clause.
  - **Stands:** the three-tier hierarchy for feature work, the `prd`/`slice` labels, and every other clause. PIP-002's rendered text stays.
- **ADR-0042 D3, in part.**
  - **Falls:** the `--squash` flag of `gh pr merge --squash --auto`, for `pipeline-upgrade` PRs only, which use `--merge --auto`.
  - **Stands:** `--auto`, "a red-CI PR never merges, even on reviewer APPROVE", and asynchronous merge reporting.

## Left standing, explicitly

**Read through D1's rename map, not superseded.** Each text below names a pre-move path. It is true verbatim at the mapped path, and its substance is unchanged.
- ADR-0004 D3 (layer 1 is `githooks/pre-commit` via `core.hooksPath`), as ADR-0086 already superseded it in part: the issue-bound `codex/<type>/<N>-<slug>` branch prefix is an added case of that hook, and the moved hook keeps it.
- ADR-0008 D6 (`bootstrap.sh`; `core.hooksPath`).
- ADR-0015 D1 (entries are still in `.claude/settings.json`).
- ADR-0023 D6 and D7 (the commit-msg hook and the hook scripts are files under the package's `githooks/` and `hooks/`).
- ADR-0034 D4–D6 (README generation, now home-scoped by D5).
- ADR-0042 D1 (the workflow runs `ci-checks.sh`, now from the package; generated by D2).
- ADR-0054 D3 (fixtures live in the package's `dashboard/fixtures/`).
- ADR-0064 D3 (`health.py` stays the single registry; extended above).
- ADR-0067 D1 and D4 (tests and quarantine).
- ADR-0070 D4 (guardrail set, extended by D2).
- ADR-0073 D1–D3 (rendered rules and imports, home-only by D5).
- ADR-0076 D1–D4 (verbs under `tools/pipe/`, the closed kind enum in `tools/trace.py`, the verdict assertion, the hook's deny forms).
- ADR-0077 D1 (`reviewer.md` stays canonical for R-LOC).
- ADR-0079 D1 (the recorded-CI evidence path).
- ADR-0080 D2 (the retired verb and `tools/trace.py`'s enum).
- ADR-0084 D1/D2 (CHECK 25/26, now home-scoped by D5; the allowlist moves with `tools/`).
- ADR-0088 D2–D5 (observability doc, README entrypoint, roster constant, `dashboard/**` routing).
- ADR-0089 D1 (CHECK 29's subject set, restated at the package paths in D1; `.claude/pipeline.conf` stays outside the package).
- ADR-0086 D2–D6, each unchanged in substance:
  - D2: the branch classifier is `.claude/pipeline/tools/workflow_branch.py`, and the typed `codex/` grammar lives in the moved `githooks/pre-commit`; COM-002's OpenAI attribution clause stands and binds the install, wire, upgrade and refresh commits (D3, D4).
  - D3: the adapter still validates proof before it runs the verbs, now at their package paths.
  - D4: `.codex/hooks.json` does not exist at a455795, and D4 has its handlers resolve from the git root. **Merge order:** if it lands after BASE, its handler commands name `.claude/pipeline/…` paths from the start; if it lands before BASE, the move sweeps it like `.claude/settings.json`, and it joins the prompt-path arm's root OpenAI entrypoints.
  - D5: the adapter's tests stay in the suite CI runs, as package tests, marked `home` because they read the root entrypoints (D5).
  - D6: forward binding is unaffected.

  ADR-0086's Alternatives rejected moving every canonical file into a shared package "now", for merge-conflict and regression risk during concurrent development. That was a timing judgement, not a decision. This move is sequenced after the concurrent PRDs (PRD §4), and it keeps each file canonical at one new address, so ADR-0086's one-source property holds.

**Rendered rules.** Every rendered rule keeps its substance. Every rendered statement whose text names a moved path at BASE is re-rendered at the mapped path with "(paths per ADR-0092 D1)" appended, because hosts load that layer and have no ADRs to read through. A full token scan of `_global.md` and `rules/*.md` at a455795 finds fifteen: CRI-004, DOC-001, PIP-014, PIP-015, PIP-016, PIP-017, PIP-023, PIP-024, PIP-031, PIP-032, PIP-033, PIP-034, REG-001, REG-003 and VER-011. The committing slice re-scans at its own BASE (Propagation). The rest keep their text verbatim: HOK-001..009, ISO-001..006, DOC-002..006, VER-002, VER-007, VER-008, VER-012, PIP-025, PIP-030, COM-002 and every other id. HOK-007 names `.claude/skills/` and `.claude/agents/` as the directories Claude Code loads, which the shims keep real; PIP-025 names "the `tests/` suite" as a concept, not a command path; PIP-030 and COM-002 (ADR-0086) name no path. Those phrases stay.

**Also left standing:**
- **ADR-0001 D8.** Its required orientation artifacts (`CLAUDE.md`, `README.md`, `decisions/`) are each repository's own. The home repository still has all three. The package carries its own `CLAUDE.md` and ships neither `README.md` nor `decisions/`, which each repository keeps for itself.
- **ADR-0090 D1's tag clause.** The owner creates the version tag, and agents do not. A throwaway verification repository's rehearsal tags are not version tags (D3).
- **ADR-0040 D1/D2.** Untouched. What a PROVISIONAL production verify does to closure is a pre-existing conflict with `qa-tester.md`'s production-verify trailer rule ("On PROVISIONAL, `RESULT` is also `FAIL`"), captured as #1505. The PRD's golden test does not depend on its outcome.

**Unaffected:**
- ADR-0070 D1–D3 (branch roles and promotion).
- ADR-0075 D5, all three gates. Gate (2) still fails commit-subject violations. Under D4, a squash-shape commit is not a violation, and every other subject is judged as before.
- ADR-0090 D2–D5. An upgrade PR is ordinary work for a release drain.

## Consequences

- **A new project is three commands:**
  1. `install.sh --source … --tag …`;
  2. push;
  3. `bootstrap.sh`.

  The owner runs the last two. An upgrade is one command and one reviewed PR. The owner's v1.0 definition (Q3) becomes testable, and the PRD's golden test is that test.
- **Names do not change.** `/ship` stays `/ship` and `reviewer` stays `reviewer`, so skill references, `subagent_type` dispatches and the logs keyed on them (`workflow-events.jsonl`, trace spans) stay continuous. This is the price the plugin alternative would have charged.
- **The home repository dogfoods the host layout.** Home CI additionally runs the self-install test, which costs time on every PR.
- **About 15 prompt files and 4 rule files exist twice** (source plus shim). The copies are byte-derived, currency-gated, and excluded from R-LOC.
- **Every upgrade in a host is a guardrail-touching promotion.** It waits for `.claude/PROMOTE_OK` (ADR-0070 D4), which is the human ack for new pipeline code.
- **Hosts receive no ADRs.** ADR links inside package prompts are provenance. They resolve at home and dangle in hosts, which is why D5 scopes the link checks `home`. Relative links from package prompts to `decisions/` gain one `../` per level the move adds, and CHECK 4 fails on any that dangle at home.
- **Hosts render no rules of their own in v1.0** (D5).
- **Running sessions must restart** after the prompt-surface and hook moves merge. Until then their loaded hook commands point at deleted scripts, and `core.hooksPath` in existing clones points at a deleted `.githooks/`. The deploy-handshake banner (SessionStart) detects both. `bootstrap.sh` repairs the second.
- **Rendered rules:** PIP-035, PIP-036 and PIP-037 are added, and every rendered text naming a moved path is re-rendered at package paths (fifteen at a455795, listed under Left standing). `RULE_IDS_BASELINE` rises to the live baseline at merge + 3.
- **The OpenAI adapter moves with the product** (ADR-0086, PIP-030). PR #1476 merged at a455795. Its code and tests move with `tools/` and `tests/`, and its compiled-in paths are rewritten in the slice that moves what they name (D1). Its router targets the package skill source, not the shim, so ADR-0086 D1's "source procedure, not a copied body" holds. `AGENTS.md`, `.agents/skills/` and `docs/openai-workflow.md` stay at the root as home-side files for v1.0; they are prompt-path subjects at home, and their tests are `home`-marked. A host receives the adapter's code but not its entrypoints, so an OpenAI run in a host refuses on the missing sources rather than running unguarded; the Codex host is v1.1 (#1436). PIP-030 keeps its text.
- **Accepted costs:**
  - The first install commits to the host's current branch before protection exists. That fits a fresh repository only; installing onto an existing protected host is deferred with the WEDLY port (D381).
  - A pushing credential needs GitHub's `workflow` scope to add the generated workflow.
  - A fresh clone evaluates clause 2 of the squash shape by tree shape, not by split comparison (D3).

## Alternatives considered

- **(A) Subtree at `.claude/pipeline/` plus generated shims.** Chosen, as D1–D5.
  - **PRO:**
    - one vehicle and one version, committed with the host;
    - git does the merging (Q6);
    - names and logs unchanged;
    - agents keep every frontmatter field;
    - settings hooks, project skills, subagents and `CLAUDE.md` all load in a `claude -p` run without `--bare` (the headless doc: `-p` "loads the same context an interactive session would"), and the permissions doc's list of what a never-trusted `-p` run withholds names none of them, which the golden test needs;
    - Windows-safe;
    - reuses this repository's generate-and-gate pattern (ADR-0034 D4, ADR-0073 D2).
  - **CON:**
    - about 19 duplicated files;
    - a shim generator and a settings merge to maintain;
    - a collision refusal instead of namespacing;
    - `upgrade` must hand off to a fresh process.
- **(B) Plugin through a git marketplace, instead of subtree.** Rejected.
  - **PRO:**
    - native discovery and native update (`claude plugin update`);
    - namespacing prevents collisions with host skills;
    - no copies.
  - **CON:**
    - It cannot carry `CLAUDE.md` ("not loaded as project context"), rules, the CI workflow, git hooks, or the tools CI runs, because CI never loads plugins. It is therefore never a whole vehicle.
    - Skills become `/pipeline:ship` and agents `pipeline:reviewer`. That rewrites every skill reference, agent-name token and `subagent_type` line (about 300, measured at 2980962), and breaks continuity of every log keyed on `subagent_type`.
    - The per-user plugin cache means the version is not committed with the host, so CI and sessions can run different versions.
    - Third-party marketplaces do not auto-update by default, project settings no longer install external-source plugins per user (v2.1.195), and `extraKnownMarketplaces` waits for trust.
    - It reverses Q6.
- **(C1) Hybrid: a marketplace plugin for skills, agents and hooks, plus a subtree for CLAUDE.md, rules, tools, CI and git hooks.** Rejected.
  - **PRO:** native discovery for the prompt layer, and git-versioned code.
  - **CON:**
    - two vehicles with two version axes (per user versus per repository), so a skill can call a tool flag its subtree does not have yet;
    - two upgrade commands, which contradicts Q3's one command;
    - plugin-scope hooks share B's trust limits, so guard hooks would need the settings path anyway;
    - B's namespacing cost.
- **(C2) Subtree at `.claude/skills/pipeline/` shaped as a project `@skills-dir` plugin (one vehicle, zero copies for skills, agents and hooks).** The strongest alternative. Rejected for v1.0.
  - **PRO:** a single git subtree (Q6 kept), no shim copies, and native namespacing.
  - **CON:**
    - B's rename and log-continuity cost;
    - the permissions table withholds project `@skills-dir` plugins in `claude -p`/SDK runs and under parent-folder trust, so headless jobs, the golden sandbox and fresh clones would lose the whole prompt layer silently;
    - it loads only from the primary working directory;
    - its hooks would carry the same trust gate, so guard hooks must stay in settings;
    - rules, CI and git hooks still need shims;
    - it moves the plan's prefix.
  - **Revisit trigger:** a recorded host name collision, or a recorded shim-drift incident that the currency arm missed.
- **Symlinks from the fixed locations into the package.** Rejected. `core.symlinks` is `false` on the owner's Windows machine (measured), where git checks a symlink out as a one-line text file. The memory docs recommend an import over a symlink on Windows.
- **Pointer shims** (a shim body that says "read the package file"). Rejected. A subagent's system prompt would become an instruction to fetch its real prompt through a tool call: lower authority, an extra turn, and an adherence risk.
- **`--add-dir .claude/pipeline` or `CLAUDE_CODE_PLUGIN_DIRS`.** Rejected. Launch flags and environment variables are not in the repository, project settings cannot set `CLAUDE_CODE_PLUGIN_DIRS`, and every headless job and worktree session would need them.
- **Home keeps the product at the root, and publish copies it into a prefix.** Rejected. Each version would be an unrelated commit, so `git subtree pull` would have no common base to merge from, and home would never run the paths hosts run.
- **Home prefix `pipeline/`, as the plan's text says.** Rejected. Home would spell every command `pipeline/tools/…` while hosts spell it `.claude/pipeline/tools/…`. Either every prompt would resolve a root at runtime, or home would never run the prompts hosts run.
- **A moving `release` distribution branch, as the plan's text says.** Replaced by tags.
  - ADR-0089 D1 names "release" a branch role.
  - Hosts should pin immutable versions.
  - A branch would add a second name for the same split commits.
  - Revisit if a track-the-latest host appears.
- **Define the squash shape as a parentless commit** (this ADR's first draft). Rejected by measurement: git-subtree gives every pull squash the previous squash as its parent (Context). Every correct upgrade would have failed its own pristine test, CHECK 3 and `pr-merge`.
- **Fetch the split commit from `package_source` in every check.** Rejected. It puts the network inside CI and the pristine arm, and the golden test's upstream is a local path that the host's CI cannot reach. The tree-shape test needs no remote object, and the parent chain alone already rejects a GitHub squash-merge.
- **Squash-merge upgrade PRs to keep PIP-001 uniform.** Rejected. The next `git subtree pull` would either parent its squash on a whole-repository commit or conflict against the previous version (Context).
- **Write our own conformant squash commit, to keep ADR-0001 D12 whole.** Rejected. It re-implements git-subtree's internals, against the plan's "git does the merging".
- **The owner pushes upgrades straight to the integration branch** (`enforce_admins` is off). Rejected. It breaks ADR-0001 D12 hard rule 3 ("every change ships through a PR") and bypasses CI.
- **The owner runs every sandbox publish and push by hand, instead of a carve-out.** Rejected. The PRD's criteria sandboxes run in CI and in qa-tester, where no owner is present, and the rules exist to protect real repositories, which the carve-out never touches.
- **All PACKAGE-INTEGRITY logic inside `dashboard/health.py`.** Rejected. `package.py` must run the pristine test before an upgrade and `check` after it, in a host whose package may not carry `dashboard/` yet (the PRD's walking skeleton) and without importing the health registry. One implementation in `package.py` with a delegating registry row keeps ADR-0064 D3's single-implementation property.
- **Deselect `home` tests with `-m "not home"`.** Rejected. Deselection prints no skip reason, so a home-only test would vanish silently in a host. One conftest-driven skip keeps the reason and the node ids.
- **Keep the old root spellings and let each module guess the repository root.** Rejected. That is the re-anchor shadow (D1).
- **A configurable prefix.** Rejected. Q7 concerns host identity (branch, slug, machine paths), while the prefix is the product's own address, identical everywhere by D1. Every prompt command would need a resolver for no host benefit.
- **A separate install-record file.** Rejected. It would be a second parser for per-repository identity, against ADR-0089 D1's single parser.
- **Detect home or host by probing for `decisions/`.** Rejected. Hosts write their own `decisions/` through `/to-prd`. A probe guesses; a recorded key is observed (ADR-0083).

## Propagation

Runtime-surface hits (`grep -rn "ADR-NNNN" .claude/agents/ .claude/skills/ .claude/settings.json`) for each superseded ADR number, measured at `develop` `a455795`. Hit lines per number: ADR-0001 6, ADR-0002 14, ADR-0003 33 (none cites D1, the only clause touched) and ADR-0042 2. `.claude/settings.json` has zero hits for all four. Every listed file also moves under D1; the move does not change its disposition. Line numbers drift as PRs land, so each entry quotes or names what it cites, and the committing slice re-greps at its own BASE.

- **ADR-0001:**
  - `adr-critic.md` (:14 "historical defect", :96/:98/:248 D3, :242 the grandfathered set): **grandfather-with-reason.** D3 is not superseded, and the rest is history.
  - `reviewer.md` (:241 D8, the supersession safety valve): **grandfather-with-reason.** D8 stands (Left standing).
- **ADR-0002:**
  - `reviewer.md` (:490, the post-verdict merge paragraph naming `gh pr merge --squash --auto`; :544, the tool-boundaries line "ONLY `--squash --auto` … never `--merge`"): **update-in-this-wave.** Each gains "(`--merge --auto` for a `pipeline-upgrade` PR, ADR-0092 D4)".
  - `ship/SKILL.md` (:362, "reviewer takes over per ADR-0002 (auto-merge on APPROVE via `python tools/pipe/pr-merge <PR>`, internally `gh pr merge --squash --auto`"): **update-in-this-wave**, the same change.
  - `reviewer.md` (:15, :190 D9, :301), `implementer.md` (:10, :176), `codebase-critic.md` (:10, :316, :395), `ship/SKILL.md` (:358, :499) and `adr-critic.md` (:82, a hypothetical example): **grandfather-with-reason.** Each cites APPROVE-gates-merge, the reviewer as sole merge gate, the direct-commit ban, or the rule set. All stand.
- **ADR-0003:** no runtime file cites D1, the only clause touched, so each file below is **grandfather-with-reason.** Each cites a decision that stands (D2, D3, D4, D6, D7 or D8), names the ADR generically, or uses it as a historical example.
  - `adr-critic.md` (:10 D2, :14 history, :76 example, :96 history, :124 history, :140 D4, :255 D2)
  - `backlog-critic.md` (:136 D2)
  - `codebase-critic.md` (:396 D2)
  - `implementer.md` (:105 D8, :176 D2/D4/D8)
  - `prd-critic.md` (:10 D2, :75 D4, :278 D2)
  - `qa-tester.md` (:581 D4)
  - `reviewer.md` (:526, :545, :546 D4)
  - `slicer-critic.md` (:294 D3)
  - `slicer.md` (:137 D3)
  - `qa-plan/SKILL.md` (:61, :64 D4)
  - `ship/SKILL.md` (:306 D8, :340 D3, :491 D2/D4/D7/D8, :512 D8)
  - `to-issues/SKILL.md` (:8 D2/D6, :18 D4, :76 D4, :82 D2/D6)
  - `to-prd/SKILL.md` (:16 D8, :30 D8, :91 D2/D6/D8)
- **ADR-0042:**
  - `codebase-critic.md` (:398 D1): **grandfather-with-reason.** D1 is extended, not superseded.
  - `reviewer.md` (:3, unchanged; the description naming `gh pr merge --squash --auto` per ADR-0042 D3): **update-in-this-wave.** It gains "(merge-commit mode for the `pipeline-upgrade` lane, ADR-0092 D4)". The R-UPGRADE section is added in the same wave.

**Beyond ADR-number cites: every prompt line that states the merge mode** (`grep -n -i squash .claude/agents/*.md .claude/skills/*/SKILL.md`, 9 lines at a455795):
- `reviewer.md` :19 (the run context, "`pr-merge <PR>`'s underlying `gh pr merge --squash --auto` call") and `ship/SKILL.md` :357 (the implementer-isolation paragraph naming the squash merge): **update-in-this-wave**, the same change as `reviewer.md` :490.
- `ship/SKILL.md` :364 ("the suspect set = squash commits since the last `develop_green` event"): **update-in-this-wave.** It becomes "the merged commits since …", since an upgrade merge commit is a suspect too.
- `reviewer.md` :514 (the multiple-APPROVE serialization paragraph, "every squash lands on the exact main it was CI-tested against"): **grandfather-with-reason.** It describes the serialization of squash merges (ADR-0062 D2), which stands, and upgrade merges serialize the same way.
- `adr-critic.md` :82: covered above.
- The four remaining lines are the ADR-cited lines above (`reviewer.md` :3, :490, :544; `ship/SKILL.md` :362).

**Code that assumes squash merges:** `dashboard/health.py` `check_test_ordering()` (:4854 at a455795) detects a merge-preserving commit by its two parents (its squash-detection comment: "a merge commit with exactly one parent is a squash … a merge-preserving strategy produces two parents") and takes its direct-history path, and upgrade branches are `chore/*`, never fix-type. `tools/record-green.sh` (its "PR-mergeCommit lookup" block) resolves the merged PR by `mergeCommit.oid`, which a merge commit also has. Both are **grandfather-with-reason.**

**Everything the move invalidates** (counts measured at `develop` `a455795` with Context's regex; re-measured at BASE when the committing slice lands). Each file is **update-in-this-wave**; the spelling follows D1's map.

- **Prompt surface:**
  - agents: `reviewer.md` 39, `qa-tester.md` 21, `codebase-critic.md` 17, `implementer.md` 13, `prd-critic.md` 5, `adr-critic.md` 4, `backlog-critic.md` 3, `slicer.md` 2, `slicer-critic.md` 1;
  - skills: `ship` 33, `to-issues` 5, `to-prd` 4, `qa-plan` 2, `promote-to-backlog` 1.
  - `qa-tester.md`'s route globs (hook-fire route `.claude/hooks/**`, command-run route `dashboard/**` under ADR-0088 D5) map to the package paths.
  - `reviewer.md`'s R-LOC section is rewritten (D2), its R-DOCS-CURRENT gains its `home` scope (D5), and R-UPGRADE is added (D4).
  - Relative links to `decisions/` gain one `../` per level the move adds.
- **Hooks:**
  - `log-tool-event.sh` 3, `pre-tool-bash-classify.py` 5, `pre-tool-bash.sh` 7, `session-start.sh` 5 (including its two `../../tools` climbs, `_SS_TOOLS_DIR` and `HANDSHAKE_SH`, D1), `user-prompt-submit.sh` 2. `pre-tool-bash-classify.py`'s `_PIPELINE_CONFIG_PY` climb becomes `../tools/` too (D1);
  - `.claude/settings.json` 7. Its entries are regenerated from `hooks/settings-hooks.json`.
  - The PIP-017 deny patterns are re-verified to match `.claude/pipeline/tools/promote.sh`.
- **Git hooks:** `install.sh` 16, `pre-commit` 12, `commit-msg` 2. `pre-commit` gains D5's scope table, so README regeneration and the secrets scan run home-only.
- **`tools/`:**
  - `ci-checks.sh` 120. It gains the scope table, CHECK 3's squash-shape exemption, the 17 retargeted soft-degrade guards (D1's layout arm), CHECK 8(c)'s package-owned count (D5), CHECK 12's package path, and CHECK 30 (30 given the PRD's §4 sequencing, else the next free number at merge; `ci-checks.sh` tops out at CHECK 28 at a455795).
  - `gen_rules.py` 68. Its output targets, `CLAUDE_MD_GLOBAL_IMPORT`, `GENERATED_HEADER` and the per-scope header text, `SCOPE_PATHS` (`hooks` → `.claude/pipeline/hooks/**, .claude/settings.json`; `isolation` → `.claude/worktrees/**, .claude/pipeline/tools/worktree-guard.sh`; `slicing` → `.claude/pipeline/agents/slicer*.md, .claude/pipeline/skills/*/SKILL.md`), the root binding (`_resolve_repo_root()`, D1), the host-mode refusal (D5), PIP-035..037, the re-rendered statements and the baseline all change. The rendered `generated/_global.md` is not placed under `.claude/rules/`.
  - `gen_repo_map.py` 34 (and `_resolve_repo_root()`, D1; host-mode refusal, D5; the rendered map's paths and link targets take the package spelling); `deploy-handshake.sh` 27 (the expected `core.hooksPath` becomes `.claude/pipeline/githooks`); `pipe/dispatch` 13; `pipe/pr-merge` 14 (plus D4's three legs); `record-green.sh` 13; `check-verdict-presence.py` 10; `pipe/record-green` 10; `ci-failure-kind.sh` 9; `portability-allowlist.txt` 9; `repair-topology.md` 9; `pipe/prd-close` 7; `promote.sh` 7; `trace.py` 4; `check-slicer-provenance.py` 3; `pipe/pr-open` 4; `pipe/qa-verify` 3; `secrets-allowlist.txt` 3; `README.md` 4 (its OpenAI adapter paragraph included); `worktree-guard.sh` 5; `release.py` 6; `pipeline_config.py` 5; the OpenAI adapter's `openai_workflow.py` 8 and `gen_openai_skills.py` 3 (see the adapter entry below).
  - `pipeline_config.py` gains `package_source`, `mode`, `repo-root` and `package-root`. It and `tools/release.py` (ADR-0089 D1, ADR-0090 D3) move under D1 like every other file in the directory; neither derives a root from `__file__`.
- **`dashboard/`:** `health.py` 118 (META-TRIPWIRE's guardrail set per D2, the delegating PACKAGE-INTEGRITY row, `_REQUIRED_LABELS`, `_HEALTH_REPO_ROOT`, and its blessed `sys.path` insertion, which stays), `tracestore.py` 19 (and `_REPO_ROOT`), `pipeline_spec.py` 18, `README.md` 16, `readme_gen.py` 7 (`_READMEGEN_REPO_ROOT` and `_resolve_invoking_repo_root()`; host-mode refusal), `discovery.py` 6 (`_DISCOVERY_REPO_ROOT`), `_constants.py` 4, `collector.py` 4 (`_REPO_ROOT`), `telemetry_root.py` 3 (`_TELEMETRY_CODE_ROOT`), `_gitfiles.py` 1, `comparison.py` 1, `gh_cache.py` 1.
- **`tests/`:** 790 lines across the 131 test files at a455795. The conftest roots (D5) replace every self-located root, in both spellings (`Path(__file__)…parent.parent`, and `os.path` as in `test_promote_ack_880.py`'s `REPO_ROOT` and the five `_TESTS_DIR` files). `test_health_grouping_931.py` and `test_purpose_groups_968.py` reach `dashboard/` through `..`, and `test_no_unguarded_pytest_import_985.py` lists its own directory; all three take `PKG_ROOT` instead, so no test but `conftest.py` references `__file__`. The 12 files that pass a `-c` argument to a subprocess (`git grep -l '"-c"' -- 'tests/test_*.py'`) are migrated by hand, not by `sed`, because some build product paths inside those strings. Tests of home-only material take the `home` marker.
- **Root and home files:**
  - `bootstrap.sh` 26: it moves, sets `core.hooksPath` to `.claude/pipeline/githooks`, runs the chmod on `hooks/`, and its `LABELS` gains `pipeline-upgrade` (absent at a455795).
  - `README.template.md` 23 (unchanged): it is rewritten from clone-as-template to the install model, then `README.md` is regenerated.
  - `CLAUDE.md` 31: it is split, with the pipeline content going to `.claude/pipeline/CLAUDE.md` and home content plus the import line staying.
  - `.github/workflows/ci.yml` 3 (unchanged): it becomes the generated shim.
  - `.gitignore`: it gains the marked block.
  - `docs/observability.md` 6: it moves.
- **The OpenAI adapter (ADR-0086; extended by D1).** Each is **update-in-this-wave**, in the slice that moves the root a path names (PRD §5):
  - `tools/openai_workflow.py` 8: `GLOBAL_SOURCES` (adding `.claude/pipeline/CLAUDE.md`), the area-rule source in `instructions()`, the `_global.md` header check, `ROUTE_SOURCE`, `SCOPE_ADDITIONS` and the two verb paths in `delegate()`; its `sys.path` insertion stays (D1).
  - `tools/gen_openai_skills.py` 3: `render()`'s source and the router's link target become `.claude/pipeline/skills/<n>/SKILL.md`.
  - `tools/workflow_branch.py` 0: it moves, nothing else.
  - `AGENTS.md` 3 and `.agents/skills/ship/SKILL.md` 3 (regenerated by `gen_openai_skills.py`): the files they tell Codex to read and the commands they name.
  - `docs/openai-workflow.md` 20: paths.
  - `tests/test_openai_skills.py` 5 and `tests/test_openai_workflow.py` 23: expectations and fixtures follow, and both take the `home` marker (D5).
  - The prompt lines that point OpenAI runs at these files (`implementer.md`, `qa-tester.md`, `reviewer.md`, `ship/SKILL.md`) and the root `CLAUDE.md`'s OpenAI paragraph are counted above and follow the same map; the package `CLAUDE.md` re-bases their links to the root files.
- **Topics affected** (ADR-0032's advisory topic flags): CLAUDE.md §3's PR-tier line gains the upgrade PR beside I3; the glossary's R-CLOSES entry gains "upgrade PRs are judged by R-UPGRADE", and its lane-PR entry gains the line that an upgrade PR (`pipeline-upgrade`) is not an ADR-0090 lane PR (`lane`); `README.template.md`'s install and bootstrap sections; `AGENTS.md` and `docs/openai-workflow.md` (the files an OpenAI run reads).
- **`decisions/README.md`:** the 0092 row, plus supersession annotations on the rows for 0001, 0002, 0003 and 0042.
- **`tools/gen_rules.py` rule texts:**
  - **PIP-035** → "The pipeline product lives only under `.claude/pipeline/` in the home repository and in every host; `decisions/`, `docs/decision-log/`, `qa-proof/` and the README stay outside it. Claude Code, GitHub and git reach it only through generated shims (`.claude/skills/<n>/`, `.claude/agents/pipeline/`, `.claude/rules/pipeline/`, package-owned `.claude/settings.json` hook entries, `.github/workflows/ci.yml`, a marked `.gitignore` block) plus one `@.claude/pipeline/CLAUDE.md` line in the root CLAUDE.md. Shims are never hand-edited. The repository and package roots that locate files come only from `tools/pipeline_config.py`, never from a file's own location; a file may still reach its own package siblings from its own location, including a `sys.path` entry for `from tools import …`. CI CHECK 30 (or the next free CHECK number at merge; PACKAGE-INTEGRITY) fails on a stale or colliding shim, a missing import line, a product file outside the prefix or home material inside it, a prompt or rendered-rule command path missing the prefix, a CI guard path that does not exist, a root derived from self-location, or a literal own-location climb that names no tracked path (ADR-0092 D1/D2)."
  - **PIP-036** → "A pipeline version is `VERSION` (`X.Y.Z`) plus an annotated tag `vX.Y.Z` on the `git subtree split --prefix=.claude/pipeline` commit; milestone `v<major>.<minor>[.<patch>]` names tag `v<major>.<minor>.<patch or 0>`. Outside throwaway verification repositories the tag is created only by the owner-run `package.py publish` (advisory). Hosts install with `install.sh` (`git subtree add --squash`) and upgrade with `package.py upgrade vX.Y.Z` (`git subtree pull --squash`, then the new version's shim refresh), which never writes the host's CLAUDE.md. A squash-shape commit carries `git-subtree-dir: .claude/pipeline` and a 40-hex `git-subtree-split:` line, has its split's tree, and has no parent or one squash-shape parent; a host's package tree must equal the tree of its newest subtree-trailer commit, which must be squash-shape. An upgrade lands as a `pipeline-upgrade` PR (an upgrade PR, not an ADR-0090 lane PR) judged by the reviewer's R-UPGRADE rubric alone and merged by `tools/pipe/pr-merge` with a merge commit, never a squash, which then closes its issue; the squash-shape commit is the only commit exempt from the Conventional Commits subject format and the Co-Authored-By trailer (ADR-0092 D3/D4)."
  - **PIP-037** → "Every CI check and pre-commit step declares where it runs (`home`, `host` or `both`), selected by `pipeline_config.py mode` (host iff `.claude/pipeline.conf` names a `package_source`); a check outside its scope prints `N/A (<scope>-only)`, never PASS. A check whose subject both a host and the package write scores only the package-owned part (tested for CHECK 8; advisory for checks added later). `gen_rules.py`, `gen_repo_map.py` and `readme_gen.py` refuse in host mode. In hosts, tests marked `home` are skipped with reason `home-only`. A completeness test fails on an unscoped check or an unmarked home-reading test, and the self-install test fails when host-mode CI is not green on a fresh install of the working tree's package (ADR-0092 D5)."
  - **Re-rendered at package paths**, each in the slice that moves the root it names, with "(paths per ADR-0092 D1)" appended before its closing citation and no other change. The list is the fifteen statements the a455795 token scan finds (Left standing); the committing slice adds any other statement that names a moved path at its own BASE:
    - CRI-004: `dashboard/fixtures/` → `.claude/pipeline/dashboard/fixtures/`;
    - PIP-014: `tools/pipe/{…}` and `tools/promote.sh` → `.claude/pipeline/tools/pipe/{…}` and `.claude/pipeline/tools/promote.sh`;
    - PIP-015 and PIP-024: `tools/trace.py` (and PIP-024's `tools/pipe/batch-plan`) → under `.claude/pipeline/tools/`;
    - PIP-016: `tools/pipe/pr-merge` → `.claude/pipeline/tools/pipe/pr-merge`;
    - PIP-017: `tools/promote.sh` → `.claude/pipeline/tools/promote.sh`;
    - PIP-023: `bash tools/ci-checks.sh` → `bash .claude/pipeline/tools/ci-checks.sh`;
    - PIP-031: `tools/`, `dashboard/`, `.claude/hooks/`, `.githooks/`, `bootstrap.sh` and `tools/pipeline_config.py` → their `.claude/pipeline/` equivalents;
    - PIP-032: `dashboard/_constants.py` → `.claude/pipeline/dashboard/_constants.py`;
    - PIP-033: `tools/release.py freeze` → `.claude/pipeline/tools/release.py freeze`;
    - PIP-034: `tools/pipe/dispatch`, `tools/pipe/pr-merge` and `tools/release.py` → their `.claude/pipeline/tools/` equivalents (`fix/<n>-lane-<slug>` is a branch-name pattern, not a path, and is unaffected);
    - REG-001: `tools/ci-checks.sh` and `pytest tests/` → `.claude/pipeline/tools/ci-checks.sh` and `pytest .claude/pipeline/tests/`;
    - REG-003: `tests/quarantine.txt` → `.claude/pipeline/tests/quarantine.txt`;
    - VER-011: `tools/portability-allowlist.txt` → `.claude/pipeline/tools/portability-allowlist.txt`;
    - DOC-001: `python3 dashboard/readme_gen.py` → `python3 .claude/pipeline/dashboard/readme_gen.py`.
  - `RULE_IDS_BASELINE` → the live baseline at merge + 3, with the breakdown PIP(n) → PIP(n+3) and the baseline comment block. If PIP-035..037 are taken at BASE, the next three free ids are used, and the rule stays "live baseline at merge + 3". Re-rendered statements change no count.
  - Regenerate `.claude/pipeline/generated/_global.md` and the area rules, then run `refresh`.
- **Grandfathered as history:** every `decisions/`, `qa-proof/` and `docs/decision-log/` mention of a pre-move path.

## Open questions deferred

- **Double load of the package `CLAUDE.md`.** A subdirectory `CLAUDE.md` also loads on demand once Claude reads a file under `.claude/pipeline/`. The import may therefore load it twice, the same class as the slice #945 `_global.md` lesson. The filename is owner-settled.
  - **Measure** with an `InstructionsLoaded` hook during the golden run.
  - **If it double-loads,** the options are a `claudeMdExcludes` entry or a rename in v1.1.
- **Host ADR numbers against home citations.** Package prompts cite home ADRs by number (for example adr-critic's AC-BOOTSTRAP-MODE asks drafts to cite ADR-0004 D2). A host's own `decisions/` starts its own numbering, so the same number can name an unrelated host ADR, and a host critic may report a cited home ADR as "not present". The options are a reserved host number range, a citation form that names the home repository, or critic rubrics that treat a package-prompt cite as provenance. It goes with the v1.1 work on a host's own `decisions/`.
- **Migrating to a plugin** (Alternatives C2), if its revisit trigger fires.
- **v1.1:**
  - host hygiene scanning (secrets, portability) with a host-owned allowlist;
  - index and link checks, and rendered rules, for a host's own `decisions/`;
  - installing onto an existing host with a protected default branch (D381);
  - a "newer version available" line in status (plan Block 5);
  - the Codex host (#1436), including delivery of `AGENTS.md`, `.agents/skills/` and `docs/openai-workflow.md` to hosts.
- **Whether the package should carry a copy of `LICENSE`.** The owner decides the licensing of a split package.

## References

- **Owner decisions:** grill 2026-09-23 Q3 (installable and upgradeable in v1.0), Q6 (git subtree), Q7 (universal); plan "Cílový tvar balíčku", Block 6, verification row 6 and the golden test; D381 (no WEDLY port yet).
- **Superseded:** ADR-0001 D1 (in full); ADR-0001 D12, ADR-0002 D9-revised, ADR-0003 D1 and ADR-0042 D3 (each in part).
- **Extended:** ADR-0089 D1, ADR-0090 D1, ADR-0086 D1, ADR-0076 D3, ADR-0064 D3, ADR-0015 D1, ADR-0073 D1–D3, ADR-0070 D4, ADR-0083 D3, ADR-0042 D1, ADR-0067 D1, ADR-0084 D1/D2, ADR-0034 D4–D6, ADR-0088 D3, ADR-0004 D2.
- **Also cited:** ADR-0001 D8, ADR-0003 D8, ADR-0004 D3, ADR-0008 D6, ADR-0023 D6/D7, ADR-0034 D5, ADR-0040 D1/D2, ADR-0054 D3, ADR-0062 D2, ADR-0067 D4, ADR-0070 D1–D3, ADR-0075 D5, ADR-0076 D1/D2/D4, ADR-0077 D1/D2, ADR-0079 D1, ADR-0080 D2, ADR-0086 D2–D6, ADR-0088 D2/D4/D5, ADR-0089 D2, ADR-0090 D2–D5.
- **Evidence:** the git-subtree experiment in Context (git 2.54.0.windows.1, 2026-09-23; add plus two pulls, a no-local clone, and two simulated squash-merges); `git-core/git-subtree`'s `new_squash_commit` and `cmd_merge`.
- **Platform docs** (verified 2026-09-23): the URLs in the Context table.
- **Issues and PRs:** PRDs #1496, #1500, #1501 (land first); #1436 and #1441–#1443 (Codex host, v1.1); PR #1476 (ADR-0086, merged at a455795); #1505 (PROVISIONAL semantics).
- **Verification note for reviewers:** every `ADR-NNNN D<n>` this ADR cites was opened on `origin/develop` at `a455795`, including ADR-0086 D1–D6 (landed with #1476), ADR-0089 D1–D4 and ADR-0090 D1–D5, and each cited heading exists and matches its characterization here.
