# November verifier source selection

Reviewed `aff873e8529ce6e96698b37e4f8198ed08a1e2f8`. Combined Windows-tested revision `551d5fbb27853acc3a319fc3320c76d31f6d9b7b`. The two corrections are confined to the offline verifier and independent source reader. The archived prices, product audit, replay policy, and Market Terminal use the same code and data as before.

| Operator case | Reviewed process and saved evidence | Corrected process and saved evidence | Market disposition |
|---|---|---|---|
| Explicit archive is missing or a regular file | Exit 0 for each; reported registered=2 and identical inputs | Exit 1 for each; first divergence identifies `dukascopy_archive/source_validation` with `FileNotFoundError` or `NotADirectoryError` | Requested archive unverified |
| Dated subdirectory denies enumeration (controlled) | Exit 0; the enumeration error was suppressed | Exit 1; saved `PermissionError` | Requested archive unverified |
| Readable empty directory; or archive option omitted | Both exit 0; empty appeared as registered=2 | Both exit 0. Empty reports **2 controls / 0 archive files / 2 unique inputs**. Omission reports only the control scope and 2 unique inputs. | Control hours only |
| Two repeated controls plus one distinct dated hour | Exit 0; 8,637 rows, no separate input counts | Exit 0; **2 / 3 / 3**, 8,637 rows. Repeating only the controls gives **2 / 2 / 2**. | Extra hour is a controlled one-tick fixture |
| Different bytes claimed for an existing control hour | Exit 1, conflicting inputs | Exit 1, same source diagnostic and UTC epoch | Conflicting archive rejected |
| November 3 query failed; 29 retained genuine days | Exit 1, `KeyError: 1762128000` | Exit 0, independent/product agreement; failed-day receipt preserves code −10005, UTC bounds and 24 `OPEN_QUERY_FAILED` hours | `REJECTED` before replay |
| November 1 query failed; stray file present | Exit 1, `KeyError: 'sha256'` | Exit 0; 24 `CLOSED_QUERY_FAILED` hours. The unclaimed bytes contribute no quotes. A mixed Sunday in the controlled suite has 22 closed and 2 open failed hours. | `REJECTED` before replay |
| Successful query with retained zero-row file | Exit 0; captured with zero rows | Exit 0; November 1 remains a successful empty day. `failed_days` is empty for the original capture. | Original capture rejected on other evidence |
| Successful November 1 query, missing file or mismatched file pin | Both exit 1; missing file gave an epoch-key error, changed pin gave a generic manifest mismatch | Both exit 1; report names November 1 and the specific missing-file or SHA-256 error, before decoding mismatched bytes | Source verification failed |
| Product reports a failed hour as `MISSING_OPEN` (controlled) | Partial source failed before comparison | Exit 1; first differing hour and both statuses saved, even though both verdicts are `REJECTED` | Agreement check failed |
| Genuine unchanged 30-day MT5 capture | Exit 0; product rejected | Exit 0; independent and product reasons and all hourly receipts agree | `REJECTED` before replay |

In the old Dukascopy reader, a glob returned no matches for both invalid paths and readable empty directories. The merged hour map still contained the controls, so the command reported an identical replay. Directory enumeration now propagates access errors, and the report counts files before merging overlapping hours. For MT5 the old verifier indexed the file list before reading the daily terminal result. The corrected verifier reads the pinned daily claim first and verifies successful files before decoding. It preserves failed attempts and derives each affected hour's status from the New York forex calendar.

## Retained evidence and workload

Dukascopy November 13, 2025 from 13:00 through 15:00 UTC matched all **8,636** BID/ASK quotes. The BID-rise strategy qualified at **13:00:00.666 / ordinal 5 / BID 1.16239**, entered at **13:00:00.771 / ordinal 6 / ASK 1.16243**, and stopped at **13:31:38.103 / ordinal 1564 / BID 1.16143**. The reopened ledger had **2,881** gap intervals. Cold, warm and resumed V2 ledger SHA-256 was `7a6795c12f0e0585e2f7ab85fdac682d8839f53f36a221dcd9140ba8d3c98254`. The two BI5 SHA-256 pins, exact proof records and verifier's compact-JSON digest are in the [command receipt](source-selection.json). November remains **INCOMPLETE** with **480 missing open hours**.

The original 30-day MT5 manifest pin was `5431d15fa8a24b63b5f5b1f62241caa52110049ab8c87c8d1f7f5783d84d606c`. The audit counted 1,121,792 observations, including **12,071 in eight modeled closed hours**, **ten missing open hours** and **45 open-session gaps**. Source delivery time was unavailable. No new provider or terminal query was made for these checks.

Across three alternating fresh-process runs per version, median elapsed time for the genuine MT5 command including product audit was 2.107 s reviewed and 1.902 s corrected. This is a measured difference of −0.205 s (−9.7%) on one host; three samples with uncontrolled OS caching are insufficient for a speedup claim. Each revision verified the same 67,315,200 bytes of daily arrays and the 9,614-byte manifest in each audit. The cold and warm Dukascopy runs on both revisions each recorded 12 BI5 decodes, 12 Parquet verifications, and 842,604 logical bytes. Physical disk I/O was unmeasured.

## Windows reproduction and rollback

Set up a clean worktree of the reviewed verifier as the baseline. The regression fixtures are generated locally. The genuine command uses the retained files under the paths shown below.

```powershell
git worktree add --detach ../TradeForge-pr38-sources-before aff873e8529ce6e96698b37e4f8198ed08a1e2f8
.venv/Scripts/python.exe -m pytest -q tests/test_verify_nov25_archive.py tests/test_verify_nov25_windows.py tests/test_month_mt5_audit.py
.venv/Scripts/python.exe scripts/verify_nov25_windows.py --controls storage/t06-input --archive storage/nov25/raw --mt5-capture storage/nov25/mt5/capture.json --mt5-sha256 5431d15fa8a24b63b5f5b1f62241caa52110049ab8c87c8d1f7f5783d84d606c --mt5-preflight . --baseline ../TradeForge-pr38-sources-before --candidate . --repeats 1 --workspace storage/pr38-sources/genuine-tmp --output storage/pr38-sources/genuine.json
.venv/Scripts/python.exe -m scripts.verify_month_replay --output storage/pr38-sources/legacy.json
$env:TRADEFORGE_T10_SOURCE_STORAGE=(Resolve-Path storage).Path
$env:TRADEFORGE_T07_REAL_BI5=(Resolve-Path storage/t06-input/13h_ticks.bi5).Path
.venv/Scripts/python.exe -m pytest -q
npm --prefix frontend test
npm --prefix frontend run build
$env:TRADEFORGE_E2E_API_PORT='8784'
$env:TRADEFORGE_E2E_UI_PORT='4784'
npm --prefix frontend run e2e -- --retries=0
```

To roll back the command changes, revert `551d5fb` then `9303211`. Older command versions will again misreport invalid archive paths and fail to inspect partial terminal captures. No market file, SQLite receipt, or policy migration is involved. Public admission remains 160 cutoffs, historical intents retain `AWAITING_EXECUTION_VALIDATION`, and production recorded trading remains `BLOCKED`.
