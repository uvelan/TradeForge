# TF-I01 historical coverage plan

Reviewed and starting SHA c51f7133ecff916b3c9463ff734bdb924a68509f on open PR #3. The baseline has 90 green backend tests. An independently specified November H1 forex week returned an empty previous-week series because the engine required 168 continuous bars.

Keep UTC calendar-day and Monday-week boundaries. Compute expected UTC candle starts from either an explicitly selected IANA Sunday-open/Friday-close schedule, a continuous schedule, or unknown schedule. Default only the synthetic and spot sources to continuous. Classify each immediate reference window separately, including an absent week and a scheduled closed day. Unavailable evidence must not supply a level from an older window.

Keep 2,000 candles as the request limit. Retrieve the newest applicable day and week windows through the server's read-only source adapters, with separate fixed history and partition bounds. Scope retrieval to source, symbol, timeframe, feed and price basis. In saved replay, require the frozen partition manifest and independently verify its matching checksums. Keep the chart input and independently loaded reference evidence separate. Report decode counts, lineage and refusals in the result.

Confirm only when the UTC period ended and every expected candle is available. Restrict drawing of the level to current-period bars at or after its evidence availability. An unknown schedule may expose a candidate in evidence with provisional status. The chart plots only confirmed reference levels. Keep existing session range and numerical calculations unchanged.

Review of design. Explicit calendars do not identify holidays or broker-specific breaks unless their inputs actually model them. A frame crossing a session boundary cannot be classified from a full-frame OHLC bar and must fail conservatively. Catalog entries and caller IDs cannot elevate browser authority. Reuse Parquet checks rather than caching unverifiable range sums. Keep a read budget and measure actual decoder and evaluator costs.
