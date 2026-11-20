# TF-I10A.1 bounded CRT/SMT successor review

PR #17 was merged at `d003eb935de788f039d840cba87d742e92861358` after both workflows passed on the approved head `4832cffa42de52af27654c7f7ce84a15be2241fb`. This change branches from that merge on `codex/crt-smt-successor-evidence`. It uses evaluator v6 and the existing checkpoint format. The new historical method `CRT_SMT_ASOF_REFERENCE` is part of run identity. Existing single-attempt and FVG runs retain their calculation methods and stored identities.

## Three-opportunity fixture

[The fixture plan](TF-I10A1-CRT-SMT-PLAN.md) specifies the expected sequence before the scheduler comparison. The fixture writes two verified Parquet partitions of 51 canonical M5 candles per instrument. Availability uses nominal close plus 0 seconds for EURUSD and 20 seconds for GBPUSD. Cutoffs below are seconds after 2020-01-02 10:00:00 UTC. Family IDs and complete SMT participant and pivot references are in the [captured fixture trace](evidence/TF-I10A1-crt-smt/fixture.txt).

| Family | Root / activation / terminal | Independent expectation | Scheduler result |
| --- | --- | --- | --- |
| A | 0 / 3600 / 7200 | Expired, zero intents | `EXPIRED`, `SETUP_EXPIRY_REACHED`, zero intents |
| C | 7200 / 10800 / 14120 | Qualified on the later comparison confirmation, one intent | `TRIGGER_QUALIFIED` at 13:55:20 UTC, one intent |
| D | 10800 / 14400 / 15000 | Invalidated at the opposite CRT boundary, zero intents | `INVALIDATED`, `BOUND_REFERENCE_TERMINAL`, zero intents |

A new family B first appeared at A's terminal cutoff (+7200) and could not start an attempt. A later high-sweep/reclaim revision of A at +7500 could not rearm its spent family. The independent prefixes and scheduler showed no successor attempt at those boundaries.

C reclaimed at +12900 and reached the primary midpoint at +13500. The bearish SMT candidate was available at +13520. It was still the latest lifecycle revision at +14119, with no intent. The peer candle arrived at +14120 and the confirmation referenced both original lanes and their pivots. Under a zero-delay counterfactual the independent reference qualified at +13800. Removing the required peer confirmation candle yielded `INSUFFICIENT_EVIDENCE` with `REQUIRED_SOURCE_COVERAGE_UNKNOWN` and zero intents in the reference. Both public admission endpoints rejected the incomplete archive.

## Independent comparison and publication

The reference recollects canonical archive prefixes and enumerates modeled arrivals, setup release and expiry from the fixture. It supplies successor boundaries from the predeclared scenario and uses the existing rule evaluator for local rule semantics. It obtains no cutoffs, spent families or decisions from the production scheduler or ledger.

At 12 material cutoffs the test compares lifecycle revisions, CRT family and latest revision, phase states, qualification timing, primary and comparison dependencies, complete rule and supporting proofs, and intent presence. The scheduler evaluated 86 distinct cutoffs in the bounded fixture. Chunk sizes 1, 7, 17 and 93 returned identical records, including decision and intent identities. A forced interruption at +14100 left a durable checkpoint and the public decisions and intents endpoints returned empty lists. Resume with chunk size 3 published the same decisions and proofs, with exactly one `AWAITING_EXECUTION_VALIDATION` intent. Validation and enqueue rejected a fixture extension with 194 scheduled cutoffs and eight reserved dynamic boundaries.

## Supported configuration

| Producer and inputs | `SINGLE_ATTEMPT` | `NEW_CONTEXT_FAMILY` |
| --- | --- | --- |
| Existing primary EURUSD M5 FVG | Existing behavior | Existing mandatory ACTIVE FVG alias and budget |
| Primary EURUSD / comparison GBPUSD M5, primary SHORT, latched ACTIVE CRT context alias | Existing behavior | Verified Dukascopy BID, same feed identity, one partition per lane, continuous calendar, modeled delays 0s/20s, default H1 aggregate CRT except four retained ranges, SMT period 2 and alignment 0 with remaining default settings, at most 160 cutoffs |
| Other CRT/SMT configurations, including the eight approved real cases | Existing behavior where valid | HTTP 422 at validation and enqueue |
| Admitted CRT/SMT configuration with missing archive coverage or excessive cutoff schedule | Existing single-attempt checks | HTTP 422 before queueing |

The fresh-prefix scheduler uses the 160-cutoff limit and reserves two dynamic boundaries per possible CRT attempt under the pinned hourly configuration. The normal worker verifies the archive again before execution. Completion remains the gate for public result access. No reusable prefix optimization or full-session equivalence is established by this fixture. Reverse direction, other delays and calendars, other producer settings, and multiple partitions per lane remain outside the admission scope.

## Verification and rollback

Local verification passed 718 backend tests, including seven new fixture cases. The separately targeted run passed 55 evaluator and history regressions, and the legacy golden fingerprint passed. The backend suite left all 44 inventoried workspace archive files byte-for-byte unchanged. TypeScript compilation and the production build passed, Vitest passed 8 tests, and Chromium passed all 71 tests with zero retries. The replay checkpoint repetition passed 20/20, the delayed-response replay check passed 1/1, and cold arrival capture passed 1/1, each with zero retries.

The [evidence directory](evidence/TF-I10A1-crt-smt/) contains command outputs for the focused fixture, historical/evaluator regressions, legacy goldens, syntax, complete backend suite and workspace archive inventory, frontend compilation/build, Vitest, and Chromium. The [real archive rerun](evidence/TF-I10A1-crt-smt/real-session.json) used a checksum-verified disposable archive copy. All eight approved single-attempt cases expired with zero intents. Both historical admission endpoints returned HTTP 422 for the full-session and 50-minute successor variants, with zero published decisions.

Run git revert on implementation commit 35915ab75d6f231d4ebd3577510d03cb8425ffdf to restore the prior admission policy. Before a rollback, cancel or drain CRT/SMT work using the new method. Retain completed runs and their recorded proofs for audit. No schema migration or data rewrite is required. This phase leaves TF-I10A.2 pending and production recorded trading **BLOCKED**.
