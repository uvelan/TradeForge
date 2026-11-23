# TF-I10A.2 offline fixed-price LIMIT entry validation

PR #22 merged at `117d939cfafeb7b67e97091af11c5a4c582b55eb` from reviewed head `c74f113f3e32bfbb0cbb1eac14b87e318c9e513b`. This increment uses the same offline service and CLI as [MARKET V2](TF-I10A2-OFFLINE-MARKET-ENTRY.md).

## Execution policy

`OFFLINE_FIXED_PRICE_LIMIT_ENTRY` version 1 uses the published `FIXED_ABSOLUTE` price, its declared price basis, the symbol point scale and checksum-pinned synthetic bid/ask quotes. The service converts the serialized decimal limit to points exactly and rejects off-grid prices with `UNSUPPORTED_LIMIT_PRICE_GRID`, zero orders and zero fills. It never rounds a limit to a nearby tick. A LONG touches at ask ≤ limit. A SHORT touches at bid ≥ limit. Fills use the executable quote, including price improvement. The declared basis remains in the result even when the executable side differs. The result includes the exact quote, source sequence, observation and delivery timestamps, price in units, and scale.

The MARKET V2 eligibility window and sort order also apply to LIMIT. Observation and delivery must be strictly after qualification, and delivery at expiry is allowed. The first possible touch is selected by delivery, observation, then source sequence. Coverage must be continuous through that delivery. All rows at the selected priority are compared, including a row that misses the limit. Conflicting rows at that priority are ambiguous, even if a later quote definitely touches. Conflicts after a certain fill cannot change it. Identical rows give a single simulated fill. If no eligible quote touches, complete coverage through expiry gives `VERIFIED_EXPIRED`. A shorter prefix with no touch leaves execution unresolved under `INSUFFICIENT_QUOTE_COVERAGE`.

## Expected and observed

The service tests publish real historical fixture records through the normal API and execute them against labeled synthetic quote tapes. Qualification is `1577973320000` UTC milliseconds and expiry is `1577973620000`. All quoted prices are EURUSD. The full evidence records the tape fingerprint and the winning quote or rejection for every case.

| Input | Expected | Observed |
| --- | --- | --- |
| Merged baseline, published LONG LIMIT `1.10310` | V2-era service returns unsupported | `UNSUPPORTED_ENTRY_TYPE`, zero fills |
| LONG, prefix with bid `1.10309` / ask `1.10312` | No touch on ask, no fill on the covered prefix | Zero fills, `INSUFFICIENT_QUOTE_COVERAGE` for expiry |
| LONG, followed by `1.10307 / 1.10310` | Fill on ask `1.10310` at +2 ms | Exact ask, sequence 5 at +2 ms |
| LONG, first qualifying quote `1.10305 / 1.10308` | Improve to ask `1.10308` | Exact ask, sequence 4 at +1 ms |
| SHORT, prefix with `1.10308 / 1.10311` | No touch on bid, no fill on the covered prefix | Zero fills, `INSUFFICIENT_QUOTE_COVERAGE` for expiry |
| SHORT, followed by `1.10310 / 1.10313` | Fill on bid `1.10310` at +2 ms | Exact bid, sequence 5 at +2 ms |
| SHORT, first qualifying quote `1.10312 / 1.10315` | Improve to bid `1.10312` | Exact bid, sequence 4 at +1 ms |
| Earlier or equal-qualification observation delivered late | Ignore both, fill on the next eligible touch | Ask `1.10310`, sequence 5 at +2 ms |
| Touch delivered at inclusive expiry | Fill on sequence 6 at expiry | Ask `1.10309` at `1577973620000` ms |
| Sealed window with no eligible touch | Verified expiry | `VERIFIED_EXPIRED`, zero fills |
| One-millisecond gap before apparent fill | Insufficient coverage | `INSUFFICIENT_QUOTE_COVERAGE`, zero fills |
| Touch/miss conflict before definite touch, both input orders | Ambiguous | `AMBIGUOUS_QUOTE_ORDER`, zero fills in each order |
| Same conflict after definite touch, both input orders | Preserve first touch | Ask `1.10310`, sequence 5 at +2 ms in each order |
| Limit `1.103101` on the EURUSD grid | Reject without rounding | `UNSUPPORTED_LIMIT_PRICE_GRID`, zero orders and fills |

The LIMIT baseline and corrected LONG replay share the same published intent and tape fingerprint `34d848b012f195bbdfdfe92e224cfc7dc3171cbd719a9792cfc741aae5f13e3b`. The corrected result has a new identity. Replaying the same LIMIT request reproduces its result and fill IDs and leaves one immutable result per unique input. Tests also compare the source run, decision, attempt, intent and embedded proof bytes before and after replay.

The pinned MARKET V2 request used the same tape. It retained result ID `ME-23b6e1a3cef4b381f3ac3225c66fe02bf65c70e6dcbdb3ae4320da1dd883c2ae`, fill ID `MF-3e04a45b6d3d98bf7b4f89844859c9445bceb4fcbc5711ccbb762274d9d049e8`, and its complete 5,042-byte stored payload. [Merged baseline payloads and tape](evidence/TF-I10A2-limit-entry/merged-baseline.json) · [Focused verification, full fingerprints and quote references](evidence/TF-I10A2-limit-entry/focused.txt)

## Reproduction

The baseline capture was run before editing the simulator using the command below. The capture fixture has since moved to `research_backend/tests/capture_offline_entry_baseline.py` to keep it out of the normal suite. To repeat the capture in isolation, use the merged simulator at the SHA above with this PR's two fixture helpers and run the capture file explicitly. The replay comparison loads the committed capture as an independent expected payload.

```powershell
.venv/Scripts/python.exe -m pytest -q -s --tb=short research_backend/tests/test_capture_limit_baseline.py
.venv/Scripts/python.exe -m pytest -q -s --tb=short research_backend/tests/test_offline_limit_entry.py research_backend/tests/test_offline_market_entry.py research_backend/tests/benchmark_dukascopy_nov13.py::test_nov13_market_entry_negative_control
.venv/Scripts/python.exe -m pytest -q
git diff --check
```

The November 13 negative control requires the approved local private Dukascopy archive from PR #21. The regular suite uses synthetic executable tapes. The private archive is used only to reproduce the completed zero-intent ledger and submit it to the normal offline simulation service. The focused run passed 13 tests and reproduced 14 attempts, zero intents, zero simulated orders and zero fills. The approved historical ledger and original archive fingerprints matched.

## Rollback and scope

Revert this increment's commit to restore the merged MARKET V2 behavior and its unsupported LIMIT result. Keep the additive execution result table and existing immutable rows. Existing historical intents, decisions, proofs and provider data require no migration. This increment covers only offline entry price and time. STOP stays unsupported. Public admission remains capped at 160 cutoffs. Historical intents retain `AWAITING_EXECUTION_VALIDATION`. Production recorded trading remains `BLOCKED`.
