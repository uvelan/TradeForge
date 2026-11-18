# TF-I08C matching and canonical lifecycle integrity

| Item | Reference |
| --- | --- |
| Reviewed and actual starting HEAD | 9b43492830487ed4a4fe5c51f675312987823db4 |
| Implementation | 2db7f941b59ca3bbf18269e990e3d49e5fa58ad2 |
| Review | [PR #12](https://github.com/uvelan/TradeForge/pull/12) |
| Contracts | Strict SMT algorithm 2, SMT lifecycle 2, timing policy 3, evidence references 1 |
| Cursor collection | strict-smt-v2 |

## Baseline reproduction and correction

The two API/registry reproductions failed on the reviewed code. See the [original failing output](evidence/TF-I08C-integrity/baseline-defects.txt).

| Fixture and assertion | Reviewed result | Corrected result |
| --- | --- | --- |
| Primary M1 candle opened at UTC offset 0. Recorded arrival 1000. Evaluate at 999 and 1000. | No event at 999. At 1000 the API backdated candidate to 840 and confirmation to 900. | No event at 999. Candidate becomes available at 1000. The longer fixture confirms at 1080 using the candle that began at 1020. At 1079 it still has no confirmation. |
| Zero-bar mode with the same delayed prefix | Covered by the new regression. | Candidate and confirmation both become available at 1000. Registry current queries beyond the evaluated snapshot fail closed. |
| GBPUSD laggard candle starting at 600 is absent. Evaluate at 720. | API current eligibility false, canonical registry current eligibility true. | Both report false. Historical confirmation at 600 remains eligible. Canonical current is true at 659 and false starting at the missing candle's nominal close of 660. |
| A recorded, non-breaching laggard candle with nominal close 660 arrives at 695. | Reviewed API had no canonical uncertainty interval. | Current status is uncertain on [660, 695). Eligibility resumes at 695. Re-evaluating later does not make the earlier interval eligible. |

## Matching frontier and identity

The shared session prefix starts at the declared market-open interval intersected with the frozen range. Stop at the first UTC slot missing usable recorded evidence on either side. A single pass per session maintains the maximum recorded arrival and a rolling SHA-256 witness of the paired candles. Both pivot directions share the summaries. A match reads the prefix ending at its latest possible peer's right confirmation candle in constant time. Thus the prefix includes all earlier coverage, earlier pairing decisions that may consume a peer, and potential unmatched earlier pivots. The per-session peer cursor still enforces nearest one-to-one assignment and the existing tie-break. Scheduled closes reset matching.

The added frontier costs O(n) time and O(n) bounded storage for n usable paired slots. Local witness and peer selection remain bounded by the configured pivot and alignment windows. No pivot rescans the session prefix. The monitor exposes the prefix interval. The matching record retains both matching endpoints and both prefix digests within the two frozen source attestations. The market-event ID continues to bind the local pivot/windows and effective closure instants. Changes to unrelated prefix bytes that leave the reconstructible pairing and its closure instants unchanged do not rename the event. Changes to the exact prefix attest a different evidence record and snapshot. The existing 100-window regression verifies that distinction.

Candidate activation starts at corrected match availability and may move later if recorded laggard pre-activation evidence requires it. A pre-activation breach remains disqualifying. Only fully post-activation shared candles can count toward confirmation. The existing ambiguity rule applies to a candle that straddles activation. A recorded laggard wick can invalidate independently.

## Canonical lifecycle

Each calculation owns its SMT families. The matching-closure publication is research-only and requires the same exact four pinned pivot producer references used by every lifecycle publication. The candidate depends on the matching publication. Confirmation also requires the exact candidate, whose availability must equal the activation barrier. Terminal revisions link the exact preceding attested state. The registry rejects wrong producer snapshots, wrong pivot sets for a matching proof, missing candidates, unattested matching evidence, wrong families and forward dependencies. It validates immutable signatures for strategy consumers. API eligibility is projected from this registry.

Use family-specific uncertainty boundaries to avoid replacing historical confirmation records when current knowledge is incomplete. Boundaries are immutable within a calculation. An interval starts when the relevant nominal close passes without trustworthy evidence. A genuinely late recorded arrival can close that interval at its actual arrival. Recovered intervals remain queryable at earlier cutoffs within the snapshot. The registry rejects current queries later than the cutoff it evaluated. These rules are local to strict SMT.

| Condition | Canonical current state or change | Historical consequence |
| --- | --- | --- |
| Shared confirmation still pending and no laggard uncertainty | SMT_CANDIDATE, research-only | No confirmation |
| Missing or unrecorded laggard observation | UNCERTAIN from the expected close | Preserve prior confirmation |
| Straddling activation wick that could breach, or no trustworthy straddling observation | UNCERTAIN from the straddling candle's nominal close | No whole-candle confirmation through this ambiguity. Earlier zero-bar confirmation remains historical. |
| First expected open candle closes beyond the frozen window | UNCERTAIN | Preserve prior confirmation |
| Late evidence resolves all open uncertainty without a terminal event | Current at the actual recovery instant if already confirmed | No retroactive recovery |
| Recorded breach or complete observation horizon | SMT_INVALIDATED or EXPIRED, respectively | Active state ends. Prior confirmation remains historical. |

## Recorded-arrival policy audit

Strict SMT requires the stored SERVER_RECORDED_ARRIVAL_V1 marker, an explicit closed flag, and integer availability at or after nominal close. The normal MT5 and Dukascopy candle import paths do not supply authenticated arrival data. Standard chart/replay fallbacks can display nominal-close timing, which this adapter does not admit as recorded evidence.

The single-instrument strict engines currently distinguish checksum-verified archive data from client-supplied candles, and apply their timing guard to the stored availability field. The shared loader only enforces the server-arrival marker for callers using strict_arrivals. As a result, other archived analytical paths can treat an explicit stored available_at as recorded without verifying an equivalent capture-policy marker. This difference is documented for follow-up. Single-instrument admission and persisted archives were preserved for this scoped correction.

## Verification

See all [execution evidence](evidence/TF-I08C-integrity/). The backend suite includes both original repro tests, delayed quiet and unmatched-pivot prerequisites, rewinds, exact registry queries, two-family isolation, shared and laggard-only observations, legacy SMT and authority cases, and the 22 approved legacy golden fingerprints. The UI replay fixture uses mocked SMT responses to verify visible current-state uncertainty and rewind. API and registry fixtures use actual archived test candles.

| Execution | Observed result |
| --- | --- |
| `python -m pytest -q research_backend/tests/test_smt_integrity.py` at reviewed HEAD before changes | Both required reproductions failed, with the original timestamps and contrary registry result preserved |
| `python -m pytest -q` on the implementation | 432 passed. Includes strict timing, registry isolation, SMT authority, immutable legacy journal and alerts, and all 22 approved golden fingerprints. |
| `npm test` | 8 tests passed across 3 files |
| `npm run build` | TypeScript and Vite passed. The build retained the existing MUI bundling warnings. |
| `npm run e2e -- --retries=0 --reporter=line` | 65 passed in 6.9 minutes. [Confirmed replay](evidence/TF-I08C-integrity/strict-smt-confirmed-replay.png) and [uncertain replay](evidence/TF-I08C-integrity/strict-smt-uncertain-replay.png). |
| `npm run e2e -- --retries=0 --reporter=line --repeat-each=20 --grep 'persists a trendline and resumes a frozen replay checkpoint'` | All 20 consecutive cases passed in 3.4 minutes |
| `npm run e2e -- --retries=0 --reporter=line --grep 'persists a trendline and resumes replay during delayed analytical'` | Passed in 19 seconds |

One verification command was rejected by the PowerShell parser before it launched any tests. The serial command was corrected and launched from the same checkout. The sandbox launcher also failed on the initial attachment read, so the approved shell path was used.

## Before and after performance

Reproduce with [benchmark.py](evidence/TF-I08C-integrity/benchmark.py) executed with `runpy.run_path(..., run_name="__main__")` from each checkout. The reviewed and corrected runs used Windows 11, Python 3.13.14, isolated SQLite/Parquet stores, three warmups and 30 serial paired samples per M5 workload. The calculation time includes archive validation and evidence publication. The in-process ASGI lane includes API validation and response encoding. Python allocations are measured separately. Windows working-set readings are process high-water marks, cumulative across each run.

| Workload, candles per feed | Calculation P50 / P95 ms before → after | ASGI P50 / P95 ms before → after | Added frontier P50 / P95 ms | Peak Python MiB before → after |
| --- | ---: | ---: | ---: | ---: |
| Dense, 2,000 | 343.0 / 470.5 → 367.5 / 441.3 | 355.6 / 471.1 → 383.0 / 456.9 | 3.1 / 4.5 | 14.5 → 18.1 |
| Sparse, 10,000 | 556.4 / 621.8 → 571.2 / 616.1 | 553.4 / 617.8 → 569.9 / 646.2 | 15.5 / 17.7 | 41.0 → 43.5 |
| Dense, 10,000 | 1590.3 / 1685.1 → 1984.2 / 2065.1 | 1600.5 / 1670.3 → 2003.5 / 2186.0 | 15.3 / 77.7 | 73.0 → 90.8 |
| Delayed session prefix, 2,000 | 170.4 / 252.2 → 202.2 / 274.9 | 178.0 / 259.8 → 214.9 / 278.9 | 3.0 / 4.2 | 10.7 → 12.4 |
| Missing laggard with 21 current families, 2,000 | 326.5 / 455.5 → 396.2 / 489.0 | 338.0 / 498.8 → 407.8 / 486.4 | 3.0 / 4.1 | 14.4 → 17.9 |

For the dense 10,000 lane, registry publications rose from 8,325 to 9,990 and dependency lookups from 19,972 to 34,953. The eight-event payload grew from 120,414 to 158,556 bytes. Archive checks and decoding stayed at 10 partitions and 20,000 rows. The process working-set peak rose from 261,168 to 281,324 KiB. Rewind and re-advance measured 1551.9 and 1673.3 ms before, then 2018.1 and 1944.1 ms after. The reviewed matcher did not instrument a complete frontier, so its frontier cost is unmeasured. All five workloads stayed within the existing aggregate budgets. The dense overhead is material and remains a cost of publishing exact matching and lifecycle dependencies. [Reviewed metrics](evidence/TF-I08C-integrity/benchmark-before.txt) · [Corrected metrics](evidence/TF-I08C-integrity/benchmark-after.txt)

## Remaining limitations and rollback

Strict confirmation still depends on production availability capture outside this phase. Inverse and cross-provider pairing remain unsupported. A frozen window cannot establish ongoing current validity beyond its evaluated cutoff. Session calendars remain explicitly declared and do not authenticate the source's real trading hours. When an earlier missing slot blocks a session, the algorithm declines to infer later pairings in that session.

Rollback by reverting this verification commit and then 2db7f941b59ca3bbf18269e990e3d49e5fa58ad2 on the feature branch. No persisted data or legacy journal migration is required. Old strict exports remain historical. Version 1 cursors must be restarted rather than interpreted under version 2.
