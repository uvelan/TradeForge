# TF-I09A final evidence-binding closure

Reviewed head was `b5184c2e3ca9603d622c582e9ea4fdf6134d2f42`. The focused implementation is commit `19151114fa52f61cad28d37fae2788f80e287bcf`. The implementation and evidence commits are on [PR #15](https://github.com/uvelan/TradeForge/pull/15), where the final HEAD and both exact workflow runs are linked.

## Baseline and correction

At the reviewed commit, the requested READY_FOR_EVALUATION definition with an independent LATEST_ELIGIBLE CRT expansion trigger returned VALID_STRATEGY_DEFINITION. The same baseline accepted an unbound SMT role filter and a context predicate that depended on trigger activation. The corrected validator rejects all three conditions. [Actual baseline](evidence/TF-I09A-closure/baseline.json) · [Actual corrected validation](evidence/TF-I09A-closure/corrected.json) · [Reproduction script](evidence/TF-I09A-closure/reproduce.py)

A tracked CRT definition admits its root ACTIVE reference as the initial selection. Every other CRT rule in that definition uses BOUND_FAMILY with the root alias and exact dependency, including setup progression, reclaim, trigger, invalidation, midpoint and other supported CRT projections. Expiry and terminal revisions are scoped to the same family. This contract admits no independent CRT reference in a tracked definition. The editor exposes the required alias, and errors point to the affected rule's `same_family_as` field.

## Canonical fixtures and activation

The two-range fixture uses actual strict CRT publications and registry references. Range A becomes available at 11:00 UTC and is selected at an actual activation cutoff of 11:05. A reclaims at 11:20 and a canonical related bearish SMT confirms at 11:30. Range B reclaims at 12:05 and expands at 12:10. B's expansion cannot qualify A, whose trigger yields UNKNOWN. A's own expansion at 12:20 qualifies the bound condition. A expires at 12:25 while B remains current, and B cannot keep the attempt eligible. An additional fixture verifies invalidation and completion while its peer remains usable. [Canonical regression fixtures](../../research_backend/tests/test_strategy_binding_closure.py)

At new activation, the contract verifies that the historical ACTIVE event is an exact eligible publication, it is within evidence age, and the latest exact revision establishes the selected family's current usability at that cutoff. A terminal reference cannot activate. Missing current evidence yields UNKNOWN. A later canonical reclaim can keep an existing selection usable and does not change its activation time. Frozen rewind reconstructs the earlier attempt using its original activation cutoff. The evidence reference retains its 11:00 availability and the attempt separately retains its 11:05 activation.

## SMT and phase ordering

SMT_LEADER and SMT_LAGGARD require a same-dependency SMT_CONFIRMED alias. Their family comes from the selected divergence's exact registry proof. In the strict SMT fixture, family X matches the direction but has the wrong leader. Family Y has the required leader and a different market-event identity in the same exact calculation snapshot. The same-family selection rejects Y for X and accepts the role predicate when X itself supplies the expected role. The templates leave role filters off and declare trade direction independently.

Temporal links compare the causal availability of selected activations. The normalized direction must stay within one phase or go from context to setup, or from context or setup to trigger or invalidation. Invalidation is a pre-trigger gate. A trigger cannot establish invalidation's prerequisite. The same direction applies to binding dependencies. Later-phase prerequisites and implicit historical-observation exceptions are rejected. BEFORE reverses the edge during validation. Equal timestamps still fail strict ordering. Existing graph cycle detection still applies.

## Compatibility and TF-I09B handoff

Strategy definition schema stays at **2**, with **STRATEGY_DEFAULTS_V2**. The stricter evidence-binding policy is explicitly version **2** in schema 2 capability discovery, validation restrictions, and the attempt trace contract. The trace envelope remains version 2 and pins the specific binding policy. Producer algorithms, evidence reference version 1 and causal timing version 3 stay pinned. No database migration is needed. Existing stored JSON and fingerprints remain immutable. Compatible definitions retain their prior fingerprint. Previously ready incompatible versions remain readable and have a derived revision-required result with field issues. Correcting one appends a version. Optimistic concurrency and archival still apply. The schema 1 historical compatibility path is unchanged.

The future evaluator must validate the immutable definition under binding policy 2 before processing market observations, pin the source snapshots, and use the exact matched prerequisite and current-family validity at the actual activation cutoff. Subsequent phases consume the pinned family or the separately bound divergence as required. Preserve the activation independently of source event availability, never replace a latched family, apply terminal and expiry guards before qualifying a trigger, and reconstruct at the original cutoff on rewind. The evaluator remains pending TF-I09B. Production recorded-data readiness remains blocked.

## Rollback

Before reverting the focused source and evidence commits in a maintenance branch, stop the backend and back up the database. The correction uses the same stored schema, so no data migration is required. The previous validation policy admitted the reproduced unsafe definitions. Keep historical evidence and apply the revised review restrictions before any future evaluation.

## Verification and performance

[Commands](evidence/TF-I09A-closure/commands.md) · [Prior benchmark](evidence/TF-I09A-integrity/benchmark.json) · [Repeated benchmark](evidence/TF-I09A-closure/benchmark.json)

The existing benchmark used 30 serial timed samples after three warmups. The closure 96-node graph measured **1.036 / 1.314 ms P50/P95** compared with **1.054 / 1.388 ms** previously. The 85-edge binding graph measured **1.159 / 1.257 ms**, compared with **1.161 / 1.531 ms**, and used **321,455** traced Python bytes with a **29,004** byte response. Variation across runs is expected. The underlying contracts and measured scope remain definition validation.

| Operation | Reviewed P50 / P95 ms | Closure P50 / P95 ms |
| --- | ---: | ---: |
| Fingerprint hash | 0.044 / 0.049 | 0.043 / 0.050 |
| Create | 10.542 / 13.516 | 10.692 / 14.249 |
| Revise | 10.206 / 11.264 | 10.649 / 13.975 |

Backend passed **591 tests** and the focused backend lane passed **219**. The approved numerical regression preserved all **22** fingerprints. Vitest passed **8** and the TypeScript/Vite production build succeeded. [Backend](evidence/TF-I09A-closure/backend.txt) · [Focused](evidence/TF-I09A-closure/focused.txt) · [Vitest](evidence/TF-I09A-closure/vitest.txt) · [Production build](evidence/TF-I09A-closure/build.txt)


The complete Chromium suite passed **70** with zero retries. The checkpoint run passed **20 consecutive repetitions** and delayed-analysis drawing stress passed **1** test. The corrected Builder test exercised the CRT and SMT alias selectors and retained both bindings after reload. [Chromium](evidence/TF-I09A-closure/chromium.txt) · [Checkpoint run](evidence/TF-I09A-closure/checkpoints.txt) · [Delayed stress](evidence/TF-I09A-closure/delayed-stress.txt) · [Builder screenshot](evidence/TF-I09A-closure/strategy-bound-trigger.png)

### Hosted runner correction

The first push and PR checks at `3188c34efb3a1fe6a9c5ed82a46697a0ca8c4710` each failed the same newly added strict SMT test on an empty database. The fixture wrote directly to default storage and relied on local tables already being present. A fresh local database reproduced the missing arrival_sessions error. The regression now requests the existing isolated client fixture and calls the strict SMT endpoint while capturing the actual registry. All original family assertions remain in force. [Hosted failure excerpts](evidence/TF-I09A-closure/hosted-first.txt) · [Fresh local reproduction](evidence/TF-I09A-closure/hosted-local-reproduction.txt) · [Isolated fixture pass](evidence/TF-I09A-closure/hosted-local-fixed.txt)

The earlier local runs left six synthetic epoch-dated clock-feed sessions in the workspace's default archive. They remain there. The isolated fixture prevents new writes to that archive from this test.


The corrected branch passed the complete **591-test** backend suite with a newly created storage root. [Fresh-storage backend log](evidence/TF-I09A-closure/backend-fresh-storage.txt)
