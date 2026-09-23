"""
tests/test_bootstrap_branch_protection_1294.py

Originally the regression test for issue #1294 (root-cause capture,
`backlog`+`root-cause`): bootstrap.sh step 5 called the GitHub
branch-protection API against `main` instead of `develop`. Fixed in PR #1454
by a static text assertion on `branches/develop/protection`.

Replaced (slice #1511 / ADR-0089 D2's Enforcement mechanism) by this
BEHAVIORAL test: it runs the real `bootstrap.sh` against a local bare
"origin" with only a `main` branch (mirroring PRD #1500's Sandbox D — new
project, default roles) and a `gh` shim that records every call's arguments
and `--input` payload, per PRD #1500's own "Shim" definition. It asserts:

  1. The integration branch (`develop`, this repo's tracked default) is
     created on origin at the release branch's (`main`) tip.
  2. The only protection PUT paths are `branches/develop/protection` and
     `branches/main/protection`, each carrying the R4 `ci` payload with
     `enforce_admins` false.
  3. A second run is a no-op: exit 0, `develop`'s sha unmoved.

Assertion 2's second half is REVERSED on purpose from the original #1294
test (which asserted `branches/main/protection` was ABSENT): #1294's defect
was `develop` left unprotected, which the integration PUT still covers;
under ADR-0089 D2 the release branch (`main`) is now DELIBERATELY protected
too, with the same payload.

Never touches the network (S1-e): `gh`, `pip`, `pip3`, `winget`, `brew`,
`apt-get` and `sudo` are all shimmed as argument-recording no-ops, and each
shim's win over `command -v` is verified BEFORE running bootstrap (mirrors
tests/test_terminal_beacons_1310.py's precondition pattern). The sandbox is
built by cloning this checkout's HEAD (never `--branch develop` — a CI
checkout has no local `develop` branch pre-merge).

Runner: stdlib unittest + pytest compatible.
  python -m pytest tests/test_bootstrap_branch_protection_1294.py -v
"""

import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BOOTSTRAP_SH = REPO_ROOT / "bootstrap.sh"
BASH = shutil.which("bash")

_NOOP_SHIM_BODY = "#!/bin/bash\nexit 0\n"

# A fake `ssh` command (via GIT_SSH_COMMAND) that routes git's ssh transport
# straight to a local bare repo, bypassing the network entirely. This lets
# origin's URL be a real `git@host:owner/repo` string — satisfying
# bootstrap.sh's resolve_origin_slug() regex, which `git remote get-url`
# must report VERBATIM for the slug to parse — while every actual git
# operation (push/fetch/ls-remote) stays local. `git remote get-url` reports
# the REWRITTEN value when `url.<x>.insteadOf` is configured (verified
# empirically), so that mechanism cannot serve this purpose; a GIT_SSH_COMMAND
# override is invisible to `get-url`, which is exactly what's needed here.
_FAKE_SSH_BODY = """#!/bin/bash
cmd="${@: -1}"
case "$cmd" in
  *git-upload-pack*) exec git upload-pack "$FAKE_SSH_TARGET" ;;
  *git-receive-pack*) exec git receive-pack "$FAKE_SSH_TARGET" ;;
  *) exit 1 ;;
esac
"""

_GH_SHIM_BODY = """#!/bin/bash
# gh shim per PRD #1500's "Shim" definition: records "$*" (plus stdin, with
# newlines removed, when --input - is among the args) to $GH_SHIM_LOG.
# Prints a PR URL for `pr create`, "[]" for any --json call. Always exits 0.
args="$*"
if printf '%s' "$args" | grep -q -- '--input -'; then
  stdin_content=$(cat | tr -d '\\n')
  printf '%s %s\\n' "$args" "$stdin_content" >> "$GH_SHIM_LOG"
else
  printf '%s\\n' "$args" >> "$GH_SHIM_LOG"
fi
if printf '%s' "$args" | grep -q 'pr create'; then
  echo "https://github.com/example/throwaway/pull/7"
elif printf '%s' "$args" | grep -q -- '--json'; then
  echo "[]"
fi
exit 0
"""


def _git(args, cwd, check=True, env=None):
    return subprocess.run(
        ["git"] + args, cwd=str(cwd), check=check, capture_output=True, text=True, env=env,
    )


class TestBootstrapCreatesAndProtectsBothBranches(unittest.TestCase):
    """bootstrap.sh creates the integration branch from the release tip and
    protects BOTH configured branches, never just one (ADR-0089 D2)."""

    def setUp(self):
        if BASH is None:
            self.skipTest("bash not available — skipping behavioral test")
        if not BOOTSTRAP_SH.exists():
            self.fail(f"bootstrap.sh not found at {BOOTSTRAP_SH}")

        self._tmpdir = Path(tempfile.mkdtemp(prefix="bootstrap-behav-1294-"))
        self.addCleanup(shutil.rmtree, self._tmpdir, ignore_errors=True)

        self._shim_dir = self._tmpdir / "shim-bin"
        self._shim_dir.mkdir()
        self._write_shim("gh", _GH_SHIM_BODY)
        for name in ("pip", "pip3", "winget", "brew", "apt-get", "sudo"):
            self._write_shim(name, _NOOP_SHIM_BODY)

        self._path_override = str(self._shim_dir) + os.pathsep + os.environ.get("PATH", "")
        self._require_each_shim_wins()

        self._fake_ssh = self._tmpdir / "fake-ssh.sh"
        self._fake_ssh.write_text(_FAKE_SSH_BODY, encoding="utf-8", newline="\n")
        self._fake_ssh.chmod(self._fake_ssh.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        self._work = self._build_sandbox()

    def _write_shim(self, name: str, body: str) -> None:
        shim = self._shim_dir / name
        shim.write_text(body, encoding="utf-8", newline="\n")
        shim.chmod(shim.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    def _resolve_in_bash(self, tool: str, path_override=None) -> str | None:
        env = dict(os.environ)
        if path_override is not None:
            env["PATH"] = path_override
        proc = subprocess.run(
            [BASH, "-c", 'command -v -- "$1"', "_", tool],
            env=env, capture_output=True, text=True, timeout=30,
        )
        resolved = proc.stdout.strip()
        return resolved if proc.returncode == 0 and resolved else None

    def _require_each_shim_wins(self) -> None:
        """S1-e: verify each shim wins `command -v` under this test's PATH
        BEFORE running bootstrap; fail loudly if any does not.

        Compares by the shim dir's basename ("shim-bin") rather than the
        full absolute path — bash under MSYS reports its own mount-translated
        form (e.g. /tmp/...) for a Windows path, which never string-matches
        the Python-side `str(self._shim_dir)` even when it IS the same file.
        """
        for name in ("gh", "pip", "pip3", "winget", "brew", "apt-get", "sudo"):
            resolved = self._resolve_in_bash(name, self._path_override)
            self.assertIsNotNone(resolved, f"shim for '{name}' did not resolve at all under the override PATH")
            self.assertIn(
                self._shim_dir.name, resolved,
                f"shim for '{name}' did not WIN command -v (resolved to {resolved!r}, "
                f"not inside the shim dir {self._shim_dir}) — a real binary shadowed it",
            )

    def _base_env(self) -> dict:
        env = os.environ.copy()
        env["PATH"] = self._path_override
        env["GIT_SSH_COMMAND"] = str(self._fake_ssh).replace("\\", "/")
        env["FAKE_SSH_TARGET"] = str(self._bare)
        return env

    def _build_sandbox(self):
        """Clone this checkout's HEAD (never --branch develop — a CI
        checkout has no local develop branch pre-merge). Push it to a fresh
        bare 'origin' as `main` only (Sandbox D shape: new project, default
        roles), with origin's URL set to a real `git@host:owner/repo` string
        whose ssh transport is redirected to the local bare repo via
        GIT_SSH_COMMAND — so bootstrap.sh's resolve_origin_slug() sees a
        real owner/repo shape (verbatim from `git remote get-url`) while
        every git operation (push/fetch/ls-remote) stays entirely local."""
        work = self._tmpdir / "work"
        _git(["clone", "-q", str(REPO_ROOT), str(work)], self._tmpdir)
        _git(["config", "user.email", "test@example.com"], work)
        _git(["config", "user.name", "Test"], work)
        _git(["checkout", "-q", "-B", "main"], work)

        bare = self._tmpdir / "origin.git"
        _git(["init", "-q", "--bare", str(bare)], self._tmpdir)
        self._bare = bare

        _git(["remote", "set-url", "origin", "git@fakehost:example/throwaway.git"], work)
        env = self._base_env()
        push = _git(["push", "-q", "origin", "main"], work, check=False, env=env)
        if push.returncode != 0:
            self.fail(f"sandbox setup: initial push to fake origin failed: {push.stderr}")
        fetch = _git(["fetch", "-q", "--prune", "origin"], work, check=False, env=env)
        if fetch.returncode != 0:
            self.fail(f"sandbox setup: initial fetch from fake origin failed: {fetch.stderr}")

        return work

    def _run_bootstrap(self, gh_log_path: Path) -> subprocess.CompletedProcess:
        env = self._base_env()
        env["GH_SHIM_LOG"] = str(gh_log_path)
        return subprocess.run(
            [BASH, str(BOOTSTRAP_SH.resolve())],
            cwd=str(self._work), env=env, capture_output=True, text=True, timeout=120,
        )

    def test_creates_integration_branch_and_protects_both(self):
        gh_log = self._tmpdir / "gh.log"
        gh_log.touch()

        result = self._run_bootstrap(gh_log)
        self.assertIn(
            result.returncode, (0,),
            msg=f"bootstrap.sh exited {result.returncode}\nstdout={result.stdout}\nstderr={result.stderr}",
        )

        # (1) develop created at main's tip.
        develop_sha = _git(["rev-parse", "refs/heads/develop"], self._tmpdir / "origin.git").stdout.strip()
        main_sha = _git(["rev-parse", "refs/heads/main"], self._tmpdir / "origin.git").stdout.strip()
        self.assertTrue(develop_sha, "origin has no 'develop' branch after bootstrap.sh")
        self.assertEqual(develop_sha, main_sha, "'develop' must be created at 'main's tip")

        # (2) exactly two protection PUT paths, each with the R4 ci payload
        #     and enforce_admins false. REVERSED from #1294: main protection
        #     is now REQUIRED present, not forbidden.
        log_text = gh_log.read_text(encoding="utf-8")
        put_lines = [
            l for l in log_text.splitlines()
            if "-X PUT" in l and "/protection" in l
        ]
        develop_puts = [l for l in put_lines if "branches/develop/protection" in l]
        main_puts = [l for l in put_lines if "branches/main/protection" in l]
        self.assertEqual(len(develop_puts), 1, f"expected exactly 1 develop protection PUT, log:\n{log_text}")
        self.assertEqual(len(main_puts), 1, f"expected exactly 1 main protection PUT (REVERSED #1294 assertion), log:\n{log_text}")
        other_puts = [l for l in put_lines if l not in develop_puts and l not in main_puts]
        self.assertEqual(other_puts, [], f"no protection PUT besides develop/main is expected: {other_puts}")

        for label, lines in (("develop", develop_puts), ("main", main_puts)):
            line = lines[0]
            self.assertIn('"context": "ci"', line, f"{label} protection PUT missing the R4 ci check: {line}")
            self.assertIn('"enforce_admins": false', line, f"{label} protection PUT must have enforce_admins false: {line}")

    def test_second_run_is_a_no_op(self):
        gh_log = self._tmpdir / "gh.log"
        gh_log.touch()
        first = self._run_bootstrap(gh_log)
        self.assertEqual(first.returncode, 0, f"first run failed: {first.stderr}")
        before = _git(["rev-parse", "refs/heads/develop"], self._tmpdir / "origin.git").stdout.strip()

        gh_log2 = self._tmpdir / "gh2.log"
        gh_log2.touch()
        second = self._run_bootstrap(gh_log2)
        self.assertEqual(
            second.returncode, 0,
            msg=f"second bootstrap.sh run exited {second.returncode}\nstdout={second.stdout}\nstderr={second.stderr}",
        )
        after = _git(["rev-parse", "refs/heads/develop"], self._tmpdir / "origin.git").stdout.strip()
        self.assertEqual(before, after, "second bootstrap.sh run must leave 'develop' unmoved (idempotent no-op)")


if __name__ == "__main__":
    unittest.main()
