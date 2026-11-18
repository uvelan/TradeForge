# TF-I08B calculation isolation review

The reviewed and starting SHA is ad94bd22e7c31685a78e448cacfcbb1eb3f0fa90. Implementation 9d310aa591257963e5bb9d25c34a1f710158bda7 and archived integration test commit e2a4c237bd7ac3b3c8b5d51703d569298760dc40 remains on PR #11. The following commit records the verification evidence.

## Reproduction

The reviewed code produced stable FVG formation ID 0600eb7339e7b87d7027f56645d123ea6f53149e426469b976bc6716bff15425 in both archived windows. Its long window returned UNVERIFIED_AVAILABILITY and its short window returned ELIGIBLE. The published snapshots differed. Both calculation orders raised a conflicting-publication exception in a shared registry. Four real API requests, covering both indicator orders with independent CRT and order-block context, returned HTTP 422 with the same error. [Reviewed-head output](evidence/TF-I08B-registry/reviewed-head.txt)

The corrected registry retains the formation ID and isolates both publications. The archive-backed API cases exercise independent and shared context, reversed request ordering, source scope and the exact publication reference. Shared compatible FVG context uses one FVG execution. [Corrected API and strict checks](evidence/TF-I08B-registry/strict-final.txt)

## Identity and dependency contract

- Stable event and family IDs describe the market event and lifecycle. They keep their existing algorithms.
- The coordinator derives the calculation ID from engine, effective rules, fixed window and source configuration. It derives the attestation snapshot from the calculation ID, ordered observation evidence including availability, native reference inputs and the evaluation cutoff. History digests are reused within the request.
- Evidence-reference v1 names the event, category, calculation and attestation snapshot. It carries effective publication configuration, authority, availability and ordered source fields for source, symbol, timeframe, feed, price basis and dataset.
- A same-calculation dependency is looked up within that calculation. A cross-engine dependency uses the exact reference captured at the producer handoff and must match the producer calculation pinned by the coordinator. Unresolved, forward, circular, ambiguous and incompatible edges fail closed. Decisions use the validated summary and preserve the full event signature check.
- Current-state family and gap indexes are local to each calculation. Expiry and quiet unrecorded observations in sibling windows leave independent timelines intact. Expiry does not erase a valid historical observation, and rewind uses the consumer's cutoff.
- Public contextual references retain the exact producer reference. Strict order-block context exposes the market event ID for the FVG, with witness identity still available separately.
- Strict timing remains v3. Evidence-reference v1 is additive and pagination snapshots are salted for the new reference contract. Existing strict exports remain historical records. They require a fresh calculation to supply v1 request-scoped evidence. Legacy research v1 preserves all 22 approved output fingerprints.

## Verification and performance

The benchmark warms the analytical calculation and HTTP request, then alternates 30 paired samples per version for each workload. It runs without concurrent browser or test processes. Calculation, serialization and API timing are reported separately from the untimed profile pass. The profiles include publication, signature computation, configuration and history preparation, dependency resolution and current-state and gap indexing. Allocation peak uses tracemalloc and native working set is sampled around a calculation. The existing coordinator memory budget accounts for each canonical publication. [Raw benchmark and profiles](evidence/TF-I08B-registry/benchmark.txt)

Commands are run from the repository root unless noted.

~~~powershell
git worktree add --detach "$env:TEMP/TradeForge-TFI08B-reviewed-ad94bd2" ad94bd22e7c31685a78e448cacfcbb1eb3f0fa90
$env:PYTHONPATH='.'
.venv/Scripts/python.exe docs/implementation/evidence/TF-I08/benchmark-paired.py "$env:TEMP/TradeForge-TFI08B-reviewed-ad94bd2" --causal-registry
.venv/Scripts/python.exe -m pytest -q -s research_backend/tests/test_strategy_calculation_isolation.py research_backend/tests/test_strict_causal_timing.py research_backend/tests/test_causal_evidence_integrity.py research_backend/tests/test_analysis_evidence.py
.venv/Scripts/python.exe -m pytest -q
Set-Location frontend
npm test
npm run build
npx playwright test --retries=0
npx playwright test e2e/research.spec.ts --grep 'persists a trendline and resumes a frozen replay checkpoint' --repeat-each=20 --retries=0
npx playwright test e2e/research.spec.ts --grep 'during delayed analytical and persistence responses' --retries=0
~~~

## Serial measurements

| Workload | API P50/P95 before → after, ms | Response bytes before → after | Allocation peak before → after, KiB |
| --- | ---: | ---: | ---: |
| 2,000 strict caller candles with context | 434/623 → 495/669 | 785219 → 1309431 | 5890 → 7189 |
| 10,000 verified archive candles | 1494/1576 → 1744/2053 | 777070 → 1296792 | 30205 → 34940 |

The archive median rose by 16.7%. The 20% reduction target was missed. Exact producer references add response metadata, and the profile includes full canonical event encoding and signature checks. The archive workload preserved one compatible structure execution, seven history reuses and five physical checksum checks and decodes. It recorded 7,989 publications and 17,682 dependency lookups in both versions.

## Results

The final focused suite passed 92 cases and the full backend suite passed 385. Vitest passed 8 cases and the TypeScript/Vite build passed. The full Chromium suite passed 64 cases, the drawing/replay checkpoint passed 20 consecutive repetitions, and the delayed-analysis drawing stress fixture passed. Browser runs used zero retries. The recorded failures are the six reviewed-head reproductions.

In the archived workload profile, publication accounted for 0.625 → 0.896 seconds inclusive and full signature work for 0.341 → 0.465 seconds inclusive. Exact reference resolution took 0.028 seconds, and calculation identity and history preparation took 0.042 seconds in the corrected profile. Lifecycle expiry, gap isolation and cutoff rewind are covered by the dedicated registry fixtures.

## Limits and rollback

Archived checksums authenticate stored bytes. Availability still depends on the original sample and recording process. The registry is request-scoped. Revert the corrective commits on the PR branch to restore the prior implementation. Restart strict history from the restored version. Persisted records and exports need no migration.
