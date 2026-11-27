# Drawings on native historical replay

PR #41 was merged with GitHub’s head guard. The verified merge on remote main was `7153f40f22d7f50529ef7c019921aa003a7ae6e7`. Its parents were `a3c9fe40e18b43f09afffdc20731e40381dc5c50` and approved head `79bae188e45e5ca632c371ce676b10c46b69a074`. [PR #41 push checks](https://github.com/uvelan/TradeForge/actions/runs/45965221708) · [PR #41 pull-request checks](https://github.com/uvelan/TradeForge/actions/runs/45965228860)

## Starting behavior

On the merged application, the primary pane accepted pointer placement, hit selection after pan/zoom, a direct anchor edit and undo. Selecting companion pane 2 in a genuine four-pane replay and clicking the two anchor locations produced no drawing POST. In a separately imported four-pane controlled fixture, pane 4’s book stayed at revision zero. ChartView displayed replay candles without the drawing callbacks provided to the primary chart. A keyboard operator could arm the tool in the toolbox but could not focus the plot to commit an anchor.

## Data and scope

The genuine drawing journey uses checksum-pinned local Dukascopy EURUSD and GBPUSD BID CSV candles. In the interval from 2026-11-12 08:35 UTC to 2026-11-17 22:00 UTC (exclusive end), the verified panes contain 997 EURUSD M5, 5,000 GBPUSD M1, 998 GBPUSD M5 and 5,000 EURUSD M1 bars. Delivery is modeled at candle close. The capacity fixture separately uses controlled BI5 imports and four distinct market contexts with 5,000 bars each. [Source hashes and aggregation convention](../independent-acceptance.json)

A drawing book belongs to its source, symbol and timeframe. Reopening a saved chart reads the latest version of its market’s book. The replay’s candle capture and the drawing book have separate identities. Edits and undo append book revisions. The existing API accepts up to 100 drawings per book.


## Using the drawing toolbox

1. Resume the saved replay in Market Terminal. Select the inspection pane and check the symbol and timeframe in the drawing toolbox.
2. Choose the shape and activate **Draw**. Click the anchor locations on that pane, or tab to its plot, move the target with the arrow keys and press Enter for each anchor. Escape cancels the draft. The legacy quick-level rail continues to target the primary pane.
3. Select a supported drawing on the plot or choose it in the toolbox. Drag an editable handle, or choose **Set anchor** and place it again. **Undo drawing edit** writes another revision. The API acknowledges each completed edit automatically. Use **Save replay position** to persist the chart cutoff and selected candle.
4. Resume the saved replay after restarting the API and browser. Select the original pane to inspect the drawing again. Two panes with the same source, instrument and timeframe share persisted drawing versions. Each pane owns its selection and edit history. Another edit to the same book invalidates an older undo stack.

In native replay, the canvas draws a shape when its anchor times are present among that pane’s available candles. The toolbox lists the user-authored market book across cutoffs. Its annotations are separate from historical strategy decisions, fills and source candles.

## Acceptance

The combined functional and test candidate was `1f320ddf74367e7c6669c47204b4c5a3ac1c13b7`. In the integrated run, the primary horizontal line snapped to the verified EURUSD M5 high of **1.10065** at 2026-11-17 21:10 UTC. The GBPUSD M1 trendline kept its ID through the anchor edit (revision 2), undo (revision 3) and fresh-process reopen. The held response came from the EURUSD M1 book while GBPUSD M1 was active. The GBPUSD selection and its source pin survived the release. A read-only SQLite comparison after shutdown matched four original versions, the edit, and the undo. [Integrated journey and pins](integrated-journey.json) · [SQLite revision check](immutable-revisions.json) · [Worker baseline](genuine-baseline.json)

The controlled duplicate-market regression in `frontend/e2e/archive-replay.spec.ts` checks both pane-local selection and shared-version safety. Its delayed candle first becomes drawable at the recorded availability. The keyboard and pointer journeys are in the [operator verification and screenshots](operator-verification.md).

Across the controlled 25- and 250-drawing tiers, the pooled pan/zoom p95 values were 16.70 ms at both sizes. The 250 tier rendered 63, 63, 62 and 62 drawings. The populated companion took 465.20 ms p95 to show the selected drawing and 1,187.70 ms p95 to acknowledge the save. The price preview reached the canvas stroke call in 1.70 ms p95. Each of the four 250-item exploratory writes returned HTTP 422 and preserved the previous book. [Benchmark method, seven measured trials per tier, before/after comparisons and cleanup](capacity.md)

## Verification and rollback

The implementation worker used `systematic-debugging` to trace the companion failure to missing plot callbacks. The interaction worker used `prototype` to check keyboard placement and cancellation, `frontend-design` to integrate those controls with the existing toolbar, and `impeccable` to check focus, prompt placement and pane identification in Chromium. The independent worker used `ponytail-review` to verify book ownership, counts, teardown and timing boundaries. The browser screenshot and capacity reports above record the corresponding checks.

From the repository root, use disposable stores and the locked Windows dependencies. The complete native import setup is in the [parent runbook](../README.md#local-commands).

~~~powershell
$env:TRADEFORGE_T10_SOURCE_STORAGE=(Resolve-Path storage).Path
$env:TRADEFORGE_T07_REAL_BI5=(Resolve-Path storage/t06-input/13h_ticks.bi5).Path
.venv/Scripts/python.exe -m pytest -q
npm --prefix frontend test
npm --prefix frontend run build
$env:TRADEFORGE_E2E_API_PORT='8796'
$env:TRADEFORGE_E2E_UI_PORT='4796'
npm --prefix frontend run e2e -- --retries=0
$env:TRADEFORGE_E2E_API_PORT='8797'
$env:TRADEFORGE_E2E_UI_PORT='4797'
npm --prefix frontend run e2e -- --config playwright.recorded.config.ts --retries=0
node frontend/capacity-e2e/verify-native-replay-browser.mjs --drawings --workspace storage/drawing-owner/genuine --output storage/drawing-owner/drawing-journey.json --csv storage/tf-i08e-source --api-port 8798 --ui-port 4798
node frontend/capacity-e2e/verify-native-replay-browser.mjs --workspace storage/drawing-owner/browser --output storage/drawing-owner/restart.json --bi5 storage/t06-input --csv storage/tf-i08e-source
~~~

All three worker turns overlapped for approximately 20m 31s. Two incomplete initial benchmark attempts exposed browser resource-timing buffer exhaustion and a comparison of screen coordinates across different autoscale ranges. A third pass measured the upper bound imposed by the test driver. The final paired runs measured selection in a prearmed animation-frame observer and the drag preview in the canvas stroke callback. The final baseline ran for 97s and the candidate for 114s. No comparable serial delivery run was taken. [Per-agent counters with sampling cutoff](usage.json)

To roll back locally, run the application at the verified PR #41 merge and restart the API and browser. For a later merge to main, revert this drawing PR’s merge commit with `git revert -m 1 <merge-sha>`. Keep the SQLite databases and archives. This change uses the existing drawing schema and optimistic revisions.

Public strategy admission remains at 160 cutoffs, historical intents retain `AWAITING_EXECUTION_VALIDATION`, November MT5 replay remains `REJECTED`, and production recorded trading remains `BLOCKED`.
