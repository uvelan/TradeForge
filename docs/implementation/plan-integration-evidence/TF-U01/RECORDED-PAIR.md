# PASS — local historical EURUSD/GBPUSD chart comparison

Verified on Windows against the local Dukascopy M5 BID candle archive for **13 November 2026**, [13:00, 15:00) UTC. Both files identified by the [reviewed pin](../../evidence/TF-I10A1-dukascopy-nov13/pin.json) matched their catalog IDs, source feed `dukascopy-web-m1-v1`, symbols, timeframe, basis and SHA-256. Each contains 24 candles in the acceptance window. The same files contain zero candles in the requested 2025 window. The [2025 EURUSD-only result](RECORDED.md) remains PASS. **2025 cross-instrument comparison and live MT5 are UNVERIFIED.**

| Instrument | Dataset ID | Parquet SHA-256 |
|---|---|---|
| EURUSD | ca01e9ac45346d7c9fdfdf4263085831a07735bc16158901c9a14187f77aaf4e | fa2af192aa86dc41bb20df51dffe95c29bc48cdf2fe38b643f699985c6b0e09d |
| GBPUSD | 1cc6acc1b358b3b043438badc69f72413827a8cd5dad379de8e41dd9aaaecfd0 | aed73f09d78bb3df29d3678e0ace1f00949a673a920ed22dcd6d23cb7a173d02 |

### Expected and observed OHLC

Every displayed value below matched its instrument's independently decoded Parquet row in both primary/comparison orders. The date and time in the crosshair matched the exact UTC candle-open timestamp.

| Open UTC | EURUSD open / high / low / close | GBPUSD open / high / low / close |
|---|---|---|
| 13:00 | 1.10503 / 1.10550 / 1.10494 / 1.10545 | 1.32256 / 1.32287 / 1.32238 / 1.32283 |
| 14:00 | 1.10501 / 1.10533 / 1.10498 / 1.10527 | 1.32321 / 1.32341 / 1.32305 / 1.32318 |
| 14:55 | 1.10569 / 1.10595 / 1.10557 / 1.10568 | 1.32448 / 1.32488 / 1.32447 / 1.32456 |

The offline test opens the source catalog read-only, decodes the verified bytes directly with Arrow and restores just the two files and catalog rows into disposable test storage. It checks all 48 window rows over HTTP, the terminal's own archive requests, and the full plotted candlestick series. It matches the lanes by UTC time, exercises overlay and separate panes, swaps instruments and navigates to Trade Planner and back. It delays delivery of a genuine EURUSD peer response until GBPUSD has replaced it and the new chart capture has saved. The chart, selected capture action and both legends still match the current pair after delivery.

At 14:05 UTC frozen replay includes the 14:00 bar on each lane and excludes the 14:05 bar on each lane. The chart uses nominal M5 close availability. The separate strategy recipe models a 20-second GBPUSD delay. Snapshot metadata identifies the primary symbol, timeframe, source and cutoff; the test checks the peer separately via the chart series and legend.

### Reproduce offline

From the repository root with the existing local database, market files, backend environment and Chromium dependencies:

~~~powershell
$env:TRADEFORGE_COMPARISON_DAY='2026-11-13'
$env:TRADEFORGE_E2E_API_PORT='8537'
$env:TRADEFORGE_E2E_UI_PORT='4537'
cd frontend
npm run e2e -- --config playwright.comparison.config.ts --retries=0
~~~

Local result: **1 passed, zero retries**. During development the first legend locator also matched the SMT status line, and an HTTP listener captured the shorter Overview response. The final test targets the timestamp line and the terminal's 600-bar request. No product discrepancy was observed. The test uses local files and does not initiate a broker session or provider download.
