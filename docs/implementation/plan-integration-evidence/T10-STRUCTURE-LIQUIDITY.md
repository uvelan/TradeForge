# Historical M5 structure and liquidity replay

**Starting revision** `76764fb5167fe08f9b961572b08fcbb8721c49d2`. This milestone concerns historical candle evidence. Historical intents retain AWAITING_EXECUTION_VALIDATION; the public strategy cap is 160 cutoffs and production recorded trading remains BLOCKED.

## Recorded workload and rule profile

The local acceptance uses the common 13 November 2026 Dukascopy EURUSD and GBPUSD M5 BID window, [00:00, 22:00) UTC. Each pane has 264 candles; the genuine case models availability at nominal close. No live MT5 observation is claimed.

| Symbol | Provider CSV SHA-256 | Replay Parquet SHA-256 |
|---|---|---|
| EURUSD | `bc2e49a78edad37b73ac1bcdc006892f54aab60c3e769ec837998773cb5960f6` | `fa2af192aa86dc41bb20df51dffe95c29bc48cdf2fe38b643f699985c6b0e09d` |
| GBPUSD | `a2b4947d5e79198920589263a9626e476012650a09d8ba52386e244ab78c1928` | `aed73f09d78bb3df29d3678e0ace1f00949a673a920ed22dcd6d23cb7a173d02` |

The replay uses confirmed structure v2 with the New York forex weekly calendar, two left and two right children, wick-based pivot prices, strict ties, zero tolerance/break margin and wick candidates enabled. Liquidity uses the existing equal-level pool and sweep rules. The master plan calls for displacement on MSS but supplies no numeric cutoff. The versioned replay profile uses a confirmed CHOCH whose broken pivot was available by the break candle’s open, with a directional candle body of at least 0.00100 in price units (ten pips on either instrument). This is a research definition for this profile. Existing BOS, CHOCH and liquidity producer rules continue to determine their original events.

## Operator journey and correctness

1. In **Market Terminal**, select Dukascopy saved EURUSD M5, add GBPUSD M5 in a second pane, load the UTC window, and start **Bar replay**.
2. Expand **Replay structure and liquidity**, choose the event pane, and select an event. Use **Older replay events** for earlier results. The receipt gives observation, anchor, confirmation, first availability, price level and supporting event IDs. Expand the direct candles to see pivot and confirmation roles, UTC timestamps, prices, partition ID and SHA-256. Broader calculation context is disclosed separately.
3. For the GBPUSD bullish MSS, seek to 13:30 UTC and then advance to 13:35. Rewind before first availability with the selection pinned; the earlier cutoff cannot supply the receipt. Advance again, save the replay position, restart the API and Chromium, and reopen the saved session. Each pane restores its own event choice. The adversarial journey held a genuine old response across a rewind and a pane change. An injected archive-verification failure removed the prior evidence.

**Correctness verdict: improved** over the baseline's HTTP 404 for the replay event receipt. The independent reference used provider decimals and UTC opens and verified every summary, first publication boundary, selected receipt, and direct candle role. Expected and observed matched for 75 genuine EURUSD and 91 genuine GBPUSD events at 61 and 67 distinct publication instants respectively. The counts include candidates, revisions and pool events. EURUSD contained 16 BOS, 4 CHOCH, 1 sweep and 0 MSS. GBPUSD contained 15 BOS, 4 CHOCH, 1 sweep and 1 MSS. Liquidity receipts report the observed side; they do not imply a trade direction. Representative exact matches follow, all times UTC.

| Symbol · event | Direction / side | Level | Anchor | Confirmation | First availability |
|---|---|---:|---|---|---|
| EURUSD · BOS | BULLISH | 1.1062 | 01:25:00 | 01:50:00 | 01:50:00 |
| EURUSD · CHOCH | BEARISH | 1.10589 | 03:45:00 | 04:15:00 | 04:15:00 |
| GBPUSD · SWEPT | HIGH | 1.32407 | 01:45:00 | 07:15:00 | 07:15:00 |
| GBPUSD · MSS | BULLISH | 1.32306 | 13:05:00 | 13:35:00 | 13:35:00 |

Every selected receipt was unavailable one second before its first availability. At a pinned noon cutoff the 36 EU and 50 GB visible events were identical after extending the capture and swapping lanes. On a controlled EURUSD prefix the wick crossing with a close back inside produced only a provisional wick candidate at 01:45. Changing only the final close to the crossed price produced a confirmed BOS. Delaying a GBPUSD pivot confirmation child by 20 seconds moved EQH availability to 00:40:20. The three controlled cases yielded nine checked receipts. Controlled MSS at 0.00100 and just under it (0.00099) also exercised the inclusive displacement threshold.

[All expected and observed events with source references](../evidence/T10-STRUCTURE-LIQUIDITY/oracle.json) · [Worker browser receipt at 66fd00f](../evidence/T10-STRUCTURE-LIQUIDITY/browser-journey.json) · [Restarted selection](../evidence/T10-STRUCTURE-LIQUIDITY/browser-reopened.png) · [Combined verification at 47ffc9e](../evidence/T10-STRUCTURE-LIQUIDITY/combined.json)

## Runtime scorecard

The same Windows host served both revisions (Intel Core i7-9700K, eight logical processors, 15.92 GiB RAM, Python 3.13.14, Node 24.14.0, npm 11.1.0). Backend cold reopening used eight fresh Python processes with the operating system file cache uncontrolled. Warm actions used thirty samples. The common Chromium workload used eight fresh browser contexts and the same server; step and rewind each have 24 samples. Browser timings end at visible H1 evidence. The inspector starts collapsed and makes zero event requests in that state.

| Action | n | Median, ms (delta) | p95, ms (delta) |
|---|---:|---:|---:|
| API cold_reopen | 8 | 82.97 → 76.98 (-5.99, -7.2%) | 123.44 → 86.84 (-36.60, -29.7%) |
| API warm_reopen_only | 30 | 13.71 → 13.60 (-0.12, -0.9%) | 16.20 → 20.56 (+4.36, +26.9%) |
| API warm_step | 30 | 20.39 → 19.61 (-0.79, -3.9%) | 22.62 → 20.63 (-1.99, -8.8%) |
| API warm_rewind | 30 | 20.24 → 20.21 (-0.03, -0.1%) | 23.57 → 25.01 (+1.44, +6.1%) |
| API older_page | 30 | 78.37 → 77.78 (-0.59, -0.8%) | 90.36 → 84.33 (-6.03, -6.7%) |
| Visible reopen | 8 | 321.37 → 247.54 (-73.83, -23.0%) | 451.53 → 274.19 (-177.34, -39.3%) |
| Visible step | 24 | 188.96 → 150.53 (-38.42, -20.3%) | 258.13 → 181.74 (-76.39, -29.6%) |
| Visible rewind | 24 | 183.18 → 149.74 (-33.43, -18.3%) | 260.77 → 202.96 (-57.81, -22.2%) |
| Visible inspect | 8 | 53.67 → 49.26 (-4.41, -8.2%) | 74.98 → 62.64 (-12.34, -16.5%) |

**Common-path efficiency: inconclusive.** Point estimates improved for the visible actions. The run order and concurrent worker activity were uncontrolled. The cold API and warm step medians also fell, but warm reopen and rewind p95 increased. The two baseline runs show substantial natural variation. Percentages describe these runs rather than a general speedup.

**New event inspection: unmeasured improvement; first baseline established.** At a warm cutoff the single-pane event list took 42.27 / 51.45 ms median / p95. Opening the list and selecting its receipt took 91.02 / 97.53 ms. Open-inspector step and rewind took 46.27 / 54.49 and 46.08 / 52.29 ms. The cold reopen followed by first inspector access took 393.26 / 822.93 ms including first-use initialization. Each measured list verified a 10,538-byte Parquet artifact and decoded 264 rows. List plus selection did this twice. Execution is bounded but repeats this work on subsequent inspections. SQLite SELECT counts, file digests, decoded rows and sample ranges are in the runtime receipt. Physical disk/cache misses and complete browser process-tree memory are unmeasured.

Common cold backend peak working set was 93,792 → 93,768 KiB (−24, −0.03%). Warm common peak was 100,580 → 100,592 KiB (+12, +0.01%). The candidate warm worker including the new workload peaked at 128,184 KiB (+27,592 KiB, +27.4% over its common-path high-water mark). **Memory verdict: inconclusive on common actions; degraded when the inspector is exercised.** Preliminary eager instrumentation loaded an extra module and inflated the common-path peak; the receipt preserves that attempt alongside the corrected measurement.

[API samples and counters](../evidence/T10-STRUCTURE-LIQUIDITY/runtime.json) · [Visible common-action samples](../evidence/T10-STRUCTURE-LIQUIDITY/common-browser-timing.json)

## Delivery and verification timeline

26 November 2026, UTC. Dispatch is the owner's actual spawn call. Start and finish are the runtime's turn context and task completion. The post-dispatch snapshot at 06:57:58 showed all three running. Each had a sibling checkout, independent local storage and an assigned API/UI port pair.

| Worker | Model | Dispatch | Start | Finish | Elapsed | Assigned ports |
|---|---|---|---|---|---:|---|
| /root/structure_api | GPT-6.2 Astra high | 06:57:19.272 | 06:57:21.161 | 07:10:40.891 | 799.73 s | 8661 / 4661 |
| /root/structure_ui | GPT-6.2 Sol high | 06:57:29.178 | 06:57:30.856 | 07:14:53.653 | 1,042.80 s | 8662 / 4662 |
| /root/structure_oracle | GPT-6.2 Sol high | 06:57:41.227 | 06:57:43.240 | 07:15:51.943 | 1,088.70 s | 8663 / 4663 |

The owner started at 06:57:05.913. Integration began at 07:10:17 with A's commit while B and C were active. The union of worker lifetimes was 1,110.78 s. Their summed lifetimes were 2,931.23 s. All three overlapped for 777.65 s; concurrent lifetimes exceeded wall time by 1,820.45 agent-seconds. This measures overlap, including coordination and waiting. There was no matched serial implementation run. **Delivery speedup verdict: unmeasured.**

The backend worker committed `07dbd9c`. B’s worker-tested UI revision was `66fd00f`, with evidence in `9762cb3`. C supplied the harness in `43f4f05` and evidence/instrumentation in `3a3e09b`. Its API run was on `0d82347`, with the same backend implementation as A. The integration owner ran the independent API and browser checks on `47ffc9edbcafdb5ec37cd539bb5a79d35388dab4`. The two browser runs produced identical observed rows, IDs, pins and restart selections. Their wall times differed.

The UI worker's strengthened StrictMode assertion initially observed two requests on opening the panel. The failure was recorded at 07:12:11; the shared in-flight request and error guard were committed at 07:13:04 (53 s to corrected code). The repeated browser journey passed. C repeated the performance run after correcting an eager import in the measuring code. First candidate finish to corrected repeat finish was 170 s (07:12:06–07:14:56), overlapping oracle verification. These elapsed intervals must not be added to wall time. Initial focused validation, conditional screenshot reframing, and the final combined reruns are retained in the evidence. The measured critical path was combined-candidate assembly, isolated timing, combined focused checks, then the parallel full suites with backend as the last gate, followed by documentation and push. Verified-push time and the end-to-end duration are in the operator handoff.

## Integration heads and necessary rerun

The focused API, genuine browser and common UI measurements ran on `47ffc9edbcafdb5ec37cd539bb5a79d35388dab4`. Its full Windows suite also passed. During that suite the remote PR branch advanced to contributor revision `f8de84bf26c000b0d2f0cd801f6ea39221b71712`, which narrows the existing M5 timeframe assertion to the linked pane's controls. The owner inspected the diff and merged it at 07:29:39 UTC. The resulting combined-tested SHA is `66ad699e4aaad482593bc9b241e09219155ed571`. Exactly one browser-test locator changed between these two integration revisions. The runtime, numerical oracle and market-data browser evidence are unchanged. The owner repeated the full test matrix to verify this exact combination. The final commit records the handoff only.

| Earlier combined check | Observed result | Elapsed |
|---|---|---:|
| Focused API suites | 64 passed | 24.01 s |
| Independent all-event API oracle | 166 genuine and 9 controlled receipts matched | 35.10 s |
| Genuine Chromium | Initial and fresh-process restart passed, zero retries | 25.24 s + 10.28 s |
| Backend | 1,137 passed, one POSIX-only skip; existing Starlette deprecation warning | 626.02 s |
| Frontend units; production build | 13 passed; build passed with dependency bundler warnings | 1.04 s; 16.36 s |
| Standard Chromium | 88 passed, zero retries | 421.45 s |

**Final combined Windows verdict: passed.** On `66ad699e4aaad482593bc9b241e09219155ed571` the full backend suite passed 1,137 tests in 634.10 s with one POSIX-only skip. Standard Chromium passed 88 tests in 425.40 s, zero retries. The 13 frontend tests passed in 2.02 s and the production build passed in 16.63 s. The full independent API oracle and genuine browser receipts above apply to identical production code. The test matrix was launched at 07:29:53 UTC and the last backend output was written at 07:40:32 UTC (639 s elapsed). Across both candidates, first full-suite launch to final result spanned 22m 20s, including inspection and incorporation of the contributor change. First integration to first candidate freeze took 5m 46s. Re-integrating the upstream test and revalidating added a full verification gate.

## Token and change accounting

The runtime receipt reports cumulative-counter deltas from the start of this milestone for the integration owner, all three requested workers and their four linked approval-review sessions. Repeated implementation checks and associated agent turns are included. Cached input is a subset of input and reasoning is a subset of output. Total is input plus output. A separate breakdown of internal provider retries is UNAVAILABLE. Tool execution time is not an additional token charge. The receipt contains aggregate counters and timestamps only.

**Token efficiency: unmeasured** against a comparable implementation baseline. The pre-push runtime sample at 2026-11-26T07:40:45.985235+00:00 reconciled 64,739,403 total tokens (64,614,195 input and 125,208 output). The published receipt is explicitly a pre-push checkpoint. The handoff includes the later read after push verification. Tokens for text produced after the last read cannot be observed during the same reply. Applicable pricing and billing adjustments are UNAVAILABLE, so no cost is estimated. [Per-agent model, input, cached input, output, reasoning and total](../evidence/T10-STRUCTURE-LIQUIDITY/usage-checkpoint.json)

**Code efficiency: unmeasured** as a relative implementation claim. Measured net diff from `76764fb` to combined code `47ffc9e` comprises 8 production files (+402/−12 lines), 8 tests, runners and benchmark files (+1,593/−1), and 6 evidence/documentation files (+15,510/−1 text lines and one binary screenshot). Most evidence lines are per-event JSON. The final handoff adds documentation and evidence only. The implementation reuses the canonical structure and liquidity engines plus the existing checksum-verifying reader. Opening a selected receipt still reads and verifies the source again.

## Reproduce on Windows

Run from the repository root with the pinned local storage. Each verifier owns a disposable database. No provider download is needed.

```powershell
.venv/Scripts/python.exe -m pytest -q research_backend/tests/test_replay_structure.py research_backend/tests/test_replay_hour.py research_backend/tests/test_market_structure.py research_backend/tests/test_structure_continuity.py research_backend/tests/test_liquidity.py
.venv/Scripts/python.exe -m scripts.verify_t10_structure_liquidity --source-storage storage --output storage/t10-sl-integration/combined-oracle.json
$env:TRADEFORGE_E2E_API_PORT='8671'
$env:TRADEFORGE_E2E_UI_PORT='4671'
.venv/Scripts/python.exe -m scripts.verify_t10_chart --structure-liquidity --source-storage storage --output storage/t10-sl-integration/browser
.venv/Scripts/python.exe -m scripts.benchmark_t10_structure_liquidity --source-storage storage --output storage/t10-sl-integration/backend-runtime.json
$env:TRADEFORGE_T10_SOURCE_STORAGE=(Resolve-Path storage).Path
.venv/Scripts/python.exe -m pytest -q
npm --prefix frontend test
npm --prefix frontend run build
$env:TRADEFORGE_E2E_API_PORT='8670'
$env:TRADEFORGE_E2E_UI_PORT='4670'
npm --prefix frontend run e2e -- --retries=0
```

For the UI comparison use the same test/config and the same dependencies in each checkout. Set the two absolute input paths to the original storage and Python installation. The baseline worktree at `76764fb` received only copies of the benchmark harnesses and a link to frontend dependencies. The candidate's normal chart controls ran with the inspector closed. Measurement includes the input action and visible response. It excludes browser launch and initial page navigation.

```powershell
$env:TRADEFORGE_E2E_API_PORT='8674'
$env:TRADEFORGE_E2E_UI_PORT='4674'
$env:TRADEFORGE_PAIR_STORAGE=Join-Path ([IO.Path]::GetTempPath()) ('tradeforge-e2e-'+[guid]::NewGuid())
$env:TRADEFORGE_E2E_PYTHON='K:/Projects/TradeForge/.venv/Scripts/python.exe'
$env:TRADEFORGE_COMPARISON_DAY='2026-11-13'
$env:TRADEFORGE_COMPARISON_SOURCE_DIR='K:/Projects/TradeForge/storage/research'
$env:TRADEFORGE_UI_TIMINGS='K:/Projects/TradeForge/storage/t10-sl-integration/ui-reproduction.json'
npm --prefix frontend run e2e -- --config playwright.t10-common-timing.config.ts --retries=0
```

## Rollback

Stop the local API and frontend and run both from the starting revision in a separate checkout, against the same local storage. Retain historical sessions, databases and provider files. The later FVG, order block, independent CRT lifecycle and confluence acceptance gates remain separate.
