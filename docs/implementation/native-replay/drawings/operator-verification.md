# Drawing operator verification

Controlled archive replay used 5,000 closed bars in each named pane. EURUSD M5 was primary, with GBPUSD M1, USDJPY M5, and XAUUSD M1 as companions. Windows Chromium exercised the production replay and drawing routes in an isolated store. The Playwright probe observed chart series counts and visible range.

| Operator action | Merge 7153f40 | Combined candidate |
| --- | --- | --- |
| Arm a horizontal line in the primary pane. Pan and zoom, select the line, drag its handle, then undo. | Passed on the first pointer actions. | Passed. Mouse pan and wheel zoom changed the logical range. Undo restored the exact prior scene. |
| Arm a drawing with Enter and reach the chart with Tab. | The plot could not receive keyboard focus. The placement prompt overlapped the chart toolbar. | Passed. The plot shows a focus ring, anchor reticle, and time and price. Arrow keys move the anchor. Enter places or edits it. |
| Switch the Inspection pane to chart view 4 and draw a horizontal line there. | The companion book stayed at revision 0. | Passed. The toolbox named chart view 4 and its market. Only that plot showed the prompt. Its saved revision advanced by one and the primary scene matched its initial revision and items. |

Keyboard verification also used the Set anchor 1 and Undo drawing edit buttons. Escape canceled a new draft without a write. Shift plus the left and right arrows still moved the replay timeline with the plot focused. The chart becomes a Tab stop while a toolbox drawing is armed.

The final operator run passed all three cases in 44.1 seconds on functional checkpoint `04d65ae`, with zero retries and a 1536 × 900 viewport. The earlier keyboard checkpoint also passed the production build and all 35 existing Chromium drawing placement and editing cases with zero retries.

[Selected drawing after navigating](operator-pointer-selected.png) · [Focused keyboard target](operator-keyboard-target.png) · [Companion drawing destination](operator-companion-toolbox.png)

The throwaway interaction prototype is commit `07c441e` on local branch `codex/draw-b-keyboard-prototype`. Its Chromium walkthrough checked Tab entry and exit, two-anchor entry, Escape, undo, and Shift+Arrow. The production test uses the existing chart projection and the same placement path as pointer gestures.

The dedicated suite uses the controlled archive prepared in the [native replay runbook](../README.md). Use a disposable copy because the operator cases write drawing versions. Set `TRADEFORGE_NATIVE_STORAGE` to the copy and `TRADEFORGE_NATIVE_FIXTURE` to the matching preparation receipt, then run this from the repository root.

```powershell
$env:TRADEFORGE_E2E_API_PORT='8822'
$env:TRADEFORGE_E2E_UI_PORT='4822'
npm --prefix frontend run e2e -- --config playwright.native-drawing.config.ts --retries=0
```
