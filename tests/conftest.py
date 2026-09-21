"""tests/conftest.py — quarantine enforcement (ADR-0067 D4 "run-and-log, never gate").

Parses `tests/quarantine.txt`'s active entries and applies
`pytest.mark.xfail(strict=False)` to any collected test whose node id matches
an active entry. The test still runs and its outcome remains visible in
captured pytest output, but an xfail (expected-failure, non-strict) does not
count as a failure toward the overall pytest run's exit code — so a
quarantined test no longer gates CHECK 12 (tools/ci-checks.sh).

Format read is exactly the one `tests/quarantine.txt`'s own header documents
— no new quarantine syntax is introduced (PRD #1462 §3 non-goal):

    <test_node_id>  # captured #<issue-number>  [quarantined: YYYY-MM-DD]

Blank lines and full-line `#`-comments are ignored. The node id is the first
whitespace-delimited token on each active line.
"""

from pathlib import Path

import pytest

_QUARANTINE_FILE = Path(__file__).parent / "quarantine.txt"


def _active_quarantine_node_ids():
    """Return the set of test node ids named by active quarantine.txt entries."""
    if not _QUARANTINE_FILE.exists():
        return set()
    node_ids = set()
    for line in _QUARANTINE_FILE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        node_ids.add(stripped.split()[0])
    return node_ids


def pytest_collection_modifyitems(config, items):
    """Mark collected items named by tests/quarantine.txt as xfail(strict=False)."""
    quarantined = _active_quarantine_node_ids()
    if not quarantined:
        return
    for item in items:
        if item.nodeid in quarantined:
            item.add_marker(
                pytest.mark.xfail(
                    reason="quarantined via tests/quarantine.txt (ADR-0067 D4: run-and-log, never gate)",
                    strict=False,
                )
            )
