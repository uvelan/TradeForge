# Verification commands

Run from the repository root with the project virtual environment active.

```text
git fetch origin
git fetch origin main
git rev-parse HEAD
python -m compileall -q research_backend scripts
python -m pytest -q -s --tb=short research_backend/tests/test_bounded_crt_smt_successors.py research_backend/tests/test_historical_successor_oracle.py research_backend/tests/test_historical_strategy_runs.py research_backend/tests/test_historical_publication_v2.py research_backend/tests/test_strategy_evaluator.py research_backend/tests/test_negated_current.py
python -m pytest -q --tb=short research_backend/tests/test_historical_strategy_runs.py -k "warmup_candidates or measured_zero"
python scripts/verify_strategy_test_isolation.py
git diff --check
```

Run frontend checks from `frontend`. The Chromium command used a temporary artifact directory and local API/UI ports 8223 and 4223.

```text
npm run build
npm test
npm run e2e -- --retries=0 --reporter=line
npm run e2e -- e2e/terminal.spec.ts --retries=0 --reporter=line
```

The build log omits repeated bundler diagnostics. The focused log omits unrelated negated-CURRENT fixture payloads and retains their test summary.
