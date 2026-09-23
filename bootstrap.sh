#!/usr/bin/env bash
# bootstrap.sh — fresh-clone setup for project-claude
#
# Purpose:
#   Bring a fresh clone of this repo to a usable state in one command.
#   Run this once after `git clone`. Re-running is safe (idempotent).
#
# Scope (per ADR-0008 D6, slice #60; extended by ADR-0030 D1+D2;
# extended by ADR-0089 D2, slice #1511):
#   1. Sanity: confirm we're inside a git repo + `gh` is authenticated.
#   2. Create the 8 repo-level labels (skip if they already exist).
#   3. Install local git hooks (`git config core.hooksPath .githooks`).
#   4. Detect the GitHub Project v2 board (manual hint if missing).
#   4b. Create the configured integration branch from the release tip, when
#       origin lacks it (ADR-0089 D2; idempotent, never forced).
#   5. Apply branch protection R1+R2+R4 to BOTH configured branches
#      (warn-and-proceed if no admin, or if the release branch is absent).
#   6. python3 presence check (warn-only; required by event logger).
#   7. jq install — idempotent winget/brew/apt (ADR-0030 D1).
#   8. Playwright Python library install (pip install playwright; ADR-0050 D1).
#
# Explicit DEFERRALS (NOT done here):
#   - Matt Pocock skills install                    — user-level concern
#   - MCP server configuration                      — user-level concern
#   - CI / GitHub Actions / bot identity            — deferred to PRD-CI
#   - Branch protection R4 (required status checks)  — payload applied per ADR-0042; owner enables
#   - Branch protection R3 (required approving reviews / non-author review) — deferred to a future bot-identity PRD
#   - `--global` git config changes                 — out of scope
#   - `--check` / dry-run mode                      — YAGNI (ADR-0008 OQ)
#   - GitHub Project v2 board creation              — complex GraphQL; manual
#
# Failure mode:
#   Best-effort. `set -uo pipefail` only — NOT `-e`. Per-step failures
#   warn and continue; the script never aborts on a single-step failure.
#
# See:
#   - decisions/0008-workflow-autolog-bootstrap-and-naming.md (D6)
#   - .githooks/install.sh, .githooks/pre-commit, .githooks/commit-msg
#   - tools/branch-protection-config.example.json (reference shape)

set -uo pipefail

readonly SCRIPT_NAME="bootstrap.sh"
readonly SCRIPT_VERSION="0.1.0 (slice #60)"
readonly TAG="[${SCRIPT_NAME}]"

# Outcome lines accumulated by each step, printed in the final summary.
SUMMARY=()

# ---- helpers --------------------------------------------------------------

log()  { printf '%s %s\n' "$TAG" "$*"; }
warn() { printf '%s WARN: %s\n' "$TAG" "$*" >&2; }
step() { printf '\n%s step %s: %s\n' "$TAG" "$1" "$2"; }
note() { SUMMARY+=("$1"); }

# Resolve <owner>/<repo> from the origin remote. Supports both SSH
# (`git@github.com:owner/repo.git`) and HTTPS (`https://github.com/owner/repo.git`).
# Echoes "owner/repo" on success; non-zero exit on failure.
resolve_origin_slug() {
    local url
    url=$(git remote get-url origin 2>/dev/null) || return 1
    # Strip optional trailing ".git"
    url="${url%.git}"
    # SSH form: git@host:owner/repo
    if [[ "$url" =~ ^git@[^:]+:([^/]+/[^/]+)$ ]]; then
        printf '%s\n' "${BASH_REMATCH[1]}"
        return 0
    fi
    # HTTPS form: https://host/owner/repo  (or http://)
    if [[ "$url" =~ ^https?://[^/]+/([^/]+/[^/]+)$ ]]; then
        printf '%s\n' "${BASH_REMATCH[1]}"
        return 0
    fi
    return 1
}

# ---- step 1: sanity checks ------------------------------------------------

step 1 "sanity checks"

# 1a. We must be inside a git work tree. The script is runnable from any
# subdirectory, but if we aren't in *any* git tree we can't do anything useful.
REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || REPO_ROOT=""
if [[ -z "$REPO_ROOT" ]]; then
    warn "not inside a git repository; cannot proceed with most steps."
    note "⚠ sanity: not inside a git repo — aborting"
    # Print summary and exit early; nothing else will work.
    printf '\n%s ---- summary ----\n' "$TAG"
    for line in "${SUMMARY[@]}"; do printf '%s   %s\n' "$TAG" "$line"; done
    exit 1
fi

CWD_ABS=$(pwd -P 2>/dev/null || pwd)
REPO_ROOT_ABS=$(cd "$REPO_ROOT" && pwd -P 2>/dev/null || pwd)
if [[ "$CWD_ABS" != "$REPO_ROOT_ABS" ]]; then
    warn "not at repo root ($REPO_ROOT_ABS); the script will operate against the repo root anyway."
fi

log "$SCRIPT_NAME $SCRIPT_VERSION — repo root: $REPO_ROOT_ABS"

# 1b. Confirm `gh` is installed AND authenticated. Without it, every
# subsequent step that talks to GitHub will fail.
if ! command -v gh >/dev/null 2>&1; then
    warn "'gh' CLI not found on PATH; GitHub-touching steps will be skipped."
    note "⚠ sanity: gh CLI missing — GitHub steps skipped"
    GH_OK=0
elif ! gh auth status >/dev/null 2>&1; then
    warn "'gh auth status' failed; run 'gh auth login' first."
    note "⚠ sanity: gh not authenticated — GitHub steps skipped"
    GH_OK=0
else
    log "gh CLI present and authenticated."
    note "✓ sanity: git repo + gh authenticated"
    GH_OK=1
fi

# Resolve the origin slug once; reused by label + branch-protection steps.
ORIGIN_SLUG=""
if [[ "$GH_OK" -eq 1 ]]; then
    ORIGIN_SLUG=$(resolve_origin_slug) || ORIGIN_SLUG=""
    if [[ -z "$ORIGIN_SLUG" ]]; then
        warn "could not parse <owner>/<repo> from origin remote URL; some steps may be skipped."
    else
        log "origin slug resolved to: $ORIGIN_SLUG"
    fi
fi

# ---- step 2: create the 7 repo-level labels -------------------------------

step 2 "create repo labels (idempotent)"

# Label spec — keep aligned with CLAUDE.md "Hierarchy — PRD → Slice → PR".
# Format: "<name>|<color-hex>|<description>"
# NOTE: root-cause label already exists on the live repo; entry here is for fresh clones.
LABELS=(
    "prd|a2eeef|Product Requirements Document"
    "slice|0075ca|INVEST-shaped vertical slice of a PRD"
    "backlog|cccccc|Forward-looking work queue item; not yet a PRD"
    "captured|8b949e|Graveyard of backlog-critic rejects per ADR-0008 D1; lazy human review (cull or rescue)"
    "trivial|fbca04|≤10 LoC runtime; I3 trivial-lane PR; reviewer fast-paths"
    "needs-human|d93f0b|Round-3 BLOCK escalation per I5"
    "needs-human-check|e4e669|QA-plan residual queue; cleared by the operator via decide-flow / gh issue list"
    "root-cause|B60205|rule #13 root-cause capture (3-part shape per ADR-0063)"
    "bug|d73a4a|Class label: breaks the system's own promise, doc drift included (ADR-0090 D1)"
    "feature|1d76db|Class label: anything that is not a bug (ADR-0090 D1)"
    "lane|5319e7|Release-mode file-lane PR closing bug issues only (ADR-0090 D3/D4)"
)

create_label() {
    local name="$1" color="$2" desc="$3"
    # `gh label list` output begins with the label name in column 1, tab-separated.
    # We grep with a tab anchor to avoid prefix-collisions like `prd` vs `prd-foo`.
    if gh label list --repo "$ORIGIN_SLUG" --limit 200 2>/dev/null | grep -q "^${name}"$'\t'; then
        log "label '$name' already exists; skipping."
        return 0
    fi
    if gh label create "$name" --color "$color" --description "$desc" --repo "$ORIGIN_SLUG" >/dev/null 2>&1; then
        log "label '$name' created."
        return 0
    fi
    warn "failed to create label '$name'."
    return 1
}

if [[ "$GH_OK" -eq 1 && -n "$ORIGIN_SLUG" ]]; then
    created=0
    skipped=0
    failed=0
    for spec in "${LABELS[@]}"; do
        IFS='|' read -r name color desc <<<"$spec"
        # We re-grep per-label to keep idempotency check tight; the per-call
        # cost (one `gh label list` per label) is acceptable for 7 labels.
        before=$(gh label list --repo "$ORIGIN_SLUG" --limit 200 2>/dev/null | grep -c "^${name}"$'\t' || true)
        if create_label "$name" "$color" "$desc"; then
            if [[ "$before" -eq 0 ]]; then created=$((created+1)); else skipped=$((skipped+1)); fi
        else
            failed=$((failed+1))
        fi
    done
    note "✓ labels: ${created} created, ${skipped} already existed, ${failed} failed"
else
    warn "skipping label creation (gh not ready or origin slug unresolved)."
    note "⚠ labels: skipped (gh not ready)"
fi

# ---- step 3: install git hooks --------------------------------------------

step 3 "install git hooks (core.hooksPath = .githooks)"

# Setting core.hooksPath is itself idempotent — setting it to the same value
# is a no-op. We compare before/after to report an honest "already done" vs
# "newly set" outcome in the summary.
HOOKS_BEFORE=$(git -C "$REPO_ROOT" config --local --get core.hooksPath 2>/dev/null || echo "")
if git -C "$REPO_ROOT" config --local core.hooksPath .githooks 2>/dev/null; then
    if [[ "$HOOKS_BEFORE" == ".githooks" ]]; then
        log "core.hooksPath was already '.githooks'; no change."
        note "✓ git hooks: already configured"
    else
        log "core.hooksPath set to '.githooks' (was: '${HOOKS_BEFORE:-<unset>}')."
        note "✓ git hooks: configured (.githooks)"
    fi
else
    warn "failed to set core.hooksPath."
    note "⚠ git hooks: failed to configure"
fi

# Best-effort: ensure the hook scripts are executable. On Windows filesystems
# the bit is ignored, but chmod still succeeds; on POSIX it actually matters.
# We silently skip any file that doesn't exist (e.g., partial clone).
for f in "$REPO_ROOT/.githooks/pre-commit" "$REPO_ROOT/.githooks/install.sh" "$REPO_ROOT/.githooks/commit-msg"; do
    if [[ -f "$f" ]]; then
        chmod +x "$f" 2>/dev/null || warn "could not chmod +x '$f' (likely Windows filesystem; git index bit still applies)."
    fi
done

# Same idempotent chmod for Claude Code hook scripts under .claude/hooks/
# (per ADR-0023 D7). Glob expands to nothing if the directory is missing on a
# partial clone; the `|| true` keeps the script best-effort.
[ -d "$REPO_ROOT/.claude/hooks" ] && chmod +x "$REPO_ROOT"/.claude/hooks/*.sh 2>/dev/null || true

# ---- step 4: project board v2 (detect only; manual create if missing) -----

step 4 "GitHub Project v2 board (detect only)"

# Honest scope: creating a v2 project board requires GraphQL mutations
# (projectV2Create, then adding fields, then creating single-select options
# for each column). That's complex enough to deserve its own slice. For now
# we only detect existence and print a manual-setup hint if missing.
#
# Owner is derived from origin slug; we list projects owned by that user/org
# and treat "any project exists" as "good enough" — the columns Backlog /
# Captured / Todo / In Progress / Done are assumed configured per ADR-0008 D6.
if [[ "$GH_OK" -eq 1 && -n "$ORIGIN_SLUG" ]]; then
    OWNER="${ORIGIN_SLUG%%/*}"
    # `gh project list` requires the `project` scope on the token. If the
    # token lacks it, the command fails — that's a warn, not an error.
    if gh project list --owner "$OWNER" --limit 50 >/dev/null 2>&1; then
        proj_count=$(gh project list --owner "$OWNER" --limit 50 --format json 2>/dev/null \
            | grep -o '"number"' | wc -l | tr -d ' ')
        if [[ "$proj_count" -gt 0 ]]; then
            log "found $proj_count project(s) owned by '$OWNER'."
            log "✓ project board exists; columns assumed configured per ADR-0008 D6."
            note "✓ project board: detected ($proj_count owned by $OWNER)"
        else
            warn "no project boards found for owner '$OWNER'."
            warn "manual setup required — create a v2 project with columns:"
            warn "    Backlog, Captured, Todo, In Progress, Done"
            warn "see ADR-0008 D6. Suggested command:"
            warn "    gh project create --owner $OWNER --title 'project-claude pipeline'"
            note "⚠ project board: none found — manual setup required"
        fi
    else
        warn "'gh project list' failed (token may lack 'project' scope or org permission)."
        warn "skipping project board check. To grant the scope: gh auth refresh -s project,read:project"
        note "⚠ project board: detection skipped (missing 'project' scope)"
    fi
else
    warn "skipping project board check (gh not ready)."
    note "⚠ project board: skipped (gh not ready)"
fi

# ---- step 4b: create the integration branch from the release tip ---------

step "4b" "create integration branch from release tip (idempotent)"

# ADR-0089 D2: a repo built from this template starts with only its default
# (release) branch. Before applying protection (next step), ensure the
# configured integration branch exists on origin, created at the release
# branch's tip. Non-destructive: a plain (never forced) ref-to-ref push, so
# it can never overwrite existing history. Skips — never invents a base —
# when the release branch itself is absent from origin.
#
# Located relative to THIS script's own path (S1-c / ADR-0089), never via
# `$REPO_ROOT/tools/...` of the cwd repo.
_BOOTSTRAP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INTEGRATION_BRANCH=$(python3 "$_BOOTSTRAP_DIR/tools/pipeline_config.py" integration 2>/dev/null) || INTEGRATION_BRANCH=""
RELEASE_BRANCH=$(python3 "$_BOOTSTRAP_DIR/tools/pipeline_config.py" release 2>/dev/null) || RELEASE_BRANCH=""

if [[ -z "$INTEGRATION_BRANCH" || -z "$RELEASE_BRANCH" ]]; then
    warn "could not resolve branch roles (tools/pipeline_config.py failed); skipping integration-branch creation."
    note "⚠ integration branch: skipped (role resolution failed)"
elif git ls-remote --exit-code --heads origin "$INTEGRATION_BRANCH" >/dev/null 2>&1; then
    log "integration branch '$INTEGRATION_BRANCH' already exists on origin; skipping."
    note "✓ integration branch: already exists ($INTEGRATION_BRANCH)"
elif ! git ls-remote --exit-code --heads origin "$RELEASE_BRANCH" >/dev/null 2>&1; then
    warn "release branch '$RELEASE_BRANCH' not found on origin; cannot create '$INTEGRATION_BRANCH' from it. skipping."
    note "⚠ integration branch: skipped (release branch '$RELEASE_BRANCH' absent)"
else
    git fetch origin "$RELEASE_BRANCH" >/dev/null 2>&1 || true
    if git push origin "refs/remotes/origin/${RELEASE_BRANCH}:refs/heads/${INTEGRATION_BRANCH}" 2>/dev/null; then
        log "created integration branch '$INTEGRATION_BRANCH' at the tip of '$RELEASE_BRANCH'."
        note "✓ integration branch: created ($INTEGRATION_BRANCH from $RELEASE_BRANCH)"
    else
        warn "failed to create integration branch '$INTEGRATION_BRANCH' from '$RELEASE_BRANCH'."
        note "⚠ integration branch: creation failed"
    fi
fi

# ---- step 5: branch protection R1+R2+R4 on both configured branches ------

step 5 "branch protection R1+R2+R4 on both configured branches"

# R1 = require PR (required_pull_request_reviews block present, count=0).
# R2 = no force-push, no deletion (allow_force_pushes=false, allow_deletions=false).
# R4 (required status checks) — payload is included here but NOT enabled
# mid-PRD. See comment below.
#
# This call requires admin permission on the repo. Fork contributors and
# non-admin collaborators will get 403 — that's the canonical warn-and-proceed
# case. We discard stderr so the contributor isn't scared by a raw API error;
# the summary line tells them what happened.
#
# R4 (required status checks) — enable is OWNER-RUN after this PRD's slices
# merge AND the CI workflow has produced a named check run on the integration
# branch. Do NOT enable mid-PRD: it would block this PRD's own merges.
# The context "ci" matches the GitHub Actions job name in
# .github/workflows/ci.yml (ADR-0042 D2).
#
# ADR-0089 D2: the SAME payload is applied to BOTH configured branches — the
# integration branch (this repo's live shape, unchanged) and the release
# branch (newly realizing ADR-0075 D5 gate (1) on every host that runs
# bootstrap). enforce_admins stays false on both, so tools/promote.sh's admin
# fast-forward keeps working.
BP_BODY='{
  "required_status_checks": { "strict": true, "checks": [ { "context": "ci" } ] },
  "enforce_admins": false,
  "required_pull_request_reviews": {
    "required_approving_review_count": 0,
    "dismiss_stale_reviews": false
  },
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false
}'

# put_branch_protection <branch>
#   PUTs BP_BODY to <branch>'s protection endpoint; warns and notes the
#   outcome. Endpoint is deliberately slash-less: a leading "/repos/..."
#   gets rewritten to "C:/Program Files/Git/repos/..." by MSYS path
#   conversion on Windows Git Bash, yielding "invalid API endpoint"
#   (ADR-0030 hardening class). gh treats the slash-less form identically
#   on every platform.
put_branch_protection() {
    local branch="$1"
    local err rc
    err=$(printf '%s' "$BP_BODY" \
        | gh api -X PUT "repos/${ORIGIN_SLUG}/branches/${branch}/protection" --input - 2>&1 >/dev/null)
    rc=$?
    if [[ "$rc" -eq 0 ]]; then
        log "branch protection applied to '$branch' (R1+R2+R4)."
        note "✓ branch protection: R1+R2+R4 applied to $branch"
    elif printf '%s' "$err" | grep -qi "upgrade to github pro"; then
        warn "branch protection unavailable for '$branch': private repos need GitHub Pro (or make the repo public); skipping."
        note "⚠ branch protection ($branch): skipped (plan does not cover private-repo protection)"
    else
        warn "branch protection failed for '$branch'; skipping. gh said: ${err:-<no stderr captured>}"
        warn "if you're a maintainer, retry with a token that has 'repo' admin scope."
        note "⚠ branch protection ($branch): skipped (see warning above)"
    fi
}

if [[ "$GH_OK" -eq 1 && -n "$ORIGIN_SLUG" ]]; then
    if [[ -n "$INTEGRATION_BRANCH" ]]; then
        put_branch_protection "$INTEGRATION_BRANCH"
    else
        warn "skipping integration-branch protection (role resolution failed)."
        note "⚠ branch protection (integration): skipped (role resolution failed)"
    fi
    if [[ -n "$RELEASE_BRANCH" ]] && git ls-remote --exit-code --heads origin "$RELEASE_BRANCH" >/dev/null 2>&1; then
        put_branch_protection "$RELEASE_BRANCH"
    else
        warn "release branch absent from origin or unresolved; skipping its branch protection."
        note "⚠ branch protection (release): skipped (branch absent or unresolved)"
    fi
else
    warn "skipping branch protection (gh not ready or origin slug unresolved)."
    note "⚠ branch protection: skipped (gh not ready)"
fi

# ---- step 6: python3 presence check (warn-only) ---------------------------

step 6 "python3 presence check (warn-only)"

# python3 is required by the canonical workflow event logger
# (.claude/hooks/log-tool-event.sh calls python3 for JSON emission) and by
# the Playwright qa-tester route (step 8). We do NOT auto-install it —
# a missing Python runtime is a host-setup concern. Warn-and-continue.
if command -v python3 >/dev/null 2>&1; then
    log "python3 present: $(python3 --version 2>/dev/null)"
    note "✓ python3: present"
else
    warn "python3 not on PATH. Required by the workflow event logger and Playwright qa-tester."
    warn "  Install: https://www.python.org/downloads/ (or: winget install Python.Python.3 on Windows; brew install python on macOS)"
    note "⚠ python3: missing (install for event logger + Playwright qa-tester)"
fi

# ---- step 7: jq install (per ADR-0030 D1) ---------------------------------

step 7 "jq install (idempotent; cross-platform)"

# jq is required by:
#   - .claude/hooks/pre-tool-edit.sh (parses tool_input.file_path JSON)
#   - .claude/hooks/session-start.sh (emits hookSpecificOutput JSON)
# On Windows Git Bash, jq is NOT installed by default, which triggers the
# rule-#10 ask fallback on every Edit/Write (real user-impact today per
# captured #222). Per ADR-0030 D1: detect, then OS-specific install if missing.
# Best-effort warn-and-continue; idempotent (skip if installed).
#
# OS detection: $OSTYPE works on bash; fall back to `uname`.
if command -v jq >/dev/null 2>&1; then
    log "jq present: $(jq --version 2>/dev/null | head -1)"
    note "✓ jq: present (skipped install)"
else
    case "$OSTYPE" in
        msys*|cygwin*|win32*)
            JQ_OS="windows"
            ;;
        darwin*)
            JQ_OS="macos"
            ;;
        linux*)
            JQ_OS="linux"
            ;;
        *)
            # Fallback to uname if OSTYPE not informative.
            UN=$(uname -s 2>/dev/null || echo "")
            case "$UN" in
                MINGW*|MSYS*|CYGWIN*) JQ_OS="windows" ;;
                Darwin)               JQ_OS="macos" ;;
                Linux)                JQ_OS="linux" ;;
                *)                    JQ_OS="unknown" ;;
            esac
            ;;
    esac
    log "jq missing; attempting install for OS=$JQ_OS"
    JQ_INSTALL_RC=1
    case "$JQ_OS" in
        windows)
            if command -v winget >/dev/null 2>&1; then
                # --silent + acceptance flags avoid interactive prompts.
                winget install --id jqlang.jq --silent --accept-source-agreements --accept-package-agreements >/dev/null 2>&1 && JQ_INSTALL_RC=0
            else
                warn "winget not available on Windows; cannot auto-install jq."
                warn "  Manual: download from https://stedolan.github.io/jq/download/ and add to PATH."
            fi
            ;;
        macos)
            if command -v brew >/dev/null 2>&1; then
                brew install jq >/dev/null 2>&1 && JQ_INSTALL_RC=0
            else
                warn "brew not available on macOS; cannot auto-install jq."
                warn "  Manual: install Homebrew (https://brew.sh) then 'brew install jq'."
            fi
            ;;
        linux)
            if command -v apt-get >/dev/null 2>&1; then
                if command -v sudo >/dev/null 2>&1; then
                    sudo apt-get install -y jq >/dev/null 2>&1 && JQ_INSTALL_RC=0
                else
                    apt-get install -y jq >/dev/null 2>&1 && JQ_INSTALL_RC=0
                fi
            else
                warn "apt-get not available; cannot auto-install jq (try yum/dnf/pacman manually)."
            fi
            ;;
        *)
            warn "Unknown OS for jq install; skipping."
            ;;
    esac
    if [[ "$JQ_INSTALL_RC" -eq 0 ]]; then
        log "jq install attempted; verify with 'command -v jq' in a new shell (PATH may need refresh)."
        note "✓ jq: install attempted ($JQ_OS)"
    else
        warn "jq install failed or skipped. PreToolUse Edit/Write hook will fall back to rule-#10 ask."
        note "⚠ jq: missing (install manually for $JQ_OS)"
    fi
fi

# ---- step 8: Playwright Python library install (per ADR-0050 D1) ----------

step 8 "Playwright Python library install (pip install playwright — idempotent)"

# ADR-0050 D1 reinstates Playwright as the qa-tester browser driver, replacing
# Claude_Preview MCP. The driver is now the Playwright Python LIBRARY driving
# the already-installed Chrome via channel="chrome", headless=True.
#
# This step installs only the Playwright Python library — NOT a chromium binary
# download. Chrome is assumed installed on the host (no `playwright install
# chromium` is run). Per ADR-0050 D1: no ~150 MB binary download.
#
# Idempotent: `pip install playwright` is a no-op if the library is already
# present. Non-fatal: warns and continues if pip is not available.
#
# Supersedes: the prior ADR-0049 D1 note (Claude_Preview was harness-provided;
# no pip install was needed). ADR-0049 D1/D2 are now superseded by ADR-0050.
# The step number (8) is preserved for audit-trail continuity (do not
# renumber). ADR-0089 D2's integration-branch-creation step lives at 4b,
# between step 4 and step 5 — it does not shift this step's number.
if command -v pip >/dev/null 2>&1 || command -v pip3 >/dev/null 2>&1; then
    PIP_CMD="pip"
    command -v pip >/dev/null 2>&1 || PIP_CMD="pip3"
    log "installing Playwright Python library via $PIP_CMD (library only; no chromium download)..."
    if $PIP_CMD install playwright >/dev/null 2>&1; then
        log "Playwright library install succeeded (or already present)."
        note "✓ Playwright library: installed via $PIP_CMD (ADR-0050 D1)"
    else
        warn "Playwright library install failed; qa-tester browser route may not work."
        warn "  Manual: pip install playwright"
        note "⚠ Playwright library: install failed — run 'pip install playwright' manually"
    fi
else
    warn "pip not found on PATH; cannot install Playwright library."
    warn "  Install pip, then run: pip install playwright"
    note "⚠ Playwright library: pip missing — install pip then run 'pip install playwright'"
fi

# ---- step 9: label-sync drift warning (ADR-0068 D1) -------------------------

step 9 "label-sync drift check (declared vs live)"

# Warn if the labels declared in LABELS[] above differ from labels present on
# the live repo. Drift = bootstrap.sh drifted from the live label set (observed
# twice historically). Advisory only: never blocks.
# Per ADR-0068 D1 (hygiene registry D1 — bootstrap.sh label-sync drift).

if [[ "$GH_OK" -eq 1 && -n "$ORIGIN_SLUG" ]]; then
    LIVE_LABELS=$(gh label list --repo "$ORIGIN_SLUG" --limit 200 --json name 2>/dev/null \
                  | python3 -c "import json,sys; d=json.load(sys.stdin); [print(e['name']) for e in d]" \
                  2>/dev/null || true)
    if [[ -z "$LIVE_LABELS" ]]; then
        warn "label-sync: could not fetch live labels for drift check."
        note "⚠ label-sync: drift check skipped (could not fetch live labels)"
    else
        DECLARED_ONLY=()
        LIVE_ONLY=()
        # Declared label names
        DECLARED_NAMES=()
        for spec in "${LABELS[@]}"; do
            IFS='|' read -r _lname _lcolor _ldesc <<<"$spec"
            DECLARED_NAMES+=("$_lname")
        done
        # Check declared-but-not-live
        for _dname in "${DECLARED_NAMES[@]}"; do
            if ! printf '%s\n' "$LIVE_LABELS" | grep -qxF "$_dname"; then
                DECLARED_ONLY+=("$_dname")
            fi
        done
        # Check live-but-not-declared (only project-relevant labels: known set)
        # We warn on unexpected labels only if they look like project labels
        # (skip GitHub auto-created labels like "bug", "documentation", etc.)
        _GITHUB_DEFAULT_LABELS=("bug" "documentation" "duplicate" "enhancement"
                                 "good first issue" "help wanted" "invalid"
                                 "question" "wontfix")
        while IFS= read -r _lname; do
            [[ -z "$_lname" ]] && continue
            _is_default=0
            for _def in "${_GITHUB_DEFAULT_LABELS[@]}"; do
                [[ "$_lname" == "$_def" ]] && { _is_default=1; break; }
            done
            [[ "$_is_default" -eq 1 ]] && continue
            _is_declared=0
            for _dname in "${DECLARED_NAMES[@]}"; do
                [[ "$_lname" == "$_dname" ]] && { _is_declared=1; break; }
            done
            [[ "$_is_declared" -eq 0 ]] && LIVE_ONLY+=("$_lname")
        done <<< "$LIVE_LABELS"

        if [[ ${#DECLARED_ONLY[@]} -eq 0 && ${#LIVE_ONLY[@]} -eq 0 ]]; then
            log "label-sync: declared labels match live repo (no drift)."
            note "✓ label-sync: no drift"
        else
            if [[ ${#DECLARED_ONLY[@]} -gt 0 ]]; then
                warn "label-sync DRIFT: declared but not on live repo: ${DECLARED_ONLY[*]}"
                warn "  Fix: run 'bootstrap.sh' after creating missing labels, or remove from LABELS array."
            fi
            if [[ ${#LIVE_ONLY[@]} -gt 0 ]]; then
                warn "label-sync DRIFT: on live repo but not declared: ${LIVE_ONLY[*]}"
                warn "  Fix: add these to the LABELS array in bootstrap.sh."
            fi
            note "⚠ label-sync: DRIFT detected (declared-only: [${DECLARED_ONLY[*]}]; live-only: [${LIVE_ONLY[*]}])"
        fi
    fi
else
    warn "skipping label-sync drift check (gh not ready or origin slug unresolved)."
    note "⚠ label-sync: skipped (gh not ready)"
fi

# ---- end-of-run summary ---------------------------------------------------

printf '\n%s ---- summary ----\n' "$TAG"
for line in "${SUMMARY[@]}"; do
    printf '%s   %s\n' "$TAG" "$line"
done
printf '%s done. re-run any time — every step is idempotent.\n' "$TAG"
