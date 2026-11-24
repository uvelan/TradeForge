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

One request holds one read transaction on a Phase A V2 manifest. The resolver compares the archive root with the source pin, copies the source catalog through SHA-256 verification, and selects from that private read-only SQLite snapshot. A nonempty source WAL or rollback journal is rejected. Use offline copies; the command is intended for parity trials. Each request requires temporary disk space for the source database and the largest selected Parquet partition and rehashes both. There is no performance or live-data acceptance claim.

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
