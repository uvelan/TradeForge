# T10 historical chart replay

Scope: Windows-local CCI(20), fast Stochastic(14) %K with a three-point simple %D, and one recorded same-bar bearish EURUSD/GBPUSD SMT family on November 13, 2026. Starting PR #37 revision: `d7e73e992d2c7c941984670709d665d037037fa4`.

## Inputs and clock

Both archives use Dukascopy M5 BID candles from feed `dukascopy-web-m1-v1`. The numerical chart starts with zero warm-up candles and reads the 24 opens from 13:00 through 14:55 UTC. Candle-open time locates a plotted point. Nominal close (open + 300 seconds) supplies its modeled availability. Neither archive measures actual delivery latency. The SMT watch additionally needs earlier candles to establish its previous pivot and uses 12:15–13:40 UTC. The separate historical strategy recipe with a 20-second peer delay is outside this watch.

| Symbol | Registered ID | Parquet SHA-256 |
|---|---|---|
| EURUSD | `ca01e9ac45346d7c9fdfdf4263085831a07735bc16158901c9a14187f77aaf4e` | `fa2af192aa86dc41bb20df51dffe95c29bc48cdf2fe38b643f699985c6b0e09d` |
| GBPUSD | `1cc6acc1b358b3b043438badc69f72413827a8cd5dad379de8e41dd9aaaecfd0` | `aed73f09d78bb3df29d3678e0ace1f00949a673a920ed22dcd6d23cb7a173d02` |

## Independent numerical reference

The reference uses exact rational arithmetic on the provider's separately checksummed M5 CSV values. The acceptance command imports the pinned Parquet into disposable local storage and checks its candle endpoint against those values. Each of the 25 prefix states per instrument is compared at the chart indicator endpoint. At every close the harness verifies both the immediately preceding second and the exact boundary, checks that adding later candles with the original cutoff leaves the response series and input fingerprint intact, and compares every older point as the cutoff advances.

| Indicator | Last contributing open for first point | First available UTC | Points per symbol at 15:00 | Max absolute error, EURUSD / GBPUSD |
|---|---|---|---:|---|
| CCI(20) | 14:35 | 14:40 | 5 | 0 / 0 |
| Stochastic %K(14) | 14:05 | 14:10 | 11 | 7.10e−12 / 7.81e−12 |
| Stochastic %D(3) | 14:15 | 14:20 | 9 | 4.60e−12 / 4.10e−12 |

Errors are measured against the reference converted to the API's floating-point output format. The assertion tolerance is 1e−10. At 15:00 the EURUSD CCI/%K/%D reference is 55.17643142 / 82.24852071 / 78.22000663, and GBPUSD is 120.08801651 / 80.22813688 / 85.93155894. [Numerical receipts](../evidence/T10/numerical.json)

## Signal chronology

The previous and current pivot opens are 12:35 and 13:05 UTC. EURUSD highs rise from 1.10549 to 1.10557. GBPUSD highs fall from 1.32316 to 1.32306. These are observations on the archived BID lanes. The watch records an immutable revision when the additional closed candles required by its rules become available.

| As-of UTC | Expected revision of family `7e9485b627eb7c327f80ae78f4e524920c33ef6ffe6802ecdd76397c53985e43` |
|---|---|
| 13:19:59 | None |
| 13:20:00 and 13:24:59 | Candidate · `2283b00d97e3c216c0c142f48303f315cb89fce5f6407a4b6e8135e33f357318` |
| 13:25:00 and 13:29:59 | Confirmed · `c04e4d87c513f365988fa35d9e81f4c67eb80afb603623b550b7d82e012c9ff0` |
| 13:30:00 | Invalidated · `2d8c348da578ee8e3d7335c9d674c215d67798f007b37404b6ab9d821d7738df` |

Only the watch's actual local publication timestamp describes when the application published evidence. The 2026 modeled transition times describe what this historical replay could know.

All six expected states matched the strategy producer and the watch. The independent check saved the confirmation while requesting the invalidated revision still returned 404. After processing through 13:40, the rewound receipt recovered that same confirmation and the full 14-bar supporting sequence on each lane. The last supporting candles for confirmation opened at 13:20 and closed EURUSD 1.10513 and GBPUSD 1.32289. The next GBPUSD high of 1.32328 exceeded its prior pivot high of 1.32316, producing invalidation at the 13:30 close. [Source bars, proofs and replay receipt](../evidence/T10-chart-parity/signal-lifecycle.json)

## Operator journey

1. In **Data Library → Historical SMT watch**, choose the two partitions above (EURUSD primary), start 12:15 UTC and end 13:40 UTC. Select **Watch exact M5 pairs** and wait for the verified frontier.
2. In **Market Terminal**, choose the saved Dukascopy archive, EURUSD, M5 and UTC display. Under the saved range, enter November 13 for both dates, 13:00 for the start and 15:00 for the exclusive end. Load the range. Enable CCI(20) and Stochastic(14). For two linked chart views, choose GBPUSD M5 in the second view and enable its indicators and crosshair synchronization.
3. Start historical replay. Seek 13:20, step to 13:25 and 13:30, then step back to 13:25. Under **Recorded SMT at chart cutoff**, select the bearish family anchored at 13:05 and choose **Inspect transition candles**. Expand each lane for its pivots, dataset ID, checksum and last supporting candle. In a single-instrument view the other instrument is labeled watch peer. With a comparison selected, the receipt follows chart lane order. A chart-window calculation can have zero signals when its window excludes the 12:35 pivot. The recorded watch evidence remains available.
4. Advance to each indicator's first availability. Hover at a shared candle open to inspect both instruments. Swap the instruments and repeat. Save the replay position. After restarting the API and browser, choose **Resume saved replay**. In the swapped comparison, checkpoint `4cb062680d01be495e1a2d2c604c512f948a58623f277425c766983c21c8e1db` restored index 4 (five closed bars), the 13:25 confirmation, GBPUSD first, EURUSD second, and both 13:20 UTC OHLC rows.
5. **Signal Explorer → Evidence Inspector → Replay recorded evidence** also supports stepping through the selected archived family. Replay now draws its recorded lifecycle at the cursor. The inspector keeps later parent details and attachment actions outside the rewound view.

The Chromium test reads the actual plotted series and compares them with independent projections from the verified source. During the paired replay it holds back a genuine HTTP 13:25 response, advances to 13:30, then delivers the older confirmation. The invalidation stays selected. The prior paired archive regression separately exercises a delayed comparison response across an instrument switch. [Initial expected/observed values](../evidence/T10-chart-parity/browser/chart-journey.json) · [Restart comparison](../evidence/T10-chart-parity/browser/chart-reopened.json) · [Single chart](../evidence/T10-chart-parity/browser/single-eurusd.png) · [Linked instruments](../evidence/T10-chart-parity/browser/linked-gbpusd-eurusd.png) · [Reopened confirmation](../evidence/T10-chart-parity/browser/reopened-confirmation-receipt.png)

## Reproduction on Windows

Run from the repository root with the original local archives, project Python environment, frontend dependencies and Playwright Chromium installed. These commands use disposable application databases and keep the local dashboard on its existing ports.

```powershell
$env:TRADEFORGE_T10_SOURCE_STORAGE=(Resolve-Path storage).Path
.venv/Scripts/python.exe -m scripts.t10_numerical_acceptance --source-storage storage
.venv/Scripts/python.exe -m scripts.verify_archive_smt_watch
.venv/Scripts/python.exe -m pytest -q --ignore=storage/t10-workers
npm --prefix frontend test
npm --prefix frontend run build
$env:TRADEFORGE_E2E_API_PORT='8540'
$env:TRADEFORGE_E2E_UI_PORT='4540'
npm --prefix frontend run e2e -- --retries=0
$env:TRADEFORGE_E2E_API_PORT='8567'
$env:TRADEFORGE_E2E_UI_PORT='4567'
.venv/Scripts/python.exe -m scripts.verify_t10_chart --output storage/t10-integration/chart
$env:TRADEFORGE_E2E_API_PORT='8541'
$env:TRADEFORGE_E2E_UI_PORT='4541'
$env:TRADEFORGE_COMPARISON_DAY='2026-11-13'
npm --prefix frontend run e2e -- --config playwright.comparison.config.ts --retries=0
.venv/Scripts/python.exe -m scripts.verify_archive_smt_watch --browser
```

The genuine test runs each of its two phases in a new API and browser process. Both use the same private database and browser preferences. Each invocation selects the applicable phase. Hosted tests can run without possessing the local archive; only the local genuine-data execution supplies the recorded-market acceptance.

## Limits and rollback

This run covers two numerical indicator definitions on M5 BID and one recorded same-bar bearish family. Other numerical and structural indicators, higher-timeframe alignment, longer workspaces, MT5, and throughput or rendering benchmarks require separate evidence. The 480 missing November 2025 open hours and T08 broker statement remain outstanding. Historical admission stays at 160 cutoffs and production recorded trading remains BLOCKED.

To roll back, revert the T10 commits after `d7e73e992d2c7c941984670709d665d037037fa4` through this handoff in reverse order and restart the local application. This increment adds no database migration. Retain archives, watch receipts and replay sessions.

## Integrated Windows verification

Tested code SHA: `b9b4637c081c4aa7ecb1c18c99145356ec42e501`. The handoff commit following it adds documentation, receipts and screenshots only.

| Check | Observed | Elapsed time |
|---|---|---:|
| Independent numerical runner | Both symbols passed, 25 closed-bar prefixes each | 16.75 s |
| Watch recovery with fresh processes | All six probes matched, one family confirmation alert; uninterrupted, restarted and crash-before/after-commit proofs agreed | 33.98 s |
| Full backend, genuine indicator cases enabled | 1,123 passed, one POSIX FIFO fixture skipped, one Starlette deprecation warning | 718.01 s |
| Frontend unit tests | 13 passed | 1.82 s |
| Production frontend build | Passed, with dependency bundler warnings | 16.85 s |
| Standard Chromium | 86 passed with zero retries, including the 100-cycle navigation check | 473.50 s |
| New genuine chart and restart | Both phases passed with zero retries | 43.46 s initial, 8.71 s reopen |
| Existing genuine paired-chart stale-pane control | 1 passed, zero retries | 28.41 s |
| Signal Explorer genuine browser regression | Original and restarted API/browser passed, zero retries | 50.23 s total |

In development the first chart attempt used an incompatible assertion call, and the second matched two status elements following a successful checkpoint save. The corrected assertions passed before integration. Visual inspection found pane controls overlapping the UTC legend; the final screenshots include the corrected placement. The initial integrated pytest command found duplicate test modules inside the three isolated worker checkouts and stopped during collection. The full rerun excluded only those duplicate worktrees. They have since been moved beneath an ignored dot directory, and default collection also found 1,124 project tests.
