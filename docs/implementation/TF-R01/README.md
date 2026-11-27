# Windows-local capacity and recovery

Base `7d26bbcbd475c9b0550e0f5ac3a8ab8acf505c59`. Combined functional candidate `d4b4773ffb661ecb730af0fcc4a8cc0b0ec55bc5`. The changes instrument the benchmark and the browser test probe. The application, archive formats and trading policies have zero changed lines.

## Million-row audit — PASS

On Windows, both implementations independently processed 1,121,792 observations from 30 pinned MT5 files. All 720 hours and the ordered endpoints of all 45 open-session gaps matched. The 12,071 observations in eight modeled closed hours and ten missing open hours keep the capture **REJECTED before replay**. The independent verifier spent most time grouping observations, and the product audit spent most time in the bounded-read routine. Warm medians were 681.6 and 609.7 ms, respectively. Each read 67,324,814 logical bytes per trial. Fresh-process measurements used the Windows-managed filesystem cache. [Hardware, commands, cold/warm trials and methodology](AUDIT-CAPACITY.md)

## Recorded replay recovery — PASS on the observed interval

The Dukascopy control covers 13:00–15:00 UTC on November 13, 2025. The monthly BID-rise strategy qualified, entered at 1.16243 ASK on ordinal 6 and stopped at 1.16143 BID on ordinal 1,564. The three events and exact quote pins matched across fresh and warm uninterrupted runs, and across restarts. Strategy Lab was terminated with the position open at 13:10 UTC and again at the source-file boundary. Two concurrent duplicate deliveries at each checkpoint left 43 batch receipts and the same ledger SHA-256, `7a6795c12f0e0585e2f7ab85fdac682d8839f53f36a221dcd9140ba8d3c98254`. The run reopened 2,881 gap intervals. Quote observation is recorded and immediate delivery is modeled. November lacks 480 open hours. [Uninterrupted and resumed reference](replay-reference.json) · [Three-process browser receipt with exact quote pins](replay-restart.json)

## Chart workload

The controlled Chromium test exercises the real Market Terminal renderer and chart library with a deterministic DEMO response. Each pane has a distinct candle cadence and price sequence. The test verifies all plotted OHLC points in their named panes before each sample, places a horizontal level on the primary chart, pans and zooms, and checks that the visible range changes. Each size has 30 route unmounts, ten each at one, two and four panes. The first three per layout are warm-ups. Heap samples follow forced garbage collection with the chart unmounted. Frame intervals span pointer and wheel input through subsequent animation frames; they measure visible interaction cadence. Mount latency, peak mounted heap and GPU memory are unmeasured.

The terminal requests 600 bars per pane and the saved replay service bounds an explicit primary window to 600. The 5,000-bar response exercises renderer capacity in isolation. **End-to-end four-pane saved replay at 5,000 bars per pane remains UNVERIFIED.** The separate genuine-data browser run uses the two original BI5 files, checks displayed OHLC against provider ticks, and exercises replay and navigation. Its four-pane layout repeats EURUSD. It supplies genuine price evidence and is separate from the controlled multi-cadence load test.

## Reproduction

At 1536 × 900 CSS pixels and DPR 1 (96 CSS dpi), the four-pane renderer met the proposed 33 ms p95 for the measured gestures. Each row uses seven measured mount/pan/zoom/unmount cycles after three warm-ups. The nearest-rank p95 pools their frame intervals.

| Bars per pane | Panes | Frames | Median / p95 ms | Unmounted JS heap, first → last measured (bytes) |
|---:|---:|---:|---:|---:|
| 600 | 1 | 122 | 16.7 / 16.8 | 12,817,344 → 13,428,412 |
| 600 | 2 | 123 | 16.7 / 16.8 | 13,818,884 → 13,641,408 |
| 600 | 4 | 122 | 16.7 / 16.8 | 13,747,716 → 14,111,360 |
| 5,000 | 1 | 122 | 16.7 / 16.7 | 12,836,468 → 13,433,448 |
| 5,000 | 2 | 119 | 16.7 / 16.7 | 13,546,528 → 13,648,436 |
| 5,000 | 4 | 122 | 16.7 / 16.7 | 13,785,716 → 13,853,212 |

Each workload created and disposed 148 chart instances, with zero chart-owned subscriptions, observers, listeners, timers, animation frames or uncaught page errors remaining. The 0.1 ms p95 difference is inconclusive for a speed comparison. No application optimization was made. The GC measurements cover a short soak. [600-bar samples](chart-600.json) · [5,000-bar samples](chart-5000.json) · [Summary](chart-summary.json)

The combined candidate passed 1,238 backend tests with one skip and a Starlette dependency warning, 13 frontend unit tests, the production build, all 91 standard Chromium tests and both genuine-data Chromium journeys. Each full suite ran once. The focused capacity run passed at both sizes, and ran again after the standard suite cleared its nested output directory. The reproduction command below isolates that output. [Suite commands, exit codes and elapsed times](combined-verification.json)

### Delivery and review cost

Owner start 05:57:50.792 UTC. Worker task starts were 05:58:06, 05:58:22 and 05:58:31. All three had active turns concurrently for 197.7 seconds, summed over two intervals. This excludes the quota interruption. After redispatch the audit worker finished, the recovery worker completed its independent review, and the owner took over the chart harness. Worker final turns ended at 06:10:10 (audit), 06:08:24 (chart) and 06:12:57 (recovery). The critical path ran through the chart harness, the combined backend suite (788.3 seconds wall) and the isolated chart evidence rerun (59.0 seconds). Three development runs exposed the click-target, range-selection and pane-mount issues above. No comparable serial delivery or separate rework time counter exists.

The [sanitized usage receipt](usage.json) covers provider counters through 06:30:20 UTC. Total tokens were 29,474,888 across integration (11,216,281), audit (2,346,138), chart (1,369,592), recovery/review (3,388,041) and linked approval reviewers (11,154,836). Cached input and reasoning are already included in their parent totals. The receipt excludes subsequent handoff activity. Production churn was zero. Benchmark and browser-test code at the tested SHA added 360 lines and removed one. The subsequent 29-line Python utility summarizes the recorded samples independently of the application. Publication time, exact final head and final churn are recorded in the PR handoff.

### Local commands

From the repository root in PowerShell, keep the two original BI5 files in `storage/t06-input` and the original MT5 manifest and daily arrays under `storage/nov25/mt5`. The runners use disposable application storage.

```powershell
.venv/Scripts/python.exe docs/implementation/TF-R01/benchmark_mt5_audit.py storage/nov25/mt5/capture.json --output storage/r01/audit-samples.json
.venv/Scripts/python.exe -m scripts.verify_month_replay --output storage/r01/replay.json
node frontend/paper-e2e/verify-month-process-restart.mjs --reference storage/r01/replay.json --output storage/r01/restart.json
$env:TRADEFORGE_E2E_API_PORT='8784'
$env:TRADEFORGE_E2E_UI_PORT='4784'
npm --prefix frontend run e2e -- --config playwright.capacity.config.ts --retries=0 --output ../storage/r01/chart
npm --prefix frontend run e2e -- --config playwright.recorded.config.ts --retries=0
$env:TRADEFORGE_T10_SOURCE_STORAGE=(Resolve-Path storage).Path
$env:TRADEFORGE_T07_REAL_BI5=(Resolve-Path storage/t06-input/13h_ticks.bi5).Path
.venv/Scripts/python.exe -m pytest -q
npm --prefix frontend test
npm --prefix frontend run build
npm --prefix frontend run e2e -- --retries=0
```

The independent reviewer used `ponytail-review` to check timing boundaries, hourly parity, source identity, pane membership and resource cleanup. `systematic-debugging` traced development failures to an axis-canvas click, a range probe tied to chart creation order, and polling before secondary panes mounted. The final test selects the plotting cell, reads the primary range explicitly, and polls each named pane. This benchmark uses the existing UI. No design or interaction skill was required. Rollback is a revert of the benchmark PR; local archives, databases and immutable IDs require no migration. Historical admission remains at 160 cutoffs, intents retain `AWAITING_EXECUTION_VALIDATION`, and production recorded trading remains `BLOCKED`.
