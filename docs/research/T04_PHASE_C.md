# T04 Phase C — canonical partition publication

Base and PR #31 merge: `a632a002d82162a50079c188aba1a5e507ccc2c4`, containing approved head `c6a42c8d77e6a7673fffb89df198e7ce8e259497`.

## Ownership and visibility

Both adapters call `tradeforge.publication.publish`. The publication owner is the research workspace SQLite database (`research/tradeforge.sqlite3` by default). The mounted application passes the configured research database to its fixed catalog. A standalone fixed workspace uses its own research subdirectory unless its caller supplies the owner path. Use one owner per workspace when configuring separate storage roots. The Parquet files keep their existing archive-relative paths, compression, row groups and legacy IDs.

The owner records one immutable V1 publication per (catalog namespace, legacy ID). Its JSON contains the exact compatibility metadata, including the file locator, checksum, source, basis and creation timestamp. Both adapters encode with their existing Arrow schemas. The spool determines the original research content ID. Under SQLite write ownership, the publisher inserts the uncommitted intent, fsyncs a destination-volume stage, verifies it, renames it, and verifies the final bytes before commit. A retry adopts an exact complete stage or file-only orphan. A conflicting file, missing registered file or invalid stage raises an integrity error. The registrar never discovers or adopts unrelated orphan files by scanning directories.

Research compatibility rows and canonical rows participate in the same transaction, including caller-owned transactions for compound imports. Fixed metadata lives in a different SQLite file. The adapter holds its compatibility write transaction, prepares the projection, commits the canonical owner, then commits the projection before acknowledging the request. A process failure between commits leaves one complete canonical file and receipt and no visible fixed dataset. Retrying the original request recovers the fixed projection from the original owner's metadata. This is a roll-forward protocol across stores. Backup and restore both databases and archive roots together using the offline restore procedure.

The default readers, HTTP contracts and historical evidence IDs remain in service. The opt-in Phase B reader still requires an offline completed manifest pinned to its source snapshots. Register a new snapshot to trial reads of newly published data.

## Recovery evidence

Each process-exit test performs actual writes in an isolated workspace, exits with code 75, checks both SQLite stores, then retries the identical request twice. Public visibility below refers to a committed dataset in the legacy catalog. All successful retries yielded one canonical receipt, one compatibility row, the expected candles, matching SHA-256 and zero residual stages.

| Interruption | Canonical receipts / visible datasets before retry | File available for recovery |
|---|---:|---|
| Before rename, both adapters | 0 / 0 | Verified stage |
| After rename before SQLite commit, both adapters | 0 / 0 | Complete final file |
| After commit before reply, both adapters | 1 / 1 | Original published file and first metadata |
| After owner commit before fixed projection commit | 1 / 0 | Complete file and original canonical metadata |

Separate controls inject ENOSPC after a partial staging write and an incorrect staged checksum for both adapters. Each yielded zero published rows and zero visible files. A real SQLITE_FULL in an enclosing research import transaction rolled back its dataset and receipt, leaving a complete file that an identical retry recovered. Concurrent fixed and research duplicate requests produced two receipts total, one per namespace. The fixed catalog retained the independent checksum, wrong-type and conflicting-orphan regressions.

## Historical compatibility

Fixtures used the actual writers from the PR #31 merge. Source hashes before and after publication matched:

| Original record | Legacy ID | Parquet SHA-256 before and after |
|---|---|---|
| Fixed EURUSD | `9d5d471070c1ff475eebf8552e282f309e987a12795d60dee57141c8cdb8f0a4` | `971a977835511f1676485ebfbbbff266e9e07a406c7a20d48f4b6cb6d3a8a5f4` |
| Research SMT EURUSD | `75cecac4e625ff74408dda574cf259466c6fc3d4ff6673e01507de5c1be410dd` | `7471680b95cacb1dc0ef5a115248ef09d1ab033f0bcb82aed51eecfe281b7589` |
| Research SMT GBPUSD | `ec1db9530da75c843590803e832c3e519155a4902934d57af13732aaa6ae671e` | `885086b60b749914f8ee6a9724bfac9eab89cfaad94ff60bb9d270bd042832a0` |

The historical fixed metadata and cutoff response matched exactly. A saved signal referenced the two original research datasets and a trade linked to the saved signal revision. After duplicate publication, retrieving the confirmed evidence returned the original candle payload, and downloading the already-issued AI package returned the exact original ZIP bytes and registered SHA-256. These measurements use synthetic fixtures with real legacy encoders, SQLite transactions, and files.

```powershell
.venv/Scripts/python.exe -m pytest -q tests/test_partition_publication.py tests/test_terminal.py tests/test_restore_rehearsal.py tests/test_shadow_candle_reads.py research_backend/tests/test_historical_import.py research_backend/tests/test_historical_csv.py research_backend/tests/test_arrival_integrity.py research_backend/tests/test_smt_authority.py
.venv/Scripts/python.exe -m pytest -q
```

## Rollback and limits

Pause ingestion. Finish any interrupted fixed projection using an identical retry on this version. Preserve the owner database, compatibility database and files, then run the PR #31 release with ingestion paused for read-only access. Avoid simultaneous old and new writers. Resume canonical ingestion by redeploying this version. Never overwrite a mismatched file as part of recovery. A torn stage from an untested interruption mid-copy requires inspection if its checksum fails. Reserve temporary space for the encoded spool plus the staged file. The kill tests cover process failure on the local filesystem; controller failure and power-loss durability on production storage require separate validation.

Public admission remains 160 cutoffs, historical intents retain AWAITING_EXECUTION_VALIDATION and production recorded trading remains BLOCKED.
