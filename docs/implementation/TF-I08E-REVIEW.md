# TF-I08E real market-data onboarding and acceptance

TF-I08D merged as `f5099602485fdd1587c29b3790fb3f7ac4409158`, which is also the TF-I08E starting commit. The initial implementation is `5983fdb4ed9e5453c1b1d037fe01f457db48e318`. The final implementation checkpoint is `a4bf1a700a96adf2be4c7c9238425d7a4e82d9cc`. This branch is open for independent review. [Prerequisite PR](https://github.com/uvelan/TradeForge/pull/13) · [TF-I08E PR](https://github.com/uvelan/TradeForge/pull/14)

| Gate | Decision |
| --- | --- |
| Historical simulation | **READY** for the pinned, complete November 13 EURUSD and GBPUSD BID sample. Five-session coverage remains incomplete. |
| Host UTC | **FAIL** against the one-second threshold |
| Live recorded acceptance | **NOT EXECUTED** |
| Production recorded readiness | **BLOCKED** |

## Actual inventory and lawful acquisition

The configured local SQLite catalog is `storage/research/tradeforge.sqlite3`. Files are under `storage/research/market`. Before onboarding, the catalog had zero datasets and zero sealed recorded manifests. After onboarding it has 36 candle partitions with 30,451 records from 18 original provider CSVs, comprising 32 BID and 4 ASK partitions. There are zero imported production tick partitions and zero sealed recorded manifests. Offline test and benchmark catalogs use isolated temporary directories. [Before inventory](evidence/TF-I08E/inventory.txt) · [Per-source manifest, hashes and coverage](evidence/TF-I08E/data-quality.json)

On November 19, 2026, the official free Dukascopy web widget supplied UTC CSV exports for EURUSD and GBPUSD. The original provider filenames, acknowledged export date, SHA-256, declared UTC day, point scale, BID or ASK basis, first local import time and derived partition checksums are recorded. The inputs and full price-bearing responses are kept in ignored local storage. The public PR includes source metadata, counts, timestamps and response fingerprints. The separate BI5 source documented by Dukascopy uses credentials and requester-pays billing. This phase made no requester-pays request. Redistribution rights were not assumed. [Dukascopy free historical export](https://www.dukascopy.com/swiss/english/marketwatch/historical/) · [Dukascopy BI5 access and format](https://wiki.dukascopy.com/en/development/data-export/) · [Dukascopy website terms](https://www.dukascopy.com/swiss/english/legal-pages/terms-of-use/)

The accepted session is **2026-11-13 00:00 through 22:00 UTC**, with 1,320 of 1,320 M1 and 264 of 264 M5 BID bars per instrument. The Friday market closure accounts for the two hours after 22:00. Both ASK exports for that day are also retained. Five selected sessions from November 9 through 13 have actual gaps. November 16, 17 and 18 were also sampled and show why a daily CSV filename alone cannot establish completeness. Each recorded missing slot is listed in the quality report. Importers do not fill gaps or change the calendar to make feeds appear complete. [Provider trading hours](https://www.dukascopy.com/swiss/english/forex/forex-trading-accounts/link/?c=659ee037587d8)

## Deterministic importer and bounded reads

The baseline three-tick reproduction changed the candle open and close when the same observations arrived in reverse order. The BI5 v2 feed `dukascopy-bi5-v2` uses `UTC_TICK_MS_FILE_ORDINAL_V2`. The input's declared day or hour and checksum identify the source. Within that file, millisecond UTC event time sorts first and the original provider file record ordinal breaks equal-millisecond ties. Identical records sharing that source identity are counted once. Conflicting records with the same identity fail. Distinct ordinal observations remain distinct even when the prices are equal. The importer validates compression, record width, declared UTC interval, point scale, bid/ask order and finite nonnegative volume. Completed hourly and daily windows cannot overlap, even if observed ticks happen to be disjoint. Identical re-import verifies the existing partitions and returns the original manifest. [Order failure reproduction](evidence/TF-I08E/baseline-order.txt)

BI5 tick partitions preserve both sides in integer point units and original side volume. Derived BID and ASK M1/M5 partitions have first/maximum/minimum/last price and a tick-count candle volume. An M5 tick bucket with incomplete M1 constituents remains available for chart inspection and is excluded from historical simulation. The web CSV feed is separate. It retains the provider's M1 volume and sums it to M5, where all five UTC minute slots exist. The BID and ASK browser exports are separately identified and are never joined to infer a tick spread. The web tick comparison lane has only seconds, provider file row order and a single quote side. It does not establish live arrival, millisecond order, or synchronized bid/ask ticks. The BI5 flow was validated with deterministic synthetic binary fixtures because no approved local BI5 source was available.

Parquet complete reads verify SHA-256 and the registered row count on the same open file and iterate in batches of up to 8,192 rows. Replay retains its 500,000 selected quote cap. Chart and indicator tail reads retain their explicit bounds and pinned analytical reads check their decoded counts. The importer publishes the raw decoded ticks, both price bases and both timeframes in one SQLite catalog transaction. Atomic visibility applies to catalog consumers. A failed transaction may leave an uncatalogued immutable Parquet file that the same verified import can reuse. The original provider files are retained locally. The test with 8,205 ticks forces multiple row groups and verifies that the first tick remains the derived open even though it is outside the legacy 2,000-row chart tail.

## Real numerical and simulation acceptance

For each instrument, all 264 stored M5 OHLC records agreed with both a separate fold of the original M1 exports and an independently exported provider M5 file. The two provider BID tick samples used 3,315 EURUSD and 3,105 GBPUSD observations from 12:00–13:00 UTC. Their direct five-minute folds agreed with the twelve corresponding native provider M5 bars for each instrument. No OHLC mismatches were observed. The original sources and pinned Parquet selection were checked by SHA-256. [Numerical checks](evidence/TF-I08E/numerical-checks.json)

For the accepted day the market structure, liquidity, FVG, order block, H1→M5 CRT and positive-pair M1/M5 SMT requests ran with `SIMULATED_NOMINAL_CLOSE_PLUS_DELAY_V1`. The four modeled primary/comparison delays were 0/0, 2/2, 2/15 and 15/30 seconds. These were modeled sensitivities. All eight SMT combinations returned eight verified simulation-eligible events each. Both feeds had complete slot coverage. The SMT engine reports `PARTIAL / MATCHING_OR_ACTIVATION_COVERAGE_INCOMPLETE` for these bounded evaluations, so the accepted results do not establish that every possible match could activate inside the window. Recorded admission returned `UNVERIFIED_AVAILABILITY` for the same files.

Independent checks used wick extrema against neighboring bars, close beyond the selected structural level, three-candle FVG bounds, H1 OHLC folded from original M1, CRT midpoint, matched SMT pivot prices and times, divergence magnitudes, and shared-bar confirmation times. Repeated requests preserved fingerprints, changing modeled delays changed the calculation identities, backward pagination reassembled the same stream, rewind emitted no future revisions, and a CRT probe before the first H1 close returned no formations. H4→M15 was outside the materialized archive scope. [Exact frozen requests](evidence/TF-I08E/frozen-requests.json) · [Response fingerprints and statuses](evidence/TF-I08E/evaluations.json)

## Host clock and MT5

Windows Time reported leap indicator 3, stratum 0 and no successful synchronization. Five independent samples from `time.windows.com` differed by approximately +6.557 to +6.559 seconds. No operating-system time configuration was changed. Because the host failed the required precondition, the Python/MQL5 comparison and ten-candle read-only acceptance were not executed. The previous two-hour MT5 timestamp discrepancy remains unresolved and `UNVERIFIED_TIMESTAMP_DOMAIN` continues to block production recorded capture. [Fresh host clock](evidence/TF-I08E/host-clock.txt) · [Previous MT5 diagnostic](evidence/TF-I08D-integrity/mt5-times-explicit.json)

On the affected workstation, use **Settings → Time & language → Date & time → Set time automatically → Sync now**. Where the controls are managed, have the system administrator verify the configured Windows Time source. Re-run `w32tm /query /status` and `w32tm /stripchart /computer:time.windows.com /samples:5 /dataonly`. Once sync is established and all offsets are below one second, run the repository Python and MQL5 diagnostics together while quotes update. Apply a broker conversion only if independently supported by the evidence.

## Manual reproduction and rollback

1. In the official Historical Data Export widget, select each instrument and **Export → Minute → 1 → 13 November 2026 → UTC → Bid → Download**. Repeat for Ask to reproduce the alternate side. Retain the original filenames locally and record their SHA-256. The numerical lane also uses an independent five-minute BID export and a 12:00 UTC BID tick export for each instrument.
2. Run `python -m scripts.import_dukascopy_csv EURUSD 2026-11-13 <original-file>` from the repository root with the intended TradeForge environment. For Ask, append `--side ASK`. If using local aliases as in this evidence, pass `--original-name` with the provider filename. For approved BI5 files, submit the original binary at `POST /research/api/market/import/bi5?symbol=EURUSD&day=2026-11-13` on the mounted app, adding `hour` only for hourly files. Check the returned source and partition hashes.
3. For the acceptance script, place preserved provider files under `storage/tf-i08e-source` with local aliases of the form `EURUSD_2026-11-13_M1_BID.csv`, `EURUSD_2026-11-13_M5_BID.csv`, and `EURUSD_2026-11-13_T12_BID.csv`. Use the same aliases for the other symbol and side. Run `python -m scripts.verify_historical_acceptance 2026-11-13 --sources storage/tf-i08e-source --public docs/implementation/evidence/TF-I08E --private storage/tf-i08e-acceptance`. Keep the private directory out of Git.
4. Back up SQLite and the market directory before a rollback. Revert the TF-I08E commits through normal Git history. The new imports use additive catalog entries and separate immutable feed identities. Preserve them for audit, or restore the pre-import database and files together. Existing BI5 and recorded arrival partitions are preserved. A range mixing old and new feed identities requires deliberate pinned selection.




## Performance and resource results

Thirty measured serial iterations followed three warmups per lane. Import publication includes isolated database setup and immutable Parquet writes. Python allocation and native process high-water measurements were collected separately. An earlier measurement overlapped local Chromium and is retained separately. The table uses the final benchmark measured after the browser runs. [Raw metrics and five-session frozen request](evidence/TF-I08E/performance.json)

| Lane | P50 / P95 ms | Records or partitions |
| --- | ---: | --- |
| Real EURUSD web CSV decode | 11.5 / 12.0 | 1,320 M1 records |
| Real M1→M5 derivation | 0.8 / 0.8 | 264 complete M5 records |
| Real CSV publication including setup | 170.5 / 207.8 | 2 partitions |
| Real CSV duplicate import | 17.5 / 19.0 | Existing source and 2 verified partitions |
| Synthetic BI5 decode | 32.1 / 34.1 | 25,000 ticks, approximately 780,000 ticks/s at P50 |
| Synthetic BI5 aggregation to M1 | 33.1 / 34.9 | 25,000 ticks, 52 bars |
| Synthetic BI5 aggregation to M5 | 33.1 / 35.6 | 25,000 ticks, 11 bars |
| Synthetic BI5 publication including setup | 303.8 / 367.5 | 5 partitions |
| Synthetic BI5 duplicate import | 30.4 / 34.2 | Existing source and 5 verified partitions |
| Real one-session M5 SMT | 46.7 / 52.4 | 2 checksums, 528 rows, 169,459 JSON bytes |
| Real five-session incomplete M5 SMT | 123.0 / 156.9 | 10 checksums, 2,807 rows, 64,815 JSON bytes |
| Strict recorded admission on the same real archives | 19.0 / 23.7 | 2 checksums, 528 rows, 2,682 JSON bytes |

The five-session M5 comparison failed readiness with **13 EURUSD and 12 GBPUSD missing open slots**. The gate timing measures a rejected recorded request. A comparable real recorded analytical timing is unavailable. The previous controlled dense recorded-SMT benchmark measured **113.5 MiB** peak Python allocation. Current peaks were approximately 1.0 MiB for real CSV publication, 20.1 MiB for synthetic BI5 publication, 1.7 MiB for one-session simulation and 5.5 MiB for five-session simulation. Native cumulative point-in-run peaks ranged from 95,432 to 170,452 KiB. Batching limits a decoded slice. The full import and replay still retain bounded in-memory structures. Estimated accounting is not a hard allocation limit.

## Test evidence and failed attempts

- Full Python passed **482**, including **20** new cases, all **22** legacy fingerprints, strict timing, SMT journals and alerts, lease takeover, receipt witnesses and stale aggregates. The focused lane passed **223**.
- Vitest passed **8**. TypeScript and Vite production build passed. Full Chromium passed **66** with zero retries. All **20** consecutive drawing/replay checkpoints and the single delayed-analysis stress test passed. Those local browser runs preceded the final gated check for incomplete BI5 M1 constituents. The final hosted runs validate the resulting commit.
- The initial focused import run caught BID and ASK ambiguity at the legacy chart selector. It now preserves the BID default and permits explicit ASK inspection through pinned archive selection. Both the original range assertions and the newly expanded inventory assertions passed.
- One provider-browser attempt tried reusing the export dialog after Download closed it. Subsequent requests reopened the dialog and returned the original files. [Failed export attempt](evidence/TF-I08E/export-tick-attempt.txt) · [Successful acceptance exports](evidence/TF-I08E/export-acceptance.txt)
- The first operational verifier assembled backward SMT pages in traversal order. The second used an uppercase direction label for SMT. Both were errors in the verification runner. Reassembly now prepends the older pages and recognizes actual SMT direction labels. The full real-data verification passed after the corrections. [First attempt](evidence/TF-I08E/real-acceptance-attempt.txt) · [Second attempt](evidence/TF-I08E/real-acceptance-attempt2.txt) · [Passing acceptance](evidence/TF-I08E/real-acceptance.txt)
- The first benchmark reached the analysis phase and stopped with `NameError: name 'app' is not defined`. The runner now imports the FastAPI app explicitly. The attached timings are from the completed rerun.

[Backend](evidence/TF-I08E/backend.txt) · [Focused](evidence/TF-I08E/focused.txt) · [Vitest](evidence/TF-I08E/vitest.txt) · [Build](evidence/TF-I08E/build.txt) · [Chromium](evidence/TF-I08E/playwright.txt) · [Checkpoint repetitions](evidence/TF-I08E/checkpoints.txt) · [Drawing stress](evidence/TF-I08E/drawing-stress.txt) · [Reproduction commands](evidence/TF-I08E/commands.md)

The preliminary code checkpoint passed the [push workflow](https://github.com/uvelan/TradeForge/actions/runs/44457613127) and the [PR workflow](https://github.com/uvelan/TradeForge/actions/runs/44457642513). The final evidence commit receives independent checks on the [PR checks page](https://github.com/uvelan/TradeForge/pull/14/checks).
