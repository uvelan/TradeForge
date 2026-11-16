# TF-D01 PR #1 review follow-up

[Review comment](https://github.com/uvelan/TradeForge/pull/1#issuecomment-6898179451) · Reviewed revision 3940724c1eee5c5c7ba02e620324e345675c2e90

| Review finding | Verification |
|---|---|
| Persisted anchor editing | Chromium places and selects trendlines, rectangles, and risk/reward drawings via their strokes. It edits each anchor in turn and checks that only that anchor's timestamp and price move, each save advances the revision once, IDs and item count are stable, and the exact scene returns after reload. |
| Both risk/reward directions | Tests capture a canvas preview for long and short placements, reject a wrong-side target during creation, and check ordered prices and durable storage. The original implementation also sent an invalid persisted edit to the server, then closed the edit following HTTP 422. The fix checks the same ordering locally before saving and keeps the anchor armed. The test asserts zero writes for the invalid edit, one write for a valid correction, and persistence of both drawings after reload. |
| Refresh and StrictMode remount | With real pointer ownership, the browser refreshes candles while a drag is active. The old host detaches, capture clears, and its pointer-cancel listener is removed. A new gesture in the same scope saves once. During a second in-progress gesture, switching from M5 to M15 releases capture and saves to neither scope. The new M15 chart then accepts a drawing, with one POST per intended completed action. |
| Fullscreen and resize | Chromium enters and exits real DOM fullscreen with a GBPUSD comparison on the left axis, resizes the viewport, and places horizontal lines. The persisted timestamp matches the primary candle identified by the rendered OHLC legend. A canvas stroke probe checks that the persisted price projects back to the clicked plot row. The initial pixel probe also exposed a test issue: opening selection controls after save shifts the plot's screen position. The final assertion uses the plot-relative row. No fullscreen production change was needed. |

## Run locally on Windows

From the repository root:

    .\.venv\Scripts\python.exe -m pytest -q
    cd frontend
    npm run build
    npm test
    $env:TRADEFORGE_E2E_API_PORT='8098'
    $env:TRADEFORGE_E2E_UI_PORT='4198'
    npm run e2e

The local runs passed 77 Python tests, 4 Vitest tests, TypeScript and production build, and 19 Chromium tests. Existing SMT, replay, snapshot, chart-linking, and saved simulation cases remained green. [Backend](evidence/TF-D01-review/backend.txt) · [Frontend](evidence/TF-D01-review/frontend.txt) · [Chromium](evidence/TF-D01-review/browser.txt)

Selected Windows screenshots: [invalid-edit reproduction](evidence/TF-D01-review/invalid-edit-before.png), [trendline edit](evidence/TF-D01-review/trend-edited.png), [rectangle edit](evidence/TF-D01-review/rectangle-edited.png), [risk/reward edit](evidence/TF-D01-review/risk-reward-edited.png), [long preview](evidence/TF-D01-review/long-preview.png), [short preview](evidence/TF-D01-review/short-preview.png), [scope remount](evidence/TF-D01-review/scope-remount.png), [fullscreen](evidence/TF-D01-review/fullscreen-placement.png), and [resized plot](evidence/TF-D01-review/resized-placement.png).

The drawing suite produces screenshots for each updated interaction and Playwright traces for all its tests. Use the Windows run artifacts named TF-D01-browser-evidence on [PR #1's Checks tab](https://github.com/uvelan/TradeForge/pull/1/checks).

The server remains the authority for drawing validation. There are no API, database, scene revision, ID format, or preference changes. Rolling back this follow-up restores the earlier client validation behavior without changing saved data. TF-D02 remains out of scope.
