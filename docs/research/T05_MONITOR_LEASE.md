# T05 first slice: background SMT monitor ownership

A poller claims the configured watch in a SQLite transaction before requesting
paired candles. A second poller skips an unexpired lease. Each claim has a
unique owner and the existing watch generation. The lease lasts 30 seconds and
is not extended while an acquisition is blocked. A later poller may claim at
expiry; a worker that returns after expiry or after configuration replacement
cannot append observations, corrections, or alerts. A disabled watch cascades
its lease removal. The recorder checks generation, owner and expiry inside the
same SQLite writer transaction as evidence publication.

A clean poll releases only its lease. A process exiting without release leaves
a persisted row and a later worker can reclaim it at expiry. The bounded lease
assumes the system wall clock advances; a backwards wall-clock adjustment can
delay expiry, but an obsolete owner cannot replace or release a newer claim.
Existing journals are preserved. Stop workers before deploying or rolling
back so a pre-lease process cannot write concurrently. Rollback does not
require dropping the additive table.

Fixture verification:

```text
python -m pytest -q research_backend/tests/test_smt_monitor_lease.py research_backend/tests/test_smt_authority.py
```

The tests use synthetic MT5 boundary data and an isolated SQLite store. This
slice changes only automated watch polling; explicit chart capture remains
unchanged. It does not establish real MT5 feed/timestamp authority, continuous
monitor readiness, persistent observation frontiers, gap classification, or
bounded backfill. Leave those production gates closed.
