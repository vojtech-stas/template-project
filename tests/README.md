# tests/ — regression suite

This directory holds the project-claude regression test suite, seeded per ADR-0067 D1.

## Runner choice

**pytest** is the authoritative runner in CI: `.github/workflows/ci.yml`
installs it ahead of CHECK 12, so CHECK 12 always takes CHECK 12's
pytest branch, not the stdlib-`unittest` fallback (PRD #1462 §2 #1/#2,
superseding old PRD #813 §4's "unittest is primary"). `tests/conftest.py`'s
quarantine-enforcement hook only runs under pytest, so pytest is also
required for quarantine entries (`tests/quarantine.txt`) to be honored.
stdlib `unittest` discovery remains a no-dependency fallback for local runs
where pytest isn't installed — CHECK 12 still uses it in that case, but
without quarantine enforcement.

Run the suite:
```bash
# Preferred (matches CI):
pytest tests/ -v

# Fallback (no external dependencies, no quarantine enforcement):
python -m unittest discover -s tests -p "test_*.py"
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
exists. Under pytest, a quarantined test (an active entry in
`tests/quarantine.txt`) still runs and its outcome stays visible in the
captured output, but its failure does not fail CHECK 12 — "run-and-log,
never gate" per ADR-0067 D4. Any other test failure still fails the check.
The collected count is reported in the pass line. `tools/ci-checks.sh` also
runs CHECK 28 (QUARANTINE-SLA), which fails the build if any active
quarantine entry has passed its 30-day fix-or-delete SLA.

## Quarantine

Flaky or known-broken tests may be quarantined in `tests/quarantine.txt` per
ADR-0067 D4. Every quarantine entry must carry a `captured`-labeled issue
reference. Entries older than 30 days are SLA breaches.
