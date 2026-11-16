# TF-D02 plan

Base is merged PR #1 on main at 65e5827698640d739b749bb0748e36c271d9971f. PR #1 merged the reviewed head 3d97bdb45a641b735e28add9aa623e2066ded4de. TF-D01 already provides scoped optimistic scene writes, stroke selection and captured placement. Saved drawings currently need the toolbox for movement or anchor changes.

1. Keep the primary chart and the existing pointer controller. Extend its owned gesture to include selected-body and selected-handle edits. Leave empty and unowned navigation gestures with Lightweight Charts. Escape, pointer cancellation, scope/lifecycle and projection changes discard the gesture.
2. Project narrow body strokes and selected handles. Prefer selected handles, then the nearest stroke, then the later rendered item. Use one horizontal price handle, two trend endpoints, four rectangle corner combinations, and separate position entry/stop/target handles. Locked drawings remain selectable.
3. Transform immutable anchor copies. Preserve relative UTC and price offsets for body moves, maintain rectangle and position invariants, require timestamps represented by the displayed primary candles, and reject a risk/reward direction flip. Keep the saved primitive visible and put only a valid draft on the top layer.
4. Submit one version for a changed valid release through the drawing book. Retain optimistic revision and Undo/Redo. Never retry a 409 automatically.
5. Exercise persisted scenes, exact revisions, pointer cancellation, old data, replay, layouts and toolbar fallbacks in isolated Chromium. Run Python, TypeScript/Vite and Vitest, then hand off a dedicated PR against main with exact-SHA CI evidence.

Reviewed scope: preserve original candle/source/sidebar preferences and schema. A translation through unmatched UTC gaps is rejected rather than fabricating candles. Legacy horizontal rail levels remain separate. No indicator, replay engine, trade or execution changes.
