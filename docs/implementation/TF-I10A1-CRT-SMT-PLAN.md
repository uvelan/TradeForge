# Bounded CRT/SMT successor fixture plan

Approved PR #17 was merged at `d003eb935de788f039d840cba87d742e92861358`. The experiment starts from that main commit. Times below are offsets in seconds from 2020-01-02 10:00:00 UTC.

Both instruments use canonical M5 candles. EURUSD is the primary with 0-second modeled delay. The GBPUSD comparison arrives 20 seconds after nominal close, with a 0-second counterfactual. CRT uses the primary H1 aggregate. Bearish SMT compares both M5 lanes with two-bar pivots, zero alignment tolerance in bars, and one confirmation bar. The strategy is SHORT. CRT reclaim establishes setup. The trigger requires a later primary CRT midpoint expansion and a confirmed bearish SMT observation. Expiry is 3,599 seconds from activation.

| Cutoff | Family and expected outcome |
| ---: | --- |
| 3600 | A, the H1 reference at offset 0, activates. |
| 7200 | A expires without an intent. B, the H1 reference at 3600, becomes available at the exact terminal cutoff and is rejected for rearm. |
| 7500 | The primary sweep/reclaim produces a later revision of spent A. It stays rejected and cannot activate another attempt. |
| 10800 | C, the H1 reference at 7200, activates strictly after A. |
| 12900 | C reclaims its high. |
| 13500 | A later candle reaches C's midpoint. The same candle closes the second pivot's right wing. |
| 13520 | Late comparison closes the matching frontier and makes the bearish SMT candidate available. |
| 14119 | No confirmation or intent. The required comparison confirmation candle has not yet arrived. |
| 14120 | The later comparison arrival confirms SMT and qualifies exactly one C intent. |
| 14400 | D, the H1 reference at 10800, activates. |
| 14700 | D reclaims its high. |
| 15000 | D reaches its lower boundary and invalidates before a qualifying divergence. No intent. |

Reference families are identified by their canonical H1 start and then pinned to the exact IDs computed from independent archive prefixes. The zero-delay control should qualify C at 13800. Omitting the GBPUSD confirmation bar should leave the relevant prefix uncertain and cause admission validation to fail for incomplete coverage.

The reference computes fresh prefixes and steps the existing rule evaluator from the fixture's calendar and modeled arrivals. It does not consult the successor scheduler or its ledger. The initial change is evidence only. Enable publication only after checking the full reference, execution order and recovery within 160 cutoffs.
