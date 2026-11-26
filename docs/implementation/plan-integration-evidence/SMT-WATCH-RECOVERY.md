# Windows-local paired SMT watch

Base: `f9c2f20590e17fad42acd1f4c98a188c331e3461`, PR #37. [Stored receipts, dataset IDs, checksums and revision IDs](SMT-WATCH-RECEIPTS.json).

## Operator workflow

1. In Data Library select the reviewed Dukascopy EURUSD and GBPUSD M5 BID partitions listed in the receipt. Enter 2026-11-13 12:15 and 13:40 UTC and choose **Watch exact M5 pairs**. The worker can run with the chart closed. The two source files remain in the local archive.
2. In Signal Explorer select the bearish family's **Confirmed** transition and open its recorded chart. Expand each dataset to inspect the original ID and SHA-256. The two series stop at the selected transition. The pivot time, modeled availability and local watch publication time are displayed separately. The historical confirmation remains inspectable after invalidation.
3. Close and restart the local application using the same storage directory. Open Data Library to recover the verified frontier. Return to Signal Explorer and reopen the same confirmation. Stopping the application preserves the watch. Replacing or disabling it is a separate operation.
4. In Strategy Lab reopen a completed historical run and select its recorded session/layout. The default is that session's greatest committed millisecond. **Inspect saved checkpoint** opens an earlier receipt explicitly. Session preference is local to this browser and run. A fresh browser defaults to the most recently created session.

## Historical acceptance

One open FX interval, 17 exact UTC candle-open pairs, lower and upper bounds 12:15 and 13:40 UTC. Both lanes use `dukascopy-web-m1-v1`, BID. Source candle times are opens. Availability is modeled at the five-minute close with zero added delivery delay. Read-only Arrow decoding and SHA-256 verification precede the independent two-left/two-right wick comparison. The alert retains the poll observation time and the local wall-clock second sampled while inserting the confirmed revision inside the publication transaction; it does not establish when a historical provider delivered a quote.

| UTC observation or probe | Independent expectation | Observed journal / strategy producer |
|---|---|---|
| 12:35 and 13:05 swing highs | EURUSD 1.10549 → 1.10557, GBPUSD 1.32316 → 1.32306 | Same timestamps, values and bearish direction |
| 13:19:59 → 13:20:00 | Second right-hand bar closes; none → candidate | None → SMT_CANDIDATE |
| 13:24:59 → 13:25:00 | 13:20 GBPUSD high 1.32297 remains below 1.32316 | Candidate → SMT_CONFIRMED, one confirmation alert |
| 13:29:59 → 13:30:00 | 13:25 GBPUSD high 1.32328 crosses 1.32316 | Confirmed → SMT_INVALIDATED |
| Reopen confirmation | Both charts end with the 13:20 candle available at 13:25 | Both cutoffs and original dataset references matched |
| End | Both feeds verified through 13:40 | COMPLETE, one stored alert for this family |

The strategy evidence collector was run from fresh prefixes at all six probes. The monitor stores the exact strict SMT revision and matching evidence at first publication. Signal Explorer filters time before selecting lifecycle status; an invalidation cannot hide the earlier confirmation in an as-of search. The selected family's key is `7e9485b627eb7c327f80ae78f4e524920c33ef6ffe6802ecdd76397c53985e43`.

## Restart, concurrency and gaps

The checkpoint and immutable original transitions and alerts commit together under a generation/owner/expiry check with a SQLite writer lock. The archive case used real process termination before commit and immediately after commit, with a controlled lease clock on recovery. In the first case the public frontier remained at 12:55 with zero alerts; in the second it reopened at 13:35 with an alert already durable. Both finished at 13:40. Repeated polling produced one alert. All stored lifecycle IDs, timestamps and original strategy proofs compared equal across four databases including an uninterrupted run. Wall-clock publication time is specific to each run. The Chromium check uses two actual API server lifetimes and the same temporary profile.

Separate controlled broker observations exercise a missing comparison candle, overlapping polls, and admission of the restored observation. The frontier waits at the missing open. The recovered journal matches the uninterrupted capture. A positively changed GBPUSD high (28 → 31 in the synthetic fixture) preserves the original confirmation, records the old and new bar, and retracts the active alert. Lease cases cover busy, precise expiry, takeover, disable, and reconfiguration. No stale owner publishes a checkpoint, transition or alert.

A single active watch is supported. Archive enrollment accepts 1–60 M5 bars in one open FX interval, with two immutable partitions of up to 2,000 candles each. Replacing archive bytes breaks the pin. Live capture retains a bounded overlap (600 bars behind the frontier, up to 500 ahead) and cannot infer a closure from an absent quote. An outstanding gap can therefore require operator investigation or a new enrollment. Revisions outside the available broker history are unverified. The terminal smoke retrieved six closed M5 bars for each symbol with last terminal-reported open 1795623300. Broker timestamp mapping and actual delivery latency remain unverified.

## Reproduce locally

Run from the repository root with the original local archives present. Each acceptance run writes to a disposable profile.

```powershell
.venv/Scripts/python.exe -m scripts.verify_archive_smt_watch
.venv/Scripts/python.exe -m scripts.verify_archive_smt_watch --browser
.venv/Scripts/python.exe -m scripts.verify_archive_smt_watch --mt5-smoke
.venv/Scripts/python.exe -m pytest -q research_backend/tests/test_smt_watch_recovery.py research_backend/tests/test_smt_monitor_lease.py research_backend/tests/test_smt_authority.py research_backend/tests/test_recorded_session.py
.venv/Scripts/python.exe -m pytest -q
npm --prefix frontend test
npm --prefix frontend run build
$env:TRADEFORGE_E2E_API_PORT='8553'
$env:TRADEFORGE_E2E_UI_PORT='4553'
npm --prefix frontend run e2e -- --retries=0
$env:TRADEFORGE_E2E_API_PORT='8559'
$env:TRADEFORGE_E2E_UI_PORT='4559'
npm --prefix frontend run e2e -- --config playwright.recorded.config.ts --retries=0
```

## Local verification results

| Check | Result |
|---|---|
| Focused watch, lease, journal, recorded replay | 19 passed in 15.80 s |
| Full backend on Windows | 1,063 passed, 1 skipped, one TestClient deprecation warning, 597.90 s |
| Frontend unit / production | 13 tests passed; TypeScript and Vite build passed |
| Standard Chromium | 85 passed in 7.6 min, zero retries |
| Recorded Strategy Lab / 100 navigation cycles | 2 passed in 2.3 min, zero retries |
| Archive watch Chromium, first launch and fresh API restart | 1 passed in 39.0 s and 1 passed in 13.9 s, zero retries |
| Genuine archive drill | Four executions, six as-of probes each, identical revision proofs and one alert per family |
| Local terminal | Read-only M5 smoke passed for EURUSD and GBPUSD |

Development checks exposed a shadowed clock function at publication, invalid source encoding in the replay selector, and a comparison that included two transient read counters in the expected saved payload. These were corrected. The initial recorded browser probe requested resume *before* the earliest saved checkpoint and correctly received HTTP 422. The final probe commits 14:45 after 15:00 from the existing 14:30 checkpoint, then verifies that reopening still defaults to 15:00 and explicitly selecting 14:45 returns its original receipt.

## Rollback and separate acceptance

Disable the watch through the current monitor API before downgrading, then stop all workers and revert the milestone commit. Keep both archive files, the local SQLite database, checkpoint table and immutable signal receipts. A pre-upgrade worker cannot interpret a historical watch configuration.

Month-scale executable replay remains open. The available recorded EURUSD bid/ask inputs cover 13:00–15:00 UTC on November 13, 2025. The remaining hours and November 1–12 and 14–30 lack local tick coverage. The November 2026 pair provides candles. Public historical admission remains capped at 160 cutoffs, historical intents retain AWAITING_EXECUTION_VALIDATION, and production recorded trading remains BLOCKED.
