"""
dashboard/readme_gen.py — README generator; canonical CLI entrypoint (ADR-0088 D3).

Run directly: python3 dashboard/readme_gen.py

Exports:
    render_pipeline_mermaid(spec) -> str
    generate_readme() -> None
"""

import os
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Repo root — readme_gen.py lives at <repo>/dashboard/readme_gen.py
# ---------------------------------------------------------------------------
_READMEGEN_REPO_ROOT = Path(__file__).resolve().parent.parent

# sys.path injection so discovery is importable when readme_gen.py is run
# from server.py (which has already done the same injection, but this guards
# the standalone import case).
_DASHBOARD_DIR_STR = str(Path(__file__).resolve().parent)
if _DASHBOARD_DIR_STR not in sys.path:
    sys.path.insert(0, _DASHBOARD_DIR_STR)

from discovery import discover_skills, discover_agents, discover_hooks, discover_adrs  # noqa: E402
from pipeline_spec import get_spec as _get_pipeline_spec  # noqa: E402


def _resolve_invoking_repo_root() -> Path:
    """Resolve the repo root from the INVOKING worktree's cwd.

    Used by --generate-readme so the generator writes into the worktree that
    invoked it (not the worktree where server.py physically lives, which may be
    a sibling worktree on a different branch).

    Resolution order:
      1. git rev-parse --show-toplevel (run from cwd — worktree-aware)
      2. $CLAUDE_PROJECT_DIR env var
      3. Path(__file__).resolve().parent.parent (script-file fallback)
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=os.getcwd(),
        )
        if result.returncode == 0:
            root = Path(result.stdout.strip())
            if root.is_dir():
                return root
    except Exception:
        pass

    claude_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
    if claude_dir:
        p = Path(claude_dir)
        if p.is_dir():
            return p

    return Path(__file__).resolve().parent.parent


def _node_id(name: str) -> str:
    """Sanitise a SPEC node id to a valid mermaid node ID (hyphens → underscores)."""
    return name.replace("-", "_")


def _node_decl_spec(name: str, kind: str, label: str) -> str:
    """Return a mermaid node declaration for a SPEC v2 node.

    Shape conventions (matching vis-network topology render):
      human        — [label]      (rectangle)
      orchestrator — ["/name"]    (rectangle, slash-prefix)
      skill        — ["/name"]    (rectangle, slash-prefix)
      agent (critic)   — {name}   (rhombus)
      agent (generator)— [name]   (rectangle)
      artifact     — [(label)]    (cylinder-ish via (()) not available; use [()])
    """
    nid = _node_id(name)
    if kind == "human":
        return f"{nid}[\"{label}\"]"
    elif kind in ("skill", "orchestrator"):
        return f"{nid}[\"/{name}\"]"
    elif kind == "artifact":
        return f"{nid}[({label})]"
    elif kind == "agent":
        # Distinguish critics from generators by name heuristic
        if name.endswith("-critic") or name in ("reviewer",):
            return f"{nid}{{{{{label}}}}}"
        return f"{nid}[{label}]"
    else:
        return f"{nid}[{label}]"


def _edge_line_spec(edge: dict) -> str:
    """Render one SPEC edge to a mermaid edge string.

    Uses from_node/to_node (hyphen ids) converted to underscores for mermaid.
    style='dashed' → -.-  or  -.label.-
    style='solid'  → -->  or  -->|label|
    """
    src = _node_id(edge["from_node"])
    tgt = _node_id(edge["to_node"])
    label = (edge.get("label") or "").strip()
    style = edge.get("style", "solid")

    if style == "dashed":
        if label:
            return f"  {src} -.{label}.- {tgt}"
        else:
            return f"  {src} -.- {tgt}"
    else:  # solid
        if label:
            return f"  {src} -->|{label}| {tgt}"
        else:
            return f"  {src} --> {tgt}"


def render_pipeline_mermaid(spec: dict) -> str:
    """Render the SPEC v2 to a mermaid flowchart TD string.

    Returns a complete ```mermaid ... ``` fenced block for embedding in
    README.md via the {{GENERATED:pipeline-diagram}} placeholder.

    Derives ALL structure from spec (ADR-0053 D2):
    - Node declarations from spec['nodes'] grouped by stage field.
    - Edges from spec['edges'] (from_node/to_node in hyphen id-space).
    - ClassDef assignments from each node's kind field.

    Node IDs are sanitised (hyphens → underscores); labels from SPEC.
    """
    nodes = spec.get("nodes", {})
    edges = spec.get("edges", [])

    # Group nodes by stage
    by_stage: dict = {}
    for name, meta in nodes.items():
        stage = meta.get("stage")
        if stage not in by_stage:
            by_stage[stage] = []
        by_stage[stage].append((name, meta))

    # Collect node_id → mermaid class for classDef assignments
    node_classes: dict[str, str] = {}
    for name, meta in nodes.items():
        kind = meta.get("kind", "agent")
        nid = _node_id(name)
        if name == "reviewer":
            node_classes[nid] = "reviewer_cls"
        elif kind == "human":
            node_classes[nid] = "human"
        elif kind in ("skill", "orchestrator"):
            node_classes[nid] = "skill"
        elif kind == "agent":
            if name.endswith("-critic") or name == "reviewer":
                node_classes[nid] = "critic"
            else:
                node_classes[nid] = "gen"
        elif kind == "artifact":
            node_classes[nid] = "artifact"

    lines: list[str] = []
    lines.append("```mermaid")
    lines.append("flowchart TD")

    # ---- Subgraph S1: Idea capture -----------------------------------------
    lines.append('  subgraph S1["Stage 1: Idea capture"]')
    for name, meta in by_stage.get("S1", []):
        lines.append(f"    {_node_decl_spec(name, meta['kind'], meta['label'])}")
    lines.append("  end")

    # ---- Subgraph S2: PRD authoring + slice decomposition ------------------
    lines.append('  subgraph S2["Stage 2-3: PRD + slice decomposition"]')
    for name, meta in by_stage.get("S2", []):
        lines.append(f"    {_node_decl_spec(name, meta['kind'], meta['label'])}")
    lines.append("  end")

    # ---- Subgraph S3: Implementation ---------------------------------------
    lines.append('  subgraph S3["Stage 4: Implementation"]')
    for name, meta in by_stage.get("S3", []):
        lines.append(f"    {_node_decl_spec(name, meta['kind'], meta['label'])}")
    lines.append("  end")

    # ---- Subgraph S4: Acceptance -------------------------------------------
    lines.append('  subgraph S4["Stage 5: Acceptance"]')
    for name, meta in by_stage.get("S4", []):
        lines.append(f"    {_node_decl_spec(name, meta['kind'], meta['label'])}")
    lines.append("  end")

    # ---- Subgraph SS: Side workflows ----------------------------------------
    lines.append('  subgraph SS["Side workflows"]')
    for name, meta in by_stage.get("SS", []):
        lines.append(f"    {_node_decl_spec(name, meta['kind'], meta['label'])}")
    lines.append("  end")

    # ---- Edges (from SPEC edges list) ----------------------------------------
    for edge in edges:
        lines.append(_edge_line_spec(edge))

    # ---- classDef declarations -----------------------------------------------
    lines.append("  classDef human fill:#3b82f6,color:#fff")
    lines.append("  classDef skill fill:#14b8a6,color:#fff")
    lines.append("  classDef gen fill:#22c55e,color:#fff")
    lines.append("  classDef critic fill:#f97316,color:#fff")
    lines.append("  classDef reviewer_cls fill:#ef4444,color:#fff")
    lines.append("  classDef artifact fill:#9ca3af,color:#fff")

    # ---- class assignments from kind/name ----------------------------------
    by_class: dict[str, list[str]] = {}
    for nid, cls in node_classes.items():
        by_class.setdefault(cls, []).append(nid)
    for cls_name in ("human", "skill", "gen", "critic", "reviewer_cls", "artifact"):
        if cls_name in by_class:
            ids = ",".join(sorted(by_class[cls_name]))
            lines.append(f"  class {ids} {cls_name}")

    lines.append("```")
    return "\n".join(lines)


def _build_component_map() -> str:
    """Build the component-map section from filesystem discovery."""
    skills = discover_skills()
    agents = discover_agents()
    hooks = discover_hooks()
    adrs = discover_adrs()

    lines = []

    # Skills
    lines.append("### Skills\n")
    lines.append("User-invocable commands under `.claude/skills/`:\n")
    if skills:
        for s in skills:
            name = s.get("name") or s["path"].split("/")[-2]
            desc = s.get("description", "")
            path = s["path"]
            if desc:
                lines.append(f"- **[`/{name}`]({path})** — {desc}")
            else:
                lines.append(f"- **[`/{name}`]({path})**")
    else:
        lines.append("_(no skills found)_")
    lines.append("")

    # Agents
    lines.append("### Subagents\n")
    lines.append("Specialist agents under `.claude/agents/`:\n")
    critics = [a for a in agents if a.get("type") == "critic"]
    generators = [a for a in agents if a.get("type") != "critic"]
    if critics:
        lines.append("**Critics** (adversarial gates):\n")
        for a in critics:
            name = a.get("name") or a["stem"]
            desc = a.get("description", "")
            path = a["path"]
            if desc:
                lines.append(f"- **[`{name}`]({path})** — {desc}")
            else:
                lines.append(f"- **[`{name}`]({path})**")
        lines.append("")
    if generators:
        lines.append("**Generators** (output-producing agents):\n")
        for a in generators:
            name = a.get("name") or a["stem"]
            desc = a.get("description", "")
            path = a["path"]
            if desc:
                lines.append(f"- **[`{name}`]({path})** — {desc}")
            else:
                lines.append(f"- **[`{name}`]({path})**")
        lines.append("")

    # Hooks
    lines.append("### Hooks\n")
    lines.append(
        "Claude Code session hooks configured in `.claude/settings.json`"
        " (scripts in `.claude/hooks/`):\n"
    )
    if hooks:
        seen_hooks = set()
        for h in hooks:
            key = (h.get("clean_name", h["name"]), h["event"], h.get("matcher", ""))
            if key in seen_hooks:
                continue
            seen_hooks.add(key)
            clean_name = h.get("clean_name", h["name"])
            event = h["event"]
            matcher = h.get("matcher", "")
            desc = h.get("description", "")
            path = h.get("path", ".claude/settings.json")
            when = f"{event} · {matcher}" if matcher else event
            if desc:
                lines.append(f"- **[`{clean_name}`]({path})** (`{when}`) — {desc}")
            else:
                lines.append(f"- **[`{clean_name}`]({path})** (`{when}`)")
    else:
        lines.append("_(no hooks configured)_")
    lines.append("")

    # ADRs (just count + link)
    lines.append("### Architecture Decision Records\n")
    lines.append(
        f"[`decisions/`](decisions/) holds {len(adrs)} ADR(s)."
        " See [`decisions/README.md`](decisions/README.md) for the full index."
    )
    lines.append("")

    return "\n".join(lines)


def _build_counts() -> str:
    """Build the counts summary line."""
    skills = discover_skills()
    agents = discover_agents()
    hooks = discover_hooks()
    adrs = discover_adrs()

    critics = [a for a in agents if a.get("type") == "critic"]
    generators = [a for a in agents if a.get("type") != "critic"]

    # Deduplicate hooks by (name, event)
    seen = set()
    unique_hooks = []
    for h in hooks:
        key = (h["name"], h["event"])
        if key not in seen:
            seen.add(key)
            unique_hooks.append(h)

    lines = [
        f"> **Auto-generated component counts** (as of last generator run):"
        f" {len(skills)} skill(s),"
        f" {len(critics)} critic(s) + {len(generators)} generator(s),"
        f" {len(unique_hooks)} hook(s),"
        f" {len(adrs)} ADR(s)."
    ]
    return "\n".join(lines)


def _build_critic_list() -> str:
    """Build a markdown bullet list of adversarial critics from the filesystem.

    Discovers critics via discover_agents() (type == 'critic'), sorted by stem.
    Each bullet: **[`name`](path)** — first sentence of frontmatter description.
    Returns a plain bullet list (no trailing newline) suitable for
    {{GENERATED:critic-list}}.
    """
    agents = discover_agents()
    critics = sorted(
        [a for a in agents if a.get("type") == "critic"],
        key=lambda a: a.get("stem", a.get("name", "")),
    )
    lines = []
    for c in critics:
        name = c.get("name") or c["stem"]
        path = c["path"]
        desc = c.get("description", "")
        # Use first sentence only (up to first ". " or end of string)
        first_sentence = desc.split(". ")[0].rstrip(".")
        if first_sentence:
            lines.append(f"- **[`{name}`]({path})** — {first_sentence}.")
        else:
            lines.append(f"- **[`{name}`]({path})**")
    return "\n".join(lines)


def generate_readme() -> None:
    """Read README.template.md, substitute placeholders, write README.md.

    Placeholders:
      {{GENERATED:pipeline-diagram}}  — fixed Mermaid diagram block
      {{GENERATED:component-map}}     — filesystem-derived skills/agents/hooks/ADR map
      {{GENERATED:counts}}            — one-line component count summary
      {{GENERATED:critic-list}}       — filesystem-derived adversarial-critic bullet list

    Idempotent: running twice produces the same README.md.
    No LLM calls — pure stdlib + pathlib.

    Uses _resolve_invoking_repo_root() so that when invoked from a worktree,
    the README is written into THAT worktree's root rather than the script's
    physical location (which may be a sibling worktree on a different branch).
    """
    gen_root = _resolve_invoking_repo_root()
    template_path = gen_root / "README.template.md"
    readme_path = gen_root / "README.md"

    if not template_path.exists():
        print(
            f"ERROR: template not found at {template_path}",
            file=sys.stderr, flush=True,
        )
        sys.exit(1)

    template = template_path.read_text(encoding="utf-8")

    substitutions = {
        "{{GENERATED:pipeline-diagram}}": render_pipeline_mermaid(_get_pipeline_spec()),
        "{{GENERATED:component-map}}": _build_component_map().rstrip("\n"),
        "{{GENERATED:counts}}": _build_counts(),
        "{{GENERATED:critic-list}}": _build_critic_list(),
    }

    result = template
    for placeholder, value in substitutions.items():
        result = result.replace(placeholder, value)

    header = (
        "<!-- AUTO-GENERATED from README.template.md"
        " — edit the template, run the generator. -->\n"
    )
    final = header + result

    readme_path.write_text(final, encoding="utf-8")
    print(f"README.md written ({len(final)} bytes)", flush=True)


if __name__ == "__main__":
    generate_readme()
