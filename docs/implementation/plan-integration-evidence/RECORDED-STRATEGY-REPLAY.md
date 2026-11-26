# Windows local strategy-to-exit replay

PR #37, starting at `4942d2b103fa7e8efe7914d44ce123ad097e8887`. [Measured receipts, strategy version, decision and intent IDs, quote references, hashes and timings](RECORDED-STRATEGY-RESULTS.json). The natural run uses the [M5 FVG strategy definition](REPLAY-FVG-DEFINITION.json). The positive execution controls qualify intents using a controlled strategy fixture, then execute against genuine Dukascopy observations.

## Use the workflow

1. In Data Library register the two reviewed EURUSD BI5 hours for November 13, 2025. Open Strategy Lab, select a ready historical strategy version and **Verified workspace partitions**. Select its candle IDs, warm-up, evaluation start, and evaluation end. Queue the evaluation. Existing validation and worker budgets apply.
2. On the completed run, open **Recorded quote replay**. Set **Replay through (UTC)** and select **Save replay checkpoint**. Close the application if desired. After restart select the same saved historical run, choose a later cutoff and select **Resume recorded replay**. **Decision, quote and partition evidence** shows quantities, pricing sides, source ordinals, partition hashes and exact proof references. **Inspect historical decision** opens the qualification trace.
3. To reproduce the natural case in a temporary workspace with the original files already in `storage/t06-input`, run:

```powershell
.venv/Scripts/python.exe -m scripts.verify_recorded_backtest
```

For scripting against the running local API, use `scripts.recorded_backtest --run RUN_ID` or `--recipe HISTORICAL_REQUEST.json`, with `--start 2025-11-13T13:00:00Z --end 2025-11-13T15:00:00Z`. An optional `--through` sets the checkpoint time. Supply the returned ID with `--resume` and the same full window to advance. Historical qualification and position execution have separate immutable identities. The HTTP routes are GET and POST under `/api/historical-strategy-runs/v1/{run_id}/recorded-replay`.

## Input evidence

The local source inventory found two EURUSD BI5 files and no further registered tick partitions in the operational research catalog. Separate 2026 M5 BID candles lack executable bid/ask observations for this test. The two original files, feed `dukascopy-bi5-v2`, were checksum-verified before decoding. The recorded BID and ASK are integer points at scale 100,000. The UTC window was [13:00,15:00) on November 13, 2025.

| Hour UTC | Source SHA-256 | Tick Parquet SHA-256 | Quotes |
| --- | --- | --- | ---: |
| 13 | `2f47bcb14e0a2c4dcd6d65eb819b85f1b5168b656d1f5f67c3dcd2240569f014` | `ca71608096bb6ec870c2a621d761910f0d9a45d7d9ce7fe9e99569ef983e6276` | 3,171 |
| 14 | `9d40aa3147d93e6daa2218933877adb9471dc02ff08b19fc0d272f1eb781688f` | `6944703b23da14f4727177baab237abc04c36cb502a2f95db97e4493dd11e5c8` | 5,465 |

The observations ran from 13:00:00.089 through 14:59:59.825 UTC. The largest within-hour separations were 36.433 and 19.623 seconds. The hour boundary separated consecutive observations by 5.632 seconds. The two unverified intervals in the requested window were [13:00:00.000,13:00:00.089) and [14:59:59.826,15:00:00.000). No interior interval exceeded 60 seconds. Bracketing and the NY 17:00 calendar are research policies. Provider delivery is modeled at zero delay. Spread comes from the observed sides; the simulator adds no second spread charge. Entry requires zero modeled slippage. Quantities are exact fractions of initial exposure before fees. No account P&L or liquidity guarantee is produced.

The coverage lookup originally raised SQLite `OperationalError: malformed JSON` when an unrelated valid-JSON manifest contained a scalar where a candle pin would be expected. The coverage regression now requires an unchanged successful response after that insertion and HTTP 409 with `CONFLICTING_IMPORT_CLAIM` and the dataset ID after a second claim on the selected dataset.

## Decision and restart evidence

| Run | Expected | Observed |
| --- | --- | --- |
| Natural FVG, 13:00 warm-up, 14:00–15:00 evaluation | Use only genuine M5 BID candle evidence and published intent proofs | One decision, `INVALIDATED / EXPLICIT_INVALIDATION` at 15:00. Zero intents, orders, fills and exits. The local browser queued the strategy, saved a 14:30 checkpoint, reopened it and resumed through 15:00. |
| Controlled LONG MARKET, original tick partition | Qualification 13:55:20.000; ASK 1.16174 at 13:55:21.403; reduce 1/4 on BID 1.16176 at 13:55:59.647; close 3/4 at first elapsed-time quote | Entry ordinal 2971, partial ordinal 2982, final ordinal 3004 at 13:56:21.606 on BID 1.16183. Break-even and trailing activation evidence retained. Closed 1, remaining 0. |
| Same controlled intent, overlapping shards, new API process | Same economic events, IDs, quantities, decision, exact proof and quote references | The first process committed with 3/4 open and exited with code 75. A fresh process resumed using reversed shard selection. All compared fields matched the uninterrupted result; retry kept three checkpoint rows total across both layouts. A shard selection omitting observations was rejected. |
| Controlled LONG LIMIT and STOP, 14:00 source file | First eligible ask at the limit or through the stop | LIMIT at ASK 1.16274 at 14:55:37.210; STOP at ASK 1.16276 at 14:55:20.449. Each receipt identified the source file and winning ordinal. |
| Controlled missing evidence | No fill across an unverified exit interval | A 60.001-second open-session gap before a stop-crossing bid returned `INSUFFICIENT_QUOTE_COVERAGE` with zero exits and the last verified quantity. This gap control uses generated quotes. |

The disposable benchmark saves a new strategy revision each time. Use the same published run and pins when comparing IDs. The original-partition and shard controls share a historical decision; the execution stage is the part independently replayed across layouts.

Each replay compares the entire selected tick sequence against freshly decoded original bytes. Original observations are ordered by event millisecond and provider ordinal. Conflicting or incomplete partition selections are rejected. A horizon is inclusive for decisions and observed quotes within the half-open archive window. The historical worker can write a qualified decision at a later cutoff than the proven trigger; the replay reports the receipt cutoff and the trigger availability separately. It verifies each exact intent proof was available by the trigger.

A checkpoint transaction persists the exact recorded inputs, layout, cutoff, decisions, entries, management events, source references and known exposure. On resume the service re-verifies the inputs, reuses certain completed exits and reconstructs any open position from its immutable entry prefix. This is a replay-based recovery design; it does not claim incremental runtime for still-open positions. A layout switch has its own checkpoint identity. The fee, tick, calendar and data limitations of a replay remain visible after restart.

## Measurement and remaining scope

The complete two-hour strategy workflow, from enqueue through returning the final projection, took **1.716 s** on local Windows after importing source data. API process peak was **147,144,704 bytes**; the spawned qualification worker peaked at **83,787,776 bytes**. These are separate per-process high-water marks. The API made two observed Parquet read opens and read two retained BI5 blobs; the worker made two observed Parquet read opens. These counts describe application reads, not physical disk I/O. The existing T06 GET benchmark on identical source hours took **2.053 s**, with **155,598,848 bytes** process peak, 35,680,513 bytes peak Python allocations and zero unresolved strategy outcomes in that separate benchmark.

**Month-scale acceptance remains open.** For calendar November 2025 the workspace lacks tick archives for November 1–12 and 14–30, plus the hours outside [13:00,15:00) on November 13. No market observation is inferred for those dates. Each public recorded request is bounded by 50 files and 500,000 source quotes. The public historical cutoff budget remains 160. Actual delivery latency, fill size, fees, paper accounts and broker routing are unverified. Production recorded trading remains BLOCKED.

## Regression commands and rollback

```powershell
$env:TRADEFORGE_T07_REAL_BI5=(Resolve-Path storage/t06-input/13h_ticks.bi5).Path
$env:TRADEFORGE_T07_LIMIT_BI5=(Resolve-Path storage/t06-input/14h_ticks.bi5).Path
.venv/Scripts/python.exe -m pytest -q research_backend/tests/test_recorded_session.py research_backend/tests/test_import_claim_lookup.py research_backend/tests/test_recorded_first_exit.py research_backend/tests/test_recorded_market_entry.py research_backend/tests/test_recorded_limit_entry.py research_backend/tests/test_recorded_stop_entry.py
.venv/Scripts/python.exe -m scripts.benchmark_t06 --source storage/t06-input
.venv/Scripts/python.exe -m scripts.verify_recorded_backtest
.venv/Scripts/python.exe -m pytest -q
npm --prefix frontend test
npm --prefix frontend run build
$env:TRADEFORGE_E2E_API_PORT='8553'
$env:TRADEFORGE_E2E_UI_PORT='4553'
npm --prefix frontend run e2e -- --retries=0
$env:TRADEFORGE_E2E_API_PORT='8554'
$env:TRADEFORGE_E2E_UI_PORT='4554'
npm --prefix frontend run e2e -- --config playwright.recorded.config.ts --retries=0
```

Local verification: 55 focused replay checks passed with both real price files selected. The genuine-data Chromium suite passed both tests in 2.4 minutes, including Strategy Lab and the 100-cycle chart gate. Standard Chromium passed 85/85 in 8.2 minutes with zero retries. All 13 frontend unit tests and the TypeScript/Vite production build passed.

The first full backend run (concurrent with other suites) had 1,061 passes, one skip and one lease-expiry failure in the scheduler equivalence harness. That case passed in isolation in 9.50 seconds. The harness drives a worker without the production supervisor; it now renews its live lease through the same fenced heartbeat API during checkpoint batches. On the final candidate the complete backend suite passed 1,062 tests with one Windows-inapplicable POSIX FIFO test skipped in 412.43 seconds. The test client emitted one deprecation warning.

Development found a qualified decision summary missing before its historical receipt cutoff. The regression compares the emitted position's decision ID to the visible causal summary. Legacy entry and exit tables retain their original schemas, policies and IDs. Revert the milestone commit to restore the previous UI and API. Keep the additive checkpoint table and source archives for audit.
