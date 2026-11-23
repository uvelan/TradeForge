# Test report

- Baseline fixed-exit regression: `pytest -q research_backend/tests/test_offline_first_exit.py`, 24 passed on Linux CPython 3.13.5.
- Initial T03 plus fixed-exit focused suite: 40 passed, including duplicate/orphan/API and existing writer controls.
- Independent ordering and compatibility tests: `pytest -q research_backend/tests/test_offline_management_ordering.py`, 9 passed on Linux CPython 3.12.14 against #28 plus the QA additions. Fixtures generated on exact #26.
- Recovery candidate: `pytest -q tests`, 11 passed including the restore rehearsal.
- Frontend on unchanged source: `npm ci; npm run build; npm test`, TypeScript and Vite passed and 8 Vitest tests passed. Node 24.22.3, npm 11.19.1.
- Scoped added-line secret scan: eight common credential formats, no findings in candidate diff; not a history-wide or comprehensive scanner. PyPI release metadata: 34 pinned releases checked, no advisory records returned. npm registry audit: 156 reported dependencies, zero vulnerabilities reported. Results are checks as of this validation, not a general security guarantee.

The aggregate code-and-test snapshot 173af0580d8c6e176fd632ab9560c59ba5d35bd7 is running the branch-coverage suite. The integration PR records the measured totals and exact hosted validation SHA. Local Linux is not proof of Windows MT5 or browser execution. Candidate CI does not substitute for final-head CI.

## Synthetic timing check

From the repository root run `PYTHONPATH=. python docs/implementation/final-integration-evidence/benchmark.py`. The checked-in [measurements](benchmark-results.json) are five warm samples from Linux CPython 3.13.5 on snapshot 173af05. Median simulation times over 10,000 ordered quotes were 7.69–18.48 ms across the policies, excluding tape parsing, entry verification and persistence. Median verification of a repeated synthetic 10,000-candle legacy import was 58.25 ms including staging. This is not a production throughput guarantee.
