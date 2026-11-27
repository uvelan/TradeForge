# Native four-pane saved replay on Windows

PR #40 was merged with GitHub's expected-head guard at `9db18748765736d8ed326f34af718359e44fed33`. Both Windows jobs passed. The freshly fetched merge and branch base are `a3c9fe40e18b43f09afffdc20731e40381dc5c50`; its first parent is `7d26bbcbd475c9b0550e0f5ac3a8ab8acf505c59`, its second the reviewed head. [Push check](https://github.com/uvelan/TradeForge/actions/runs/45952450340) · [PR check](https://github.com/uvelan/TradeForge/actions/runs/45952491895)

## Operator journey

1. In Market Terminal choose a saved candle source. Select the primary symbol, timeframe and chart layout; choose each companion's instrument and timeframe. Enter the UTC bounds and load the saved range.
2. Choose **Freeze range for replay**. Inspect the per-pane requested interval, eligible slots, returned bar count and gaps. The ordinary preview and **Bar replay** quick action retain their earlier limits. A preview error can coexist with an eligible archive replay; a rejected replay displays its own reason.
3. Choose **Inspection pane**. Use the **Inspect candle** slider and arrows to select a historical candle. Open **Selected candle source** for its availability convention, recorded availability and contributing partition IDs and SHA-256s. The timeline moves independently of this selection. Rewinding past its availability hides its prices.
4. Choose **Save replay position**. After restarting the local API and browser, **Resume saved replay** restores the frozen layout, cutoff, selected candle and pins, including when current layout preferences differ.

## Boundaries and compatibility

For malformed SQLite metadata, the [partition diagnostic matrix](malformed-partitions/README.md) records primary and companion HTTP outcomes, exact selection boundaries, file-open checks and the restart regression.

POST `/research/api/replay/sessions` accepts an optional `window_bars` from 1 to 5,000 per saved-source pane. Omitting it retains the existing capture path. Previously persisted session payloads keep their IDs. Explicit over-limit ranges reject before insertion. The archive path reads from a consistent catalog snapshot, authenticates the Parquet bytes it decodes, rejects contradictory overlaps, and retains original contributors to an identical overlap. Each distinct pane has separate file, row and byte limits. Repeated pane requests share verified work within the request. Native playback follows availability while candles remain plotted by UTC open time. A gap remains visible; the chart cannot attest provider completeness.

The 160-cutoff strategy-evaluation cap and the smaller analytical overlay limits are independent of chart capacity. Historical intents retain `AWAITING_EXECUTION_VALIDATION`; production recorded trading is `BLOCKED`. The November 2025 MT5 tick capture remains rejected on its recorded clock evidence.

## Before and after

| Source and operator action | Observed | Verdict |
|---|---|---|
| Reviewed baseline, 601 closed M5 candles | Preview returned the latest 600; freezing returned HTTP 422. A shortened 600-bar request succeeded. | Reproduced |
| Controlled BI5 imported through the ordinary API | EURUSD M5, GBPUSD M1, USDJPY M5 and XAUUSD M1 each plotted 5,000 exact candles. A 5,001-bar range was rejected. Each lane used 18 pinned partitions. | PASS at native four-pane capacity |
| Genuine Dukascopy UTC BID CSVs for EURUSD and GBPUSD | In the shared window from 2026-11-12 08:35 to 2026-11-17 22:00 UTC (end exclusive), the four named panes matched 997 EURUSD M5, 5,000 GBPUSD M1, 998 GBPUSD M5 and 5,000 EURUSD M1 rows. M5 references use complete M1 children. | PASS at these counts. Four genuine, distinct 5,000-bar lanes remain unverified |
| Genuine November 13, 2025 Dukascopy EURUSD | Original two BI5 hours reproduced 24 M5 and 120 M1 BID candles. | PASS |
| Missing, delayed and overlapping observations | Nonaligned requested bounds excluded incomplete slots. The later arrival appeared exactly at its cutoff. Identical overlaps retained both source pins. A terminal clock step exposed an eligible peer after the final primary arrival without creating a primary candle. | PASS |
| Keyboard and fresh-process recovery | Opened, selected XAUUSD M1 at UTC epoch 1713431160, bookmarked and saved. Fresh API and Chromium recovered step 4,999 (zero-based), the selected candle and archive pins. Rewinding to bookmark 602 hid its OHLC; returning to the final cutoff recovered it. | PASS |

The 16 source CSV hashes, two original BI5 hashes, session IDs, per-pane proof digests and restart receipt are in [independent acceptance](independent-acceptance.json). The oracle decoded the inputs independently and checked OHLC, open time, availability and membership in the recorded partition. Candle delivery in the genuine fixtures is modeled at nominal close. The delayed-arrival fixture is controlled.

## Windows performance

On the PR #40 Windows host (i7-9700K, 16 GiB RAM), Chromium ran at 1536 × 900 CSS pixels and DPR 1. The saved replay had one horizontal annotation and 20,000 source-verified bars across four panes. Each trial opened the saved session, waited for the HTTP response, verified every series in its named pane and allowed two animation frames before stopping the load timer. The pan/zoom timer used the PR #40 gesture and excluded the separate open and verification interval. Three warm-ups preceded 27 measured native trials. The baseline used the PR #40 intercepted four-pane workload with three warm-ups and seven measured trials on the same machine.

| Measure | PR #40 remeasurement | Native replay |
|---|---:|---:|
| Verified saved replay open, median / p95 | Unavailable at 5,000 bars | 1,775 / 2,015 ms |
| Four-pane gesture frame p95, first seven measured trials | 16.8 ms | 16.7 ms |
| All measured gesture frames, median / p95 | 16.7 / 16.8 ms (117 intervals) | 16.7 / 16.7 ms (469 intervals) |
| Post-GC unmounted JS heap, first → last measured trial | 13.14 → 13.21 MiB | 17.60 → 18.23 MiB |

**Frame target met** for the declared workload. The 0.1 ms difference does not establish a speedup. Native peak working sets were 578.52 MiB for the renderer, 187.02 MiB for the API and 91.36 MiB for the browser process. These are separate Windows process-lifetime peaks; their timestamps can differ. Memory cost is higher than the renderer-only control on the comparable unmounted-heap measure. Native teardown disposed all 240 chart instances over 30 cycles. No chart-owned subscriptions, observers, listeners, timers, frames or uncaught page errors remained. The 33 ms target applies to gestures. [Raw native trials](native-frames.json) · [Raw baseline trials](baseline-frames.json) · [Quantiles and process counters](performance.json)

## Verification and delivery

The full local suites ran once on combined functional commit `a77cf74d64681c616ae1ff9a5adfa0868de7376a`. Backend: 1,256 passed, one POSIX-only FIFO fixture skipped, one dependency deprecation warning, 889.73 seconds pytest time. Frontend: production build passed, 13 unit tests passed, 94 standard Chromium tests passed with zero retries, and both existing genuine-data browser journeys passed. The new clean-profile journey separately restarted the services and verified its saved receipt. [Commands and elapsed times](verification.json)

All three worker turns overlapped for 32 minutes 30 seconds. Work started at 06:36:49 UTC, with separate worktrees and runnable checkpoints while GitHub finished PR #40's required checks. Rework included correcting nonaligned range ends, a sparse primary hiding a later peer, and analytical pane choices after restoring a different layout. The API worker's terminal-clock follow-up turn took 3 minutes 17 seconds. Browser harness development hit a too-short startup deadline and a keyboard select/wait race; the final standalone journey passed from clean storage. The critical path then ran through the final native measurement and the 14-minute-55-second full backend command. Neither a comparable serial run nor separately attributable aggregate rework time was measured.

At the combined-tested SHA the production diff was +829/−167 lines and the test, benchmark and verification-harness diff was +1,483/−0. Later changes are evidence, an exercised receipt summarizer and the feature matrix. The [sanitized per-agent usage receipt](usage.json) gives model, cached and uncached input, output, reasoning, and totals through 07:35:25 UTC. The observed total including linked approval reviewers was 120,994,633 tokens. Cached input and reasoning are counted once. The PR handoff identifies the final head and wall time through remote verification.

## Local commands

| Lane | Runnable checkpoint | Skill, decision and verification |
|---|---|---|
| Replay API | `47d0c88`, followed by `3dd0c3b`, `d0b0b47`, `21835d5`; archive capture, evidence readers and backend tests | Backend review located the primary and companion cutoff disagreement and added an explicit terminal step. `research_backend/tests/test_native_replay_window.py` checks bounds, late arrivals, exact partition membership, compatibility, bounded reads and terminal checkpoints. |
| Operator UI | Local disposable prototype `9c21857`; implementation `f70637e`, `6fa157b`, `a1d39bc`; Market Terminal, chart components and browser specs | `prototype` separated the selected candle from the replay clock; keyboard walkthroughs covered missing and delayed data. `frontend-design` integrated the action beside the existing UTC range controls. `impeccable` reviewed visible coverage, pane labels and focus; `frontend/e2e/archive-replay.spec.ts` verifies partial coverage, stale responses and restored evidence panes. |
| Independent acceptance | `df366e7`, followed by `a1332e6` and `a77cf74`; independent oracle and native Chromium journey | `systematic-debugging` reproduced the 601st-bar rejection and out-of-order delivery failure. `ponytail-review` found recovered analytical pane menus still followed live layout preferences. The focused regression and fresh-browser review confirmed the fix. |

Run from the repository root with the locked Windows environment. Use a disposable directory for application data. The named genuine sources remain local.

```powershell
.venv/Scripts/python.exe scripts/verify_native_replay.py inventory --csv storage/tf-i08e-source --output storage/native-run/inventory.json
node frontend/capacity-e2e/verify-native-replay-browser.mjs --workspace storage/native-run --output storage/native-run/browser.json --bi5 storage/t06-input --csv storage/tf-i08e-source
```

For the timed workload, run the local application API on a disposable store, then prepare the controlled BI5 fixture through it. Close that API before starting the benchmark. The benchmark launches its own API against the same store. No candle HTTP response is intercepted.

```powershell
$env:TRADEFORGE_STORAGE=(New-Item -ItemType Directory -Force storage/native-run/benchstore).FullName
$env:TRADEFORGE_DATA_DIR=Join-Path $env:TRADEFORGE_STORAGE research
# Terminal 1
.venv/Scripts/python.exe -m uvicorn tradeforge.api:app --host 127.0.0.1 --port 8790
# Terminal 2, from repository root
.venv/Scripts/python.exe scripts/verify_native_replay.py prepare --api http://127.0.0.1:8790/research --output storage/native-run/fixture.json
$env:TRADEFORGE_NATIVE_FIXTURE=(Resolve-Path storage/native-run/fixture.json).Path
$env:TRADEFORGE_NATIVE_STORAGE=(Resolve-Path storage/native-run/benchstore).Path
$env:TRADEFORGE_E2E_API_PORT='8791'
$env:TRADEFORGE_E2E_UI_PORT='4791'
npm --prefix frontend run e2e -- --config playwright.native-capacity.config.ts --retries=0 --output ../storage/native-run/performance
```

Rollback by reverting this PR's merge commit with `git revert -m 1 <merge-sha>` and restarting the local application. Keep the databases and original archives. The previous application reads pre-existing legacy sessions. Retain a workspace backup if the newer native checkpoints need to be reopened with this revision.
