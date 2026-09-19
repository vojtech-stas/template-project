# tests/ — regression suite

This directory holds the project-claude regression test suite, seeded per ADR-0067 D1.

## Runner choice

**stdlib `unittest`** is the primary runner — no external dependencies required.
**pytest** is optional; it is preferred when installed but is not a requirement
(per PRD #813 §4: "no new external dependencies — pytest only if already available").

Run the suite:
```bash
# From the repo root (stdlib — works everywhere):
python -m unittest discover -s tests -p "test_*.py"

# Optional (when pytest is installed):
pytest tests/ -v
```

## CI integration

OpenAI slice-1 contracts: `python -m unittest tests.test_openai_workflow
tests.test_openai_skills -v` (one command). The tests cover deterministic router
parity, resolver and preflight refusals, typed-branch consumers, independent
isolation, artifact/host correspondence and zero downstream verification/closure
calls on invalid proof. Disposable fixtures are not production evidence. Compare
the full base/candidate suites with identical prerequisites and isolated logs;
existing failures are not permission to add failures, errors or skips.

`tools/ci-checks.sh` runs the suite automatically as CHECK 12 when `tests/`
exists. A failure in any test fails the CI check. The collected count is
reported in the pass line.

## Founding test

`test_events_interleave.py` — regression for the events.py interleave defect
(issue #730, ADR-0067 D1). This bug shipped twice; the test ensures it cannot
ship a third time.

**What the bug was:** `serve_runs(?session=<id>)` broke early when it
encountered a line from a different session, truncating the target session's
events whenever two concurrent sessions' lines interleaved in the JSONL file.

**Why it shipped twice:** the second author rationalised the break with a
docstring comment saying "if we see a different session, the target session
is done" — a false assumption for concurrent sessions. The forensic post-mortem
lives in `qa-proof/forensics/event-pipeline-autopsy.md`.

## Quarantine

Flaky or known-broken tests may be quarantined in `tests/quarantine.txt` per
ADR-0067 D4. Every quarantine entry must carry a `captured`-labeled issue
reference. Entries older than 30 days are SLA breaches.
