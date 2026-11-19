# TF-I08D durable arrival and aggregate integrity

The reviewed [PR #13](https://github.com/uvelan/TradeForge/pull/13) started at `09a0040612ea852bb4bb1fa519b11cb3fe1f7c1e`. Local HEAD and the fetched development branch agreed. The core implementation is `19b0c166f08fe15e1121e7f81c109fc894a20ff3`. The final source checkpoint, including Data Library and test synchronization changes, is `e686e10d6d4a0d216da657cc348d4fb0ef9c83ea`. This report and its evidence supplement those commits.

## Reproductions and corrected contract

The native M5 and derived M5 tests use a nominal close of 11:05:00 UTC, provider response at 11:05:01, completed observation write at the modeled 11:05:02 boundary, and a competing SQLite writer at receipt acquisition. In the fixture, the receipt can proceed at 11:05:08 and completes at the modeled 11:05:09 boundary. Against the reviewed code, both records were retrospectively visible at 11:05:05. After the fix an intermediate freeze finds no eligible row, and a later frozen archive rewound to 11:05:05 still hides the row. The frozen record becomes available at 11:05:09. The same baseline reread manifest A after corrected manifest B and inserted a third M5 observation. The corrected reader preserves the original high of 13, the corrected high of 21, and two published observations. [Baseline failures](evidence/TF-I08D-integrity/baseline-defects.txt) · [Adversarial tests](../../research_backend/tests/test_arrival_integrity.py)

`SERVER_RECORDED_ARRIVAL_V2` separates provider event time, local server observation, durable observation commit, durable receipt commit, and strict visibility. The observation row retains the raw provider epoch and the UTC candle open separately. Server observation time comes from the local guarded wall clock. SQLite writes the observation before preparing the receipt. The receipt's `persisted_ms` is a transaction preparation timestamp. The append-only `arrival_publications` row records a clock sample after the receipt transaction commits. Only a committed witness can enter a V2 manifest. `receipt_committed_by_ms` bounds the earlier receipt commit and does not timestamp the witness row's own commit. This finite publication contract is `POST_COMMIT_RECEIPT_WITNESS_V1`. It rounds upward to the next millisecond and then to analytical UTC seconds. The witness row's own commit controls when readers can use the proof. It does not assign an earlier time when the receipt was not yet durable.

Every SQLite connection requests `synchronous=FULL`. Write transactions use `BEGIN IMMEDIATE` and validate a fresh clock and the exact lease token after lock acquisition. A live writer checks again before committing observations and witnesses. A replacement obtains a fresh token, and release deletes only the old token. Stop sets the worker flag and releases its lease through the same SQLite serialization boundary. A blocked native call can return after stop or takeover without gaining authority to write. A crash before the receipt leaves an observation draft. A crash after the receipt but before its witness leaves an unwitnessed receipt. A later worker must observe and witness again with its own valid lease and current clock. Transaction commit and lease takeover serialize on the writer lock. The witness time bounds the receipt commit under the local clock policy and does not provide independent external UTC assurance.

The calendar and sessions of both the original writer and the recovery witness are covered by the sealed manifest attestation. V2 uses manifest schema 2 and strict mode version 2. The local monotonic/jump guard remains `LOCAL_UTC_MONOTONIC_GUARD_V1` and is sampled at additional transaction boundaries. V1 manifests remain addressable by their original ID and keep their original bytes, rows and classification. Strict lookup now returns `LEGACY_RECEIPT_HAS_NO_DURABLE_PUBLICATION_PROOF` for those manifests. A V1 receipt never becomes a V2 witness. Simulation keeps its existing modeled timing and authority.

## Aggregate lineage

`RECORDED_M1_TO_M5_V2` reads the exact observations selected by frozen admission. Each output records the original source manifest, source cutoff, source feed, attestation, and the five ordered constituent candle opens, ledger sequences, available times and fingerprints. The algorithm and feed scope identify a stable M5 candle separately from its immutable output observation sequence and its witness publication time.

| Input at one M5 boundary | Result |
| --- | --- |
| Same five observations, including through a manifest with irrelevant changes | Reuse the existing version and proof |
| All five sequence coordinates are at least as new, with one or more newer | Append a version, even if the aggregate OHLC is identical |
| An older coordinate, including a mix of older and newer coordinates | Reject the whole request |
| A missing minute or an incomplete calendar interval | Leave the M5 slot absent |
| A newer request supersedes an unwitnessed draft | The old request cannot later witness that draft |

The comparison and observation append share a SQLite writer transaction. Publication checks draft supersession again. Sealing pins each selected output slot to its chosen sequence, including eligible earlier versions for that slot. Concurrent writes elsewhere in the range cannot add slots or replace that choice. A rejected stale aggregate may leave unwitnessed operational draft/receipt rows after interruption. Frozen reads and strict readiness exclude them.

## MT5 diagnostic and production disposition

The read-only Python run used MetaTrader5 5.0.6231 and connected terminal build 6320. Automatic discovery initially returned IPC error `-10003`. Passing the installed executable path connected. The six samples per symbol showed five distinct EURUSD ticks and six distinct GBPUSD ticks. Raw tick epochs rendered as UTC remained approximately 7,205 seconds ahead of host UTC while those ticks changed. For example, EURUSD epoch `1795062602133` renders as `2026-11-19T04:30:02.133Z`, while the response was received at `2026-11-19T02:29:57.469447Z`. The local host reported India Standard Time and Windows Time reported stratum 0. A later independent NTP stripchart returned three host-to-reference offsets from +6.479 to +6.483 seconds. The wall-clock guard detects discontinuities rather than absolute UTC error. Restore verified host synchronization before enabling any recorded provider. [Independent host clock output](evidence/TF-I08D-integrity/independent-host-clock.txt) The log includes raw M1 and M5 position results, both UTC-aware range queries and per-call response measurements. These observations establish the discrepancy survives tick refresh and the Python process sees it before chart rendering. They do not establish a trustworthy UTC epoch, clock synchronization, or a broker offset schedule. [Connected diagnostic](evidence/TF-I08D-integrity/mt5-times-explicit.json) · [Initial IPC attempt](evidence/TF-I08D-integrity/mt5-times.json)

MetaQuotes documents UTC for Python range input and returned bar times. MQL5 `TimeCurrent` is a last-quote server time, `TimeTradeServer` is a client-side estimate, and `TimeGMT` is derived from local OS settings. Each is evidence with different limits. The included read-only MQL5 script prints clock and bar epochs for comparison. Its terminal-side run was not executed in this session. To collect it, compile [DiagnoseMt5Time.mq5](../../scripts/DiagnoseMt5Time.mq5) in MetaEditor, run it on EURUSD and GBPUSD charts while the Python diagnostic is sampling, and retain only lines beginning with `TradeForge`. Check host UTC independently before interpreting any differences. [Python Copy Rates Range](https://www.mql5.com/en/docs/python_metatrader5/mt5copyratesrange_py) · [TimeCurrent](https://www.mql5.com/en/docs/dateandtime/timecurrent) · [TimeTradeServer](https://www.mql5.com/en/docs/dateandtime/timetradeserver) · [TimeGMT](https://www.mql5.com/en/docs/dateandtime/timegmt)

Production adapter policy is `UNVERIFIED_TIMESTAMP_DOMAIN`. The writer accepts identity conversion only from a server-owned provider adapter that explicitly attests `UTC_EPOCH_VERIFIED_V1` under its feed scope. Known-good test adapters use identity conversion and preserve both values. There is no broker wall-clock conversion, constant offset, DST mapping or request-controlled override. Positive and negative offsets, repeated local times, DST transitions and unknown historical offsets have no verified conversion path. The ten-M1-per-instrument live acceptance and post-capture replay were not executed because their timestamp precondition failed. The real read-only adapter separately rejected all four EURUSD and GBPUSD M1/M5 scopes with zero observations, witnesses or remaining leases. Production recorded readiness is **BLOCKED**. [Live capture gate](evidence/TF-I08D-integrity/mt5-live-gate.json)

## Historical archive onboarding

The configured production workspace has **0 dataset rows and 0 sealed manifests**, measured with a read-only catalog query. Historical simulation passes deterministic offline coverage, purpose separation, modeled delays and SMT reproducibility checks. Production EURUSD/GBPUSD history and CRT/SMT against a real imported archive remain unverified. [Workspace inventory](evidence/TF-I08D-integrity/inventory.txt)

1. Obtain a matching completed UTC trading day for EURUSD and GBPUSD. Dukascopy documents a free web CSV export and a distinct official S3 BI5 source that requires configured AWS credentials and uses Requester Pays. Its website has proprietary-rights and use terms. Use only permitted data, and confirm billing approval before any requester-pays access. This run downloaded no archives and incurred no such charge. [Free export](https://www.dukascopy.com/swiss/english/marketwatch/historical/) · [BI5 access](https://wiki.dukascopy.com/en/development/data-export/) · [Terms](https://www.dukascopy.com/swiss/english/legal-pages/terms-of-use/)
2. For a provider-approved local BI5 sample, keep its original object identity, UTC day or hour, and SHA-256. Start the local app, then import each instrument with the existing endpoint. For example, use the following command for a daily archive, replacing the local path with the acquired file. For supported hourly archives, also pass the matching `hour` query parameter.

   ```powershell
   curl.exe --fail-with-body -X POST -F "file=@EURUSD.bi5" "http://127.0.0.1:8000/research/api/market/import/bi5?symbol=EURUSD&day=2026-11-16"
   ```

3. This importer produces bid/ask tick data and BID M5 candles. It does not materialize M1 or ingest the web CSV. Use a supported, provider-licensed M1 source for the M1 lane. Keep the M5 pair on the same feed, price basis and timeframe. In Data Library, select Historical simulation, use the common UTC interval and forex calendar, and enter explicit modeled delays per feed. Keep the returned checksum manifest, readiness result and SMT response. For CRT, use the existing `POST /api/indicators/evaluate` with `availability_mode=HISTORICAL_SIMULATION`, `frozen=true`, the same frozen partition/checksum IDs and cutoff, and the `candle_range_theory` indicator. Repeat identical inputs to compare result IDs and evidence. Report actual missing slots rather than filling them.

## Rollback and known limits

Stop any capture session and preserve a backup of SQLite and the market directory. Revert the implementation and associated report commits on the PR branch through ordinary Git reverts. Keep the additive tables and immutable V2 artifacts for audit. V1 code cannot attest V2 and should not be used to reauthorize old V1 evidence for strict strategy consumption. Keep strict recorded use disabled if reverting before the V2 fix. No broker calibration, licensed archive onboarding, live SMT publication or venue execution validation was completed here.



## Verification history

The verification environment used Python 3.13.14, Node 24.14.0 and npm 11.1.0 on Windows. The [commands](evidence/TF-I08D-integrity/commands.md) reproduce the execution lanes. The earlier full backend check of the initial implementation passed 457 tests. Later adversarial checks include frozen rewind and sub-millisecond rounding.

The original reproduction harness first needed lease heartbeats for the five-minute candle. The reproduced failures reported above use the corrected harness against the reviewed implementation. During correction, the legacy fixture initially omitted the required partition reference, and the new frozen rewind assertions initially omitted the explicit availability mode. Those were test input errors. [Frozen-request fixture attempt](evidence/TF-I08D-integrity/adversarial-fixture-attempt.txt)

The first full Chromium attempt passed 65 of 66. The existing availability test still looked for the former receipt label. The fixture now checks the actual post-commit label, recorded and blocked states, source lineage, and stale aggregate rejection. The second attempt passed 65 of 66 and timed out on a valid long-position resize. Both current attempts passed the previously reported rectangle test. The prior rectangle trace reported zero rendered handles when four were expected. The long-position trace showed no save request and reused coordinates across three gestures. Its screenshot shows a different viewport at failure. The test helper now returns the same rendered sample that passed the handle-count check, and the risk/reward test remeasures before each gesture. The previous rectangle timeout did not recur, so its earlier cause remains unconfirmed. Drawing production code, expectations, retry policy and timeouts were left intact. [First browser run](evidence/TF-I08D-integrity/playwright-attempt1.txt) · [Second browser run](evidence/TF-I08D-integrity/playwright-attempt2.txt) · [Rectangle handles](evidence/TF-I08D-integrity/rectangle-handles.png) · [Long-position failure](evidence/TF-I08D-integrity/long-position-failure.png)

The capture latency runner initially lacked fixture-only UTC attestation, and the production gate rejected those injected ticks. The benchmark now supplies the explicit fixture attestation. [Benchmark fixture failure](evidence/TF-I08D-integrity/benchmark-capture-attempt1.txt)



## Measured performance

The two archived benchmark runners use isolated SQLite and Parquet, identical workloads from the reviewed TF-I08D baseline, and 30 warmed serial samples per representative lane. API times include archive verification, calculations and serialization. The peak Python allocation is measured in a separate tracing run. Native working-set peaks are cumulative for the process. Results below are P50/P95 milliseconds unless noted. [Reviewed benchmark](TF-I08D-REVIEW.md#performance) · [Current analytical benchmark](evidence/TF-I08D-integrity/benchmark.txt) · [Current capture and aggregate benchmark](evidence/TF-I08D-integrity/benchmark-capture-extra.txt)

| Dual-feed SMT lane | Previous API | Current calculation | Current API | Python peak before → after MiB |
| --- | ---: | ---: | ---: | ---: |
| 2,000 dense recorded | 417.9 / 493.5 | 407.7 / 491.5 | 422.3 / 489.6 | 20.3 → 22.7 |
| 2,000 dense simulation | 396.9 / 485.1 | 380.9 / 455.7 | 395.8 / 479.7 | 17.9 → 17.8 |
| 10,000 sparse recorded | 694.4 / 735.0 | 720.7 / 765.6 | 726.9 / 819.2 | 50.3 → 62.6 |
| 10,000 sparse simulation | 621.1 / 658.0 | 623.4 / 665.7 | 635.8 / 674.5 | 38.3 → 38.4 |
| 10,000 dense recorded | 2117.7 / 2226.2 | 2150.6 / 2261.6 | 2169.2 / 2323.5 | 101.3 → 113.5 |
| 10,000 dense simulation | 2057.8 / 2176.7 | 2076.5 / 2652.7 | 2085.8 / 2627.9 | 89.3 → 89.3 |

Dense recorded SMT verified 10 checksums, decoded 20,000 records, made 9,990 registry publications, and returned 167,277 bytes. Native high water moved from 338,092 to 358,128 KiB. The decoded records now include receipt witnesses and timestamp provenance. Sparse and dense recorded allocation peaks increased. Dense simulated P95 also increased in this run. These measurements do not establish a cause for the simulation change.

| Lane | Previous | Current |
| --- | ---: | ---: |
| Two-symbol persistence | 31.6 / 39.1 | 41.0 / 44.1 |
| Duplicate two-symbol poll | 9.7 / 13.8 | 7.3 / 9.0 |
| 5 ms injected per capture transaction, two-symbol poll | 51.6 / 58.9 | 80.1 / 103.3 |
| Correction lookup pair | 20.9 / 25.9 | 20.0 / 22.8 |
| Original M5 from five verified M1 observations | — | 67.3 / 76.5 |
| Corrected M5 from five verified M1 observations | — | 67.4 / 106.2 |
| Repeated identical M5 aggregate | — | 37.8 / 44.5 |
| Rejected stale source | — | 12.3 / 13.5 |

The injected-delay run measured observation, receipt and witness transactions individually at 12.7 / 16.7, 11.9 / 19.5 and 11.9 / 17.5 ms. These timings include the injected delay and SQLite commit. The deterministic contention test measures ordering through a competing writer. The delayed benchmark is an injected slowdown and does not represent a measured disk- or broker-specific contention scenario. The restart duplicate poll took 18.4 ms in one measurement. The writer recorded 66 unique observations with zero queued work. Sealing 33 observations took 108.2 ms in one measurement. The reviewed benchmark did not time M5 publication or stale rejection separately.




## Final source verification

| Check | Result |
| --- | --- |
| Full Python suite | 462 passed, including 14 new adversarial cases |
| Focused arrival, strict timing, strategy evidence and SMT | 203 passed |
| Legacy indicators | All 22 approved golden fingerprints unchanged |
| SMT journals, alerts and registry isolation | Passed in the full suite |
| Compile checks | Passed |
| Vitest | 8 passed |
| Production TypeScript and Vite build | Passed |
| Chromium, full, zero retries | 66 passed |
| Rectangle and long-position drawing, four repetitions each | 8 passed |
| Drawing/replay checkpoint, zero retries | 20 consecutive passes |
| Delayed-analysis drawing stress, zero retries | 1 passed |

The production build emitted third-party module directive warnings. The final browser run was clean. The rectangle and long-position replays finished with the full persistence assertions. Both the earlier runs and the final source reruns passed 20/20 checkpoint repetitions and 1/1 delayed-analysis stress case. The first inventory invocation omitted the repository import path and failed before it could query the configured DB. The recorded inventory uses the corrected read-only command.

[Full backend](evidence/TF-I08D-integrity/backend.txt) · [Focused suite](evidence/TF-I08D-integrity/strict-and-legacy.txt) · [Compile](evidence/TF-I08D-integrity/compile.txt) · [Vitest](evidence/TF-I08D-integrity/vitest.txt) · [Build](evidence/TF-I08D-integrity/build.txt) · [Full Chromium](evidence/TF-I08D-integrity/playwright.txt) · [Drawing repetitions](evidence/TF-I08D-integrity/drawing-repetitions.txt) · [Checkpoint repetitions](evidence/TF-I08D-integrity/checkpoints-final.txt) · [Delayed-analysis stress](evidence/TF-I08D-integrity/drawing-stress-final.txt)

The screenshots show controlled Playwright responses. The [recorded readiness view](evidence/TF-I08D-integrity/arrival-readiness.png) and [aggregate lineage view](evidence/TF-I08D-integrity/aggregate-lineage.png) are fixture evidence. The [blocked capture view](evidence/TF-I08D-integrity/arrival-blocked.png) shows how the UI presents an unresolved timestamp domain.

GitHub ran both the [push workflow](https://github.com/uvelan/TradeForge/actions/runs/44447024281) and [pull request workflow](https://github.com/uvelan/TradeForge/actions/runs/44447029786) on source checkpoint `e686e10d6d4a0d216da657cc348d4fb0ef9c83ea`. Both passed on attempt 1. The [captured Actions metadata](evidence/TF-I08D-integrity/source-ci.json) includes the exact source SHA for both events. The evidence-only handoff commit receives its own workflows on the [PR checks page](https://github.com/uvelan/TradeForge/pull/13/checks).
