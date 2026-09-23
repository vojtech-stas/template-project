#!/usr/bin/env bash
# tools/ci-checks.sh — deterministic CI gate for project-claude.
#
# Runnable locally and in GitHub Actions (see .github/workflows/ci.yml).
# Exits 0 if all checks pass; exits 1 if any check fails.
# ADR-0042 D1 — mechanical CI gate; mirrors audit-meta DOCS-1/DOCS-2/DOCS-7
# logic + ADR-0070 D1 origin/develop base for commit-range checks.
#
# Usage: bash tools/ci-checks.sh
#   (run from the repo root)

# set -e intentionally OMITTED: this script accumulates failures via FAIL_COUNT
# so ALL checks must run regardless of earlier failures.  -u (unbound vars) and
# -o pipefail are kept; -e (abort-on-error) is incompatible with the run-all
# design (see #727 root-cause).
set -uo pipefail

FAIL_COUNT=0

fail() {
    echo "FAIL: $*" >&2
    FAIL_COUNT=$((FAIL_COUNT + 1))
}

pass() {
    echo "PASS: $*"
}

# ---------------------------------------------------------------------------
# CHECK 1: settings.json valid JSON
# ---------------------------------------------------------------------------
echo "--- CHECK 1: .claude/settings.json valid JSON ---"
if python3 -m json.tool .claude/settings.json > /dev/null 2>&1; then
    pass ".claude/settings.json is valid JSON"
else
    fail ".claude/settings.json is not valid JSON"
fi

# ---------------------------------------------------------------------------
# CHECK 2: README regen-clean
# ---------------------------------------------------------------------------
echo "--- CHECK 2: README regen-clean ---"
if ! command -v python3 > /dev/null 2>&1; then
    # python3 itself missing is the one soft-degrade every check shares
    # (ADR-0088 D3 — unchanged).
    echo "SKIP: CHECK 2 — python3 not available (soft-degrade)"
elif [ ! -f "dashboard/readme_gen.py" ]; then
    # ADR-0088 D3: the missing-generator branch fails CLOSED, not SKIP — a
    # moved/deleted generator must never turn the README gate into a silent
    # green SKIP.
    fail "CHECK 2 — dashboard/readme_gen.py not found; README currency cannot be verified"
else
    # Stash the current README into a temp file so we can restore it without
    # clobbering pre-existing uncommitted edits (issue #727: git checkout --
    # README.md is destructive; cp/mv is safe).
    _readme_tmp=$(mktemp)
    cp README.md "$_readme_tmp"
    python3 dashboard/readme_gen.py > /dev/null 2>&1
    if git diff --exit-code README.md > /dev/null 2>&1; then
        pass "README.md is up-to-date with regen output"
    else
        fail "README.md is stale — run 'python3 dashboard/readme_gen.py' and commit"
    fi
    # Always restore to pre-check state (avoids polluting diff for other checks
    # and preserves any pre-existing uncommitted edits).
    cp "$_readme_tmp" README.md
    rm -f "$_readme_tmp"
fi

# ---------------------------------------------------------------------------
# CHECK 3: Commit subjects — ≤72 chars + Conventional Commits format
# ---------------------------------------------------------------------------
echo "--- CHECK 3: commit subjects over origin/develop..HEAD ---"
# Fetch origin/develop so the range is available in CI (ADR-0070 D1).
git fetch origin develop --quiet 2>/dev/null || true

CONV_RE='^(feat|fix|chore|refactor|docs|test|perf|style|build|ci)(\(.+\))?: .+'
RANGE_COMMITS=$(git log --no-merges --format='%s' origin/develop..HEAD 2>/dev/null || true)

if [ -z "$RANGE_COMMITS" ]; then
    echo "CHECK 3 VACUOUS — no commits in range; subject-format not verified"
else
    CHECK3_FAIL=0
    while IFS= read -r subject; do
        [ -z "$subject" ] && continue
        # Length check
        len=${#subject}
        if [ "$len" -gt 72 ]; then
            fail "commit subject exceeds 72 chars ($len): $subject"
            CHECK3_FAIL=1
        fi
        # Conventional Commits format
        if ! printf '%s' "$subject" | grep -qE "$CONV_RE"; then
            fail "commit subject not Conventional Commits format: $subject"
            CHECK3_FAIL=1
        fi
    done <<< "$RANGE_COMMITS"
    if [ "$CHECK3_FAIL" -eq 0 ]; then
        pass "all commit subjects pass length + Conventional Commits check"
    fi
fi

# ---------------------------------------------------------------------------
# CHECK 4: Dangling ADR links — delegated to health.py registry (DOCS-7)
# ADR-0064 D3: single-source implementation; verdict-identical to prior bash.
# ---------------------------------------------------------------------------
echo "--- CHECK 4: dangling decisions/NNNN-*.md links in tracked .md files ---"
if ! command -v python3 > /dev/null 2>&1 || [ ! -f "dashboard/health.py" ]; then
    echo "SKIP: CHECK 4 — python3 or dashboard/health.py not available (soft-degrade)"
else
    CHECK4_OUTPUT=$(python3 dashboard/health.py --check DOCS-7 2>&1)
    CHECK4_EXIT=$?
    if [ "$CHECK4_EXIT" -eq 0 ]; then
        pass "CHECK 4 (DOCS-7): $CHECK4_OUTPUT"
    else
        fail "CHECK 4 (DOCS-7): $CHECK4_OUTPUT"
    fi
fi

# ---------------------------------------------------------------------------
# CHECK 5: decisions/README.md index consistency — delegated to registry
# ADR-0064 D3: DOCS-1 (forward: index→file) + DOCS-2 (reverse: file→index).
# Verdict-identical to prior bash loop pair.
# ---------------------------------------------------------------------------
echo "--- CHECK 5: decisions/README.md <-> decisions/*.md index consistency ---"
if ! command -v python3 > /dev/null 2>&1 || [ ! -f "dashboard/health.py" ]; then
    echo "SKIP: CHECK 5 — python3 or dashboard/health.py not available (soft-degrade)"
else
    CHECK5_FAIL=0
    CHECK5_1=$(python3 dashboard/health.py --check DOCS-1 2>&1)
    CHECK5_1_EXIT=$?
    CHECK5_2=$(python3 dashboard/health.py --check DOCS-2 2>&1)
    CHECK5_2_EXIT=$?
    if [ "$CHECK5_1_EXIT" -ne 0 ]; then
        fail "CHECK 5 (DOCS-1): $CHECK5_1"
        CHECK5_FAIL=1
    fi
    if [ "$CHECK5_2_EXIT" -ne 0 ]; then
        fail "CHECK 5 (DOCS-2): $CHECK5_2"
        CHECK5_FAIL=1
    fi
    if [ "$CHECK5_FAIL" -eq 0 ]; then
        pass "CHECK 5 (DOCS-1/2): decisions/README.md index is consistent with decisions/*.md on disk"
    fi
fi

# ---------------------------------------------------------------------------
# CHECK 6: Dangling ADR D-ID citations (mirrors CHECK 4 / DOCS-7 mechanic)
# ---------------------------------------------------------------------------
echo "--- CHECK 6: dangling ADR D-ID citations in tracked .md files ---"
# Requires python3 + git; soft-degrade if unavailable.
if ! command -v python3 > /dev/null 2>&1 || ! command -v git > /dev/null 2>&1; then
    echo "SKIP: CHECK 6 — python3 or git not available (soft-degrade)"
else
python3 - << 'PYEOF'
import re, os, sys, glob, subprocess

# ---------------------------------------------------------------------------
# Allowlist: (file_suffix_pattern, adr_nnnn, d_id, reason)
# Entries are matched when the source file path ENDS WITH file_suffix_pattern.
# Add an entry here (with a reason comment) instead of editing immutable ADRs.
# ---------------------------------------------------------------------------
ALLOWLIST = [
    # Pedagogical example in AC-SUPERSEDES-WITHOUT-HEADER rule body — fictional
    # scenario contrasting "ADR-0002 D1" with a real policy. ADR-0002 has no D1
    # (only D9-revised). Citation is illustrative, not referential.
    ('.claude/agents/adr-critic.md', '0002', 1,
     'pedagogical example in rule body; fictional D1 for illustration only'),

    # ADR-0081's Propagation ledger names the adr-critic.md / "ADR-0002 D1"
    # allowlist entry above to classify a sibling grandfathered hit. It is a
    # meta-reference to that already-allowlisted FICTIONAL cite, not a live
    # citation — ADR-0002 still has no D1 (only D9-revised). Allowlisted here
    # rather than edited, per this list's own header (ADRs are immutable).
    ('decisions/0081-post-audit-dead-weight-retirements.md', '0002', 1,
     'meta-reference to the adr-critic.md pedagogical entry; fictional D1'),

]

def is_allowlisted(filepath, nnnn, did):
    """Return True if (filepath, nnnn, did) is in the allowlist."""
    for suffix, a_nnnn, a_did, _ in ALLOWLIST:
        if filepath.endswith(suffix) and nnnn == a_nnnn and did == a_did:
            return True
    return False

# Step 1: Build ADR D-ID heading map from decisions/NNNN-*.md files.
adr_d_ids = {}
for adrfile in sorted(glob.glob('decisions/[0-9]*.md')):
    nnnn = adrfile[10:14]
    headings = set()
    try:
        with open(adrfile, 'r', encoding='utf-8', errors='replace') as f:
            for line in f:
                # Accept: ### D<n>  ### D<n>.  ### D<n>:  ### D<n> (  ### D<n>-
                m = re.match(r'^### D(\d+)([.:( \-]|$)', line.strip())
                if m:
                    headings.add(int(m.group(1)))
    except OSError:
        pass
    adr_d_ids[nnnn] = headings

# Step 2: Enumerate tracked .md files (reuse CHECK 4's exclusions).
result = subprocess.run(
    ['git', 'ls-files', '*.md'],
    capture_output=True, text=True
)
md_files = [
    f for f in result.stdout.strip().split('\n')
    if f
    and not f.startswith('.git/')
    and not f.startswith('.claude/worktrees/')
    and not f.startswith('tool-results/')
]

# Step 3: Citation pattern — catch common forms:
#   ADR-0008 D6
#   [ADR-0008](path) D6
#   ADR-0008 D6/D7  (extracts D6 and D7 separately via findall)
#   Avoids over-matching: D<n> must be the IMMEDIATE next token after the ADR
#   ref (+ optional md-link close), separated by exactly one space — no
#   intervening parentheses, dashes, or prose words.
#   Rejected forms (false positives): "ADR-0033 (D10)" or
#   "[ADR-0012](path) — prose (D10 references)".
CITE_RE = re.compile(
    r'(?:\[)?ADR-([0-9]{4})(?:\][^\)]*\))?'  # ADR-NNNN or [ADR-NNNN](...)
    r' D([0-9]+)\b'                           # single space then D<n> directly
)

# Also catch slash-separated D-IDs like "D6/D7" after the initial match
SLASH_D_RE = re.compile(r'\bD([0-9]+)\b')

# Step 4: Scan all files for citations.
danglings = []  # (filepath, nnnn, did)
seen_danglings = set()

for mdfile in md_files:
    if not os.path.isfile(mdfile):
        continue
    try:
        with open(mdfile, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()
    except OSError:
        continue

    for m in CITE_RE.finditer(content):
        nnnn = m.group(1)
        first_did = int(m.group(2))

        # Collect all D-IDs from this match position (handles D6/D7 slash form):
        # Look at the text immediately following the matched D<n> for /D<m> forms.
        tail_start = m.end()
        tail_end = min(len(content), tail_start + 20)
        tail = content[tail_start:tail_end]
        did_list = [first_did]
        for slash_m in re.finditer(r'^/D([0-9]+)\b', tail):
            did_list.append(int(slash_m.group(1)))

        for did in did_list:
            key = (mdfile, nnnn, did)
            if key in seen_danglings:
                continue

            # Skip allowlisted entries.
            if is_allowlisted(mdfile, nnnn, did):
                continue

            # Check ADR file exists.
            adr_glob = glob.glob(f'decisions/{nnnn}-*.md')
            if not adr_glob:
                # ADR file doesn't exist — but this is also caught by CHECK 4.
                # Skip here to avoid double-reporting (CHECK 4 owns file-level checks).
                continue

            # Check D-ID heading exists.
            valid_dids = adr_d_ids.get(nnnn, set())
            if did not in valid_dids:
                seen_danglings.add(key)
                danglings.append((mdfile, nnnn, did))

# Step 5: Report results.
if danglings:
    for filepath, nnnn, did in sorted(danglings):
        print(
            f'FAIL: CHECK 6 — dangling D-ID ADR-{nnnn} D{did} in {filepath}',
            file=sys.stderr
        )
    sys.exit(1)
else:
    print('PASS: CHECK 6 — no dangling ADR D-ID citations found')
    sys.exit(0)
PYEOF
CHECK6_EXIT=$?
if [ "$CHECK6_EXIT" -ne 0 ]; then
    FAIL_COUNT=$((FAIL_COUNT + 1))
fi
fi  # end python3/git availability check

# ---------------------------------------------------------------------------
# CHECK 7: doc-vs-reality drift gate (ADR-0047 D3)
#   (a) source ↔ reality  — PIPELINE/KNOWN_CRITICS spec vs .claude/agents/*.md
#   (b) artifact ↔ source — README critic list entries have matching agent files
#   (c) prose-facts ↔ reality — CLAUDE.md critic count/names vs filesystem
# ---------------------------------------------------------------------------
echo "--- CHECK 7: doc-vs-reality drift gate ---"
if ! command -v python3 > /dev/null 2>&1 || ! command -v git > /dev/null 2>&1; then
    echo "SKIP: CHECK 7 — python3 or git not available (soft-degrade)"
else
python3 - << 'PYEOF'
import re, os, sys, glob

REPO_ROOT = os.getcwd()
AGENTS_DIR = os.path.join(REPO_ROOT, '.claude', 'agents')
CONSTANTS_PY = os.path.join(REPO_ROOT, 'dashboard', '_constants.py')
CLAUDE_MD  = os.path.join(REPO_ROOT, 'CLAUDE.md')
README_MD  = os.path.join(REPO_ROOT, 'README.md')

fail_msgs = []

def fail(msg):
    fail_msgs.append(msg)

# ------------------------------------------------------------------ helpers --
def read_file(path):
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            return f.read()
    except OSError:
        return ''

# ------------------------------------------------ (a) source ↔ reality ------
# Parse KNOWN_CRITICS from dashboard/_constants.py (set literal, one name per
# line) — the single-sourced home since ADR-0088 D4 retired dashboard/server.py.
spec_text = read_file(CONSTANTS_PY)
if not spec_text:
    fail('CHECK 7(a) — could not read dashboard/_constants.py')
else:
    # Extract the KNOWN_CRITICS set: lines like: "    \"reviewer\","
    kc_block = re.search(
        r'KNOWN_CRITICS\s*=\s*\{([^}]+)\}', spec_text, re.DOTALL
    )
    if not kc_block:
        fail('CHECK 7(a) — KNOWN_CRITICS not found in dashboard/_constants.py')
    else:
        spec_critics = set(re.findall(r'"([^"]+)"', kc_block.group(1)))
        # Discover agent file stems from .claude/agents/*.md
        agent_stems = set()
        if os.path.isdir(AGENTS_DIR):
            for f in glob.glob(os.path.join(AGENTS_DIR, '*.md')):
                agent_stems.add(os.path.splitext(os.path.basename(f))[0])

        # Every critic in the spec must have a matching agent file
        for critic in sorted(spec_critics):
            if critic not in agent_stems:
                fail(
                    f'CHECK 7(a) — spec critic "{critic}" has no '
                    f'.claude/agents/{critic}.md file'
                )

        # Every *-critic.md file must appear in the spec (or be reviewer.md)
        # We check all agent files whose stem ends in "-critic"
        for stem in sorted(agent_stems):
            if stem.endswith('-critic') and stem not in spec_critics:
                fail(
                    f'CHECK 7(a) — .claude/agents/{stem}.md exists but '
                    f'"{stem}" is not in KNOWN_CRITICS spec'
                )

# ------------------------------------------- (a2) single-definition assertion
# ADR-0088 D4: KNOWN_CRITICS must be defined exactly once across dashboard/
# and tools/ — dashboard/_constants.py is the sole canonical home. A second
# definition anywhere under those two trees is a FAIL naming that file.
_kc_def_re = re.compile(r'^\s*_?KNOWN_CRITICS\s*=', re.MULTILINE)
_kc_def_files = []
for _tree in ('dashboard', 'tools'):
    _tree_dir = os.path.join(REPO_ROOT, _tree)
    if not os.path.isdir(_tree_dir):
        continue
    for _f in glob.glob(os.path.join(_tree_dir, '**', '*.py'), recursive=True):
        _text = read_file(_f)
        if _kc_def_re.search(_text):
            _kc_def_files.append(os.path.relpath(_f, REPO_ROOT).replace(os.sep, '/'))
if len(_kc_def_files) > 1:
    fail(
        'CHECK 7(a) — multiple KNOWN_CRITICS definitions found: '
        + ', '.join(sorted(_kc_def_files))
        + ' (expected exactly one, in dashboard/_constants.py)'
    )
elif len(_kc_def_files) == 0:
    fail('CHECK 7(a) — no KNOWN_CRITICS definition found under dashboard/ or tools/')

# ------------------------------------------------ (b) artifact ↔ source -----
# README "Adversarial critics" section lists critics — each must have an agent file.
readme_text = read_file(README_MD)
if not readme_text:
    print('SKIP: CHECK 7(b) — README.md not found (soft-degrade)')
else:
    # Find the "## Adversarial critics" section and extract linked agent names.
    # Pattern: **[`name`](.claude/agents/name.md)**
    section_m = re.search(
        r'##\s+Adversarial critics\s*(.*?)(?=\n## |\Z)',
        readme_text, re.DOTALL
    )
    if section_m:
        section = section_m.group(1)
        # Extract stem from links: .claude/agents/<stem>.md
        readme_critic_stems = set(
            re.findall(r'\.claude/agents/([a-z0-9-]+)\.md', section)
        )
        for stem in sorted(readme_critic_stems):
            agent_path = os.path.join(AGENTS_DIR, stem + '.md')
            if not os.path.isfile(agent_path):
                fail(
                    f'CHECK 7(b) — README "Adversarial critics" lists '
                    f'"{stem}" but .claude/agents/{stem}.md does not exist'
                )
    # If section not found, soft-skip (README structure may differ)

# ------------------------------------------------ (c) prose-facts ↔ reality -
# CLAUDE.md says "currently runs **N critics**: name, name, ..."
claude_text = read_file(CLAUDE_MD)
if not claude_text:
    print('SKIP: CHECK 7(c) — CLAUDE.md not found (soft-degrade)')
else:
    # Re-parse KNOWN_CRITICS (already done above) for count comparison.
    if spec_text and 'spec_critics' in dir():
        # Extract the numeric count claim
        count_m = re.search(
            r'currently runs \*\*(\d+) critics\*\*', claude_text
        )
        if count_m:
            claimed_count = int(count_m.group(1))
            actual_count  = len(spec_critics) if spec_critics else 0
            if claimed_count != actual_count:
                fail(
                    f'CHECK 7(c) — CLAUDE.md claims {claimed_count} critics '
                    f'but KNOWN_CRITICS spec has {actual_count}'
                )

        # Extract names from the inline list after "currently runs **N critics**:"
        list_m = re.search(
            r'currently runs \*\*\d+ critics\*\*:\s*([^\n.]+)',
            claude_text
        )
        if list_m:
            raw = list_m.group(1)
            # Names are backtick-quoted: `reviewer`, `prd-critic`, ...
            named = set(re.findall(r'`([^`]+)`', raw))
            agent_stems_c = set()
            if os.path.isdir(AGENTS_DIR):
                for f in glob.glob(os.path.join(AGENTS_DIR, '*.md')):
                    agent_stems_c.add(os.path.splitext(os.path.basename(f))[0])
            for name in sorted(named):
                if name not in agent_stems_c:
                    fail(
                        f'CHECK 7(c) — CLAUDE.md names critic "{name}" '
                        f'but .claude/agents/{name}.md does not exist'
                    )

# ------------------------------------------------------------------ output --
if fail_msgs:
    for msg in fail_msgs:
        print(f'FAIL: {msg}', file=sys.stderr)
    sys.exit(1)
else:
    print('PASS: CHECK 7 — no doc-vs-reality drift detected')
    sys.exit(0)
PYEOF
CHECK7_EXIT=$?
if [ "$CHECK7_EXIT" -ne 0 ]; then
    FAIL_COUNT=$((FAIL_COUNT + 1))
fi
fi  # end python3/git availability check

# ---------------------------------------------------------------------------
# CHECK 8: Agent-payload hook-path fixture test + hook-entry-count assertion
#   Updated for PRD #668 slice #670: Agent hooks now call log-tool-event.sh.
#   Updated for PRD #876 slice #877: consolidated to auto-mode; added count gate.
#   (a) settings.json references log-tool-event.sh and NOT log-event.sh (deleted).
#   (b) The fixture's .tool_input.subagent_type resolves non-empty via python3.
#   (c) Hook-command count (sum of len(e['hooks'])) <= 9 (target: 8; cap adds 1 slack).
#       Rationale: 15->8 consolidation per PRD #876; strict cap <= 9 here.
#   Soft-degrades if python3 unavailable.
# ---------------------------------------------------------------------------
echo "--- CHECK 8: Agent-payload hook-path fixture test + hook-entry-count ---"
FIXTURE="dashboard/fixtures/agent-payload-sample.json"
SETTINGS=".claude/settings.json"
if ! command -v python3 > /dev/null 2>&1; then
    echo "SKIP: CHECK 8 — python3 not available (soft-degrade)"
elif [ ! -f "$FIXTURE" ]; then
    fail "CHECK 8 — fixture not found: $FIXTURE"
elif [ ! -f "$SETTINGS" ]; then
    fail "CHECK 8 — settings.json not found: $SETTINGS"
else
    CHECK8_FAIL=0
    # (a) Assert settings.json references log-tool-event.sh (consolidated logger).
    if ! grep -q 'log-tool-event\.sh' "$SETTINGS"; then
        fail "CHECK 8 — settings.json does not reference log-tool-event.sh at all"
        CHECK8_FAIL=1
    fi
    # Verify no reference to deleted log-event.sh remains (PRD #876).
    if grep -q 'log-event\.sh' "$SETTINGS"; then
        fail "CHECK 8 — settings.json still references deleted log-event.sh"
        CHECK8_FAIL=1
    fi
    # (b) Assert the fixture's tool_input.subagent_type resolves non-empty via python3.
    SUBAGENT_VAL=$(python3 -c "
import json, sys
with open('$FIXTURE') as f:
    d = json.load(f)
val = d.get('tool_input', {}).get('subagent_type', '')
print(val)
" 2>/dev/null)
    if [ -z "$SUBAGENT_VAL" ]; then
        fail "CHECK 8 — tool_input.subagent_type resolved empty in $FIXTURE via python3 (fixture stale?)"
        CHECK8_FAIL=1
    fi
    # (c) Hook-command count: sum of len(e['hooks']) across all entries. Cap <= 9.
    HOOK_COUNT=$(python3 -c "
import json
data = json.load(open('$SETTINGS'))
print(sum(len(e['hooks']) for v in data['hooks'].values() for e in v))
" 2>/dev/null)
    if [ -z "$HOOK_COUNT" ]; then
        fail "CHECK 8 — could not compute hook-command count from $SETTINGS"
        CHECK8_FAIL=1
    elif [ "$HOOK_COUNT" -gt 9 ]; then
        fail "CHECK 8 — hook-command count $HOOK_COUNT exceeds cap of 9 (PRD #876 target: 8)"
        CHECK8_FAIL=1
    fi
    if [ "$CHECK8_FAIL" -eq 0 ]; then
        pass "CHECK 8 — log-tool-event.sh wired; no log-event.sh refs; hook-count=$HOOK_COUNT (<=9); subagent_type='$SUBAGENT_VAL' from fixture"
    fi
fi

# ---------------------------------------------------------------------------
# CHECK 9: Dashboard check_docs* drift guard
#   Derives the full DOCS-* check list from the registry CLI
#   (python3 dashboard/health.py --list | grep ^DOCS-), then runs each via
#   python3 dashboard/health.py --check <ID>.  WARN is allowed; only FAIL
#   trips this check.  Covers all registered DOCS-* IDs — the list grows
#   automatically as new checks are added to the registry, so no manual
#   update is needed here (ADR-0064 D3 single-source model).
#
#   Prior design imported server.py directly (DOCS-1..10 hard-coded list);
#   that approach silently omitted DOCS-11 (check_docs11_dead_citations) and
#   any future DOCS-N — a regression-class risk per codebase-critic CC-REF-CURRENCY.
#   The registry-CLI approach eliminates the static enumeration entirely.
# ---------------------------------------------------------------------------
echo "--- CHECK 9: dashboard check_docs* drift guard ---"
if ! command -v python3 > /dev/null 2>&1; then
    echo "SKIP: CHECK 9 — python3 not available (soft-degrade)"
elif [ ! -f "dashboard/health.py" ]; then
    echo "SKIP: CHECK 9 — dashboard/health.py not found (soft-degrade)"
else
    CHECK9_FAIL=0
    # Derive all DOCS-* IDs from the registry; exit non-zero if --list fails.
    DOCS_IDS=$(python3 dashboard/health.py --list 2>/dev/null | grep '^DOCS-' || true)
    if [ -z "$DOCS_IDS" ]; then
        fail "CHECK 9 — 'python3 dashboard/health.py --list' returned no DOCS-* IDs"
        CHECK9_FAIL=1
    else
        while IFS= read -r doc_id; do
            [ -z "$doc_id" ] && continue
            # Run check via registry CLI; exit 0 = PASS/WARN, exit 1 = FAIL.
            CHECK9_OUT=$(python3 dashboard/health.py --check "$doc_id" 2>&1)
            CHECK9_CHECK_EXIT=$?
            if [ "$CHECK9_CHECK_EXIT" -ne 0 ]; then
                fail "CHECK 9 — $doc_id reports FAIL: $CHECK9_OUT"
                CHECK9_FAIL=1
            fi
        done <<< "$DOCS_IDS"
    fi
    if [ "$CHECK9_FAIL" -eq 0 ]; then
        DOC_COUNT=$(echo "$DOCS_IDS" | wc -l | tr -d ' ')
        pass "CHECK 9 — all $DOC_COUNT DOCS-* checks return non-FAIL on clean main"
    else
        FAIL_COUNT=$((FAIL_COUNT + 1))
    fi
fi  # end python3/dashboard/health.py availability check

# ---------------------------------------------------------------------------
# CHECK 10: Trailer-schema completeness (ADR-0054 D2 + ADR-0059 D1/D2)
#   Critic files (*-critic.md + reviewer.md): must document VERDICT, REASON,
#   ROUND, and CRITIC: (attribution field per ADR-0059 D1).
#   Generator files (implementer.md, slicer.md, qa-tester.md): must document
#   DIDNT_TOUCH: and CONCERNS: (scope-disclosure fields per ADR-0059 D2).
#   Whole-file fixed-string grep; SPIDR-I fallback (fenced-block scoping later
#   if false-positive issues arise).  Deterministic, local, network-free.
# ---------------------------------------------------------------------------
echo "--- CHECK 10: trailer-schema completeness (critic CRITIC: + generator DIDNT_TOUCH:/CONCERNS:) ---"
CHECK10_FAIL=0

# --- 10a: Critic files: VERDICT, REASON, ROUND, CRITIC: ---
CRITIC_FILES=()
# nullglob: if no *-critic.md files exist, the glob expands to nothing rather
# than passing the literal pattern string through (closes #667).
shopt -s nullglob
# Collect all *-critic.md files
for f in .claude/agents/*-critic.md; do
    [ -f "$f" ] && CRITIC_FILES+=("$f")
done
shopt -u nullglob
# Add reviewer.md
[ -f ".claude/agents/reviewer.md" ] && CRITIC_FILES+=(".claude/agents/reviewer.md")

for agent_file in "${CRITIC_FILES[@]}"; do
    for key in VERDICT REASON ROUND "CRITIC:"; do
        if ! grep -qF "$key" "$agent_file" 2>/dev/null; then
            fail "CHECK 10 — $agent_file missing trailer key: $key"
            CHECK10_FAIL=1
        fi
    done
done

# --- 10b: Generator files: DIDNT_TOUCH: and CONCERNS: ---
GENERATOR_FILES=()
for f in .claude/agents/implementer.md .claude/agents/slicer.md .claude/agents/qa-tester.md; do
    [ -f "$f" ] && GENERATOR_FILES+=("$f")
done

for agent_file in "${GENERATOR_FILES[@]}"; do
    for key in "DIDNT_TOUCH:" "CONCERNS:"; do
        if ! grep -qF "$key" "$agent_file" 2>/dev/null; then
            fail "CHECK 10 — $agent_file missing generator trailer key: $key"
            CHECK10_FAIL=1
        fi
    done
done

if [ "$CHECK10_FAIL" -eq 0 ]; then
    pass "CHECK 10 — all critic files document VERDICT/REASON/ROUND/CRITIC:; all generator files document DIDNT_TOUCH:/CONCERNS:"
fi

# ---------------------------------------------------------------------------
# CHECK 11: qa-tester.md documents PROOF_SOURCE (ADR-0061 D2)
#   Verifies that qa-tester.md contains the PROOF_SOURCE: field in its
#   production-verify trailer template, confirming that structured proof
#   provenance (session_id + timestamp) is documented for machine-validation.
#   Whole-file fixed-string grep; no network calls; deterministic.
# ---------------------------------------------------------------------------
echo "--- CHECK 11: qa-tester.md documents PROOF_SOURCE ---"
QA_TESTER=".claude/agents/qa-tester.md"
if [ ! -f "$QA_TESTER" ]; then
    fail "CHECK 11 — $QA_TESTER not found"
else
    if grep -qF "PROOF_SOURCE:" "$QA_TESTER" 2>/dev/null; then
        pass "CHECK 11 — $QA_TESTER documents PROOF_SOURCE:"
    else
        fail "CHECK 11 — $QA_TESTER missing PROOF_SOURCE: (required by ADR-0061 D2)"
    fi
fi

# ---------------------------------------------------------------------------
# CHECK 12: tests/ suite (ADR-0067 D1)
#   Runs pytest when tests/ exists; FAIL on any test failure.
#   Reports collected count in the pass line.
#   Falls back to stdlib unittest discovery when pytest is unavailable.
#   Per-check aggregation: FAIL_COUNT incremented on failure only.
# ---------------------------------------------------------------------------
echo "--- CHECK 12: tests/ suite ---"
if [ ! -d "tests" ]; then
    echo "SKIP: CHECK 12 — tests/ directory not found (soft-degrade)"
elif ! command -v python3 > /dev/null 2>&1; then
    echo "SKIP: CHECK 12 — python3 not available (soft-degrade)"
else
    CHECK12_FAIL=0
    if python3 -m pytest --version > /dev/null 2>&1; then
        # pytest available — run with collect count
        CHECK12_OUTPUT=$(python3 -m pytest tests/ -v --tb=short 2>&1)
        CHECK12_EXIT=$?
        # Extract collected count from pytest output (e.g. "collected 3 items")
        CHECK12_COUNT=$(echo "$CHECK12_OUTPUT" | grep -oE 'collected [0-9]+ item' | grep -oE '[0-9]+' || echo "?")
        if [ "$CHECK12_EXIT" -eq 0 ]; then
            pass "CHECK 12 — pytest: $CHECK12_COUNT test(s) collected and passed"
        else
            fail "CHECK 12 — pytest: test suite failed (exit $CHECK12_EXIT)"
            echo "$CHECK12_OUTPUT" >&2
            CHECK12_FAIL=1
        fi
    else
        # Fallback: stdlib unittest discover
        CHECK12_OUTPUT=$(python3 -m unittest discover -s tests -p "test_*.py" -v 2>&1)
        CHECK12_EXIT=$?
        CHECK12_COUNT=$(echo "$CHECK12_OUTPUT" | grep -oE 'Ran [0-9]+ test' | grep -oE '[0-9]+' || echo "?")
        if [ "$CHECK12_EXIT" -eq 0 ]; then
            pass "CHECK 12 — unittest: $CHECK12_COUNT test(s) collected and passed"
        else
            fail "CHECK 12 — unittest: test suite failed (exit $CHECK12_EXIT)"
            echo "$CHECK12_OUTPUT" >&2
            CHECK12_FAIL=1
        fi
    fi
fi

# ---------------------------------------------------------------------------
# CHECK 13: Secrets gate (ADR-0068 D2)
#
# NEVER-LIST RULE: a secret that reaches a commit is ROTATED immediately —
# allowlisting a real secret is the named anti-pattern. This gate exists to
# catch accidental autonomous-agent commits before they reach the remote.
#
# Patterns: key/token/private-key shape + entropy heuristic.
# Gate of record (ADR-0042 D1); .githooks/pre-commit is the advisory mirror.
# Allowlist: tools/secrets-allowlist.txt (reviewed false positives only).
# Scope: tracked non-binary files in git diff HEAD (staged + working tree).
#        Skips: decisions/*.md, .claude/logs/, docs/*.md (prose mentions only).
# Per-check aggregation: FAIL_COUNT incremented on FAIL only.
# ---------------------------------------------------------------------------
echo "--- CHECK 13: secrets gate (ADR-0068 D2) ---"
if ! command -v python3 > /dev/null 2>&1; then
    echo "SKIP: CHECK 13 — python3 not available (soft-degrade)"
elif ! command -v git > /dev/null 2>&1; then
    echo "SKIP: CHECK 13 — git not available (soft-degrade)"
else
python3 - << 'SECRETS_PYEOF'
import re, sys, os, subprocess, glob

# Secret-shaped patterns (ADR-0068 D2): key/token/private-key + entropy heuristic.
# These match value-like strings following assignment operators or JSON colon.
_SECRET_PATTERNS = [
    # Generic API key/token/secret patterns: name = <value> or "name": "<value>"
    re.compile(
        r'(?i)(?:api[_-]?key|api[_-]?secret|auth[_-]?token|access[_-]?token'
        r'|secret[_-]?key|private[_-]?key|client[_-]?secret|oauth[_-]?token'
        r'|bearer[_-]?token|password)\s*[=:]\s*["\']?([A-Za-z0-9+/=_\-]{20,})["\']?'
    ),
    # GitHub PAT shape: ghp_ / gho_ / ghs_ / ghr_ / github_pat_ prefix
    re.compile(r'\b(ghp_[A-Za-z0-9]{36,}|gho_[A-Za-z0-9]{36,}'
               r'|ghs_[A-Za-z0-9]{36,}|ghr_[A-Za-z0-9]{36,}'
               r'|github_pat_[A-Za-z0-9_]{36,})\b'),
    # AWS access key ID pattern: AKIA prefix
    re.compile(r'\b(AKIA[A-Z0-9]{16})\b'),
    # Hex-encoded keys ≥ 40 chars after assignment
    re.compile(
        r'(?i)(?:key|secret|token|password)\s*[=:]\s*["\']?([0-9a-f]{40,})["\']?'
    ),
]

# Entropy heuristic: long base64-like strings in value position.
_ENTROPY_RE = re.compile(
    r'[=:]\s*["\']?([A-Za-z0-9+/]{32,}={0,2})["\']?\s*$'
)
_ENTROPY_THRESHOLD = 4.2  # bits/char Shannon entropy


def shannon_entropy(s):
    if not s:
        return 0.0
    from collections import Counter
    import math
    freq = Counter(s)
    length = len(s)
    return -sum((c / length) * math.log2(c / length) for c in freq.values())


# Path skip list: prose-only files where pattern matches are expected.
_SKIP_PATH_RE = re.compile(
    r'^(decisions/|docs/|\.claude/logs/|\.claude/worktrees/|qa-proof/)',
    re.IGNORECASE
)

# Load allowlist (tools/secrets-allowlist.txt).
_allowlist_patterns = []
allowlist_path = os.path.join(os.getcwd(), 'tools', 'secrets-allowlist.txt')
if os.path.isfile(allowlist_path):
    with open(allowlist_path, 'r', encoding='utf-8', errors='replace') as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith('#'):
                try:
                    _allowlist_patterns.append(re.compile(_line))
                except re.error:
                    pass  # skip malformed patterns

def is_allowlisted(line_text):
    for pat in _allowlist_patterns:
        if pat.search(line_text):
            return True
    return False


def check_file(filepath):
    """Return list of (lineno, line, reason) tuples for violations in filepath."""
    violations = []
    try:
        with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
            for lineno, line in enumerate(f, 1):
                line_stripped = line.rstrip('\n')
                if is_allowlisted(line_stripped):
                    continue
                # Check named patterns
                for pat in _SECRET_PATTERNS:
                    m = pat.search(line_stripped)
                    if m:
                        violations.append((lineno, line_stripped[:120],
                                           'secret-pattern'))
                        break
                else:
                    # Entropy heuristic
                    em = _ENTROPY_RE.search(line_stripped)
                    if em:
                        candidate = em.group(1)
                        ent = shannon_entropy(candidate)
                        if ent >= _ENTROPY_THRESHOLD and len(candidate) >= 32:
                            violations.append((lineno, line_stripped[:120],
                                               f'entropy={ent:.1f}'))
    except (OSError, UnicodeDecodeError):
        pass
    return violations


# Get tracked files from git diff (HEAD range: staged + committed since origin/develop).
# We scan the full file content of any file in the diff rather than just the diff lines,
# since secrets in context lines are also dangerous.
try:
    result = subprocess.run(
        ['git', 'ls-files', '--cached'],
        capture_output=True, text=True, encoding='utf-8', errors='replace'
    )
    tracked_files = [
        f.strip() for f in result.stdout.splitlines()
        if f.strip()
        and not _SKIP_PATH_RE.match(f.strip())
    ]
except Exception as e:
    print(f'SKIP: CHECK 13 — git ls-files failed: {e}')
    sys.exit(0)

# Filter to files that exist and are text-like (skip binaries by extension).
_BINARY_EXTS = {'.png', '.jpg', '.jpeg', '.gif', '.ico', '.pdf',
                '.zip', '.tar', '.gz', '.whl', '.pyc', '.so', '.dll'}
files_to_scan = []
for f in tracked_files:
    if not os.path.isfile(f):
        continue
    _, ext = os.path.splitext(f.lower())
    if ext in _BINARY_EXTS:
        continue
    files_to_scan.append(f)

all_violations = []
for filepath in files_to_scan:
    viols = check_file(filepath)
    for lineno, line, reason in viols:
        all_violations.append((filepath, lineno, line, reason))

if all_violations:
    for filepath, lineno, line, reason in all_violations[:10]:
        print(
            f'FAIL: CHECK 13 — secrets gate: {filepath}:{lineno}: '
            f'[{reason}] {line[:80]}',
            file=sys.stderr
        )
    if len(all_violations) > 10:
        print(f'FAIL: CHECK 13 — ... and {len(all_violations) - 10} more violation(s)',
              file=sys.stderr)
    print(
        'NOTE: If this is a false positive, add a reviewed entry to '
        'tools/secrets-allowlist.txt with a reason comment. '
        'NEVER allowlist a real secret — rotate it immediately.',
        file=sys.stderr
    )
    sys.exit(1)
else:
    scanned = len(files_to_scan)
    print(f'PASS: CHECK 13 — secrets gate: no secret-shaped strings in '
          f'{scanned} tracked file(s)')
    sys.exit(0)
SECRETS_PYEOF
CHECK13_EXIT=$?
if [ "$CHECK13_EXIT" -ne 0 ]; then
    FAIL_COUNT=$((FAIL_COUNT + 1))
fi
fi  # end python3/git availability check

# ---------------------------------------------------------------------------
# CHECK 14: HOOK-LIVENESS (slice #849 / #849)
#   Ensures the hook-layer dark-detection check is wired and exits cleanly.
#   WARN is allowed (hook-fires.jsonl may not exist in CI); only FAIL trips this.
#   Delegates to registry CLI (single-source per ADR-0064 D3).
# ---------------------------------------------------------------------------
echo "--- CHECK 14: HOOK-LIVENESS dark-detection check ---"
if ! command -v python3 > /dev/null 2>&1 || [ ! -f "dashboard/health.py" ]; then
    echo "SKIP: CHECK 14 — python3 or dashboard/health.py not available (soft-degrade)"
else
    CHECK14_OUTPUT=$(python3 dashboard/health.py --check HOOK-LIVENESS 2>&1)
    CHECK14_EXIT=$?
    if [ "$CHECK14_EXIT" -eq 0 ]; then
        pass "CHECK 14 (HOOK-LIVENESS): $CHECK14_OUTPUT"
    else
        fail "CHECK 14 (HOOK-LIVENESS): $CHECK14_OUTPUT"
    fi
fi

# ---------------------------------------------------------------------------
# CHECK 15: PROOF-INTEGRITY (slice #839 / ADR-0070 D5)
#   DOM-attestation integrity for browser-route proof artifacts.
#   WARN is allowed (may have no qualifying data yet); only FAIL trips this.
#   Delegates to registry CLI (single-source per ADR-0064 D3).
# ---------------------------------------------------------------------------
echo "--- CHECK 15: PROOF-INTEGRITY DOM-attestation check ---"
if ! command -v python3 > /dev/null 2>&1 || [ ! -f "dashboard/health.py" ]; then
    echo "SKIP: CHECK 15 — python3 or dashboard/health.py not available (soft-degrade)"
else
    CHECK15_OUTPUT=$(python3 dashboard/health.py --check PROOF-INTEGRITY 2>&1)
    CHECK15_EXIT=$?
    if [ "$CHECK15_EXIT" -eq 0 ]; then
        pass "CHECK 15 (PROOF-INTEGRITY): $CHECK15_OUTPUT"
    else
        fail "CHECK 15 (PROOF-INTEGRITY): $CHECK15_OUTPUT"
    fi
fi

# ---------------------------------------------------------------------------
# CHECK 16: META-TRIPWIRE (slice #840 / ADR-0070 D4)
#   Guardrail-machinery promotion meta-tripwire.
#   WARN is allowed (day-one: no promotions yet); only FAIL trips this.
#   FAIL means: unpromoted batch touches guardrail-machinery path(s) without
#   a promotion-ack — must be acknowledged before promoting to main.
#   Delegates to registry CLI (single-source per ADR-0064 D3).
# ---------------------------------------------------------------------------
echo "--- CHECK 16: META-TRIPWIRE guardrail-machinery check ---"
if ! command -v python3 > /dev/null 2>&1 || [ ! -f "dashboard/health.py" ]; then
    echo "SKIP: CHECK 16 — python3 or dashboard/health.py not available (soft-degrade)"
else
    CHECK16_OUTPUT=$(python3 dashboard/health.py --check META-TRIPWIRE 2>&1)
    CHECK16_EXIT=$?
    if [ "$CHECK16_EXIT" -eq 0 ]; then
        pass "CHECK 16 (META-TRIPWIRE): $CHECK16_OUTPUT"
    else
        fail "CHECK 16 (META-TRIPWIRE): $CHECK16_OUTPUT"
    fi
fi

# ---------------------------------------------------------------------------
# CHECK 17: gen_rules.py regen-clean guard (PRD #937 slice #940 / ADR-0073 D3)
#   Runs gen_rules.py --check: diffs .claude/generated/_global.md (GLOBAL
#   @import target, relocated out of .claude/rules/ per slice #945 to avoid
#   double-load) + AREA .claude/rules/<scope>.md files against a fresh
#   generation; also verifies CLAUDE.md contains the '@.claude/generated/
#   _global.md' import line. Exits non-zero if any output is stale or missing.
#   Soft-degrades if python3 or tools/gen_rules.py is unavailable.
# ---------------------------------------------------------------------------
echo "--- CHECK 17: gen_rules.py regen-clean (.claude/generated/ + @import line) ---"
if ! command -v python3 > /dev/null 2>&1 || [ ! -f "tools/gen_rules.py" ]; then
    echo "SKIP: CHECK 17 — python3 or tools/gen_rules.py not available (soft-degrade)"
else
    CHECK17_OUTPUT=$(python3 tools/gen_rules.py --check 2>&1)
    CHECK17_EXIT=$?
    if [ "$CHECK17_EXIT" -eq 0 ]; then
        pass "CHECK 17: gen_rules.py outputs are up-to-date (_global.md + area files + @import)"
    else
        fail "CHECK 17: gen_rules.py outputs stale — run 'python3 tools/gen_rules.py' and commit"
        echo "$CHECK17_OUTPUT" >&2
    fi
fi

# ---------------------------------------------------------------------------
# CHECK 18: AS-AUDIT subagent-prompt quality check (PRD #919 slice #921)
#   Runs the AS-* audit-subagents checks across all .claude/agents/*.md via
#   the health registry.  WARN is allowed (advisory quality signal); only
#   FAIL trips CI.  Replaces the manual /audit-subagents skill on every PR.
#   Delegates to registry CLI (single-source per ADR-0064 D3).
# ---------------------------------------------------------------------------
echo "--- CHECK 18: AS-AUDIT subagent-prompt quality check ---"
if ! command -v python3 > /dev/null 2>&1 || [ ! -f "dashboard/health.py" ]; then
    echo "SKIP: CHECK 18 — python3 or dashboard/health.py not available (soft-degrade)"
else
    CHECK18_OUTPUT=$(python3 dashboard/health.py --check AS-AUDIT 2>&1)
    CHECK18_EXIT=$?
    if [ "$CHECK18_EXIT" -eq 0 ]; then
        pass "CHECK 18 (AS-AUDIT): $CHECK18_OUTPUT"
    else
        fail "CHECK 18 (AS-AUDIT): $CHECK18_OUTPUT"
    fi
fi

# ---------------------------------------------------------------------------
# CHECK 19: Slicer-provenance guard (PRD #919 slice #922; lanes per slice #1067)
#   Queries open `slice`-labeled GitHub issues and flags any that lack the
#   `Slicer-provenance:` trailer written by `/to-issues` (rule #16 + linear
#   flow convention).  Enforces the mandatory slicer step: PRD-decomposition
#   slices MUST be created via /to-issues (slicer + slicer-critic), not
#   hand-crafted via gh.  EXEMPTION (narrow): slices carrying the
#   `root-cause` label are the rule-#13 lane, which never passes through the
#   slicer BY DESIGN, and are exempt from the trailer requirement — no other
#   label grants exemption.  Scope: `slice`-labeled issues only —
#   trivial-lane (I3) PRs are never affected (no slice issue exists for
#   them).  Soft-degrades if gh is missing or unauthenticated in CI.
#   Calls the standalone script directly (NOT via --check <ID>) so PARITY
#   is unaffected and no false registry-orphan is created.
# ---------------------------------------------------------------------------
echo "--- CHECK 19: slicer-provenance guard ---"
if ! command -v python3 > /dev/null 2>&1 || [ ! -f "tools/check-slicer-provenance.py" ]; then
    echo "SKIP: CHECK 19 — python3 or tools/check-slicer-provenance.py not available (soft-degrade)"
else
    CHECK19_OUTPUT=$(python3 tools/check-slicer-provenance.py 2>&1)
    CHECK19_EXIT=$?
    if [ "$CHECK19_EXIT" -eq 0 ]; then
        pass "CHECK 19 (slicer-provenance): $CHECK19_OUTPUT"
    else
        fail "CHECK 19 (slicer-provenance): $CHECK19_OUTPUT"
    fi
fi

# ---------------------------------------------------------------------------
# CHECK 20: gen_rules.py + gen_repo_map.py regen-clean (slice #940 / ADR-0073)
#   Confirms that:
#   (a) .claude/generated/_global.md is up-to-date with gen_rules.py output,
#       AND CLAUDE.md contains the @import line for it (ADR-0073 D1). Lives
#       in .claude/generated/, not .claude/rules/, to avoid double-load
#       (slice #945).
#   (b) .claude/generated/_repo-map.md is up-to-date with gen_repo_map.py
#       output.
#   Runs both generators in --check mode; exits non-zero on any stale output.
#   Soft-degrades if python3 or the generator scripts are unavailable.
# ---------------------------------------------------------------------------
echo "--- CHECK 20: CLAUDE.md generated-region regen-clean (ADR-0073) ---"
if ! command -v python3 > /dev/null 2>&1 || [ ! -f "tools/gen_rules.py" ]; then
    echo "SKIP: CHECK 20 — python3 or tools/gen_rules.py not available (soft-degrade)"
elif [ ! -f "CLAUDE.md" ]; then
    fail "CHECK 20: CLAUDE.md not found"
else
    CHECK20_FAIL=0
    # (a) gen_rules.py --check: verifies _global.md + area files + @import line
    CHECK20_RULES_OUTPUT=$(python3 tools/gen_rules.py --check 2>&1)
    CHECK20_RULES_EXIT=$?
    if [ "$CHECK20_RULES_EXIT" -eq 0 ]; then
        pass "CHECK 20a: gen_rules.py outputs are up-to-date (_global.md + area files)"
    else
        fail "CHECK 20a: gen_rules.py outputs stale — run 'python3 tools/gen_rules.py' and commit"
        echo "$CHECK20_RULES_OUTPUT" >&2
        CHECK20_FAIL=1
    fi
    # (b) gen_repo_map.py --check: verifies _repo-map.md freshness
    if [ -f "tools/gen_repo_map.py" ]; then
        CHECK20_MAP_OUTPUT=$(python3 tools/gen_repo_map.py --check 2>&1)
        CHECK20_MAP_EXIT=$?
        if [ "$CHECK20_MAP_EXIT" -eq 0 ]; then
            pass "CHECK 20b: gen_repo_map.py output is up-to-date (_repo-map.md)"
        else
            fail "CHECK 20b: _repo-map.md stale — run 'python3 tools/gen_repo_map.py' and commit"
            echo "$CHECK20_MAP_OUTPUT" >&2
            CHECK20_FAIL=1
        fi
    else
        echo "SKIP: CHECK 20b — tools/gen_repo_map.py not found (soft-degrade)"
    fi
fi

# ---------------------------------------------------------------------------
# CHECK 21: DEPLOY-HANDSHAKE self-test backstop (PRD #1075 slice #1079)
#   Runs tools/deploy-handshake.sh --self-test — the CI-SAFE internal-
#   consistency leg only (script parses, .claude/hooks/ non-empty,
#   .claude/settings.json valid JSON, .githooks/ present). Deliberately does
#   NOT run the full local-leg comparison (running-vs-deployed-branch content
#   hash, attached/detached HEAD) — GitHub Actions checks out PRs in DETACHED
#   HEAD by design, so that comparison would always false-positive in CI.
#   The real deploy-gap invariant is asserted by the LOCAL leg only, against
#   the real ROOT checkout (operator-run; see tools/repair-topology.md).
#   Soft-degrades if bash or the script is unavailable.
# ---------------------------------------------------------------------------
echo "--- CHECK 21: DEPLOY-HANDSHAKE self-test (CI-safe internal-consistency leg) ---"
if ! command -v bash > /dev/null 2>&1 || [ ! -f "tools/deploy-handshake.sh" ]; then
    echo "SKIP: CHECK 21 — bash or tools/deploy-handshake.sh not available (soft-degrade)"
else
    CHECK21_OUTPUT=$(bash tools/deploy-handshake.sh --self-test 2>&1)
    CHECK21_EXIT=$?
    if [ "$CHECK21_EXIT" -eq 0 ]; then
        pass "CHECK 21 (DEPLOY-HANDSHAKE self-test): $CHECK21_OUTPUT"
    else
        fail "CHECK 21 (DEPLOY-HANDSHAKE self-test): $CHECK21_OUTPUT"
    fi
fi

# ---------------------------------------------------------------------------
# CHECK 22: RECORD-VS-GH — recorded pr_merged spans vs gh's merged-PR ground
# truth on develop, promoted into CI per PRD #1127 §2 criterion 11a / slice
# #1136 (ADR-0076 D6: the reconciler family "promoted into CI" contributes to
# the recorded-evidence trigger for the deferred sequencing-as-code
# executor). Delegated to health.py registry — same shape as CHECK 4/5;
# health.py's own gh-unavailable handling already degrades to WARN (exit 0)
# so no separate soft-degrade branch is needed here beyond python3/health.py
# availability.
# ---------------------------------------------------------------------------
echo "--- CHECK 22: RECORD-VS-GH — recorded pr_merged spans vs gh ground truth ---"
if ! command -v python3 > /dev/null 2>&1 || [ ! -f "dashboard/health.py" ]; then
    echo "SKIP: CHECK 22 — python3 or dashboard/health.py not available (soft-degrade)"
else
    CHECK22_OUTPUT=$(python3 dashboard/health.py --check RECORD-VS-GH 2>&1)
    CHECK22_EXIT=$?
    if [ "$CHECK22_EXIT" -eq 0 ]; then
        pass "CHECK 22 (RECORD-VS-GH): $CHECK22_OUTPUT"
    else
        fail "CHECK 22 (RECORD-VS-GH): $CHECK22_OUTPUT"
    fi
fi

# ---------------------------------------------------------------------------
# CHECK 23: verdict-presence — every recently-merged develop PR carries a
# reviewer `VERDICT: APPROVE` comment (PRD #1214 criterion 3c / ADR-0080 D1's
# supersession of ADR-0075 D6, slice #1219). This is the D6-supersession's
# compensating control: unlike the ledger-side MERGED-WITHOUT-VERDICT
# reconciler (WARN-only in a fresh checkout with no local trace history),
# this check reads GROUND TRUTH a fresh hosted checkout genuinely has (gh PR
# comments), so a violation REALLY FAILS CI, not WARN. Mechanically bounded
# window (last 20 merged develop PRs, matching the `gh pr list --base
# develop --state merged --limit 20` convention CHECK 22/RECORD-VS-GH
# already use) — NO special-case grandfather list. GH_TOKEN +
# `pull-requests: read` + `issues: read` are already wired in ci.yml
# (CHECK-19 precedent); soft-degrades to SKIP when gh is unavailable/
# unauthenticated for local dev runs without a token.
# ---------------------------------------------------------------------------
echo "--- CHECK 23: verdict-presence — merged develop PRs carry VERDICT: APPROVE ---"
if ! command -v python3 > /dev/null 2>&1 || [ ! -f "tools/check-verdict-presence.py" ]; then
    echo "SKIP: CHECK 23 — python3 or tools/check-verdict-presence.py not available (soft-degrade)"
else
    CHECK23_OUTPUT=$(python3 tools/check-verdict-presence.py --limit 20 2>&1)
    CHECK23_EXIT=$?
    if [ "$CHECK23_EXIT" -eq 0 ]; then
        pass "CHECK 23 (verdict-presence): $CHECK23_OUTPUT"
    else
        fail "CHECK 23 (verdict-presence): $CHECK23_OUTPUT"
    fi
fi

# ---------------------------------------------------------------------------
# CHECK 24: queue-drain mode enforcement (ADR-0085 D6 / PRD #1326 §2 criteria
# 21-22, slice #1329). Two legs:
#   (a) DRAIN-LEDGER delegation — health-registry form, identical in shape to
#       CHECK 22/RECORD-VS-GH. Deliberately NOT a `grep DRAIN tools/ci-checks.sh`
#       self-match (prd-critic recommendation 7: a check whose only assertion is
#       its own banner text is self-satisfying); this binds the criterion to the
#       row's BEHAVIOUR. The row is offline-decidable and WARNs (exit 0) when no
#       ledger exists, so a fresh checkout passes honestly rather than by luck.
#   (b) grill-me `/goal`-absence — the handoff summary must name the real
#       drain entry mode, not the aspirational "/goal-style" placeholder that
#       described a skill which was never built.
# ---------------------------------------------------------------------------
echo "--- CHECK 24: queue-drain mode (DRAIN-LEDGER delegation + /goal absence) ---"
if ! command -v python3 > /dev/null 2>&1 || [ ! -f "dashboard/health.py" ]; then
    echo "SKIP: CHECK 24a — python3 or dashboard/health.py not available (soft-degrade)"
else
    CHECK24_OUTPUT=$(python3 dashboard/health.py --check DRAIN-LEDGER 2>&1)
    CHECK24_EXIT=$?
    if [ "$CHECK24_EXIT" -eq 0 ]; then
        pass "CHECK 24a (DRAIN-LEDGER): $CHECK24_OUTPUT"
    else
        fail "CHECK 24a (DRAIN-LEDGER): $CHECK24_OUTPUT"
    fi
fi

GRILL_SKILL=".claude/skills/grill-me/SKILL.md"
if [ ! -f "$GRILL_SKILL" ]; then
    fail "CHECK 24b: $GRILL_SKILL not found"
else
    GOAL_REFS=$(grep -c '/goal' "$GRILL_SKILL" || true)
    if [ "$GOAL_REFS" -eq 0 ]; then
        pass "CHECK 24b: grill-me handoff carries no '/goal' placeholder (count=0)"
    else
        fail "CHECK 24b: grill-me SKILL.md still references '/goal' on $GOAL_REFS line(s) — name the /ship queue-drain entry mode instead (ADR-0085 D6)"
    fi
fi

# ---------------------------------------------------------------------------
# CHECK 25: Portability scrub (ADR-0084 D1)
#
# Repository is public. Tracked text must not carry machine-local absolute
# path detail: Class A (an OS-account segment inside a Users path) or
# Class B (a local clone root, either the native drive-letter form or the
# MSYS/Git-Bash mount-form Git Bash substitutes for it).
#
# Scope: every tracked file (git ls-files --cached), tree-wide, mirroring
#        CHECK 13's actual code (not its own stale diff-scoped comments —
#        see #1404). Excludes qa-proof/** (evidence), decisions/** (immutable
#        ADR archive), and tools/portability-allowlist.txt itself (excluded
#        by construction so it cannot trip the check it feeds).
# Allowlist: tools/portability-allowlist.txt — path+literal-substring
#        anchored, never line-number-anchored, never a whole-file exemption.
# Per-check aggregation: FAIL_COUNT incremented on FAIL only.
# ---------------------------------------------------------------------------
echo "--- CHECK 25: portability scrub (ADR-0084 D1) ---"
if ! command -v python3 > /dev/null 2>&1; then
    echo "SKIP: CHECK 25 — python3 not available (soft-degrade)"
elif ! command -v git > /dev/null 2>&1; then
    echo "SKIP: CHECK 25 — git not available (soft-degrade)"
else
python3 - << 'PORTABILITY_PYEOF'
import re, sys, os, subprocess

# Class A/B patterns (ADR-0084 D1), exactly as measured and shipped.
CLASS_A = re.compile(r'\b[Uu]sers[\\/][A-Za-z_][A-Za-z0-9_.-]{2,}')
CLASS_B_ARM1 = re.compile(r'\b[A-Za-z]:[\\/][A-Za-z_][A-Za-z0-9_ .-]{2,}[\\/]')
CLASS_B_ARM2 = re.compile(r'(^|[^A-Za-z0-9_])/[A-Za-z]/[A-Za-z_][A-Za-z0-9_.-]{2,}/')
_PATTERNS = (CLASS_A, CLASS_B_ARM1, CLASS_B_ARM2)

EXCLUDE_PREFIXES = ('qa-proof/', 'decisions/')
ALLOWLIST_PATH = 'tools/portability-allowlist.txt'
EXCLUDE_FILES = {ALLOWLIST_PATH}

# Load allowlist: path|literal substring|reason, path+substring anchored.
_allowlist = {}
if os.path.isfile(ALLOWLIST_PATH):
    with open(ALLOWLIST_PATH, 'r', encoding='utf-8', errors='replace') as _f:
        for _line in _f:
            _line = _line.rstrip('\n')
            if not _line.strip() or _line.lstrip().startswith('#'):
                continue
            _parts = _line.split('|', 2)
            if len(_parts) < 2:
                continue
            _path, _substr = _parts[0], _parts[1]
            _allowlist.setdefault(_path, []).append(_substr)

def is_allowlisted(filepath, line_text):
    for substr in _allowlist.get(filepath, []):
        if substr in line_text:
            return True
    return False


try:
    result = subprocess.run(
        ['git', 'ls-files', '--cached'],
        capture_output=True, text=True, encoding='utf-8', errors='replace'
    )
    tracked_files = [f.strip() for f in result.stdout.splitlines() if f.strip()]
except Exception as e:
    print(f'SKIP: CHECK 25 — git ls-files failed: {e}')
    sys.exit(0)

subject_files = [
    f for f in tracked_files
    if not f.startswith(EXCLUDE_PREFIXES) and f not in EXCLUDE_FILES
    and os.path.isfile(f)
]

_BINARY_EXTS = {'.png', '.jpg', '.jpeg', '.gif', '.ico', '.pdf',
                '.zip', '.tar', '.gz', '.whl', '.pyc', '.so', '.dll'}

violations = []
for filepath in subject_files:
    _, ext = os.path.splitext(filepath.lower())
    if ext in _BINARY_EXTS:
        continue
    try:
        with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
            for lineno, line in enumerate(f, 1):
                line_stripped = line.rstrip('\n')
                if not any(pat.search(line_stripped) for pat in _PATTERNS):
                    continue
                if is_allowlisted(filepath, line_stripped):
                    continue
                violations.append((filepath, lineno, line_stripped[:120]))
    except (OSError, UnicodeDecodeError):
        continue

if violations:
    for filepath, lineno, line in violations[:10]:
        print(
            f'FAIL: CHECK 25 — portability scrub: {filepath}:{lineno}: {line[:80]}',
            file=sys.stderr
        )
    if len(violations) > 10:
        print(f'FAIL: CHECK 25 — ... and {len(violations) - 10} more violation(s)',
              file=sys.stderr)
    print(
        'NOTE: remove the machine-local path detail, or if this line documents '
        'the hazard rather than leaking a real path, add a path+literal-substring '
        'entry to tools/portability-allowlist.txt with a reason comment '
        '(ADR-0084 D1).',
        file=sys.stderr
    )
    sys.exit(1)
else:
    print(f'PASS: CHECK 25 — portability scrub: no Class A/B path leaks in '
          f'{len(subject_files)} tracked file(s)')
    sys.exit(0)
PORTABILITY_PYEOF
CHECK25_EXIT=$?
if [ "$CHECK25_EXIT" -ne 0 ]; then
    FAIL_COUNT=$((FAIL_COUNT + 1))
fi
fi  # end python3/git availability check

# ---------------------------------------------------------------------------
# CHECK 26: qa-proof/ shape (ADR-0084 D2)
#
# The sanctioned shape for tracked files under qa-proof/ is
# qa-proof/<prd-num>/** where <prd-num> is a bare numeric segment
# (ADR-0049 D3's own examples). Nine pre-existing files predate this
# definition and are grandfathered by name (D2) — no other exceptions.
# ---------------------------------------------------------------------------
echo "--- CHECK 26: qa-proof/ shape (ADR-0084 D2) ---"
if ! command -v python3 > /dev/null 2>&1; then
    echo "SKIP: CHECK 26 — python3 not available (soft-degrade)"
elif ! command -v git > /dev/null 2>&1; then
    echo "SKIP: CHECK 26 — git not available (soft-degrade)"
else
python3 - << 'QAPROOFSHAPE_PYEOF'
import re, sys, subprocess

SHAPE_RE = re.compile(r'^qa-proof/[0-9]+/.+')

GRANDFATHERED = {
    'qa-proof/design/judges-and-verdicts.md',
    'qa-proof/design/winner-architecture.md',
    'qa-proof/design/winner-staging.md',
    'qa-proof/forensics/code-health-auditor.md',
    'qa-proof/forensics/event-pipeline-autopsy.md',
    'qa-proof/forensics/github-trail-assessor.md',
    'qa-proof/forensics/process-retrospective.md',
    'qa-proof/forensics/purpose-archaeologist.md',
    'qa-proof/forensics/topology-ontologist.md',
}

try:
    result = subprocess.run(
        ['git', 'ls-files', '--cached'],
        capture_output=True, text=True, encoding='utf-8', errors='replace'
    )
    tracked_files = [f.strip() for f in result.stdout.splitlines() if f.strip()]
except Exception as e:
    print(f'SKIP: CHECK 26 — git ls-files failed: {e}')
    sys.exit(0)

qa_proof_files = [f for f in tracked_files if f.startswith('qa-proof/')]

violations = [
    f for f in qa_proof_files
    if not SHAPE_RE.match(f) and f not in GRANDFATHERED
]

if violations:
    for f in violations[:10]:
        print(f'FAIL: CHECK 26 — qa-proof/ shape: {f} does not match '
              f'qa-proof/<prd-num>/** and is not on the grandfather list',
              file=sys.stderr)
    if len(violations) > 10:
        print(f'FAIL: CHECK 26 — ... and {len(violations) - 10} more violation(s)',
              file=sys.stderr)
    print(
        'NOTE: move the file under qa-proof/<prd-num>/, or if this is a '
        'pre-existing file that predates ADR-0084 D2, it must be added to '
        'the fixed grandfather list by a superseding ADR — CHECK 26 does '
        'not grow its own grandfather list.',
        file=sys.stderr
    )
    sys.exit(1)
else:
    print(f'PASS: CHECK 26 — qa-proof/ shape: {len(qa_proof_files)} tracked '
          f'file(s) all conform to qa-proof/<prd-num>/** or are grandfathered')
    sys.exit(0)
QAPROOFSHAPE_PYEOF
CHECK26_EXIT=$?
if [ "$CHECK26_EXIT" -ne 0 ]; then
    FAIL_COUNT=$((FAIL_COUNT + 1))
fi
fi  # end python3/git availability check

# ---------------------------------------------------------------------------
# CHECK 27: closed beacon-status schema across the hook fleet (ADR-0083
# D1/D2, HOK-008, slice #1312)
#
# Every record a hook script appends to hook-fires.jsonl must carry a
# "status" key drawn from the closed set {attempt, ok, ERROR}, plus exactly
# one named grandfather (session-start.sh's "python3_selftest" self-test
# beacon, ADR-0083 D2). This check resolves BOTH direct literal status
# values (bash printf format strings; python dict literals) AND indirect
# values reached only through a `beacon(status, reason=None)`-style Python
# helper's call graph (log-tool-event.sh, session-start.sh) -- it traces
# each hook-fires.jsonl write site back to its actual literal rather than
# pattern-matching on the "hook" key alone, so a dict that shares a "hook"
# key but writes to a DIFFERENT file (the reject_obj construct, which
# targets workflow-events.rejects.jsonl) is never false-positived.
#
# Subject set (ADR-0083 D3 / VER-009 -- a check's subject set is defined,
# not assumed): every ".claude/hooks/*.sh" AND every ".claude/hooks/*.py",
# both globbed at run time -- never a single hard-coded filename, so a
# second .py helper is covered the day it lands, not the day someone
# remembers to add it here.
#
# A hook-fires.jsonl append the bash leg cannot resolve to the recognized
# printf shape is a LOUD violation, not a silent zero-contribution skip --
# the "check that cannot redden on its own primary defect class" failure
# ADR-0083 D2 names explicitly. Known blind spot: this still cannot see a
# beacon written via a shell builtin/construct with no ">>" token at all
# (e.g. `exec 3>>file; printf ... >&3`) -- see the CI-check comment on
# UNRECOGNIZED_APPEND_RE below and the PR's CONCERNS note.
# ---------------------------------------------------------------------------
echo "--- CHECK 27: closed beacon-status schema (ADR-0083 D1/D2) ---"
if ! command -v python3 > /dev/null 2>&1; then
    echo "SKIP: CHECK 27 — python3 not available (soft-degrade)"
else
python3 - << 'BEACONSCHEMA_PYEOF'
import ast, glob, os, re, sys

ALLOWED = {"attempt", "ok", "ERROR"}
GRANDFATHER = {("session-start.sh", "python3_selftest")}

violations = []

# --- bash-side: printf literals that append to hook-fires.jsonl ------------
PRINTF_RE = re.compile(r"printf\s+'(\{.*?\})\\n'.*>>\s*\"[^\"]*hook-fires\.jsonl\"")
STATUS_RE = re.compile(r'"status"\s*:\s*"([^"]*)"')
HOOK_RE = re.compile(r'"hook"\s*:\s*"([^"]*)"')
# A write site the check can SEE (append-redirects into hook-fires.jsonl) but
# cannot resolve through PRINTF_RE's recognized shape. Used below to turn a
# beacon written any other way (double-quoted printf, `echo`, an unquoted
# path, ...) into a loud violation instead of a silent zero-contribution skip
# -- the "check that cannot redden on its own primary defect class" failure
# ADR-0083 D2 names explicitly.
UNRECOGNIZED_APPEND_RE = re.compile(r">>\s*[\"']?[^\"'\s]*hook-fires\.jsonl")


def join_continuations(content):
    """Join backslash-continued physical lines into logical lines so a printf
    and its `>>` redirect are matched within ONE statement -- never bleeding
    forward into an unrelated LATER printf's redirect (a non-greedy DOTALL
    regex would still scan arbitrarily far forward for the next '>>' across
    statement boundaries if not confined this way)."""
    out = []
    buf = ""
    start = 1
    for idx, raw_line in enumerate(content.split("\n"), start=1):
        line = raw_line.rstrip("\r")
        if buf == "":
            start = idx
        if line.endswith("\\") and not line.endswith("\\\\"):
            buf += line[:-1] + " "
        else:
            buf += line
            out.append((start, buf))
            buf = ""
    if buf:
        out.append((start, buf))
    return out


hook_files = sorted(glob.glob(".claude/hooks/*.sh"))

for path in hook_files:
    base = os.path.basename(path)
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    for line_no, logical_line in join_continuations(content):
        m = PRINTF_RE.search(logical_line)
        if not m:
            stripped = logical_line.strip()
            if (not stripped.startswith("#")
                    and UNRECOGNIZED_APPEND_RE.search(logical_line)):
                violations.append(
                    f"{path}:{line_no} -- logical line appends to hook-fires.jsonl but "
                    f"does not match the recognized printf '{{...}}\\n' ... >> "
                    f'"...hook-fires.jsonl" beacon shape (extend CHECK 27\'s PRINTF_RE or '
                    f"rewrite the emitter in the canonical shape)"
                )
            continue
        literal = m.group(1)
        status_m = STATUS_RE.search(literal)
        hook_m = HOOK_RE.search(literal)
        hookname = hook_m.group(1) if hook_m else "?"
        if not status_m:
            violations.append(
                f'{path}:{line_no} -- beacon for hook={hookname!r} has no "status" key'
            )
            continue
        value = status_m.group(1)
        if value in ALLOWED or (base, value) in GRANDFATHER:
            continue
        violations.append(
            f"{path}:{line_no} -- beacon for hook={hookname!r} has status={value!r}, "
            f"outside the closed set {sorted(ALLOWED)} and not the named grandfather"
        )

# --- python-side: AST-based dict-literal + beacon() call-graph resolution --


def find_pyeof_blocks(content):
    """Extract (start_line, source) for every `<<'PYEOF' ... \\nPYEOF` heredoc
    block embedded in a bash hook script."""
    blocks = []
    lines = content.splitlines()
    i = 0
    while i < len(lines):
        if re.search(r"<<\s*'PYEOF'", lines[i]):
            start = i + 1
            body = []
            j = start
            while j < len(lines) and lines[j].strip() != "PYEOF":
                body.append(lines[j])
                j += 1
            blocks.append((start + 1, "\n".join(body)))
            i = j
        i += 1
    return blocks


def check_python_source(source, filelabel, line_offset, base_filename):
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        violations.append(f"{filelabel} -- python parse error: {exc}")
        return

    parent = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parent[child] = node

    def enclosing_function(node):
        cur = node
        while cur in parent:
            cur = parent[cur]
            if isinstance(cur, ast.FunctionDef):
                return cur
        return None

    # Variable resolution is SCOPE-KEYED (function-node-or-None, name) --
    # two same-named variables in different scopes (e.g. a module-level
    # `line = json.dumps(event, ...)` alongside a `beacon()`-local
    # `line = json.dumps(obj, ...)`) must never be conflated.
    var_dict = {}
    var_dumps_dict = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            scope_key = enclosing_function(node)
            val = node.value
            if isinstance(val, ast.Dict):
                var_dict[(scope_key, name)] = val
            elif (isinstance(val, ast.Call) and isinstance(val.func, ast.Attribute)
                  and val.func.attr == "dumps" and val.args):
                inner = val.args[0]
                if isinstance(inner, ast.Dict):
                    var_dumps_dict[(scope_key, name)] = inner
                elif isinstance(inner, ast.Name):
                    resolved = var_dict.get((scope_key, inner.id))
                    if resolved is not None:
                        var_dumps_dict[(scope_key, name)] = resolved

    def resolve_write_arg(node, scope_key):
        if isinstance(node, ast.BinOp):
            left = resolve_write_arg(node.left, scope_key)
            return left if left is not None else resolve_write_arg(node.right, scope_key)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "dumps":
            if node.args:
                inner = node.args[0]
                if isinstance(inner, ast.Dict):
                    return inner
                if isinstance(inner, ast.Name):
                    return var_dict.get((scope_key, inner.id))
            return None
        if isinstance(node, ast.Name):
            return var_dumps_dict.get((scope_key, node.id)) or var_dict.get((scope_key, node.id))
        return None

    def dict_value(d, key):
        for k, v in zip(d.keys, d.values):
            if isinstance(k, ast.Constant) and k.value == key:
                return v
        return None

    def resolve_status_values(value_node, dict_node):
        """Direct literal -> [value]. Name-valued (the beacon() helper
        pattern) -> resolve via call graph: find the enclosing FunctionDef's
        matching parameter, then every Call elsewhere invoking that function
        by name, collecting the literal argument at that position/keyword."""
        if isinstance(value_node, ast.Constant) and isinstance(value_node.value, str):
            return [value_node.value], False
        if isinstance(value_node, ast.Name):
            func = enclosing_function(dict_node)
            if func is None:
                return [], True
            param_name = value_node.id
            arg_names = [a.arg for a in func.args.args]
            param_index = arg_names.index(param_name) if param_name in arg_names else None
            values = []
            unresolved = False
            for call in ast.walk(tree):
                if isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == func.name:
                    found = None
                    if param_index is not None and len(call.args) > param_index:
                        found = call.args[param_index]
                    else:
                        for kw in call.keywords:
                            if kw.arg == param_name:
                                found = kw.value
                    if found is None:
                        continue
                    if isinstance(found, ast.Constant) and isinstance(found.value, str):
                        values.append(found.value)
                    else:
                        unresolved = True
            return values, unresolved
        return [], True

    for node in ast.walk(tree):
        if not isinstance(node, ast.With):
            continue
        for item in node.items:
            call = item.context_expr
            if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == "open"):
                continue
            opens_hookfires = False
            for a in list(call.args) + [kw.value for kw in call.keywords]:
                if isinstance(a, ast.Constant) and isinstance(a.value, str) and "hook-fires.jsonl" in a.value:
                    opens_hookfires = True
                if isinstance(a, ast.Call) and isinstance(a.func, ast.Attribute) and a.func.attr == "join":
                    for arg2 in a.args:
                        if isinstance(arg2, ast.Constant) and isinstance(arg2.value, str) and "hook-fires.jsonl" in arg2.value:
                            opens_hookfires = True
            if not opens_hookfires:
                continue
            var_name = item.optional_vars.id if isinstance(item.optional_vars, ast.Name) else None
            if not var_name:
                continue
            scope_key = enclosing_function(node)
            for wnode in ast.walk(node):
                if (isinstance(wnode, ast.Call) and isinstance(wnode.func, ast.Attribute)
                        and wnode.func.attr == "write" and isinstance(wnode.func.value, ast.Name)
                        and wnode.func.value.id == var_name):
                    write_arg = wnode.args[0] if wnode.args else None
                    dict_lit = resolve_write_arg(write_arg, scope_key) if write_arg is not None else None
                    line_no = wnode.lineno + line_offset - 1
                    if dict_lit is None:
                        violations.append(
                            f"{filelabel}:{line_no} -- hook-fires.jsonl write() payload could not be "
                            f"statically resolved to a dict literal (extend CHECK 27 or use a literal "
                            f"dict / beacon()-style call)"
                        )
                        continue
                    status_val_node = dict_value(dict_lit, "status")
                    if status_val_node is None:
                        violations.append(f'{filelabel}:{line_no} -- beacon dict has no "status" key')
                        continue
                    values, unresolved = resolve_status_values(status_val_node, dict_lit)
                    if unresolved:
                        violations.append(
                            f'{filelabel}:{line_no} -- beacon "status" value could not be fully '
                            f"resolved through its call graph (a non-literal argument reaches it)"
                        )
                        continue
                    for v in values:
                        if v in ALLOWED or (base_filename, v) in GRANDFATHER:
                            continue
                        violations.append(
                            f"{filelabel}:{line_no} -- beacon status={v!r} outside the closed set "
                            f"{sorted(ALLOWED)} and not the named grandfather"
                        )


# Subject set is DERIVED from the glob, not asserted by a hard-coded single
# path (ADR-0083 D3 / VER-009: "a check's subject set is defined, not
# assumed"). D2's Enforcement clause scopes this leg to
# ".claude/hooks/*.sh" AND ".claude/hooks/*.py" -- glob both the same way so
# a second .py helper landing tomorrow is covered on day one, not silently
# under-scored the way the single hard-coded literal was.
py_files = sorted(glob.glob(".claude/hooks/*.py"))
for py_path in py_files:
    with open(py_path, "r", encoding="utf-8") as f:
        check_python_source(f.read(), py_path, 1, os.path.basename(py_path))

for path in hook_files:
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    for start_line, body in find_pyeof_blocks(content):
        check_python_source(body, f"{path} (heredoc)", start_line, os.path.basename(path))

if violations:
    for v in violations[:20]:
        print(f"FAIL: CHECK 27 — {v}", file=sys.stderr)
    if len(violations) > 20:
        print(f"FAIL: CHECK 27 — ... and {len(violations) - 20} more violation(s)", file=sys.stderr)
    sys.exit(1)
else:
    print(
        f"PASS: CHECK 27 — closed beacon-status schema: {len(hook_files)} hook script(s) "
        f"+ {len(py_files)} python hook helper(s) conform to the closed status set"
    )
    sys.exit(0)
BEACONSCHEMA_PYEOF
CHECK27_EXIT=$?
if [ "$CHECK27_EXIT" -ne 0 ]; then
    FAIL_COUNT=$((FAIL_COUNT + 1))
fi
fi  # end python3 availability check

# ---------------------------------------------------------------------------
# CHECK 28: QUARANTINE-SLA — tests/quarantine.txt entries within the 30-day
# SLA (ADR-0067 D4; PRD #1462 / slice #1463)
#
#   Delegates to the health.py registry (ADR-0064 D3 single-source model),
#   mirroring CHECK 9's WARN-allowed/FAIL-blocks pattern: health.py's own
#   `--check` CLI exits 0 on PASS/WARN and 1 on FAIL, so a WARN-worthy
#   register (entries present but none breaching) still passes this check;
#   only a FAIL (>30-day breach) trips it.
# ---------------------------------------------------------------------------
echo "--- CHECK 28: QUARANTINE-SLA — quarantine register 30-day SLA ---"
if ! command -v python3 > /dev/null 2>&1; then
    echo "SKIP: CHECK 28 — python3 not available (soft-degrade)"
elif [ ! -f "dashboard/health.py" ]; then
    echo "SKIP: CHECK 28 — dashboard/health.py not found (soft-degrade)"
else
    CHECK28_OUTPUT=$(python3 dashboard/health.py --check QUARANTINE-SLA 2>&1)
    CHECK28_EXIT=$?
    if [ "$CHECK28_EXIT" -eq 0 ]; then
        pass "CHECK 28 — $CHECK28_OUTPUT"
    else
        fail "CHECK 28 — $CHECK28_OUTPUT"
    fi
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "--- CI SUMMARY ---"
if [ "$FAIL_COUNT" -eq 0 ]; then
    echo "ALL CHECKS PASSED"
    exit 0
else
    echo "FAILED: $FAIL_COUNT check(s) failed" >&2
    exit 1
fi
