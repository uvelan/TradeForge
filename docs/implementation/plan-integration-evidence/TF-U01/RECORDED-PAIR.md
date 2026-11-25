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

At 14:05 UTC frozen replay includes the 14:00 bar on each lane and excludes the 14:05 bar on each lane. The chart uses nominal M5 close availability. The separate strategy recipe models a 20-second GBPUSD delay. At revision 0309736, snapshot metadata identifies the primary symbol, timeframe, source and cutoff; the test checks the peer separately via the chart series and legend.

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

### Reopening paired Learning Journal evidence

On 0309736, frozen Lesson CHART-00001 pointed to snapshot 74ddb803325b2acf12a4ddbe97828aaefdc4bf0acbab757e05adf8d67c674207. Its receipt named EURUSD, M5, DUKASCOPY_SAVED, cutoff 1794578700, null signal reference, 938 x 510 PNG and USER_CHART_CAPTURE. After restart the image returned 200, and its metadata URL returned 404. On the updated API, reading the same old database returned the original metadata and PNG with an identical SQLite row. The UI labels the missing historical peer.

New receipts add lane_schema_version 1 and ordered primary/comparison lanes, each with symbol, timeframe, feed, basis, partition IDs and checksums, plotted bar times, OHLC digest and last bar. The server verifies the plotted values against the archived bytes before hashing the metadata together with the PNG.

| Primary / comparison at 14:05 UTC | Reopened 14:00 closes | Snapshot ID | PNG SHA-256 |
|---|---|---|---|
| EURUSD / GBPUSD | 1.10527 / 1.32318 | 0b1d7277d52b0d3b61e9397e7c5a818488cc5fddabf7696b696713904544caf6 | e3f1c8143fc79f58d19f0b01bbbd8240a678dec2e5de9d9f78708c68806519eb |
| GBPUSD / EURUSD | 1.32318 / 1.10527 | 1f0783bfcb3633585f3ddbfb2f7da412a77160181fabba0d1786ffff6c78265b | 14bac987e946efc39a2607b6b26c67dade0b41ac65ff9c86f936b1e9d1b48cad |

Both lessons reopened in a fresh Chromium process against a restarted API on the same disposable database. Returned metadata and image bytes matched the original receipts. Separate cases delayed a peer across a swap and a save across removal of the peer. The persisted request retained the originating image and lanes. The replacement chart did not offer the obsolete capture for attachment. A development assertion assumed the hover legend remained visible with the pointer outside the chart. The final check inspects the plotted series directly.

~~~powershell
.venv/Scripts/python.exe scripts/verify_paired_snapshot_restart.py
.venv/Scripts/python.exe -m pytest -q research_backend/tests/test_chart_snapshot_lanes.py research_backend/tests/test_workspace_tools.py research_backend/tests/test_ai_evidence_snapshot.py
npm --prefix frontend test
npm --prefix frontend run build
cd frontend
npm run e2e -- chart-capture-context --retries=0
npm run e2e -- --config playwright.recorded.config.ts --retries=0
~~~

Local results: paired capture and restart 2 passed, focused backend 10 passed, frontend unit suite 13 passed, production build passed, capture-context Chromium 7 passed, 2025 single-symbol Chromium 1 passed including 100 navigation cycles. All browser runs used zero retries. Rollback: revert the capture change and restart the local API and frontend. The prior version can still serve the PNGs. No database migration is required.
