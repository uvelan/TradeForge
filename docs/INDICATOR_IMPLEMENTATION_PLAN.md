# Indicator implementation plan

The research terminal already provides OHLC charts, replay, and venue-specific data. The research backend has a standalone sample breakout backtest and manual review exports. No shared indicator calculations or definition registry exist.

1. Define a Python registry and a bounded, validated OHLC calculation API. Reuse the exact selected chart candles so replay, saved feeds, and live providers share the same inputs.
2. Implement numerical indicators and strictly causal structure observations. Define seeds, warm-up gaps and what each marker's timestamp means. Treat calculation timestamps as provenance and keep them out of signal rules.
3. Render overlays and oscillator panes using the chart's existing series lifecycle. Persist selections and parameters locally. Exclude the latest unconfirmed candle and discard stale responses on instrument, source, and replay changes.
4. Add session boundaries, liquidity and CRT rule definitions before wiring them into strategy and evidence workflows. Session rules need explicit calendars and DST handling. ICT/CRT labels require versioned, testable definitions.
5. Connect frozen datasets and indicator versions to research, strategy and manual review artifacts. Validate confirmation timestamps at every consumption boundary. Benchmark on representative large histories before extending the request window.

The initial chart calculation uses a bounded window. Recursive values depend on the start of that window. Missing bars are never fabricated, and periods count supplied observations. Volume analytics require an explicit volume basis. Exact spread analysis needs bid/ask data.

## Implemented chart slice

The Market Terminal applies one selected indicator to its primary chart. The selection and period persist in the browser. Requests are bounded to 2,000 bars, reject invalid OHLC or non-increasing timestamps, and exclude the latest unconfirmed MT5 bar. Replay submits only the bars visible at the current step. Binance's live in-progress bar is outside the initial indicator scope.

| Indicator | Version 1 definition | First possible output |
|---|---|---|
| SMA / WMA | Close mean / linearly recency-weighted close mean | Period bars |
| EMA | SMA seed, alpha 2 / (period + 1) | Period bars |
| Bollinger | Close SMA ± two population standard deviations | Period bars |
| Donchian | Rolling highest high and lowest low including the current closed bar | Period bars |
| ATR / NATR | Wilder true range with first bar high minus low / ATR as a percentage of close | Period bars |
| RSI | Wilder gains and losses, flat input returns 50 | Period + 1 bars |
| MACD | EMA difference, fast period scaled from 12:26, fixed 9-period signal, histogram difference | Slow-period bars for MACD, slow period + 8 for signal |
| ROC | Percentage change from the close period bars earlier | Period + 1 bars |
| Swings | Strict symmetric pivots, ties ignored. Points carry the confirming bar time and a separate pivot time for wick placement. The chart reveals the marker when the confirming bar closes. | 2 × width + 1 bars |

Numerical, prefix invariance, validation and browser workflow tests passed. Remaining scope includes the rest of the mathematical library, full structure events, liquidity, ICT/SMC, CRT, sessions, confluence, strategy/backtest integration, evidence export, templates and performance benchmarks.

SMT comparison v2 finds strict, independently confirmed pivots on both instruments. Each pivot requires contiguous bars within its swing window. It matches the nearest unused same-type comparison pivot inside the configured UTC offset, choosing the earlier pivot on ties and waiting for the entire match window to become observable. Consecutive matched pairs must move in opposite directions and each must exceed the configured basis-point tolerance relative to its own prior wick.

Defaults are three bars on each side of a swing, a two-bar UTC matching offset, a 0.5 basis-point tolerance and one additional shared closed bar for confirmation. Either instrument can lead. The lagging side crossing its original reference by more than the tolerance invalidates the event.

The SQLite journal stores append-only transitions only when both saved partitions are resolved server-side, their file checksums pass, and the submitted window exactly matches their aligned, completed bars. Evidence includes feed and dataset IDs, checksums, observed coverage, timeframe, rules, wick prices, and UTC times. Changed candles at the same dataset identity produce a source mismatch. A new dataset version produces independent current results. Earlier evidence remains in the journal and is never substituted into the chart. Older journal rows are labeled unverified. Live MT5 and demo chart calculations are unverified previews. Live alerting is suspended pending a server-owned capture path. Replay calculations are read-only. The local journal is available at `/research/api/indicators/smt/history`.

The journal API provides a timing breakdown from the immutable event evidence. Pivot times are candle opens. Confirmation first requires closing the right-hand swing bars, then observing the full UTC matching window, then closing any extra shared confirmation bars. With default rules and simultaneous M5 pivots, a 01:50 open can confirm at 02:25. Any future SMT backtest must consume `confirmed_at` and evaluate fills at or after that timestamp. The existing sample backtest does not consume SMT.
