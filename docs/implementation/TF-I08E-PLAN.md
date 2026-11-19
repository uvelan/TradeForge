# TF-I08E data acceptance plan

The reviewed prerequisite is PR #13 at `9200c1c1a6101b4e0113f906102b0165f897c7f6`. Verify review state and exact hosted checks. After direct user confirmation, GitHub merged it as `f5099602485fdd1587c29b3790fb3f7ac4409158`. TF-I08E starts from that main commit.

The configured catalog is `storage/research/tradeforge.sqlite3`, with Parquet under `storage/research/market`. Tests and benchmarks use an isolated catalog. Archived availability is modeled only. Recorded admission still requires independently verified UTC and the TF-I08D receipt witness.

1. Reproduce order-sensitive OHLC, audit bounded readers and preserve existing archives.
2. Use provider milliseconds and per-file ordinal, reject conflicting identities and overlapping source windows, and publish ticks and M1/M5 BID and ASK candles atomically in a versioned feed. Retain checksum and the declared source window.
3. Verify provider acquisition, clock quality, the configured inventory, coverage and separate readiness gates. Independently compare M5 tick aggregation with complete M1 and evaluate supported real archives if available.
4. Run deterministic verification separately from operational acceptance and retain sanitized evidence.
