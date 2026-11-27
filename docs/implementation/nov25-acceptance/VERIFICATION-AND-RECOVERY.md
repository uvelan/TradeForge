# PR #38 verification and interrupted acquisition

This correction changes the Windows verification and archive-acquisition commands. The economic replay policy, public routes and stored market receipts use their existing versions.

## Observed on Windows

Reviewed head `f8c1c6052b8c41c6922c5d0c9938a341d08bc963`. Combined code and test revision `3fb4c7eb8159283ffb1d64934546ec7897ef4774`. Only the handoff and its compact evidence receipt follow this revision.

| Process experiment | Reviewed command | Corrected command |
|---|---|---|
| The same old application is used as baseline and candidate. It omits the reopened gap count. | Exit 0, candidate parity passed, count `null` | Exit 1. Baseline accepted. On disk the candidate's first divergence is `parity/reopened_gap_count`, expected 2,881 and observed `null`. |
| MT5 product receipt disagrees with the independent reference | Exit 0 despite a saved mismatch. In the retained capture control, changing only the product's closed count gave 12,072 versus 12,071. | Exit 1 for that identical one-row mutation. The saved first divergence gives stage `mt5_preflight_closed_rows`, expected 12,071 and actual 12,072. The committed subprocess regression also checks a disagreement on eligibility. |
| Independent and product MT5 checks reject the genuine capture | Exit 0 | Exit 0, market status `REJECTED`, no first divergence. |
| Kill writer after 31 bytes of the third line, following two complete 503 receipts | Forced exit 15. Both restarts exited 1 on an unterminated JSON string. | Forced exit 15. First restart exited 0, retained both original lines, appended a verified controlled BI5 (200) and a 429 cooldown. Second restart exited 0, retained all four lines byte-for-byte, one catalog pin and zero download calls. |
| Same torn tail after a committed 429 with an active cooldown | Exit 1 before checking cooldown | Exit 0, `PROVIDER_COOLDOWN_ACTIVE`, zero calls. A malformed *newline-terminated* earlier row still exits 1 at its original line and leaves the file intact. |

The writer serializes acquisition on the receipt. Recovery scans committed lines before trimming a torn suffix. If the final JSON object is complete and only its newline is absent, recovery keeps the object and restores the delimiter. The termination and HTTP responses above are controlled. The recorded-price assertions below use the retained original archives.

| Dukascopy BID-rise event, November 13 | UTC | Source ordinal | Price |
|---|---|---:|---:|
| Qualified | 13:00:00.666 | 5 | 1.16239 BID |
| Entry | 13:00:00.771 | 6 | 1.16243 ASK |
| Protective stop | 13:31:38.103 | 1,564 | 1.16143 BID |

The independent check matched every one of the **8,636 quotes**. Reopening reported **2,881 gap intervals**. The existing cold, warm and restarted V2 ledger SHA-256 is `7a6795c12f0e0585e2f7ab85fdac682d8839f53f36a221dcd9140ba8d3c98254`. The independent tool hashes the same ledger using compact JSON separators and reports `c1522e073b8e1118ea97547626155422f3eb58c3def6f8342cb87877423087be`. Monthly Dukascopy acceptance is **INCOMPLETE** with 480 missing open hours. Delivery is modeled at the source timestamp.

Both checks rejected the pinned MT5 capture before economic replay. Its 30 daily files contain 1,121,792 observations. There are 12,071 observations in eight scheduled closed hours, ten missing open hours and 45 open-session gaps. The two rejection reasons are `RECORDED_CLOCK_CONTRADICTS_REPLAY_CALENDAR` and `OPEN_SESSION_COVERAGE_INCOMPLETE`. No provider requests were needed for either feed.

Thirty warm reopens of the same completed session on each revision gave median/p95 **24.61/26.94 ms** on the reviewed code and **24.35/26.69 ms** on the correction. Both returned the same checkpoint digest and 2,881 gaps. The median difference of −0.26 ms (−1.1%) is inconclusive for a performance improvement. Replay decoded 12 BI5 inputs and verified 12 Parquet partitions per timed run, 842,604 logical bytes. Physical disk reads were unmeasured. Acquisition recovery now scans the existing JSONL once to check committed history and cooldowns; that scan is outside the replay timer.

The [compact Windows command outputs](verification-correction.json) include the file pins, policy, event proofs, before/after gap results, individual timed runs, MT5 admission, and reopened-session checksums. The [existing evidence inventory](README.md) gives the exact missing intervals.

## Reproduce locally

Run from a clean checkout. The two BI5 hours and the checksummed capture with its 30 adjacent daily files are retained locally. The controlled fault tests run offline.

```powershell
git worktree add --detach ../TradeForge-merge37-smoke ad000f939de3bbbb8c5ba7b15a58cfd4ec102311
git worktree add --detach ../TradeForge-pr38-benchmark-base f8c1c6052b8c41c6922c5d0c9938a341d08bc963
.venv/Scripts/python.exe -m pytest -q tests/test_verify_nov25_windows.py research_backend/tests/test_month_archive_acquisition.py
.venv/Scripts/python.exe scripts/verify_nov25_windows.py --controls storage/t06-input --archive storage/nov25/raw --mt5-capture storage/nov25/mt5/capture.json --mt5-sha256 5431d15fa8a24b63b5f5b1f62241caa52110049ab8c87c8d1f7f5783d84d606c --mt5-preflight . --baseline ../TradeForge-merge37-smoke --candidate . --repeats 2 --workspace storage/pr38-correction/oracle --output storage/pr38-correction/genuine.json
.venv/Scripts/python.exe scripts/verify_nov25_windows.py --controls storage/t06-input --baseline ../TradeForge-merge37-smoke --candidate ../TradeForge-merge37-smoke --repeats 0 --output storage/pr38-correction/after-gap.json
.venv/Scripts/python.exe -m scripts.verify_month_replay --output storage/pr38-correction/legacy-ledger.json
.venv/Scripts/python.exe scripts/benchmark_month_reopen.py --baseline ../TradeForge-pr38-benchmark-base --candidate . --source storage/t06-input
```

The deliberate historical-candidate command should exit 1 and save its first divergence. A missing count is compatible only in the historical baseline role. A candidate must report an integer, including zero. The controlled suite injects discrepancies into the command and checks the process exit status and the report on disk. The genuine MT5 comparison must exit 0 when both calculations reject the tape.

## Rollback

Revert `3fb4c7e`, `2b3a728` and `0660ad4` in that order to return these commands to their reviewed behavior. Preserve the original captures, SQLite sessions, BI5s and committed JSONL receipts. Previously repaired histories remain readable by the older acquisition command. Returning to the reviewed verifier also restores its false-success behavior. The public cap is 160 cutoffs, historical intents retain `AWAITING_EXECUTION_VALIDATION`, and production recorded trading remains `BLOCKED`.
