# TF-P01: durable offline paper-order ledger slice

The isolated PAPER ledger models one account and one instrument, with integer cash
and price units per whole quantity unit. It accepts manual immediate market requests
and injected bid/ask evidence only. A buy pays the ask; a sell receives the bid and
cannot exceed the paper position. Cash cannot go negative. Stale or future evidence
and insufficient balances become immutable rejections. There is no shorting, margin,
commission model, spread override, live authority or network adapter.

Each order ID has exactly one terminal outcome, scoped to its dedicated SQLite
database. An exact retry returns the original fill/reject. Cancellation can bind the
same not-yet-processed order ID first; a competing or subsequent submission then
returns the cancellation. Once processing has won, a cancel cannot undo the fill or
create an extra event. The SQLite write transaction serializes separate connection
instances. These are immediate paper market orders; this is not a general outstanding
limit/stop simulator or a broker cancel protocol.

The repository refuses unrelated SQLite databases and mismatched opening policies.
It replays every stored event against its original evidence and recomputes post-event
balances before accepting a new action. The 1,024-event cap bounds replay and storage
per account. Existing application databases, endpoints and UIs are untouched.
Use a dedicated, process-controlled file; this ledger does not authenticate users,
verify the provenance of manually supplied quotes or protect against a party with
arbitrary filesystem/SQLite write access.

```text
python -m pytest -q tests/test_paper_ledger.py
```

Further paper workflows require route/UI integration, policy and fees, multi-instrument
portfolio accounting, compatibility and authentication review, and broader recovery and
load testing. No broker or account credentials are required or handled.
