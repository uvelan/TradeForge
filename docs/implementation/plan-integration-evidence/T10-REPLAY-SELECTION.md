# T10 · Frozen family selection and the 15:00 indicator checkpoint

PR #37 started this correction at `566dc22d48f0b810dc3240413f0fa665098cdf34`. All three workers were dispatched before waiting. The single post-dispatch status snapshot on the Windows workstation showed:

| Worker ID | Model | State at snapshot | Assigned API / browser ports |
|---|---|---|---|
| `/root/picker_a` | GPT-6.2 Astra, high | running | 8581 / 4581 |
| `/root/identity_b` | GPT-6.2 Sol, high | running | 8582 / 4582 |
| `/root/indicators_c` | GPT-6.2 Sol, high | running | 8583 / 4583 |

Each worker had a separate sibling worktree outside the repository's pytest collection root. Browser work used isolated data directories.

## Operator behavior

Choose a family explicitly under **Recorded SMT family** when replaying an archived chart. The pin identifies the selected family by its full event key within the ordered feed and partition scope. Rewind before that family's first candidate to inspect the absence of a revision. The picker keeps the chosen key while another family is eligible. Advance across the candidate and later transitions, browse other pages, or reopen the same scope to inspect the pinned family's applicable revision. Automatic preview remains available for browsing.

The backend regression seeds an unrelated family's provenance with the desired key to distinguish exact identity from a text search. On the starting revision, requesting the future family at time 119 returned the other family's already-available candidate. The assertion expected an empty page. The regression also checks the candidate and confirmation boundaries and repeats the earlier read after reading the later result.

For the separate numerical checkpoint, use EURUSD and GBPUSD M5 BID in two linked views, the saved [13:00, 15:00) UTC window on November 13, 2026, CCI(20), and fast Stochastic(14, 3). Save at 15:00, restart the local API and browser, resume the saved replay and rewind before warm-up. Advance again to compare the restored series with the independently pinned provider CSV and Parquet data. [Archive pins and formula conventions](T10-HISTORICAL-CHART.md)

## Indicator restart evidence

Session `e5bcdbe2a81b0d9dbb3e806d2754888335d423d2e2109fed296b7335145b9c29` saved position 23, the twenty-fourth closed M5 candle. The browser read the actual plotted line-series arrays in both views. An independent oracle calculated expected values using exact rational arithmetic on the separately checksummed provider CSVs. At 15:00 each lane had five CCI, eleven %K and nine %D points. All 50 timestamps and all 50 values were identical across the pre-restart plot, reopened plot and final recovery. The largest absolute difference from the numerical reference was 7.81 × 10⁻¹² (tolerance 10⁻¹⁰). At 14:05, both lanes showed 13 closed candles and zero indicator points. Stepping forward restored %K at 14:10, %D at 14:20 and CCI at 14:40.

[Point-by-point receipt and archive hashes](../evidence/T10-pin-c/dual-chart-indicators-checkpoint.json)


## Independent picker regression

B's immutable family key is `5aaf19ac4d08237796dccf3154fc0e0dcc333229e6656ca778424fdbbed54a7b`. Three real archive watches with different pinned rules generated the eligible families. The spec requested pages of two and retained the server's cursor, order and response bodies. It selected B exactly once. The spec bytes were identical on the baseline and corrected service (Windows checkout SHA-256 `5f603c536a6d88c207f754aa2a644df2fc3527e2d14605f3455ceb30abbfdd2f`).

| Operator action | Starting behavior | Corrected observation |
|---|---|---|
| Rewind from 13:25 to 13:20, with A eligible | A's candidate replaced the selected family | B remained selected with no revision and no displayed receipt |
| Advance to 13:25, then browse the older page | The older page showed C | B's candidate `cc454f320070e8fa8175503e4a0831f3b1b8496fab0e6ffd5791637e24ab05a2` and original lane pins recovered. Browsing the page retained B. |
| Hold B's exact 13:30 invalidated response; deliver it after rewinding | The old picker never requested the off-page family by exact key | The server returned B's `3366e60877d258ce0291262b38da1ba632854749abd2dbe19718b24380cacc2e`; after delivery the 13:20 view continued to show no revision |
| Reopen, swap the pair, then return | The global pin leaked into the swapped pair | Fresh Chromium context restored B for its scope. The reversed scope previewed A automatically; returning restored B. |

Previously saved global preferences do not identify their chart scope. The upgraded picker starts in automatic mode until a selection is made in that scope. Its preference includes the ordered chart lanes, feed, instrument, basis, timeframe and partition pin pairs. Errors retrieving a revision remain visible. [Before and after browser receipts](T10-SMT-PIN-CHROMIUM.json)

## Worker commits and commands

| Worker | Commit | Measured result |
|---|---|---|
| A | `c67dad211193c2ccab445ec8d69f0ff7470de0e2` | Production change. Build and existing genuine chart/reopen journey passed. |
| B | `545a125b764e6e184f619882363a103d1dd1175e`, evidence `4a651b076348f0b5fc563453baa2dfba3c6c1d8a` | Identical spec failed in 28.78 s on the original and passed in 16.13 s on A's change. |
| C | `6e0a675347f608acfa4497a53ce4395a34f1f2d3` | All plotted indicator values on both charts checked after restarting the API and browser. Initial and restart phases passed in 8.71 s and 11.18 s. |

Run from the repository root in PowerShell with the pinned archives available. Each browser command starts its own disposable server and application database.

```powershell
$env:TRADEFORGE_T10_SOURCE_STORAGE=(Resolve-Path storage).Path
$env:TRADEFORGE_COMPARISON_DAY='2026-11-13'
$env:TRADEFORGE_E2E_API_PORT='8592'
$env:TRADEFORGE_E2E_UI_PORT='4592'
npm --prefix frontend run e2e -- --config playwright.smt-pin.config.ts --retries=0 --reporter=line
$env:TRADEFORGE_E2E_API_PORT='8593'
$env:TRADEFORGE_E2E_UI_PORT='4593'
.venv/Scripts/python.exe -m scripts.verify_t10_chart --indicators-checkpoint --source-storage storage --output storage/t10-integrity/checkpoint
.venv/Scripts/python.exe -m pytest -q research_backend/tests/test_smt_authority.py
.venv/Scripts/python.exe -m pytest -q
npm --prefix frontend test
npm --prefix frontend run build
$env:TRADEFORGE_E2E_API_PORT='8591'
$env:TRADEFORGE_E2E_UI_PORT='4591'
npm --prefix frontend run e2e -- --retries=0
```

The original numerical and SMT chart test also ran on ports 8594/4594 using `.venv/Scripts/python.exe -m scripts.verify_t10_chart --output storage/t10-integrity/prior`. The earlier development assertions assumed an exact number of older watch families and a top-level dataset ID on a replay response. The final specs check the genuine page contents and the persisted partition ID/checksum pairs.

## Rollback

For a local rollback, stop the API and frontend, run revision `566dc22d48f0b810dc3240413f0fa665098cdf34` from a separate checkout against the same workspace, and restart both processes. No database migration or rewrite of receipts is required. The older picker resumes its global selection behavior. Keep the persisted scoped preferences for use when upgrading again. Public historical admission remains 160 cutoffs and production recorded trading remains BLOCKED.

## Combined Windows run

Tested code SHA `e78f69d76d78f55aa9a3223c0a9f886a0b413f99`. Subsequent commits add handoff documentation and evidence only.

| Check | Result | Time |
|---|---|---:|
| Focused SMT authority API | 9 passed | 19.80 s |
| B's independent picker regression | 1 passed, zero retries | 24.1 s |
| C's dual-chart checkpoint | Save and restarted restore both passed, zero retries | 17.21 s / 11.44 s |
| Earlier genuine T10 journey | Single/paired chart and fresh-process restore passed, zero retries | 53.31 s / 22.76 s |
| Full backend, recorded numerical inputs enabled | 1,124 passed, 1 Windows-inapplicable POSIX FIFO fixture skipped, 1 Starlette deprecation warning | 707.40 s |
| Frontend unit suite | 13 passed | 3.24 s including npm |
| Production build | Passed with dependency bundler warnings | 20.41 s |
| Standard Chromium suite | 86 passed, zero retries, including 100 navigation cycles with no browser errors | 472.42 s including npm |

Each full suite ran once after the focused checks on the combined code. Each two-phase browser command ran both applicable phases in sequence. The 15:00 receipt generated by the integrated browser matched the committed Git blob byte for byte, SHA-256 `3822c6c94f8370c87a02a2657924be6b7cb4504ff1a822fc037e01904d273df9`.
