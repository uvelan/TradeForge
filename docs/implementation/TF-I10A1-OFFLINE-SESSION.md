# Offline full-session CRT/SMT successor replay

This research branch starts from verified main after PR #19 merged at `59557bf6a650194d6e4012004db2cb50a2d381ee`. The approved bounded fixture ran first. Its [ledger](evidence/TF-I10A1-offline-session/bounded-ledger.json) preserves all original decisions, proofs, opportunity rows and the intent. The [profile](evidence/TF-I10A1-offline-session/bounded-profile.txt) attributes 1.869 seconds to archive history loading and normalization (0.296 seconds of partition reads), 1.299 seconds to strict SMT including nested history calls, and 0.182 seconds to evaluator replay. The times overlap. Optimization targets verified archive decoding and normalization.

## Declared synthetic session

Use the approved short EURUSD/GBPUSD M5 configuration with continuous calendar and modeled arrivals 0s/20s. Use the original A/B/C/D candles and extend both lanes with the respective last candle through 24 evaluated hours. Warmup starts 2020-01-02 10:00 UTC. Evaluation starts at +3600 and ends at +90000. Offsets are seconds since warmup start.

A binds at +3600 and expires at +7200. B first qualifies at +7200 and is suppressed at the exact boundary. C binds at +10800, has a bearish SMT candidate at +14119 and qualifies exactly one historical intent at +14120. D binds at +14400 and invalidates at +15000. The repeated final candles have no further sweep or new priced pivot, so remaining attempts cannot qualify another intent. Their hourly context roots and expiries continue through the same run, with no reset of spent or boundary history. Explicit offline ceiling is 800 inspected cutoffs. Public admission retains 160.

## Correctness and publication

The independent reference builds its own chronological schedule from M5 modeled arrivals, producer publication and uncertainty edges, rule-age edges, setup release, and expiry. It calculates each prefix fresh and carries the previous terminal cutoff and spent families across all 24 evaluated hours. The optimizer uses a replay-local cache of SHA-256-verified immutable archive rows and normalized candles. Both paths rebuild CRT, SMT and their cutoff-specific registries using the original producers. Source and cutoff checks still apply to each request. Resume starts a new cache and verifies archive bytes again.

Both paths used the same immutable run identity. They matched at all 611 inspected cutoffs, including complete decision-trace fingerprints, and on every field of the final decisions, opportunities, accounting state, intents and proofs. No snapshot or proof normalization was needed. The first development comparison found an extra +14121 inspection in the independent harness, visible as a transition at the first differing outer cutoff of +14400. Its cause was treating the lower bound after C as an event. The corrected reference admits that timestamp only when a publication, candle or timer is due. The final comparison had no divergence.

The ledger recorded 25 opportunities, 14 bound attempts, 11 terminal-boundary suppressions, zero unresolved inspections (empty reason counts) and one intent. Terminal states were 11 expired, one invalidated, one qualified and one no-trade. The complete common records and exact hashes appear in [comparison.json](evidence/TF-I10A1-offline-session/comparison.json). It includes the continuous calendar, source lanes, exact archive hashes and immutable run identity. Producer calculations and evaluation were never split into independently started windows.

At +14119 C's latest attested SMT revision was the candidate and there was no intent. At +14120 that same family's confirmed revision was eligible and issued exactly one intent. Both traces and proofs matched. Two variants of a later candle in a noncontributing partition yielded the same pinned contributing selection at every earlier cutoff. The exact paired proofs at +14119 and +14120 also matched. Rewriting an already contributing partition or changing a declared delay defines new provenance and can change proof identities. The test keeps those differences visible and uses the same declared 0s/20s policy for the two paths.

SQLite recovery stopped after +7200 and again at +14100, the last scheduled inspection before confirmation. A duplicate committed batch retained one B suppression. The final resume changed chunk size from 1 to 23 and reproduced all 611 trace hashes and the exact published decisions, intents and opportunity records. The public results and checkpoint endpoints withheld incomplete data. Public validation and enqueue both continued to reject this session with HTTP 422 under the 160-cutoff budget. The isolated recovery test used the internal store to exercise the publication fence. No public endpoint or worker selects the offline prototype.

## Synthetic benchmark

Windows 11 Pro 10.0.26200, Intel Core i7-9700K, Python 3.13.14. Separate fresh Python processes ran each path. Cold is the first evidence calculation in that process. Warm is the second execution. The operating-system file cache was not flushed. Peak is process working-set high water, so the warm figure includes the preceding cold execution.

| Path | Process state | Elapsed | Peak working set | Verified archive decodes | CRT / SMT calculations | Cutoffs | Attempts |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Fresh independent | Cold | 30.71 s | 150.96 MiB | 1,226 | 613 / 613 | 611 | 14 |
| Fresh independent | Warm | 31.44 s | 151.75 MiB | 1,226 | 613 / 613 | 611 | 14 |
| Cached offline | Cold | 27.50 s | 116.24 MiB | 6 | 613 / 613 | 611 | 14 |
| Cached offline | Warm | 26.67 s | 116.88 MiB | 6 | 613 / 613 | 611 | 14 |

Counts include the two horizon checks. The optimized prefixes make two physical decodes beyond those four validation decodes. Per-prefix producer calculations remain exact and unchanged in count. Total history loading and normalization dropped from 15.26/15.80 seconds to 11.47/11.20 seconds, cold/warm. This is one cold and one warm observation per path on this fixture. The optimized path fit the 150-second and 768-MiB worker thresholds on both runs. The offline ceiling is 800 cutoffs, the optimized timing guard is 150 seconds, and the fresh reference timing guard is 300 seconds. No wider worker-budget claim follows from this fixture.

## Real archive and verdicts

The currently approved real Dukascopy catalog uses the recorded forex-weekly calendar and its immutable BID partitions. The existing real regression recipes remain on their original producer and delay assumptions. They are incompatible with the pinned continuous-calendar M5 successor case, so this PR establishes no real full-session successor coverage. All eight checksum-verified real single-attempt controls completed as `EXPIRED` with zero intents. Both successor variants continued to return HTTP 422 before queueing. [Real controls](evidence/TF-I10A1-offline-session/real-controls.json)

- **Synthetic full-session semantic equivalence** passed for the declared corpus.
- **Synthetic worker-budget fit** passed for both measured optimized invocations.
- **Real full-session successor coverage** unverified.

The focused backend run passed 71 tests. The separately rerun archive-integrity regression passed and confirmed a fresh offline cache rejects a corrupted pinned partition. Both benchmark commands and the end-to-end recovery command passed.

[Commands and captured results](evidence/TF-I10A1-offline-session/)

Rollback by reverting implementation commit `5c69d75f9b845125c6f5a498f20cd25b1b6c97f6` and retaining the evidence. The offline prototype is opt-in and adds no database migration. Full-session public admission remains capped at 160. TF-I10A.2 remains pending, historical intents retain `AWAITING_EXECUTION_VALIDATION`, and production recorded trading remains `BLOCKED`.
