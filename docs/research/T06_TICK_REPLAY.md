# T06 — deterministic multi-partition tick replay

PR #33 merged at `69359c205de9b1dadc733729b8e98d1301b5b8c2`, which contains its approved head `6f19a6463237348d3de0f91d0f0550fe2bd1c1a3`. This change uses the existing backtest GET and saved-run POST, the canonical publication owner, and the verified partition reader.

| Endpoint or reader probe | Merged baseline | T06 |
|---|---|---|
| Single sequence vs split at the same millisecond, overlapping one identical source event | Same trade; no replay coverage manifest | Exactly five ordered quotes either way, identical coverage and decisions. BUY at 180000 ms, ASK 101; target at 181000 ms, BID 106, before the adverse quote at the same millisecond. |
| Contradictory row at identical source priority | Rejected | HTTP 422; both reader selection orders reject. Missing or different source fingerprints at a cross-file millisecond also reject. |
| Winter weekend, March DST weekend, flat at both session boundaries | Aggregate suppressed (winter and spring reproduced before implementation) | Two completed simulated trades, 2.5R mean in the hand-built fixture. Spring closure spans 47 hours; autumn spans 49. |
| Friday entry still open at spring closure; favorable first Sunday quote | Existing engine split observed candle sessions | Zero completed trades, `CANDLE_SESSION_BOUNDARY`, null expectancy. |
| Unexplained one-minute Friday hole, followed by another setup | Both trades emitted; aggregate suppressed | One verified pre-gap trade. Evaluation stops at the first missing open minute; no later entry or aggregate. |
| Break during an open trade between sparse quotes | Existing coverage guard | `INTRADAY_QUOTE_GAP`, null aggregate; later price cannot close that trade. |

The source-order regression uses the same checksum-identified input sequence and the same candles for both layouts. A complete partition and two overlapping shards have separate dataset IDs. The test compares each pinned shard's ordered rows against the original, then compares the endpoint's decisions, full trade records, unresolved outcomes, aggregate and coverage. The saved-run quote-path regression checks the last included ordinal. The single-ID request continues to emit the original `lineage.tick_partition` shorthand.

## Ordering, coverage and limits

Execution model 3 merges verified streams by UTC millisecond and source ordinal. Fixed-size Arrow batches decode the same private bytes that passed SHA-256. The reader accounts for the legacy writer's ordering by whole second. Cross-file ties require a common source fingerprint, from the registered import or a checksum-bound row's explicit `source_sha256`. An ordinal is local to its source file. Complete matching rows at the same priority count once; distinct ordinals in one source remain distinct observations. Multi-file comparison retains all columns until duplicate verification, including volumes. The single-file execution path projects quote fields. The replay materializes at most 500,000 input rows from at most 50 tick files; the cap counts duplicates before merging. This is a bounded in-memory replay rather than an unlimited stream.

The calendar policy `DUKASCOPY_FX_NY17_V1` applies to EURUSD and GBPUSD on the Dukascopy BI5 feed. It uses the existing America/New_York schedule from Sunday 17:00 through Friday 17:00. Contradictory observed candles or quotes in scheduled closures reject the request. Other feeds and instruments, including metal daily breaks, use unknown-calendar handling for gaps. Session-specific holiday exceptions remain unverified. [Dukascopy trading hours](https://www.dukascopy.com/swiss/english/forex/forex-trading-accounts/link/) and [session and DST conventions](https://wiki.dukascopy.com/en/manuals/jforex4-desktop/trading-sessions-logic/).

The result records the required half-open candle interval, observed first and last quote, count, classified gaps and requested/effective quote lag. Execution retains the reviewed conservative rule: at most one bar and at most 60 seconds between observed executable quotes; a caller may tighten the threshold. This administrative ceiling is not an empirical guarantee of quote completeness. Open-session candle holes and unresolved trade windows withhold aggregate metrics. Execution prices are historical bid/ask observations with configured costs; actual liquidity, missing ticks below the ceiling and holidays without authoritative session evidence remain unverified.

## Recorded benchmark

The [machine-readable result](evidence/t06-eurusd-bi5.json) includes full source and partition hashes, IDs, source intervals, timestamps, fills, and policy. Two public provider files were imported through the BI5 importer into a temporary database. The measured operation was one GET in a fresh process on Windows 11, Python 3.13.14, with allocation tracing enabled. The filesystem could be warm from import; the process peak includes interpreter and API initialization. Preparation and network time are excluded.

| EURUSD, 13 November 2025, 13:00–15:00 UTC | Measurement |
|---|---:|
| Input BI5 / tick Parquet | 40,141 / 100,293 bytes |
| Tick partitions / quotes | 2 / 8,636 |
| Elapsed / peak Python allocations / process peak RSS | 1.982 s / 35,617,376 bytes / 154,775,552 bytes |
| Completed simulated trades / unresolved | 17 / 0 |

The provider data was fetched from `https://datafeed.dukascopy.com/datafeed/EURUSD/2025/10/13/{13,14}h_ticks.bi5` (zero-based provider month). Raw files remain outside Git. This is one measured window, not a capacity or performance distribution at the input ceiling.

```powershell
.venv/Scripts/python.exe -m pytest -q research_backend/tests/test_t06_replay.py research_backend/tests/test_tick_replay.py research_backend/tests/test_historical_import.py research_backend/tests/test_workspace_tools.py --tb=short
.venv/Scripts/python.exe -m scripts.benchmark_t06 --source storage/t06-input
.venv/Scripts/python.exe -m pytest -q
```

The focused run passed 41 tests; all ten T06 cases also passed after adding a reversed-conflict assertion. The baseline passed 14 existing focused probes. Four initial new probes exposed the missing manifest, two suppressed weekend aggregates and the post-gap trade.

## Rollback

Restore the PR #33 merge revision for new requests and use `tick_dataset_id` for the previous version-2 single-file workflow. Retain all SQLite records and archives, including the execution model recorded in each immutable saved run. Historical multi-file paths without common-source evidence at a tied millisecond may now return an integrity error; those archives need independent provenance to establish tie order. No database migration, reader cutover, new broker route or writer retirement is involved. Public admission is capped at 160 cutoffs. Production recorded trading remains BLOCKED.
