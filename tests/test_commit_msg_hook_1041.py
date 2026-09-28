"""
Regression tests for slice #1041 — commit-msg hook blocks >72-char and
uppercase-after-colon subjects at commit time, mirroring reviewer
R-CONV-COMMITS + CI tools/ci-checks.sh CHECK 3 exactly, WITHOUT being
stricter than CI on merge/revert/fixup commits (root-cause: #1017/#824/#869).

Root cause (verbatim, from issue #1041): "the only enforcement is post-push
CI; nothing blocks at commit time" — ~5 PRs in one session needed reword +
re-review because a bad subject wasn't caught until CI ran on the pushed PR.

This test invokes .githooks/commit-msg as a real subprocess (git's commit-msg
hook contract: $1 is the path to the commit message file), so it exercises
the actual hook that fires on `git commit`, not a re-implementation.

All assertions are offline (subprocess against a temp message file); no
network calls; deterministic on Windows Git Bash / POSIX sh.

Runner: stdlib unittest + pytest compatible.
  python -m pytest tests/test_commit_msg_hook_1041.py -v
  python -m unittest tests.test_commit_msg_hook_1041 -v
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
HOOK = REPO_ROOT / ".claude" / "pipeline" / "githooks" / "commit-msg"

# Locate a POSIX shell to invoke the hook with, mirroring how git itself
# invokes commit-msg hooks (via the shebang, or via sh on platforms where
# the executable bit / shebang isn't honored, e.g. some Windows setups).
SH = os.environ.get("COMMIT_MSG_TEST_SH", "bash")


def _run_hook(message_text):
    """Write message_text to a temp file and invoke the hook against it.
    Returns (returncode, stdout, stderr).
    """
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, encoding="utf-8"
    ) as f:
        f.write(message_text)
        msg_path = f.name
    try:
        result = subprocess.run(
            [SH, str(HOOK), msg_path],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return result.returncode, result.stdout, result.stderr
    finally:
        try:
            os.unlink(msg_path)
        except OSError:
            pass


class TestCommitMsgHookExists(unittest.TestCase):
    """Sanity: the hook file must exist before behavioral tests can run.
    This is the test-first assertion that FAILS before the fix is applied
    (in the pre-fix state on this branch's working tree, prior to the
    merge/revert-exemption fix commit) and PASSES after.
    """

    def test_hook_file_exists(self):
        self.assertTrue(
            HOOK.is_file(),
            f".githooks/commit-msg not found at {HOOK}",
        )


class TestCommitMsgHookRejectsOverLength(unittest.TestCase):
    """A subject >72 chars must be rejected (exit != 0), matching CI CHECK 3's
    72-char cap and reviewer R-CONV-COMMITS.
    """

    def test_over_72_chars_rejected(self):
        if not HOOK.is_file():
            self.skipTest("hook not present yet (test-first, pre-fix)")
        subject = (
            "feat: this is uppercase and also deliberately way over "
            "the seventy two char cap xx"
        )
        # Force lowercase-after-colon to isolate the length violation only.
        subject = "feat: this subject is deliberately long enough to exceed the seventy two character cap for sure"
        self.assertGreater(len(subject), 72)
        rc, out, err = _run_hook(subject + "\n")
        self.assertNotEqual(rc, 0, f"expected reject; stdout={out!r} stderr={err!r}")


class TestCommitMsgHookRejectsUppercaseAfterColon(unittest.TestCase):
    """A subject with an uppercase first char after '<type>(<scope>): ' must
    be rejected (exit != 0), matching reviewer R-CONV-COMMITS lowercase rule.
    """

    def test_uppercase_after_colon_rejected(self):
        if not HOOK.is_file():
            self.skipTest("hook not present yet (test-first, pre-fix)")
        subject = "feat: Add Ship Skill"
        rc, out, err = _run_hook(subject + "\n")
        self.assertNotEqual(rc, 0, f"expected reject; stdout={out!r} stderr={err!r}")


class TestCommitMsgHookAcceptsValidSubject(unittest.TestCase):
    """A well-formed subject (lowercase after colon, <=72 chars, valid type)
    must be accepted (exit 0).
    """

    def test_valid_subject_accepted(self):
        if not HOOK.is_file():
            self.skipTest("hook not present yet (test-first, pre-fix)")
        subject = "feat(hooks): commit-msg blocks bad subjects"
        self.assertLessEqual(len(subject), 72)
        rc, out, err = _run_hook(subject + "\n")
        self.assertEqual(rc, 0, f"expected accept; stdout={out!r} stderr={err!r}")


class TestCommitMsgHookAllowsMergeAndRevert(unittest.TestCase):
    """Merge/revert/fixup subjects must be ALLOWED even though they don't
    match the Conventional Commits type prefix — mirroring how CI CHECK 3
    handles them (git log --no-merges excludes merges from the range
    entirely; reviewer R-CONV-COMMITS explicitly exempts git revert's
    auto-generated 'Revert "..."' shape). A hook stricter than CI here
    would block legitimate merge/revert commits (acceptance criterion:
    "don't be stricter than CI or you'll block legit commits").
    """

    def test_merge_commit_subject_allowed(self):
        if not HOOK.is_file():
            self.skipTest("hook not present yet (test-first, pre-fix)")
        subject = "Merge branch 'develop' into fix/1041-commit-msg-hook"
        rc, out, err = _run_hook(subject + "\n")
        self.assertEqual(rc, 0, f"merge commit should be allowed; stdout={out!r} stderr={err!r}")

    def test_revert_commit_subject_allowed(self):
        if not HOOK.is_file():
            self.skipTest("hook not present yet (test-first, pre-fix)")
        subject = 'Revert "feat: add ship skill"'
        rc, out, err = _run_hook(subject + "\n")
        self.assertEqual(rc, 0, f"revert commit should be allowed; stdout={out!r} stderr={err!r}")

    def test_fixup_commit_subject_allowed(self):
        if not HOOK.is_file():
            self.skipTest("hook not present yet (test-first, pre-fix)")
        subject = "fixup! feat(hooks): commit-msg blocks bad subjects"
        rc, out, err = _run_hook(subject + "\n")
        self.assertEqual(rc, 0, f"fixup commit should be allowed; stdout={out!r} stderr={err!r}")


if __name__ == "__main__":
    unittest.main()
