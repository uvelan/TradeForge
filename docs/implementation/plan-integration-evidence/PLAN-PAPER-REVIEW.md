# Windows-local plan, paper trade and journal review

Base `c338c3fe0fb61dfb6b1062db650483306f1fc9ab`, integration PR #37. [Acceptance receipts](PLAN-PAPER-RECEIPTS.json) contain all three orders, fill IDs, quote ordinals, archive pins, the original plan and image digests, restart counts and the journal review reference.

## Operator steps

1. Register the two reviewed November 13, 2025 EURUSD BI5 hours in Data Library. In Market Terminal select the saved Dukascopy M5 BID chart and freeze replay at 13:05 UTC. Save chart evidence and choose **Create trade plan**. Enter the hypothesis, entry conditions, invalidation, management, levels and USD risk budget. Save the plan.
2. In Trade Planner choose **Versions** for that decision. At the intended revision, inspect the planned levels and **Paper trade review**. Enter a replay decision time and whole EUR quantity. **Submit paper buy** explicitly attests the discretionary trigger. Saving a plan, opening a signal, and reopening an existing order perform zero economic actions.
3. Stop and restart the app using the same local profile. Open the same plan revision. Advance the decision time and **Reduce paper position**. Restart again. **Close remaining paper position** consumes the exact outstanding balance. If the plan has been revised, select the original revision to manage its position.
4. Choose **Record plan lesson** on the submitted revision. Enter the operator review and append it to the Learning Journal. Expand **Planned versus paper execution** under the entry to compare the written levels with the entry, reductions, close, costs and balances. **Open linked plan** reopens the exact revision. A passed setup can instead use **No trade** in Trade Planner and a reason.
5. For the separate 2026 example select the confirmed transition in Signal Explorer, choose **Create plan draft**, then save. After restarting, **Versions → Inspect original SMT evidence** reopens that revision and both source partitions. Its candle archive supplies analytical evidence only.

## Model and accounting

`PLAN_EURUSD_CASH_MARKET_V1` uses one independent USD practice balance per plan, initialized from its frozen equity. The current paper ledger supports a cash-funded long EURUSD position. No short margin, portfolio netting, lot inference or FX conversion is modeled. The operator supplies whole EUR. Prices and cash use integer USD units of 0.00001. Commission, extra slippage and delivery delay are assumed zero. Spread is observed. The receipt records the operator-selected time in request.decision_ms. The existing ledger applies the chosen quote at its delivery time, which is the ledger processing timestamp. Stop and target are review benchmarks for discretionary management; the operator submits each action. There is no order book, queue, size, or broker execution attestation.

The server verifies the frozen chart, BI5 candle import identity and plan/signal chronology, then reads through the existing recorded-session path. Each fill uses the first executable observation strictly after the action time. Within a timestamp the verified source ordering applies. Coverage must bracket the action from the original cutoff or prior fill. An open-session quote gap over the existing 60-second tolerance blocks publication. The server checks cash and risk against the actual ASK and original stop. A reduction must leave positive EUR; a close must consume the exact remainder.

The existing `PaperLedger.apply` determines the cash and inventory changes. A paper-specific immutable event table in the research SQLite database binds plan revision, command, fill, and quote references in a single writer transaction. This permits atomic exclusion between a no-trade decision and a first paper submission. The previous standalone paper repository remains readable. Each sequence can commit once; an identical retry returns the original receipt, and a changed request at that sequence is rejected.

## Independently reconciled recorded-price journey

The plan and quantities below are operator-controlled. The quotes are genuine Dukascopy observations. The original pinned file was separately LZMA-decoded with big-endian BI5 records before reading API results. All times below are UTC on November 13, 2025. The plan used entry 1.16210, stop 1.16110, target 1.16410, USD 10,000 opening cash and a 0.5% risk budget.

| Action and decision | First executable quote, expected = observed | Cash delta USD | Cash USD | Open EUR | Cumulative realized USD |
|---|---|---:|---:|---:|---:|
| Buy 1,000 at 13:05:00 | 13:05:02.028 · ordinal 506 · ASK 1.16211 | −1,162.11000 | 8,837.89000 | 1,000 | 0.00000 |
| Sell 400 at 13:06:00 | 13:06:00.038 · ordinal 558 · BID 1.16199 | +464.79600 | 9,302.68600 | 600 | −0.04800 |
| Sell 600 at 13:07:00 | 13:07:05.141 · ordinal 595 · BID 1.16194 | +697.16400 | 9,999.85000 | 0 | −0.15000 |

The entry was one price point above the planned level. Neither exit represents an automatic stop or target hit. The realized result is 400 × (1.16199 − 1.16211) + 600 × (1.16194 − 1.16211). Modeled fees total USD 0.00000.

The feed is `dukascopy-bi5-v2`. Original 13h and 14h BI5 SHA-256 values are `2f47bcb14e0a2c4dcd6d65eb819b85f1b5168b656d1f5f67c3dcd2240569f014` and `9d40aa3147d93e6daa2218933877adb9471dc02ff08b19fc0d272f1eb781688f`. All three fills used the 13h tick partition `713b347197c528d5d92242630e3369924152e83e7e9728079ef22a39c32170e2`, SHA-256 `ca71608096bb6ec870c2a621d761910f0d9a45d7d9ce7fe9e99569ef983e6276`. The frozen chart selected both hourly M5 partitions; only the 13:00 candle was visible by 13:05. Their IDs, byte hashes and the hash of the plotted OHLC are recorded in the receipt. The separate November 13, 2026 SMT confirmation became knowable at 13:25 UTC under nominal candle-close availability. Its EURUSD and GBPUSD candle SHA-256 values are `fa2af192aa86dc41bb20df51dffe95c29bc48cdf2fe38b643f699985c6b0e09d` and `aed73f09d78bb3df29d3678e0ace1f00949a673a920ed22dcd6d23cb7a173d02`.

## Recovery and negative controls

The browser journey launches a fresh local API and Chromium instance for each phase. On reopening after entry and after reduction, the complete HTTP receipt list is compared with its pre-shutdown bytes after JSON decoding. Separate read-only SQLite checks compare the actual frozen plan payload and PNG hashes after every phase. Plan v2 changes the thesis after the partial fill; the final close and journal remain bound to v1. Each successful action is posted twice, and the ledger contains three economic events at the end. The no-trade reason is preserved with zero orders. The separate 2026 SMT plan also has zero orders after a rejected attempt to use the 2025 action.

Controlled regressions cover concurrent duplicates, conflicting redelivery, wrong plan revision or sequence, excessive or zero-residual reductions, insufficient risk capacity, a decision before the original evidence, and a quote gap before the proposed close.

## Local verification

The final operator drill passed all five process launches with zero Playwright retries. Entry, partial, close/review, signal plan, and signal-plan reopen took 13.50, 13.94, 16.36, 11.93 and 15.45 seconds respectively, including startup. Concurrent duplicate, chronology, SQLite, price-side and snapshot tests passed (15 focused checks). The full Windows backend suite ran once on the final application code: 1,065 passed in 649.47 seconds, one POSIX FIFO fixture skipped and one TestClient deprecation warning. All 13 frontend unit tests and the TypeScript/Vite production build passed. Standard Chromium passed 85/85 in 8.1 minutes. The two recorded EURUSD regressions, including 100 navigation cycles, passed in 2.1 minutes. Both 2026 paired-chart capture and post-restart reopen checks passed. Browser regressions ran with zero retries.

During development an API regression used the wrong revision-request envelope and correctly received 422. The first browser revision-switch test interacted with the previous version selector while the new response was pending. The planner now clears the stale version view on selection; the regression waits for v2 before inspecting v1. A later browser assertion matched three inspector controls and was narrowed to the Evidence Inspector region. These failures occurred before the final passing acceptance drill.

## Reproduction and rollback

From the Windows checkout with the local archives present:

```powershell
.venv/Scripts/python.exe -m scripts.verify_plan_paper --output storage/paper-accept.json
.venv/Scripts/python.exe -m pytest -q research_backend/tests/test_plan_paper.py tests/test_paper_ledger.py research_backend/tests/test_chart_snapshot_lanes.py
.venv/Scripts/python.exe -m pytest -q
npm --prefix frontend test
npm --prefix frontend run build
$env:TRADEFORGE_E2E_API_PORT='8553'
$env:TRADEFORGE_E2E_UI_PORT='4553'
npm --prefix frontend run e2e -- --retries=0
$env:TRADEFORGE_E2E_API_PORT='8559'
$env:TRADEFORGE_E2E_UI_PORT='4559'
npm --prefix frontend run e2e -- --config playwright.recorded.config.ts --retries=0
.venv/Scripts/python.exe -m scripts.verify_paired_snapshot_restart
```

Stop the local app, back up the workspace, and revert the milestone commit. Keep the SQLite table, archived source files, snapshots and journal rows for audit. Restore the candidate code to inspect its paper receipts again. Previous plan versions, entry simulators and fixed public historical limit of 160 cutoffs are preserved. Production recorded trading remains BLOCKED.
