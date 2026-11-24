# PR 29 integration re-review

Reviewed baseline `3fcd2ec9c00101d0bc09ebac52ebe7581fb662d0`; fetched main `c3e4ae28fe152530e2b4fc3c4c0eec826737fbf0`. The six workflow action changes from #27 are isolated in commit `689fce2`. Its original restore rehearsal and report were already present.

## Observation clock and evidence

Process delivery, observation, and source sequence in lexicographic priority order. A stop move takes effect at its delivery time and only for subsequent priorities. For each executable quote select the last such move delivered at or before its observation time. The initial fill establishes the original protective level. A delayed breach records the applicable level's quote in exit.stop_quote and the executing quote in exit.quote. The latest ratchet remains available separately. Targets retain their original fill anchor.

All four direction/rule pairs on the reviewed service exited on the Q+5 gap even when observed at Q+3. On corrected replay:

| Management | Direction | Move at Q+4 | Q+5 gap | Observed Q+3 | Observed Q+4 or Q+5 |
| --- | --- | --- | --- | --- | --- |
| Break-even | LONG | 1.10314 | 1.10310 BID | Open | Break-even stop |
| Break-even | SHORT | 1.10306 | 1.10311 ASK | Open | Break-even stop |
| Trailing | LONG | 1.10364 | 1.10310 BID | Open | Trailing stop |
| Trailing | SHORT | 1.10256 | 1.10311 ASK | Open | Trailing stop |

For two successive LONG ratchets the levels are 1.10350 and 1.10390. A delayed bid of 1.10340 observed between them executes the first level; 1.10370 stays open. The SHORT mirror uses 1.10250 and 1.10210. A delayed ask of 1.10260 executes the first level; 1.10230 stays open. Earlier observations may still execute the original stop or target. Re-querying the earlier inspection after persistence reproduced its response and IDs.

## Combined management

Stop, target, then elapsed-seconds exit take terminal precedence. A terminal quote closes the available remainder. Otherwise, a qualifying partial executes once; eligible break-even activation and trailing advancement can occur at that same priority. Adopt the tighter stop, retaining the existing level on equality and choosing break-even on a simultaneous equal improvement. Even when another rule supplies a tighter stop, break-even activation retains its own evidence. No update loosens the stop. A material conflicting tie or incomplete delivery coverage halts before committing a transition at that priority. Candle-count deadlines remain unsupported.

On both synthetic directions, the all-four fixture closed exactly 1/4 at the simultaneous partial/activation/ratchet quote, leaving 3/4. On the terminal quote (stop, target or elapsed seconds), it closed 3/4, leaving 0. At a deadline coinciding with the initial favorable crossing it closed 1/1 by time with zero partials. Identical requests preserved the event IDs.

Break-even and trailing use V2 policies, and combinations have a separate policy. The ordinary regression seeds the eighteen reviewed payloads and checks byte preservation, coexistence, cutoff rereads and retry row counts. The opt-in historical-writer regression loads both reviewed modules from Git, submits the original request and the corrected request to the same database and verifies the two rows. Existing fixed-exit and entry baselines continue to apply.

## Catalog and Windows checks

Reads copy blocks into a private temporary file as SHA-256 is calculated. Polars decodes only the verified snapshot. Catalog registration keeps the streaming, regular-file checksum check. A deterministic test changes the original path to another valid Parquet file immediately before decoding: the in-flight cutoff read returns the verified dataset, and the next read detects the checksum mismatch. The temporary copy adds disk I/O per read.

```powershell
.venv/Scripts/python.exe -m pytest -q research_backend/tests/capture_integration_legacy.py
.venv/Scripts/python.exe -m pytest -q -s research_backend/tests/test_offline_combined_management.py research_backend/tests/test_offline_management_ordering.py research_backend/tests/test_offline_first_exit.py research_backend/tests/test_offline_break_even.py research_backend/tests/test_offline_trailing_stop.py research_backend/tests/test_offline_partial_exit.py research_backend/tests/test_offline_time_exit.py tests/test_restore_rehearsal.py research_backend/tests/benchmark_dukascopy_nov13.py::test_nov13_market_entry_negative_control
.venv/Scripts/python.exe -m pytest -q
```

Rollback before merge by reverting the correction commit and, if necessary, the separate workflow commit. After an approved merge, revert its merge commit with mainline parent 1 through a reviewed PR. Retain immutable result rows, catalogs and provider files. Public admission remains at 160 cutoffs; historical intents await execution validation. Production recorded trading remains BLOCKED.

## Partial-only V1 compatibility correction

The reviewed partial-only writer at `3fcd2ec9c00101d0bc09ebac52ebe7581fb662d0` persisted UNREPRESENTABLE_EXIT_LEVELS before initializing normalized exposure. On the integration head `a7f700ced34a1df6491a001395d1074d05e0f221`, the same identity acquired an extra remaining_fraction field. Replaying either LONG or SHORT against that original row raised `ValueError: Offline exit result identity conflict`. Both cases reproduced before editing the service; the other six comparisons passed.

The correction restores the original V1 serialization and retains the combined policy's initial quantity. The service still compares the entire persisted payload before returning an existing result. It never rewrites an immutable row. A noncanonical off-grid row created by the intermediate integration revision will therefore continue to fail verification if encountered.

The historical-writer regression runs on a clone containing the reviewed revision. For each direction it uses a verified EURUSD entry, a 10-pip stop, 3R target and 25% partial. At 1.333R the 133.3-point offset is off-grid. The other three cases use 1.5R. Each test first commits the original writer's result, runs the current service twice on exactly the same inputs and compares the result ID, canonical UTF-8 payload and stored blob. There is exactly one row in each database after both retries.

| Case, both directions | Observed result | Simulated partials / final exits |
| --- | --- | --- |
| 1.333R, off-grid | UNREPRESENTABLE_EXIT_LEVELS | 0 / 0 |
| 1.5R, before touch | OPEN_AT_CUTOFF | 0 / 0 |
| 1.5R, after partial | OPEN_AT_CUTOFF | 1 / 0 |
| After subsequent protective stop | SIMULATED_EXIT | 1 / 1 |

```powershell
.venv/Scripts/python.exe -m pytest -q research_backend/tests/capture_integration_legacy.py -k partial_only --tb=short
.venv/Scripts/python.exe -m pytest -q research_backend/tests/capture_integration_legacy.py research_backend/tests/test_offline_partial_exit.py research_backend/tests/test_offline_combined_management.py research_backend/tests/test_offline_management_ordering.py research_backend/tests/test_offline_break_even.py research_backend/tests/test_offline_trailing_stop.py research_backend/tests/test_offline_time_exit.py
.venv/Scripts/python.exe -m pytest -q
```

Revert the partial-only correction commit to restore the intermediate behavior; doing so reintroduces the original-row conflict. No migration or mutation of historical rows is involved.
