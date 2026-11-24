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

## PR #34 partition-layout correction

At reviewed head `25778352e268c8686105266a6d077e830f31412d`, the replay audited the requested period but selected partitions only between the first and last observed candles. A Friday 22:01 UTC quote after the 22:00 close, or a Sunday 21:59 quote before the 22:00 reopening, could therefore disappear when split into its own Parquet file. A second defect checked provenance only when file IDs differed. Opposite target and stop quotes observed in the same millisecond could acquire an apparent order solely by being packed into one file.

| Evidence | Reviewed GET / saved POST | Corrected GET / saved POST |
|---|---|---|
| Trailing or leading closure contradiction | One file 422/422; split 200/201 | 422/422 in every layout, zero saved runs |
| Different source fingerprints, opposite prices, distinct ordinals at one millisecond | One file 200/201; split 422/422 | 422/422 in every layout, same ambiguity diagnostic, zero saved runs |
| Different source fingerprints reusing an ordinal | 422/422 with layout-dependent diagnostics | 422/422 with the same ambiguity diagnostic |
| Clean weekend or quotes exactly at exclusive end | Admitted clean controls | 200/201; complete trades, timestamps, coverage and metrics match across layouts, including an exact boundary duplicate in the shards |

Selection, observation filtering and the calendar audit now share the effective half-open period. The period starts at the first returned candle only for a truncated chart range. File integrity and input budgets continue to cover complete selected files. Ordering policy `UTC_MS_SOURCE_ORDINAL_V2` checks source identity before comparing ordinals within each millisecond. Unpinned legacy ordinals retain file-local scope. Conflicting explicit sources cannot acquire a common order even in one file. The saved quote route reapplies the recorded period when available.

The pinned November 13, 2025 benchmark repeated the same 8,636 quotes and two input hashes. All 17 trades, coverage, timestamps and aggregate metrics matched. Only the execution-model ordering label changed in the response. One traced fresh-process measurement took 2.071 s versus 1.982 s (+0.089 s). Peak Python allocation rose by 10 bytes to 35,617,386 bytes; peak process memory rose by 16,384 bytes to 154,791,936 bytes. These are individual observations. [Measurement and compared fields](evidence/t06-layout-benchmark.json)

Reproduction adds `research_backend/tests/test_t06_layout.py` to the focused pytest command above. The expanded suite passed 47 tests, and the six layout regressions passed again after adding an out-of-window tie and duplicated shard boundary. For rollback to a released version use the PR #33 merge and single-ID workflow described above. Reverting only this correction restores both reviewed layout defects.

## Mixed calendar gaps and malformed tick priorities

Reproduced on `932ebc5ea94f0cfcc62c9e823f18e24ff5fd0bc9`. Both public endpoints placed the entire mixed gap in MISSING_OPEN. The corrected GET (200), saved POST (201), and subsequent saved-run GET (200) agree on these half-open UTC intervals. The replay uses the persisted `first_unverified_candle_s` as its missing-candle frontier.

| Fixture | Stored intervals in chronological order | Frontier (UTC) |
|---|---|---|
| Winter reopening one minute late | KNOWN_CLOSED [Jan 10 22:00, Jan 12 22:00); MISSING_OPEN [Jan 12 22:00, 22:01) | Jan 12 22:00, 2025 |
| Spring DST reopening one minute late | KNOWN_CLOSED [Mar 7 22:00, Mar 9 21:00); MISSING_OPEN [Mar 9 21:00, 21:01) | Mar 9 21:00, 2025 |
| Last Friday minute missing | MISSING_OPEN [Jan 10 21:59, 22:00); KNOWN_CLOSED [Jan 10 22:00, Jan 12 22:00) | Jan 10 21:59, 2025 |

Each fixture kept one pre-gap target exit at 2.5R and suppressed expectancy, expectancy bounds, win rate and total R. The later setup produced no fill.

Genuine Parquet with an integer and a NULL ordinal at the same millisecond returned 500/500 for GET/POST when packed, versus 422/422 when split. The same happened with a NULL millisecond. Validation now runs before sorting each second. The single-ID shorthand, packed range, split range and reversed split range all return 422/422 with only `Invalid tick timestamp or source order`; zero saved rows. Valid same-source order and duplicate controls continue to pass.

The focused command above, including `research_backend/tests/test_t06_layout.py`, passed 52 tests. The November 13, 2025 benchmark used the same pinned inputs and reproduced all 17 trades across 8,636 quotes. The only response difference was the new null missing-candle frontier. Compared with the reviewed revision, elapsed time was 2.054 vs 2.071 s, Python peak allocation 35,616,953 vs 35,617,386 bytes, and peak process memory 154,976,256 vs 154,791,936 bytes. Each is a single fresh-process observation. No schema migration is required; the released rollback remains the PR #33 merge and the single-ID workflow described above.

### Ordinal validation at the replay boundaries

The reader verifies the complete selected partition and validates each timestamp against its recorded second. Only quotes in the half-open replay interval need a usable source ordinal. The Parquet regression exercises a single dataset ID, one-file range, split range and reversed split selection through both public endpoints.

| NULL ordinal location | Backtest GET | Saved-run POST | Observed result in all four layouts |
| --- | --- | --- | --- |
| One millisecond before start | 200 | 201 | Matches clean trades, metrics and coverage |
| Exactly at start | 422 | 422 | Invalid tick timestamp or source order, zero saved runs |
| One millisecond before end | 422 | 422 | Invalid tick timestamp or source order, zero saved runs |
| Exactly at end | 200 | 201 | Matches clean trades, metrics and coverage |

At reviewed head `01987663`, the new matrix failed both outside cases and passed both inside cases. After correction the five focused test modules passed all 56 cases, including the calendar frontier and conflicting-origin controls. Reproduce the matrix with `python -m pytest -q research_backend/tests/test_t06_layout.py -k ordinal_boundary`.

The pinned November 13 benchmark (`python -m scripts.benchmark_t06 --source storage/t06-input`) reproduced the previous input fingerprints and complete replay result across 8,636 quotes and 17 trades. Single measured elapsed times were 2.054 s previously and 2.006 s here. This run peaked at 35,614,866 bytes of traced Python allocations and 153,559,040 bytes process memory.
