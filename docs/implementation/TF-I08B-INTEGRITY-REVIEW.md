# TF-I08B evidence integrity correction

PR #11 starts at rejected head 09289dd5bb293f81da60c492df5a3abbc22a7706. Correction c05c3071c99d599a20cb868636fd934de9d28fed adds the request-scoped evidence registry and updates the five analytical engines. The evidence commit records verification.

## Reproduction

The H1 fixture uses high 105, low 100 and midpoint 102.5. Recorded M5 sweep closes at 11:05 UTC. An unrecorded midpoint observation closes at 11:10 UTC, followed by a recorded opposite-boundary reach at 11:15 UTC. The rejected head returned ELIGIBLE, UNVERIFIED_AVAILABILITY, ELIGIBLE. Strict v3 returns ELIGIBLE, UNVERIFIED_AVAILABILITY, UNVERIFIED_AVAILABILITY. The final event pins the reclaim as its interaction barrier and the midpoint revision as lineage. Both runs preserve exact event IDs in their logs.

- [Failing result from the rejected implementation](evidence/TF-I08B-integrity/rejected-head.txt)
- [Corrected result and the strict/legacy fixtures](evidence/TF-I08B-integrity/strict-and-goldens.txt)

## Dependency specification

| Engine | Interaction predicate | Inherited evidence |
| --- | --- | --- |
| Structure | FORMATION for confirmed pivots, CLOSE_ONLY for confirmed directional breaks | Prior classified pivot, active level and recorded continuity segment. Quiet accepted candles influence window, structural state and coverage. Wick candidates remain descriptive. |
| Liquidity | FORMATION for pool revisions, WHOLE_CANDLE for wick sweeps, CLOSE_ONLY for breaks | Canonical constituent pivots, prior revision and exact pre-interaction revision. Unrecorded quiet or revision candles taint subsequent transitions in the same segment. |
| Order blocks | FORMATION for the block, WHOLE_CANDLE for touch and mitigation | Eligible BOS or CHOCH, known candidate candle, activation and latest lifecycle revision. Quiet candles affect continuity and expiry. |
| FVG | FORMATION for the gap, WHOLE_CANDLE for wick and close-mode interactions | Three recorded formation candles, activation and prior mitigation revision. Quiet candles can affect expiry. ATR-based evidence remains withheld. |
| CRT | AGGREGATED_REFERENCE for H1/H4/D1 references, WHOLE_CANDLE for interactions | Reference constituents, active reference or reclaim barrier, prior lifecycle revision and every accepted candle that advances range age. |

The coordinator receives canonical publications before pagination. Consumers require an exact canonical event in the same request, a supported category, version, confirmation and lifecycle state, a recorded stamp with verified source authority, matching source and compatible producer configuration, established coverage and a valid cutoff. An activation reference supplies the actual barrier. A whole-candle barrier must be at or before open. A close-only barrier must precede close. Lineage must have been available when the new event was known. Dependencies resolve only to earlier published attested summaries, so forward edges and cycles fail closed without recursive traversal. Each resolved summary is cached. The ledger reserves memory against the existing coordinator budget.

A historical observation survives later expiry, while an active-reference lookup requires the current attested revision at the lookup cutoff. Existing strict context indexes formations and conservatively omits a formation once a newer revision supersedes it. No payload supplied by a caller enters the evidence registry. Archive checksum verification validates stored bytes. It cannot authenticate how an arrival timestamp was originally recorded.

Legacy research stays at v1 and all 22 golden fingerprints match. The strict timing and evidence contract advances from v2 to v3. The strict namespaces change event IDs and snapshots and reject incompatible cursors. The existing numerical algorithm versions are preserved.

## Verification and benchmark commands

~~~powershell
.venv/Scripts/python.exe -m pytest -q -s research_backend/tests/test_causal_evidence_integrity.py research_backend/tests/test_strict_causal_timing.py research_backend/tests/test_analysis_evidence.py::test_approved_golden_singles_and_combined_are_unchanged
.venv/Scripts/python.exe -m pytest -q
$env:PYTHONPATH='.'
git worktree add --detach "$env:TEMP/TradeForge-TFI08B-rejected-09289dd" 09289dd5bb293f81da60c492df5a3abbc22a7706
.venv/Scripts/python.exe docs/implementation/evidence/TF-I08/benchmark-paired.py "$env:TEMP/TradeForge-TFI08B-rejected-09289dd" --causal-integrity
Set-Location frontend
npm test
npm run build
npx playwright test --retries=0
npx playwright test e2e/research.spec.ts --grep 'persists a trendline and resumes a frozen replay checkpoint' --repeat-each=20 --retries=0
~~~

The paired benchmark alternates 12 samples per version on identical inputs. It records calculation and API P50/P95, serialization, response bytes, allocation peak, process working set, dependency lookups, and coordinator reuse and archive counts. Local browser and test processes were also active during the sample. The first checkpoint run overlapped these workloads and timed out three times before the chart left its loading state. Its log is retained.

| Workload | API P50/P95 before → after, ms | Response bytes before → after | Peak Python allocation before → after, KiB |
| --- | ---: | ---: | ---: |
| 2,000 candles, strict context | 562/899 → 799/1236 | 553990 → 785222 | 4779 → 5901 |
| 10,000 verified archive candles | 1373/2186 → 2260/2809 | 548791 → 777069 | 25493 → 30290 |

The registry recorded 3,563 dependency lookups over 1,597 publications for the caller workload, and 17,682 over 7,989 publications for the archive workload. Both workloads retained one compatible structure execution. The archive workload retained seven history reuses, five checksum checks and five partition decodes. The measured overhead includes canonical validation, larger strict payloads and serialization. See [paired benchmark](evidence/TF-I08B-integrity/benchmark-final.txt).

The initial concurrent full Chromium run passed 63 and timed out once during drawing edit. The concurrent checkpoint run passed 17 and timed out three times during chart loading. Both initial logs are retained, and the serial commands use the same assertions and zero retries.

Local verification passed 370 backend cases, including 21 new regressions. The focused run passed 62 strict and golden checks. Vitest passed 8 cases and the TypeScript/Vite production build passed. The serial full Chromium suite passed 64 cases and the serial checkpoint run passed 20 consecutive repetitions. Both used zero retries.

The new cases exercise CRT midpoint, extreme and quiet-candle inheritance, FVG partial and quiet-candle inheritance, order-block touch and liquidity boundary inheritance, invalid metadata and dependency graphs, forged payloads, active-reference expiry, cutoff rewind and zero-delivery resolution.

The current report lives with the final evidence commit. Push and pull-request workflows are verified separately against that exact commit.

## Rollback

Revert the corrective implementation and evidence commits on the PR branch. Existing data requires no migration. Restart strict history from the restored version rather than reusing a v3 cursor.
