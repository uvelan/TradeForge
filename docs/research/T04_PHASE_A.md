# T04 Phase A shadow registration

Base commit `666d0fffdfa3ccd64a36ab6df8547f39d3ee06b4`. The command inventories isolated, immutable SQLite backups and their accompanying archive copies. Both application readers and writers continue using the legacy catalogs.

## Running and tracing a registration

Use SQLite's backup API as in `tests/test_restore_rehearsal.py` to include committed WAL contents. Copy referenced archives preserving relative paths. Keep the input snapshot offline and unchanged throughout registration. The output directory must exist. Example from the repository root:

```sh
python -m tradeforge.shadow_manifest --fixed-root restore --research-db restore/research/tradeforge.sqlite3 --research-market restore/research/market --manifest shadow.sqlite --batch-size 64
```

Version 1 pins the two database file hashes and resolved archive roots. A changed snapshot requires a separate manifest. An interrupted run may contain a partial inventory. Rerun the same command through successful exit for the full reconciliation. It rechecks both catalogs ordered by legacy ID, including previously inspected files. Each batch commits its mappings and contracts together. A verification failure on a retry removes the current mapping's revision and records its reason. An artifact contract alone is never an active mapping. SQLite foreign keys link the mapping to the verified contract. The verifier uses the PR #29 descriptor and SHA-256 check with a private temporary snapshot for Parquet decoding. Allow transient disk space for the largest input file. File reads stream in MiB blocks and candle decoding in batches of 1,024. Companion import lookup can scan multiple import manifests per candle partition.

```sql
SELECT m.catalog, m.legacy_id, m.legacy_record, m.reason, a.revision, a.contract
FROM mappings m LEFT JOIN artifacts a USING (revision)
WHERE m.catalog = :catalog AND m.legacy_id = :id;
```

The raw catalog row includes its locator, registered checksum, acquisition or registration time and original field names. A mapped revision is SHA-256 of the sorted compact JSON contract. Distinct legacy IDs remain individually searchable, even if they share one revision. Within the fixed deterministic EURUSD simulator feed, matching source, bytes, interval, basis, scale, schema and clock claims identify the same data. Other producers retain the legacy claim ID in their identity because matching bytes and labels alone cannot prove a shared origin.

## Source contract

| Field | Fixed dataset_versions | Research datasets |
|---|---|---|
| Source and price | Provider, feed, symbol, timeframe, price basis, integer point scale | Provider, feed_id, symbol, kind, timeframe, price_basis. Price scale unknown from dataset row |
| Time range | UTC half-open request; observed first and last bar independently decoded | Inclusive first_time and last_time cross-checked against decoded epoch seconds |
| Availability | Explicit available_at verified at event time plus one timeframe, modeled close | Unknown for ordinary candles. Import or registration time does not attest individual publication |
| Coverage | Row count, observed endpoints and missing interior grid slots | Same checks; neither catalog row establishes scheduled session coverage |
| Provenance | Schema and validation status, acquisition time, Parquet checksum | File checksum, registration time, and optional historical_imports source hash, import interval, aggregation policy and parent tick ID linked by candle dataset ID |
| Unknowns | Instrument specification version, calendar, raw source hash and parentage | Instrument specification version, calendar, and lineage absent a companion attestation |

Initial mapping supports candle partitions on the existing timeframes with BID, ASK or synthetic_bid basis. All inspected ticks and arrival-candle rows receive a reason. Unsupported records with a readable locator and valid registered checksum still undergo file verification. Recorded parent IDs and raw-source checksums are catalog attestations. Registration verifies the referenced Parquet artifact. It does not validate broker rights, executable liquidity, or the original downloaded raw file.

## Evidence

The committed test builds both databases with the normal writers and restores isolated copies. It adds adversarial rows there. Reconciliation was:

| Input | Catalog | Inspected | Mapped | Unresolved |
|---|---|---:|---:|---:|
| Committed synthetic fixture | Fixed | 5 | 3 | 2 |
| Committed synthetic fixture | Research | 3 | 2 | 1 |
| Local archive copies | Fixed | 2 | 2 | 0 |
| Local archive copies | Research | 42 | 36 | 6 |

The fixture reasons were MISSING_FILE, CHECKSUM_MISMATCH and MISSING_PRICE_BASIS. Four unique artifact revisions accounted for the five successful mappings. After interruption with two committed rows, batch sizes 1 and 4 reproduced every mapping, reason and artifact from the uninterrupted batch-size-3 run. Hashes of both restored catalogs and all archive files matched before and after. Fixed availability-cutoff reads at 1020 and research event-window reads ending exclusively at 960 matched the original writer outputs.

Local execution used temporary SQLite backups and copied artifacts. The command including Python startup took 0.90 seconds on this workspace. All six local unresolved rows were UNSUPPORTED_KIND. Local files are excluded from Git. Two representative local records:

| Catalog / legacy ID | Source | Event bounds, inclusive UTC seconds | Rows | Registered Parquet SHA-256 |
|---|---|---|---:|---|
| Fixed / `1328977ac9c7d55f83243307d6dfff54b40690d697ff31734d1566f27a0b8c62` | simulator, deterministic-eurusd-v1, EURUSD M15 synthetic_bid, scale 100000 | 1763942400–1764114300 | 192 | `90439f9f45656ae735897d1da25458d62ed2f3c6df25c42f3d37512a4a572f37` |
| Research / `089975ac99528fe2eb78654bdd483d1aeca5c58883fcbecbe8f760c83a81a2f7` | DUKASCOPY, dukascopy-web-m1-v1, EURUSD M1 BID | 1794960000–1795046340 | 1440 | `40384cd7efc21b86454515001c9b539346998641195fd47fe06f849dcb60aed4` |

```sh
python -m pytest -q tests/test_shadow_manifest.py tests/test_restore_rehearsal.py
python -m pytest -q
```

Rollback consists of ceasing registration and retiring the separate shadow SQLite file after closing its connection. Phase B reader selection and production writer cutover require separate decisions.
