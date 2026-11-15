# UI test plan

- Backend tests cover ordinary SMT invalidation and source corrections, exact linked revisions and replay evidence, quote/candle contradictions, corrupt duplicates, drawing version conflicts, replay checkpoints, same-millisecond quote ordering, immutable simulation inputs, stale plan versions, no-trade finalization, PNG capture checks and linked journal revisions.
- Vitest covers replay availability, preset validation, and London/New York DST boundaries.
- Playwright covers keyboard navigation, signal evidence and replay to confirmation, plan autosave, real drawing and replay persistence, archived simulation reload, existing catalog operation and the preserved SMT monitor. Test fixtures use isolated SQLite/Parquet storage. Signal events in the keyboard workflow use prepared fixture responses.
- Open validation includes real MT5 reconnect/capture behavior, broker scope and data terms, assistive-technology and complete viewport matrix, crash/restore, and long chart-soak profiling.
