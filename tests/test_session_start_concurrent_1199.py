"""
tests/test_session_start_concurrent_1199.py

New-feature regression tests for slice #1199 (PRD #1193 slice 3, ADR-0079 D3
-- "Hook overhead diet with an outcome-carrying beacon", per rule #18: the
`### D3` heading in decisions/0079-recorded-ci-trust-and-hook-diet.md names
session-start.sh's five `gh` queries running concurrently as part of D3's
scope).

`.claude/hooks/session-start.sh`'s five independent `gh` queries (needs-human
issues, needs-human PRs, open slices, open PRs, captured count) now run
CONCURRENTLY: each backgrounded job writes its RAW gh JSON to a private
tempfile; after `wait`, jq formats each tempfile SEQUENTIALLY using the
ORIGINAL, byte-identical filters (see PR body for the profiling that led to
this two-phase shape over a live gh|jq pipe per backgrounded job).

Covers this slice's acceptance criteria:
  3c2 -- HOK-008 attempt-beacon-first ordering preserved: the fake `gh` used
         by these tests asserts, on every one-of-five-query invocation, that
         the sandboxed hook-fires.jsonl already contains the "attempt"
         beacon line BEFORE that gh call executes.
  3c4 -- additionalContext fields byte-compatible with pre-change output on
         identical (fixture) repo state: this file diffs the PRE-CHANGE
         script content (pinned at f8e840a69b87138d7c0150cbd183998ba5df844d
         -- origin/develop HEAD at the point ADR-0079 D3 / slice #1197
         landed, named in this slice's own body) against the CURRENT
         (post-change) script, via an identical-fixture same-harness
         execution of both.
  Per-query output isolation (the slicer's named risk, this slice's own
  "What ships"): one query's failure/timeout must degrade only ITS OWN
  field to "(query failed)", never corrupt a sibling's captured text.
  Concurrency itself: a controllable fake-gh delay proves the five queries
  overlap in wall time rather than running serially (deterministic, fast,
  CI-safe -- the REAL 3x-timed-run medians against production gh live in
  the PR body per AC 3c, since they require live-repo state that cannot be
  fixture-pinned).

3c3 (CI-tested literals survive verbatim) is NOT re-tested here: this slice
does not modify the 'git fetch origin develop' / 'HEAD..origin/develop' /
'behind origin/develop' lines at all (out-of-bounds per this slice's own
body), and tests/test_guard_develop_841.py already covers those literals
end-to-end; duplicating that coverage here would violate DRY (rule #9).

Fixture discipline (rule #21 / this slice's hard constraints): every test
targets a FULLY SANDBOXED CLAUDE_PROJECT_DIR -- a fresh temp dir that is
deliberately verified (in setUp, via a failing `git rev-parse
--show-toplevel`) to NOT be inside any git repository, so lib-root.sh's
git-common-dir resolution soft-degrades to that dir directly and LOG_DIR /
hook-fires.jsonl / workflow-events.jsonl all resolve INSIDE the sandbox --
never the real `.claude/logs/*` store -- and a fake `gh` binary shadows
PATH, so no real GitHub network call is ever made. The harness also NEVER
dials the retired dashboard ports: it truncates session-start.sh's execution
at the closing `fi` of the `GH_OK` block -- the entire extent of code this
slice touches -- which is BEFORE the (since-removed, #1191/ADR-0088 D1)
dashboard-liveness probe that used to live there.

Runner: stdlib unittest + pytest compatible.
  python -m pytest tests/test_session_start_concurrent_1199.py -v
"""

import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
SESSION_START_SH = REPO_ROOT / ".claude" / "hooks" / "session-start.sh"
LIB_ROOT_SH = REPO_ROOT / ".claude" / "hooks" / "lib-root.sh"

# origin/develop HEAD at the point ADR-0079 D3 / slice #1197 landed -- the
# state of session-start.sh BEFORE this slice's concurrency change. Named
# explicitly in slice #1199's own body ("ADR-0079 is LANDED on develop
# (head f8e840a)"); rule #18 citation: ADR-0079's `### D3` heading is
# "Hook overhead diet with an outcome-carrying beacon" and its Consequences
# section explicitly states "Session start drops ~8 s; the injected context
# is unchanged, so nothing downstream notices" -- the exact byte-compat
# property 3c4 pins.
PRE_CHANGE_SHA = "f8e840a69b87138d7c0150cbd183998ba5df844d"


def _bash_available() -> bool:
    try:
        r = subprocess.run(["bash", "--version"], capture_output=True, timeout=5)
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _resolve_jq_dir():
    """Resolve jq's directory via a login-shell bash (sources rc files, so
    it sees PATH entries a bare non-login python-spawned bash would miss)
    and convert to a Windows-native path for env["PATH"] prepending. Returns
    None if jq cannot be located at all. (Mirrors
    test_deny_guard_mechanical_1133.py's helper exactly.)"""
    try:
        found = subprocess.run(
            ["bash", "-lc", "command -v jq"], capture_output=True, text=True, timeout=10,
        )
        if found.returncode != 0 or not found.stdout.strip():
            return None
        posix_path = found.stdout.strip()
        win = subprocess.run(
            ["bash", "-lc", f"cygpath -w '{posix_path}'"], capture_output=True, text=True, timeout=10,
        )
        if win.returncode != 0 or not win.stdout.strip():
            return None
        return str(Path(win.stdout.strip()).parent)
    except Exception:
        return None


_JQ_DIR = _resolve_jq_dir()


def _git_show(sha: str, path: str) -> str:
    result = subprocess.run(
        ["git", "show", f"{sha}:{path}"],
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"git show {sha}:{path} failed (rc={result.returncode}): {result.stderr}"
        )
    return result.stdout


# ---------------------------------------------------------------------------
# Fake `gh` -- shadows PATH, never makes a real GitHub network call.
#
# Recognizes exactly the five query shapes session-start.sh's GH_OK block
# invokes (`gh issue list --label <l> ...` / `gh pr list ...` / `gh pr list
# --label needs-human ...`) plus `gh auth status` (the GH_OK detection
# probe, which always succeeds instantly). For each of the five, it (a)
# checks the sandboxed hook-fires.jsonl for the attempt beacon and records
# an ORDER_OK/ORDER_VIOLATION line (3c2), (b) optionally sleeps per a
# JSON config keyed by query, and (c) optionally fails (exit 1, no stdout)
# to prove per-query output isolation.
# ---------------------------------------------------------------------------
_FAKE_GH_BODY = '''import sys, os, json, time

def _read_text(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""

def _append(path, line):
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\\n")
    except Exception:
        pass

argv = sys.argv[1:]

if "auth" in argv and "status" in argv:
    sys.exit(0)

query_key = None
if "issue" in argv and "list" in argv:
    try:
        label = argv[argv.index("--label") + 1]
    except (ValueError, IndexError):
        label = None
    query_key = label
elif "pr" in argv and "list" in argv:
    query_key = "pr_needs_human" if "--label" in argv else "pr_open"

if query_key is None:
    sys.stderr.write("fake-gh: unrecognized invocation: %r\\n" % (argv,))
    sys.exit(2)

order_marker = os.environ.get("FAKE_GH_ORDER_MARKER")
hook_fires = os.environ.get("FAKE_GH_HOOK_FIRES")
if order_marker and hook_fires:
    content = _read_text(hook_fires)
    attempt_marker = chr(34) + "status" + chr(34) + ":" + chr(34) + "attempt" + chr(34)
    verdict = "ORDER_OK" if attempt_marker in content else "ORDER_VIOLATION"
    _append(order_marker, "%s %s" % (query_key, verdict))

cfg_raw = os.environ.get("FAKE_GH_CONFIG", "{}")
try:
    cfg = json.loads(cfg_raw)
except Exception:
    cfg = {}
entry = cfg.get(query_key, {})
delay = float(entry.get("delay", 0) or 0)
fail = bool(entry.get("fail", False))
items = entry.get("items", [])

if delay:
    time.sleep(delay)

if fail:
    sys.exit(1)

print(json.dumps(items))
sys.exit(0)
'''


def _write_fake_gh(dirpath: str) -> str:
    """Write a fake `gh` binary into dirpath; prepend dirpath to PATH to
    shadow the real `gh`. Never touches a real GitHub issue/PR/network call.

    Always a bare POSIX-shebang `gh` script (no `.bat`/extension): this
    harness invokes session-start.sh exclusively via `bash`, and bash's own
    `command -v` PATH resolution (unlike Python's subprocess launcher) does
    NOT apply Windows PATHEXT-style extension resolution to bare names --
    a `gh.bat`-only fixture silently falls through to the REAL `gh` on
    Windows git-bash. A python3-shebang script is directly bash-executable
    on both POSIX and MSYS/git-bash."""
    sh_path = os.path.join(dirpath, "gh")
    with open(sh_path, "w", encoding="utf-8", newline="\n") as f:
        f.write("#!/usr/bin/env python3\n")
        f.write(_FAKE_GH_BODY)
    os.chmod(sh_path, 0o755)
    return dirpath


# ---------------------------------------------------------------------------
# Truncation + execution harness.
#
# Executes session-start.sh's lines [1 .. closing 'fi' of the GH_OK block]
# ONLY -- the entire extent of code this slice touches. This deliberately
# excludes the (since-removed, #1191/ADR-0088 D1) dashboard-liveness probe
# -- so the harness NEVER dials the retired dashboard ports -- and the
# later python3 event-emission heredoc (unaffected by this slice; its v2
# event shape is provably unchanged by inspection, not by execution, since
# this slice adds no lines before it and does not touch it).
# ---------------------------------------------------------------------------

_FIELD_TRAILER = (
    '\nprintf \'SESSION_START_TEST_FIELD::NH_ISSUES::%s\\n\' "$NH_ISSUES"\n'
    'printf \'SESSION_START_TEST_FIELD::NH_PRS::%s\\n\' "$NH_PRS"\n'
    'printf \'SESSION_START_TEST_FIELD::SL::%s\\n\' "$SL"\n'
    'printf \'SESSION_START_TEST_FIELD::PR::%s\\n\' "$PR"\n'
    'printf \'SESSION_START_TEST_FIELD::CAP::%s\\n\' "$CAP"\n'
)


def _truncate_at_gh_block(script_text: str) -> str:
    lines = script_text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if 'GH_OK" -eq 1 ]; then' in line:
            start = i
            break
    if start is None:
        raise AssertionError(
            "session-start.sh: GH_OK block start marker not found "
            "(script structure changed -- update this harness)"
        )
    end = None
    for j in range(start + 1, len(lines)):
        if lines[j].strip() == "fi":
            end = j
            break
    if end is None:
        raise AssertionError("session-start.sh: GH_OK block closing 'fi' not found")
    return "\n".join(lines[: end + 1]) + "\n"


def _parse_fields(stdout: str) -> dict:
    out = {}
    for line in stdout.splitlines():
        if line.startswith("SESSION_START_TEST_FIELD::"):
            _, key, val = line.split("::", 2)
            out[key] = val
    return out


def _run_truncated_session_start(
    session_start_text: str,
    lib_root_text: str,
    *,
    fake_gh_config: dict,
    order_marker_path: str = None,
    timeout: float = 30,
):
    """Execute the GH_OK-block-truncated script in a fully sandboxed
    CLAUDE_PROJECT_DIR against a fake `gh`. Returns
    (fields: dict, elapsed: float, hook_fires_path: str, result)."""
    sandbox = tempfile.mkdtemp(prefix="session_start_sandbox_1199_")
    harness_dir = tempfile.mkdtemp(prefix="session_start_harness_1199_")
    try:
        # Sandbox MUST NOT resolve inside any git repo -- lib-root.sh's
        # git-common-dir walk must soft-degrade to CLAUDE_PROJECT_DIR
        # itself, keeping LOG_DIR fully inside the sandbox (canonical
        # .claude/logs/* stays untouched -- rule #21 / this slice's hard
        # constraint).
        probe = subprocess.run(
            ["git", "-C", sandbox, "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=10,
        )
        if probe.returncode == 0:
            raise AssertionError(
                "sandbox tmp dir unexpectedly resolves inside a git repo "
                f"({probe.stdout.strip()!r}) -- refusing to run (would leak "
                "into canonical .claude/logs/*)"
            )

        gh_dir = _write_fake_gh(harness_dir)

        combined = _truncate_at_gh_block(session_start_text) + _FIELD_TRAILER
        script_path = os.path.join(harness_dir, "session-start-trunc.sh")
        with open(script_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(combined)
        with open(os.path.join(harness_dir, "lib-root.sh"), "w", encoding="utf-8", newline="\n") as f:
            f.write(lib_root_text)

        env = os.environ.copy()
        path_prefix = gh_dir
        if _JQ_DIR:
            path_prefix = _JQ_DIR + os.pathsep + path_prefix
        env["PATH"] = path_prefix + os.pathsep + env.get("PATH", "")
        env["CLAUDE_PROJECT_DIR"] = sandbox.replace(os.sep, "/")
        env["FAKE_GH_CONFIG"] = json.dumps(fake_gh_config)
        hook_fires_path = os.path.join(sandbox, ".claude", "logs", "hook-fires.jsonl")
        env["FAKE_GH_HOOK_FIRES"] = hook_fires_path
        if order_marker_path:
            env["FAKE_GH_ORDER_MARKER"] = order_marker_path

        start = time.monotonic()
        result = subprocess.run(
            ["bash", script_path],
            input="", capture_output=True, text=True,
            env=env, timeout=timeout,
        )
        elapsed = time.monotonic() - start

        return _parse_fields(result.stdout), elapsed, hook_fires_path, result
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)
        shutil.rmtree(harness_dir, ignore_errors=True)


def _jq_available() -> bool:
    if _JQ_DIR:
        return True
    r = subprocess.run(["bash", "-lc", "command -v jq"], capture_output=True, timeout=10)
    return r.returncode == 0


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class SessionStartHarnessTestBase(unittest.TestCase):
    def setUp(self):
        if not _bash_available():
            self.skipTest("bash not available in this environment")
        if not _jq_available():
            self.skipTest("jq not available in this environment")
        self.assertTrue(
            SESSION_START_SH.exists(),
            f"session-start.sh not found at {SESSION_START_SH}",
        )


class TestBeaconOrderPreserved(SessionStartHarnessTestBase):
    """3c2 -- HOK-008 attempt-beacon-first ordering preserved under
    concurrency: the attempt beacon (written synchronously, before the
    GH_OK block is even reached) must precede EVERY one of the five
    backgrounded gh invocations."""

    def test_attempt_beacon_precedes_all_five_child_queries(self):
        session_start_text = SESSION_START_SH.read_text(encoding="utf-8")
        lib_root_text = LIB_ROOT_SH.read_text(encoding="utf-8")
        marker_fd, marker_path = tempfile.mkstemp(suffix=".marker")
        os.close(marker_fd)
        os.unlink(marker_path)  # start absent; fake gh appends fresh
        try:
            cfg = {
                k: {"items": [], "delay": 0, "fail": False}
                for k in ("needs-human", "slice", "captured", "pr_open", "pr_needs_human")
            }
            fields, elapsed, hook_fires, result = _run_truncated_session_start(
                session_start_text, lib_root_text,
                fake_gh_config=cfg, order_marker_path=marker_path,
            )
            # NOTE: `hook_fires` itself is not re-checked for existence here --
            # `_run_truncated_session_start`'s `finally` block already rmtree's
            # the sandbox (including hook-fires.jsonl) before returning, so a
            # post-hoc `os.path.exists(hook_fires)` would always be False by
            # construction, not because the beacon was never written. The
            # marker file below is the real proof: each fake-gh invocation
            # read hook-fires.jsonl WHILE it still existed (mid-execution,
            # before cleanup) and recorded what it saw.
            self.assertTrue(
                os.path.exists(marker_path),
                f"fake gh never ran (no order-marker file written); "
                f"stdout={result.stdout!r} stderr={result.stderr!r}",
            )
            marker_lines = Path(marker_path).read_text(encoding="utf-8").splitlines()
            self.assertEqual(
                5, len(marker_lines),
                f"expected exactly 5 child-query order checks, got {marker_lines} "
                f"(stderr={result.stderr!r})",
            )
            violations = [l for l in marker_lines if "ORDER_VIOLATION" in l]
            self.assertEqual(
                [], violations,
                "HOK-008: attempt beacon must precede every child-query gh "
                f"invocation, but saw: {marker_lines}",
            )
        finally:
            if os.path.exists(marker_path):
                os.unlink(marker_path)


class TestPerQueryOutputIsolation(SessionStartHarnessTestBase):
    """Named risk (this slice's own 'What ships'): one query's failure must
    degrade ONLY its own field to "(query failed)", never corrupt a
    sibling's captured text."""

    def test_one_slow_failing_query_does_not_corrupt_siblings(self):
        session_start_text = SESSION_START_SH.read_text(encoding="utf-8")
        lib_root_text = LIB_ROOT_SH.read_text(encoding="utf-8")
        cfg = {
            "needs-human": {"items": [{"number": 101, "title": "Fix auth"}], "delay": 0, "fail": False},
            "slice": {"items": [], "delay": 0.3, "fail": True},
            "captured": {"items": [{"number": 202, "title": "Cap A"}, {"number": 203, "title": "Cap B"}], "delay": 0, "fail": False},
            "pr_open": {"items": [{"number": 303, "title": "PR Z"}], "delay": 0, "fail": False},
            "pr_needs_human": {"items": [], "delay": 0, "fail": False},
        }
        fields, elapsed, hook_fires, result = _run_truncated_session_start(
            session_start_text, lib_root_text, fake_gh_config=cfg,
        )
        msg = f"fields={fields} stdout={result.stdout!r} stderr={result.stderr!r}"
        self.assertEqual(fields.get("SL"), "(query failed)", msg)
        self.assertEqual(fields.get("NH_ISSUES"), "1+ open; recent: #101 Fix auth", msg)
        self.assertEqual(fields.get("CAP"), "2+ open; recent: #202 Cap A | #203 Cap B", msg)
        self.assertEqual(fields.get("PR"), "1+ open; recent: #303 PR Z", msg)
        self.assertEqual(fields.get("NH_PRS"), "0 needs-human PRs", msg)


class TestConcurrencyTiming(SessionStartHarnessTestBase):
    """The five queries run CONCURRENTLY, not serially -- proven with a
    controllable fake-gh delay (deterministic, CI-safe). The REAL
    3x-timed-run medians against production gh (AC 3c) live in the PR
    body, since they require live-repo state this fixture cannot pin."""

    def test_five_delayed_queries_run_concurrently(self):
        session_start_text = SESSION_START_SH.read_text(encoding="utf-8")
        lib_root_text = LIB_ROOT_SH.read_text(encoding="utf-8")
        delay = 0.6
        cfg = {
            k: {"items": [], "delay": delay, "fail": False}
            for k in ("needs-human", "slice", "captured", "pr_open", "pr_needs_human")
        }
        fields, elapsed, hook_fires, result = _run_truncated_session_start(
            session_start_text, lib_root_text, fake_gh_config=cfg,
        )
        # Serial would cost >= 5 * delay = 3.0s; concurrent should land near
        # one delay-width plus process overhead. Generous CI-safe margin.
        self.assertLess(
            elapsed, delay * 3,
            f"5 queries at {delay}s each took {elapsed:.2f}s -- looks serial, "
            f"not concurrent (stdout={result.stdout!r} stderr={result.stderr!r})",
        )
        self.assertGreaterEqual(
            elapsed, delay * 0.7,
            f"elapsed {elapsed:.2f}s is suspiciously below the configured "
            f"per-query delay ({delay}s) -- fake-gh delay may not be applied",
        )


class TestByteCompatiblePrePostChange(SessionStartHarnessTestBase):
    """3c4 -- additionalContext fields byte-compatible with pre-change
    output on identical (fixture) repo state."""

    def test_fields_identical_pre_and_post_change(self):
        post_text = SESSION_START_SH.read_text(encoding="utf-8")
        post_lib = LIB_ROOT_SH.read_text(encoding="utf-8")
        pre_text = _git_show(PRE_CHANGE_SHA, ".claude/hooks/session-start.sh")
        pre_lib = _git_show(PRE_CHANGE_SHA, ".claude/hooks/lib-root.sh")

        cfg = {
            "needs-human": {"items": [{"number": 11, "title": "Alpha"}], "delay": 0, "fail": False},
            "slice": {"items": [{"number": 12, "title": "Beta"}, {"number": 13, "title": "Gamma"}], "delay": 0, "fail": False},
            "captured": {"items": [], "delay": 0, "fail": False},
            "pr_open": {"items": [{"number": 14, "title": "Delta"}], "delay": 0, "fail": False},
            "pr_needs_human": {"items": [{"number": 15, "title": "Epsilon"}], "delay": 0, "fail": False},
        }

        pre_fields, _, _, pre_result = _run_truncated_session_start(
            pre_text, pre_lib, fake_gh_config=cfg,
        )
        post_fields, _, _, post_result = _run_truncated_session_start(
            post_text, post_lib, fake_gh_config=cfg,
        )

        self.assertEqual(
            set(pre_fields), set(post_fields),
            f"field set changed: pre={sorted(pre_fields)} post={sorted(post_fields)} "
            f"pre_stderr={pre_result.stderr!r} post_stderr={post_result.stderr!r}",
        )
        self.assertEqual(
            pre_fields, post_fields,
            "additionalContext fields must be byte-compatible pre- vs "
            f"post-change on identical fixture state.\npre={pre_fields}\n"
            f"post={post_fields}\npre_stderr={pre_result.stderr}\n"
            f"post_stderr={post_result.stderr}",
        )


if __name__ == "__main__":
    unittest.main()
