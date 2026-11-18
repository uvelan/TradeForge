# TF-I08D market-data readiness and availability authority

| Reference | SHA |
| --- | --- |
| Reviewed TF-I08C PR #12 head | 805945e4f06d03004d5a102fd2cefe643afad3ea |
| TF-I08C merge and TF-I08D base | 921452be91513643b9c5cdd373db117f2f936b32 |
| Implementation | ff30dd65eb8edd3b4551c3c551f40cd76a4b4bf8 |

The prerequisite passed both workflows at the reviewed SHA and had no unresolved review threads. TF-I08D tracks a separate feature branch.

## Admission reproduction and disposition

The reviewed code admitted all 80 explicit no-marker archive timestamps in the shared single-instrument history reader. Strict SMT refused the same inputs as unverified. The baseline engine fixture reported its actual coverage and strategy reasons, including unknown coverage where the fixture lacked a calendar. It did not supply the missing authoritative writer contract. The corrected test supplies declared coverage and a registered capture fixture. It verifies explicit no-marker and nominal-close-only archives admit zero recorded timestamps. Early archive timestamps fail source integrity. Client-provided capture markers do not change client authority.

| Analytical path | Corrected admission evidence |
| --- | --- |
| Market structure | The ordinary archive is ineligible. Registered pivot and structure events with present coverage are eligible. |
| Liquidity | The ordinary archive is ineligible. Registered qualifying events are eligible, subject to whole-candle timing and known coverage. |
| FVG | The ordinary archive is ineligible. Registered events pass where the positive timing and dependency rules permit. |
| Order blocks | The common bare archive is ineligible. The registered overlapping-window regressions produce the exact BOS dependency and the block at 37500. |
| CRT | The ordinary archive is ineligible. Registered qualifying reference/lifecycle events pass while expired and research-only records remain classified. |
| SMT | Bare and nominal-close archives are unverified. Registered delayed dual-feed fixtures preserve exact frontier, candidate, confirmation, and current eligibility. |

[Baseline output](evidence/TF-I08D/baseline-arrival.txt) · [Corrected output](evidence/TF-I08D/admission.txt)

## Capture and trust boundary

Capture is off by default. The local Data Library starts and stops a worker explicitly. One worker owns up to eight MT5 symbol scopes at a common M1 or M5 timeframe. The research adapter serializes access through its existing MT5 lock and excludes the most recent bar by timestamp. Capture validates symbol, basis, aligned UTC slot, explicit provider closed status if supplied, nominal close, volume, and the declared calendar. The logical feed identifier is a stable hash-based alias of the actual terminal metadata. No account identifiers are written into the capture. Read-only polling has no trading permissions or order submission.

A local SQLite lease fences each symbol and timeframe across workers. Start is idempotent while the matching configuration runs. The worker has one in-flight batch and no producer queue, so slow I/O delays polling rather than allowing memory growth. It uses bounded reconnect backoff and reports overdue slots independently from worker state. Recovery has a bounded 32-bar lookback. The first polling snapshot excludes historical observations whose nominal close predates explicit session start. After a full restart, uncaptured earlier slots remain missing. Already observed candles can produce corrections at the new observation time.

Provider timestamp identifies the candle interval. The server records the time at which it first observed the closed value using UTC milliseconds and a monotonic elapsed measurement under LOCAL_UTC_MONOTONIC_GUARD_V1. A regression or wall/monotonic discrepancy beyond two seconds prevents trusted continuation. The implementation makes no NTP, timestamp-offset, or provider authentication claim. OHLC and original source timing are distinct. The SQLite observation is appended and committed before sampling the receipt time. Analytical availability uses the post-commit receipt time rounded up to integer UTC seconds. A crash between the durable observation and its receipt leaves the row invisible. A confirming later poll can issue a receipt at its later actual time. Public replays additionally require a sealed manifest. The receipt describes durable candle evidence, while later sealing selects that already recorded timeline.

Frozen manifests bind scope, cutoff, clock/capture policy, calendar, exact IDs and SHA-256 values, and registered session IDs. Capture rows include nominal close, observation and receipt timestamps, sequence, content fingerprint, volume, and source metadata when supplied. They live in a separate Parquet kind. Sealing assembles immutable bounded Parquet batches from the receipt ledger. Only the trusted sealing path registers partitions and the manifest. The analytical reader verifies the selected files, catalog scope, capture partition registration, sessions, and per-row timing. Ordinary imports, raw marker strings, and checksums alone never grant recorded admission. The trust root is the operating-system account protecting the local application, SQLite database and filesystem.

The existing archive writer recovers a checksum-identical orphan file at its canonical path. Conflicting bytes or a catalog entry without a valid file fail closed. An interrupted seal can leave complete but unattested archives. These cannot grant authority, and a retry can adopt the same verified bytes. The schema adds immutable records and a bounded lease table without rewriting existing datasets, journal rows, drawings or replay checkpoints.

## Corrections, aggregation, simulation and readiness

Unchanged repeated observations preserve their receipt and fingerprint. A changed value appends a new sequence linked to the original. Replaying an earlier cutoff uses the latest admitted value available then. The regression captures an original first observable at 62, then a correction first observable at 76. The older manifest retains the original after 76 and a newly sealed manifest shows the correction at 76. Frozen calculation and cursor identities include the manifest context and evaluation mode.

Recorded M1-to-M5 aggregation uses a distinct feed under RECORDED_M1_TO_M5_V1. It verifies the input manifest and requires all five admitted slots. Open and close use first and final constituents, high and low use extrema, volume is summed, and the constituent fingerprints are retained. An aggregate receives a new durable publication receipt. The test keeps its aggregate unavailable until 706 despite earlier constituent availability, and declines a second aggregate with a missing slot. Existing CRT uses its complete constituent-grid timing for in-process derived references. Native higher-timeframe references remain separately scoped, and an incompatible recorded or simulated reference fails closed.

The version 1 modes are LEGACY_RESEARCH, STRICT_RECORDED and HISTORICAL_SIMULATION. Recorded uses SERVER_RECORDED_ARRIVAL_V1. Historical simulation uses SIMULATED_NOMINAL_CLOSE_PLUS_DELAY_V1 with bounded nonnegative integer delay. It derives availability from nominal close plus the configured delay for a frozen verified historical archive and declared calendar. Both SMT inputs can have distinct delays. The simulation purpose has its own calculation identity, cache, reference authority and eligibility label. The recorded strategy guard rejects simulation references. The Data Library presents modes, capture state, sealed selection, per-feed and joint readiness, frozen as-of evaluation, and downloads the evaluation with its mode. Existing replay and legacy SMT settings and outputs remain available in their normal screens.

A read-only inventory of the configured workspace research catalog found zero datasets and zero sealed manifests. No existing workspace data is presented as eligible. The test archives independently reproduce both READY_RECORDED and modeled historical admission. Production remains blocked by the live acceptance result below. [Inventory](evidence/TF-I08D/inventory.txt)

## Live broker acceptance

**FAIL. Production recorded-data readiness remains BLOCKED.** The read-only adapter found the connected terminal and returned aligned M1 arrays for both EURUSD and GBPUSD. Their timestamps were approximately two hours ahead of the host UTC clock. Both scopes degraded with PROVIDER_NOMINAL_CLOSE_IN_FUTURE, published zero receipts, and stopped. No timestamp offset was inferred. The verification used a temporary database and archive folder. [First live attempt](evidence/TF-I08D/live-readonly.txt) · [Timestamp diagnostic](evidence/TF-I08D/live-diagnostics.txt) · [Confirmed rejection](evidence/TF-I08D/live-readonly-rerun.txt)

After a provider with verified UTC timestamp semantics is available, reproduce using the [read-only acceptance script](evidence/TF-I08D/live_acceptance.py) or Data Library. Start EURUSD and GBPUSD M1 with the verified calendar, allow at least one new candle close, confirm both durable receipt timestamps follow nominal close, freeze the common window, verify exact manifest and readiness, and stop. Then check cross-symbol replay at a cutoff before and after the recorded arrival. A successful single observed candle proves the capture path only for that window.

## Verification

See [execution logs](evidence/TF-I08D/). The baseline setup needed the actual liquidity indicator key and displayed provenance field. The first reconnect fixture omitted the scheduler lease heartbeat and was corrected to reproduce a live worker. The first full backend run identified six previously checksum-only analytical fixtures. They now register the capture evidence explicitly while keeping the original lifecycle and order-independence assertions. All 448 backend checks pass, including all 22 approved golden fingerprints and legacy SMT journals and alerts. The corrected admission and capture cases total 16.

| Check | Observed result |
| --- | --- |
| `npm test` | 8 passed |
| `npm run build` | TypeScript and Vite passed. Existing MUI bundling warnings remain. |
| `npm run e2e -- --retries=0 --reporter=line` | First run 65 passed and the existing rectangle-handle case timed out. Isolated full rerun 66 passed in 4.3 minutes, including that case and the new capture control fixture. |
| Drawing and replay checkpoint, zero retries | All 20 consecutive cases passed |
| Delayed-analysis drawing stress | Passed |

The [readiness screenshot](evidence/TF-I08D/arrival-readiness.png) shows controlled browser fixture data. Its READY_RECORDED label describes test data, while the real broker acceptance failed as documented above.

## Performance

The [main benchmark](evidence/TF-I08D/benchmark.py) and [capture follow-up](evidence/TF-I08D/benchmark_capture_extra.py) used Python 3.13.14 on Windows 11 with isolated SQLite and Parquet. Every table row used 30 warmed serial samples. The ASGI lane includes request validation, archive checks and response serialization. Python allocation tracing ran separately. Windows working-set figures are cumulative process high-water marks.

| Dual-feed SMT workload | Mode | Calculation P50 / P95 ms | API P50 / P95 ms | Peak Python MiB |
| --- | --- | ---: | ---: | ---: |
| 2,000 dense candles per feed | Recorded | 409.4 / 478.1 | 417.9 / 493.5 | 20.3 |
| 2,000 dense | Simulation | 390.9 / 478.1 | 396.9 / 485.1 | 17.9 |
| 10,000 sparse | Recorded | 687.2 / 737.5 | 694.4 / 735.0 | 50.3 |
| 10,000 sparse | Simulation | 622.5 / 656.4 | 621.1 / 658.0 | 38.3 |
| 10,000 dense | Recorded | 2115.8 / 2277.4 | 2117.7 / 2226.2 | 101.3 |
| 10,000 dense | Simulation | 2073.5 / 2156.8 | 2057.8 / 2176.7 | 89.3 |

The recorded dense lane verified 10 checksums, decoded 20,000 raw records, issued 9,990 registry publications, and returned 167,277 bytes. Working-set high water was 338,092 KiB. The existing analytical guards for requested slots, archive decodes, partition count, work and estimated memory are still enforced. The measured Python peak increased from TF-I08C's 90.8 MiB to 101.3 MiB. Recorded-session metadata is larger than modeled OHLC input, and the existing estimate measures charged units rather than actual process allocations. This is an outstanding dense-workload cost.

The two-symbol capture benchmark measured 31.6 / 39.1 ms to persist a pair and 9.7 / 13.8 ms for duplicate polls. It recorded 66 unique observations with queue depth zero. Sealing 33 rows took 115.5 ms. A restart poll left duplicate evidence unchanged. Synthetic 5 ms delays added per SQLite capture transaction produced 51.6 / 58.9 ms per two-symbol pair. A verified read at cutoffs before and after a correction took 20.9 / 25.9 ms per pair. The delayed transaction fixture measures injected contention rather than the performance of a particular physical disk or broker. [Analytical and capture logs](evidence/TF-I08D/benchmark.txt) · [Storage and correction logs](evidence/TF-I08D/benchmark-capture-extra.txt)

## Limitations and rollback

The current production MT5 terminal failed UTC validation. The system has no validated broker-local-clock conversion, NTP accuracy guarantee, native H1 capture, or production Dukascopy arrival writer. Per-worker capture is deliberately serialized at the provider. A blocked native provider call cannot be canceled safely in Python. Stop returns STOPPING if needed while the existing lease and in-flight operation remain fenced. The capture timeline starts from running the new worker and cannot recover original dissemination times for periods when it was offline. Broader broker calendars and holiday overrides require evidence from their respective feeds. Independent UI as-of controls evaluate frozen evidence without changing the existing chart checkpoint or strategy replay contracts.

Revert the evidence commit and then ff30dd65eb8edd3b4551c3c551f40cd76a4b4bf8 on the feature branch. Stop the capture worker before rollback and keep the additive tables and immutable files as historical evidence. Existing application records require no migration back.
