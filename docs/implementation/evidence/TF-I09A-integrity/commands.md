# TF-I09A integrity verification commands

The baseline reproduction script imports the original strategy fixture. Reproduce it from the reviewed SHA `5b0d93151e7451cabdc0274501a011cbc0579a25` with this script copied into the baseline checkout and the repository root on PYTHONPATH. The terminal details came from the actual CRT engine.

```powershell
git fetch origin
git rev-parse origin/feat/TF-I09A-strategy-definition
$env:PYTHONPATH=(Get-Location).Path
& .\.venv\Scripts\python.exe docs/implementation/evidence/TF-I09A-integrity/baseline.py
& .\.venv\Scripts\python.exe -m pytest -q
```

Run the corrected verification serially from the repository root with locked dependencies installed.

```powershell
$env:PYTHONPATH=(Get-Location).Path
& .\.venv\Scripts\python.exe docs/implementation/evidence/TF-I09A-integrity/benchmark.py
& .\.venv\Scripts\python.exe -m compileall -q research_backend tradeforge scripts
& .\.venv\Scripts\python.exe -m pytest -q
& .\.venv\Scripts\python.exe -m pytest -q research_backend/tests -k 'strategy or causal or strict or smt or evidence or historical or arrival or legacy or approved_golden'
Set-Location frontend
npm run test
npm run build
$env:TRADEFORGE_E2E_API_PORT='8196'
$env:TRADEFORGE_E2E_UI_PORT='4196'
npm run e2e -- --retries=0 --reporter=line
npm run e2e -- e2e/research.spec.ts -g 'persists a trendline and resumes a frozen replay checkpoint' --retries=0 --repeat-each=20 --reporter=line
npm run e2e -- e2e/research.spec.ts -g 'during delayed analytical and persistence responses' --retries=0 --reporter=line
```

The golden suite compares all 22 approved fingerprints in one test. The serial Playwright configuration uses isolated temporary storage.

## Initial attempts

- The first regression run used the literal `config` in a synthetic structure producer instead of a 64-character configuration digest. The strict exact-reference DTO rejected the fixture. The corrected fixture uses a digest, and the pure selector treats unrecognized or malformed exact evidence as UNKNOWN. [First attempt](first-test.txt) · [Corrected focused run](contracts.txt)
- All three initial Strategy Builder browser cases passed. [Focused browser run](builder-first.txt)
