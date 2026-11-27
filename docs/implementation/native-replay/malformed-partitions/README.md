# Malformed native replay partitions on Windows

Starting remote head `85fb38839c6281f2286573e0d903c2df4e8d879f`. The correction and tests are in `f4a540aa9a8136486516badddff76001ce2bde29`. These results use disposable controlled Parquet partitions, the actual registrar and the mounted `POST /research/api/replay/sessions` with `DUKASCOPY_SAVED` and `window_bars=5000`.

| Source and request | Before, HTTP and saved sessions | After, HTTP and saved sessions |
|---|---|---|
| Completed shadow registration, single primary, explicit window, BLOB `last_time` | 500, 0 → 0 | 409 `SHADOW_UNRESOLVED: INVALID_UTC_TIMESTAMP`, 0 → 0 |
| Same, BLOB `rows_count` | 500, 0 → 0 | 409 `SHADOW_UNRESOLVED: ROW_COUNT_MISMATCH`, 0 → 0 |
| Shadow primary with a neighboring partition, either defect, explicit or recent | 409 with Windows file-sharing error, 0 → 0 | 409 with registered reason and partition ID, 0 → 0 |
| Valid primary with malformed shadow companion, explicit or recent | 409 with Windows file-sharing error, 0 → 0 | 201 with the same diagnostic on the companion, 0 → 1 |
| Default reader, primary / companion, explicit or recent | 500 / 500, neither saved | 409 / 201; primary 0 → 0, companion 0 → 1. Both diagnostics include the field name and partition ID |

Single-partition fixture ID: `a8bcca01e9f40eb7f1bcdaf8b57e922d46b4967e717b4a58099d0c280abfbf6b`. Neighbor fixture malformed ID: `94982ed51ef60c4340c170779cc1f4928b3255c6ec676928214c65b9e08765cc`. The fixture mutates a different column in each disposable database. [Single-partition receipts](single-partition.json) · [Explicit and automatic recent-window API matrix](api.json)

**Root cause.** Native replay parsed catalog timestamps and added the row count before resolving the manifest mapping. With multiple SQL results, an exception could also retain the active SELECT cursor while the resolver tried to remove its private source snapshot on Windows. Cleanup then masked the original failure. The resolver now checks the selected row before metadata use, and the catalog context closes its cursor before disposing of the SQLite snapshot. The default reader checks the timestamp and row count at the point of use. The HTTP tests instrument actual file opens after registration and verify that replay never opens the malformed partition's Parquet.

**Selection and compatibility.** SQLite considers a BLOB greater than TEXT. Thus BLOB `last_time` can satisfy the SQL lower bound and must be diagnosed; BLOB `first_time` fails the existing upper-bound predicate. The latter control still opens the valid neighboring partition, with identical OHLC, cutoff, partition ID and checksum. The separate sequential probe also documents the existing snapshot lifetime: after a successful save changes the source SQLite file, reusing the old shadow manifest can produce `SHADOW_SOURCE_SNAPSHOT_MISMATCH`. The baseline and correction agree on this result.

**Verification.** The independent reviewer used `ponytail-review` and the mounted HTTP tests to distinguish a rejected primary from a saved session with an unavailable pane. `systematic-debugging` isolated the metadata failure and the cursor-lifetime failure separately. No operator screen changes were needed. For the focused matrix, run:

```powershell
.venv/Scripts/python.exe -m pytest -q tests/test_native_malformed_partitions.py research_backend/tests/test_native_replay_window.py tests/test_shadow_candle_reads.py
```

The focused suite passed 54 tests; the independent reviewer separately reran all 12 new cases. The full Windows run on the tested revision passed 1,268 backend tests with one POSIX-only skip and one existing dependency warning, 13 frontend unit tests, the production build, all 94 standard Chromium tests and both existing genuine-data browser tests. The native four-pane journey passed with a fresh disposable store, two API processes, two browser processes and zero uncaught browser errors. [Exact commands, counts and timings](verification.json) · [Restart receipt](restart.json)

Production churn was +23/−9 lines; independent tests added 119 lines. The rework interval from the single-row corrected probe to the candidate commit was 2 min 9 s, including the neighboring-row Windows lock reproduction, correction and focused checks. No full suite needed a retry. The separate diagnostic matrix was rerun to release baseline SQLite handles before deleting its disposable workspace. At 08:03:58 UTC, sampled totals were 13,239,211 owner tokens, 5,040,354 independent reviewer tokens and 2,217,276 approval-review tokens. Cached input and reasoning are included once. [Per-agent usage](usage.json)

Rollback by reverting `f4a540aa9a8136486516badddff76001ce2bde29` and restarting the API. There are no database migrations, archive rewrites or changes to persisted session formats.
