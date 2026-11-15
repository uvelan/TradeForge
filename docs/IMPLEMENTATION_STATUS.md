# Implementation status

The Local Research MVP ZIP supplies the default dashboard and its research workflows. The original catalog remains accessible from Data Library and keeps its existing SQLite and Parquet paths. Research records and artifacts use a separate database and directory. Both API surfaces run in the same loopback process and share an MT5 access lock.

The merge fixes the missing Vite CSS declaration and routes AI and journal downloads to the mounted research API. BI5 decompression has bounded output and memory, validates volumes, and runs off the event loop. Parquet reads stream checksum calculation and read the needed trailing row groups. Research position sizing remains illustrative, and the example OHLC backtest omits transaction costs. Live MT5 and real Dukascopy data remain unverified.

After the merge, 22 backend tests, 1 frontend unit test and 2 Chromium browser tests passed. The strict production build and Python dependency check passed. The dashboard, research API and original catalog responded on the restarted local app.

- Verified on Windows: localhost startup, SQLite migration, deterministic EURUSD simulator, versioned Parquet catalog, and interactive terminal chart.
- Passed: 9 Pytest cases, strict TypeScript and Vite build, 1 Vitest case, 1 Playwright Chromium test, and Python dependency consistency check. Backend tests cover restart persistence, simultaneous duplicate ingestion, interrupted publication and retry, checksum failure, cutoff filtering, invalid candles, origin rejection and MT5 reconnection with a terminal double. Browser coverage checks chart canvas rendering after generation and reload without page errors.
- Build observations: the bundler reported MUI client directives and a large JavaScript chunk. Pytest reported a Starlette TestClient deprecation warning. There are no known failing tests in this slice.
- In progress in Phase 2–3: read-only MT5 status and bounded M1–M30 candle imports into the catalog are tested with a terminal double. Missing slots are surfaced without classifying their cause. The updated frontend builds and passes its unit test. Backend coverage is now 10 passing tests. A live terminal and account have not been verified.
- Pending Phase 2–3: broker calendars, confirmed live MT5 ingestion and quotes, Dukascopy, durable jobs, tick storage, session overlays and replay controls. Direct Dukascopy access and file contract still need verification.
- Pending Phase 4–9: planning, risk, research, backtesting, AI review, trade reconciliation, learning, full adversarial acceptance, recovery, backup and benchmarking.
