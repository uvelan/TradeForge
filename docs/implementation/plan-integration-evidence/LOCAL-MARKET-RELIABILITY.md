# Windows local market-data recovery drill

Baseline `e2a5a66341bf249b036c1e876bbb006755e4a129`, PR #37. The drill uses the Data Library, saved-candle API, Market Terminal, Learning Journal and Signal Explorer. All writes and fault injection target disposable storage. [Dataset IDs, hashes, Lesson image digests and timings](LOCAL-MARKET-RELIABILITY.json).

| Operator workflow | Expected | Observed locally |
| --- | --- | --- |
| Import and verify | Recognize each of the four inputs by SHA-256. An unknown file fails before publication. Retrying preserves the receipts. | A changed byte returned 422. The two 2026 files registered under their original IDs. Two 2025 files supplied 3,171 and 5,465 quotes and 24 M5 BID bars. Every returned 2025 bar matched the independent source decoder. Both 2026 instruments had 264 M5 BID bars. The paired chart test checks all 24 shared bars per instrument in the 13:00–15:00 UTC window. |
| Read during another import | Concurrent readers can continue at their last committed generation. The uncommitted hour stays invisible. | With two Parquets and the first BI5 hour committed, 7 datasets were visible. After the second hour’s first file rename and before its SQLite commit, the missing hour returned 404. Abrupt writer exit (75) and an injected disk-full error (HTTP 507) each left those same 7 datasets visible. The open paired chart, Lesson metadata and PNG matched throughout. Two successful deliveries of the second hour returned the same receipt and left 12 datasets. |
| Restore and reopen | A clean storage and browser profile can reproduce source reads and reopen original evidence after an API restart. | Both catalogs, archive files and journal were copied. All 12 dataset records and publication receipts matched, as did both retained BI5 files, complete snapshot records and PNG bytes. The two frozen Lessons retained their opposite lane orders and 14:05 UTC cutoff. Their 14:00 closes were EURUSD 1.10527 and GBPUSD 1.32318. The separate retrospective signal capture reopened with its own signal time. |

## Operator commands and scope

From the repository root on Windows, with project dependencies installed, run:

```powershell
.venv/Scripts/python.exe -m scripts.verify_local_market_workspace --output storage/reliability-evidence.json
```

The command reads the two 2025 hourly files in `storage/t06-input` and resolves the two 2026 files from the local research catalog. Their expected hashes are fixed independently of the upload. It never acquires prices over the network. On this run import, paired-chart verification, fault/retry, restore, restored-chart verification and reopened evidence took 6.94, 15.20, 11.25, 0.22, 7.08 and 6.13 seconds respectively. SQLite backup hashes and PNG digests identify this particular run. Dataset identities are independent of the drill’s temporary directory.

To operate manually, open **Data Library → Register a reviewed local archive**, select a file and click **Register or resume file**. Re-select the identical file after interruption. Under **Inspect archived candle coverage**, select a partition and open **Data Trust**. The 2025 feed is `dukascopy-bi5-v2`, with the supplied hours covering [13:00,15:00) UTC on 13 November. Reconciliation checks all compressed-file records against the stored quote sequence and the selected side’s candle OHLC. For older imports without retained bytes, an identical source-file retry supplies the missing original. Unoccupied grid slots are shown separately. The 2026 feed is `dukascopy-web-m1-v1`, with M5 BID bars spanning [00:00,22:00) UTC on 13 November. These are archived candles. Neither source attests that the provider observed every market event or that modeled availability represents actual delivery.

For a real restore, stop writers and the app, specify independent absolute paths and use a **new destination**:

```powershell
.venv/Scripts/python.exe -m scripts.restore_local_workspace K:/Projects/TradeForge/storage D:/TradeForgeRecovered/storage
$env:TRADEFORGE_STORAGE='D:/TradeForgeRecovered/storage'
$env:TRADEFORGE_DATA_DIR='D:/TradeForgeRecovered/storage/research'
```

Start the application with those settings only after successful verification and a complete `restore-report.json`. If verification fails, retain the destination for inspection and retry into a fresh directory. The manifest maps ten candle partitions. Its two unsupported tick partitions remain traceable in the research catalog. Reconciliation found 10 import references, 63 SMT revisions, 126 lane references and zero dangling receipts. The fixed catalog in the genuine drill was empty. The existing fixed-catalog and export restore tests exercise those paths separately.

The drill used independent Windows application storage and browser profiles under the current Windows account. The storage fault was injected at the filesystem publication boundary. Physical drive exhaustion, a separate Windows account, live MT5, provider-wide tick completeness, storage quota enforcement and high-volume deployments remain unverified. Reader cutover and writer retirement are separate decisions. Historical intents remain `AWAITING_EXECUTION_VALIDATION`; public admission is capped at 160 cutoffs and production recorded trading remains `BLOCKED`.

## Development checks and rollback

The first browser comparison requested the default 180-bar tail while expecting 240 bars. The test now requests its intended limit. The first restore comparison encountered WAL sidecars because SQLite preserved the source journal mode in the backup. The restore command normalizes the private backup’s journal mode before read-only registration. Subsequent end-to-end verification passed with zero Playwright retries.

```powershell
.venv/Scripts/python.exe -m pytest -q tests/test_partition_publication.py tests/test_phase_e_rehearsal.py tests/test_restore_rehearsal.py research_backend/tests/test_workspace_tools.py
.venv/Scripts/python.exe -m pytest -q research_backend/tests/test_local_market_reliability.py
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

The final local backend suite ran once: 1,057 passed, one skipped, and one Starlette test-client deprecation warning in 608.16 seconds. The focused suites had 31 passes in 19.81 seconds plus one source-reconciliation test in 1.99 seconds. Frontend unit tests passed 13/13 (2.94 seconds wall clock), and TypeScript plus the Vite production build passed (18.51 seconds). Standard Chromium passed 85/85 in 7.9 minutes with zero retries. The separate 2025 recorded-data test passed 100 chart navigation cycles in a 2.1-minute browser run. The five browser phases of the recovery drill passed with zero retries.

To roll back, stop the restored instance, restart with the original storage settings, and revert the milestone commit. Prior readers can use the existing Parquet files and catalog IDs. The new source-bytes table is additive and can be retained for audit.

## Review ZIPs after relocation

At baseline `9db563956dd6696bd6990a489566dbfcc790ca06`, offline verification accepted both copied ZIP formats, but the restored app returned 404 on both downloads, 409 on both structured review imports, and 409 on recorded replay detail. The catalog still correctly retained the original absolute location as issuance provenance. The app now reads the issued filename only under the active profile's export directory, checking the filename, physical file and original SHA-256. It never looks for a package in the old profile.

The isolated mounted-app test creates a package of each format, runs the real offline restore to a fresh absolute root, confirms the original package row and copied ZIP are unchanged, then opens the restored app after removing the original profile. Both downloads and recorded detail return 200; structured review imports return 201, retain the submitted JSON, and deduplicate retries. A missing active copy returns 404 on download even while the original still exists. Changed bytes return 409 and create no extra review.

```sh
python -m pytest -q research_backend/tests/test_review_package_relocation.py research_backend/tests/test_historical_packages.py research_backend/tests/test_ai_review_decisions.py tests/test_restore_rehearsal.py
```

The same new two-case test failed as described on the baseline (exit 1) and the combined suite passed on the corrected tree (7 passed, one existing Starlette deprecation warning; Linux Python 3.12, locked dependencies except Windows-only MetaTrader5). Independent diff review found no blocking defect. This is an isolated restore test, not a rerun against a production archive or a separate Windows account.

Rollback by reverting the locator change; no database migration, row rewrite or archive modification is required. Older code again needs the issuing profile at its original path, so keep the original workspace or its backup until operator acceptance. Trading gates are unchanged.
