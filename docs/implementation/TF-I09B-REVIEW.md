# TF-I09B mechanical strategy evaluator review

TF-I09A merged at `f5693c54bead5d62a99e52e34e2d4a66e0345cc7`, which is the TF-I09B baseline. The final implementation source is `d92d33077b14bd811b330727adf277da9648e394`. The subsequent evidence commit is linked from [PR #16](https://github.com/uvelan/TradeForge/pull/16). See the [implementation plan](TF-I09B-PLAN.md) and [verification evidence](evidence/TF-I09B/).

## Contract and producer dependencies

Evaluator v1 accepts ready immutable strategy schema 2, defaults policy V2, binding policy 2, and `HISTORICAL_SIMULATION` only. It revalidates the stored version, cross-checks its configuration fingerprint, and resolves the supplied selection ID and fingerprint against the server allowlist. It pins the UTC warm-up and evaluation boundaries, calendar, feed, timeframe, basis and partition checksums. Untrusted paths, evidence flags and caller-supplied events have no request fields. The only default production selection is the approved November 13, 2026 Dukascopy BID scope for EURUSD and GBPUSD on M1 and M5.

The evaluator coordinates the existing CRT, strict SMT, Market Structure, Liquidity, FVG and Order Block producers. Liquidity and Order Block pin their configured Market Structure parents. SMT pins both participant feeds, their pivots, matching frontier and lifecycle. Identical effective calculations reuse one producer result inside a request. Incompatible source or parameter identities retain distinct snapshots. An internal canonical registry sink captures typed publications independently of public event, zone and history-page limits. Each exact reference is admitted through its own snapshot, purpose, source and revision.

| Conditions | Canonical projection |
| --- | --- |
| CRT reference, reclaim, state, midpoint | Exact CRT lifecycle revision and range midpoint |
| SMT confirmed, leader, laggard | Confirmed strict SMT divergence and matched pair role on the same family |
| Structure pivot, break, BOS direction, CHOCH direction, break price | Confirmed structure events, subtype, direction and break level |
| Liquidity pool, active pool, sweep | Pool lifecycle state and sweep or break event |
| FVG active and FVG state | Exact FVG lifecycle revision |
| Order Block active and Order Block state | Exact Order Block lifecycle revision |

## Truth, timing and attempts

A satisfied condition has a canonical, eligible purpose-aware proof. A failed condition requires complete applicable coverage and a known negative. Missing, stale, ambiguous and ineligible required evidence produce UNKNOWN. SMT absence remains UNKNOWN if the matching frontier cannot establish it. Mandatory coverage is checked before rule evaluation. ALL, ANY and NOT apply the definition contract's three-valued truth tables.

The evaluator walks availability time. Historical evidence can establish prerequisites before activation, while each phase's activation records the cutoff when its conditions were met. Explicit AFTER and SEQUENCE require strictly later availability and apply the declared bound. Traded-role elapsed eligible bars require complete coverage on the declared time frame. Seconds expiry schedules its first expired cutoff. The final allowed expiry boundary is inclusive.

| State | Transition contract |
| --- | --- |
| WAITING_FOR_CONTEXT | No attempt has selected a usable context |
| WAITING_FOR_SETUP | Context is active and setup is pending |
| SETUP_OBSERVED | Setup activated at an explicit cutoff |
| WAITING_FOR_TRIGGER | Later eligible trigger proof is pending |
| INSUFFICIENT_EVIDENCE | A required input, family, timing, coverage or invalidation is unresolved |
| TRIGGER_QUALIFIED | Terminal qualification with a single entry intent |
| INVALIDATED | Terminal family or explicit invalidation |
| EXPIRED | Terminal family or configured expiry |
| NO_TRADE | Terminal known negative at the frozen endpoint |

CRT activates only an exact eligible historical ACTIVE reference with an eligible latest family state. The reference remains latched to its family across reclaim. Peer range expansions cannot satisfy that family's trigger. Bound terminal and unresolved revisions check the exact family's state. SMT direction and pair roles are verified against the same bound strict SMT family, with exact participant provenance. Required family validity and explicit invalidation precede expiry and qualification.

A qualified intent records the declared traded role and direction, order kind, configured fixed price where applicable, basis, earliest boundary, order expiry and risk and management fingerprints. The intent remains `AWAITING_EXECUTION_VALIDATION`. Evaluation does not provide quote-relative validation, market fills, executable ASK inferred from BID, trading, or performance.

## Identity, persistence and replay

The run ID hashes the immutable strategy reference, configuration, purpose, version, source selection and evaluation boundaries. The decision ID hashes the semantic result, exact provenance/evidence, timeline and cutoff. Diagnostic work counters and processing times are excluded. SQLite stores the predecessor separately so an independent reconstruction at the same cutoff can reproduce the same ID regardless of previous checkpoints. The stored append sequence uses an expected decision ID and an immediate transaction. Repeated requests return the same saved decision, stale competing advances conflict, and existing decisions are immutable. An intent ID is derived from the attempt and qualification cutoff.

Reconstruction starts at the original evaluation boundary and materializes causal availability prefixes at the requested cutoff. Advancement rebuilds that same bounded graph once per request and persists a forward checkpoint. The browser and backend fixtures compare persisted and reconstructed IDs, rewind across qualification, open the exact archived observation and re-advance without another logical intent. A single run stops at the first terminal attempt.

## Archive isolation and accepted market-session check

Six exact synthetic clock-calibration archive sessions, partitions and manifests were identified without deleting or rewriting records. The [quarantine inventory](../../research_backend/app/quarantined_capture.json) pins the affected identifiers. Archive readers and manifest admission exclude that provenance. No matching active leases or recorded readiness entries were identified. The backend test wrapper hashes the configured workspace database, WAL if present, and market files before and after pytest. The test fixtures initialize disposable storage before the app imports. The accepted-market script reads and checksums the frozen partitions, then copies them to temporary storage for execution. The repository includes no raw provider archives.

The [acceptance trace](evidence/TF-I09B/real-session-final.json) evaluates explicitly named test configurations for LONG and SHORT at role delays 0/0, 2/2, 2/15 and 15/30 seconds using M5 CRT and M1 SMT. Each delayed reference was independently reaggregated from its 12 source bars and checked against the evaluated observation availability and activation. All eight runs reproduced their decision IDs and expired without a qualified trigger. The delays are modeled assumptions. The default templates remain drafts and the result establishes neither a recommendation nor strategy performance.

## API access and reproduction

The Strategy Builder exposes Evaluate on compatible ready versions. It displays modeled latency, frozen coverage, rule and phase outcomes, family bindings, temporal links, expiry, invalidation, decision history and evidence. The chart action opens the pinned source at the evidence cutoff and marks the observation. The browser fixture uses explicitly isolated synthetic data. See the [evaluation screenshot](evidence/TF-I09B/strategy-evaluation.png) and [frozen chart screenshot](evidence/TF-I09B/strategy-evaluation-chart.png).

Routes are rooted at `/api/strategy-evaluations/v1`. Fetch `GET /selections`, then send the pinned identity in `POST /validate` or `POST /api/strategy-evaluations/v1`. A frozen request includes evaluator_version, purpose, strategy_id, strategy_version_id, configuration_fingerprint, selection_id, selection_fingerprint, warmup_start, evaluation_start and cutoff. The real-session trace includes eight complete examples. Send `{"cutoff":1794607200}` to `POST /{run_id}/reconstruct` for a nonpersistent view when that UTC cutoff falls inside the run. `POST /{run_id}/advance` also takes expected_decision_id. `GET /{run_id}/decisions/{cutoff}/evidence/{snapshot}/{event_id}` verifies membership in that decision. Append `/chart` to retrieve the evidence chart. `GET /{run_id}/attempts/{attempt_id}` inspects a saved attempt.

```powershell
$env:PYTHONPATH = (Get-Location).Path
.\.venv\Scripts\python.exe scripts/verify_strategy_test_isolation.py
.\.venv\Scripts\python.exe -m compileall -q research_backend tradeforge scripts
.\.venv\Scripts\python.exe scripts/verify_real_strategy_evaluation.py --samples 30
Set-Location frontend
npm test
npm run build
npm run e2e -- --retries=0 --reporter=line
npm run e2e -- e2e/research.spec.ts -g 'persists a trendline and resumes a frozen replay checkpoint' --retries=0 --repeat-each=20 --reporter=line
npm run e2e -- e2e/research.spec.ts -g 'during delayed analytical and persistence responses' --retries=0 --reporter=line
```

## Verification and budgets

The [baseline](evidence/TF-I09B/baseline.txt) ran 591 tests. Final backend pytest ran 609 tests, including all 22 legacy numerical fingerprints. The 44 tracked workspace archive files were byte-identical before and after. The [backend run](evidence/TF-I09B/backend-final.txt) and [Vitest](evidence/TF-I09B/vitest.txt), [final build](evidence/TF-I09B/build-final.txt), [Chromium](evidence/TF-I09B/browser.txt), [checkpoint repetitions](evidence/TF-I09B/checkpoint-final.txt) [final evaluator browser check](evidence/TF-I09B/browser-final-evaluator.txt) and [stress](evidence/TF-I09B/browser-stress.txt) logs record the actual commands' results. The baseline and final pytest summaries include the existing Starlette TestClient deprecation warning. The first [browser run](evidence/TF-I09B/browser-first.txt) exposed a date-input race and ambiguous evidence locator. The first [candidate overflow run](evidence/TF-I09B/candidate-first.txt) had an incorrect age window in the fixture. Those failures are retained. The first hosted push and PR runs had the same Windows short-path assertion failure. The fix resolves the temporary directory before using it. The first checkpoint repetition series is also retained. One iteration navigated away during an evaluator UI edit. The final series ran on frozen source with the same retries and timeout.

The accepted-market benchmark takes 30 warmed serial samples per case. It reports P50/P95, peak Python allocations, response bytes and available decoder and registry counters in [the trace](evidence/TF-I09B/real-session-final.json). Latencies are local-process measurements. The benchmark records native process working-set high-water marks of 177.4–191.7 MB. Those cumulative values include app startup and retained runtime allocations. Resource guards cover graph size, 6,000 frozen observation slots, a 4,096-entry timeline, 16,000 publications, a million candidate scans, 256 KiB serialized decisions, up to two concurrent requests, 256 saved decisions per run and 512 runs. Producers additionally use the existing analytical work and estimated 96 MiB budget.


| Workload | P50 (ms) | P95 (ms) | Peak Python bytes |
| --- | ---: | ---: | ---: |
| One instrument, one timeframe | 34.434 | 42.778 | 764,233 |
| Full approved CRT/SMT session | 182.304 | 274.665 | 7,232,473 |
| Earlier reconstruction | 89.055 | 148.307 | 3,912,164 |
| Six compatible producers | 270.870 | 408.365 | 8,815,517 |
| Persisted advancement, including calculation and write | 200.562 | 292.722 | 7,382,090 |
| Decision serialization | 0.108 | 0.132 | 18,898 |
| Maximum graph compilation | 0.073 | 0.197 | 30,552 |
| Oversized graph rejection | 0.650 | 0.665 | 182,892 |

Excluding producer algorithms, compiling the strategy graph scales with nodes and dependency edges. Constructing the timeline takes O(E log E) across available events and scheduled expiries. Rule processing uses binary search on indexed publication lanes followed by scans of the eligible time windows, bounded by the combined scan limit. Retained prefix state is proportional to admitted bars, publications, timeline entries and rule transitions. Runtime accounts for work and bounded input, with no hard wall-clock or process-RSS guarantee.

## Limits, future integration and rollback

Production recorded readiness remains BLOCKED. No trusted MT5 clock attestation was added. The evaluator only admits the frozen session described above. There is no whole-history incremental producer certification, executable quote model, or fill simulation. Coverage outside admitted windows and dense inputs outside the evaluator budgets require separate validation. The six-producer run measures overlapping canonical output in the approved session. A separate worst-case dense market-history benchmark and a hard wall-clock cutoff remain unverified. The rule integration test, binding contract suite and real-session traces establish the cases they exercise. They do not supply independent end-to-end triggering examples for every observation or divergence direction.

TF-I10A must consume immutable strategy, selection, evaluator and cutoff identities and exact canonical proofs. Require incremental results to agree with independent as-of reconstruction before any event can become backtest input. The execution layer must separately establish executable quotes, execution model and evidence eligibility.

To roll back, revert the implementation and evidence commits in reverse order and redeploy. The migration is additive. Keep the immutable evaluation tables for audit and retain the quarantine inventory and archival exclusion while investigating affected sources. Preserve strategy versions, archive files and user records.
