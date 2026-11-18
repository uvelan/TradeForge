# TF-I08C cross-symbol SMT causal evidence review

The reviewed TF-I08B head was 7126e1cd8bbb4707df04e7f41d6d9457cd63cc28. Its [push workflow](https://github.com/uvelan/TradeForge/actions/runs/44300093569) and [PR workflow](https://github.com/uvelan/TradeForge/actions/runs/44300105408) passed on attempt 1. The PR had no unresolved review threads and was merged into main as a25839025bad7309fd966d278d90d0ad1f2219ff. The same merge is the TF-I08C base. The implementation commit is a12956790676c65ea6189e650a10d3189c91d930. The commit containing this report records local execution evidence, and GitHub checks on the pull request identify the exact tested branch head.

## Detector contract

Strict SMT algorithm version 1 uses timing policy 3 and evidence references 1. The reference version gains an optional SMT participant map. Existing single-symbol references retain their original shape. The only strict relationship is POSITIVE. For highs use sign +1 and for lows use sign −1. Calculate each side's change as sign × (new wick − previous wick) / previous wick × 10,000. One side must be strictly above +tolerance and the other strictly below −tolerance. An equal extreme, distance on the tolerance boundary, or absent peer never qualifies. The legacy API uses the same arithmetic and nearest-peer helper and retains its journal, alerts, and correction handling.

Both feeds use the same provider, price basis, UTC candle-open timeframe grid and declared calendar. Each feed supplies a separate range, manifest, symbol, dataset/feed identity and source authority. The UTC intersection determines the analytical window. The archive loader checks every selected manifest entry against the catalog and the stored bytes. Duplicate, overlapping or misplaced candles fail closed. Weekday intervals are derived with the calendar's IANA timezone. A session boundary resets pivot pairing. Closed periods contribute zero confirmation slots.

A pivot's right-hand confirmation candle determines its nominal confirmation. Its actual availability is the maximum recorded arrival over all constituent candles. To close a primary matching window, both feeds must supply every expected slot from the earliest possible peer's left edge through the latest possible peer's right edge, inclusive of its right-hand confirmation candle. The alignment bounds are inclusive. Pair in ascending primary UTC order with the nearest eligible comparator, earlier UTC time breaking ties. A matched comparator and all older comparators cannot be assigned to subsequent primary pivots. Incomplete continuity blocks later pairings until an independent declared session. This conservative policy does not infer a negative from absent data.

The market-event hash binds the pair's provider/feed/symbol/calendar policies, effective rules, four pivot witnesses and both matching windows. Frozen partition IDs and range endpoints bind the exact calculation snapshot. This preserves reconstructible IDs across unrelated rolling prefixes while a relevant wick, timestamp, arrival or matching change requires a new identity. Lifecycle revision IDs hash their relevant witness and previous revision. Cursors bind the exact snapshot and cutoff. Pins bind the exact two producer snapshots with side-qualified local IDs. The registry checks four eligible pivot edges, their scope and their visibility by the barrier. Existing single-symbol checks still reject attempts to use a cross-symbol SMT edge.

## Lifecycle

| State | Transition or assessment |
| --- | --- |
| PENDING_MATCH | The common cutoff or recorded coverage has yet to establish matching. No strategy publication. |
| SMT_CANDIDATE | Two complete matched pairs satisfy the strict divergence inequalities. Relevant pre-activation laggard observations must be present and avoid a breach. Candidate lineage is research-only. |
| SMT_CONFIRMED | The requested count of consecutive market-open shared observations is available and there is no observed disqualifying laggard wick. Zero bars confirms at candidate availability. An ambiguous straddling laggard wick blocks whole-candle confirmation. |
| SMT_INVALIDATED | An independently recorded laggard wick breaches its prior pivot after activation. A simultaneous breach takes precedence over confirmation. Historical confirmations remain visible. |
| EXPIRED | The complete configured laggard observation horizon has elapsed. A later confirmation arrival cannot revive it. |
| UNCERTAIN | An open laggard observation is missing, a wick straddles activation ambiguously, or the frozen window ends before current validity can be assessed. Historical confirmation and current eligibility remain independent. |

The API lists bounded history separately from current state. API replay uses the common cutoff, never writes to the legacy journal, and delivers no alerts. In the Market Terminal, select the saved data source and comparison, then open SMT monitor, select Strict causal SMT and declare the market hours. Existing chart markers display legacy SMT. Strict evidence and references are inspected in the monitor.

## Source readiness

The MT5 history import calls the broker for historical rates. Those rows carry broker timestamps for candle intervals but lack a server-recorded close-arrival time. BI5 inputs retain tick timestamps and source order. The existing candle aggregation provides event-time OHLC and does not preserve real candle arrival evidence. The frozen replay path may add nominal-close availability for old data. Strict SMT re-reads the immutable archive and accepts recorded timestamps only with the stored SERVER_RECORDED_ARRIVAL_V1 marker, explicit closed flag, and UTC arrival at or after nominal close. The ordinary import paths implement no writer for that marker. A catalog checksum authenticates local bytes against the frozen manifest. It establishes neither provider authenticity nor the accuracy of an upstream clock.

**Existing MT5 and Dukascopy archives are not ready for strategy-eligible strict SMT.** The verification fixtures write known arrival observations to isolated Parquet archives through the existing storage code. No data migrations, live capture changes, new alerts or trading features are included.

## Exact reproductions

- The M1 fixture uses highs 15 → 17 for EURUSD and 30 → 28 for GBPUSD at UTC offsets 120 and 360. The default one-bar test with zero alignment window makes the candidate available at 540 and the confirmation at 600. The bullish fixture reverses the wick side. A third fixture reverses which instrument leads.
- A constituent delayed from nominal availability 540 to 550 postpones candidate availability to 550 on either feed. The first full post-activation candle starts at 600. Its delayed arrival at 685 overrides the nominal close at 660.
- With the primary missing at offset 540, the laggard breach at 600 invalidates the candidate before confirmation. A breach at 660 instead preserves the confirmation at 600. Rewind to immediately before the breach hides its revision and re-advance reproduces its identity.
- The nearest-peer fixture delays the comparison pivot at 600 until 1005 and establishes that neither the earlier delivery of the farther peer nor the nominal window end at 960 closes the match early.
- An unrelated rolling prefix is advanced 100 times. Market-event and candidate revision IDs remain fixed while the exact attestation changes. A correction to a non-extreme wick-window member invalidates the old cursor and changes relevant IDs.
- A browser fixture exercises delayed scope switching and shared frozen replay cutoffs. Its [confirmed replay screenshot](evidence/TF-I08C/strict-smt-confirmed-replay.png) uses mocked HTTP responses. Backend tests independently verify the actual archive route and registry.

## Verification and performance

| Check | Result |
| --- | --- |
| Backend pytest | 418 passed, including 33 new strict SMT cases and the approved 22 legacy golden fingerprints. The reviewed prerequisite report recorded 385 passed. |
| Vitest | 8 passed across 3 test files. |
| TypeScript and Vite | Passed. Vite reported existing MUI module directive warnings. |
| Full Chromium, zero retries | 65 passed in 4.7 minutes on the final implementation. The first run passed 64 and failed the session-overlay browser case. It interacted with controls while H1 candles were loading. Waiting for the H1 response and a visible chart fixed the test race without changing the assertions. |
| Isolated browser cases | 3 passed, including the corrected session-overlay case and strict SMT running alongside FVG, order blocks, and CRT. |
| Drawing/replay checkpoints | 20 of 20 consecutive passes with zero retries. The independent delayed-analysis stress case also passed. |

Commands and raw outputs are in the [execution evidence directory](evidence/TF-I08C/). Local backend execution used Python 3.13.14. Local frontend execution used Node 24.14.0 and npm 11.1.0. The browser verification used Chromium through Playwright with isolated API and UI ports 8185 and 4185.

The [benchmark reproduction](evidence/TF-I08C/benchmark.py) runs in an isolated SQLite and Parquet store and compares the prerequisite detector's mathematical path with the final archived strict path. The two paths have different responsibilities and outputs. It samples replay rewind and re-advance separately from the warmed paired timings. Python allocations are measured separately so tracing does not skew the latency samples. The first benchmark attempt measured the dense 2,000-candle case and then stopped when the CRT companion correctly rejected an M1 timeframe. Its original output is retained, and the corrected run uses M5 throughout.

| M5 candles per feed | Case | Legacy detector P50 / P95 ms | Strict calculation P50 / P95 ms | Strict ASGI P50 / P95 ms | Peak Python MiB | Response bytes |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 2,000 | Dense | 137.232 / 162.510 | 291.149 / 454.648 | 297.941 / 389.233 | 14.69 | 118,595 |
| 10,000 | Sparse | 276.447 / 365.736 | 531.221 / 695.560 | 551.059 / 794.753 | 40.91 | 128,553 |
| 10,000 | Dense | 3952.159 / 5732.828 | 1576.368 / 2023.820 | 1582.444 / 1993.629 | 73.00 | 119,702 |
| 2,000 | Delayed and missing | 66.926 / 92.160 | 135.421 / 242.427 | 141.456 / 198.754 | 9.31 | 127,503 |

Each row uses 30 warmed, serial, paired samples on Windows 11 with Python 3.13.14. The strict ASGI lane uses FastAPI TestClient without a network socket. The 10,000-candle sparse mixed workload, consisting of the strict endpoint followed by an analytical request for structure, FVG, OB and CRT, measured 777.081 / 1108.483 ms. Its companion request executed structure twice and each other engine once. SMT alone executed two pivot streams, one per feed. The dense 10,000 case verified and decoded 10 partitions with 20,000 records and produced 8,325 registry publications and 19,972 dependency lookups. Windows process working-set high water reached 292,132 KiB across the benchmark. This includes Python, Arrow and the ASGI test client. Serialization alone ranged from 0.699–0.778 ms P50 and 0.861–1.239 ms P95 for eight delivered historical events, including authoritative current state. The dense 10,000 replay rewind and re-advance measured 1611.443 and 1697.953 ms once each on overlapping archives. Every evaluation rechecks stored bytes.

Candle hashes are computed once per usable candle and reused in pivot windows, match windows, lifecycle witnesses and revisions. Registry publications still sign the full canonical record. The dense lane attests thousands of records even though delivery is paginated, and its 73 MiB Python peak identifies retention and signature costs for follow-up profiling. Benchmarks use a 12-bar expiry and do not establish worst-case latency for every configuration.

## Known limitations and rollback

The calendar is declared, not provider-authenticated. The current coverage policies support continuous sessions and explicit weekly forex scheduling. Holidays, special closes, cross-provider pairs, inverse relationships and unprovable wall-clock order remain outside the strict contract. After a missing market-open candle, the adapter declines to reconstruct later match continuity in that session. Strict current validity is bounded by the configured expiration horizon and the available archive. Browser checkpoint and drawing regression remain separate from frozen SMT playback. The adapter publishes analytical evidence only and has no production recorded-arrival writer.

Rollback by reverting the TF-I08C implementation commit and its evidence commit on the feature branch. Existing database schemas, archives, legacy SMT journal and alert identities require no rollback or migration.
