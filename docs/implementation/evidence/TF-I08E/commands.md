# TF-I08E verification commands

Use the repository root and its configured Python environment. Keep the provider files and complete price-bearing responses in the ignored `storage` directory. The local verification test server uses isolated storage.

```powershell
$env:PYTHONPATH = (Get-Location).Path
& .\.venv\Scripts\python.exe docs/implementation/evidence/TF-I08D/inventory.py
w32tm /query /status
w32tm /query /source
w32tm /stripchart /computer:time.windows.com /samples:5 /dataonly
& .\.venv\Scripts\python.exe -m pytest -q
& .\.venv\Scripts\python.exe -m pytest -q research_backend/tests -k 'historical_csv or historical_import or arrival or strict or causal or smt or evidence or legacy'
& .\.venv\Scripts\python.exe -m compileall -q research_backend tradeforge scripts
& .\.venv\Scripts\python.exe -m scripts.verify_historical_acceptance 2026-11-13 --sources storage/tf-i08e-source --public docs/implementation/evidence/TF-I08E --private storage/tf-i08e-acceptance
& .\.venv\Scripts\python.exe -m scripts.benchmark_historical_acceptance --sources storage/tf-i08e-source --evidence docs/implementation/evidence/TF-I08E
```

Test commands from `frontend`:

```powershell
npm run test
npm run build
$env:TRADEFORGE_E2E_API_PORT = '8192'
$env:TRADEFORGE_E2E_UI_PORT = '4192'
npm run e2e -- --retries=0 --reporter=line
npm run e2e -- e2e/research.spec.ts -g 'persists a trendline and resumes a frozen replay checkpoint' --retries=0 --repeat-each=20 --reporter=line
npm run e2e -- e2e/research.spec.ts -g 'during delayed analytical and persistence responses' --retries=0 --reporter=line
```

The original baseline used the three-tick synthetic reproduction documented in the review and its log. For manual data acquisition and import commands, see the review. The acquisition log gives successfully downloaded original filenames. Full licensed payloads are intentionally stored locally.
