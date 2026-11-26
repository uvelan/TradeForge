# Monthly BID-rise replay across scheduled closures

Starting PR #37 head `43e0c2aaea38f068e7b2f482cb73cda434554a49`. The named rule remains the first 10-point BID rise per UTC day. V2 makes a scheduled closure a terminal uncertainty for an outstanding decision. A pending order retains zero units; an open position retains its one unit and original fill. Neither can execute on reopening. A flat strategy establishes a fresh anchor on Sunday.

## Public API reproduction

These are controlled BI5 files imported into disposable catalogs. All requests used the normal monthly inspection and advance endpoints. Within each pair only the final Friday observation millisecond changed. The Sunday opening quote was 1.09900 BID / 1.09902 ASK, observed exactly at reopening. Times below are UTC.

| State on Friday | Last quote | V1 observed | V2 observed |
|---|---|---|---|
| Open, filled at 1.10012 ASK | close−2 ms | One unresolved open-market gap | Same one unresolved event, one unit remaining, zero Sunday exits |
| Open, same fill | close−1 ms | Sunday BID stopped the position | One scheduled-closure unresolved event, one unit remaining, zero Sunday exits |
| Qualified, awaiting first later ASK | close−2 ms | One unresolved open-market gap | Same one unresolved event, zero units, zero fills |
| Qualified, awaiting first later ASK | close−1 ms | Filled on Sunday at 1.09902 ASK | One scheduled-closure unresolved event, zero units, zero fills |
| Flat | Either | Sunday qualified and entered | Sunday qualified at reopen+10 ms and entered at reopen+20 ms |

For November 14–16 the weekly calendar closed Friday at 22:00 and reopened Sunday at 22:00 (48 hours). For March 7–9 it closed Friday at 22:00 and reopened Sunday at 21:00 (47 hours, US spring DST). Both calendars produced the table above. The V1 baseline failed four of 12 matrix cases. With the close−2 ms quote, the unobserved open interval was exactly [close−1 ms, close). The close−1 ms case had an empty open gap. Its V2 event records the closure time and the exact preceding quote. The closed interval itself contributes no missing open time. The positive continuous two-file control still produced one target at 1.10230 BID.

## Restart and historical compatibility

Each restart test seeded one database then copied it before any replay decisions. An uninterrupted child process produced the reference. Independent processes advanced the other copy to the last Friday batch, 24 hours into closure, just after the first reopening batch, and finally month end. Every new process retried the last committed batch and compared its full payload. The final ordered batch hashes (including coverage, quantities, event IDs and source pins) matched:

| Fixture | Uninterrupted and restarted ledger SHA-256 |
|---|---|
| November, close−2 ms | `413a9d495a6310c5c1da90202f6d945240b8819b36c07ddef52c9937436826a9` |
| March DST, close−1 ms | `0e687e879d672fffed14b5cc6de5d481090deda19983a67c3b0cb902a0b8f457` |

V1 resolved uncertainty only inside the function that recorded a nonempty open-market gap. At close−1 ms its cursor equalled the closing boundary; clearing the cursor left the position executable. V2 first accounts for any real open gap, then marks any still-executable outstanding position unresolved at closure. The first cause wins.

The V1 writer at the starting SHA wrote the committed [Friday checkpoint fixture](../../../research_backend/tests/month_weekend_v1.json). Its session starts with `39d7d97a` and its checkpoint digest starts with `98d67fd1`. Against the original database, inspection and exact retry returned the same V1 checkpoint (HTTP 200). Attempting new work returned HTTP 409. All 20 original checkpoint rows, serialized payloads and hashes were unchanged. The regression also checks quote lookup and receipt pagination. Strategy Lab labels legacy sessions read-only and offers the current inventory for a fresh replay.

## Genuine archive and identity

On November 13, both approved Dukascopy EURUSD BI5 files supplied 8,636 observations over [13:00,15:00). The raw-file oracle and both service versions produced qualification at ordinal 5, entry at 1.16243 ASK at ordinal 6, and stop at 1.16143 BID at ordinal 1564. Every quote, anchor, partition ID, checksum, millisecond, side, price and remaining quantity matched field by field. Qualification, fill and stop IDs all changed because the policy enters the session fingerprint. The [control receipt](month-weekend-control.json) maps each V1 ID to its V2 successor and records the restarted V2 ledger.

The operational November inventory still has two registered hours, 480 missing open hours and 238 scheduled closure hours. The authorized local fetch for November 2 at 22:00 UTC returned HTTP 429. The verification compared the complete missing-hour list against the [accepted inventory](month-replay.json). Genuine prices from the two-hour window do not span a weekend. Full-month market acceptance remains open.

## Reproduce locally

```powershell
.venv/Scripts/python.exe -m pytest -qs research_backend/tests/test_month_weekend.py research_backend/tests/test_month_replay.py
.venv/Scripts/python.exe -m scripts.verify_month_replay --browser --output storage/weekend-november-v2.json
.venv/Scripts/python.exe -m scripts.month_archive 2025-11 --source storage/t06-input
.venv/Scripts/python.exe -m scripts.month_archive 2025-11 --source storage/t06-input --fetch
.venv/Scripts/python.exe -m pytest -q
npm --prefix frontend test
npm --prefix frontend run build
```

For the baseline, run the weekend matrix against the starting revision's service using the same two BI5 fixtures. Use a disposable database. For normal operation, select November 2025 in Strategy Lab, inspect coverage, and run the current monthly policy.

Final local checks on implementation `69784dccb86e6a6bebd421a3f3a9a5f49492c0e4`: 18 focused replay tests passed, including the continuous two-file control. The full backend suite passed 1,086 tests with one skip and one Starlette deprecation warning in 504.73 seconds. Frontend unit tests passed 13/13, the production build succeeded, and the genuine-data Strategy Lab Chromium journey passed with zero retries.

## Rollback

Stop monthly replay writes and back up the quiescent workspace. For an operational rollback, run the previous build against a pre-upgrade workspace copy and retain the V2 database separately for inspection with the corrected reader. V1 code predates the policy check and must not advance a V2 session. Revert this milestone on the integration branch if withdrawing the change. The public strategy cutoff budget remains 160. Broker-statement reconciliation is the next independent major milestone; production recorded trading remains blocked.
