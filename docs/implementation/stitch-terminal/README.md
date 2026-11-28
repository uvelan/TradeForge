# Market Terminal replay workspace

The desktop comparison shows the same saved 13 November 2026 Dukascopy M5 BID session, GBPUSD candle at 13:20 UTC and replay cutoff at 14:05 UTC (18:50 and 19:35 IST). EURUSD closed at 1.10513 at the selected open and GBPUSD closed at 1.32289. The chart uses two price scales in overlay mode. The planner and journal use the selected pane's bookmark.

| View | Original | Updated |
| --- | --- | --- |
| 1440 × 900 | [Before](before/terminal-1440.png) | [After](after/terminal-1440.png) |
| 1280 × 800 | [Before](before/terminal-1280.png) | [After](after/terminal-1280.png) |
| 390 × 844 | [Before](before/terminal-390.png) | [After](after/terminal-390.png) |

[Indicators](after/layers.png) · [Session settings](after/sessions.png) · [Decision drawer](after/decision.png) · [Restart receipt with IDs and both partition hashes](restart.json)

## Operator controls

| Existing action | Workspace location |
| --- | --- |
| Navigate to another page | Left navigation rail, expandable at desktop sizes |
| Select source, layout and timeframe | Instrument toolbar, under Chart settings on a phone |
| Inspect a primary, comparison or numbered candle | Evidence inspector, with separate candle and cutoff clocks |
| Inspect the chart feed, missing bars and partition checksums | Provenance |
| Configure indicator dependencies, period and timing | Indicators & layers |
| Configure local session hours and kill-zone overlays | Sessions |
| Draw, lock, move, edit, undo or redo | Drawing toolbox immediately before the chart in keyboard order |
| Select a range, freeze, resume or save position | Chart transport and saved range controls |
| Save a plan or record a pass | Record decision, attach chart capture, then create a plan or enter a pass reason |
| Return to a decision | Journal, chart capture details, Return to decision candle |
| Inspect SMT, H1, structure, CRT, zones or confluence | Evidence inspector |

The prototype used a disposable archive and the same chart capture and journal services as the local application. The original transport occupied the first desktop screen before the plot. The revised layout puts evidence alongside the chart. The drawing regression established that the toolbox also needs to precede the plot in keyboard order. The responsive review used the three actual screenshots above and keyboard checks for opening and dismissing the inspector and settings.

## Reproduce locally

From the repository root with the original provider CSV exports available:

```powershell
$env:TF_TERMINAL_SHOTS=(Join-Path $PWD 'storage/stitch-shots')
node frontend/capacity-e2e/verify-native-replay-browser.mjs --forex-decisions --workspace storage/stitch-replay --output storage/stitch-restart.json --csv storage/tf-i08e-source --bi5 storage/t06-input --api-port 8814 --ui-port 4814
```

The genuine journey exercises one linked plan, one direct pass, a layout change and a fresh browser and API. The standard archive replay tests use controlled responses for delayed observations, absent panes and failed captures. Candle evidence alone does not establish an executable bid or ask.

The [baseline operator checks](baseline-actions.json) used both desktop sizes. At each size the operator selected GBPUSD, enabled SMA, toggled a session overlay and recorded a pass. The [controlled restart checks](controlled-restart.json) cover a delayed peer, a frozen source error and a 5,000-bar window. These three passes created three journal entries and zero trades or paper events.

## Decision drawer re-review

Reproduced on `624338a562d74003a606bb5b49ed16a4cc0a0da8`. Each browser ran against a disposable workspace. The restart run used the registered Dukascopy 13 November 2026 EURUSD/GBPUSD M5 BID exports. The delay, error and numbered H1 cases used controlled inputs.

| Operator action | Reviewed head | Corrected observation |
| --- | --- | --- |
| Release A's pass response after attaching B | A cleared B's reason on success or displayed A's error on B | B retained its unsaved reason. The committed A refreshed the journal. Saving B added the second distinct entry. Editing the same pending draft also retained the newer text. |
| Capture GBPUSD H1 in numbered pane 2 over EURUSD M5 | Drawer said GBPUSD M5. Plan context used the primary feed and basis | Drawer and persisted plan said GBPUSD H1. Versions reopened the saved capture. The bookmark kept pane 2 and its candle while the envelope retained EURUSD M5. A separately controlled ASK peer detected a basis swap. |
| Retry Journal after HTTP 503 | No in-place retry. A prior error masked successful reads | Retry recovered populated and empty lists. The operator could open capture details. A delayed older read could not replace the new list. No writes. |
| Open at 390 px or shrink from desktop | Settled chart host ended at x=383, right scale ended at x=595 | Right scale occupied x=323–383. The visible last-price label read 1.10527. Escape returned focus to Inspect evidence. Resizing preserved the drawing, selected candle and cutoff. |

[Original and reopened IDs, archive pins and plot bounds](review/real.json) · [Controlled H1 plan, pass bookmarks and database counts](review/controlled.json) · [Fresh 390 × 844 screenshot](review/fresh-phone.png)

The genuine GBPUSD plan bookmarked the 13:20 UTC candle at 14:05 UTC, the EURUSD pass the same open at 14:10 UTC. After restarting both processes the two original IDs and both partition pins matched. The journey created two journal entries, one plan and zero paper events. The controlled Parquet run saved the 12:00 and 13:00 UTC H1 opens at a common 14:00 UTC cutoff. The H1 plan used `controlled-peer-h1 · BID`.

The prototype exercise held a committed response while navigating between captures. That identified the immutable capture as the boundary for draft state. Systematic debugging traced the phone overflow to the global 540 px data-table minimum on the chart renderer's table, and traced the incorrect plan context to the primary feed. Frontend design kept the retry beside the Journal alert and preserved both price scales on phones. The screenshot checks inspect the chart table bounds and painted last-price badge after layout settles.

Windows verification used 13 frontend unit tests, a production build, 106 standard Chromium tests, eight separately configured native drawing tests, ten backend snapshot tests and both archive journeys. The final populated/empty recovery assertions also passed separately. Initial archive harness runs timed out during optional live MT5 startup. The successful archive-only subprocess exercises the normal missing-MT5 path.

```powershell
.venv/Scripts/python -m pytest research_backend/tests/test_chart_snapshot_lanes.py -q
npm.cmd --prefix frontend test
npm.cmd --prefix frontend run build
npm.cmd --prefix frontend run e2e -- --retries=0
$env:TRADEFORGE_NATIVE_STORAGE=(Resolve-Path storage/review46-drawing-store)
npm.cmd --prefix frontend run e2e -- --config playwright.native-drawing.config.ts --retries=0
node frontend/capacity-e2e/verify-native-replay-browser.mjs --decision-review --workspace storage/review46-integration --output storage/review46-controlled.json --api-port 8833 --ui-port 4833
$env:TF_TERMINAL_SHOTS=(Join-Path $PWD 'storage/review46-shots')
node frontend/capacity-e2e/verify-native-replay-browser.mjs --forex-decisions --workspace storage/review46-real --output storage/review46-real.json --csv storage/tf-i08e-source --bi5 storage/t06-input --api-port 8832 --ui-port 4832
```

Rollback by reverting the review correction and rebuilding the frontend. The correction uses the existing journal, plan and snapshot schemas.
