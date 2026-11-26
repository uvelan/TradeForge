# PR 37 Windows-local integration closure

The merge decision concerns the single-operator local application. The operator can import recorded data, freeze and save a paired chart, restore it in another application profile, run a bounded strategy, inspect the executable quote and reopen its review. Three failures at the boundaries between those features were reproduced and corrected. The final decision and exact-head verification receipt are attached to [PR 37](https://github.com/uvelan/TradeForge/pull/37).

| Revision | Meaning |
|---|---|
| `517f8122b633f9143324f99dd7b075c7c689df3f` | Fetched main and PR merge base |
| `93691cd45de2c6b5ad32f7d146b0bfa5fb4749c3` | Fetched PR 36 head. Git ancestry check returned 0 against PR 37 and 1 against main. Leave both PRs open until independent approval. Close 36 only after verifying this commit on merged remote main. |
| `989b4f82ff3729b5d495527c3c034d0453621f73` | Starting integration head. Both hosted Windows jobs passed. Includes the contributor's Strategy Builder committed-save correction. |
| `03b2b148cb6f0a3e79c0bcfe6fd1fb7100c2264b` | Combined production, tests, historical review receipts and comparison script. Full local suites target this SHA. |

## What the three independent reviews changed

| Owner / worker commit | Reproduction on the starting revision | Corrected result |
|---|---|---|
| Strategy, execution and accounting · Astra high · `b4674d9` → `ef5e6b3` | An explicit paper entry leaves the plan marked PLANNED. The public API accepted a fresh PRE_TRADE export after the fill (201). | Checks the paper ledger in the same read transaction as the export. After entry and after final close the request returns 422. The pre-existing ZIP still has its original bytes. Eight focused paper, concurrency and relocation cases passed. |
| Terminal, Builder and operator recovery · Sol high · `4292ec4` → `c838735` | On reopen an initial 503 on the library GET discarded working template controls and gave no retry. | Templates remain available. Retry uses GET, including after a second 503. Three focused Chromium cases and TypeScript passed. |
| Provenance, reproduction and plan · Sol high · `a2355df` → `40d229d` | On a genuine confluence rerun a historical worker poll hit SQLITE_BUSY configuring a connection. A second SQLite connection holding a lock reproduced supervisor termination with a queued run. | BUSY/LOCKED retry on the normal poll interval. Resource checks and fenced final writes survive contention. Unexpected database errors propagate. After lock release the worker claims the queued run. 65 focused worker, publication, confluence, monthly and relocation tests passed. |

## Operator journeys and inputs

1. Open Data Library and import the local BI5 files, then retry. Inspect BID/ASK quote coverage and reconstructed M5 OHLC. In Market Terminal select saved EURUSD/GBPUSD M5 BID, freeze at 14:05 UTC and save a Lesson in each lane order. During another publication a missing partition remains 404, and an injected full disk returns 507. Retry, restore both databases and archive roots into a disposable application profile, open the saved Lessons and compare pins and PNG bytes. The drill found 12 research datasets and 12 publication receipts, 10 mapped candle partitions, two explicitly unsupported tick mappings, 63 SMT revisions, 126 pinned lane references and zero dangling receipts. Both 14:00 candles were visible at 14:05, with distinct closes 1.10527 and 1.32318. See [partition, database and PNG hashes](market-restore.json).
2. In Strategy Builder save v1, then edit and save v2. Intercept the subsequent history and library reads with a 503 after the successful POST. Each save returned 201, the UI retained the committed version, and refreshing issued zero writes. A separate read-only connection counted two versions of one strategy. A separate pagination fixture retained all 45 versions. Fresh production API and Chromium processes reopened v2 and v1 by their original IDs. In Market Terminal, rewind and swap panes, then restart and reopen the 13:25 UTC confluence receipt. Both processes returned the same saved ID and evidence. See [version IDs and restart receipt](operator.json).
3. In Strategy Lab evaluate the saved FVG strategy on November 13, 2025. It invalidated at 15:00 UTC with zero intents. A separate named monthly BID-rise strategy qualified at ordinal 5, entered at ordinal 6 (1.16243 ASK) and stopped at ordinal 1564 (1.16143 BID). A pre-exit export omits the closed outcome. At availability, the second export contains −0.00100 USD per EUR. Upload a review, record a decision, restart and inspect the same quote references. Positive MARKET/management controls used controlled qualification with recorded prices. Paper accounting on a separate explicit long order reduced 1,000 EUR by 400 then 600, with modeled zero fees. See [independent strategy, execution and review receipts](../plan-integration-evidence/CLOSURE-STRATEGY-REPLAY-REVIEW.json).

| Source | Window, clock and proof |
|---|---|
| Dukascopy EURUSD BI5 BID/ASK | November 13, 2025 13:00–15:00 UTC. 3,171 and 5,465 observed quotes. Raw SHA-256 `2f47bcb14e0a2c4dcd6d65eb819b85f1b5168b656d1f5f67c3dcd2240569f014` and `9d40aa3147d93e6daa2218933877adb9471dc02ff08b19fc0d272f1eb781688f`. Entries use the named model's zero delivery delay. No broker arrival or fill timestamps were recorded. |
| Dukascopy EURUSD M5 BID | November 13, 2026, 00:00–22:00 UTC, 264 bars. Dataset `ca01e9ac45346d7c9fdfdf4263085831a07735bc16158901c9a14187f77aaf4e`, Parquet `fa2af192aa86dc41bb20df51dffe95c29bc48cdf2fe38b643f699985c6b0e09d`. |
| Dukascopy GBPUSD M5 BID | Same session, 264 bars. Dataset `1cc6acc1b358b3b043438badc69f72413827a8cd5dad379de8e41dd9aaaecfd0`, Parquet `aed73f09d78bb3df29d3678e0ace1f00949a673a920ed22dcd6d23cb7a173d02`. Candle availability at nominal close unless a specific recipe models extra delay. These BID candles supply analytical evidence. |

## Plan reconciliation

Master v1 SHA-256 `0bba853659726d9abd80aa084e861479ad46000e3672d02ad210ea3ae0d46e66` identifies the operator-supplied document outside Git. The [remediation plan](../../research/05_REMEDIATION_PLAN.md) at `4906828`, [roadmap](../../research/08_IMPLEMENTATION_ROADMAP.md) at `e7074a7` and [integration feature matrix](../plan-integration-evidence/FEATURE-MATRIX.md) at the starting head are the other inputs. Evidence revisions below are ancestors of the combined candidate unless identified as worker commits. The earlier final-integration matrix and backlog are historical snapshots. This ledger attributes work already shipped on main separately from the integration.

| Requirement | Local workflow, attribution and evidence | Disposition and outstanding gate |
|---|---|---|
| TF-000, architectural invariants | Existing offline baseline, SQLite and Parquet; integration adds quiescent profile restore, source and package hashes. Main `517f812`, genuine drill `ab4ba42`. | **verified** for reproducible local baseline, mode separation and isolated restore. |
| TF-D01 | Main implements real chart placement. Golden Chromium drawing cases, `3d97bdb`. | **verified** for golden placement tools. |
| TF-D02 | Main persists editable versions, conflict checks and undo for tested shapes, `6f5140d`, controlled charts. | **partially verified**. Broader tool catalog and all input gestures lack individual acceptance. |
| TF-U01, master UI | Main supplies chart UI. PR adds persistent panels, cleanup and 100 StrictMode navigations (`29f2d8f`), 2025 EUR-only recorded replay (`0309736`) and 2026 paired frozen capture (`e2a5a66`). Builder recovery above. | **verified** for local workspace acceptance on those sources and layouts. 2025 dual-symbol and live feeds unverified. |
| TF-I01 | Main multi-instance registry; PR adds six indicators, independent numerical controls `d09b5ce`, recorded CCI and Stochastic replay/restore in the feature matrix. | **partially verified**. Recorded all-prefix checks cover named indicators. Broader seed/convergence and volume-dependent claims require appropriate inputs. |
| TF-S01 | Main strict SMT; PR explains, rewinds and restores one exact-bar paired profile, `c338c3f`, 2026 pair. | **partially verified**. Additional peer sets, inverse correlation, session/sweep combinations and broader matching tolerances lack acceptance. |
| TF-I02 | Main analytical producers. PR adds chart proof and restart for structure/liquidity, zones, H1 CRT and the two-component join, `4e03463`, `6e2eb41`, `0cc58b0`; 2026 pair plus controlled gaps. | **partially verified**. Other confluence/overlay profiles and a broader ICT catalog have no independent equivalent proof. |
| TF-L01 and personalized product flow | PR adds blind assessment, immutable plan revision, outcome reveal, scheduled practice and reopen. `6c704b8`, 2025 paper and 2026 SMT cases. | **partially verified**. Full syllabus, adaptive spaced proficiency and wider setup drills remain. |
| TF-P01 | Main has plan and trade records. PR explicitly links paper order to immutable plan and supports a reconciled local cash-funded EURUSD position, partial close, restart, no-trade, `217cc66`. | **partially verified**. General portfolio margin, shorts, foreign-currency costs, liquidity and a broad broker-style order lifecycle lack evidence. |
| TF-E01 | PR's broker-neutral descriptors, read-only capability and offline risk tests; controlled fixtures, `989b4f8`. | **partially verified**. Real adapter status/acknowledgement and broker-specific operational risk gates lack acceptance. |
| TF-E02 / TF-E03 | No practice or live routing authorized in this integration. Main and PR preserve read-only capability. | **deferred by scope**. Practice needs separate authorization and broker access; live needs independent validation and separate explicit approval. |
| TF-R01 | Main checksum publication and recovery; PR adds local reliability, measured short replay and restored receipts, `ab4ba42`, `404216d`. | **partially verified**. Million-quote/month runs, four charts × 5,000 bars, heavy drawings and long-duration soak remain open. |
| T01 | Main rejects contradictory observed BID extremes. PR checks original BI5 ordinals and derived side-specific OHLC, `ab4ba42`, 2025 BI5. | **partially verified**. Reconstructed bars cannot certify all market ticks were captured. |
| T02 | Main immutable SMT states. PR projects closed as-of chart and saved signal evidence, `c338c3f`, `e2a5a66`, 2026 pair and delayed controls. | **verified** for the admitted archived signal and closed-pane workflow. |
| T03 | Main canonical staged publication, checksums and caller atomicity; PR performs genuine interruption, corruption, 507 and retry/restore, `ab4ba42`. | **verified** for tested local adapters, complete visibility and retry. Disk exhaustion was injected. |
| T04 | Main A/B registration/parity, C publication and E cutover rehearsal. PR preserves references under relocation, `517f812`, `ab4ba42`; fixed and research fixtures plus genuine archive. | **partially verified**. Writable reader default change, stable manifest rollout, quota and compatibility-writer retirement need separate signoff. |
| T05 | Main watch contracts; PR durable frontier, leases, correction and historical alert recovery, `c338c3f`, 2026 pair + controlled omission. | **partially verified**. Live outage catch-up, provider-clock calibration and prolonged disconnects unverified. |
| T06 | Main deterministic multi-partition backtest and DST calendar. PR preserves legacy inspection, adds bounded monthly strategy and weekend recovery, `404216d`, 2025 quotes + controlled closure. | **partially verified**. Calendar beyond supported profile and scale unverified. Full November run **blocked by an external input**: 2 observed hours, 480 missing open hours, 238 scheduled closed. |
| T07 | Main recorded MARKET plus offline synthetic entry/management. PR includes exact #36 LIMIT and adds recorded STOP and checkpointed management. `93691cd`, `f9c2f20`, genuine 2025 quotes plus controlled intents. | **partially verified**. Recorded observations establish modeled prices only. Broker delivery, spread at execution, size, fees and live liquidity lack independent proof. |
| T08 | PR imports a controlled redacted-format statement, handles hedging/netting, FX and closing valuation. `80a5ff9`, controlled fixture. | **blocked by an external input** for broker acceptance. Need authorized redacted rows, independent opening/closing balances, timestamp convention, currency and dated contract/FX/leverage evidence. |
| T09 | Main immutable review/package foundation. PR adds cutoff metric, versioned originals, correction, operator decision and relocation, `616a677`, `d7e73e9` and closure guard. | **verified** for the named historical price-change metric and PRE_TRADE compatibility. Account P&L and monthly expectancy require independent inputs. |
| T10 | Main producers and rule definitions. PR adds terminal replay, stable revision selection and component/coverage drill-down, `0cc58b0`. 2026 pair; 516 reference cutoffs, 45-cutoff disjoint negative control in pinned evidence. | **partially verified**. Named numerical, H1, structure, liquidity, zone, CRT and confluence profiles have local evidence. General combinations and large-chart performance remain open. |
| T11 | Main restore foundations; PR adds offline hourly acquisition inventory, retry/de-dup, two-profile restore and package reopening, `ab4ba42`, genuine short archive. | **partially verified**. Persistent quota/dry-run cost guard, unattended acquisition/backoff, distinct OS account and archive-scale pilot remain. |
| T12 | Proposed analytics and execution-framework pilots in remediation plan. | **deferred by scope**. No acceptance run or upstream validated account equity for portfolio analytics. |

Roadmap gate roll-up: M0 is **partially verified** (observed-price integrity, terminal evidence and crash recovery pass; full source completeness unresolved). M1 is **partially verified** (both reader contracts and restore tested; quota and reader cutover open). M2 is **partially verified** (bounded strategies pass; month archive missing). M3 is **partially verified** (recorded profiles pass; broader numerical/profile coverage open). M4 is **partially verified** (review workflow passes; real accounting blocked). M5 is **partially verified** (isolated application-profile recovery passes; separate OS-user and scale acceptance open). The master release protocol requires an independent merge decision. Public historical admission remains 160 cutoffs, historical intents remain AWAITING_EXECUTION_VALIDATION and production recorded trading remains BLOCKED.

## Rollback

Before merging, leaving main at its current revision is sufficient. After an approved merge, take a quiescent copy of both SQLite databases and archive roots, then revert the integration merge through a reviewed PR. Test the prior application on a disposable copy and leave all versioned rows, ZIPs and partitions intact for inspection. Keep legacy reader defaults and compatibility writes. The three closure repairs have independent commits; reverting the PRE_TRADE guard or the worker fix would restore a demonstrated failure.

## Reproduction and measured cost

Use the pinned files in local storage on Windows. Each verifier makes its own disposable working copy. The production source files were not downloaded or republished. The 2026 vendor CSV SHA-256 values are `bc2e49a78edad37b73ac1bcdc006892f54aab60c3e769ec837998773cb5960f6` (EURUSD) and `a2b4947d5e79198920589263a9626e476012650a09d8ba52386e244ab78c1928` (GBPUSD).

```powershell
git fetch origin main feature/tradeforge-plan-integration '+refs/pull/36/head:refs/remotes/origin/review-36' '+refs/pull/37/head:refs/remotes/origin/review-37'
git merge-base --is-ancestor origin/review-36 origin/review-37
git merge-base --is-ancestor origin/review-36 origin/main
.venv/Scripts/python.exe -m scripts.verify_local_market_workspace --output storage/pr37-closure/market.json
.venv/Scripts/python.exe -m scripts.verify_recorded_backtest --source storage/t06-input
.venv/Scripts/python.exe -m scripts.verify_historical_package --output storage/pr37-closure/review.json
$env:TRADEFORGE_T10_SOURCE_STORAGE=(Resolve-Path storage).Path
.venv/Scripts/python.exe -m pytest -q
npm --prefix frontend test
npm --prefix frontend run build
$env:TRADEFORGE_E2E_API_PORT='8780'
$env:TRADEFORGE_E2E_UI_PORT='4780'
npm --prefix frontend run e2e -- --retries=0
git worktree add --detach ../TradeForge-pr37-closure-main 517f8122b633f9143324f99dd7b075c7c689df3f
.venv/Scripts/python.exe docs/implementation/pr37-closure/compare_candle_reads.py --main ../TradeForge-pr37-closure-main --output storage/pr37-closure/candle-measurement.json
```

| Workload, same Windows host | Main | PR | Interpretation |
|---|---:|---:|---|
| Two saved candle API reads alternating EURUSD/GBPUSD, 24 verified M5 rows per response, 60 timed requests per revision after warmup. Median / p95 ms, initial measurement. | 8.400 / 9.368 | 8.732 / 9.743 at starting head | Slightly slower observed. [Raw samples](candle-reads-start.json) |
| Same comparison during combined verification | 21.000 / 48.789 | 14.502 / 20.745 at combined head | Concurrent suite activity changed the timings materially. **Inconclusive** for speed. All 120 timed responses agreed with provider OHLC. [Raw samples](candle-reads.json) |
| Recorded tick backtest GET, three alternating fresh processes per revision after warmup, 8,636 ticks, 17 modeled trades | 2.263 s / 147.75 MiB | 2.159 s / 150.98 MiB on worker candidate | Same complete response hash in all six runs. Median time −4.6%, median peak working set +2.2%; small sample, uncontrolled OS cache. [Raw samples](tick-runtime.json) |
| Monthly bounded runner, 43 requests on the two available hours | — | Cold 1.935 s / 128.16 MiB; warm 1.269 s / 129.38 MiB | 12 complete-hour reads, 842,604 logical bytes, identical cold/warm/restarted ledger. No month-scale performance estimate. |

The three reviewers ran concurrently in separate sibling worktrees. Full backend, frontend unit/build and the standard Chromium suite ran once on the combined candidate; the exact outcomes, timing, code churn and sampled runtime token counters are recorded with the final PR decision. Playwright was invoked with zero retries. The owner did not use trial-and-error sampling of the market windows or rerun the full suites for the subsequent Markdown/JSON evidence change.
