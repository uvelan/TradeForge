# Canonical market-data catalog cutover

This is a planned storage-contract migration (T03/T04), with the existing SQLite, Parquet and Windows runtime retained. Keep old files and IDs immutable. Define the authoritative schema first and initially register legacy files by reference. Consolidate the two market-data writer/catalog contracts. Leave plan, review, journal, and broker ledger tables under their current domain authority.

## Prerequisites and version contract

1. Reproduce A05 and A20-A22 in both current write paths. Take an offline or consistent SQLite backup plus an inventory of Parquet and companion roots with hashes. Restore to an isolated root before designating a writer authoritative.
2. Inventory `tradeforge.catalog.dataset_versions` and research `datasets`. Export relative path and old stable ID, checksum, source/basis/scale, clock semantics, interval and coverage. Never infer a broker identifier or tick precision from a symbol alone. Flag ambiguous basis, unknown aliases/currency, off-grid bars or high-low discrepancies as unresolved/untrusted rather than rewriting evidence.
3. Define v1 manifest fields for dataset ID and immutable revision, provider, account-independent feed identity, symbol alias and specification version, price basis, UTC half-open request, precision, granularity, column/schema version, available and elapsed times, row count, file checksum, parent partitions, raw bytes hash, observed and scheduled coverage, ingest software and publication state. Use stable source order within ms and stable partition/sequence identity across ties. Keep synthetic/broker/provider outputs unmixable.
4. Design v1 typed Arrow schema with exact scaled integers and signed instrument price validation. Define available-at separate from event timestamp, UTC units explicit. Registry records original provider specifications, legal rights notes and timezone/calendar version; no hard-coded weekend exception fills a price.

## Reversible phases

| Step | Work and gate |
|---|---|
| A. Register in shadow | Add additive schema migration, read both stores in stable batches using checksums. Build legacy-ID mapping and bind equivalent true duplicates by source AND data content; different source assertions remain separate. Persist counts, checksums, gap/reject reasons and zero unknown mappings for the initial supported subset. No transcode. |
| B. Parallel reads | Both existing APIs remain. Feature flag canonical resolver for each, run parity for cutoff, price units, ordering, lineage, quote/candle inconsistency, partition overlap and synthetic source labeling. Zero unexplained event/value differences or permission regressions. |
| C. Canonical writers | Publish new partitions through staged intent and verified commit. One SQLite writer owns manifest publication. Keep existing routes through adapter; no parallel independent writes. Test terminate/restart before and after rename, disk pressure and idempotency. Alert evidence and old exports retain verified file identifiers. |
| D. Optional compaction | Canonicalize selected partitions by streaming source to new typed files. Compare counts, fixed-point extrema, sample/full computed hashes by policy and changes in metadata. Record source→derived lineage and validate single and multi-partition replay. Pin old paths while any report links to them. |
| E. Cutover and retire | Roll out read-by-read behind flags. Require successful restore rehearsal, feature parity at two checkpoints and explicit signoff before removing duplicate writes. Remove unused code only after owners verify zero accesses; archive files and mapping until retention permits deletion and backups verified. |

Rollback switches readers to legacy adapters and disables new writes or projects canonical records via a controlled read-only compatibility adapter. New canonical files and mappings survive a rollback. Use a tested backup restore only into a separate root to avoid discarding post-cutover writes. Never batch-delete stale files on discovery.

## Capacity and operational bounds

Use a configurable cap constrained by actual disk size and free space. An example for 500 GB available is 330 GB verified tick/candle store, 40 GB staging, 30 GB cache, 30 GB for logs/exports/manifest snapshots, 70 GB free headroom. External backup occupies another device and requires a separate estimate. Freeze acquisition before reserve or cost limits breach. Canonical tables are primarily manifest/query indices, with payloads in Parquet.

Illustrative scale only. At 50–150 million quote rows per instrument-year and 10–30 compressed bytes/row, storage is 0.5–4.5 GB per instrument-year. Four instruments × five years yields 10–90 GB before raw cache, duplicates, bars, metadata, temporary compaction and backup. A stress assumption of 300 million rows/year at 40 B/row means 240 GB for those 20 instrument-years before overhead. Measure at least one quiet and volatile month per selected feed, account for real compression/turnover/date permissions, and resize before bulk acquisition. Retention and reproducibility take precedence over maximizing history.

Partition quotes by provider/feed/instrument/day with bounded row groups (partition by hour when huge or useful for provider retries). Use UTC metadata predicates and projected columns with Arrow batch reads/Polars lazy scan. Budget concurrency, reject oversized staging jobs and merge incrementally. Schedule bounded checksums, reconcile stale intents, export a backup manifest and revalidate hashes and SQLite integrity on test restore. Avoid loading a complete multi-year window into process memory. [Benchmark plan](06_TEST_AND_BENCHMARK_PLAN.md)
