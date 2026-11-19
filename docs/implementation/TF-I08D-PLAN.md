# TF-I08D market-data readiness plan

Base and TF-I08C merge: 921452be91513643b9c5cdd373db117f2f936b32. The reviewed PR #12 head 805945e4f06d03004d5a102fd2cefe643afad3ea had successful push and pull-request checks, a clean merge, and no blocking threads.

## Audit and boundary

The research API reads MT5, Binance, simulation and saved Parquet. Its MT5 and BI5 historical import routes have no arrival capture. BI5 aggregation describes event-time OHLC. The catalog app has a separate historical MT5 import and SQL catalog. Neither import, chart replay's close fallback, legacy SMT capture journals, nor the existing breakout and tick replay exports are authorized arrival writers. Existing strict single-symbol readers check partition bytes and can admit an explicit stored timestamp. Strict SMT additionally checks a row marker, which alone does not prove the writer. Record the baseline before changing admission.

Keep one localhost research capture manager using the shared read-only research MT5 lock. Exclude the newest provider candle, accept M1 and M5 on an aligned UTC clock, and gate first observations to the session start. Recover only within a bounded polling horizon and record actual recovery time. Store immutable capture sessions, observations, durable-write receipts and correction links in SQLite. Do not rewrite archives or promote existing journal and backfill data. A capture snapshot seals bounded Parquet batches and registers a checksum sidecar after successful publication. File or catalog artifacts without trusted registration confer no arrival authority. The local database and filesystem are trusted only within the operating-system account.

Use separately versioned legacy, recorded, and historical-simulation admission. The historical simulation policy exposes nominal close plus a nonnegative configured delay per input. It permits only explicitly frozen, checked OHLC archives with a declared calendar. Mode, delay, availability policy and frozen identity travel in evidence. The recorded guard remains separate from a simulated consumer. Keep compatibility with old request fields while removing their ability to prove arrival.

## Verification

Test with injected clocks and a controlled MT5 adapter, including developing candles, outages, restart, corrections, clock jumps, forged evidence, independent feed delays, and cutoff rewind. Verify old chart, drawings, SMT journal, alert and approved fingerprints. Attempt a read-only live availability probe and keep production blocked without verified broker capture. Commit observed timings and execution evidence to a dedicated PR.
