# Historical CRT and SMT confluence

For the subsequent Windows correction to coverage witnesses and saved receipt discovery, see [Confluence coverage and saved receipts](T10-CONFLUENCE-CORRECTION.md). The measurements below describe the original implementation.

## Profile pinned before inspecting results

`CRT_SMT_M5_V1` uses EURUSD for the H1 CRT reference and EURUSD/GBPUSD for positive-correlation exact M5 SMT. Both lanes require saved Dukascopy BID M5 candles with verified catalog pins. Pane order is a presentation choice. The genuine acceptance window is 13 November 2026, 12:00–16:00 UTC, with no earlier input; the comparison uses modeled M5 close unless the capture supplies availability.

At each cut, select the visible CRT family with the latest H1 reference start and the SMT family with the latest first candidate availability. Break inter-family ties with the lexicographically smallest family ID. Within the selected family use the last canonical revision visible at the cutoff, including equal-time transitions. A newer family displaces the older even if it is pending. Display each producer's own family ID, revision, time and evidence.

A reclaimed low requires bullish SMT; a reclaimed high requires bearish SMT. Expansion retains the original sweep direction. CRT expiry, invalidation, ambiguous chronology or reaching the opposite boundary ends eligibility. SMT must be currently confirmed under period 2, zero alignment bars, zero tolerance, one confirmation bar and 64 expiry bars. A candidate, an ended confirmation, an unresolved open slot, or a future peer arrival cannot establish agreement. A confirmed opposite direction is affirmative disagreement. Lack of evidence must remain visible. Decisions are research observations with no execution authority.

## Input pins

48 M5 bars in each pane of the selected local window. Each registered file contains 264 rows over the provider session. The feed is `dukascopy-web-m1-v1`. No account, live feed or execution simulation is involved.

| Instrument | Provider CSV SHA-256 | Catalog dataset ID | Parquet SHA-256 |
|---|---|---|---|
| EURUSD | `bc2e49a78edad37b73ac1bcdc006892f54aab60c3e769ec837998773cb5960f6` | `ca01e9ac45346d7c9fdfdf4263085831a07735bc16158901c9a14187f77aaf4e` | `fa2af192aa86dc41bb20df51dffe95c29bc48cdf2fe38b643f699985c6b0e09d` |
| GBPUSD | `a2b4947d5e79198920589263a9626e476012650a09d8ba52386e244ab78c1928` | `1cc6acc1b358b3b043438badc69f72413827a8cd5dad379de8e41dd9aaaecfd0` | `aed73f09d78bb3df29d3678e0ace1f00949a673a920ed22dcd6d23cb7a173d02` |

## Windows operator workflow

1. In Market Terminal, open a saved paired Dukascopy EURUSD/GBPUSD M5 BID replay. Freeze or resume the chart and expand **Replay CRT and SMT confluence**. Both components use the displayed evidence cutoff, bounded by the chart clock.
2. Inspect the current receipt. Open each component to see its condition, direction, selected family and revision, first availability, source pins and candles. Choose an earlier UTC probe or step the chart across a publication.
3. Choose **Save exact receipt** to retain that verdict and proof. Saving explicitly rechecks the current receipt hash. Reopen the saved entry at or after its evidence cutoff; rewinding earlier hides it, and advancing recovers it. Saved content lives in the local database. The original version kept the saved menu in the local browser profile. The corrected inspector lists the session’s SQLite receipts, including older saves made from another profile.
4. Swap the two panes to inspect the same producer directions. EURUSD remains the CRT instrument and the SMT primary. The ordering of the chart panes does not change the research rule.

## Genuine expected and observed transitions

The independent reference calculated UTC H1 ranges from the provider rows, paired same-open-time M5 pivots, and replayed each producer through each candidate cutoff. It checked the second before and the instant of each nominal close or recorded fixture arrival. The table includes same-state revisions. All 97 genuine probes agreed. Times are UTC on 13 November 2026.

| Time | Joined verdict | EURUSD CRT revision and reason | Exact-pair SMT revision and state |
|---|---|---|---|
| 12:00:00 | INCOMPLETE | NO_VISIBLE_CRT_FAMILY | NO_VISIBLE_SMT_FAMILY |
| 13:00:00 | INCOMPLETE | 1 · CLOSED_REFERENCE | NO_VISIBLE_SMT_FAMILY |
| 13:05:00 | INCOMPLETE | 2 · SAME_CANDLE_RECLAIM | NO_VISIBLE_SMT_FAMILY |
| 13:10:00 | INCOMPLETE | 3 · NEW_SWEEP_EXTREME | NO_VISIBLE_SMT_FAMILY |
| 13:20:00 | INCOMPLETE | 4 · POST_RECLAIM_MIDPOINT | 1 · SMT_CANDIDATE |
| 13:25:00 | ALIGNED | 4 · POST_RECLAIM_MIDPOINT | 2 · SMT_CONFIRMED |
| 13:30:00 | NOT_ALIGNED | 5 · CLOSE_BEYOND_INVALIDATION | 3 · SMT_INVALIDATED |
| 14:00:00 | NOT_ALIGNED | 1 · CLOSED_REFERENCE | 3 · SMT_INVALIDATED |
| 14:25:00 | NOT_ALIGNED | 2 · SAME_CANDLE_RECLAIM | 3 · SMT_INVALIDATED |
| 14:30:00 | NOT_ALIGNED | 3 · OUTSIDE_CLOSE | 3 · SMT_INVALIDATED |
| 14:55:00 | NOT_ALIGNED | 4 · POST_RECLAIM_MIDPOINT | 3 · SMT_INVALIDATED |
| 15:00:00 | NOT_ALIGNED | 1 · CLOSED_REFERENCE | 3 · SMT_INVALIDATED |
| 15:30:00 | INCOMPLETE | 2 · NO_SAME_CANDLE_RECLAIM | 3 · SMT_INVALIDATED |
| 15:40:00 | INCOMPLETE | 2 · NO_SAME_CANDLE_RECLAIM | 1 · SMT_CANDIDATE |
| 15:45:00 | NOT_ALIGNED | 3 · CANDIDATE_AGE | 2 · SMT_INVALIDATED |
| 16:00:00 | NOT_ALIGNED | 1 · CLOSED_REFERENCE | 2 · SMT_INVALIDATED |

The qualifying CRT H1 OHLC was 1.10533 / 1.10549 / 1.10442 / 1.10505. Its high reclaim remained eligible after midpoint expansion. The aligned SMT highs were EURUSD 1.10549 → 1.10557 and GBPUSD 1.32316 → 1.32306. At 13:25 the component revisions were `828cf3b23c25fca8b0efa8aeb4c5a5620a33f7be59ce2d05db5a86b12e11c451` and `c04e4d87c513f365988fa35d9e81f4c67eb80afb603623b550b7d82e012c9ff0`. The positive interval was [13:25:00, 13:30:00). Neither bullish agreement nor confirmed opposition occurred in this genuine window.

## Controlled cases and recovery

Numbers in this table count inspected cutoffs, not independent opportunities. Every verdict matched the independently expected result; the two aligned genuine probes sampled a single five-minute interval.

| Case | Cutoffs | Aligned | Contradicted | Known false | Incomplete |
|---|---:|---:|---:|---:|---:|
| genuine | 97 | 2 | 0 | 55 | 40 |
| disjoint | 45 | 0 | 0 | 11 | 34 |
| overlap | 45 | 8 | 0 | 3 | 34 |
| delayed-overlap | 49 | 6 | 0 | 3 | 40 |
| contradiction | 45 | 0 | 11 | 0 | 34 |
| terminal-tie | 45 | 0 | 0 | 11 | 34 |
| smt-terminal | 45 | 4 | 0 | 7 | 34 |
| newest-crt | 53 | 14 | 0 | 5 | 34 |
| newest-smt | 47 | 12 | 0 | 0 | 35 |
| peer-gap | 45 | 4 | 0 | 0 | 41 |

The disjoint fixture had both component events in its final archive and zero simultaneous eligibility. The positive counterpart aligned while the reclaim remained active. The peer-gap case identifies the missing symbol and half-open M5 interval. Two simultaneous opposing SMT families also passed the public-API tie-break test in both pane orders. The independent price oracle covers distinct candidate times; the dedicated tie test resolves the two canonical producer IDs and checks the rule's lexicographic selection.

Across all cases the harness checked 516 cutoffs and 1032 current or saved rewind reads. After a fresh backend process, 52 additional receipt reads reproduced their original hashes. The API returned 409 for an unpinned, foreign, corrupted or wrong-cutoff receipt and 422 for an unrecognized profile. The chart drill distinguishes a revision's publication witnesses from later monitoring of continued eligibility. [Exact family IDs, revision IDs, availability and receipt hashes](../evidence/T10-CONFLUENCE/oracle.json)

## Cost of inspection

Windows-local FastAPI requests against the same input bytes, with a fresh backend for each cold sample. OS file cache was uncontrolled. File counters measure verified bytes and decoded rows; SQL counts are traced SELECTs. The composed and joined responses have different shapes. [Per-sample latency, counters and working set](../evidence/T10-CONFLUENCE/performance.json)

| Action | n | Median / p95 ms | Verified bytes | Decoded rows | SQL reads |
|---|---:|---:|---:|---:|---:|
| Reopen session only | 6 | 50.18 / 83.56 | 0 | 0 | 2 |
| Reopen and join | 6 | 348.64 / 364.25 | 20,818 | 528 | 6 |
| Reopen exact pin | 6 | 345.30 / 379.35 | 20,818 | 528 | 7 |
| List at successive cutoffs | 28 | 22.08 / 27.60 | 20,818 | 528 | 4–6 |
| Rewind current | 28 | 22.52 / 31.46 | 20,818 | 528 | 4 |
| Select current at 13:25 | 4 | 20.56 / 21.24 | 20,818 | 528 | 4 |
| Retrieve exact pin at 13:25 | 4 | 19.78 / 20.47 | 20,818 | 528 | 5–7 |
| Rewind saved | 28 | 21.14 / 25.43 | 20,818 | 528 | 5 |
| Preview and repeat explicit save | 4 | 47.88 / 48.73 | 41,636 | 1,056 | 9 |
| Standalone CRT list | 4 | 11.99 / 12.05 | 10,538 | 264 | 3 |
| Standalone CRT detail | 4 | 12.84 / 13.30 | 10,538 | 264 | 3 |
| Standalone strict SMT | 4 | 17.12 / 18.39 | 20,818 | 528 | 4–6 |
| Standalone CRT list + detail + strict SMT | 4 | 41.77 / 41.97 | 41,894 | 1,056 | 10 |

At 13:25, one joined read verified each input once and shared those rows between the producers. It returned both proofs (43,578 bytes), using 528 decoded rows and four SELECTs. Composing standalone reads required 1,056 decoded rows, 41,894 verified bytes and ten SELECTs. An independent request for that same cutoff still verifies both files. An explicit save also rechecks the preview, which incurs that work twice across the two requests. Once mounted, a component drill uses the selected receipt locally. The warm benchmark backend peaked at 126,168 KiB; the highest cold backend sample was 122,616 KiB. This excludes Chromium.

The retained sparse GBPUSD wick inspection was run serially on the same Windows host after all worker workloads finished and before the combined suites. Across 30 requests per build its median/p95 was 15.036/18.037 ms on the starting head and 14.554/18.832 ms on the candidate. Median improved 0.482 ms and p95 increased 0.795 ms. Both verified 2,182 bytes and decoded eight rows per request. The earlier 31.81 ms p95 did not recur. A lasting change in latency is inconclusive. [Baseline samples](../evidence/T10-CONFLUENCE/sparse-before.json) · [Candidate samples](../evidence/T10-CONFLUENCE/sparse-after.json)

## Installation and rollback

This change adds an immutable SQLite table for explicitly saved confluence receipts. Browsing and expanding a proof do not persist a receipt. For rollback, stop the local app and restart the frontend and API from `6e2eb41ee81af599dec0f80ad9c076e76e2f41b5` in a separate worktree. Keep both databases, saved archives and browser profile intact; older code ignores the added receipts. Restart the corrected version to inspect them again. The admission cap remains 160, historical intents retain `AWAITING_EXECUTION_VALIDATION`, and production recorded trading remains `BLOCKED`.

## Reproduce on Windows

Run from the repository root with the original local source files in `storage`. The verification scripts use disposable storage. The source CSV and Parquet stay local. For sparse comparison, use a detached worktree of the starting commit and run the two measurements sequentially on an otherwise idle host.

```powershell
git worktree add --detach ../TradeForge-confluence-base 6e2eb41ee81af599dec0f80ad9c076e76e2f41b5
$env:TRADEFORGE_T10_SOURCE_STORAGE=(Resolve-Path storage).Path
.venv/Scripts/python.exe -m scripts.verify_t10_confluence_compatibility --check-join --source-storage storage --compare docs/implementation/evidence/T10-CONFLUENCE/components-before.json --output storage/confluence-components.json
.venv/Scripts/python.exe -m scripts.verify_t10_confluence --source-storage storage --output storage/confluence-oracle.json
.venv/Scripts/python.exe -m scripts.verify_t10_confluence_browser --source-storage storage --case all --samples 20 --output storage/confluence-browser --api-port 8752 --ui-port 4752
.venv/Scripts/python.exe -m scripts.benchmark_t10_zone_gaps --repo ../TradeForge-confluence-base --output storage/confluence-sparse-base.json
.venv/Scripts/python.exe -m scripts.benchmark_t10_zone_gaps --output storage/confluence-sparse-candidate.json
.venv/Scripts/python.exe -m scripts.benchmark_t10_confluence --source-storage storage --cold-samples 6 --repetitions 4 --output storage/confluence-benchmark.json
.venv/Scripts/python.exe -m pytest -q
npm --prefix frontend test
npm --prefix frontend run build
$env:TRADEFORGE_E2E_API_PORT='8750'
$env:TRADEFORGE_E2E_UI_PORT='4750'
npm --prefix frontend run e2e -- --retries=0
```

## Chromium evidence and inspector cost

The Windows Chromium journey saved a genuine aligned receipt and a controlled delayed-peer receipt, moved the chart past their cutoffs, held an obsolete response, swapped panes and restarted the API, frontend and browser with the persistent local profile. Both original receipts reopened exactly. Swapping the panes created a different chart session and receipt identity with field-identical component proofs. Each drill displayed a source candle from the selected revision. [Exact genuine receipt](../evidence/T10-CONFLUENCE/aligned-receipt.json) · [Reopened verdict](../evidence/T10-CONFLUENCE/genuine-reopened-verdict.png) · [CRT candle](../evidence/T10-CONFLUENCE/genuine-reopened-crt-candle.png) · [SMT candle](../evidence/T10-CONFLUENCE/genuine-reopened-smt-candle.png) · [Controlled reopened verdict](../evidence/T10-CONFLUENCE/controlled-reopened-verdict.png)

| Chromium action | Genuine median / p95 ms | Controlled median / p95 ms | Samples per case |
|---|---:|---:|---:|
| List | 125.65 / 169.90 | 123.65 / 169.10 | 20 |
| Select | 68.35 / 87.80 | 76.60 / 87.80 | 20 |
| Rewind | 231.65 / 262.50 | 186.75 / 243.60 | 20 |
| Reopen previously saved receipt | 103.55 / 142.80 | 86.55 / 110.90 | 20 |
| CRT candle drill | 133.30 / 172.60 | 134.45 / 158.60 | 20 |
| SMT candle drill | 132.40 / 157.60 | 129.85 / 157.70 | 20 |
| Reopen in restarted processes | 496.8 | 1,598.1 | 1 |

With the inspector closed, the genuine page mounted one inspector DOM node, zero source rows and downloaded zero confluence bytes. Both cases mounted zero source rows through list/select/rewind and one on each drill. Maximum sampled Chromium JavaScript heap across the genuine actions was 60,885,143 bytes. The restart timings are individual observations. The new UI has no comparable earlier implementation for a latency improvement claim. [Per-action samples and process restart evidence](../evidence/T10-CONFLUENCE/browser.json)

## Parallel integration and verification

The starting PR revision was `6e2eb41ee81af599dec0f80ad9c076e76e2f41b5`. The combined code and tests ran at `694aa2b90ee06246af3909f8913b1619b3961eb3`. Subsequent changes on this milestone supply documentation and verification receipts. Independent worktrees used separate API/UI ports (8741/4741, 8742/4742 and 8743/4743) and disposable storage. All three workers were running at the post-dispatch snapshot.

| Worker | Model and effort | Dispatch / first active / finish UTC | Worker commits |
|---|---|---|---|
| /root/confluence_api | GPT-6.2 Astra high | 11:32:45.859 / 11:32:47.641 / 11:47:30.341 | bbaeb6a, 1e59a67, c4f789c |
| /root/confluence_ui | GPT-6.2 Sol high | 11:32:58.252 / 11:33:00.101 / 11:51:48.159 | d9cd166, 7f1fe88 |
| /root/confluence_reference | GPT-6.2 Sol high | 11:33:13.305 / 11:33:14.929 / 11:50:15.139 | 78a9c9d, f0f4f03, 3b308ca |

Owner work started at 11:31:11.594 UTC. Integration began at 11:42:28.311 UTC while the workers were still active. The three workers overlapped for 14 minutes 15 seconds. The UI/restart lane was the last worker to finish. Combined browser verification and full local suites followed the combined benchmark. No equivalent serial delivery run or applicable billing rate was available. Delivery speedup and monetary cost are unmeasured.

Development failures included a compatibility fingerprint that included a changing elapsed-time field, a tie fixture that looked for a feed label on the write response, and a browser assertion that waited for a redundant request after returning to the same cutoff. Corrections respectively excluded that single diagnostic from the standalone comparison, used the declared fixture feed, and checked the displayed receipt identity directly. Integration review also separated preview from explicit save, exposed half-open missing intervals per affected feed and preserved legacy CRT producer IDs. Final verification ran with the pinned local provider files configured.

Independent of this profile, genuine broker-statement acceptance still requires an authorized redacted statement. Full-month EURUSD replay still lacks 480 open hours in the November 2025 archive.

## Combined candidate results

| Check | Observed summary | Windows elapsed s |
|---|---|---:|
| focused | 32 passed, 1 warning in 13.38s | 15.13 |
| oracle | 516 cutoff comparisons passed | 185.04 |
| compat | Eight historical fingerprints and joined revision checks passed | 3.65 |
| browser | Four Playwright phases passed with zero retries | 141.12 |
| backend | 1182 passed, 1 skipped, 1 warning in 694.44s (0:11:34) | 695.95 |
| unit | Test Files  4 passed (4) Tests  13 passed (13) | 1.47 |
| build | TypeScript and Vite production build passed | 17.14 |
| standard | 88 passed (7.7m) | 465.48 |

Each full suite ran once on the combined candidate. The full browser command set retries to zero. [Exact commands, runtime versions and observed test summaries](../evidence/T10-CONFLUENCE/verification.json) · [Sanitized per-agent and reviewer usage through the pre-push sample](../evidence/T10-CONFLUENCE/usage-checkpoint.json). The final push verification, churn and later usage sampling cutoff are recorded in the PR conversation.
