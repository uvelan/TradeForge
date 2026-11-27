# November 2025 EURUSD recorded replay on Windows

**Full-month acceptance remains open for each feed.** The decision covers [2025-11-01 00:00, 2025-12-01 00:00) UTC. Under the New York Sunday 17:00 open / Friday 17:00 close calendar there are 482 modeled open hours and 238 closed hours. An hour with some prices does not certify continuous quote evidence.

| Input and disposition | Observed evidence | What prevents a monthly result |
|---|---|---|
| Dukascopy EURUSD, hourly BI5 BID/ASK. Two-hour replay **PASS**. | 8,636 ordered quotes on November 13, [13:00,15:00) UTC. Both original controls and partition checksums verified. Coverage improvement on this feed: zero new hours. | 480 open hours missing. The HTTPS probe with a 200 response discarded its body. Attempts to save the source returned connection timeouts; a separate probe returned HTTP 429. There is no hash or import receipt for that 200 response. |
| MT5 EURUSD, terminal server fingerprint `6b2cf3592011606b05543645`. Capture verified, **UTC replay rejected**. | Read-only COPY_TICKS_ALL returned 1,121,792 rows in 30 checksummed daily responses. Native BID, ASK, raw milliseconds and original array order retained. No historical broker delivery timestamps. | In the raw timestamp domain, 472 modeled open hours have observations, 10 have none, and eight modeled closed hours contain 12,071 rows. Under the 60-second silence threshold there are 45 open-session gaps. Historical server clock mapping and dated market sessions remain unverified. No shift, calendar substitution or cross-feed fill was used. |

First contradiction: on November 7 the first reported EURUSD tick inside modeled closure was 22:00:00.062 UTC as rendered from the raw epoch, daily ordinal 47,623. Quote BID 1.15597, ASK 1.15599. A trading outcome for this feed cannot be established under the selected calendar. Both readers checked the raw day files using capture-manifest SHA-256 `5431d15fa8a24b63b5f5b1f62241caa52110049ab8c87c8d1f7f5783d84d606c`. [Daily pins](archive/mt5-capture.json) Â· [Feed inventories and exact gap ranges](archive/coverage.json) Â· [Observed acquisition failures](archive/acquisition.json)

The [verification and interrupted-acquisition correction](VERIFICATION-AND-RECOVERY.md) records process exit codes, the two independent market verdicts, and recovery after a terminated receipt write.

The [source-selection correction](SOURCE-SELECTION.md) distinguishes an empty archive from a failed scan and a failed terminal query from a successful empty response.

### Exact missing Dukascopy open intervals

All endpoints below are UTC, start inclusive and end exclusive. The complete inventory separately identifies scheduled closures.

| November start | Exclusive end | Hours |
|---|---|---:|
| 02 at 22:00 | 07 at 22:00 | 120 |
| 09 at 22:00 | 13 at 13:00 | 87 |
| 13 at 15:00 | 14 at 22:00 | 31 |
| 16 at 22:00 | 21 at 22:00 | 120 |
| 23 at 22:00 | 28 at 22:00 | 120 |
| 30 at 22:00 | December 01 at 00:00 | 2 |

### Strategy Lab result

Choose **Strategy Lab â†’ Monthly EURUSD replay â†’ November 2025 â†’ Inspect monthly coverage â†’ Run monthly strategy**. The BID-rise V2 rule qualifies on a 10-point rise from its daily/continuous-segment anchor, takes the next strictly later ASK, and manages one normalized unit on BID with a 100-point stop and 200-point target. Transfer time, fees and added slippage are modeled as zero. Market liquidity and actual broker fills are unknown.

| Independent expected outcome on the observed segment | Service and restart result |
|---|---|
| Qualification at 13:00:00.666, ordinal 5 | Same decision and original quote proof |
| Entry at 13:00:00.771, ordinal 6, ASK 1.16243 | One unit open at the 13:10 committed checkpoint |
| Stop at 13:31:38.103, ordinal 1,564, BID 1.16143 | One unit closed, zero remaining; the returned source quote matched the original BI5 |
| Second source hour starts at 14:00 | Restart at the file boundary recovered the same spent day and already closed position; no second entry |

The raw files in chronological order have SHA-256 `2f47bcb14e0a2c4dcd6d65eb819b85f1b5168b656d1f5f67c3dcd2240569f014` (3,171 rows) and `9d40aa3147d93e6daa2218933877adb9471dc02ff08b19fc0d272f1eb781688f` (5,465 rows). The execution proof includes the original source ordinal, both prices, exact millisecond, source fingerprint and tick partition ID/hash. Session `3a2f94b1921fa540888234bcc8d3835a4a9266c810e03ea920e1fb290fb759a9` returned 43 committed receipts across 4,320 calendar slots. The existing sorted-JSON ledger checksum was `7a6795c12f0e0585e2f7ab85fdac682d8839f53f36a221dcd9140ba8d3c98254` for cold, warm and recovered runs. Identical simultaneous retries at the open checkpoint and file boundary added zero receipts. Controlled missing-file and crossing-price fixtures separately prove that an already open trade stays unresolved with no later exit.

Strategy Lab now reports committed open-gap intervals independently of the visible receipt page, alongside its existing completion frontier. The 2,881 recorded intervals comprise 2,880 missing ten-minute slots plus the first 89 ms of the observed segment. Adjacent missing slots can form one continuous gap. Completing the ledger through December 1 establishes neither a fully observed month nor month-level returns.

### Operator reproduction

From the repository root with the Windows dependencies installed, place the BI5 controls in `storage/t06-input`. Set a disposable data directory before importing. Keep the raw market archives and private terminal information outside Git.

```powershell
$env:TRADEFORGE_DATA_DIR = Join-Path $PWD 'storage/nov25-operator/research'
.venv/Scripts/python.exe -m scripts.month_archive 2025-11 --source storage/t06-input
.venv/Scripts/python.exe -m scripts.verify_month_replay --browser --output storage/nov25-operator/replay.json
```

To resume an authorized local acquisition, use the same `month_archive` command with `--fetch` and retain its per-hour HTTP receipt. For MT5, the optional acquisition command requires an already-authorized read-only terminal. A new capture may differ from this evidence. To reproduce this review exactly, use the retained capture and its pin.

```powershell
.venv/Scripts/python.exe -m scripts.capture_nov25_mt5 storage/nov25-operator/mt5-new
.venv/Scripts/python.exe -m scripts.month_mt5_audit storage/nov25/mt5/capture.json --sha256 5431d15fa8a24b63b5f5b1f62241caa52110049ab8c87c8d1f7f5783d84d606c --output storage/nov25-operator/mt5-audit.json
git worktree add --detach ../TradeForge-nov25-base ad000f939de3bbbb8c5ba7b15a58cfd4ec102311
.venv/Scripts/python.exe scripts/verify_nov25_windows.py --controls storage/t06-input --mt5-capture storage/nov25/mt5/capture.json --mt5-sha256 5431d15fa8a24b63b5f5b1f62241caa52110049ab8c87c8d1f7f5783d84d606c --mt5-preflight . --baseline ../TradeForge-nov25-base --candidate . --repeats 3 --output storage/nov25-operator/oracle.json
.venv/Scripts/python.exe scripts/benchmark_month_reopen.py --baseline ../TradeForge-nov25-base --candidate . --source storage/t06-input
```

### Independent verification, runtime and recovery

Starting commit `ad000f939de3bbbb8c5ba7b15a58cfd4ec102311` was the fetched main tip. The combined-tested revision is `b21a8d2139cba745bc258eb77a13ddc4893aa48b`. The full Windows suite report, final head, token counters and delivery measurements are attached to the PR. The final evidence-only change contains Markdown and JSON.

The direct BI5 oracle found no differences in any of 8,636 input rows, 4,320 ten-minute records, eligible observations, source references or three economic events. Its compact canonical JSON hash and the established ledger hash above use different JSON separators. Each was compared under the same serialization on both sides. Exact MT5 raw-hour classifications and gap endpoints also agreed. [Combined replay check](combined-parity.json) · [Independent method and trials](INDEPENDENT-WINDOWS-REPLAY.md) · [Restart and duplicate delivery receipts](replay/strategy-lab-recovery.json)

| Same Windows workload | Base | Candidate | Interpretation |
|---|---:|---:|---|
| Median cold time, 3 fresh processes per revision | 3.980 s | 4.110 s | +3.3%; timing inconclusive at this sample size |
| Median warm time, 3 fresh processes per revision | 3.916 s | 3.936 s | +0.5%; timing inconclusive |
| Median peak process memory, cold / warm | 127.28 / 126.09 MiB | 127.09 / 126.80 MiB | Two-hour input fits 512 MiB; the full-month workload is unmeasured |
| BI5 decodes / Parquet verifications / logical bytes per run | 12 / 12 / 842,604 | 12 / 12 / 842,604 | Same input volume; physical I/O depends on OS cache |
| Open completed ledger, 30 warm GETs per revision, median / p95 | 11.64 / 13.97 ms | 27.32 / 34.70 ms | Reopening was slower. The added count verifies all 43 persisted receipts. Checkpoint proof was unchanged. [Reopen samples](reopen-benchmark.json) |

To repeat the two actual Uvicorn restarts and concurrent retries using the reference generated above:

```powershell
node frontend/paper-e2e/verify-month-process-restart.mjs --reference storage/nov25-operator/replay.json --output storage/nov25-operator/process-restart.json
```

### Rollback and scope

Before merge, main can continue at `ad000f939de3bbbb8c5ba7b15a58cfd4ec102311`. After merge, revert this PR through review. Preserve the original BI5, local MT5 snapshots, download receipts, SQLite catalogs and immutable V2 steps. No schema migration, trading-policy version change or rollback rewrite is required. Public strategy admission remains 160 cutoffs; historical intents retain AWAITING_EXECUTION_VALIDATION. Production recorded trading remains BLOCKED.
