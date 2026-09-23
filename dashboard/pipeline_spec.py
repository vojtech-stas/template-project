"""
dashboard/pipeline_spec.py — full SPEC v2 ontology for the trail-of-record system.

Defines the complete node catalog and all workflow edges with stable E-* ids.
Each node carries:
  - kind  : 'human' | 'orchestrator' | 'skill' | 'agent' | 'artifact'
  - label : display string
  - stage : 'S1' | 'S2' | 'S3' | 'S4' | 'SS' | None
  - path  : repo-relative path (skills/agents); None for artifacts/human/orchestrator

Each edge carries:
  - id        : stable E-* identifier (never changes after assignment)
  - from_node : source node id (hyphen id-space)
  - to_node   : target node id (hyphen id-space)
  - evidence  : 'github' | 'runtime' | 'unmeasurable'
                github      = authoritative; evaluated in comparison engine
                runtime     = live enrichment only; never compared against trail
                unmeasurable = in-conversation; rendered as context only
  - required  : 'always' | 'conditional'
                always       = every run must traverse this edge
                conditional  = only triggered under certain conditions
                               (BLOCK loop, trivial-lane skip, side-workflow, etc.)
  - predicate : human-readable name for what confirms the edge
  - label     : short display label for the edge (optional)
  - style     : 'solid' | 'dashed' (default 'solid')
  - description : longer prose explanation

ADR-0053 D1/D2: artifact trail is the system of record; one evidence-annotated
SPEC is the single declared topology.  This module is the ONLY hand-edited
declaration — it replaces both PIPELINE children[] and PIPELINE __edges__.

Slice 2 of PRD #651: full SPEC v2 ontology.
Id-space: one canonical hyphen form matching skill/agent directory names.
"""

# ---------------------------------------------------------------------------
# Nodes — complete catalog (hyphen id-space)
# ---------------------------------------------------------------------------
NODES = {
    # --- human ---------------------------------------------------------------
    "user": {
        "kind": "human",
        "label": "User",
        "stage": "S1",
        "path": None,
    },

    # --- orchestrator --------------------------------------------------------
    "orchestrator": {
        "kind": "orchestrator",
        "label": "Orchestrator",
        "stage": None,
        "path": None,
    },

    # --- skills (Stage 1: Idea capture) -------------------------------------
    "grill-me": {
        "kind": "skill",
        "label": "/grill-me",
        "stage": "S1",
        "path": ".claude/skills/grill-me/SKILL.md",
    },
    "ship": {
        "kind": "skill",
        "label": "/ship",
        "stage": "S1",
        "path": ".claude/skills/ship/SKILL.md",
    },

    # --- skills (Stage 2–3: PRD authoring + slice decomposition) -----------
    "to-prd": {
        "kind": "skill",
        "label": "/to-prd",
        "stage": "S2",
        "path": ".claude/skills/to-prd/SKILL.md",
    },
    "to-issues": {
        "kind": "skill",
        "label": "/to-issues",
        "stage": "S2",
        "path": ".claude/skills/to-issues/SKILL.md",
    },

    # --- agents (Stage 2: PRD critics) -------------------------------------
    "prd-critic": {
        "kind": "agent",
        "label": "prd-critic",
        "stage": "S2",
        "path": ".claude/agents/prd-critic.md",
    },
    "adr-critic": {
        "kind": "agent",
        "label": "adr-critic",
        "stage": "S2",
        "path": ".claude/agents/adr-critic.md",
    },
    "slicer": {
        "kind": "agent",
        "label": "slicer",
        "stage": "S2",
        "path": ".claude/agents/slicer.md",
    },
    "slicer-critic": {
        "kind": "agent",
        "label": "slicer-critic",
        "stage": "S2",
        "path": ".claude/agents/slicer-critic.md",
    },

    # --- agents (Stage 3: Implementation) ----------------------------------
    "implementer": {
        "kind": "agent",
        "label": "implementer",
        "stage": "S3",
        "path": ".claude/agents/implementer.md",
    },
    "reviewer": {
        "kind": "agent",
        "label": "reviewer",
        "stage": "S3",
        "path": ".claude/agents/reviewer.md",
    },

    # --- skills (Stage 4: Acceptance) ----------------------------------------
    "qa-plan": {
        "kind": "skill",
        "label": "/qa-plan",
        "stage": "S4",
        "path": ".claude/skills/qa-plan/SKILL.md",
    },

    # --- agents (Stage 4) ---------------------------------------------------
    "qa-tester": {
        "kind": "agent",
        "label": "qa-tester",
        "stage": "S4",
        "path": ".claude/agents/qa-tester.md",
    },

    # --- skills (Side workflows) -------------------------------------------
    "promote-to-backlog": {
        "kind": "skill",
        "label": "/promote-to-backlog",
        "stage": "SS",
        "path": ".claude/skills/promote-to-backlog/SKILL.md",
    },

    # --- agents (Side workflows) -------------------------------------------
    "backlog-critic": {
        "kind": "agent",
        "label": "backlog-critic",
        "stage": "SS",
        "path": ".claude/agents/backlog-critic.md",
    },
    "codebase-critic": {
        "kind": "agent",
        "label": "codebase-critic",
        "stage": "SS",
        "path": ".claude/agents/codebase-critic.md",
    },

    # --- artifact pseudo-nodes (no file path) --------------------------------
    "prd-issue": {
        "kind": "artifact",
        "label": "PRD issue",
        "stage": "S2",
        "path": None,
    },
    "slice-issue": {
        "kind": "artifact",
        "label": "Slice issue",
        "stage": "S2",
        "path": None,
    },
    "pr": {
        "kind": "artifact",
        "label": "Pull Request",
        "stage": "S3",
        "path": None,
    },
    "merge": {
        "kind": "artifact",
        "label": "Merge",
        "stage": "S3",
        "path": None,
    },
    "closed-prd": {
        "kind": "artifact",
        "label": "Closed PRD",
        "stage": "S3",
        "path": None,
    },
    "needs-human": {
        "kind": "artifact",
        "label": "needs-human",
        "stage": "S3",
        "path": None,
    },
    "captured-issue": {
        "kind": "artifact",
        "label": "captured issue",
        "stage": "SS",
        "path": None,
    },
    "backlog-issue": {
        "kind": "artifact",
        "label": "backlog issue",
        "stage": "SS",
        "path": None,
    },
    "verify-verdict": {
        "kind": "artifact",
        "label": "verify verdict",
        "stage": "S4",
        "path": None,
    },
}


# ---------------------------------------------------------------------------
# Edges — complete declared workflow with stable E-* ids
#
# Evidence tiers (per ADR-0053 D2):
#   github      = authoritative; evaluated in the comparison engine
#                 (sub-issues, closingIssuesReferences, PR verdict comments,
#                  label events, merge events — all GitHub artifact trail)
#   runtime     = live enrichment only; confirmed by skill_invoke /
#                 agent_complete hook events; never compared against trail
#   unmeasurable = in-conversation handoffs or advisory audits;
#                  rendered as declared context, never as drift
#
# Required semantics:
#   always      = every run must traverse this edge
#   conditional = only triggered under certain conditions
#                 (BLOCK loops, trivial lane, side-workflows, optional paths)
# ---------------------------------------------------------------------------
EDGES = [
    # =========================================================================
    # Stage 1: Idea capture
    # =========================================================================

    # User invokes /ship directly (runtime: skill_invoke event)
    {
        "id": "E-USER-SHIP",
        "from_node": "user",
        "to_node": "ship",
        "evidence": "runtime",
        "required": "conditional",
        "predicate": "user_invokes_ship",
        "label": "/ship",
        "style": "solid",
        "description": "User invokes /ship directly to run the PRD→merge pipeline.",
    },
    {
        "id": "E-USER-GRILLME",
        "from_node": "user",
        "to_node": "grill-me",
        "evidence": "unmeasurable",
        "required": "conditional",
        "predicate": "user_requests_grill",
        "label": "/grill-me",
        "style": "dashed",
        "description": "User requests /grill-me to explore design options (in-conversation).",
    },

    # /grill-me → /ship: settled design handed off (in-conversation)
    {
        "id": "E-GRILLME-SHIP",
        "from_node": "grill-me",
        "to_node": "ship",
        "evidence": "unmeasurable",
        "required": "conditional",
        "predicate": "grill_settled_to_ship",
        "label": "settled design",
        "style": "dashed",
        "description": "Settled /grill-me design handed to /ship (in-conversation handoff).",
    },

    # =========================================================================
    # Stage 2–3: PRD authoring + slice decomposition
    # =========================================================================

    # /ship → /to-prd (runtime dispatch)
    {
        "id": "E-SHIP-TOPRD",
        "from_node": "ship",
        "to_node": "to-prd",
        "evidence": "runtime",
        "required": "conditional",
        "predicate": "ship_dispatches_to_prd",
        "label": "",
        "style": "solid",
        "description": "/ship invokes /to-prd to author and post the PRD issue. Conditional: only runtime-observed when /ship runs in a fresh session with live hooks; resumed-session hook blindspot produces no skill_invoke events (issue #719).",
    },

    # /to-prd dispatches critics (runtime)
    {
        "id": "E-TOPRD-PRDCRITIC",
        "from_node": "to-prd",
        "to_node": "prd-critic",
        "evidence": "runtime",
        "required": "always",
        "predicate": "to_prd_dispatches_prd_critic",
        "label": "",
        "style": "solid",
        "description": "/to-prd dispatches prd-critic for joint-APPROVE gate.",
    },
    {
        "id": "E-TOPRD-ADRCRITIC",
        "from_node": "to-prd",
        "to_node": "adr-critic",
        "evidence": "runtime",
        "required": "conditional",
        "predicate": "to_prd_dispatches_adr_critic",
        "label": "if ADR",
        "style": "dashed",
        "description": "/to-prd dispatches adr-critic when PRD includes a macro-ADR.",
    },

    # prd-critic/adr-critic APPROVE → prd-issue (github: issue creation event)
    {
        "id": "E-PRDCRITIC-APPROVE",
        "from_node": "prd-critic",
        "to_node": "prd-issue",
        "evidence": "github",
        "required": "always",
        "predicate": "prd_critic_approved_prd_posted",
        "label": "joint APPROVE",
        "style": "solid",
        "description": "prd-critic APPROVE (joint gate) triggers PRD issue creation on GitHub.",
    },
    {
        "id": "E-ADRCRITIC-APPROVE",
        "from_node": "adr-critic",
        "to_node": "prd-issue",
        "evidence": "github",
        "required": "conditional",
        "predicate": "adr_critic_approved_prd_posted",
        "label": "joint APPROVE",
        "style": "solid",
        "description": "adr-critic APPROVE (joint gate) co-triggers PRD issue creation.",
    },

    # prd-critic BLOCK → back to /to-prd (github: BLOCK verdict comment)
    {
        "id": "E-PRDCRITIC-BLOCK",
        "from_node": "prd-critic",
        "to_node": "to-prd",
        "evidence": "github",
        "required": "conditional",
        "predicate": "prd_critic_block_verdict",
        "label": "BLOCK",
        "style": "dashed",
        "description": "prd-critic BLOCK — PRD revised and resubmitted.",
    },

    # prd-issue → /to-issues (runtime: orchestrator progression)
    {
        "id": "E-PRDISSUE-TOISSUES",
        "from_node": "prd-issue",
        "to_node": "to-issues",
        "evidence": "runtime",
        "required": "conditional",
        "predicate": "prd_posted_triggers_to_issues",
        "label": "",
        "style": "solid",
        "description": "PRD issue posted; orchestrator proceeds to /to-issues for slicing. Conditional: only runtime-observed when /to-issues is invoked in a fresh session with live hooks (issue #719).",
    },

    # /to-issues dispatches slicer (runtime)
    {
        "id": "E-TOISSUES-SLICER",
        "from_node": "to-issues",
        "to_node": "slicer",
        "evidence": "runtime",
        "required": "conditional",
        "predicate": "to_issues_dispatches_slicer",
        "label": "",
        "style": "solid",
        "description": "/to-issues dispatches slicer to decompose PRD into slices. Conditional: only runtime-observed when /to-issues runs in a fresh session with live hooks (issue #719).",
    },

    # slicer → slicer-critic (runtime)
    {
        "id": "E-SLICER-SLICERCRITIC",
        "from_node": "slicer",
        "to_node": "slicer-critic",
        "evidence": "runtime",
        "required": "conditional",
        "predicate": "slicer_dispatches_slicer_critic",
        "label": "decomposition",
        "style": "solid",
        "description": "slicer submits decomposition to slicer-critic for INVEST gate. Conditional: only runtime-observed when the slicer dispatch occurs in a fresh session with live hooks (issue #719).",
    },

    # prd-issue → slice-issue (github: PRD has ≥1 native sub-issue)
    # Stable spine id preserved from slice-1; evaluated by comparison engine.
    {
        "id": "E-PRD-SLICE",
        "from_node": "prd-issue",
        "to_node": "slice-issue",
        "evidence": "github",
        "required": "always",
        "predicate": "prd_has_sub_issue",
        "label": "sub-issue",
        "style": "solid",
        "description": "PRD issue has ≥1 native sub-issue (slice) — github artifact trail.",
    },

    # slicer-critic BLOCK → back to slicer (runtime)
    {
        "id": "E-SLICERCRITIC-BLOCK",
        "from_node": "slicer-critic",
        "to_node": "slicer",
        "evidence": "runtime",
        "required": "conditional",
        "predicate": "slicer_critic_block_verdict",
        "label": "BLOCK",
        "style": "dashed",
        "description": "slicer-critic BLOCK — slicer revises decomposition.",
    },

    # =========================================================================
    # Stage 3: Implementation
    # =========================================================================

    # slice-issue → implementer (github: slice assigned/claimed)
    {
        "id": "E-SLICEISSUE-IMPL",
        "from_node": "slice-issue",
        "to_node": "implementer",
        "evidence": "github",
        "required": "always",
        "predicate": "slice_assigned_to_implementer",
        "label": "",
        "style": "solid",
        "description": "Slice issue assigned to implementer (I2 claim protocol).",
    },

    # slice-issue → pr (github: slice closed by PR via closingIssuesReferences)
    # Stable spine id preserved from slice-1; evaluated by comparison engine.
    {
        "id": "E-SLICE-PR",
        "from_node": "slice-issue",
        "to_node": "pr",
        "evidence": "github",
        "required": "always",
        "predicate": "slice_closed_by_pr",
        "label": "",
        "style": "solid",
        "description": "Slice closed by a PR via closingIssuesReferences — github artifact trail.",
    },

    # pr → reviewer (github: PR has reviewer verdict comment)
    # Stable spine id preserved from slice-1; evaluated by comparison engine.
    {
        "id": "E-PR-REVIEW",
        "from_node": "pr",
        "to_node": "reviewer",
        "evidence": "github",
        "required": "always",
        "predicate": "pr_has_verdict_comment",
        "label": "",
        "style": "solid",
        "description": "PR has ≥1 reviewer verdict comment (VERDICT: APPROVE|BLOCK) — github artifact trail.",
    },

    # reviewer APPROVE → merge (github: PR merged after APPROVE verdict)
    # Stable spine id preserved from slice-1; evaluated by comparison engine.
    {
        "id": "E-REVIEW-MERGE",
        "from_node": "reviewer",
        "to_node": "merge",
        "evidence": "github",
        "required": "always",
        "predicate": "pr_merged_after_approve",
        "label": "APPROVE",
        "style": "solid",
        "description": "PR merged; last reviewer verdict before merge was APPROVE — github artifact trail.",
    },

    # merge → closed-prd (github: all slices merged and PRD issue closed)
    # Stable spine id preserved from slice-1; evaluated by comparison engine.
    {
        "id": "E-MERGE-CLOSE-PRD",
        "from_node": "merge",
        "to_node": "closed-prd",
        "evidence": "github",
        "required": "always",
        "predicate": "all_slices_merged_prd_closed",
        "label": "",
        "style": "solid",
        "description": "All slices merged; PRD issue closed — github artifact trail.",
    },

    # reviewer BLOCK → implementer (github: BLOCK verdict + re-push)
    # Stable spine id preserved from slice-1; evaluated by comparison engine.
    {
        "id": "E-REVIEW-BLOCK",
        "from_node": "reviewer",
        "to_node": "implementer",
        "evidence": "github",
        "required": "conditional",
        "predicate": "pr_has_block_verdict",
        "label": "BLOCK",
        "style": "dashed",
        "description": "Reviewer posted BLOCK — implementer revised and re-pushed.",
    },

    # reviewer round-3 BLOCK → needs-human (github: needs-human label added)
    {
        "id": "E-REVIEWER-NEEDSHUMAN",
        "from_node": "reviewer",
        "to_node": "needs-human",
        "evidence": "github",
        "required": "conditional",
        "predicate": "pr_has_needs_human_label",
        "label": "round-3 BLOCK",
        "style": "dashed",
        "description": "Round-3 BLOCK: reviewer escalates by adding needs-human label.",
    },

    # trivial lane: PR → merge directly (no reviewer; github: trivial label + merge)
    # Stable spine id preserved from slice-1; evaluated by comparison engine.
    {
        "id": "E-TRIVIAL-LANE",
        "from_node": "pr",
        "to_node": "merge",
        "evidence": "github",
        "required": "conditional",
        "predicate": "pr_has_trivial_label",
        "label": "trivial",
        "style": "dashed",
        "description": "Trivial-lane PR (≤10 LoC, trivial label) merged without full review.",
    },

    # =========================================================================
    # Stage 4: Acceptance
    # =========================================================================

    # merge → /qa-plan (runtime: /ship triggers qa-plan)
    {
        "id": "E-MERGE-QAPLAN",
        "from_node": "merge",
        "to_node": "qa-plan",
        "evidence": "runtime",
        "required": "always",
        "predicate": "merge_triggers_qa_plan",
        "label": "",
        "style": "solid",
        "description": "/ship step 6 dispatches /qa-plan after last slice merges.",
    },

    # /qa-plan → qa-tester (runtime dispatch)
    {
        "id": "E-QAPLAN-QATESTER",
        "from_node": "qa-plan",
        "to_node": "qa-tester",
        "evidence": "runtime",
        "required": "always",
        "predicate": "qa_plan_dispatches_qa_tester",
        "label": "",
        "style": "solid",
        "description": "/qa-plan dispatches qa-tester for production verification.",
    },

    # qa-tester → verify-verdict (runtime: PRODUCTION_VERIFY trailer)
    {
        "id": "E-QATESTER-VERDICT",
        "from_node": "qa-tester",
        "to_node": "verify-verdict",
        "evidence": "runtime",
        "required": "always",
        "predicate": "qa_tester_emits_verdict",
        "label": "PASS/FAIL",
        "style": "solid",
        "description": "qa-tester emits PRODUCTION_VERIFY: PASS|FAIL verdict.",
    },

    # =========================================================================
    # Side workflows: codebase-critic (per-PRD + whole-repo background)
    # =========================================================================

    # merge → codebase-critic per-PRD gate (runtime: /ship dispatches at last slice)
    {
        "id": "E-MERGE-CODEBASECRITIC",
        "from_node": "merge",
        "to_node": "codebase-critic",
        "evidence": "runtime",
        "required": "conditional",
        "predicate": "ship_dispatches_codebase_critic_per_prd",
        "label": "per-PRD gate",
        "style": "dashed",
        "description": "codebase-critic per-PRD mode fires at last slice before reviewer (ADR-0046 D2).",
    },

    # codebase-critic → reviewer (runtime: per-PRD quality gate output)
    {
        "id": "E-CODEBASECRITIC-REVIEWER",
        "from_node": "codebase-critic",
        "to_node": "reviewer",
        "evidence": "runtime",
        "required": "conditional",
        "predicate": "codebase_critic_feeds_reviewer",
        "label": "per-PRD",
        "style": "dashed",
        "description": "codebase-critic per-PRD verdict feeds into the reviewer pass.",
    },

    # ship → codebase-critic whole-repo background (advisory, runtime)
    {
        "id": "E-SHIP-CODEBASECRITIC-BG",
        "from_node": "ship",
        "to_node": "codebase-critic",
        "evidence": "runtime",
        "required": "conditional",
        "predicate": "ship_dispatches_codebase_critic_bg",
        "label": "whole-repo bg",
        "style": "dashed",
        "description": "codebase-critic whole-repo background mode dispatched at /ship start (ADR-0051 D2).",
    },

    # =========================================================================
    # Side workflows: capture + promote-to-backlog
    # =========================================================================

    # orchestrator → captured-issue (github: gh issue create --label captured)
    {
        "id": "E-ORCH-CAPTURED",
        "from_node": "orchestrator",
        "to_node": "captured-issue",
        "evidence": "github",
        "required": "conditional",
        "predicate": "agent_created_captured_issue",
        "label": "capture",
        "style": "dashed",
        "description": "Any agent creates a captured-labeled issue (rule #11).",
    },

    # user → /promote-to-backlog (runtime)
    {
        "id": "E-USER-PTB",
        "from_node": "user",
        "to_node": "promote-to-backlog",
        "evidence": "runtime",
        "required": "conditional",
        "predicate": "user_invokes_promote_to_backlog",
        "label": "",
        "style": "dashed",
        "description": "User invokes /promote-to-backlog to curate the captured tier.",
    },

    # captured-issue → promote-to-backlog (runtime: skill processes captured issues)
    {
        "id": "E-CAPTURED-PTB",
        "from_node": "captured-issue",
        "to_node": "promote-to-backlog",
        "evidence": "runtime",
        "required": "conditional",
        "predicate": "captured_issue_enters_promotion",
        "label": "",
        "style": "solid",
        "description": "captured issue enters /promote-to-backlog pipeline.",
    },

    # promote-to-backlog → backlog-critic (runtime)
    {
        "id": "E-PTB-BACKLOGCRITIC",
        "from_node": "promote-to-backlog",
        "to_node": "backlog-critic",
        "evidence": "runtime",
        "required": "always",
        "predicate": "ptb_dispatches_backlog_critic",
        "label": "",
        "style": "solid",
        "description": "/promote-to-backlog dispatches backlog-critic for APPROVE/BLOCK gate.",
    },

    # backlog-critic APPROVE → backlog-issue (github: label change captured→backlog)
    {
        "id": "E-BACKLOGCRITIC-APPROVE",
        "from_node": "backlog-critic",
        "to_node": "backlog-issue",
        "evidence": "github",
        "required": "conditional",
        "predicate": "backlog_critic_approved_promoted",
        "label": "APPROVE",
        "style": "solid",
        "description": "backlog-critic APPROVE: issue relabeled captured→backlog.",
    },

    # backlog-critic BLOCK → captured-issue stays (github: no label change)
    {
        "id": "E-BACKLOGCRITIC-BLOCK",
        "from_node": "backlog-critic",
        "to_node": "captured-issue",
        "evidence": "github",
        "required": "conditional",
        "predicate": "backlog_critic_block_stays_captured",
        "label": "BLOCK",
        "style": "dashed",
        "description": "backlog-critic BLOCK: issue stays in captured tier.",
    },
]


def get_spec() -> dict:
    """Return the full SPEC v2 as a JSON-serializable dict.

    Shape:
        {
          "version": "v2",
          "nodes": {id: {kind, label, stage, path}, ...},
          "edges": [{id, from_node, to_node, evidence, required, predicate,
                     label, style, description}, ...],
        }

    Consumed by:
      - readme_gen.py's render_pipeline_mermaid() (the README's generated
        mermaid diagram, entrypoint `python3 dashboard/readme_gen.py`)
      - dashboard/comparison.py compare() (github-tier edges)
    """
    return {
        "version": "v2",
        "nodes": NODES,
        "edges": EDGES,
    }
