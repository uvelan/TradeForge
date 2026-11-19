# Closure verification commands

To reproduce the baseline, check out `b5184c2e3ca9603d622c582e9ea4fdf6134d2f42` with the current reproduction script copied into it. The same script recreates the corrected outcomes on the closure commit.

```powershell
git fetch origin
git rev-parse origin/feat/TF-I09A-strategy-definition
$env:PYTHONPATH=(Get-Location).Path+';'+(Resolve-Path research_backend/tests).Path
& .\.venv\Scripts\python.exe docs/implementation/evidence/TF-I09A-closure/reproduce.py
& .\.venv\Scripts\python.exe docs/implementation/evidence/TF-I09A-closure/benchmark.py
& .\.venv\Scripts\python.exe -m compileall -q research_backend tradeforge scripts
& .\.venv\Scripts\python.exe -m pytest -q
& .\.venv\Scripts\python.exe -m pytest -q research_backend/tests/test_strategy_binding_closure.py research_backend/tests/test_strategy_integrity.py research_backend/tests/test_strategy_definitions.py research_backend/tests/test_strict_causal_timing.py research_backend/tests/test_causal_evidence_integrity.py research_backend/tests/test_smt_causal.py research_backend/tests/test_analysis_evidence.py
Set-Location frontend
npm run test
npm run build
$env:TRADEFORGE_E2E_API_PORT='8197'
$env:TRADEFORGE_E2E_UI_PORT='4197'
npm run e2e -- --retries=0 --reporter=line
npm run e2e -- e2e/research.spec.ts -g 'persists a trendline and resumes a frozen replay checkpoint' --retries=0 --repeat-each=20 --reporter=line
npm run e2e -- e2e/research.spec.ts -g 'during delayed analytical and persistence responses' --retries=0 --reporter=line
```

The first focused browser run failed one new test because a partial accessible-label match selected both Observation and Observation selection. The corrected test selects the control containing the canonical SMT_LEADER option and retains the original assertions. [Initial browser log](builder-first.txt). All 22 legacy golden numerical fingerprints are verified in one backend test.
