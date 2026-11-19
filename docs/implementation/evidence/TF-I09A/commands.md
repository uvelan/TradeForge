# TF-I09A verification commands

Run from the repository root with the locked Python and Node dependencies installed. The Playwright configuration launches isolated SQLite storage and the same application stack. The app uses Windows Python 3.13.14. See the runtime log for Node, npm and Chromium versions.

```powershell
& .\.venv\Scripts\python.exe -m compileall -q research_backend tradeforge scripts
& .\.venv\Scripts\python.exe -m pytest -q
& .\.venv\Scripts\python.exe -m pytest -q research_backend/tests -k 'strategy_definition or causal or strict or smt or evidence or historical or arrival or legacy'
& .\.venv\Scripts\python.exe -m pytest -q research_backend/tests/test_strategy_definitions.py research_backend/tests/test_analysis_evidence.py::test_approved_golden_singles_and_combined_are_unchanged
$env:PYTHONPATH=(Get-Location).Path
& .\.venv\Scripts\python.exe docs/implementation/evidence/TF-I09A/benchmark.py

Set-Location frontend
npm run test
npm run build
$env:TRADEFORGE_E2E_API_PORT='8193'
$env:TRADEFORGE_E2E_UI_PORT='4193'
npm run e2e -- --retries=0 --reporter=line
$env:TRADEFORGE_E2E_API_PORT='8194'
$env:TRADEFORGE_E2E_UI_PORT='4194'
npm run e2e -- e2e/research.spec.ts -g 'persists a trendline and resumes a frozen replay checkpoint' --retries=0 --repeat-each=20 --reporter=line --output=test-results/checkpoint-tf-i09a
$env:TRADEFORGE_E2E_API_PORT='8195'
$env:TRADEFORGE_E2E_UI_PORT='4195'
npm run e2e -- e2e/research.spec.ts -g 'during delayed analytical and persistence responses' --retries=0 --reporter=line --output=test-results/delayed-tf-i09a
```

The [merged TF-I08E baseline](../../TF-I08E-REVIEW.md#test-evidence-and-failed-attempts) reports 482 backend tests, 22 legacy fingerprints and 66 full Chromium scenarios. The final backend suite includes the additive TF-I09A tests. The golden regression compares all 22 approved fingerprints in one pytest case.

## Failure log

| Attempt | Observed failure | Follow-up |
| --- | --- | --- |
| Initial new backend test suite | The preservation check compared a saved plan's response with the pre-normalized request and omitted its three existing nullable fields. | Compared the before/after persisted plan records. [Initial pytest log](strategy-first.txt) |
| First maximum-graph benchmark | The fixture exceeded the 96-node maximum by one. | Reduced the largest subgroup by one. |
| Second benchmark startup | Corrected fixture was out of scope for the benchmark entry point. | Initialized it in the entry point and reran. |
| First isolated delayed-analysis stress | DEMO candles were still loading when the precondition for drawing timed out at five seconds while other suites were running. | Preserved the report in [first stress log](delayed-stress-first.txt). Rechecked after the other suites. |
| First checkpoint repetitions | One of 20 stopped at the same five-second demo-canvas precondition. | Preserved the [19/20 first run](checkpoints-first.txt) and repeated in isolation. |
| Initial `git diff --cached --check` | Eight new files had an extra empty line at EOF. | Normalized their final newlines and reran the diff check before committing. |

The full Chromium test run uses zero retries. The stress scenario intercepts analytical and persistence responses after the chart has loaded. The initial timeout preceded both intercepts.
