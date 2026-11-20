# TF-I10A.1 historical strategy evaluation review

Base is the authorized PR #16 merge `0ae62d83f76ecfd8228569071e29db4bfe4fed6c`. Development is on `feat/TF-I10A1-incremental-historical-evaluation`. [PR #17](https://github.com/uvelan/TradeForge/pull/17) records the review head and individual workflow runs.

## Contracts and causal timing

Historical run v1 pins a ready schema 2 strategy revision, evaluator v4, binding policy 2, the normalized definition digest, producer versions, the exact frozen source and partition digests, UTC windows, session calendar, instrument roles, quote basis, modeled per-feed delays, rearm policy and resource budgets. Source admission requires either an exact server-approved catalog entry or immutable Dukascopy import lineage. The archive is checksum-verified on calculation and checked against the complete expected schedule through the final modeled close. A caller cannot supply an archive path or replacement strategy.

The canonical evaluator advances against a coherent evidence set at each availability cutoff. The historical scheduler uses candle deadlines, publications, uncertainty and recovery boundaries, session deadlines, evaluator-generated expiry and setup boundaries, and the explicit endpoints. The v3 regression used two maintained FVG aliases with a same-phase AFTER relation and a predecessor revision. Reversing the ALL children changed the result between INVALIDATED and WAITING_FOR_TRIGGER. V4 evaluates maintained dependencies in graph order. Same-phase temporal dependencies use the latest verified predecessor. Cross-phase dependencies remain anchored to the original activation. Stored v1, v2 and v3 decisions remain readable and immutable.

## Producer support

| Producer | Supported mode | Boundary |
| --- | --- | --- |
| FVG | Verified bounded-prefix reuse for the version and fixture scope | Single verified archive prefix including wick and lifecycle publications. Both directions had exact event and decision comparisons at fixture cutoffs. |
| Structure | Independent reference, or bounded independent prefix checks | Confirming pivots and protected swing revisions require per-cutoff checks. |
| Liquidity | Independent reference, or bounded independent prefix checks | Pool formation, revision and expiry depend on structure. |
| Order blocks | Independent reference, or bounded independent prefix checks | Structure dependency and lifecycle timing require independent verification. |
| CRT | Independent reference, or bounded independent prefix checks | Complete HTF constituents, reference rollover and expiry. Full-session multi-attempt is unsupported. |
| SMT | Independent reference, or bounded independent prefix checks | Pair arrival timing, matching frontier and uncertainty recovery. Full-session multi-attempt is unsupported. |

The FVG path calculates the bounded canonical producer once per worker invocation and steps the evaluator over its indexed publication prefix. It does not claim a mutable streaming producer. The other multi-producer path compares publications, eligibility, uncertainty and coverage against fresh canonical calculations before using each prefix. It fails closed on divergence or more than 160 cutoffs. Only the calculation-snapshot coordinate and the enclosing evaluation horizon are normalized in those comparisons. Candle, event, family and availability identities are retained. The FVG test independently compares canonical decisions as well as publication prefixes. Generalized independent multi-attempt decision equivalence remains unverified.

## Attempts, persistence and worker recovery

SINGLE_ATTEMPT retains the reference evaluator's first terminal opportunity and publishes its result as a single transaction. NEW_CONTEXT_FAMILY requires a mandatory positive context-family alias. After a terminal decision, a successor must bind a different family with eligibility strictly after that terminal cutoff. Terminal decisions, checkpoints, attempts and intents have unique identities and reject updates and deletes. A stored checkpoint pins the verified calculation snapshots and restores exact proofs. The worker reopens the pinned archive on resume and reconstructs the bounded producer data once, then continues strictly after the committed frontier.

The worker runs in a separate local child process, with one global lease, eight queued runs, a 150-second supervised wall-clock limit and a sampled 768 MiB working-set threshold where supported. Publication uses a fresh lease check in a SQLite immediate transaction. Cancellation fences writes before process termination. Safe resume starts at the last committed checkpoint after cancellation, interruption or lost lease. The external deadline and cancellation tests terminate an actual uncooperative subprocess. The memory test injects a measured-threshold crossing and verifies termination, without allocating 768 MiB. A supervisor failure can delay process cleanup until that child reaches its next cooperative check. The lease still prevents publication after expiry.

The mirrored canonical FVG fixtures establish invalidation, rearm, subsequent qualification, independent context families, stable intent identity, exact checkpoint resume and duplicate chunk handling. The transactional failure test injects an invalid row after a valid one and verifies that neither the decision nor its frontier survives rollback. A distinct three-opportunity CRT/SMT fixture, full-session multi-attempt verification and per-opportunity suppression accounting are still outstanding.

## Real acceptance and performance

Eight November 13, 2026 Dukascopy BID EURUSD and GBPUSD M1/M5 cases used the original LONG and SHORT settings, each with 0/0, 2/2, 2/15 and 15/30 second modeled delays. All eight SINGLE_ATTEMPT runs reproduced EXPIRED with zero qualified intents. All eight full-session NEW_CONTEXT_FAMILY runs exceeded the independent-prefix cutoff budget and published zero decisions. All eight 50-minute NEW_CONTEXT_FAMILY windows completed with independently checked producer prefixes, one context family, one opportunity pending at the horizon, and zero qualified intents. See [real session evidence](evidence/TF-I10A1/real-session.json) for the pinned fixture, exact prior decision identities and independently checked CRT OHLC and availability.

The opt-in historical prefix benchmark compared 100 serial independently collected canonical FVG prefixes against one verified bounded calculation and 100 prefix projections, with 30 warmed samples on Windows, Python 3.13.14. Prefixes matched independently before timing.

| Path | P50 | P95 | Peak Python allocations | Process peak working set |
| --- | ---: | ---: | ---: | ---: |
| Independent canonical prefixes | 1,279.445 ms | 1,383.195 ms | 5,049,756 bytes | 128,438,272 bytes |
| Shared bounded canonical prefix | 26.006 ms | 37.542 ms | 2,031,001 bytes | 129,736,704 bytes |

The bounded sample used 123 M5 candles, one producer, one partition decode and seven publications. Its maximum serialized checkpoint in the harness was 1,137 bytes. The approximately 49x median improvement applies to prefix collection and projection. Working-set peaks were sampled cumulatively in one process. Separate checkpoint-save, checkpoint-restore, full-session multi-producer throughput and end-to-end v3/v4 benchmarking remain unverified. See [raw benchmark](evidence/TF-I10A1/prefix-benchmark.txt).

## Verification commands

- `python scripts/verify_strategy_test_isolation.py` runs backend regression tests and hashes workspace archives before and after.
- `python -m pytest -q research_backend/tests/test_historical_strategy_runs.py research_backend/tests/test_historical_dependency_order.py research_backend/tests/test_strategy_evaluator_integrity.py` exercises incremental, legacy and lifecycle contracts.
- `python -m pytest research_backend/tests/benchmark_historical_prefix.py -s -q` runs the opt-in 30-sample benchmark.
- `python scripts/verify_real_strategy_evaluation.py --historical` checksum-verifies, copies and evaluates the approved archive in temporary storage.
- `python -m compileall -q research_backend scripts`, `npm run build`, `npm run test` and `npm run e2e -- --retries=0 --reporter=line` cover syntax, production build, Vitest and Chromium. The offline Playwright app explicitly disables local MT5 access so installed terminals cannot hold the demo and strategy requests.

[Historical Strategy Lab screenshot](evidence/TF-I10A1/historical-evaluation.png) shows a completed historical run with its qualified intent. [Initial v3 ordering reproduction](evidence/TF-I10A1/ordering-baseline.txt) records the original defect. [Earlier browser smoke failure](evidence/TF-I10A1/browser-target.txt) and [isolated browser smoke](evidence/TF-I10A1/browser-historical.txt) record the MT5 dependency issue and recovery.

## Results

- Backend passed all 675 tests, including the existing 19 observation mappings, 22 numerical fingerprint cases, evaluator closure, historical import and arrival-capture regressions. The SHA-256 inventory on the local archive was byte-identical for all 44 files.
- Focused evaluator and research regression run passed 56 tests. Python syntax compilation, eight Vitest tests and the TypeScript/Vite production build passed.
- Clean serial Chromium run passed 71 tests with zero retries. Earlier attempts recorded 16 timeouts while local MT5 was active, followed by an interrupted run with mixed server/worker source revisions and overlapping Playwright artifact directories. The clean run started fresh services on the final source and used a dedicated temporary artifact directory.
- Drawing and replay checkpoint passed 20 consecutive repetitions. Delayed analytical and persistence responses and a fresh-server Data Library each passed one test with zero retries.
- Local results are recorded in [Python](evidence/TF-I10A1/backend-final.txt), [focused](evidence/TF-I10A1/focused-final.txt), [Vitest](evidence/TF-I10A1/vitest.txt), [build](evidence/TF-I10A1/build.txt), [Chromium](evidence/TF-I10A1/chromium-full.txt), [checkpoint repetitions](evidence/TF-I10A1/chromium-checkpoint.txt), [delayed responses](evidence/TF-I10A1/chromium-stress.txt), and [Data Library](evidence/TF-I10A1/chromium-cold.txt).
- Both push and PR workflows on the first implementation commit passed. The PR's checks tab distinguishes the latest push and pull-request workflows from earlier commits.

## Execution handoff and rollback

A future simulator should accept only an immutable intent with its historical-run ID, attempt ID, decision ID, strategy revision, exact proof references, symbol, direction, order type, price basis and pinned policies. It must enforce the strict qualification boundary and expiry, independently verify executable inputs, and maintain its own order and position lifecycle. The intent status is AWAITING_EXECUTION_VALIDATION. Production recorded readiness is BLOCKED. There is no authorization to advance to TF-I10A.2.

Rollback is to revert the feature commits and restart the local service. SQLite additions are additive. Keep the new tables and their immutable records for audit, and retain the capture and archive data. Older software can still read its own evaluation records, but cannot use v4 or historical-run v1 through its APIs.
