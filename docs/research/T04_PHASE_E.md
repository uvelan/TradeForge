# T04 Phase E reader cutover rehearsal

PR #32 merged at `ee7b48efad88c5d1ff6042d4e1beb0eb04043a73`, including `2d1cbd5aaf97dcc5759ce6793467c2255d220635`. This rehearsal ran against a copy of the local TradeForge workspace and a separate synthetic CI fixture. Production settings, publication ownership and compatibility writes require no changes for this run.

## Two workspace checkpoints

| Checkpoint | Legacy IDs fixed / research | Receipts / dangling | Registered fixed / research | Successful HTTP pairs fixed / research | Expected HTTP 409s | Legacy rollback matches |
|---|---:|---:|---:|---:|---:|---:|
| Restored workspace before publication | 2 / 42 | 0 / 0 | 2 / 36 | 4 / 32 | 2 | 36 / 36 |
| After one M5 publication per adapter | 3 / 43 | 2 / 0 | 3 / 37 | 6 / 33 | 2 | 39 / 39 |

All 75 successful comparisons matched the entire decoded HTTP payload, including provenance, coverage and prices. The 36 earlier responses also matched across checkpoints. Both APIs rejected an incomplete registration with HTTP 409 and `SHADOW_REGISTRATION_INCOMPLETE: rerun registration` at each checkpoint. Six research arrival-candle artifacts per checkpoint registered with `UNSUPPORTED_KIND`. The saved Dukascopy chart selects BID, so the four ASK partitions were checksum-verified and registered but did not contribute an HTTP comparison. Every fixed ID received two cutoff probes. Each eligible saved research partition received a one-bar window. No unexplained differences were observed in these probes.

The initial fixed workspace had M15 and D1. The committed synthetic fixture exercises M5 at both checkpoints; the restored workspace exercises the injected M5 boundary at checkpoint two. At 10:04:59 UTC the fixed result is empty. At 10:05:00 it contains one candle with time 36000, availability 36300 and integer OHLC 110310/110312/110308/110311. The research window [10:00, 10:05) contains the 10:00 candle with OHLC 1.10310/1.10312/1.10308/1.10311. Both exclude the 10:05 candle. The injected research feed is synthetic fixture data written through the saved Dukascopy adapter; its provider label is preserved.

The [checkpoint evidence](evidence/t04-phase-e.json) contains both DB hashes, every archived file checksum, counts, reasons, and complete successful response objects. Each response index addresses SHA-256 of sorted, compact JSON. Acquisition timestamps and SQLite snapshot hashes are specific to this run.

## Restore and historical references

SQLite online backups include committed WAL frames. The rehearsal copies both databases and both market roots plus the issued-export directory into new isolated roots. It verifies integrity and foreign keys and rejects a capture if source file inventories, copied bytes or either connection's data version differ across the capture. Run during a quiescent window. An interrupted capture must be repeated in a fresh directory. Each checkpoint registers a distinct snapshot and is exercised with background research workers stopped.

In the workspace, 36 saved import references resolved to their recorded partitions and hashes. The 130 historical SMT revisions contained 260 lanes without a dataset pin. Their history survives the restore; an unpinned lane cannot establish archive lineage. The workspace contained zero issued AI packages and zero dangling receipts. A separate fixture used the SMT and export services to create a pinned two-lane signal and an issued ZIP. The restored ZIP matched its registered checksum and its embedded signal matched the saved revision. Absolute export locations are reconciled to the isolated export tree for verification; this rehearsal does not migrate the download endpoint's absolute locators.

A separate failure in that fixture used the actual `d77143f` research writer. Three receipts and two research datasets survived backup, including exactly one dangling receipt reported separately by namespace and ID. Its row and original metadata stayed in both databases' respective snapshot histories (source and restored research catalogs). Do not repair or discard such rows during a rehearsal.

## Reproduction and rollback

Run from the repository root with locked dependencies and full Git history:

```powershell
.venv/Scripts/python.exe -m pytest -q tests/test_phase_e_rehearsal.py research_backend/tests/test_cutover_saved_refs.py tests/test_restore_rehearsal.py tests/test_shadow_candle_reads.py --tb=short
$env:T04_REHEARSAL_SOURCE=(Resolve-Path storage).Path
$env:T04_REHEARSAL_OUTPUT='storage/t04-phase-e-results-new.json'
.venv/Scripts/python.exe -m pytest -q tests/test_phase_e_rehearsal.py --tb=short
Remove-Item Env:T04_REHEARSAL_SOURCE,Env:T04_REHEARSAL_OUTPUT
.venv/Scripts/python.exe -m pytest -q
```

Use an unused output filename. The local workspace run passed in 4.87 seconds. The four-file focused run passed all 24 tests in 9.53 seconds. Raw evidence output stays local unless explicitly selected for review.

Rollback in the isolated API process consists of clearing `TRADEFORGE_CANDLE_SHADOW_MANIFEST` and repeating the same GET requests. This recovered the complete legacy responses for existing and newly published partitions with their original IDs. Keep the catalogs, archive files, receipts and manifests. This PR adds only rehearsal code, tests and evidence; reverting it removes the rehearsal. Reader cutover and writer retirement each require separate signoff. Phase D compaction was unnecessary.

## PR #33 single-partition SMT pin correction

The restored signal-candles API uses the lane's recorded dataset ID and SHA-256. At reviewed head `de075891a7a7978aa344e40f1efdc77273754a15`, setting only the confirmed comparison lane's saved checksum to 64 zeros left reconciliation successful while the endpoint returned HTTP 409, `Recorded evidence unavailable: Original partition unavailable`. The catalog rows and both Parquet files were identical before and after this controlled corruption.

The corrected reconciliation checks the pin against the restored catalog row whose file bytes passed SHA-256 verification. The mismatched fixture rejects event `cc053b428e20262e1fff15f26411a02e74c7042b900a25423b0f5d4fdea8c781`, lane `comparison`, dataset `ba55f3a9f52fcdce23863c8fa23be4c85e6d4b707ab4912cd2800f880aaaa5f3`. Its actual GBPUSD Parquet SHA-256 is `e9e493e2f724a2eefbdf71d26bff03f083732a98a06e347ffd411542815c24db`. The endpoint returned the same 409 after the correction. With genuine pins, reconciliation verified four lane references across two saved revisions. HTTP 200 returned the original ten EURUSD and ten GBPUSD M1 candles, including their OHLC values, from the restored database and archive.

The workspace rerun reproduced the checkpoint counts above: 36 and 39 exact HTTP matches, 36 and 39 rollback matches, two expected rejections each, zero unexplained differences. Each checkpoint had 260 genuinely unpinned historical lanes and zero dangling receipts. Every Parquet checksum matched the committed checkpoint evidence. The second checkpoint includes new acquisition timestamps.

| Workspace rerun | Fixed SQLite SHA-256 | Research SQLite SHA-256 |
|---|---|---|
| 1 | `ada4bd670bbd60a1f6b564b73acfa4a7df3e63c65223b2e2014f5eaa8cce63a9` | `68ff962a230b45f9ccf9a2b61add60b292b183c7dfc913de5f5ec64fc56cb2a3` |
| 2 | `0fede100b8f30e05626bcaaa16ca8c693cd76a6f3487a44b672ec2dc98899771` | `b0b23dc10f88edad31a8c847f6560c13ccbf6bbf75d9f63bf5a39286880ea4e0` |
