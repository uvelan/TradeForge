# Historical FVG and order-block replay

PR #37 began this milestone at `5cb186a6a4977bb552d7c1f62b7d5b728f12f30d`. The saved replay uses the 13 November 2026 Dukascopy M5 BID pair from 00:00 to 22:00 UTC, 264 closed candles per instrument. Feed `dukascopy-web-m1-v1` supplies the archived prices. Arrival in the genuine control is modeled at nominal close. The receipts describe historical chart evidence. Strategy admission remains capped at 160 cutoffs, historical intents retain `AWAITING_EXECUTION_VALIDATION`, and production recorded trading remains `BLOCKED`.

## Inputs

| Instrument | Provider CSV SHA-256 | Saved partition ID | Verified Parquet SHA-256 |
|---|---|---|---|
| EURUSD | `bc2e49a78edad37b73ac1bcdc006892f54aab60c3e769ec837998773cb5960f6` | `ca01e9ac45346d7c9fdfdf4263085831a07735bc16158901c9a14187f77aaf4e` | `fa2af192aa86dc41bb20df51dffe95c29bc48cdf2fe38b643f699985c6b0e09d` |
| GBPUSD | `a2b4947d5e79198920589263a9626e476012650a09d8ba52386e244ab78c1928` | `1cc6acc1b358b3b043438badc69f72413827a8cd5dad379de8e41dd9aaaecfd0` | `aed73f09d78bb3df29d3678e0ace1f00949a673a920ed22dcd6d23cb7a173d02` |

## Operator workflow

1. Open **Market Terminal**, select saved Dukascopy EURUSD M5 BID, add GBPUSD M5 in a second pane, and load the window above. Start bar replay and expand **Replay FVG and order blocks**. Choose a pane and a mitigation rule.
2. At 00:30 UTC, select the bullish EURUSD FVG formed by the 00:25 candle. Inspect its formation and direct source candles. Switch to GBPUSD and select the bullish order block formed at the same time. Expand its qualifying BOS, broken pivot and supporting candles.
3. Step to 00:35, switch between Wick and Close, then rewind before 00:30. The selected family has no revision at that earlier cutoff. Advance to recover the original revision, return to the last candle, save the replay position and restart the API and browser. Reopen the session on the same local browser profile. Per-pane family selections and rules are local workspace preferences. Session candles and checksums come from the saved replay and its archives.

| Selected family | Expected from provider OHLC | Displayed by Chromium |
|---|---|---|
| EURUSD bullish FVG, 1.10508–1.10518 | Unavailable before 00:30. Active at 00:30. At 00:35 the wick fully mitigates it and the close leaves it active. | Same bounds, states, first availability and source IDs |
| GBPUSD bullish OB, 1.32284–1.32313 | BOS qualifies it at 00:30. At 00:35 the wick fully mitigates it and the close mitigates 31.03448% of its width. | Same bounds, states, BOS ID, broken pivot and candle proof |

The read-only route `GET /api/replay/sessions/{id}/zones` pages families in the selected pane. Supply `zone_id` for the selected family's revision history as of the UTC cutoff. Selection still resolves when that family is outside the current page. The checked profile uses FVG and OB producer version 1, wick or close mitigation, and the existing version-2 confirmed structure rules. Order blocks use confirmed BOS under the default profile. The API identifies each level as historical research. Each inspection opens and verifies the selected archive. Corrupting its bytes after a successful inspection makes the next inspection reject the partition.

## Compatibility

The compatibility probe calls the existing public indicator evaluation route using each saved pane at noon and at the 22:00 boundary. It serializes the complete returned indicator results with sorted keys and compact separators, including all returned identifiers, states, prices, and proof data. All eight SHA-256 fingerprints and byte counts matched. The clock profile for these fingerprints is `LEGACY_RESEARCH`. The new saved replay endpoint separately uses a strict cutoff and the pinned archive. [Eight baseline fingerprints](../evidence/T10-ZONES/legacy-indicator-fingerprints.json)

## Genuine expected and observed revisions

**Correctness improved** from an unavailable saved-zone route (HTTP 404 on the starting revision) to HTTP 200 with a pinned revision chain or an explicitly unavailable selection at the requested cutoff. The independent Decimal reference scans provider rows, uses independently reconstructed BOS/pivot observations and computes the opposing-candle lookup, bounds and subsequent lifecycle transitions. The saved-chart service uses the canonical producers. Both matched on every expected formation and revision below, one second before and exactly at each distinct arrival. Equal-time revisions are verified as a group with their internal order preserved. All price bounds match exactly and the percent-depth comparison uses an absolute tolerance below 1e−12 percentage points.

| Symbol | Mitigation | Type | Direction | Active | Touched | Partial | Full | Invalidated | Expired | Uncertain |
|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| EURUSD | wick | FVG | bullish | 27 | 0 | 13 | 14 | 13 | 0 | 0 |
| EURUSD | wick | FVG | bearish | 34 | 0 | 23 | 11 | 19 | 0 | 0 |
| EURUSD | wick | OB | bullish | 5 | 1 | 13 | 1 | 4 | 0 | 0 |
| EURUSD | wick | OB | bearish | 11 | 1 | 11 | 4 | 3 | 0 | 0 |
| EURUSD | close | FVG | bullish | 27 | 0 | 16 | 1 | 26 | 0 | 0 |
| EURUSD | close | FVG | bearish | 34 | 0 | 20 | 1 | 23 | 0 | 0 |
| EURUSD | close | OB | bullish | 5 | 0 | 7 | 0 | 5 | 0 | 0 |
| EURUSD | close | OB | bearish | 11 | 1 | 11 | 0 | 6 | 0 | 0 |
| GBPUSD | wick | FVG | bullish | 24 | 0 | 14 | 11 | 13 | 0 | 0 |
| GBPUSD | wick | FVG | bearish | 37 | 0 | 30 | 14 | 18 | 0 | 0 |
| GBPUSD | wick | OB | bullish | 5 | 0 | 8 | 3 | 2 | 0 | 0 |
| GBPUSD | wick | OB | bearish | 10 | 1 | 13 | 3 | 5 | 0 | 0 |
| GBPUSD | close | FVG | bullish | 24 | 0 | 17 | 2 | 21 | 0 | 0 |
| GBPUSD | close | FVG | bearish | 37 | 0 | 14 | 0 | 29 | 0 | 0 |
| GBPUSD | close | OB | bullish | 5 | 0 | 13 | 0 | 4 | 0 | 0 |
| GBPUSD | close | OB | bearish | 10 | 0 | 8 | 2 | 6 | 0 | 0 |

Counts are revisions. Each ACTIVE row forms one family. Wick and close are separate calculations on the same source. They found **61 FVG / 16 OB families on EURUSD** and **61 FVG / 15 OB on GBPUSD**, totaling 805 checked formation/revision records across both modes. At the final cutoff the oracle checked 1,633 direct source rows and 8,378 structure-context rows across selected receipts. All 31 distinct wick-profile order blocks referenced the same BOS and proof as the existing structure endpoint. For each mode, truncating or extending the capture and swapping the pane order preserved all 38 EURUSD and 36 GBPUSD families visible at noon. The worker and combined sequence and proof digests matched for both symbols and all ten controlled cases. [Combined independent oracle and timestamps](../evidence/T10-ZONES/oracle-combined.json) · [Worker oracle](../evidence/T10-ZONES/oracle.json)

## Controlled cases

The following counts enumerate revisions in disposable copies and include other families in the short prefix. Each record was compared to an independent prefix expectation before and at its reported availability. The oracle file retains each case’s bounds, transition examples and sequence digests.

| Controlled input | Wick revisions | Close revisions |
|---|---|---|
| ob_delayed_bos_straddle | ACTIVE 2, UNCERTAIN 2 | ACTIVE 2, UNCERTAIN 2 |
| ob_equal_arrival_lifecycle | ACTIVE 2, TOUCHED 1, PARTIALLY_MITIGATED 1, FULLY_MITIGATED 2 | ACTIVE 2, PARTIALLY_MITIGATED 1, INVALIDATED 1 |
| ob_equal_pivot_break_close | ACTIVE 1, FULLY_MITIGATED 1 | ACTIVE 1, INVALIDATED 1 |
| ob_exact_entry_touch | ACTIVE 3, TOUCHED 1 | ACTIVE 3 |
| ob_strict_terminal_close | ACTIVE 2, INVALIDATED 2 | ACTIVE 2, INVALIDATED 2 |
| equal_gap_boundary | No zones | No zones |
| equal_arrival | ACTIVE 1 | ACTIVE 1 |
| delayed_formation_straddle | ACTIVE 1, UNCERTAIN 1 | ACTIVE 1 |
| exact_entry_and_far_boundary | ACTIVE 1, PARTIALLY_MITIGATED 1, FULLY_MITIGATED 1 | ACTIVE 1, FULLY_MITIGATED 1 |
| strictly_past_far_boundary | ACTIVE 1, PARTIALLY_MITIGATED 1, INVALIDATED 1 | ACTIVE 1, INVALIDATED 1 |

The focused API tests additionally cover an older input arriving behind the replay frontier, a newer opposing candle unavailable at the BOS open, an expiring zone, archive mutation and overlapping pins. The replay refuses an inconsistent saved-session chronology. At the producer boundary, a discarded late row cannot enter the qualifying structure proof. A pivot delivered simultaneously with a would-be break cannot qualify that break. Two eligible lifecycle observations with a shared delivery time retain the producer's order in the latest visible revision.

## Runtime scorecard

One Windows host, pinned pair and identical settings. The API uses fresh Python processes for cold samples and a warm local TestClient for the remaining actions. Cold timings exclude process launch and OS cache eviction. Browser timing includes the operator action through visible evidence. Percentage changes compare the same action against the starting revision. Each latency verdict describes the observed sample. Small changes can reflect host variability.

| Action | Samples | Median ms (change) | p95 ms (change) | Observed verdict |
|---|---:|---:|---:|---|
| API cold_reopen_and_overlays | 8 | 92.40 → 90.02 (-2.38, -2.6%) | 150.47 → 95.47 (-55.01, -36.6%) | improved |
| API cold_reopen_only | 8 | 57.97 → 54.96 (-3.00, -5.2%) | 94.52 → 56.37 (-38.15, -40.4%) | improved |
| API collapsed_step | 30 | 32.00 → 30.91 (-1.09, -3.4%) | 34.74 → 37.81 (+3.07, +8.8%) | inconclusive |
| API collapsed_rewind | 30 | 32.91 → 30.98 (-1.93, -5.9%) | 35.35 → 35.13 (-0.23, -0.6%) | improved |
| API new_list | 30 | 59.05 | 66.61 | unmeasured comparison |
| API new_selection | 30 | 66.98 | 71.62 | unmeasured comparison |
| API new_list_and_selection | 30 | 127.16 | 138.13 | unmeasured comparison |
| Chromium structure-liquidity collapsed step | 8 | 158.98 → 164.29 (+5.31, +3.3%) | 188.68 → 239.12 (+50.43, +26.7%) | degraded |
| Chromium structure-liquidity list | 8 | 220.03 → 159.33 (-60.70, -27.6%) | 266.30 → 213.11 (-53.19, -20.0%) | improved |
| Chromium structure-liquidity select | 8 | 167.49 → 105.07 (-62.42, -37.3%) | 191.16 → 162.87 (-28.29, -14.8%) | improved |
| Chromium structure-liquidity source expansion | 8 | 112.30 → 140.53 (+28.23, +25.1%) | 135.85 → 165.97 (+30.12, +22.2%) | degraded |
| Chromium structure-liquidity rewind | 8 | 235.69 → 213.27 (-22.41, -9.5%) | 271.12 → 262.46 (-8.67, -3.2%) | improved |
| Chromium structure-liquidity cold browser reopen | 8 | 386.97 → 262.94 (-124.03, -32.1%) | 720.86 → 666.34 (-54.51, -7.6%) | improved |
| Chromium zones collapsed step | 8 | 158.29 | 173.43 | unmeasured comparison |
| Chromium zones list | 8 | 178.89 | 197.33 | unmeasured comparison |
| Chromium zones select | 8 | 126.26 | 147.27 | unmeasured comparison |
| Chromium zones source expansion | 8 | 72.90 | 88.02 | unmeasured comparison |
| Chromium zones rewind | 8 | 227.38 | 279.73 | unmeasured comparison |
| Chromium zones cold browser reopen | 8 | 283.46 | 377.13 | unmeasured comparison |
| Existing closed-panel H1 reopen | 8 | 265.23 → 261.58 (-3.66, -1.4%) | 342.74 → 307.53 (-35.21, -10.3%) | improved |
| Existing closed-panel H1 step | 24 | 143.65 → 154.98 (+11.33, +7.9%) | 178.43 → 199.03 (+20.59, +11.5%) | degraded |
| Existing closed-panel H1 rewind | 24 | 158.22 → 165.02 (+6.80, +4.3%) | 200.54 → 187.49 (-13.05, -6.5%) | inconclusive |
| Existing closed-panel H1 inspect | 8 | 48.34 → 47.38 (-0.96, -2.0%) | 52.94 → 54.77 (+1.82, +3.4%) | inconclusive |

Backend peak working set (KiB, single backend process, maximum across cold samples).

| Workload | Starting | Candidate | Change | Verdict |
|---|---:|---:|---:|---|
| cold_reopen_and_overlays | 94,700 | 94,864 | +164 (+0.2%) | degraded |
| cold_reopen_only | 93,384 | 93,468 | +84 (+0.1%) | degraded |
| warm_common | 94,524 | 96,064 | +1,540 (+1.6%) | degraded |
| Warm including new inspection | — | 127,780 | — | unmeasured comparison |

The baseline structure inspector kept all 115 rows mounted even with both tables folded. The candidate starts at zero, mounts the 109 context rows when that table opens, then returns to zero. The six direct witnesses remain available in a separate disclosure. **DOM work improved**. Source expansion takes longer on the measured structure selection. The browser p95 for eight samples is the observed maximum (nearest rank). The API uses interpolated percentiles. The collapsed inspector generated zero event requests. See the raw counters for SQL, verified bytes and decoded rows.

| Action | Bytes checksum-verified | Rows decoded | SQL SELECTs |
|---|---:|---:|---:|
| Zone list | 10,538 | 264 | 3–5 |
| Zone selection | 10,538 | 264 | 3–5 |
| List followed by selection | 21,076 | 528 | 6–8 |
| Browser opens already-loaded source table | 0 | 0 | 0 |

The selected FVG mounted 0, 5 and 0 source rows across the same fold, expand and fold actions. The newly measured zone actions establish a first baseline. Overall latency improvement is **inconclusive**. The sample favored structure selection and cold reopen, while expansion became slower. The integration run and the independent worker timing also disagreed about the direction of the warm collapsed rewind change.

## Delivery and verification

The owner started at 09:15:51.844 UTC on 26 November 2026. The post-dispatch snapshot at 09:16:47 showed all three workers running. Each used a sibling worktree outside test discovery, separate temporary SQLite and evidence directories, and a separate port pair. Backend-only workers used local TestClient transports.

| Worker | Model / effort | Dispatch UTC | Start UTC | Finish UTC | Worker commits → integrated commits |
|---|---|---|---|---|---|
| /root/zones_api | GPT-6.2 Astra / high | 09:16:06.236 | 09:16:07.914 | 09:28:25.483 | c52062d, a800b21 → 4216fa2, 8a82510 |
| /root/zones_ui | GPT-6.2 Sol / high | 09:16:19.872 | 09:16:21.508 | 09:33:59.561 | 9b06dc4, 5559923 → 31e86b8, d7de894 |
| /root/zones_oracle | GPT-6.2 Sol / high | 09:16:29.960 | 09:16:31.650 | 09:32:02.787 | 1fc6c7e → d404fd7 |

All three workers overlapped for 713.8 seconds. Their combined active interval spanned 1071.6 seconds. Integration began 09:24:34.208 while the workers continued testing. The combined candidate was frozen at 09:32:59.809. Those 505.6 seconds include review, integration and rework, overlapping with worker time. The operator journey depended on both the independent reference and the backend contract. Timed workloads ran serially before the full verification group. Parallel speedup and token efficiency are **unmeasured** because there is no comparable serial run.

Review exposed an older candle that a producer could discard after its chronological frontier advanced. A strengthened receipt excludes it from the BOS proof. The first independent controlled run also exposed an OHLC copy with an out-of-range close. The API rejected that fixture with HTTP 409. The oracle reran with valid OHLC and explicitly grouped simultaneous revisions. The final browser benchmark isolated each cold-reopened panel, followed by another complete worker browser run. These development reruns are included in the delivery and token measurements.

Worker API timings were collected on `c52062d`, the worker's complete oracle on `a800b21`, and its final browser receipt on the UI worktree with both backend changes at `cc5d7a6`. The integration acceptance and combined-tested revision is `928f8558cb435665085c4401712ce73f7729178b`. Subsequent changes package evidence only. [Backend starting timings](../evidence/T10-ZONES/api-baseline.json) · [Worker API timings](../evidence/T10-ZONES/api-candidate.json) · [Combined API timings](../evidence/T10-ZONES/api-combined.json) · [Combined browser selections](../evidence/T10-ZONES/browser-selections.json) · [Restarted selection](../evidence/T10-ZONES/zones-journey-restart.json) · [Reopened order block and qualifying BOS](../evidence/T10-ZONES/zones-reopened-order-block.png)

### Windows results

| Check | Result | Command elapsed |
|---|---|---:|
| Focused causal producer and replay tests on A | 139 passed | 26.84 s |
| Existing genuine structure browser initial + restart | Both passed | 42.99 s |
| Combined independent API reference on both modes and ten controlled cases | Passed | 216.15 s |
| Combined genuine zone browser initial + restart | 2 + 2 passed, zero retries | 40.40 s + 33.92 s |
| Full backend | 1,150 passed, 1 Windows-inapplicable POSIX FIFO fixture skipped | 661.84 s |
| Frontend unit / production build | 13 passed / built | 1.53 s / 21.24 s |
| Standard Chromium | 88 passed, zero retries | 443.56 s |

The combined suites ran once. The full group was dispatched at 09:35:50.557 UTC and the backend completed last at 09:46:58.041, a 667.5-second verification span. API, unit and browser times overlap in that group. The initial and restarted genuine-browser invocations each run two phase-specific checks. The sole backend warning concerned the existing Starlette TestClient HTTP dependency. [Commands, outcomes and measured times](../evidence/T10-ZONES/verification.json)

## Code and usage accounting

The change reuses producer and archive-verification paths. The new route is read-only. The existing structure table and the new zones table share one disclosure and source-table renderer. Code-size efficiency is **unmeasured** against an equivalent implementation. The absolute diff counts below classify application modules and chart components as production, test files/configuration and runnable acceptance scripts as verification, and documentation, receipts and the screenshot as evidence. Counts use Git numstat from the milestone base, without generated dependencies or the provider inputs. Binary files are counted separately.

| Category | Files | Lines added | Lines removed | Binary files |
|---|---:|---:|---:|---:|
| Production | 11 | 497 | 85 | 0 |
| Verification | 10 | 1843 | 3 | 0 |
| Evidence | 19 | 9256 | 1 | 1 |

[Portable per-agent and per-model token receipt](../evidence/T10-ZONES/usage-checkpoint.json). The receipt includes owner, workers and the linked approval reviewer for each. Its UTC sampling time and the last observed sample per agent are explicit. Total tokens equal input plus output. Cached and uncached input partition input tokens, and reasoning is already included in output. A separate count for opaque provider retries, actual prices and billed cost is UNAVAILABLE.

## Reproduction on Windows

Run in the repository root with the existing local provider storage and installed project dependencies. The oracle checks the CSV and Parquet pins before generating a reference. The browser runner creates its own temporary research database and restarts the services. The named output directories below are disposable.

```powershell
New-Item -ItemType Directory -Force storage/t10-zones-integration | Out-Null
$env:TRADEFORGE_T10_SOURCE_STORAGE=(Resolve-Path storage).Path
$env:TRADEFORGE_E2E_PYTHON=(Resolve-Path .venv/Scripts/python.exe).Path
$env:TRADEFORGE_E2E_API_PORT='8692'
$env:TRADEFORGE_E2E_UI_PORT='4692'
.venv/Scripts/python.exe -m pytest -q research_backend/tests/test_replay_zones.py research_backend/tests/test_replay_structure.py research_backend/tests/test_replay_hour.py research_backend/tests/test_order_blocks.py research_backend/tests/test_fvg.py research_backend/tests/test_legacy_whole_candle_timing.py research_backend/tests/test_strict_causal_timing.py
.venv/Scripts/python.exe -m scripts.verify_t10_zone_compatibility --source-storage storage --output storage/t10-zones-integration/legacy-candidate.json
.venv/Scripts/python.exe -m scripts.verify_t10_zones --source-storage storage --output docs/implementation/evidence/T10-ZONES/oracle-combined.json
.venv/Scripts/python.exe -m scripts.benchmark_t10_zones --source-storage storage --output docs/implementation/evidence/T10-ZONES/api-combined.json
.venv/Scripts/python.exe -m scripts.verify_t10_chart --zones --source-storage storage --output storage/t10-zones-integration/zones-chart
.venv/Scripts/python.exe -m scripts.verify_t10_chart --structure-liquidity --source-storage storage --output storage/t10-zones-integration/structure-chart
.venv/Scripts/python.exe -m pytest -q
npm --prefix frontend test
npm --prefix frontend run build
$env:TRADEFORGE_E2E_API_PORT='8690'
$env:TRADEFORGE_E2E_UI_PORT='4690'
npm --prefix frontend run e2e -- --retries=0
```

The shared H1 control used `playwright.t10-common-timing.config.ts` on both revisions. The command was `npm --prefix frontend run e2e -- --config playwright.t10-common-timing.config.ts --retries=0`. Set `TRADEFORGE_COMPARISON_DAY=2026-11-13`, `TRADEFORGE_COMPARISON_SOURCE_DIR` to the absolute path of `storage/research`, `TRADEFORGE_PAIR_STORAGE` to a unique temporary directory with the prefix `tradeforge-e2e-`, and `TRADEFORGE_UI_TIMINGS` to the desired JSON path. The baseline zone browser harness used `--zones --zones-baseline` with the original production sources. The baseline backend benchmark uses the harness’s `--repo` option pointing at the starting revision.

## Rollback

Stop the local frontend and API, check out the starting revision in a separate worktree, and restart the two processes against the original workspace. Keep SQLite databases, provider files and saved sessions. No database migration is required. Independent CRT lifecycle and confluence are later milestones. The genuine broker statement and 480 missing November 2025 EURUSD open hours are separate acceptance dependencies.
