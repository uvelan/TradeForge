# TF-D02 review evidence

The starting main commit was `65e5827698640d739b749bb0748e36c271d9971f` after PR #1 merged. The implementation and test commit is `e7f17f7104c1c0740defc00b3c98aa956c4d18a4`. Follow-up commit `017dd08746b9f824eb5fba022e4f2b35179f663d` adjusts the Escape regression setup to account for required empty-space deselection and preserves the TF-D01 artifact settings. The evidence commit follows them on the same feature branch.

## Design and changes

- `frontend/src/drawingPlacement.ts` extends the existing capture controller with a distinct edit gesture and tracks unowned taps without capturing their pointer. In Chromium, a rapid empty click followed by a stroke click could miss the second chart-library click notification. The controller now classifies taps from primary pointer down, movement and release. Regular drag navigation stays with the chart. SMT click handling still uses the chart event.
- `frontend/src/drawingGeometry.ts` owns hit distances, handle mapping and immutable geometry transforms. Selected handles take priority over narrow strokes. Equal stroke distances favor the later rendered drawing. Horizontal time stays fixed. Body translations preserve UTC and price offsets. Risk/reward stop and target must bracket entry in the original direction.
- `frontend/src/components.tsx` projects pointer coordinates using the primary plot bounds and primary candle series. Scope remount, Escape, pointer cancellation, viewport and layout changes clear the draft. The chart detaches the controller and releases capture on cleanup. The saved primitive remains visible during edits. The preview and handles use the existing top layer.
- `frontend/src/Drawings.tsx` and `frontend/src/App.tsx` connect selection and editing to the existing versioned scene. Each valid changed release writes one revision and creates one Undo entry. HTTP 409 blocks subsequent writes until the drawing scene is reloaded. IDs, style, visibility, locking and old persisted records keep their existing format. Replay still filters drawings at the closed-bar cutoff and direct edits use the primary candles available at that replay step.
- `frontend/e2e/drawing-edit.spec.ts` drives real pointer and touch events, inspects painted handles and checks saved scenes. `frontend/src/drawingGeometry.test.ts` independently checks transformations. The existing CI now uploads TF-D02 browser traces and screenshots.

## Verification

| Check | Baseline main | Implementation |
| --- | --- | --- |
| Backend suite | 77 passed | 77 passed |
| Vitest | 4 passed | 8 passed |
| TypeScript and Vite production build | Passed | Passed |
| Chromium | 26 passed | 35 passed (26 regression and 9 new) |

Local runs used Windows, Python 3.13.14, Node 24.14.0 and npm 11.1.0. From the repository root, run `..venvScriptspython.exe -m pytest -q`. From `frontend`, run `npm test`, `npm run build`, and `npm run e2e -- --reporter=line`. The browser run used isolated loopback ports via `TRADEFORGE_E2E_UI_PORT=4198` and `TRADEFORGE_E2E_API_PORT=8098` and used Playwright's temporary database. Hosted status and exact checked SHA are recorded in the pull request checks.

[Backend](evidence/TF-D02/backend-local.txt) Â· [Vitest](evidence/TF-D02/vitest-local.txt) Â· [Build](evidence/TF-D02/build-local.txt) Â· [Chromium](evidence/TF-D02/playwright-local.txt)

| Scenario | Observed evidence |
| --- | --- |
| Move trend body and resize both endpoints | The browser asserted one revision per edit, equal body offsets, stable UUID and unchanged scene while the pointer was held. [Live preview](evidence/TF-D02/trend-preview.png) Â· [Persisted JSON](evidence/TF-D02/persisted-trendline.json) |
| Undo, redo and reload | The browser compared exact anchor arrays after Undo, Redo and page reload. The sample shows the same UUID from original revision 1 through edited revision 6. |
| Four rectangle corners and long/short brackets | The browser checked each corner's two boundaries, both trade directions and rejection of stop/target/entry crossings. [Corner handles](evidence/TF-D02/rectangle-handles.png) |
| Scale, zoom and fullscreen | The browser saved a horizontal price edit after resize, zoom and fullscreen with the comparison scale, then verified the original UTC anchor and stable saved result. [Fullscreen chart](evidence/TF-D02/horizontal-fullscreen.png) |
| Cancel and scope switch | Escape, pointercancel, native Chromium touch cancellation and timeframe remount released capture. The browser checked both scopes for unchanged scenes. The app test entry point wraps the chart in React StrictMode. [Scope switch](evidence/TF-D02/canceled-scope.png) |
| Competing save | An independent API request advanced the revision. Chromium observed HTTP 409, blocked a retry, and reloaded the winning scene. [Conflict recovery](evidence/TF-D02/conflict.png) |

The suite asserts that a draft appears before persistence and that canceled gestures release capture. There was no frame-rate benchmark. Browser interaction was tested in Chromium including emulated touch. Physical phones, other browser engines and a live MT5 broker were outside local verification.

## Limitations and rollback

- Direct manipulation covers horizontal lines, trendlines, rectangles and risk/reward drawings. Existing toolbox controls remain available for the other drawing types.
- Existing drawing scenes are provider, symbol and timeframe scoped. A saved-date range selects candles within that scene. A range change cancels an in-flight edit when the displayed data is replaced. The scene format has no partition-level drawing ownership.
- Nonhorizontal edits need valid primary candle times. Moving a shape across a gap may be rejected when any translated anchor has no candle. This preserves the original anchor spacing.
- To roll back, revert the implementation commit on a review branch. The versioned API, schema and persisted drawings are compatible with the previous toolbox. Rolling back the code does not roll back intentional edits already saved by users.
