# Stale drawing edits on native replay

The reviewed head was `fc49b11ba94b5ee4cf7ed55b5f6f6a5143a76a54` on base `7153f40f22d7f50529ef7c019921aa003a7ae6e7`. Code and browser tests are at `b1b2ffe3c19f6eb27dcdff30dc93a1bfcaf3c273`. [Actual POST bodies, API state and source hashes](stale-edit-requests.json)

## Reproduction

Windows Chromium used the native replay service, SQLite drawing books and local Dukascopy BID candles. The EURUSD M5 move test used the genuine four-pane session. The companion test created another native session from the same archive, selecting EURUSD M5 twice so both panes addressed the same book. Archive arrival uses modeled candle close.

The original move was rejected at the replay frontier. Lock wrote revision 10 with the original anchors. Clicking an eligible origin then submitted the *unlocked* draft with expected revision 10. The server accepted revision 11, moved the trendline and reversed the lock. In the companion case, Save style wrote the new label in revision 13. The obsolete Set anchor action submitted the previous label and produced revision 14. The initial companion harness attempt used a label selector that did not resolve. The retained baseline used the combobox role and reproduced both writes.

## Correction and review

- `frontend/src/Drawings.tsx`, `useDrawingBook`. The draft remembers the item at edit start. The hook compares it with the current book item on render. A lock, removal or replacement invalidates the edit. A freshly decoded but identical item remains eligible. The commit uses the current item and the existing optimistic revision check.
- The toolbox returns to navigation, removes the placement prompt and keyboard target, and announces cancellation through a status message. An operator can unlock and start a fresh move. The existing rejected-origin message and editor remain available when the drawing itself is unchanged.
- The native browser tests check the save requests and reread SQLite-backed API state, including unrelated drawings. They reopen the saved replay, confirm the lock or label, and perform one valid fresh move. The companion label also reopens on the primary pane. The earlier zoom test now holds Enter to exercise repeat between revealing the marker and committing it with a distinct press.
- Independent code-path review used the current item check, the shared document in `usePaneDrawingBooks`, the existing pointer guard in `saveDirect`, and the server revision check in `research_backend/app/drawings.py`. A stale client that has not yet observed a competing revision still encounters the server's conflict guard. No storage migration is involved.

## Skills used

The local Windows installation resolved `systematic-debugging`, `prototype`, `frontend-design` and `ponytail-review` under `C:/Users/Uday/.codex/skills/<name>/SKILL.md`. The project supplied `K:/Projects/TradeForge/.agents/skills/impeccable/SKILL.md`.

| Skill | Decision and check |
| --- | --- |
| systematic-debugging | Captured the actual stale POSTs before editing. Compared the persisted lock/label to the version submitted by the plot. |
| prototype | Used the two native Chromium journeys as the interaction prototype, with disposable local books and existing operator controls. |
| frontend-design | Kept cancellation in the existing toolbox and ended plot placement; the test checks the toolbox announcement and plot prompt. |
| impeccable | Verified the status, removed tab stop and marker, keyboard refocus for a fresh edit, and mouse recovery on the active pane. |
| ponytail-review | Traced draft, latest item, shared book and revision guard. The receipt records stale POST counts and preservation of unrelated items for both lanes. |

## Windows reproduction

From the repository root, use a clean local directory for each run. The genuine cutoff runner imports the pinned local CSVs, freezes a four-pane replay, and restarts the API and Chromium. With both processes stopped, copy that generated workspace to a separate disposable directory for the dedicated suite.

~~~powershell
node frontend/capacity-e2e/verify-native-replay-browser.mjs --drawings --cutoff --workspace storage/drawing-stale42/genuine --output storage/drawing-stale42/restart.json --csv storage/tf-i08e-source --api-port 8828 --ui-port 4828
$seed=(Get-ChildItem storage/drawing-stale42/genuine -Directory -Filter 'native-replay-*' | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName
Copy-Item -LiteralPath $seed -Destination storage/drawing-stale42/new-test-store -Recurse
$env:TRADEFORGE_NATIVE_STORAGE=(Resolve-Path storage/drawing-stale42/new-test-store).Path
$env:TRADEFORGE_NATIVE_FIXTURE=(Resolve-Path storage/drawing-stale42/new-test-store/genuine-csv.json).Path
$env:TRADEFORGE_E2E_API_PORT='8834'
$env:TRADEFORGE_E2E_UI_PORT='4834'
npm --prefix frontend run e2e -- --config playwright.native-drawing.config.ts --retries=0 --output ../storage/drawing-stale42/native
$env:TRADEFORGE_T10_SOURCE_STORAGE=(Resolve-Path storage).Path
$env:TRADEFORGE_T07_REAL_BI5=(Resolve-Path storage/t06-input/13h_ticks.bi5).Path
.venv/Scripts/python.exe -m pytest -q
npm --prefix frontend run build
npm --prefix frontend test
$env:TRADEFORGE_E2E_API_PORT='8796'
$env:TRADEFORGE_E2E_UI_PORT='4796'
npm --prefix frontend run e2e -- --retries=0 --output ../storage/drawing-stale42/standard
~~~

For the red run, keep the regression tests and run with the reviewed version of Drawings.tsx. Pass `--grep "stale drawing edit"` to the native command. Revision numbers depend on prior actions in the disposable book. The tests compare increments and exact persisted entries.

Rollback by reverting `b1b2ffe` and rebuilding the frontend. Existing book revisions, including any old overwritten state, remain available for inspection. Applying this correction prevents future stale writes; it does not rewrite old drawing history.
