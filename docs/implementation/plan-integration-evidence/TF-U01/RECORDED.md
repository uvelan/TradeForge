# TF-U01 recorded workspace gate

Starting head: `29f2d8fb9a9e2b74a23e12cd8bb93d3f07339585`. The T06 inputs span 13 November **2025**, 13:00 inclusive to 15:00 exclusive UTC. Source DUKASCOPY_SAVED, feed dukascopy-bi5-v2, EURUSD M5 BID. BI5 records quote observation time and ordinal. Delivery time is unavailable. Replay models bar availability at the close of the five-minute UTC bucket.

| Hour UTC | Quotes | BI5 SHA-256 | M5 BID dataset ID | Parquet SHA-256 |
|---|---:|---|---|---|
| 13 | 3,171 | 2f47bcb14e0a2c4dcd6d65eb819b85f1b5168b656d1f5f67c3dcd2240569f014 | 55e8bd216f5d2b9e2b130ddedd0579dce0aa54fd561cfb6e86a633228a127b0d | a6a0d7ee85e62b4947e9b0735d31ed4985871d0cb90d2efb8c621f7d14b63e41 |
| 14 | 5,465 | 9d40aa3147d93e6daa2218933877adb9471dc02ff08b19fc0d272f1eb781688f | 42d67cb27e0d1b3554cd2f787b4d3268f8fdd9b022a520e2ce68477a2b1f6ca4 | e577e7e92bf826da9fdd2ba3ea791f20a858da732ba864d3984a21bf67a87626 |

The independent reference decompresses each checksummed BI5, unpacks big-endian 20-byte records and groups the bid in integer points by UTC five-minute opening time. Ordering within a bucket uses observation milliseconds and source ordinal. First, maximum, minimum and last provide OHLC at 100,000 points per EURUSD price unit. The browser fixture uploads the files through the real import endpoint into an isolated catalog, checks both receipts, then compares all 24 API and chart series bars. Separate requests immediately before and at 14:00 verify the half-open partition boundary.

| Crosshair UTC | Reference O / H / L / C | Displayed O / H / L / C |
|---|---|---|
| 13:00 | 1.16229 / 1.16249 / 1.16205 / 1.16206 | 1.16229 / 1.16249 / 1.16205 / 1.16206 |
| 14:00 | 1.16207 / 1.16232 / 1.16196 / 1.16220 | 1.16207 / 1.16232 / 1.16196 / 1.16220 |
| 14:55 | 1.16263 / 1.16284 / 1.16244 / 1.16278 | 1.16263 / 1.16284 / 1.16244 / 1.16278 |

Local Chromium completed 100 route cycles over one, two and four EURUSD M5 panes. Switching to an empty MT5_SAVED archive returned 404 and removed the candles. Switching back recovered both verified partitions. EMA requested the same feed, basis and partitions. The frozen chart showed only the first bar at 13:05 and two at 13:10. The saved 13:05 snapshot retained its source, symbol, timeframe and cutoff; fetching its image after navigation validated its immutable ID. The snapshot schema does not include partition hashes. Only EURUSD was supplied. For 2025, cross-instrument comparisons remain UNVERIFIED. See the separate [2026 two-instrument verification](RECORDED-PAIR.md). Live MT5 quotes remain UNVERIFIED. The local Python MT5 package was present but no running terminal was found.

## Commands

~~~powershell
.venv/Scripts/python.exe -m scripts.u01_recorded_quotes --source storage/t06-input
cd frontend
$env:TRADEFORGE_E2E_API_PORT='8537'
$env:TRADEFORGE_E2E_UI_PORT='4537'
npm run e2e -- --config playwright.recorded.config.ts --retries=0
~~~

**PASS — local historical EURUSD verification.** Run the commands above against the original checksum-pinned files after a change to the covered reader, candle calculation, chart or replay behavior. Missing files and wrong checksums fail the local test. Both files also have an all-zero pin rejection control.

At `e42336aae97252ae5c79aaabee253ab2e2a1dae7`, both standard Windows jobs passed. Hosted download rate-limited; recorded-data browser test did not run. Dukascopy returned HTTP 429. [Historical push run](https://github.com/uvelan/TradeForge/actions/runs/45599957503) and [historical PR run](https://github.com/uvelan/TradeForge/actions/runs/45599968713).

For subsequent market-data changes, use historical provider data or a read-only local MT5 terminal. Verify MT5-specific claims on the terminal and cross-instrument claims with genuine data for each instrument. Use synthetic fixtures for controlled edge cases. Deployment remains a separate decision after local implementation and verification.

Development checks caught a misencoded timezone separator and pointer coordinates sampled while fullscreen was resizing. The corrected test waits for the displayed UTC timestamp before reading OHLC. Revert the recorded-workspace commit to roll back the gate independently of the original workspace soak.
