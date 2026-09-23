#!/usr/bin/env python3
"""pre-tool-bash-classify.py — single-spawn classifier for pre-tool-bash.sh.

Reads the full PreToolUse(Bash) hook payload from stdin, extracts
tool_input.command, classifies it via the SAME clause-anchored tokenizer
that ADR-0076 D4 / PR #1141 landed (byte-identical decisions is a hard
contract of ADR-0079 D3 / slice #1198), and prints:

  line 1: outcome marker -- "deny" | "warn" | "allow"
  line 2 (only for deny/warn): the compact hookSpecificOutput/systemMessage
          JSON pre-tool-bash.sh relays to Claude Code -- unchanged in shape
          and message text from the pre-consolidation jq-per-flag version.

Also appends an `outcome`-carrying beacon line to
$_PTB_BEACON_DIR/hook-fires.jsonl (best-effort -- a logging failure never
changes the printed decision; ADR-0079 D3's additive beacon field).

Any unhandled exception here is surfaced as a non-zero exit with no stdout;
pre-tool-bash.sh treats that as an internal error (ERROR beacon, fail-open
exit 0), preserving HOK-008.

Replaces the eight sequential jq spawns the pre-consolidation script made on
every allow-path fire (one CMD extraction, two classifier-JSON shape-
validity checks, five per-flag `_flag()` reads) with this ONE python3
invocation -- ADR-0079 D3 / slice #1198.
"""
import importlib.util
import json
import os
import re
import shlex
import sys
from datetime import datetime, timezone

BOUNDARY_TOKENS = {";", "&", "&&", "|", "||"}
ENV_PREFIX_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*=')

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PIPELINE_CONFIG_PY = os.path.join(_THIS_DIR, "..", "..", "tools", "pipeline_config.py")


def _load_pipeline_config():
    # S1-c: located relative to THIS file's own path, never via cwd or
    # `git rev-parse --show-toplevel` of the caller's cwd repo. Imports the
    # resolver in-process (ADR-0089 D1) -- no subprocess, so this respects
    # ADR-0079 D3's single-spawn hook-hot-path diet (S2-a).
    spec = importlib.util.spec_from_file_location(
        "pipeline_config", _PIPELINE_CONFIG_PY
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _refspec_release_re():
    """Build the release-branch refspec-deny regex fresh on every call
    (S2-ct: role sites resolve at call time, never at module import) --
    ADR-0089 D1: the refspec deny set is built from the release role."""
    release = re.escape(_load_pipeline_config().release_branch())
    return re.compile(
        r'(\borigin\s+' + release + r'\b|:' + release + r'\b|\brefs/heads/' + release + r'\b)'
    )


def _tokenize_line(line):
    """Unchanged from the pre-consolidation classifier (ADR-0076 D4 / PR
    #1141): clause-splitting on unquoted shell separators via
    shlex.shlex(posix=True, punctuation_chars=True) -- quotes are honored, a
    quoted phrase collapses into ONE token."""
    try:
        lex = shlex.shlex(line, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        tokens = list(lex)
    except ValueError:
        # Unbalanced quote etc. -- never crash; treat the raw line as ONE
        # clause via a plain whitespace split (fail-safe, not fail-silent).
        tokens = line.split()
        return [tokens] if tokens else []
    clauses, current = [], []
    for tok in tokens:
        if tok in BOUNDARY_TOKENS:
            if current:
                clauses.append(current)
            current = []
        else:
            current.append(tok)
    if current:
        clauses.append(current)
    return clauses


def _clauses(cmd):
    out = []
    for line in cmd.split("\n"):
        out.extend(_tokenize_line(line))
    return out


def _strip_env_prefix(tokens):
    i = 0
    while i < len(tokens) and ENV_PREFIX_RE.match(tokens[i]):
        i += 1
    return tokens[i:]


def classify(cmd):
    """Unchanged decision-flag logic from the pre-consolidation script.

    S2-14: resolves (and thereby validates) the release role unconditionally
    on every call, regardless of the command's own shape -- a malformed
    `.claude/pipeline.conf` must raise (PipelineConfigError, uncaught) for
    ANY payload, not only a push-shaped one, so pre-tool-bash.sh's existing
    classifier-failed path (ERROR beacon, fail-open) fires per PRD #1500 §2
    criterion 14's own verification recipe (a generic payload command).
    No subprocess is spawned by this resolve (S2-a: in-process importlib
    load only).
    """
    _load_pipeline_config().release_branch()
    deny_gh_merge = deny_push_main = deny_promote_invocation = warn_wip = False
    warn_issue_create_label = False
    for tokens in _clauses(cmd):
        t = _strip_env_prefix(tokens)
        if not t:
            continue
        head = t[0]
        if head == "gh" and len(t) >= 3 and t[1] == "pr" and t[2] == "merge":
            deny_gh_merge = True
        if (head in ("bash", "sh") and len(t) >= 2 and t[1].endswith("tools/promote.sh")) \
           or head.endswith("tools/promote.sh"):
            deny_promote_invocation = True
        if head == "git" and len(t) >= 2 and t[1] == "push":
            if _refspec_release_re().search(" ".join(t[2:])):
                deny_push_main = True
        if head == "git" and len(t) >= 2 and t[1] == "commit":
            if "-m" in t[2:] and any("WIP" in tok for tok in t[2:]):
                warn_wip = True
        if head == "gh" and len(t) >= 3 and t[1] == "issue" and t[2] == "create":
            rest = t[3:]
            for i, tok in enumerate(rest):
                if tok == "--label" and i + 1 < len(rest):
                    parts = rest[i + 1].split(",")
                    if "slice" in parts or "prd" in parts:
                        warn_issue_create_label = True
    return {
        "deny_gh_merge": deny_gh_merge,
        "deny_push_main": deny_push_main,
        "deny_promote_invocation": deny_promote_invocation,
        "warn_wip": warn_wip,
        "warn_issue_create_label": warn_issue_create_label,
    }


def is_subagent(payload):
    """ADR-0091 D1: a hook fired inside a subagent call carries a non-empty
    `agent_id` in its stdin payload; the main thread's payload never does
    (`agent_type` alone is not a marker -- an `--agent` session's main
    thread carries it too). No environment variable carries this signal."""
    if not isinstance(payload, dict):
        return False
    return bool(str(payload.get("agent_id") or "").strip())


def decide(cmd, subagent):
    """Same priority-order cascade as the pre-consolidation bash script's
    sequential if/exit chain -- first match wins, byte-identical message
    text. Order: push-main deny > WIP warn > issue-create-label warn >
    promote-subagent deny > gh-merge deny > allow."""
    if not cmd:
        return "allow", None
    flags = classify(cmd)
    if flags["deny_push_main"]:
        release = _load_pipeline_config().release_branch()
        return "deny", (
            f'Direct push to {release} forbidden per CLAUDE.md rule #4 (all refspec '
            f'forms denied: origin {release}, HEAD:{release}, refs/heads/{release}, :{release}); '
            'open a PR instead.'
        )
    if flags["warn_wip"]:
        return "warn", (
            'WIP commit detected — CLAUDE.md rule #5 discourages WIP messages; '
            'prefer Conventional Commits.'
        )
    if flags["warn_issue_create_label"]:
        return "warn", (
            'gh issue create --label slice|prd detected — slices and PRDs are '
            'normally posted via /to-issues or /to-prd (which gate through '
            'slicer-critic/prd-critic); this raw call still proceeds. The #918 '
            'hand-created-slice class is caught deterministically via CI CHECK 19 '
            '+ the SLICE-VS-PR reconciler.'
        )
    if flags["deny_promote_invocation"] and subagent:
        return "deny", (
            'tools/promote.sh may not run from a subagent context — promotion is '
            'a human-gated orchestrator action (see #880); only the orchestrator '
            'invokes it directly.'
        )
    if flags["deny_gh_merge"]:
        return "deny", (
            'Raw `gh pr merge` denied — bypasses tools/pipe/pr-merge (no v3 '
            'pr_merged span, no APPROVE-verdict assertion). Use '
            '`python tools/pipe/pr-merge <PR>` instead.'
        )
    return "allow", None


def _write_outcome_beacon(beacon_dir, outcome):
    """Best-effort -- a logging failure never changes the printed decision."""
    if not beacon_dir:
        return
    try:
        os.makedirs(beacon_dir, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        line = json.dumps(
            {"hook": "pre-tool-bash", "status": "ok", "ts": ts, "outcome": outcome},
            separators=(",", ":"),
        )
        with open(os.path.join(beacon_dir, "hook-fires.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


def main():
    # LF-only stdout regardless of platform -- on Windows, Python's default
    # text-mode stdout translates "\n" to "\r\n", which would corrupt the
    # bash caller's exact-match on the outcome marker line. Reconfigure to
    # binary-safe LF output (Python 3.7+; stdlib only).
    try:
        sys.stdout.reconfigure(newline="\n")
    except AttributeError:
        pass
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
        cmd = (payload.get("tool_input") or {}).get("command") or ""
        subagent = is_subagent(payload)
    except Exception:
        cmd = ""
        subagent = False
    outcome, message = decide(cmd, subagent)
    _write_outcome_beacon(os.environ.get("_PTB_BEACON_DIR", ""), outcome)
    print(outcome)
    if outcome == "deny":
        print(json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": message,
                }
            },
            separators=(",", ":"),
        ))
    elif outcome == "warn":
        print(json.dumps({"systemMessage": message}, separators=(",", ":")))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        sys.stderr.write(f"pre-tool-bash-classify internal error: {exc}\n")
        sys.exit(1)
