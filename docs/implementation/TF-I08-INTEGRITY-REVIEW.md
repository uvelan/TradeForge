# TF-I08 evidence identity integrity and browser reliability

- PR #10, feat/TF-I08-unified-evidence
- Reviewed, starting, and starting remote SHA: 863f405e15650b43515423dd960488dc368d8004
- Implementation SHA: 6e150c98733800c46ee5f1246bd87b3a94aecd85. The next commit records the verification evidence.
- Approved baseline: 282 Python tests passed. The reviewed browser workflow's first attempt failed in the trendline and replay test.

## Canonical level evidence

The reviewed standalone path built identities from its generic result contract. CRT independently hashed effective settings, scope, and coverage and derived an event ID from a plotted point. The standalone product also omitted externally loaded OHLC from its calculation snapshot.

The canonical level product now owns configuration, scope, source and reference authorities, coverage, exact reference fingerprints and canonical observations. Selected compatible CRT dependencies use this same product. Each reference window includes the effective coverage and normalized OHLC evidence fingerprint, with real availability and relevant archive partition checksums. A snapshot also binds the caller evidence, UTC cutoff, frozen manifest, and logical feed and price basis. Previous-period observations originate at the period boundary and expire at the following boundary. Session observations come from the final constituent candle and become eligible only when the entire window is confirmed and available. The legacy plot remains the same.

The checked reproduction records full before/after identifiers in [identity-before-after.txt](evidence/TF-I08-integrity/identity-before-after.txt). Custom Tokyo configuration, snapshot, and observation now resolve exactly. The 1.1050 → 1.1070 daily high and 1.1010 → 1.0990 weekly low each revise the snapshot and observation ID under the same configuration. Changing the close only also revises both IDs with identical extrema. An unrelated caller change revises the calculation snapshot and preserves the relevant canonical observation ID. Exact archived reference evidence is still mixed with unverified caller observations, with authority and partition lineage carried separately. Missing, corrupt, incomplete, uncertain-schedule or budget-limited references leave independent CRT observations available and expose dependency status without borrowing expired periods. Corrected context rejects an old CRT pagination cursor.

## Browser findings

The reviewed GitHub failure showed an empty selection after the second click. A local no-retry reproduction recorded the second pointer at y=805.72 in a 768-pixel viewport after the chart shifted. The save request never began. Switching to the chart cell lets Playwright scroll and verify the actual hit target, including the library's stacked canvases. A first attempt to target the paint canvas directly was stopped after three failures because the interaction canvas correctly intercepted the pointer.

Replay creation is content-addressed. Repeated fixtures sometimes returned the same saved checkpoint, with the target candle already bookmarked. The test now observes the real returned session, checks advancement before bookmarking the next unbookmarked candle, verifies the exact checkpoint request, and reloads that exact session ID. Production drawing selection follows an acknowledged in-scope save. Chart preview updates are separate from placement cancellation. Replay controls are tied to the session identity and guard bookmark state and cursor bounds. Direct editing and undo/redo continue to run in the full suite.

The stress fixture uses the supported maximum of eight active indicators, holds a real analysis response between two anchors, holds the drawing response before selection, delays replay data before controls initialize, and verifies checkpoint persistence. A separate test switches timeframe while a persisted save response is held. Earlier runs and failures are retained. The first full local run exposed a held Playwright route leaking into the following test after teardown, and explicit waiting for route cleanup addresses it. Compact extracts of actual trace events contain request statuses and checkpoint payloads without shipping videos or large trace archives.

## Liquidity and order-block timing

The 11 characterization cases establish that legacy wick interaction can be reported when a zone activates during the observed candle, or when its OHLC arrives late. Equal activation and availability does not interact. A candle beginning after activation is eligible under the legacy timing check, and expiry or invalidation preempts the fixture's touch. Replay lookup hides evidence before its actual availability and before its observation timestamp.

These events remain suitable for retrospective chart description. Evidence and analytical contracts flag them as research-only for strategy use. OHLC cannot establish intrabar touch order when activation is inside the candle, and the existing simulated strategy engine does not consume these contextual observations. A strict whole-candle eligibility policy would need a separate versioned TF-I08B decision with explicit late-data and expiry semantics.

## Verification commands

Run from the repository root unless noted. The corresponding captured outputs are in [the evidence folder](evidence/TF-I08-integrity).

~~~powershell
git worktree add --detach "$env:TEMP/TradeForge-TFI08-baseline-863f405" 863f405e15650b43515423dd960488dc368d8004
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m pytest -q research_backend/tests/test_analysis_evidence.py::test_approved_golden_singles_and_combined_are_unchanged
$env:PYTHONPATH='.'
.venv/Scripts/python.exe docs/implementation/evidence/TF-I08/benchmark-paired.py "$env:TEMP/TradeForge-TFI08-baseline-863f405" --integrity
.venv/Scripts/python.exe docs/implementation/evidence/TF-I08-integrity/reproduce-identity.py "$env:TEMP/TradeForge-TFI08-baseline-863f405"
Set-Location frontend
npm run test
npm run build
npx playwright test e2e/research.spec.ts --grep 'persists a trendline and resumes a frozen replay checkpoint' --retries=0 --repeat-each=20
npx playwright test e2e/research.spec.ts --grep 'delayed analytical|delayed drawing save' --retries=0 --trace on
npx playwright test --retries=0
~~~

- Python: 309 passed, including 16 canonical identity and source tests, 11 timing cases, and 22 approved projection fingerprints covering singles and two combined fixtures.
- Vitest: 8 passed. TypeScript and Vite build passed with existing vendor bundler warnings.
- Focused browser repeat: 20 passed in one no-retry run. Earlier attempts recorded 19 passes and one offscreen pointer failure, then three visible actionability failures in a stopped test-only trial. The stress fixture first failed on an empty-scene selector assertion and later passed alongside the delayed-scope test.
- Full Playwright: 63 passed in 3.9 minutes with zero retries. The earlier completed run recorded 61 passes and one route-teardown failure. An intervening run was stopped when the teardown correction was ready. The push and pull-request workflow results belong to the final evidence commit and are checked independently on GitHub.

## Benchmark and limitations

The final paired run uses one Windows process and identical input fixtures. It alternates baseline and candidate ordering for 12 samples per version. Calculation, JSON and API timings are P50/P95. Python allocation peak uses tracemalloc. Native RSS is the process working set immediately before and after the allocation run and cannot isolate peak per candidate. Local background processes contribute to tail latency. The extra Level reference metadata increases output size. Full measurements, including the overlapping archive and 10,000-row cases, are in [benchmark.txt](evidence/TF-I08-integrity/benchmark.txt).

| Fixture | Calc baseline → final P50/P95 ms | API baseline → final P50/P95 ms | Python peak baseline → final KiB |
| --- | ---: | ---: | ---: |
| Previous day, 2,000 caller candles plus external reference | 25.29/43.74 → 26.28/40.26 | 93.08/122.78 → 92.48/284.17 | 1601.8 → 2095.5 |
| Five compatible, 2,000 candles | 189.71/279.66 → 195.97/247.68 | 270.87/326.61 → 248.49/431.07 | 2001.0 → 1912.7 |
| Context enabled, 2,000 candles | 221.10/525.53 → 229.82/299.41 | 234.29/346.84 → 276.77/497.83 | 4555.7 → 4988.0 |
| Shared selected levels, 2,000 candles | 300.16/504.82 → 241.71/434.89 | 455.46/864.82 → 389.30/602.16 | 8771.6 → 5635.5 |
| Five compatible, 10,000 archived candles | 1235.23/1465.74 → 1246.63/1439.56 | 1115.85/1337.63 → 1086.45/1372.35 | 23055.4 → 22879.9 |

One structure pass remains sufficient for compatible indicators. The two overlapping archive windows still use two checksum checks and two physical decodes with one partition reuse. The five-partition, 10,000-candle fixture still computes one structure pass and performs five checksum checks and decodes. The standalone and context-enabled API tails regressed in this local sample, despite the shared-selected improvement.

The optional analytical context remains research-only. The checked fixtures use deterministic stored/synthetic evidence. No live broker session or mechanical execution eligibility was verified. New level ID policy version 2 and CRT snapshots require clients to restart older pagination against the updated calculation. Existing drawings, notes, replay and user data require no schema migration.

## Rollback

Revert the implementation commit on the branch and revert this evidence commit if desired. Keep the Git history and databases. If reverting after a new CRT cursor has been issued, restart pagination under the restored version. Do not reset or force-push the PR branch.
