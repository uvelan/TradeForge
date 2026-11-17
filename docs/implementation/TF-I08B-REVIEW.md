# TF-I08B verification

- TF-I08 merge and TF-I08B base: 42bd8c7539a9a4349d73a1b79be77466b0ca40e9.
- Reviewed TF-I08 head: e6ee436193740e5ca2d80a3c991faa845e0237ab. GitHub reported first-attempt success for push and pull request, no blocking reviews, and a clean merge.
- TF-I08B implementation: 735fa5abe1eb97328d72d169571abef8735efe9e, 20a58c95cb1deaf07f2e22a88426916a1abacc4d and ff42549e30cd076d0478da93d3b6013c3775d36a. The final commit records verification.

## Timing and lifecycle

LEGACY_RESEARCH v1 remains the default. The 22 approved legacy output fingerprints match. STRICT_CAUSAL v2 uses a separate timing input for analytical snapshots and affected root event identities. Delivery limits are projection-only. The control is persisted with existing workspace settings, and switching modes uses a separate analytical request and selection scope.

The immutable UTC timing contract separates a closed observation interval, its nominal confirmation, actual availability, dependency availability, evaluation cutoff and source correction time when known. The latter is null for current data readers. Availability cannot precede the close or required dependency. Whole-candle observations allow equality at the open. Close-only observations require the dependency to precede the close strictly. A delayed close is still evaluated at its nominal close. Attaching a stamp to a browser candle, including the replay close fallback, cannot grant recorded archive availability.

| Engine | Strict behavior | Evidence |
| --- | --- | --- |
| Structure | Close-only BOS, CHOCH and countertrend breaks use the close boundary. Wick candidates use the open. Invalidation also checks the candidate availability. Ineligible observations cannot consume the pivot or move protected state. | Close after activation formed BOS. The straddling wick and a late historical close were rejected. |
| Liquidity | Sweep/reclaim interactions use the latest pool revision, including changed boundaries. A range-independent close break uses the close. Ambiguous crossing retires the reference as UNCERTAIN, without claiming a sweep, break or untouched pool. A later pool requires new confirmed pivots. | Verified late revisions retire a straddling pool, including a wick that only crossed the narrower prior boundary. A subsequent candle swept, a qualifying close broke, and expiry preempted a crossing. |
| Order blocks | Formation requires an eligible structural dependency, candidate evidence available by observation open and whole-candle displacement when configured. Block touch/mitigation needs overlap and the visibility barrier. | Ineligible BOS rejected. An eligible BOS formed the exact 100–105 zone, and straddling, subsequent mitigation and expiry fixtures separated. |
| FVG | Close mode also requires whole-candle overlap. A straddling crossing retires the zone as UNCERTAIN. Gap-through has no eligible mitigation evidence. | On the same 100–105 zone the reviewed policy produced partial mitigation and strict v2 recorded uncertainty. |
| CRT | The existing reference and post-reclaim open floors remain. Strict mode versions evidence and reports an overlapping crossing as uncertain. Dual-sweep ambiguity and unobserved gaps cannot establish strategy evidence. Strict context accepts eligible dependencies known at observation open. | Delayed reference, dual sweep and delayed reclaim fixtures. Rewind removed the browser observation before activation. |

In each engine, an unexpected continuity failure invalidates prior state before expiry or new price interactions. Confirmed expiry precedes price-based transitions. Aging uses accepted causal observations rather than invented slots. Declared scheduled closures preserve appropriate references without adding weekend bars. Later observations are ordered by availability and UTC open for ties. An older candle arriving after a newer processed candle stays out of earlier decisions. A frozen snapshot can change after an actual correction, and changed evidence invalidates its old cursors.

The backend guard accepts eligible, recorded, verified evidence at the consumer cutoff. Missing metadata, unknown authority, provisional or uncertain state, expiry, failed coverage and failed provenance do not pass it. Strict contextual selection uses this guard. The application still presents descriptive chart events even when they fail that guard.

## Reproduction and logs

- [Exact before/after event JSON](evidence/TF-I08B/fvg-before-after.jsonl). The formation is visible at UTC 1020, and the overlapping candle spans 900–1200.
- [Replay inspector screenshot](evidence/TF-I08B/strict-straddling-replay.png)
- [Backend](evidence/TF-I08B/backend-final.txt), [focused timings and goldens](evidence/TF-I08B/focused-final.txt), [Vitest](evidence/TF-I08B/vitest.txt), [build](evidence/TF-I08B/build.txt), [full Chromium](evidence/TF-I08B/browser-full.txt), [focused browser replay](evidence/TF-I08B/browser-strict.txt) and [20 repeated checkpoint runs](evidence/TF-I08B/browser-repeat.txt)

~~~powershell
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m pytest -q research_backend/tests/test_strict_causal_timing.py research_backend/tests/test_analysis_evidence.py::test_approved_golden_singles_and_combined_are_unchanged
$env:PYTHONPATH='.'
git worktree add --detach "$env:TEMP/TradeForge-TFI08B-baseline-42bd8c7" 42bd8c7539a9a4349d73a1b79be77466b0ca40e9
.venv/Scripts/python.exe docs/implementation/evidence/TF-I08/benchmark-paired.py "$env:TEMP/TradeForge-TFI08B-baseline-42bd8c7" --causal
Set-Location frontend
npm run test
npm run build
npx playwright test --retries=0
npx playwright test e2e/indicator-engine.spec.ts --grep 'strict FVG timing' --retries=0 --trace on
npx playwright test e2e/research.spec.ts --grep 'persists a trendline and resumes a frozen replay checkpoint' --retries=0 --repeat-each=20
~~~

Local verification recorded 349 backend tests, 40 strict fixtures, 8 Vitest tests, and a passing TypeScript/Vite build. Chromium recorded 64 full-suite passes and 20 checkpoint repetitions, each without retries. The full local browser run preceded the final liquidity boundary safeguard. Hosted workflows check the exact final evidence commit.

## Performance

The benchmark interleaves 12 samples per version on one Windows host with identical input evidence. Raw results include calculation/API/JSON P50 and P95, Python allocation peak, native process working-set samples, response bytes, structure executions, and archive checksum/decode counts. Native working-set samples reflect the shared process and are not per-version peaks. Concurrent local checks contributed noise to the first run. The paired results on the final implementation are in [benchmark-exact.txt](evidence/TF-I08B/benchmark-exact.txt). Sampled API tails vary by workload. Previous-day and context-only behavior were measured independently of strict archive overhead. Strict evidence increases response size.

| API fixture, ms P50/P95 | TF-I08 base | TF-I08B legacy | Strict v2 |
| --- | ---: | ---: | ---: |
| 2,000 caller, combined | 163.10/210.26 | 162.23/227.94 | 207.79/237.22 |
| 2,000 caller, context enabled | 195.06/260.62 | 189.76/291.04 | 232.36/246.49 |
| Previous day, standalone | 65.10/79.00 | 68.73/77.57 | — |
| 10,000 verified archive candles | 790.86/814.83 | 793.05/849.98 | 988.22/1024.66 |

The isolated previous-day tail increase did not reproduce in the last sample. The legacy context tail rose in that sample. The 10,000-candle fixture kept one compatible structure execution, five checksum checks and five physical decodes in each version.

## SMT boundary and limitations

A future SMT adapter needs an actual per-feed availability record for every constituent, UTC opening and closing boundaries for both frames, a shared cutoff, both feed and price-basis identities, coverage and clock-quality evidence, and stable references to both pivot pairs. It must wait for the latest required dependency across both feeds and distinguish source corrections from late arrivals. Existing SMT matching and publications were preserved.

The recorded-availability path was tested on deterministic archived fixtures. Ordinary historical or broker candles may carry only nominal opens or closes, so they cannot satisfy strict strategy eligibility without separately recorded arrival times. Coverage uses declared schedules and observed continuity. ATR-derived FVG evidence is conservatively held ineligible until its full dependency provenance can be attested. A retired uncertain reference has no automatic recovery. The workspace uses one timing mode per analytical request. Live broker availability and intrabar paths remain outside this verification.

## Rollback

Revert the implementation commits on the feature branch, plus this evidence commit if desired. Existing SQLite, Parquet, replay and export records require no migration. Restart strict cursors under the restored policy.
