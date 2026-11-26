# Inspect a completed H1 Candle Range Theory reference on an M5 replay

## Operator workflow

In Market Terminal, open a saved Dukascopy EURUSD or GBPUSD M5 BID replay. Freeze the chart, expand **Replay Candle Range Theory**, select the chart pane and choose the UTC opening time of a completed H1 reference. Open **H1 source candles** to inspect the twelve pinned M5 bars. **CRT lifecycle revisions** shows what the producer published; **CRT lifecycle source candles** links the relevant observations to their revisions. The pane can have a different arrival schedule from its peer.

The chart checkpoint controls the replay. The inspector also supports a second-precision UTC probe at or before that checkpoint. It labels the chart clock and the evidence clock separately. Rewind before formation to see that the selected family had no available revision; advance to recover its existing ID. A reference becomes available at the later of its hour close and the availability of its last required child.

The retained version-1 rule confirms a sweep and reclaim only when the same completed M5 candle sweeps exactly one side and closes back inside. A later inside close after a candidate does not establish a reclaim. The default candidate lifetime is three subsequent accepted observations. A candle sweeping both sides supplies no intrabar ordering. The market calendar is New York forex weekly. This research view uses M5 candle-level evidence and makes no claims about executable tick prices.

## Inputs

13 November 2026, 00:00â€“22:00 UTC, 264 M5 BID bars per instrument. Each CSV was independently compared with its checksum-verified Parquet partition. The provider feed is `dukascopy-web-m1-v1`; archive nominal candle close is used for modeled availability. Controlled delayed arrivals and missing observations have separate receipts.

| Instrument | Provider CSV SHA-256 | Catalog dataset ID | Parquet SHA-256 |
|---|---|---|---|
| EURUSD | `bc2e49a78edad37b73ac1bcdc006892f54aab60c3e769ec837998773cb5960f6` | `ca01e9ac45346d7c9fdfdf4263085831a07735bc16158901c9a14187f77aaf4e` | `fa2af192aa86dc41bb20df51dffe95c29bc48cdf2fe38b643f699985c6b0e09d` |
| GBPUSD | `a2b4947d5e79198920589263a9626e476012650a09d8ba52386e244ab78c1928` | `1cc6acc1b358b3b043438badc69f72413827a8cd5dad379de8e41dd9aaaecfd0` | `aed73f09d78bb3df29d3678e0ace1f00949a673a920ed22dcd6d23cb7a173d02` |

## Genuine reference comparison

Independent Decimal calculations group the CSV candles by UTC hour and compare each source row with its registered Parquet. Both panes had 22 complete hourly references. The public list and selected-receipt results were probed one second before and exactly at each material revision. A revision can keep the same state and acquire a new reason and ID.

| 13:00â€“14:00 UTC family | Prior second | Expected and observed at availability |
|---|---|---|
| EURUSD, low 1.10486 / high 1.10693 | 13:59:59 still forming | 14:00 active; 14:25 low swept and reclaimed; 14:30 outside close; 14:55 midpoint; 15:30 invalidated by close |
| GBPUSD, low 1.32238 / high 1.32503 | 13:59:59 still forming | 14:00 active; 14:55 high swept and reclaimed; 15:10 midpoint; 15:45 opposite boundary reached |

| Session observation | EURUSD | GBPUSD |
|---|---:|---:|
| Material revisions | 73 | 67 |
| Same-candle reclaim | 13 | 6 |
| Sweep without same-candle reclaim | 6 | 14 |
| Midpoint reached | 7 | 4 |
| Opposite boundary reached | 4 | 4 |
| Close beyond invalidation | 9 | 2 |
| New sweep extreme / outside close | 3 / 1 | 0 / 0 |
| Candidate expiry / reference cap expiry | 6 / 2 | 14 / 1 |
| Both sides swept, pre-reclaim opposite sweep, unobserved opposite gap, reference-age expiry, straddling observation, missing or unverifiable continuity | 0 each | 0 each |

## Controlled continuity and expiry

On the delayed paired replay, the reference that closed at 14:00 first became available at 14:00:17. The EURUSD successor opened at 14:20, closed at 14:25 and arrived at 14:25:17. At 14:25:16 the earlier revision was still current. At 14:25:17 the receipt became INVALIDATED and linked the 14:10 and 14:20 witnesses to the same revision, with missing interval [14:15, 14:20). The GBPUSD family reached the opposite boundary. These prices and delivery delays are controlled fixtures.

The clean weekend left the range active. The mixed weekend identified two missing intervals, Friday [21:55, 22:00) and Sunday [22:00, 22:05), with the Friday 21:50 and Sunday 22:05 observed opens. The first eligible evidence of that break was Sunday 22:10:17. No revision was published for the scheduled closure alone. Both-side sweep, candidate expiry in both directions, price gap beyond the opposite boundary, unavailable or straddling reference, reference age, capacity replacement and unverifiable closure were also exercised. All 42 controlled revisions matched their independent expectations across 15 scenarios. [Source pins, reason counts, exact evidence and before/at-cutoff comparisons](../evidence/T10-CRT-REPLAY/oracle.json)

## Runtime measurements

| Operation | Samples per build | Baseline median / p95 ms | Candidate median / p95 ms | Change in median / p95 ms |
|---|---:|---:|---:|---:|
| cold-only | 8 | 62.18 / 84.73 | 60.88 / 73.74 | -1.31 / -10.99 |
| cold-with-overlays | 8 | 96.99 / 117.05 | 97.97 / 111.37 | +0.98 / -5.68 |
| collapsed_step | 30 | 45.95 / 52.68 | 49.62 / 110.60 | +3.67 / +57.92 |
| collapsed_rewind | 30 | 44.29 / 65.32 | 36.89 / 52.58 | -7.40 / -12.73 |
| EURUSD_new_list | 30 | Unavailable on base | 46.31 / 66.48 | First measurement |
| EURUSD_new_selection | 30 | Unavailable on base | 50.90 / 102.33 | First measurement |
| EURUSD_new_list_and_selection | 30 | Unavailable on base | 94.84 / 139.36 | First measurement |
| GBPUSD_new_list | 30 | Unavailable on base | 53.59 / 98.93 | First measurement |
| GBPUSD_new_selection | 30 | Unavailable on base | 48.26 / 96.26 | First measurement |
| GBPUSD_new_list_and_selection | 30 | Unavailable on base | 74.56 / 112.07 | First measurement |

The separate sparse GBPUSD wick inspection (30 samples) previously measured 31.811 ms p95. This run measured 16.524 ms on the starting head and 25.604 ms on the candidate. The maximum candidate sample was 30.094 ms.

Peak warm backend process working set for common requests was 94,288 KiB on the starting head and 95,132 KiB on the candidate. Candidate peak after CRT inspection was 126,088 KiB. These figures exclude the browser. For either a list or an inspection, EURUSD verified 10,538 bytes and GBPUSD 10,280 bytes; both decoded 264 archive rows. Windows filesystem cache was uncontrolled. Cold means a fresh API process, excluding startup. The collapsed backend workload steps the existing chart overlays without fetching CRT.

Windows 11 build 26200, Core i7-9700K, 16 GB installed, Python 3.13.14, Node 24.14.0. The API benchmark runs the same actions serially on the two builds. These measurements describe this host and the pinned input. [Backend samples](../evidence/T10-CRT-REPLAY/benchmark.json) · [Sparse samples](../evidence/T10-CRT-REPLAY/sparse-final.json)

The common step p95 went from 52.68 to 110.60 ms in the first comparison, which overlapped worker verification. After all suites and workers finished, a second paired run measured 34.352 versus 34.376 ms on that same operation. Cold open was 56.464 versus 56.274 ms median. For the new inspector, EURUSD list / selection p95 was 27.204 / 29.373 ms and GBPUSD 27.829 / 29.686 ms. Peak backend working set with inspection was 125,592 KiB. Common-path speed improvement is **inconclusive**; the earlier tail increase did not recur in the isolated repeat. First-time CRT operations have a measured baseline. [Complete post-suite paired comparison](../evidence/T10-CRT-REPLAY/benchmark-idle.json)

## Browser observations

The combined browser journey checked the display immediately before and at all nine transitions of the two genuine 13:00 families, and eight transitions of the controlled pair. A stale response was released after changing panes. Reversing the chart order preserved each instrument's family, and reopening in a new Chromium process against a restarted API restored both selections and the earlier inspection cutoff. The original twelve child IDs and terminal revision IDs matched the public API receipts. Eight browser phases passed with zero retries. [Browser receipts, before/at probes and timings](../evidence/T10-CRT-REPLAY/browser.json) · [Genuine source table](../evidence/T10-CRT-REPLAY/genuine-sources.png) · [Controlled source table](../evidence/T10-CRT-REPLAY/controlled-sources.png)

Genuine source-table expansion measured 88.5 ms median and 106.2 ms p95 over 20 repetitions. The inspector mounted zero source rows while closed and twelve when expanded, with no additional CRT request. The controlled fixture measured 84.55 / 116.2 ms. Single genuine browser observations for the inspector versus the existing indicator library at the same 15:50 chart checkpoint were 203.3 / 343.5 ms for the list, 102.8 / 128.6 ms for selection, 308.3 / 318.4 ms for rewind, and 488.7 / 693.2 ms for cold resume. The older library reselects locally; it has no comparable source-table action. These single samples do not establish a speedup.

## Integration and reproduction

The starting PR head was `a8c9db640ae2c1845f501ed5e31ac3ef9ae352b9`. All combined checks used `9d8653f0ccb9e7b36a01a021b78e0351a29ad312`; the later review commit contains evidence and documentation. The three workers were dispatched before waiting and were all running in the first status snapshot. Their isolated sibling worktrees used separate disposable stores. UTC times below are from the runtime records.

| Worker and model (high) | Dispatch / start / finish UTC | Source commit | Result |
|---|---|---|---|
| `/root/crt_backend` · GPT-6.2 Astra | 10:44:56 / 10:44:58 / 10:53:58 | `fc132d30ea4c28d6502223094f24eebaec42c6e0` | Pinned read-only projection; 81 focused checks. |
| `/root/crt_chart` · GPT-6.2 Sol | 10:45:09 / 10:45:12 / 11:05:14 | `73ef0c9598110bb7249cf1724c3203ed9adc7d42` | Inspector, true process restart, stale response, eight Chromium phases. |
| `/root/crt_reference` · GPT-6.2 Sol | 10:45:20 / 10:45:22 / 10:57:35 | `31fea7137d0d13926dd167c20345ed5a1ee40de7` | Independent CSV reference and paired benchmark; development receipt at `c377d5983bc233abe1ae7f9158272912e6f302df`. Combined oracle receipt supersedes that measurement. |

Owner start was 10:44:46 UTC. The first integration pick was at 10:53:53 and combined verification started at 11:01:45. The three workers overlapped for 516.194 seconds. Three development failures were observed: numeric zero representation in the canonical rule hash, assumptions about calendar serialization and shard order in the reference harness, and a browser measurement trying to advance an already resumed final checkpoint. Corrections preserved the canonical producer and exercised the original behavior. The chart measurement also gained 20 repeated source expansions. The critical path ran through chart integration, integrated measurements and the full backend suite. No comparable serial-delivery benchmark or billed cost is available.

Use the following commands from the repository root on Windows with the verified local files in `storage`. The compiler, test runner and acceptance harnesses use the project's installed dependencies.

```powershell
git worktree add --detach ../TradeForge-t10-crt-base a8c9db640ae2c1845f501ed5e31ac3ef9ae352b9
.venv/Scripts/python.exe -m pytest -q research_backend/tests/test_replay_crt.py research_backend/tests/test_crt.py research_backend/tests/test_replay_zone_continuity.py
.venv/Scripts/python.exe -m scripts.verify_t10_zone_compatibility --suite crt --source-storage storage --output storage/crt-fingerprints.json
.venv/Scripts/python.exe -m scripts.verify_t10_crt --source-storage storage --output storage/crt-oracle.json
.venv/Scripts/python.exe -m scripts.benchmark_t10_zone_gaps --output storage/crt-sparse.json
.venv/Scripts/python.exe -m scripts.benchmark_t10_crt --source-storage storage --baseline-repo ../TradeForge-t10-crt-base --output storage/crt-performance.json
.venv/Scripts/python.exe -m scripts.verify_t10_crt_browser --source-storage storage --output storage/crt-browser --api-port 8732 --ui-port 4732
.venv/Scripts/python.exe -m pytest -q
npm --prefix frontend test
npm --prefix frontend run build
$env:TRADEFORGE_E2E_API_PORT='8730'
$env:TRADEFORGE_E2E_UI_PORT='4730'
npm --prefix frontend run e2e -- --retries=0
```

## Combined verification and delivery

| Check on the tested SHA | Observed |
|---|---|
| Focused backend | 55 passed |
| Full Windows backend, once | 1,166 passed, 3 skipped in 706.73 s; one FastAPI TestClient deprecation warning |
| Recorded numerical tests | Both archive-dependent skips passed in 2.11 s when the storage path was supplied; the third full-suite skip requires a POSIX FIFO |
| Frontend unit / production build | 13 passed; TypeScript and production build passed |
| Standard Chromium | 88 passed in 7.9 minutes, zero retries |
| Integrated CRT Chromium | 8 phases passed in 108.12 s, zero retries |
| Compatibility | All eight original indicator response fingerprints and lengths match |

The full-suite shell omitted `TRADEFORGE_T10_SOURCE_STORAGE`. The two skipped symbol cases were rerun with the verified storage path. No full suite needed repeating. The build emitted existing bundler directive warnings. [Commands, times and exit codes](../evidence/T10-CRT-REPLAY/verification.json) · [Original indicator fingerprints](../evidence/T10-CRT-REPLAY/legacy-before.json)

Production churn was +374 / −7 lines; tests, reference scripts and benchmarks were +1,768 / −3. The attached delivery receipt counts evidence text and binary files separately and records the actual remote verification time. Correctness improved from an unavailable dedicated replay route to a verified operator journey. Efficiency comparisons for delivery and token use remain unmeasured without a comparable serial run. Input includes cached tokens, and output includes reasoning. The aggregate adds input and output once. The portable final usage receipt in the PR conversation identifies the sampling cutoff; billing and opaque provider retry counts are unavailable.

## Rollback

Stop the local API and frontend, check out `a8c9db640ae2c1845f501ed5e31ac3ef9ae352b9` in a separate worktree, and restart the same local profile using that revision. Keep the SQLite databases, archives, checkpoints and browser profile. The CRT inspector reads saved sessions and requires no database migration. Confluence remains a separate T10 gate. Public historical admission stays at 160 cutoffs, historical intents retain `AWAITING_EXECUTION_VALIDATION`, and production recorded trading remains `BLOCKED`.
