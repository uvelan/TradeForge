# Offline break-even stop activation

PR #25 merged at `c3e4ae28fe152530e2b4fc3c4c0eec826737fbf0` after both workflows passed on approved head `ecc5823baf4f7df1ff3f20022cc11dad1ca06b23`. The follow-up branch is `codex/offline-break-even`.

## Execution contract

The public Python service resolves and verifies the persisted entry, including cutoff-visible fill verification. When the frozen management contract specifies break_even_at_r, OFFLINE_BREAK_EVEN_FIRST_EXIT V1 computes the activation level as fill + (fill − original stop) × break_even_at_r using exact rational arithmetic in instrument points. Fractional-point levels return UNREPRESENTABLE_EXIT_LEVELS. The existing strategy validator requires positive activation below the target multiple.

Each quote priority uses delivery, observation and source sequence in that order. A LONG closes on BID and a SHORT on ASK. The active stop and target are checked before activation. A qualifying quote arms the stop at the actual entry price before fees. Only a strictly later priority can execute against that new stop. The exit records the selected threshold and the actual executable quote price separately. A relevant conflicting tie returns AMBIGUOUS_QUOTE_ORDER. Identical records at one priority are inspected together. Incomplete delivery coverage before a possible transition returns INSUFFICIENT_QUOTE_COVERAGE. On unresolved rows the active stop and activation describe the last verified state.

An open result includes the original levels, activation threshold in points, active stop in points and the exact activation quote, side and event ID once armed. An identical replay, or re-reading the same cutoff after storing a later result, returns identical bytes and a single persisted row for that cutoff. Event IDs bind the immutable fill, management, policy and tape, and stay stable as the evaluation cutoff advances.

## Synthetic acceptance evidence

Q is the historical qualification time. Each arm quote below is delivered at Q+4 ms (sequence 7) and each gap at Q+5 ms (sequence 8). Expected and observed values agreed.

| Direction and fill | Original stop / target | Arm bid / ask | Armed stop | Gap bid / ask | Exit |
| --- | --- | --- | --- | --- | --- |
| LONG 1.10314 ASK | 1.10214 / 1.10514 | 1.10414 / 1.10419 | 1.10314 | 1.10310 / 1.10315 | 1.10310 BID |
| SHORT 1.10306 BID | 1.10406 / 1.10106 | 1.10201 / 1.10206 | 1.10306 | 1.10308 / 1.10311 | 1.10311 ASK |

Each direction has paired tapes ending in an armed-stop gap or a target exit. Re-querying Q+3 returned the original open stop and no activation. Re-querying Q+4 returned the identical open, armed result. Tests also cover wrong-side-only touches, a miss followed by the original stop, direct target jumps, two priorities in one millisecond, ties in both input orders, identical duplicates, and a one-millisecond coverage gap.

Four fixed-exit canonical payloads (both directions, open and closed) were captured through the public service on merged main with deterministic fixture IDs before changing the simulator. The regression compares their UTF-8 bytes, including all IDs. Existing pinned MARKET V2, LIMIT V1 and STOP V1 payload comparisons, historical source snapshots and the MARKET V1 cutoff gate are retained. The fixtures are synthetic. The November 13 archive is a separate negative control.

## Commands and rollback

```powershell
.venv/Scripts/python.exe -m pytest -q research_backend/tests/test_offline_break_even.py
.venv/Scripts/python.exe -m pytest -q -s research_backend/tests/test_offline_break_even.py research_backend/tests/test_offline_first_exit.py research_backend/tests/test_offline_market_entry.py research_backend/tests/test_offline_limit_entry.py research_backend/tests/test_offline_stop_entry.py research_backend/tests/benchmark_dukascopy_nov13.py::test_nov13_market_entry_negative_control
.venv/Scripts/python.exe -m pytest -q
git diff --check
```

Revert the follow-up implementation commit to restore fixed-only validation. No schema migration is involved. Keep persisted research rows and historical source records for audit. Trailing stops, partial exits, time exits, account publication and broker routing remain outside this increment. Public admission stays at 160 cutoffs, historical intents retain AWAITING_EXECUTION_VALIDATION and production recorded trading remains BLOCKED.
