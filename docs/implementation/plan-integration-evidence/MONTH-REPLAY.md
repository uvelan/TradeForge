# Windows-local monthly quote replay

**November 2025 has two verified source hours. Full-month market acceptance is open.** The local catalog contains 3,171 EURUSD quotes for 13:00–14:00 UTC and 5,465 for 14:00–15:00 UTC on November 13. The first quote was observed at 13:00:00.089 and the last at 14:59:59.825. No other November date supplied verified executable quotes. The calendar classified 238 hours as scheduled closures and 480 open hours as missing. The [archive manifest](month-archive-manifest.json) pins the UTC bounds, calendar, feed, basis and source files. The [benchmark and recovery receipt](month-replay.json) records every missing open hour, the source and Parquet pins, sizes, exact execution references, checkpoint and measurements.

## Operate and reproduce

1. From the project root, run `.venv/Scripts/python.exe -m scripts.month_archive 2025-11 --source storage/t06-input`. The two control files were registered or reverified in the normal local catalog during acceptance. Other local inputs use `YYYY-MM-DD/HHh_ticks.bi5` beneath the source directory. For provider acquisition, add `--fetch`. Completed hours are committed independently. An identical import verifies the existing files and IDs. A conflicting import stops with an error. New provider bytes are pinned on intake; the control hours also have prior independent pins.
2. Open **Strategy Lab → Monthly EURUSD replay**, choose November 2025, and select **Inspect monthly coverage**. Inspect the registered hours and missing open intervals. **Run monthly strategy** verifies each contributing BI5 and Parquet sequence. **Pause after batch** stops further submissions. After restarting the application, inspect the same month and resume its saved session. If new imports changed the inventory, select the earlier pinned session to continue it.
3. Inspect the batch coverage and decision receipts. Expand a fill, exit or unresolved event and click **Verify source quote**. The service reads the pinned BI5 and Parquet again and resolves the original millisecond and source ordinal. An unresolved event points to its last known quote and first missing millisecond.
4. Reproduce the real-archive benchmark, independent BI5 comparisons, restart and Chromium operator journey in disposable workspaces:

```powershell
.venv/Scripts/python.exe -m scripts.verify_month_replay --browser --output storage/month-results.json
.venv/Scripts/python.exe -m scripts.verify_recorded_backtest
.venv/Scripts/python.exe -m scripts.verify_plan_paper --output storage/month-paper-control.json
$env:TRADEFORGE_T07_REAL_BI5=(Resolve-Path storage/t06-input/13h_ticks.bi5).Path
.venv/Scripts/python.exe -m pytest -q research_backend/tests/test_recorded_session.py research_backend/tests/test_month_replay.py
```

An isolated acquisition was stopped after the first committed hour. Import receipts / datasets / retained BI5 blobs were 1 / 5 / 1, then 2 / 10 / 2 after resume and 2 / 10 / 2 after retry. The original hour retained the same partition identity and checksums.

The provider fetch for November 2 at 22:00 UTC returned curl exit 28 connecting to `datafeed.dukascopy.com:443`. A separate probe for November 3 at 00:00 UTC also timed out, transferred zero bytes and received no HTTP response. Reuse the acquisition command when provider access becomes available. Preserve its input files and catalog between attempts.

## Strategy and coverage contract

The month runner has one named mechanical research rule. It qualifies once per UTC day on the first observed 10-point BID rise from the anchor. The anchor is the first eligible observation of the UTC day or a new continuous segment after a gap. BUY uses the first ASK strictly after qualification. Protective and target exits use the first executable BID at or beyond 100 points below or 200 points above that fill. The strategy uses one normalized unit, recorded spread, zero modeled delay, zero modeled fees and no added slippage. An information gap while awaiting an entry or managing a position freezes that opportunity as unresolved. Outcomes apply to the supplied observations only.

The existing Strategy Builder and its 160-cutoff admission policy retain their original contract. Arbitrary saved strategies do not acquire month-scale eligibility through this runner. Input support is hourly Dukascopy EURUSD BI5 with a registered tick partition. The calendar is the existing New York Sunday 17:00–Friday 17:00 weekly model. Holiday and venue-specific closures outside that model remain unverified. The 60-second continuity threshold is an explicit simulation assumption; source arrival and market liquidity were not measured.

Each transaction evaluates at most ten minutes of one verified hourly source. Adjacent empty or closed intervals are advanced in groups of at most 144 slots. The source reader retains its existing per-file bound, checksum checks and millisecond/source ordinal ordering. A ten-minute request rechecks the full hourly file, trading repeated reads for bounded memory. A checkpoint stores the day anchor, last observation and pending coverage frontier, spent day, position, source references, emitted events and gaps atomically. Serial SQLite writers and immutable batch identities make retries idempotent. Opening a second file or restarting does not reset strategy state. The session identity includes the month manifest and policy.

## Expected versus observed

| Case | Expected from the independent source or control | Observed |
| --- | --- | --- |
| November rule, observed data | Raw big-endian BI5 records first qualify at 13:00:00.666, ordinal 5. First eligible ASK is 1.16243 at 13:00:00.771, ordinal 6. First stop BID is 1.16143 at 13:31:38.103, ordinal 1564. | All three timestamps, ordinals and prices matched. One unit opened and closed. The second file produced no second daily attempt. |
| Open-position restart | Stop at the 13:10 UTC committed frontier, then continue the original decision and its source-bound fill. | A new Python process reproduced the entire uninterrupted batch ledger, including event IDs, coverage and proof references. Chromium reopened the session, resumed and resolved the exit quote from the archive. |
| Two-file boundary, controlled quotes | An open long in one file closes on BID 1.10230 in the next. Delaying the next file’s first observation past the coverage limit makes the open trade unresolved. | Target posted once in the contiguous case. The gap case retained the entry proof with zero exits. Retrying earlier batches preserved their receipts. |
| Approved M5 FVG control, genuine data | 8,636 observations; explicit invalidation at 15:00 UTC; zero qualified positions. | Original cutoff, status, reason, coverage intervals and empty position ledger matched the previous report. |
| Existing positive execution controls | Controlled qualifying intent on real quotes opens at ASK 1.16174, reduces 1/4, exits 3/4 on the time rule. Recorded ordinals 2971, 2982, 3004. | All assertions passed, including restart and original quote references. Separately, all three paper action requests, sides, source references, price, cash and remaining quantities matched the previous paper acceptance. |

Qualification in the first row is a mechanical outcome on available data. Missing earlier hours preclude an assertion about what a continuously observed month would have produced.

## Performance

Local Windows, same catalog and source files for each measurement. Each run issued 43 advancing requests, including closure and missing-data bookkeeping. Cold means a fresh Python process. Warm means another fresh process explicitly primed its two verified readers before the timer. OS disk caches were uncontrolled. Each number is a single run; the lookup column is the median of seven seeded one-second lookups checked against independent raw-file decoding.

| Run | Replay elapsed | Peak process working set | BI5 reads / tick partition verifications | Logical pinned bytes processed | Lookup median |
| --- | ---: | ---: | ---: | ---: | ---: |
| Cold | 2.159 s | 127.44 MiB | 12 / 12 | 842,604 | 27.13 ms |
| Warm | 1.845 s | 126.50 MiB | 12 / 12 | 842,604 | 39.48 ms |

The measured process hosted the Python API, SQLite client, Parquet reader, replay and in-process HTTP test client. The working set is the process high-water mark. Browser, Node, importer, operating-system file cache and other processes are outside that figure. Byte counts include complete BI5 and Parquet lengths per verification; physical disk I/O depends on caching. Both measured runs met 512 MiB for these two hours. Full-month throughput and memory remain unverified.

## Final local checks

Implementation commit `ea04161` passed 1,071 backend tests, with one skip and a Starlette deprecation warning, in 477.55 seconds. The frontend unit suite passed 13/13 and its production build succeeded. Standard Chromium passed 85/85 in 7.2 minutes; the separate genuine-data Strategy Lab browser test passed with retries disabled. The verified monthly source lookup rejected altered partition bytes while the immutable checkpoint remained readable.

```powershell
.venv/Scripts/python.exe -m pytest -q
npm --prefix frontend test
npm --prefix frontend run build
$env:TRADEFORGE_E2E_API_PORT='8553'
$env:TRADEFORGE_E2E_UI_PORT='4553'
npm --prefix frontend run e2e -- --retries=0
```

## Rollback

Revert this milestone's commits on the integration branch and restart the local application. Keep both immutable monthly tables, provider bytes, catalogs and existing execution receipts. Old code ignores the new tables. Broker-statement reconciliation remains separate. Production recorded trading remains blocked.
