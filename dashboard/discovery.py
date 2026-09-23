"""
dashboard/discovery.py — filesystem discovery helpers for the dashboard.

Exports:
    discover_skills()
    discover_agents()
    discover_hooks()
    discover_adrs()
    discover_edges()

Import direction: server <- discovery (this module must NOT import server).
"""

import json
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Repo root — discovery.py lives at <repo>/dashboard/discovery.py
# ---------------------------------------------------------------------------
_DISCOVERY_REPO_ROOT = Path(__file__).resolve().parent.parent

# sys.path injection so telemetry_root is importable when discovery.py is
# imported standalone (e.g. by tests).
_DASHBOARD_DIR_STR = str(Path(__file__).resolve().parent)
if _DASHBOARD_DIR_STR not in sys.path:
    sys.path.insert(0, _DASHBOARD_DIR_STR)

from telemetry_root import _telemetry_log_root  # noqa: E402

# git-aware enumeration helper (issue #999 — same fix class as #926 for health.py)
# Lazily imported to keep the module usable even if _gitfiles is somehow missing.
try:
    from _gitfiles import tracked_files as _tracked_files
except ImportError:
    _tracked_files = None  # type: ignore[assignment]

# Known critics — single-sourced from _constants.py (CHECK 7 regexes that
# file's literal; ADR-0088 D4). Aliased to the pre-existing local name so
# every downstream reference in this module is unchanged.
from _constants import KNOWN_CRITICS as _KNOWN_CRITICS  # noqa: E402

_KNOWN_GENERATORS = {
    "slicer",
    "implementer",
    "qa-tester",
}


def _parse_frontmatter(path: Path) -> dict:
    """Extract YAML frontmatter fields from a markdown file (name, description, role)."""
    fields = {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        if not text.startswith("---"):
            return fields
        end = text.find("\n---", 3)
        if end == -1:
            return fields
        fm_block = text[3:end]
        for line in fm_block.splitlines():
            m = re.match(r'^(\w+):\s*(.+)$', line.strip())
            if m:
                fields[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    except Exception:
        pass
    return fields


def _classify_agent(stem: str, description: str) -> str:
    """Classify an agent as 'critic' or 'generator'.

    Priority: explicit allow-lists first (both directions), then description heuristic.
    slicer is a generator even though its description mentions 'slicer-critic'.
    """
    if stem in _KNOWN_GENERATORS:
        return "generator"
    if stem in _KNOWN_CRITICS:
        return "critic"
    # Description heuristic: look for standalone 'critic' word, not substring of another token
    if re.search(r'\bcritic\b', description.lower()):
        return "critic"
    return "generator"


def discover_skills() -> list:
    skills_dir = _DISCOVERY_REPO_ROOT / ".claude" / "skills"
    skills = []
    if not skills_dir.exists():
        return skills
    # Enumerate via git ls-files so untracked/stale dirs are invisible (#999).
    # Graceful fallback to fs-glob when git is unavailable (non-git context).
    tracked = (
        _tracked_files(_DISCOVERY_REPO_ROOT, ".claude/skills/*/SKILL.md")
        if _tracked_files is not None
        else None
    )
    if tracked is not None:
        skill_mds = sorted(tracked)
    else:
        skill_mds = sorted(skills_dir.glob("*/SKILL.md"))
    for skill_md in skill_mds:
        fm = _parse_frontmatter(skill_md)
        skills.append({
            "name": fm.get("name", skill_md.parent.name),
            "description": fm.get("description", ""),
            "path": skill_md.relative_to(_DISCOVERY_REPO_ROOT).as_posix(),
        })
    return skills


def discover_agents() -> list:
    agents_dir = _DISCOVERY_REPO_ROOT / ".claude" / "agents"
    agents = []
    if not agents_dir.exists():
        return agents
    # Enumerate via git ls-files so untracked/stale files are invisible (#999).
    # Graceful fallback to fs-glob when git is unavailable (non-git context).
    tracked = (
        _tracked_files(_DISCOVERY_REPO_ROOT, ".claude/agents/*.md")
        if _tracked_files is not None
        else None
    )
    if tracked is not None:
        agent_mds = sorted(tracked)
    else:
        agent_mds = sorted(agents_dir.glob("*.md"))
    for agent_md in agent_mds:
        fm = _parse_frontmatter(agent_md)
        stem = agent_md.stem
        description = fm.get("description", "")
        kind = _classify_agent(stem, description)
        agents.append({
            "name": fm.get("name", stem),
            "stem": stem,
            "type": kind,
            "description": description,
            "path": agent_md.relative_to(_DISCOVERY_REPO_ROOT).as_posix(),
        })
    return agents


def _read_hook_description(cmd: str) -> str:
    """Derive a human-readable description for a hook command.

    For .sh-script hooks: read the script's leading comment block (lines after
    the shebang that start with '#').  For inline jq/bash commands: derive a
    short string from the command pattern.
    """
    m = re.search(r'hooks/([a-z0-9_-]+\.sh)', cmd)
    if m:
        script_path = _DISCOVERY_REPO_ROOT / ".claude" / "hooks" / m.group(1)
        if script_path.exists():
            try:
                lines = script_path.read_text(encoding="utf-8", errors="replace").splitlines()
                for line in lines:
                    stripped = line.strip()
                    if stripped.startswith("#!"):
                        continue  # skip shebang
                    if stripped.startswith("#"):
                        text = stripped.lstrip("#").strip()
                        if text:
                            return text[:100]
                    elif stripped:
                        break  # end of leading comment block
            except Exception:
                pass

    # Inline command patterns: derive description from content
    cmd_lower = cmd.lower()
    if "workflow-events.jsonl" in cmd_lower and "agent_start" in cmd_lower:
        return "logs agent start events to workflow-events.jsonl"
    if "workflow-events.jsonl" in cmd_lower and "agent_complete" in cmd_lower:
        return "logs agent completions to workflow-events.jsonl"
    if "workflow-events.jsonl" in cmd_lower and "bash_complete" in cmd_lower:
        return "logs bash completions to workflow-events.jsonl"
    if "workflow-events.jsonl" in cmd_lower and "session_stop" in cmd_lower:
        return "logs session-stop event to workflow-events.jsonl"
    if "subagent-edits.log" in cmd_lower:
        return "logs agent file edits; notes AS-AUDIT runs in CI CHECK 18"
    if "workflow-events.jsonl" in cmd_lower:
        return "logs workflow event to workflow-events.jsonl"

    # Fallback: truncated raw command
    return cmd[:80]


def _read_hook_name(cmd: str) -> str:
    """Derive a short human-readable name for a hook command.

    For .sh-script hooks: returns the filename stem (e.g. 'session-start').
    For inline hooks: returns a concise label matching the event logged.
    """
    m = re.search(r'hooks/([a-z0-9_-]+)\.sh', cmd)
    if m:
        return m.group(1)

    # Inline command patterns: derive clean name from content
    cmd_lower = cmd.lower()
    if "workflow-events.jsonl" in cmd_lower and "agent_start" in cmd_lower:
        return "agent_start logger"
    if "workflow-events.jsonl" in cmd_lower and "agent_complete" in cmd_lower:
        return "agent_complete logger"
    if "workflow-events.jsonl" in cmd_lower and "bash_complete" in cmd_lower:
        return "bash logger"
    if "workflow-events.jsonl" in cmd_lower and "session_stop" in cmd_lower:
        return "session_stop logger"
    if "workflow-events.jsonl" in cmd_lower and "skill_invoke" in cmd_lower:
        return "skill_invoke logger"
    if "workflow-events.jsonl" in cmd_lower and "grill_qa" in cmd_lower:
        return "grill_qa logger"
    if "subagent-edits.log" in cmd_lower:
        return "subagent-edit nudge"
    if "workflow-events.jsonl" in cmd_lower:
        return "workflow event logger"

    # Fallback: short truncation (not full raw command)
    return cmd[:30].strip()


def _read_hook_purpose(cmd: str) -> str:
    """Return the header-comment block of a hook script (slice #628).

    Reads all contiguous '#'-prefixed lines after the shebang line, before
    the first non-comment non-blank line.  Strips leading '# ' / '#' from
    each line, joins with newlines, truncates to 800 chars.
    Fail-soft: returns "" if the script has no header comment or cannot be read.
    """
    m = re.search(r'hooks/([a-z0-9_-]+\.sh)', cmd)
    if not m:
        return ""
    script_path = _DISCOVERY_REPO_ROOT / ".claude" / "hooks" / m.group(1)
    if not script_path.exists():
        return ""
    try:
        lines = script_path.read_text(encoding="utf-8", errors="replace").splitlines()
        comment_lines: list[str] = []
        past_shebang = False
        for line in lines:
            stripped = line.strip()
            if not past_shebang and stripped.startswith("#!"):
                past_shebang = True
                continue
            if stripped.startswith("#"):
                text = stripped[1:].lstrip(" ")
                comment_lines.append(text)
            elif stripped == "":
                # blank lines inside header are included; stop on non-blank non-comment
                if comment_lines:
                    comment_lines.append("")
            else:
                break  # first non-comment non-blank line — header block ends
        # Strip trailing blank lines accumulated at the end
        while comment_lines and comment_lines[-1] == "":
            comment_lines.pop()
        purpose = "\n".join(comment_lines)
        return purpose[:800]
    except Exception:
        return ""


def _read_hook_fire_telemetry() -> dict:
    """Read hook-fires.jsonl and aggregate per-event-type fire_count + error_count + last_fired.

    Keys telemetry on the event-type name carried in each beacon's ``hook`` field
    (e.g. session_start, agent_complete) — distinct per-event-type entries instead
    of one shared bucket per script file.

    fire_count  = attempt beacons (status == "attempt" or no status field on legacy lines).
    error_count = error beacons (status == "error").
    last_fired  = most recent ts across all beacons for that event type.

    Reads only the last ~5000 lines to avoid unbounded growth becoming slow.
    Fail-soft: returns empty dict on any error (every event type will show 0/null).
    """
    beacon_path = _telemetry_log_root() / ".claude" / "logs" / "hook-fires.jsonl"
    telemetry: dict = {}
    if not beacon_path.exists():
        return telemetry
    try:
        # Read last ~5000 lines only
        text = beacon_path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        lines = lines[-5000:] if len(lines) > 5000 else lines
        for raw in lines:
            raw = raw.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except Exception:
                continue
            event_name = obj.get("hook", "")
            ts = obj.get("ts", "")
            status = obj.get("status", "")
            if not event_name:
                continue
            entry = telemetry.setdefault(
                event_name,
                {"fire_count": 0, "error_count": 0, "last_fired": None},
            )
            # fire_count: attempt beacons; legacy lines (no status) also count as attempts.
            if status in ("attempt", ""):
                entry["fire_count"] += 1
            elif status == "error":
                entry["error_count"] += 1
            if ts and (entry["last_fired"] is None or ts > entry["last_fired"]):
                entry["last_fired"] = ts
    except Exception:
        pass
    return telemetry


def _event_type_from_cmd(cmd: str) -> str:
    """Extract the event-type argument from a log-tool-event.sh command string.

    Commands look like: bash "...log-tool-event.sh" session_start
    Returns the event-type token (e.g. "session_start") if present, else "".
    This is used as the telemetry key so each registered event type gets its own
    distinct fire_count / error_count bucket instead of collapsing under the script
    file name.
    """
    m = re.search(r'log-tool-event\.sh["\s]+([a-z_]+)', cmd)
    if m:
        return m.group(1)
    return ""


# AUTO-MODE derived-key tables — MUST mirror log-tool-event.sh's python block
# (_PRE / _POST dicts) exactly. When event_type_arg == "auto", the script does
# NOT beacon "ok"/"error" under the literal "auto" key: it derives the real
# event_type at runtime from (hook_event_name, tool_name) and beacons under
# THAT name instead (see log-tool-event.sh AUTO-MODE block). Only the bash-
# level "attempt" beacon (written before parsing) ever uses the literal "auto"
# argument. So an "auto"-registered hook's telemetry must be aggregated across
# the whole set of derived keys its (event, matcher) pair can produce — never
# just the single literal "auto" key (slice #1056; classification gap, not
# the #1021/#1052 path-routing class).
_AUTO_PRE_MAP = {"Agent": "agent_start", "Skill": "skill_invoke"}
_AUTO_POST_MAP = {"Agent": "agent_complete", "Bash": "bash_complete", "AskUserQuestion": "grill_qa"}
_AUTO_PRE_FALLBACK = "pre_tool"
_AUTO_POST_FALLBACK = "post_tool"


def _auto_mode_derived_keys(event: str, matcher: str) -> list:
    """Return the set of telemetry keys an "auto"-mode hook's beacons can carry.

    Given the settings.json `event` (PreToolUse/PostToolUse) and `matcher`
    (pipe-separated tool-name alternation, e.g. "Agent|Skill"), returns every
    distinct derived event-type key log-tool-event.sh's AUTO-MODE block could
    produce for a tool call matching that matcher — mirroring the _PRE/_POST
    dicts + "pre_tool"/"post_tool" fallback in log-tool-event.sh verbatim.
    """
    tool_names = [t for t in matcher.split("|") if t] or [""]
    is_post = event == "PostToolUse"
    table = _AUTO_POST_MAP if is_post else _AUTO_PRE_MAP
    fallback = _AUTO_POST_FALLBACK if is_post else _AUTO_PRE_FALLBACK
    keys = []
    for tool_name in tool_names:
        key = table.get(tool_name, fallback)
        if key not in keys:
            keys.append(key)
    return keys


def _aggregate_auto_telemetry(telemetry: dict, event: str, matcher: str) -> dict:
    """Sum fire_count/error_count and max last_fired across an auto hook's derived keys."""
    agg = {"fire_count": 0, "error_count": 0, "last_fired": None}
    for key in _auto_mode_derived_keys(event, matcher):
        data = telemetry.get(key)
        if not data:
            continue
        agg["fire_count"] += data.get("fire_count", 0)
        agg["error_count"] += data.get("error_count", 0)
        last = data.get("last_fired")
        if last and (agg["last_fired"] is None or last > agg["last_fired"]):
            agg["last_fired"] = last
    return agg


def discover_hooks() -> list:
    settings_path = _DISCOVERY_REPO_ROOT / ".claude" / "settings.json"
    hooks = []
    if not settings_path.exists():
        return hooks
    telemetry = _read_hook_fire_telemetry()
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8"))
        for event, entries in data.get("hooks", {}).items():
            for entry in entries:
                matcher = entry.get("matcher", "")
                for hook in entry.get("hooks", []):
                    cmd = hook.get("command", "")
                    # Extract script name; resolve to actual .sh path when available (Fix C)
                    m = re.search(r'hooks/([a-z0-9_-]+\.sh)', cmd)
                    if m:
                        script_name = m.group(1)
                        name = script_name
                        script_path = _DISCOVERY_REPO_ROOT / ".claude" / "hooks" / script_name
                        hook_path = (
                            f".claude/hooks/{script_name}"
                            if script_path.exists()
                            else ".claude/settings.json"
                        )
                    else:
                        name = cmd[:60]
                        hook_path = ".claude/settings.json"
                    clean_name = _read_hook_name(cmd)
                    # For log-tool-event.sh registrations, use the event-type argument
                    # as the telemetry key so each event type gets its own distinct bucket
                    # (session_start, agent_complete, …) instead of collapsing under the
                    # shared script stem "log-tool-event".
                    event_type_arg = _event_type_from_cmd(cmd)
                    telemetry_key = event_type_arg if event_type_arg else clean_name
                    if event_type_arg == "auto":
                        # AUTO-MODE (slice #1056): the literal "auto" argument is never
                        # the beacon key for status ok/error — aggregate across the
                        # runtime-derived key set instead (see _auto_mode_derived_keys).
                        fire_data = _aggregate_auto_telemetry(telemetry, event, matcher)
                    else:
                        fire_data = telemetry.get(
                            telemetry_key,
                            {"fire_count": 0, "error_count": 0, "last_fired": None},
                        )
                    hooks.append({
                        "name": telemetry_key if event_type_arg else clean_name,
                        "clean_name": telemetry_key if event_type_arg else clean_name,
                        "event": event,
                        "matcher": matcher,
                        "command": cmd[:120],
                        "description": _read_hook_description(cmd),  # Fix C
                        "path": hook_path,  # Fix C: resolved .sh path
                        "purpose": _read_hook_purpose(cmd),  # slice #628
                        "fire_count": fire_data["fire_count"],
                        "error_count": fire_data.get("error_count", 0),
                        "last_fired": fire_data["last_fired"],
                    })
    except Exception:
        pass
    return hooks


def discover_adrs() -> list:
    decisions_dir = _DISCOVERY_REPO_ROOT / "decisions"
    adrs = []
    if not decisions_dir.exists():
        return adrs
    # Enumerate via git ls-files so untracked/stale files are invisible (#999).
    # Graceful fallback to fs-glob when git is unavailable (non-git context).
    tracked = (
        _tracked_files(_DISCOVERY_REPO_ROOT, "decisions/[0-9]*.md")
        if _tracked_files is not None
        else None
    )
    if tracked is not None:
        adr_files = sorted(tracked)
    else:
        adr_files = sorted(decisions_dir.glob("[0-9]*.md"))
    for adr_file in adr_files:
        title = ""
        try:
            for line in adr_file.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("# "):
                    title = line[2:].strip()
                    break
        except Exception:
            pass
        adrs.append({
            "name": adr_file.stem,
            "title": title,
            "path": adr_file.relative_to(_DISCOVERY_REPO_ROOT).as_posix(),
        })
    return adrs


def discover_edges() -> list:
    """Infer component-reference edges from canonical file bodies (body-grep).

    NOTE (slice #629, ADR-0039 D2): this is a COMPONENT-REFERENCE graph, NOT
    the canonical workflow topology. The authoritative sequential pipeline flow
    lives in the SPEC v2 (pipeline_spec.py). This function's edge-inference
    output was rendered by the Architecture topology graph's "Inferred edges"
    cross-reference view — a supplementary view, never the primary topology —
    before that UI was retired along with the rest of the served dashboard
    (ADR-0088 D1); it currently has no consumer.

    Edge types:
      skill -> agent  : skill SKILL.md body mentions a known agent stem name
      agent -> adr    : agent body cites ADR-NNNN or decisions/NNNN-
      hook  -> event  : each hook fires on its declared event

    Conservative: match agent stem names only (not path fragments).
    Deduplicate. Cap at 200 edges with a stderr warning if exceeded.
    """
    import sys as _sys
    edges: list = []
    seen: set = set()

    def add(source: str, stype: str, target: str, ttype: str, label: str = ""):
        key = (source, target)
        if key not in seen:
            seen.add(key)
            edges.append({"source": source, "sourceType": stype,
                          "target": target, "targetType": ttype,
                          "label": label})

    # Collect agent stems for conservative name matching
    agents_dir = _DISCOVERY_REPO_ROOT / ".claude" / "agents"
    agent_stems: set = set()
    if agents_dir.exists():
        for agent_md in agents_dir.glob("*.md"):
            agent_stems.add(agent_md.stem)

    # hook -> event: each configured hook fires on its event (collected first,
    # small set, so they survive if the cap fires)
    settings_path = _DISCOVERY_REPO_ROOT / ".claude" / "settings.json"
    if settings_path.exists():
        try:
            data = json.loads(settings_path.read_text(encoding="utf-8"))
            for event, entries in data.get("hooks", {}).items():
                for entry in entries:
                    for hook in entry.get("hooks", []):
                        cmd = hook.get("command", "")
                        m = re.search(r'hooks/([a-z0-9_-]+\.sh)', cmd)
                        hook_id = m.group(1) if m else cmd[:40]
                        add(hook_id, "hook", event, "event", "fires on")
        except Exception:
            pass

    # skill -> agent: skill body mentions an agent stem as a standalone word
    skills_dir = _DISCOVERY_REPO_ROOT / ".claude" / "skills"
    if skills_dir.exists():
        for skill_md in sorted(skills_dir.glob("*/SKILL.md")):
            skill_name = skill_md.parent.name
            try:
                body = skill_md.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            for stem in sorted(agent_stems):
                # Word-boundary on stem (hyphenated stems need explicit boundary)
                pattern = r'(?<![a-z0-9-])' + re.escape(stem) + r'(?![a-z0-9-])'
                if re.search(pattern, body):
                    add(skill_name, "skill", stem, "agent", "invokes")

    # agent -> adr: agent body cites ADR-NNNN or decisions/NNNN-
    decisions_dir = _DISCOVERY_REPO_ROOT / "decisions"
    if agents_dir.exists():
        for agent_md in sorted(agents_dir.glob("*.md")):
            stem = agent_md.stem
            try:
                body = agent_md.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            adr_nums: set = set(re.findall(r'ADR-(\d{4})', body))
            adr_nums.update(re.findall(r'decisions/(\d{4})-', body))
            for num in sorted(adr_nums):
                if decisions_dir.exists():
                    matching = list(decisions_dir.glob(f"{num}-*.md"))
                    adr_id = matching[0].stem if matching else f"{num}-unknown"
                else:
                    adr_id = f"{num}-unknown"
                add(stem, "agent", adr_id, "adr", "cites")

    # Cap and log if exceeded (guard against runaway false-positive explosion)
    EDGE_CAP = 200
    if len(edges) > EDGE_CAP:
        print(
            f"[dashboard] WARNING: {len(edges)} edges inferred; capping at {EDGE_CAP}.",
            file=_sys.stderr, flush=True,
        )
        edges = edges[:EDGE_CAP]

    return edges
