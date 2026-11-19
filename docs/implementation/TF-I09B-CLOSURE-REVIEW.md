# TF-I09B current-predicate closure

The reviewed and fetched starting SHA was `5ea9d90c5bfccaeb8cad081c3c0b58c01dcd269e`. The runtime correction is `919aea46900fb2b65bac40ab87a3a3ccd9d4d576`. The final evidence commit and GitHub Actions runs are linked from [PR #16](https://github.com/uvelan/TradeForge/pull/16).

## Original defect

The [reviewed-HEAD reproduction](evidence/TF-I09B-closure/baseline.txt) used the existing frozen FVG fixture with zero delay. The canonical formation at BASE + 3,000 seconds had boundaries 100 and 105. At +3,300 seconds a close of 104 produced `PARTIALLY_MITIGATED`. This was the latest eligible revision of the same still-active family. The v2 evaluator returned `SATISFIED` for the `ACTIVE` condition using the original formation reference and issued a qualified intent.

The [corrected fixture](evidence/TF-I09B-closure/focused-second.txt) resolves the exact mitigation reference. It reports `ACTIVE_ELIGIBLE` for family viability and `NOT_SATISFIED` for the maintained predicate. The attempt is invalidated and there is no entry intent.

## Activation and maintenance contract

The evaluator's semantic version advances to v3. Saved strategy definitions stay immutable at schema 2 and binding policy 2. New evaluation requests explicitly select v3 and therefore receive distinct run, decision and intent identities. v1 and v2 decision records, evidence and intent IDs are preserved and remain readable. Reconstruction and advancement of old evaluator identities produce an explicit read-only conflict. A user can start a new v3 evaluation from the same saved strategy.

| Requirement | v3 interpretation |
| --- | --- |
| LATCHED context and activated setup | Phase activation and the accepted original observations are recorded at activation. A CURRENT leaf without an established alias is an activation-time requirement in a latched phase. |
| CONTINUOUS context | The context phase remains a predicate requirement. Unbound rules may evaluate new observations according to their selection policy. |
| Established CURRENT alias | Exact family viability and the configured predicate are maintained on the same family. The latest eligible revision must satisfy the existing projection, field/operator/value, age and applicable causal window. |
| Established HISTORICAL proof | The accepted historical observation remains available as a prerequisite. Later lifecycle progression alone does not change the recorded historical predicate. |
| Explicit CRT viability guard | The tracked historical CRT family retains its existing separate viability policy. |

The original proof, latest verification and family validity have separate trace fields. The current result records its own exact revision reference and availability. A confirmed terminal family or known maintained predicate failure invalidates the attempt. Uncertain lifecycle or timing evidence blocks qualification as UNKNOWN. The evaluator never substitutes an unrelated family or recalculates an analytical producer to check maintenance.

## Temporal anchors and regression cases

At context and setup activation the evaluator stores the observations the phase accepted, including non-aliased predecessors. Established cross-phase edges retain the original input proof. A temporal relation uses its predecessor's availability, selection and phase activation boundary as applicable. A later continuous observation can satisfy its own requirement while the latched setup keeps its original causal relationship. The setup's activation remains fixed.

The canonical SMT fixture establishes original context at +1,740 seconds and setup at +1,800. The later context observation is independent. It cannot change the activation proof or turn the out-of-window trigger into an eligible trigger. A separate FVG fixture selects one condition at +3,000 and records the whole-phase and alias binding at +3,300. Latest CURRENT verification uses its own cutoff timestamp.

Canonical tests also cover FVG partial-to-deeper-partial maintenance, historical FVG prerequisite retention, terminal and straddling revisions, order block ACTIVE to TOUCHED and TOUCHED to PARTIALLY_MITIGATED, and both legacy evaluator versions. The existing suite covers same-family isolation, all 19 advertised observations and all 22 approved numerical fingerprints.

The prefix harness compares independently loaded early histories against a later producer snapshot processed through the same cutoff. It retains mechanical outcome, family and event identity, activation and selection times, predicate/verification results, temporal anchors, invalidation, expiry and intent data. The only normalized proof coordinate is the calculation snapshot ID. Comparison excludes the operational resource counts and raw supporting payloads. This verifies the tested lifecycle cases and does not certify unrestricted incremental reuse.

## Evidence and rollback

- [Backend and workspace archive inventory](evidence/TF-I09B-closure/backend.txt)
- [Focused current-predicate and integrity tests](evidence/TF-I09B-closure/focused.txt)
- [Vitest](evidence/TF-I09B-closure/vitest.txt), [production build](evidence/TF-I09B-closure/build.txt), [full Chromium](evidence/TF-I09B-closure/browser.txt), [20 checkpoint repetitions](evidence/TF-I09B-closure/checkpoint.txt), [delayed analysis](evidence/TF-I09B-closure/browser-stress.txt), [fresh-server Data Library](evidence/TF-I09B-closure/data-library-cold.txt)
- [Eight unretuned historical evaluations](evidence/TF-I09B-closure/real-session.json) on the frozen November 13, 2026 EURUSD/GBPUSD Dukascopy BID M1/M5 session
- [v3 dense benchmark](evidence/TF-I09B-closure/dense.json) and [previous v2 measurements](evidence/TF-I09B-integrity/dense-expanded.json), each using the same deterministic dense fixture and 30 warmed serial samples
- [Evaluation screenshot](evidence/TF-I09B-closure/strategy-evaluation.png) and [frozen chart screenshot](evidence/TF-I09B-closure/strategy-evaluation-chart.png)

The serial backend run passed 664 tests and verified all 44 workspace archive files byte-for-byte using SHA-256. The focused closure and integrity suite passed 55 tests. Vitest passed 8 tests and TypeScript/Vite built. Chromium passed all 71 tests with zero retries. All 20 checkpoint repetitions passed, as did delayed-analysis stress and the fresh-server Data Library check.

All eight approved historical runs reproduced their v3 decision IDs at the same cutoff. Both directions remained EXPIRED with BOUND_REFERENCE_TERMINAL in each delay model, 0/0, 2/2, 2/15 and 15/30 seconds. No case qualified. The independent checks reaggregated the accepted archived reference data. Strategy parameters and frozen input checksums were preserved.

| Dense workload | v2 P50/P95 in ms | v3 P50/P95 in ms |
| --- | ---: | ---: |
| Canonical producers | 766.651 / 923.298 | 762.149 / 867.883 |
| Full evaluation | 845.136 / 965.607 | 751.442 / 883.769 |
| Oversized selection rejection | 7.222 / 10.107 | 6.854 / 7.879 |

The v3 run produced 2,709 registry publications, 9,031 candidate scans and 3 exact lookups. Full evaluation peaked at 24,326,532 Python-traced bytes and 187,215,872 native working-set bytes. The serialized response grew from 36,986 to 43,713 bytes as the trace added activation proof and verification fields. Run-to-run timings are observations and do not isolate a performance effect of the correction.

The first baseline fixture had duplicate condition IDs and was corrected before reproducing the bug. The first v3 focused run failed two assertions that compared snapshot IDs from distinct cutoffs verbatim. The proof comparison now normalizes only that snapshot coordinate. Both intermediate executions are retained.

Production recorded readiness remains BLOCKED. The cooperative deadline and existing resource limits apply. The benchmark is host-specific and places no hard bound on blocking producer execution.

Roll back by reverting the v3 code and its evidence commit without deleting saved records or rewriting the branch. Keep v3 decisions read-only if serving older evaluator code. Existing v1 and v2 records and archive quarantine remain intact.
