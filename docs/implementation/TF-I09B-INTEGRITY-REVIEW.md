# TF-I09B decision integrity review

The reviewed remote HEAD was `eda182d2fe4cf562e53030ac273254b7db973078`. The runtime correction is `a2b92cc7a61ac3bcee8a5765d7f6fcbf1ca88b65`. The evidence commit and workflow results are linked from [PR #16](https://github.com/uvelan/TradeForge/pull/16). The existing analytical engine outputs, versioned strategy definitions, frozen source authority and archive quarantine are preserved.

## Reproductions and corrections

| Case | Reviewed v1 | Corrected v2 |
| --- | --- | --- |
| Delayed FVG creation and straddling candle | The strict producer emitted UNCERTAIN. FVG_ACTIVE returned NOT_SATISFIED because current_at returned false. | Exact family classification reports UNKNOWN and preserves STRADDLING_OBSERVATION. |
| NOT FVG_ACTIVE | The inner false became true. A negative trigger qualified. | The inner UNKNOWN remains UNKNOWN through NOT; neither context nor trigger gets positive proof from that uncertainty. |
| FVG invalidation | Invalidation read the uncertain family as a known negative. | Unknown invalidation blocks qualification. |
| Continuous SMT divergence with bound setup role | Family X selected at +1800 seconds invalidated at +1980. An independent later family replaced X and the evaluator qualified a trigger at +3000 using X's latched role. | The setup and attempt retain X. Its terminal lifecycle blocks the attempt at +1980. |
| Tentative historical setup | The reviewed implementation stored a historical leaf before whole-phase activation and could return it before revalidating predecessors. | Tentative leaves are reevaluated until phase activation. Established historical proofs remain tied to their exact revision and alias. |

The [reviewed-HEAD reproductions](evidence/TF-I09B-integrity/baseline-verified.txt) ran in an independent detached worktree and produced five expected failures against the corrected assertions. The [corrected regressions](evidence/TF-I09B-integrity/canonical-final.txt) use frozen archive inputs and the actual canonical producers. The [order-block baseline](evidence/TF-I09B-integrity/baseline-order-block.txt) reproduced false negation at the delayed activation interaction. Corrected fixtures also check confirmed OB far-edge invalidation, FVG full mitigation, liquidity straddling and an evidence-age deadline with no producer publication.

## Current families, scope and absence

The strategy registry classifies the latest applicable revision in its exact calculation, category, source, configuration, purpose and family. A current observation requires an eligible latest active revision. Confirmed full mitigation, selected proven terminal transitions and attested SMT expiry or invalidation can establish inactivity without granting those revisions positive predicate eligibility. Unknown structural coverage, ambiguous interactions, unverified or missing evidence, stale eligible evidence and out-of-horizon queries remain distinguishable. The public boolean current-at compatibility path is unchanged.

Coverage starts with the frozen source's required due observations. The search then checks the scoped producer lane, applies the predicate and its time bounds, and validates current-family state or exact historical eligibility. Current searches inspect families that predate a phase boundary so a still-active older observation cannot produce false negation. One eligible matching family can prove an existential positive. Scoped unknown families and relevant historical ambiguity propagate UNKNOWN if a positive proof is absent. Strict SMT absence remains unknown when the matching frontier cannot establish it. Three-valued ALL, ANY and NOT still use the schema's truth tables.

Aliases are stored separately from in-progress rule results. The attempt binds a successful context alias at context activation, and binds setup aliases at setup activation. A pinned family never adopts another family's revision. A current alias may progress within its family. Cross-phase relationships use the established predecessor proof and its original selection time. A historical binding can supply historical evidence without an additional CURRENT requirement. The attempt trace records bind time, requirement, original proof and known current validity. The legacy CRT binding structure remains available.

## Timing, cutoff and compatibility

The timeline includes available producer events, due coverage, SMT uncertainty and recovery boundaries, evidence-age deadlines, triggering eligibility after setup and the evaluated cutoff. The seconds expiry transition occurs at the first second after the inclusive expiry boundary. A definite elapsed seconds expiry terminates an unresolved attempt and records expiry during uncertainty. A confirmed invalidation evaluated at the same cutoff takes precedence. Setup expiry and the separately projected order-intent expiry are distinct.

First create persists the requested cutoff. Repeat create returns the saved decision at that exact cutoff if present. An unsaved cutoff on a known run returns HTTP 409 with the requested cutoff, latest saved cutoff, run ID and the reconstruction requirement. Reconstruction remains nonpersistent and forward advancement remains optimistic and atomic. Existing v1 runs, decisions, evidence references and intent IDs remain readable through the existing API and UI. Their reconstruct and advance operations are read-only conflicts. Create a new v2 run for corrected evaluation. The strategy definition stays schema 2 and binding policy stays 2.

## Observation execution inventory

Each enumerated projection was checked against canonical events, exact source/calculation and availability. Trigger fixtures also check a cutoff immediately before actual qualification.

| Observations | Fixture and outcome |
| --- | --- |
| CRT_REFERENCE, CRT_MIDPOINT, CRT_RECLAIM, CRT_STATE | Frozen range fixture qualifies each trigger at its known reference formation or lifecycle revision. |
| SMT_CONFIRMED | A later matched divergence qualifies the trigger after independently established structure setup. Its exact two participant sources are asserted. |
| SMT_LEADER, SMT_LAGGARD | Exact same-family setup role checks cover positive and opposite-role outcomes. A later trigger cannot reuse a confirmed role from an earlier setup cutoff. |
| STRUCTURE_PIVOT, STRUCTURE_BREAK, BOS_DIRECTION, CHOCH_DIRECTION, STRUCTURE_BREAK_PRICE | Independent pivot, break and reversal fixture qualifies each trigger and checks the pre-availability cutoff. |
| LIQUIDITY_POOL, LIQUIDITY_ACTIVE_POOL, LIQUIDITY_SWEEP | Verified pool formation, established active state and later sweep qualify the expected trigger. A delayed boundary fixture produces UNKNOWN. |
| FVG_ACTIVE, FVG_STATE | Verified formation qualifies both observations. Full mitigation makes the family definitively inactive and straddling remains unknown. |
| ORDER_BLOCK_ACTIVE, ORDER_BLOCK_STATE | Verified structure-linked block qualifies both observations. Canonical delayed straddling and observed far-edge invalidation classify separately. |

Independent prefix tests load each early cutoff from its own archive query and compare the state of a later canonical snapshot processed only through that early cutoff. The three delay models span early confirmation, matching and invalidation boundaries and sparse queries. CRT and the inventory fixtures add independent earlier snapshots around the trigger boundary for each tested producer. The comparison retains the selected event ID, calculation ID, source, family, timing, activation and eligibility. It excludes only the snapshot coordinate of those references and diagnostic producer/resource payloads. This is evidence for the tested configurations. It does not certify unrestricted whole-history producer reuse.

## Dense and real session evidence

The [dense benchmark](evidence/TF-I09B-integrity/dense-expanded.json) builds a separate synthetic archive with a cyclic price pattern, multiple timeframe scopes, six producers and a 96-node rule graph. It records 30 warmed serial samples per workload, including canonical producer work, full evaluator work and resource rejection. It retains publication breakdown, candidate scans, exact registry lookups, timeline entries, response size, peak Python allocations and native process working-set high-water mark. Measured producer P50/P95 was 766.651/923.298 ms. Full evaluator P50/P95 was 845.136/965.607 ms. Slot rejection was 7.222/10.107 ms. Full evaluation issued 2,709 registry publications, 9,031 candidate scans and 3 exact lookups across 1,295 timeline points. The producer published 98 overlapping FVG formations and 646 SMT lifecycle events. Full evaluation peaked at 24,325,405 Python-traced bytes and a native process working-set high-water mark of 186,826,752 bytes.

The budget tests exercise source slots, publications, timeline, candidate scans, serialized decisions, append and run limits, concurrent admission and cooperative expiry. The evaluator checks a 20-second cooperative deadline at producer boundaries, chronological evaluation and periodically during evidence scanning. Native or blocking work may complete before a cooperative check can interrupt the request. This is not a hard process-level duration bound.

The unchanged [approved historical replay](evidence/TF-I09B-integrity/real-session.json) used frozen November 13, 2026 EURUSD/GBPUSD M1/M5 Dukascopy BID checksums and the four prior delay configurations. Every LONG and SHORT case remained EXPIRED with BOUND_REFERENCE_TERMINAL. No case qualified. The script checks exact archived context numerically and reconstructs the same decision ID within evaluator v2. Production recorded readiness remains BLOCKED.

## Verification and remaining limits

The [backend log](evidence/TF-I09B-integrity/backend-final.txt) records 653 passing tests, including all 22 approved legacy fingerprints, and confirms byte-identical SHA-256 values on all 44 workspace archive files. [Vitest](evidence/TF-I09B-integrity/vitest.txt), [build](evidence/TF-I09B-integrity/build.txt), [Chromium](evidence/TF-I09B-integrity/browser.txt), [checkpoint repetitions](evidence/TF-I09B-integrity/checkpoint.txt), [delayed stress](evidence/TF-I09B-integrity/browser-stress.txt) and [cold Data Library check](evidence/TF-I09B-integrity/data-library-cold.txt) record serial results with browser retry and timeout settings unchanged. Prior failure and fixture-iteration logs are preserved under the evidence directory. The previous hosted Data Library timeout occurred at the first click following cold browser startup. The isolated cold check passed in 7.1 seconds. The full Chromium suite passed all 71 tests, the checkpoint passed all 20 repetitions and the delayed drawing stress passed. Vitest passed 8 tests and TypeScript/Vite built.

The strict SMT test suite covers uncertainty recovery, and the evaluator harness verifies selected independent prefix cases. Additional end-to-end combinations across all SMT recovery and matching-frontier configurations are needed before any incremental reuse is admitted. Dense workload coverage is limited to the supplied synthetic pattern and budget fixtures. Historical execution and broker readiness remain separate. See the [evaluation screenshot](evidence/TF-I09B-integrity/strategy-evaluation.png) and [frozen chart screenshot](evidence/TF-I09B-integrity/strategy-evaluation-chart.png).

Rollback by reverting the integrity and evidence commits without rewriting the branch or deleting stored decisions. Preserve both evaluator-version histories. Before serving old evaluator code, keep v2 records out of its reconstruction and advancement endpoints.
