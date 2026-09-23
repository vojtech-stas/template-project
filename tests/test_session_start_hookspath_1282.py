"""
tests/test_session_start_hookspath_1282.py

Regression test for issue #1282 (root-cause capture): `.claude/hooks/
session-start.sh`'s core.hooksPath check used a BARE STRING COMPARE
(`"$HOOKS" != ".githooks"`) that false-alarms on every session when
`.githooks/install.sh` writes an ABSOLUTE hooksPath (e.g.
"F:\\project_claude\\.githooks") that resolves to the SAME directory as the
documented relative ".githooks" -- the exact bug #1093 already fixed, but
only in tools/deploy-handshake.sh's own comparison; this second call site
(session-start.sh) was never updated.

ADR-0067 D3 / rule #13 regression rider: this test commit precedes the fix
commit in branch history. It intentionally FAILS on the unfixed script
(test_absolute_hookspath_resolving_to_githooks_no_false_alarm below) and
PASSES once session-start.sh's own bare compare is removed in favor of the
already-correct path-identity comparison tools/deploy-handshake.sh performs
(session-start.sh already invokes that script for the deploy-gap banner --
the fix repoints at that ONE canonical implementation instead of
reimplementing the check a second time).

test_genuinely_different_hookspath_still_alarms is the NEGATIVE CONTROL
(per the issue's own Proposed #2: "without it the test passes for a check
that never fires at all") -- it must pass BOTH before and after the fix,
proving a real mismatch is never silently swallowed by removing the buggy
duplicate check.

Fixture discipline (rule #21 / shared-git fixture discipline, #543/#545
incident): all fixtures are synthetic temp git repos (via Python's
tempfile module) -- NEVER the live worktree set. The harness truncates
session-start.sh's execution BEFORE the gh/jq-availability block (this
issue's fix does not touch that code) so no `gh` call, no dashboard probe
(the retired dashboard ports), and no write to the real `.claude/logs/*`
store ever occurs -- CLAUDE_PROJECT_DIR points at the fixture repo for the
whole run, so lib-root.sh's LOG_DIR resolves INSIDE the fixture only.

Runner: stdlib unittest + pytest compatible.
  python -m pytest tests/test_session_start_hookspath_1282.py -v
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
SESSION_START_SH = REPO_ROOT / ".claude" / "hooks" / "session-start.sh"
LIB_ROOT_SH = REPO_ROOT / ".claude" / "hooks" / "lib-root.sh"
DEPLOY_HANDSHAKE_SH = REPO_ROOT / "tools" / "deploy-handshake.sh"

_TRUNCATE_MARKER = "# ---- gh/jq availability"

# Appended after truncation. Reads HOOKS_WARN/DEPLOY_WARN defensively via
# `${VAR:-}` -- immune to `set -u` whether or not the fix has removed
# HOOKS_WARN entirely -- and flattens embedded newlines to the '@@NL@@'
# placeholder so each field prints on exactly one line for the parser
# below. NOTE: a bare '~' replacement is NOT safe here -- bash applies
# tilde-expansion to an unescaped '~' in a `${var//pat/repl}` replacement
# field, silently substituting "$HOME" (e.g. "/c/Users/name") in front of
# the real text; '@@NL@@' has no special meaning to bash.
_TRAILER = r"""
_hw="${HOOKS_WARN:-}"
_dw="${DEPLOY_WARN:-}"
_hw="${_hw//$'\n'/@@NL@@}"
_dw="${_dw//$'\n'/@@NL@@}"
printf 'TEST_FIELD::HOOKS_WARN::%s\n' "$_hw"
printf 'TEST_FIELD::DEPLOY_WARN::%s\n' "$_dw"
"""


def _bash_available() -> bool:
    try:
        r = subprocess.run(["bash", "--version"], capture_output=True, timeout=5)
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _git(*args, cwd=None, check=True):
    """Run a git command, capturing output."""
    return subprocess.run(
        ["git"] + list(args),
        cwd=str(cwd) if cwd else None,
        check=check,
        capture_output=True,
        text=True,
    )


def _build_fixture(tmp_path: Path) -> Path:
    """Synthetic repo mirroring tests/test_deploy_handshake_1079.py's
    fixture shape: .claude/hooks/session-start.sh (a stub -- the harness
    below runs the REAL repo's script content, not this file) + .claude/
    settings.json + .githooks/, committed on 'main'. core.hooksPath is
    NOT set here; callers configure it per-scenario after this returns."""
    repo = tmp_path / "repo"
    repo.mkdir()
    r = _git("init", "-b", "main", str(repo), check=False)
    if r.returncode != 0:
        _git("init", str(repo), check=True)
        _git("-C", str(repo), "checkout", "-b", "main", check=False)
    _git("-C", str(repo), "config", "user.email", "test@example.com")
    _git("-C", str(repo), "config", "user.name", "Test")

    hooks_dir = repo / ".claude" / "hooks"
    hooks_dir.mkdir(parents=True)
    (hooks_dir / "session-start.sh").write_text("#!/bin/bash\necho hi\n")
    (repo / ".claude" / "settings.json").write_text('{"hooks": {}}\n')
    githooks_dir = repo / ".githooks"
    githooks_dir.mkdir()
    (githooks_dir / "pre-commit").write_text("#!/bin/bash\nexit 0\n")

    _git("-C", str(repo), "add", ".")
    _git("-C", str(repo), "commit", "-m", "init")

    return repo


def _truncate_before_gh_block(script_text: str) -> str:
    lines = script_text.splitlines()
    idx = None
    for i, line in enumerate(lines):
        if line.strip().startswith(_TRUNCATE_MARKER):
            idx = i
            break
    if idx is None:
        raise AssertionError(
            "session-start.sh: gh/jq-availability marker not found "
            "(script structure changed -- update this harness)"
        )
    return "\n".join(lines[:idx]) + "\n" + _TRAILER


def _parse_fields(stdout: str) -> dict:
    out = {}
    for line in stdout.splitlines():
        if line.startswith("TEST_FIELD::"):
            _, key, val = line.split("::", 2)
            out[key] = val.replace("@@NL@@", "\n")
    return out


def _run_session_start(fixture_repo: Path):
    """Execute the gh-block-truncated session-start.sh against
    `fixture_repo` (via CLAUDE_PROJECT_DIR). The harness mirrors the REAL
    two-level directory shape (<root>/.claude/hooks/*.sh,
    <root>/tools/deploy-handshake.sh) so session-start.sh's own
    `$SCRIPT_DIR/../../tools/deploy-handshake.sh` relative-path resolution
    finds the REAL deploy-handshake.sh (copied in unmodified) instead of
    silently no-op'ing on a missing file."""
    session_start_text = SESSION_START_SH.read_text(encoding="utf-8")
    lib_root_text = LIB_ROOT_SH.read_text(encoding="utf-8")

    harness_root = tempfile.mkdtemp(prefix="session_start_hookspath_1282_")
    try:
        hooks_dir = Path(harness_root) / ".claude" / "hooks"
        hooks_dir.mkdir(parents=True)
        tools_dir = Path(harness_root) / "tools"
        tools_dir.mkdir()

        truncated = _truncate_before_gh_block(session_start_text)
        (hooks_dir / "session-start-trunc.sh").write_text(truncated, encoding="utf-8", newline="\n")
        (hooks_dir / "lib-root.sh").write_text(lib_root_text, encoding="utf-8", newline="\n")
        shutil.copy2(DEPLOY_HANDSHAKE_SH, tools_dir / "deploy-handshake.sh")

        env = os.environ.copy()
        env["CLAUDE_PROJECT_DIR"] = str(fixture_repo).replace(os.sep, "/")

        result = subprocess.run(
            ["bash", str(hooks_dir / "session-start-trunc.sh")],
            input="", capture_output=True, text=True,
            env=env, timeout=60,
        )
        return _parse_fields(result.stdout), result
    finally:
        shutil.rmtree(harness_root, ignore_errors=True)


class TestSessionStartHooksPathCompare1282(unittest.TestCase):
    """`.claude/hooks/session-start.sh`'s core.hooksPath check must compare
    PATH IDENTITY, not bare strings (#1282 -- ported from the #1093 fix
    already applied to tools/deploy-handshake.sh)."""

    def setUp(self):
        if not _bash_available():
            self.skipTest("bash not available in this environment")
        if not DEPLOY_HANDSHAKE_SH.exists():
            self.skipTest("tools/deploy-handshake.sh not found")

    def test_absolute_hookspath_resolving_to_githooks_no_false_alarm(self):
        """An ABSOLUTE hooksPath that resolves to the SAME directory as the
        documented relative '.githooks' must NOT produce any hooks-path
        warning. FAILS before #1282's fix (bare compare false-alarms on
        the absolute-but-equivalent path); PASSES after (path-identity
        compare, same resolution deploy-handshake.sh already performs)."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = _build_fixture(Path(tmp))
            # Mirrors the real symptom's exact shape (measured 2026-08-23:
            # "F:\project_claude\.githooks" -- an absolute path resolving
            # to the correct .githooks dir, not the literal ".githooks").
            githooks_abs = str((repo / ".githooks").resolve())
            _git("-C", str(repo), "config", "core.hooksPath", githooks_abs)
            fields, result = _run_session_start(repo)
        combined = fields.get("HOOKS_WARN", "") + fields.get("DEPLOY_WARN", "")
        self.assertEqual(
            "", combined.strip(),
            msg=(
                "session-start.sh false-alarmed on an absolute hooksPath "
                "that resolves to the correct .githooks dir (#1282); "
                f"fields={fields} stdout={result.stdout!r} stderr={result.stderr!r}"
            ),
        )

    def test_genuinely_different_hookspath_still_alarms(self):
        """NEGATIVE CONTROL: a hooksPath resolving to a genuinely DIFFERENT
        directory must still alarm. Holds both before and after #1282's
        fix -- tools/deploy-handshake.sh's own comparison (already correct
        since #1093) independently catches this class of mismatch, so
        deleting session-start.sh's redundant/buggy check must not go on
        to silently swallow real mismatches too."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = _build_fixture(Path(tmp))
            other_dir = Path(tmp) / "not-the-real-hooks-dir"
            other_dir.mkdir()
            _git("-C", str(repo), "config", "core.hooksPath", str(other_dir))
            fields, result = _run_session_start(repo)
        combined = fields.get("HOOKS_WARN", "") + fields.get("DEPLOY_WARN", "")
        self.assertNotEqual(
            "", combined.strip(),
            msg=(
                "session-start.sh silently swallowed a GENUINE hooksPath "
                f"mismatch (#1282 negative control); fields={fields} "
                f"stdout={result.stdout!r} stderr={result.stderr!r}"
            ),
        )
        self.assertIn("hooksPath", combined)


if __name__ == "__main__":
    unittest.main()
