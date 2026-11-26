# Independent Windows November 2025 EURUSD replay acceptance

The two recorded Dukascopy control hours pass. Full November economic replay remains blocked by coverage. The archive intake retained the same two independently pinned BI5 files, and the MT5 capture fails clock and coverage admission.

| Source | Observed evidence | Disposition |
| --- | --- | --- |
| Dukascopy EURUSD BI5 | November 13, 13:00–15:00 UTC. All 8,636 quotes matched in source SHA-256, original ordinal, millisecond, BID and ASK. Both versions matched all 4,320 ten-minute coverage receipts and the exact unverified intervals. | Pass for the supplied hours and missing-data bookkeeping. Of 720 calendar hours, 2 are registered, 480 open hours are missing and 238 are scheduled closed. |
| MT5 EURUSD COPY_TICKS_ALL | 30 successful unshifted UTC day queries, 1,121,792 raw observations. The product preflight matched all 720 independent hourly classifications and 45 open-session gap endpoints. 12,071 observations occur in 8 modeled closed hours, and 10 open hours have no observations. | Admission rejected. No economic replay outcome or performance verdict for this source. |

The independent calendar classifies each UTC hour by the New York weekday and wall clock, with a 17:00 Sunday open and a 17:00 Friday close. The BI5 oracle decodes the original 20-byte big-endian records and orders equal milliseconds by source ordinal. It computes coverage intervals and replays the documented 10-point BID qualification, next strictly later ASK and first BID stop or target. The harness checks every input quote at the production replay boundary, then compares the public month receipts and resolves decision evidence through the public quote endpoint. It verifies that each fill and exit references the observed qualification in the same session.

| BI5 event | November 13 UTC | Original ordinal | Observed price |
| --- | --- | ---: | --- |
| Qualification | 13:00:00.666 | 5 | 1.16239 BID |
| Entry | 13:00:00.771 | 6 | 1.16243 ASK |
| Stop | 13:31:38.103 | 1564 | 1.16143 BID |

Baseline `ad000f939de3bbbb8c5ba7b15a58cfd4ec102311` and replay candidate `c4f9ffbc3695720e89b7fe2e6a672d4a4a8115e5` have the same persisted ledger hash with no divergence. The candidate's reopened inspection reports all 2,881 committed gap intervals. These consist of 2,880 missing-open receipt intervals and one observed-slot prefix. The absolute gap endpoints form six merged intervals with 1,728,000,263 unobserved open milliseconds.

The first MT5 contradiction is Friday, November 7 at 22:00:00.062 UTC using the unshifted `time_msc` from original daily array ordinal 47,623. The model scheduled a closing at 22:00:00.000 UTC. The longest open gap starts on Sunday, November 23 at 22:00:00.000 UTC and ends on November 24 at 00:01:29.069 UTC. The observed discrepancy does not establish a clock correction. The independent check preserved every raw timestamp and each daily file's checksum. The integrated preflight provided the same classifications, raw daily source pins and first ordinals. The [sanitized receipt](independent-windows-replay.json) records exact endpoints, source hashes and the inspected revisions.

## Windows replay measurements

Each measurement used a fresh process, the same seed catalog and market files, an initial inventory request, 43 advances and a reopened inspection of the completed session. Warm runs verified the same two archived hours once before timing. The host OS file cache was uncontrolled. All 12 timed workloads reproduced their revision's independent parity ledger.

| Cache condition | Baseline median elapsed | Candidate median elapsed | Baseline median peak working set | Candidate median peak working set |
| --- | ---: | ---: | ---: | ---: |
| Cold, 3 trials per revision | 3.980 s | 4.110 s | 127.28 MiB | 127.09 MiB |
| Warm, 3 trials per revision | 3.916 s | 3.936 s | 126.09 MiB | 126.80 MiB |

Each timed workload decoded 12 complete BI5 inputs and verified 12 Parquet partitions, processing 842,604 logical bytes. Measurements cover the Python process hosting in-process HTTP, SQLite, Parquet and replay. Peak working set is the process high-water mark, including warmup when used. Import, browser rendering, acquisition, OS file cache and quote lookup after replay are outside the timer. All runs stayed below 512 MiB on these two hours. The samples varied across trials, so they leave the performance change inconclusive. Full-month throughput and memory remain unmeasured.

## Reproduce

Run with the project's Windows virtual environment, the original two BI5 controls, the retained source snapshot and clean worktrees at the recorded revisions. Product preflight can run in the integrated candidate worktree. The harness uses disposable copies of one catalog and in-process HTTP.

```powershell
.venv/Scripts/python.exe scripts/verify_nov25_windows.py `
  --controls <control-directory> --archive <archive-raw-directory> `
  --baseline <baseline-worktree> --candidate <replay-candidate-worktree> `
  --mt5-capture <capture-json-path> --mt5-preflight <integrated-worktree> `
  --mt5-sha256 5431d15fa8a24b63b5f5b1f62241caa52110049ab8c87c8d1f7f5783d84d606c `
  --repeats 3 --output storage/nov25-oracle/reproduced.json
```
