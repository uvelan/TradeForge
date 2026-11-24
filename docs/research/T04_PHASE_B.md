# T04 Phase B candle-read parity

Base and Phase A merge: `455efb7076635cc40d0b343496d0e1f79c6d440f`.

## Enable and roll back

Run Phase A registration against isolated, quiescent catalog copies as described in [the registration guide](T04_PHASE_A.md). Point the two API storage settings at those same copies and archive roots. On Windows set the following before launching the API:

```powershell
$env:TRADEFORGE_CANDLE_SHADOW_MANIFEST = 'K:\isolated\shadow.sqlite'
```

The fixed dataset detail endpoint and the research saved-candle chart endpoint use this flag. Explicit research ranges and automatic recent ranges both use the pinned research source. Other research functions, replay and writers keep their existing paths. To roll back, remove the environment variable and restart the API:

```powershell
Remove-Item Env:TRADEFORGE_CANDLE_SHADOW_MANIFEST
```

## Read contract

One request holds one read transaction on a completed V3 shadow manifest. The resolver compares the archive root with the source pin, copies the source catalog through SHA-256 verification, and selects from that private read-only SQLite snapshot. A nonempty source WAL or rollback journal is rejected. Use offline copies; the command is intended for parity trials. Each request requires temporary disk space for the source database and the largest selected Parquet partition and rehashes both. There is no performance or live-data acceptance claim.

Each selected source row must match its recorded raw evidence. Missing IDs use the pinned research rowid to retrieve their diagnostic. The resolver rejects unresolved or absent mappings, verifies the artifact contract's revision hash and source claims, confines the locator to the pinned archive root, and decodes only the bytes that passed checksum verification. An enabled failure returns an error to the caller. The parity tests replace the corresponding legacy artifact reader with a failing sentinel during every flag-on comparison.

The two public APIs keep their existing projections. Fixed OHLC values are scaled integers with a modeled close availability time. Research ranges are half-open event-time ranges. Research preserves its own prices, compatible feed and basis, duplicate-bar accounting, gap reporting and sorted tail limit.

## Boundary evidence

The first run used the existing readers before resolver implementation. The fixture has 1970-01-01 UTC M5 candles opening at 10:00 and 10:05, epoch seconds 36000 and 36300. Both paths returned the following candle arrays; each test compares the entire response body to its own API baseline.

| HTTP request | Expected and observed rows |
|---|---|
| Fixed dataset detail, cutoff 36299 (10:04:59) | `[]` |
| Fixed dataset detail, cutoff 36300 (10:05:00) | `[{"time":36000,"available_at":36300,"open":110310,"high":110312,"low":110308,"close":110311}]` |
| Research saved candles, start 36000, end 36300 | `[{"time":36000,"open":1.1031,"high":1.10312,"low":1.10308,"close":1.10311}]` |

## Reconciliation

Each counted request is a pair of HTTP calls, first flag-off and then flag-on. Expected rejections count once, including cases where both readers reject. The standalone pre-implementation baseline is excluded from the totals. All data are committed fixture recipes executed in isolated local workspaces.

| API | Requests compared | Exact successful responses | Expected rejections | Unexplained differences |
|---|---:|---:|---:|---:|
| Fixed | 10 | 5 | 5 | 0 |
| Research | 11 | 4 | 7 | 0 |

Fixed successes cover the two cutoff boundaries, genuine alias, different feed with identical bytes, and an admitted missing slot. The five rejections exercise an unresolved basis, replaced bytes, changed source database, absent mapping, and an altered revision contract.

Research successes cover the exclusive end, overlapping explicit and automatic ranges with sorted tail limits and duplicate counts, and missing slots. Rejections cover conflicting bars, incompatible feeds, NULL-ID and BLOB-basis diagnostics, replaced bytes, changed catalog, and missing mapping. In the diagnostic, source-mismatch and missing-mapping cases the legacy path can still return 200. The test requires the exact shadow failure. Source SHA-256 inventories of both SQLite databases and all fixture Parquet files match before and after reads, including reads of deliberately damaged fixtures.

```powershell
.venv/Scripts/python.exe -m pytest -qs tests/test_shadow_candle_reads.py -k boundary_legacy
.venv/Scripts/python.exe -m pytest -q tests/test_shadow_candle_reads.py tests/test_shadow_manifest.py tests/test_restore_rehearsal.py tests/test_terminal.py
.venv/Scripts/python.exe -m pytest -q
```

The independent Phase C decision covers writer cutover. Public admission remains 160 cutoffs, historical intents retain AWAITING_EXECUTION_VALIDATION, and production recorded trading remains BLOCKED.

## Registration completion and research diagnostics

**Compatibility and recovery.** Stop older registrar processes before upgrading an offline manifest. Run the same registration command against its original pinned inputs and output path. Existing V2 files require a complete reinspection before the corrected reader accepts them. The registrar upgrades eligible V1 and V2 files in place. The existing fresh-output requirement still applies to V1 files containing unidentified mappings. Artifact contract encoding and revision identity retain their existing version.

The V3 schema migration and the start of an incomplete registration generation commit together. Each subsequent batch acquires SQLite write ownership and verifies that it still owns that generation. A successful pass publishes the completion marker after both catalog scans. A reader checks eligibility and reads mappings in the same SQLite transaction. A killed registrar leaves durable batches for inspection. Rerunning the identical command performs an idempotent full scan. A superseded registrar cannot write another batch or publish the final marker.

**HTTP regression evidence.** The tests ran the original writer at `a9014d71e102a55e8f5b2ab38b4dfad6ea2e1430` against the committed false-complete fixture and terminated the upgrading CLI immediately after each specified SQLite commit. They also exercised a genuine V2 manifest from reviewed PR head `1b335e644ce83ae8df04c396ea4e5fc118424eab`.

| Public request | Reviewed head | Corrected head |
|---|---|---|
| False-complete fixed row after schema commit | 200 | 409 SHADOW_REGISTRATION_INCOMPLETE |
| Same row after one reclassification batch | 200 | 409 SHADOW_REGISTRATION_INCOMPLETE |
| Same row after resumed registration | — | 409 FIXED_COVERAGE_MISMATCH |
| Fully covered fixed row after resume, cutoff 1020 | Legacy 200 | 200, complete response matches legacy |
| Research BLOB rows_count, explicit and automatic recent windows | 500 in both | 409 ROW_COUNT_MISMATCH in both |
| Valid 2,001-row partition, same two windows | 409 bounded-reader error in both | Same 409 in both |

For the two research rejection cases, test sentinels failed on either legacy fallback or attempted Parquet decoding. The tests compared the source databases and every Parquet SHA-256 before and after. [Per-file hashes from the focused run](T04_PHASE_B_upgrade_sha256.json) are grouped by writer and interruption commit or research case. SQLite creation timestamps differ across freshly generated fixtures.

```powershell
.venv/Scripts/python.exe -m pytest -qs tests/test_shadow_read_eligibility.py tests/test_shadow_candle_reads.py tests/test_shadow_manifest.py tests/test_restore_rehearsal.py tests/capture_shadow_v1_retry.py tests/capture_shadow_malformed_retry.py
.venv/Scripts/python.exe -m pytest -q
```

**Rollback.** Unset the shadow-reader flag and restart the API. Retain the V3 manifest for audit. An older binary requires its own compatible offline output. Legacy source databases and archives need no restoration.


## Automatic recent-window diagnostics

On reviewed head `f38215c972d37cca1a94429a041f7c02f5c34900`, the recent range selector parsed source bounds before resolving their Phase A mapping. The test writes an ordinary checksummed M5 partition, copies its original ISO text bytes into a SQLite BLOB in one timestamp field, adds a later valid partition on the same feed, and runs registration. The registrar records `INVALID_UTC_TIMESTAMP` for partition `a974a548888affadd99194003407328b7b6d2afb09216aa1fbc0f25f9c68b214` in both fixtures.

| BLOB field | Request | Reviewed HTTP | Corrected HTTP |
|---|---|---|---|
| last_time | Explicit [36000,36300) | 409 with recorded reason and ID | Same 409 |
| last_time | Recent | 500 | 409 with recorded reason and ID |
| first_time | Explicit [36000,36300) | 404 | Same 404 |
| first_time | Recent | 500 | 409 with recorded reason and ID |

For the explicit first_time case, SQLite's existing range predicate excludes a BLOB first_time when it compares it to a TEXT upper bound. The later neighbor also lies outside that explicit window. Automatic selection includes both partitions and checks each compatible mapping before parsing any time. The shadow reader reuses the artifact reader's mapping and revision verification without opening a Parquet file. Separate selection controls check the latest-50 limit, feed compatibility and Dukascopy BID filter against the complete legacy HTTP response.

SHA-256 values below matched before registration and after the HTTP requests. These are isolated fixture measurements. Database hashes can vary between runs because the writers record creation times.

| Source | last_time fixture | first_time fixture |
|---|---|---|
| Research database | `de6e012ceb6a86933106826434c612f82c166eb9b07ffdfc89a8a1a3deb3274f` | `f6ec52a993b5552a45315b1137af3cd7406f512c9573d3d807739f3ca7fc0fc7` |
| Fixed database | `a9fbe5694ec507427d1f0760f6033989f60e2126c0b1d4283509faee3a9e8e4b` | `06be46e7224fd4c1e8d463bf0a17b1c6e3b0eaab4ab9191b680d2fcd2f0c8739` |
| Affected research Parquet | `062dcc3d9293b43d331f6820b34e8334f5e319bcb3666e96a2080044f6ae2502` | Same |
| Neighboring research Parquet | `eaaface7e9d7c8e82ed7d000791c411b4899752ad696694ee9d7877da789bf67` | Same |
| Fixed Parquet | `985102d9ef5e4e7393b066a577a6877b5c82f3c6f6ec99ccea2abd4866e78b21` | Same |

Reproduce the two failures on the reviewed reader or the corrected responses on this branch with the new test file and `python -m pytest -qs tests/test_shadow_read_eligibility.py -k registered_bounds`. Include `-k "registered_bounds or selected_bounds"` to run all five new cases.
