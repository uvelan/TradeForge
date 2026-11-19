# TF-I08D integrity verification commands

Run PowerShell from the repository root unless stated otherwise. The live checks used only the terminal's quote and candle readers. All SQLite/Parquet benchmark and capture-gate writes use disposable directories. The inventory opens the configured workspace database read-only.

```powershell
git fetch origin
git rev-parse HEAD
git rev-parse origin/feat/TF-I08D-market-data-readiness
git diff --check
$env:PYTHONPATH=(Get-Location).Path
& .\.venv\Scripts\python.exe --version
node --version
npm --version
& .\.venv\Scripts\python.exe -m pytest research_backend/tests/test_arrival_integrity.py -q
& .\.venv\Scripts\python.exe -m pytest -q
& .\.venv\Scripts\python.exe -m pytest research_backend/tests -q -k 'arrival or causal or smt or evidence or legacy'
& .\.venv\Scripts\python.exe -m compileall -q research_backend tradeforge scripts
& .\.venv\Scripts\python.exe scripts/diagnose_mt5_time.py --samples 5
& .\.venv\Scripts\python.exe scripts/diagnose_mt5_time.py --terminal 'C:/Program Files/MetaTrader 5/terminal64.exe' --samples 6
& .\.venv\Scripts\python.exe docs/implementation/evidence/TF-I08D-integrity/verify_live_gate.py 'C:/Program Files/MetaTrader 5/terminal64.exe'
w32tm /stripchart /computer:time.windows.com /samples:3 /dataonly
& .\.venv\Scripts\python.exe docs/implementation/evidence/TF-I08D/inventory.py
& .\.venv\Scripts\python.exe docs/implementation/evidence/TF-I08D/benchmark_capture_extra.py
& .\.venv\Scripts\python.exe docs/implementation/evidence/TF-I08D/benchmark.py
```

From `frontend`, with existing dependencies and the Playwright Chromium installation:

```powershell
npm run test
npm run build
$env:TRADEFORGE_E2E_API_PORT='8191'
$env:TRADEFORGE_E2E_UI_PORT='4191'
npm run e2e -- --retries=0 --reporter=line
npm run e2e -- e2e/drawing-edit.spec.ts -g 'all four rectangle handles|long position rejects crossed' --retries=0 --repeat-each=4 --reporter=line
npm run e2e -- e2e/research.spec.ts -g 'persists a trendline and resumes a frozen replay checkpoint' --retries=0 --repeat-each=20 --reporter=line
npm run e2e -- e2e/research.spec.ts -g 'during delayed analytical and persistence responses' --retries=0 --reporter=line
```

The baseline defect repro used the same test command restricted to the original three cases, before production changes and before the later assertions for frozen rewind and intent. The benchmark lanes collect 30 warmed serial samples. The report distinguishes earlier test-harness failures from the baseline production defects and final runs.
