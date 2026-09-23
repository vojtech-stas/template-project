#!/usr/bin/env python3
"""
tools/check-verdict-presence.py — verdict-presence guard (PRD #1214 criterion
3c / ADR-0080 D1's supersession of ADR-0075 D6, slice #1219).

Verifies that every recently-merged PR to the configured integration branch
(ADR-0089 D1) carries a reviewer comment
matching the exact `VERDICT:\\s*APPROVE` signal `tools/pipe/pr-merge`'s
`_VERDICT_APPROVE_RE` pattern requires before it will perform a merge (PIP-016
/ ADR-0076 D3). This is the D6-supersession's compensating control: the
retired gh-reconstructed panel used to render this cross-check for a human
who happened to look; this check performs the SAME verification mechanically
on every CI run, against ground truth (`gh pr view --json comments`) that a
fresh hosted checkout genuinely has — so, unlike the ledger-side
MERGED-WITHOUT-VERDICT reconciler (which WARN-degrades in a fresh checkout
with no local trace history), this check REALLY FAILS CI on a violation
(e.g. the admin-bypass merge class ADR-0076 D3's residual #1098 names).

Window scope: mechanically bounded — the last `--limit` (default 20, matching
the `gh pr list --base <integration> --state merged --limit 20` convention
already used by `dashboard/health.py`'s `_fetch_github_ci_conclusion` /
RECORD-VS-GH). NO special-case grandfather list: every PR in the window must
carry the trailer, full stop — the reviewer's `VERDICT: APPROVE` comment
convention predates ADR-0076, so there is no historical PR this check is
expected to give a pass for free.

Fixture seam (rule #21 fixture discipline: this script performs ZERO writes
to any production data store — it only reads via `gh` — so the seam carries
no contamination risk): `--fixture-file <path>` loads a JSON file shaped
`[{"number": <int>, "comments": [{"body": "..."}]}, ...]` in place of live
`gh` calls, for local demonstration/testing of the FAIL path (a canary
comment-set deliberately lacking the trailer) without needing a real
badly-merged PR to exist.

Tri-state gh-interaction contract (reviewer round-1 BLOCK, PR #1222, R-TESTS
— this is the D6-supersession compensating control; it must NOT soft-fail
invisibly): every gh-calling helper returns `(status, value)` where status is
one of:
  "ok"           — value is the useful payload (CompletedProcess / list)
  "soft_degrade" — value names the reason; ONLY for a confirmed local-dev
                   condition (gh binary literally not installed, or gh's own
                   not-authenticated error shape) — never for an ambiguous
                   failure. `main()` prints SKIP and exits 0 for this case.
  "hard_fail"    — value names the reason; ANY gh failure that is NOT
                   confirmed as one of the two conditions above (rate limits,
                   timeouts, network errors, non-JSON output, a per-PR fetch
                   failing mid-window) — `main()` prints FAIL and exits 1.
                   A required gate that cannot verify its assertion must
                   report that it cannot verify, never a silent PASS.

Exit codes:
  0 — every PR in the window carries the VERDICT: APPROVE trailer, or a
      confirmed soft-degrade condition (gh not installed / gh not
      authenticated — local dev without a GH_TOKEN; CI always has one per
      the CHECK-19 precedent, so this path is a genuine hard gate there)
  1 — one or more PRs in the window lack the trailer, OR gh failed for any
      reason NOT confirmed as a soft-degrade condition (hard_fail)

Usage:
  python3 tools/check-verdict-presence.py
  python3 tools/check-verdict-presence.py --limit 20
  python3 tools/check-verdict-presence.py --fixture-file <path>   # test/demo only

CI integration: tools/ci-checks.sh CHECK 23 calls this script directly.
"""

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PIPELINE_CONFIG_PY = os.path.join(_THIS_DIR, "pipeline_config.py")


def _load_pipeline_config():
    # S1-c: located relative to THIS file (_THIS_DIR above), never via
    # $REPO_ROOT or `git rev-parse --show-toplevel` of the cwd repo.
    spec = importlib.util.spec_from_file_location(
        "pipeline_config", _PIPELINE_CONFIG_PY
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Windows cp1252 fix (#1050 precedent, mirrors check-slicer-provenance.py):
# force stdout/stderr to utf-8 at startup — gh comment bodies routinely
# contain non-cp1252 glyphs.
# ---------------------------------------------------------------------------
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

_DEFAULT_LIMIT = 20

# Tri-state status constants.
_OK = "ok"
_SOFT_DEGRADE = "soft_degrade"
_HARD_FAIL = "hard_fail"

# The exact signal tools/pipe/pr-merge's `_VERDICT_APPROVE_RE` requires
# before performing a merge (PIP-016 / ADR-0076 D3) — kept byte-identical
# here so this check can never drift from the merge-time gate it audits.
_VERDICT_APPROVE_RE = re.compile(r"VERDICT:\s*APPROVE")


def comment_has_verdict_approve(body: str) -> bool:
    """Return True if a single comment body matches the exact
    `VERDICT:\\s*APPROVE` signal. Pure, unit-testable — no I/O."""
    if not body:
        return False
    return bool(_VERDICT_APPROVE_RE.search(body))


def pr_has_verdict(comments: list) -> bool:
    """Return True if ANY comment in the list matches the trailer. Pure,
    unit-testable — no I/O."""
    return any(comment_has_verdict_approve((c or {}).get("body", "")) for c in comments)


def classify_prs(prs: list) -> dict:
    """Classify PRs into ok/missing buckets (pure, unit-testable — no I/O).

    `prs` is a list of {"number": int, "comments": [{"body": str}, ...]}.
    Returns {"ok": [numbers...], "missing": [numbers...]}.
    """
    ok: list = []
    missing: list = []
    for pr in prs:
        number = pr["number"]
        if pr_has_verdict(pr.get("comments") or []):
            ok.append(number)
        else:
            missing.append(number)
    return {"ok": ok, "missing": missing}


def _gh_unauthenticated(stderr: str) -> bool:
    """Narrowly recognize gh's OWN not-authenticated error shapes — the
    ONLY stderr condition allowed to soft-degrade this REQUIRED gate to a
    SKIP (local dev without a GH_TOKEN).

    Deliberately narrower than a bare "token" substring (the reviewer
    round-1 BLOCK on PR #1222): gh's rate-limit message ("API rate limit
    exceeded for token ...") also contains the word "token" but is NOT an
    authentication failure — matching it as such would silently turn a
    transient/rate-limit condition into an invisible PASS for the one
    check that must not soft-fail invisibly (this compensating control's
    whole point, per ADR-0080 D1's supersession of ADR-0075 D6). Any gh
    failure that does not match one of these known auth-error shapes is
    treated as a HARD FAILURE by the caller, never a silent skip."""
    stderr_lower = (stderr or "").lower()
    return any(
        s in stderr_lower
        for s in ("not logged", "authentication", "unauthorized", "gh_token")
    )


def _run_gh(args: list, timeout: int = 30):
    """Run a `gh` subcommand. Returns (status, value) per the tri-state
    contract: ("ok", CompletedProcess) on a successful subprocess launch
    (regardless of gh's own exit code — the caller inspects that);
    ("soft_degrade", reason) ONLY when the gh binary itself is not
    installed at all (an unambiguous local-dev condition); ("hard_fail",
    reason) for a timeout or any other transport-level error — CI always
    has gh installed, so anything else here is anomalous and must not be
    silently skipped."""
    try:
        result = subprocess.run(
            ["gh", *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout,
        )
        return _OK, result
    except FileNotFoundError:
        return _SOFT_DEGRADE, "gh not found"
    except subprocess.TimeoutExpired:
        return _HARD_FAIL, "gh timed out"
    except Exception as e:
        return _HARD_FAIL, f"gh error: {e}"


def _fetch_recent_merged_pr_numbers(limit: int):
    """`gh pr list --base <integration> --state merged --limit <limit> --json
    number` — the same bounded-window convention `dashboard/health.py`'s
    `_fetch_github_ci_conclusion` / RECORD-VS-GH already use.

    Returns (status, value) per the tri-state contract: ("ok", [ints]),
    ("soft_degrade", reason), or ("hard_fail", reason)."""
    integration = _load_pipeline_config().integration_branch()
    status, result = _run_gh([
        "pr", "list", "--base", integration, "--state", "merged",
        "--limit", str(limit), "--json", "number",
    ])
    if status != _OK:
        return status, result
    if result.returncode != 0:
        if _gh_unauthenticated(result.stderr):
            return _SOFT_DEGRADE, "gh unauthenticated (no GH_TOKEN)"
        return _HARD_FAIL, (
            f"gh pr list exited {result.returncode}: {result.stderr.strip()[:120]}"
        )
    stdout = result.stdout.strip()
    if not stdout:
        return _OK, []
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return _HARD_FAIL, "gh pr list returned non-JSON output"
    return _OK, [item["number"] for item in data]


def _fetch_pr_comments(number: int):
    """`gh pr view <n> --json comments` — the same call
    `tools/pipe/pr-merge`'s `_fetch_pr_comments` uses. Returns (status,
    value) per the tri-state contract. A non-zero exit here (mid-window,
    after the list call already succeeded and proved gh IS authenticated)
    is always a hard_fail — never silently degraded to an empty comment
    list, which would mischaracterize an unverifiable PR as one that
    genuinely lacks the trailer."""
    status, result = _run_gh(["pr", "view", str(number), "--json", "comments"])
    if status != _OK:
        return status, result
    if result.returncode != 0:
        return _HARD_FAIL, (
            f"gh pr view #{number} exited {result.returncode}: "
            f"{result.stderr.strip()[:120]}"
        )
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return _HARD_FAIL, f"gh pr view #{number} returned non-JSON output"
    return _OK, data.get("comments", [])


def _fetch_prs_with_comments(limit: int):
    """Combine the two gh calls above into the shape `classify_prs`
    expects. Returns (status, value) per the tri-state contract,
    propagating the FIRST non-ok status encountered (list call or any
    per-PR comment fetch) — a partial, unverifiable window must not be
    reported as a verified PASS or a false per-PR FAIL."""
    status, value = _fetch_recent_merged_pr_numbers(limit)
    if status != _OK:
        return status, value
    numbers = value
    prs = []
    for n in numbers:
        c_status, c_value = _fetch_pr_comments(n)
        if c_status != _OK:
            return c_status, c_value
        prs.append({"number": n, "comments": c_value})
    return _OK, prs


def _load_fixture(path: str) -> list:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=_DEFAULT_LIMIT)
    parser.add_argument(
        "--fixture-file", default=None,
        help="load a JSON fixture instead of live gh calls (test/demo only)",
    )
    args = parser.parse_args(argv)
    integration = _load_pipeline_config().integration_branch()

    if args.fixture_file:
        prs = _load_fixture(args.fixture_file)
        print(f"verdict-presence check — running against fixture {args.fixture_file!r}")
    else:
        status, value = _fetch_prs_with_comments(args.limit)
        if status == _SOFT_DEGRADE:
            print(f"SKIP: verdict-presence check — {value} (soft-degrade)")
            return 0
        if status == _HARD_FAIL:
            print(
                f"FAIL: verdict-presence — {value} — a REQUIRED gate must not "
                "silently pass on an unverifiable gh result",
                file=sys.stderr,
            )
            return 1
        prs = value

    if not prs:
        print(f"PASS: verdict-presence — no merged {integration} PRs found in window")
        return 0

    result = classify_prs(prs)
    ok, missing = result["ok"], result["missing"]
    detail = f"{len(ok)} ok, {len(missing)} MISSING (window={len(prs)})"

    if missing:
        numbers = ", ".join(f"#{n}" for n in sorted(missing))
        print(f"FAIL: verdict-presence — {detail}: {numbers}", file=sys.stderr)
        print(
            f"These merged {integration} PRs carry no reviewer comment matching "
            "'VERDICT: APPROVE' — the D6-supersession compensating control "
            "(ADR-0080 D1) treats this as a real CI failure, not a WARN.",
            file=sys.stderr,
        )
        return 1

    print(f"PASS: verdict-presence — {detail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
