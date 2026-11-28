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
