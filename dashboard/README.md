# project-claude pipeline tooling

CLI-only home for the project's control-plane tooling: health checks, the
trace read-model, the README generator, and PRD-run comparison. There is no
served UI and no process to start — see [`docs/observability.md`](../docs/observability.md)
for the append-only-log observability surface that replaced the served
dashboard (ADR-0088).

## Modules

| Module | Responsibility |
|---|---|
| `_constants.py` | Single-sourced project-wide constants: the closed `KNOWN_CRITICS` roster (ADR-0088 D4). Imports nothing, so no sibling module can ever form an import cycle through it. |
| `discovery.py` | Skill/agent/hook/ADR filesystem discovery — powers the repo-map generator, the README generator, and the health registry's structural checks. |
| `health.py` | `check_docs1`–`check_docs11` docs-currency checks + STRUCT-1..10 structure checks (formerly `/audit-meta`, absorbed PRD #919 slice #920) + AS-AUDIT aggregate subagent-prompt check (formerly `/audit-subagents`, registered PRD #919 slice #921) + substrate checks (`check_capture_slo`, `check_hook_integrity`, `check_isolation_group`, `check_rule_coverage`, `check_critic_health`, `check_spec_coverage`) + verification-integrity checks (`check_blind_dispatch_rate`, `check_residual_ratio`, `check_proof_presence`, `check_merge_integrity`, `check_capture_shape`, `check_green_main`, `check_silent_drift`, `check_query_honesty` — REST-attested canary gating every `--label`-filtered `gh` call through the seam, ADR-0087 D2) + registry-integrity check (`check_parity`) + two-tier promotion checks (`check_release_ready`, `check_branch_topology`) + hygiene/session-start checks (`check_untracked_size`, `check_log_rotation`, `check_stale_branches`, `check_required_labels`, `check_session_injection`, `check_r_sensitive_detector`) + liveness/integrity checks (`check_promotion_lag`, `check_hook_liveness`, `check_proof_integrity`, `check_meta_tripwire`), TTL-cached. **CLI:** `python dashboard/health.py --check <ID>` runs a single check headlessly (exit 0 = PASS/WARN, exit 1 = FAIL, exit 2 = unknown ID); `python dashboard/health.py --list` prints all registered IDs. Per ADR-0064 D3. |
| `readme_gen.py` | README regeneration logic. **CLI:** `python3 dashboard/readme_gen.py` reads `README.template.md` + filesystem and writes `README.md` — no flag (entrypoint relocated here by ADR-0088 D3). |
| `pipeline_spec.py` | Pipeline topology spec (SPEC v2 nodes + edges) consumed by the README's generated mermaid diagram. |
| `gh_cache.py` | Shared in-memory TTL+timeout wrapper for all `gh` CLI calls (PRD #993): `gh_fetch(args, *, ttl, timeout)` runs `gh` via `subprocess.run(timeout=...)`, caches stdout by normalized command key, degrades to last-known "stale" value (or "computing" sentinel) on timeout/failure. Thread-safe; `GhResult` carries `fetched_at`+`source` for honest "as of" display. Timeout default 5s; TTL per-call-site. Prevents any single slow `gh` call from blocking the caller. Its `GhResult.source` labels are correct as-is — provenance is enforced one layer up, at `health.py`'s `_health_gh_fetch()` seam, not here (ADR-0087 D1/D3: `gh_cache.py` itself is unchanged). |
| `_gitfiles.py` | Git-tree file enumeration: `git_ls_files()` lists tracked files via `git ls-files` so discovery functions use the git index rather than `os.walk` or `glob`, avoiding false positives from untracked/generated files and working correctly in worktree-isolated sessions. Used by `discovery.py` and `health.py`. |
| `collector.py` | PRD-run artifact collection from GitHub API; `--compare` golden-run mode. |
| `comparison.py` | Run-vs-spec edge comparison, `run_pass` verdict, downloadable JSON report; violation detectors include `merged_without_ci` (non-trivial PR merged without SUCCESS `ci` statusCheckRollup — bootstrap-mode: PRs predating ADR-0042 are grandfathered); failed/not-found collection returns `run_pass: false` plus an explicit `error` (and `not_found: true`) field — never a vacuous PASS. |
| `tracestore.py` | Trace store SQLite read-model (PRD #1075 slice #1080, hardened slice #1082/#1101): folds the canonical `tools/trace.py` v3 span log (`trace-v3.jsonl`) into a disposable, gitignored SQLite read-model (`.claude/state/trace.db`) via `fold()` — the ONLY write path (refoldable-from-log, never a second source of truth, per ADR-0075 D2); `fold()` builds each generation into a scratch table and atomically swaps it in under one transaction (WAL + busy_timeout + INSERT OR IGNORE + `ORDER BY ts, rowid` tie-break + size+mtime composite freshness — closes the #1101 concurrency defects); `acid_path(pr)` is an indexed causal-chain query returning the same ordered answer as `tools/trace.py`'s linear scan (kept as the fallback/cross-check); `span_tree(trace_id)` returns one trace's ordered spans; `serve_trace_runs()` / `serve_runboard()` are background-warmed payload builders kept as library shims for their surviving test consumers, with no HTTP caller after ADR-0088 D1's server deletion; `TRACE_DB_OVERRIDE` env test seam. **CLI:** `python dashboard/tracestore.py path --pr <n>` / `fold` / `running` / `runboard`. |
| `telemetry_root.py` | Shared repo-root resolution used by `health.py` and other modules so they compute the correct root across linked worktrees. |
| `events.py` | Workflow-event log reading. No non-test importer remains after ADR-0088 D1's server deletion; slated for removal alongside its own test suite. |
| `workitems.py` | GitHub Issues fetch via `fetch_workitems()`. No non-test importer remains after ADR-0088 D1's server deletion; slated for removal alongside its own test suite. |

## Usage

Every module is invoked directly via its own CLI entrypoint — there is no
server process to start or restart:

```bash
python3 dashboard/health.py --check RELEASE-READY
python3 dashboard/health.py --list
python3 dashboard/tracestore.py runboard
python3 dashboard/readme_gen.py
```

## Configuration

| Variable | Default | Description |
|---|---|---|
| `DASH_REPO_SLUG` | _(derived)_ | Override the runtime-derived GitHub repo slug (`owner/name`). Normally derived automatically via `gh repo view` → `git remote get-url origin` parse. Set this only when both derivation paths fail (e.g. detached HEAD, no `origin` remote). Must be in `owner/name` form. Single github.com origin assumed (multi-remote / GHE out of scope — see PRD #753 §3). |

## Cross-platform notes

- Uses Python 3 stdlib only — no `pip install` required.
- Uses `pathlib` throughout; works on Windows Git Bash, Linux, macOS.

## Intended audience

Solo developer (you), plus the pipeline's own CI and hooks. Observation and
control-plane tooling; advisory unless a specific check is wired into a gate
(e.g. `RELEASE-READY` gates `tools/promote.sh`). The former `/audit-meta`
structure+docs-currency checks run automatically inside the `codebase-critic`
per-PRD pass (PRD #919 slice #920). The former `/audit-subagents`
subagent-prompt quality checks run automatically in CI via CHECK 18
(`python3 dashboard/health.py --check AS-AUDIT`, per PRD #919 slice #921).

## Two-tier delivery model (PRD #836 / ADR-0070 D1)

The project uses a `develop`/`main` two-tier model (wave 5 of workflow v2).
Agents merge slices to `develop`; `main` advances only via the deterministic
promotion gate (`tools/promote.sh` + `RELEASE-READY`). These checks are
queryable via the health check registry (`python dashboard/health.py --check <ID>`):

**Promotion gates** (`RELEASE-READY` + `BRANCH-TOPOLOGY` health-registry checks):
- **RELEASE-READY** — evaluates all six conditions from ADR-0070 D2: (a) CI green on `develop` HEAD; (b) full test suite passes; (c) latest production-verify PASS with route-appropriate proof; (d) green-develop streak intact; (e) zero open `needs-human` items; (f) unpromoted batch touches no guardrail-machinery path. A `verdict="true"` means `tools/promote.sh` may advance `main`. Details report the first failing condition when held. Per ADR-0070 D2 / ADR-0072 D1.
- **BRANCH-TOPOLOGY** — confirms slice PRs target `develop` (not `main`) and that `main` advances only via recorded `promotion` events. Dormant until slice #843 wires full branch-protection. Per ADR-0070 D1 / ADR-0072 D3.

**Promotion event log:** each promotion appends a `{"v":2,"event":"promotion","from":"develop","to":"main","sha":"..."}` event to `.claude/logs/workflow-events.jsonl` (recorded, CLI-queryable).

The sole human-blocking role in this model is acking guardrail-machinery promotions (batches touching `.github/workflows/**`, `.claude/settings.json`, `.claude/hooks/**`, `tools/ci-checks.sh`, `.githooks/**`, `*-critic.md`, or the promotion gate itself). The `R-SENSITIVE-DETECTOR` health row tallies these and their ack status. Per ADR-0070 D4.

## Fixtures

`dashboard/fixtures/` contains sample payloads used by `tools/ci-checks.sh` CHECK 8 to mechanically validate the Agent-hook payload schema. CHECK 8 uses a python3 parser (not jq): it loads the fixture via `json.load()` and asserts that `tool_input.subagent_type` resolves to a non-empty value — proving the python3 path handles the canonical `PostToolUse·Agent` payload correctly. Regenerate `dashboard/fixtures/agent-payload-sample.json` from a real `PostToolUse·Agent` payload if Claude Code's hook schema changes.
