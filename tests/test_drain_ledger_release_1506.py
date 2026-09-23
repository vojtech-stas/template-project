"""
tests/test_drain_ledger_release_1506.py

Regression tests for PRD #1501 slice #1506 — DRAIN-LEDGER's release-mode
gating (ADR-0090 D2/D3): a `run_start` record carrying `mode: "release"`
switches concurrency counting from distinct ITEMS (capped at 3, plain mode,
unchanged) to distinct LANES (capped at 15), and requires `run_start` to
carry a `version`. Plain-mode ledgers (test_drain_ledger_1329.py) are
untouched — covered there by C2 zero-diff.

Rule #21 / CRI-004: every ledger here is written under pytest's `tmp_path`,
never into `.claude/logs/drain/`.

Covers PRD #1501 §2 criterion 30 (DRAIN-LEDGER release-mode conditions):
  drain_ledger_release_cap: 16 distinct lanes concurrently open -> FAIL;
    15 distinct lanes (spread across 40 items) concurrently open -> PASS.
  drain_ledger_release_version: a release-mode run_start missing `version`
    -> FAIL naming it.
"""
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
_DASHBOARD_DIR = str(REPO_ROOT / "dashboard")
if _DASHBOARD_DIR not in sys.path:
    sys.path.insert(0, _DASHBOARD_DIR)

import health  # noqa: E402  (path bootstrap must precede the import)


def _write_ledger(directory: Path, name: str, records: list) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return path


def _release_run_start(version="v1.0"):
    rec = {
        "kind": "run_start", "ts": "2026-09-23T10:00:00Z", "run_id": "drain-release-1",
        "mode": "release",
        "counts": {"prd": 0, "slice": 0, "backlog": 0, "captured": 0}, "open_prs": 0,
    }
    if version is not None:
        rec["version"] = version
    return rec


def _lane_run(num_items: int, num_lanes: int) -> list:
    """`num_items` items spread round-robin across `num_lanes` distinct
    string lane names, all triaged then started (none done) -- every lane
    with at least one item stays concurrently open the whole run."""
    records = [_release_run_start()]
    for i in range(num_items):
        item = f"issue:{9000 + i}"
        lane = f"fix/{9000 + (i % num_lanes)}-lane"
        records.append({
            "kind": "triaged", "ts": f"2026-09-23T10:00:{i % 60:02d}Z",
            "item": item, "bucket": "autonomous", "lane": lane,
        })
    for i in range(num_items):
        item = f"issue:{9000 + i}"
        records.append({
            "kind": "item_start", "ts": f"2026-09-23T10:01:{i % 60:02d}Z", "item": item,
        })
    return records


def test_drain_ledger_release_cap_fails_at_16_lanes(tmp_path):
    _write_ledger(tmp_path, "drain-release-1.jsonl", _lane_run(num_items=16, num_lanes=16))

    result = health.check_drain_ledger(ledger_dir=str(tmp_path))

    assert result["result"] == "FAIL", result["detail"]
    assert "16 distinct lanes" in result["detail"]
    assert "15" in result["detail"]


def test_drain_ledger_release_cap_passes_at_15_lanes_40_items(tmp_path):
    _write_ledger(tmp_path, "drain-release-1.jsonl", _lane_run(num_items=40, num_lanes=15))

    result = health.check_drain_ledger(ledger_dir=str(tmp_path))

    assert result["result"] == "PASS", result["detail"]
    assert "15/15 lanes" in result["detail"]


def test_drain_ledger_release_version_missing_fails_named(tmp_path):
    records = [_release_run_start(version=None)]
    _write_ledger(tmp_path, "drain-release-2.jsonl", records)

    result = health.check_drain_ledger(ledger_dir=str(tmp_path))

    assert result["result"] == "FAIL", result["detail"]
    assert "version" in result["detail"]
    assert "run_start" in result["detail"]


def test_drain_ledger_release_version_present_does_not_fail_on_that_ground(tmp_path):
    records = [_release_run_start(version="v1.0"), {"kind": "run_end", "ts": "2026-09-23T10:05:00Z"}]
    _write_ledger(tmp_path, "drain-release-3.jsonl", records)

    result = health.check_drain_ledger(ledger_dir=str(tmp_path))

    assert result["result"] == "PASS", result["detail"]


if __name__ == "__main__":
    import unittest
    unittest.main()
