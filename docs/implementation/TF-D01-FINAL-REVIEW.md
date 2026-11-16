# TF-D01 final review verification

- Existing [PR #1](https://github.com/uvelan/TradeForge/pull/1), branch feat/TF-D01-drawing-placement.
- Starting and previously reviewed SHA: 30212fb553abab152d40112e57e261eb122e692d.
- Final implementation and test SHA: 198bc2a1a8c27daa2a42d10daa9129537874ed57. The final PR head is the following evidence commit. On this branch, retrieve its full SHA with git log -1 --format=%H -- docs/implementation/TF-D01-FINAL-REVIEW.md. The [PR Checks tab](https://github.com/uvelan/TradeForge/pull/1/checks) records the exact evidence commit, its Windows Actions conclusion and the browser artifact. Hosted runs cannot finish until this file is pushed.

## Findings and correction

| Finding | Before | After |
|---|---|---|
| P1 conflicting modes | The chart prioritized the drawing book anchor whenever both that draft and the legacy horizontal flag were active. In the isolated reproduction, clicking the rail then the chart committed an unfinished trendline and advanced its SQLite scene from revision 0 to 1. | A single active interaction state owns navigation, legacy horizontal levels, new drawings, anchor edits and plan-level selection. Mode changes cancel drafts before the next paint and release pointer capture. A trailing pointer release cannot commit in the replacement mode. Completed drawings and completed plan prices remain separate from the active interaction. |
| P2 single-anchor drag | The six-pixel threshold returned an empty point set when the two-anchor drag policy was inactive. A horizontal drag performed no saved write in the baseline reproduction. | The single-anchor policy commits the valid release point. Legacy horizontal levels use the same release policy and require a primary candle. Existing multi-anchor drag and click-click paths remain. Outside plot, missing candle, negative/non-finite price, Escape and pointer cancellation prevent a save. |

Production files: frontend/src/App.tsx, frontend/src/Drawings.tsx, frontend/src/components.tsx and frontend/src/drawingPlacement.ts. Browser tests: frontend/e2e/drawing-placement.spec.ts. Documentation: this report, the status ledger, the regression matrix and evidence/TF-D01-final.

## Browser regression results

The seven targeted tests below passed in the complete Windows Chromium suite. Assertions compare the persisted SQLite drawing scene and the existing browser-backed horizontal level records. Negative-price input is injected only in its dedicated test.

1. **the horizontal rail takes precedence over an unfinished trendline** — rail mode saves one level and leaves the versioned drawing scene unchanged.
2. **a horizontal drawing drag uses the release candle and price after zoom and resize** — after zoom, resize and enabling a left comparison scale and second pane, the saved anchor matches the rendered primary candle and click-control price at release. Reload preserves the same item.
3. **Alt+H and the rail discard rectangle and risk/reward draft anchors** — partially placed shapes are discarded and each intended level is saved once.
4. **toolbox activation clears other modes and repeated switches save only the final intended drawing** — verifies replacement of the legacy level and plan modes, exact SQLite revision, stable old records, and crosshair cancellation of an existing-anchor edit.
5. **Escape cancels all armed modes and an active drag cannot complete in a replacement mode** — confirms no revision or level change for canceled operations and no stale commit following Alt+H mid-drag.
6. **horizontal drags reject outside, missing candle and pointer cancellation** — no write for the three invalid release paths, capture released on pointer cancellation, and a subsequent valid click saves once.
7. **negative chart prices are rejected and navigation still pans and zooms** — negative input produces no write, while normal navigation changes the candle under the crosshair.

Existing tests also cover touch, fullscreen, saved anchor edits, StrictMode refresh/scope cleanup, HTTP 409 and multi-anchor placements. No original test was skipped or weakened.

## Verification commands and artifacts

From the repository root on Windows:

    .\.venv\Scripts\python.exe -m pytest -q
    cd frontend
    npm run build
    npm test
    $env:TRADEFORGE_E2E_API_PORT='8098'
    $env:TRADEFORGE_E2E_UI_PORT='4198'
    npm run e2e

77 Python tests, 4 Vitest tests, the TypeScript production build and 26 Chromium tests passed. Pytest reported its existing Starlette/httpx deprecation warning. A first baseline browser attempt encountered a fixture startup issue in the rail test. The rail test was then reproduced on the exact reviewed code in a separate tree, where it failed at the persisted scene assertion. The scratch tree briefly affected local pytest discovery and was removed before rerunning the full 77-test suite.

- [Backend](evidence/TF-D01-final/backend.txt) · [Frontend](evidence/TF-D01-final/frontend.txt) · [Browser](evidence/TF-D01-final/browser.txt)
- [Rail before](evidence/TF-D01-final/before-rail-mode.png) · [Rail after](evidence/TF-D01-final/after-rail-mode.png) · [Baseline assertion](evidence/TF-D01-final/baseline-repro.txt)
- [Horizontal drag before](evidence/TF-D01-final/before-horizontal-drag.png) · [Horizontal drag after](evidence/TF-D01-final/after-horizontal-drag.png) · [Baseline assertion](evidence/TF-D01-final/baseline-horizontal-drag.txt)
- [Mode-switch evidence](evidence/TF-D01-final/after-exclusive-modes.png). Baseline traces remain local in ignored storage/tf-d01-final-before. Final traces are uploaded by the PR workflow as TF-D01-browser-evidence.

## Remaining limits

Validation used Windows Chromium with synthetic market inputs, including emulated touch from the existing suite. Native mobile hardware and live MT5 were not used. Direct dragging of saved drawings, extra chart-pane placement and TF-D02 are outside this assignment. Reverting the implementation commit restores the prior interaction behavior without migrating or rolling back any stored drawings or preferences.
