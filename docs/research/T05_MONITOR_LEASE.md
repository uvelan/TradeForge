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
lease changes only automated watch polling; the watch and explicit live chart
capture both use the coverage checks below. It does not establish real MT5 feed/timestamp authority, continuous
monitor readiness, persistent observation frontiers, gap classification, or
bounded backfill. Leave those production gates closed.

## Sparse capture and recovery

Missing observations are not evidence of a source correction. A previously
captured candle must be present and have different OHLC values before it can
cause a correction. A sparse poll preserves existing alerts and immutable
history; it does not undo previously recorded positive corrections.

For new transitions, live capture uses only the latest paired, closed, regular
suffix. Do not replace a missing confirmation bar with a later observation
across a gap. A lagging or misaligned paired tail blocks new transitions; an
interior gap leaves only the contiguous latest suffix eligible. Both the live
response and poll status provide coverage, including a reason when degraded.
This describes only clock spacing and pairing within returned data, not known
broker operating hours or the completeness of a history request. A closed
weekend can therefore be conservatively reported as a gap.

Manual and background fixture tests confirm a previous alert survives an omitted
candle and identical recovery with no duplicate. The same fixtures retain
positive-change retraction. New confirmations cannot span an omitted bar or use
an out-of-date tail, and recovery records the actual observed notification time.

The monitor panel and live chart label degraded coverage. The saved prior alert
is not presented as a fresh confirmation during the gap. The live chart
requests the visible chart cutoff; the background monitor checks against the
current poll time, so only the monitor can assess freshness beyond that view.
