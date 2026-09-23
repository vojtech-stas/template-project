"""
dashboard/_constants.py — single-source project-wide constants.

Import-nothing by design (ADR-0088 D4): with zero imports of its own, no
sibling module under dashboard/ can ever form an import cycle through this
file. Moved here from the retired HTTP server module (ADR-0088 D1), which
was the sole prior canonical home; discovery.py and health.py previously
held their own private copies (issues #1257, #1465) kept in step only by
comments. CI CHECK 7(a) parses KNOWN_CRITICS from this file and asserts it
is the only definition under dashboard/ and tools/.

Exports:
    KNOWN_CRITICS — the closed set of critic-subagent stems (each has a
    matching .claude/agents/<stem>.md file).
"""

KNOWN_CRITICS = {
    "reviewer",
    "prd-critic",
    "adr-critic",
    "slicer-critic",
    "backlog-critic",
    "codebase-critic",
}
