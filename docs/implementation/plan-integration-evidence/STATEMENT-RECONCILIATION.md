# T08 · Local broker statement reconstruction

**Implementation verified on a controlled fixture, broker-statement acceptance pending.** Starting revision `404216d424622778cd9985f425d9149cfeca4318`, PR #37. The local research database contained zero imported broker fills. The available market-data files supply quotes and candles and do not establish broker cash, account mode, commissions or opening inventory.

The [closing-valuation correction and historical receipt checks](STATEMENT-CLOSING-VALUATION.md) document current timestamp precision, specification selection and reopening V1 results.

## Operator workflow

1. In separate Windows PowerShell windows run `.\start-api.ps1` and `.\start-terminal.ps1`. Open the local terminal, select **Data Library → Broker statement reconciliation**, and download the controlled JSON example to inspect the input format.
2. Select a redacted UTF-8 file in that format. Use a stable local book alias and stable pseudonyms for deal, posting, order and position IDs. Keep the broker export and any conversion into the supported interchange local. Native broker PDF, HTML and arbitrary CSV layouts require a field mapping. No broker connection is used. Source files are opened read-only. The local SQLite database retains submitted redacted bytes.
3. Choose **Reconcile next batch** to process up to 100 rows. Reopen the period and revision after a restart to continue at its committed row count. The final status requires a completed calculation. Inspect the balance difference, issues, individual rows, and the positions and equity calculation. Each row shows original units and currency, the inventory change, applied FX, and cash before and after.
4. Submit an amended file for the same alias and UTC period. Open **Statement fingerprint, source evidence and revision changes** to inspect changes by field or stable row ID. Previous versions remain individually selectable. Reports describe a single version of one reporting period. Overlapping periods must not be summed.

## Minimum evidence and accounting policy

The fingerprint is SHA-256 of the complete file bytes. A version belongs to the book alias and normalized start/end. Reformatting creates a distinct source version. Exact-byte redelivery returns the existing receipt. A revision contains its own reconstruction, not additional account cash flows. The existing MT5 recent-deals import retains its original ticket-based semantics. Paper, journal, export and historical execution tables have no foreign keys into this importer and receive no reconciliation writes.

- Supply the reporting interval, timestamp offsets, complete-history assertion, account currency, mode, opening/closing balances, closing equity, and both boundary inventories. Explicit empty arrays establish flat inventory. Null means unknown. Sort by UTC time and sequence, with a unique priority for each source row. The interval includes its start and excludes its end.
- Provide source deal and cash-component IDs, side, filled lots, price, position ticket and position effect for hedging, and original fees, commission, swap, deposit and withdrawal amounts. Signed postings include rebates. Reported deal profit is gross and is checked separately from charges. When available include broker running balances. An unexplained closing balance discrepancy points to the reported closing balance. Intermediate reported balances can localize the first disagreement; absent those, the importer cannot attribute a discrepancy to a particular deal.
- Provide dated linear contract size, tick size, lot step and settlement currency. Costs use weighted average per net symbol or per hedge ticket. In a net reversal, realize the old position and open the remainder at the reversal execution price. Each posting is converted and rounded half-even once to account minor units. Quantities, weighted averages and FX operations use exact rational arithmetic. Other cost bases or instruments with nonlinear profit calculations need a separately verified policy.
- Pin each required FX rate by ID, pair, price, source, observation and availability timestamps. Direct pairs multiply, inverse pairs divide. The rate must have been available by the accounting event and within the declared age. Missing FX leaves the native amount inspectable and the affected account total unknown. Supply causal BID/ASK marks for ending inventory. Mark longs at BID and shorts at ASK. Optional gross-notional margin requires dated leverage and broker closing margin. Margin offsets, tiering and broker-specific close-out mechanisms require additional evidence. Unavailable margin is labeled separately.
- Daily/weekly loss budgets use UTC and realized trading plus charges, excluding external flows and open P&L. Return usage only for a reconciled period covering the beginning of the relevant day/week. Period coverage and the redacted export's accuracy remain broker acceptance dependencies.

## Controlled receipt

The controlled November 10–15, 2025 period is USD, netting, with synthetic EURUSD fills. Local input SHA-256 `f705ec77025a6c3ea82b6dc4fa35bb113d22ecc8e7fe19cedf908c9592b15e19`. The file fingerprint is byte-sensitive, including line endings.

| USD component | Independently expected | Observed |
|---|---:|---:|
| Opening balance | 1000.00 | 1000.00 |
| Deposit and withdrawal | +100.00 − 20.00 | +80.00 |
| Gross realized, F3 and F4 | +3.00 − 2.00 | +1.00 |
| Commission, swap, fee | −1.25 − 0.25 − 0.50 | −2.00 |
| Calculated / reported cash | 1079.00 / 1079.00 | 1079.00 / 1079.00 |
| Open short marked at 1.09800 ASK | +1.00 | +1.00 |
| Calculated / reported equity | 1080.00 / 1080.00 | 1080.00 / 1080.00 |
| Gross margin at 100:1 | 10.98 | 10.98 |

**Source trace.** F1 and F2 bought 0.01 lot each at 1.10000 and 1.10200. F3 sold 0.01 at 1.10400. F4 sold 0.02 at 1.09900, closing 0.01 against average 1.10100 and opening 0.01 short. Its realized USD was (1.09900 − 1.10100) × 0.01 × 100000 = −2.00. Cash moved from 1101.75 to 1099.75. This single deal produces one trace and its opening portion contributes zero additional realized cash.

**Recovery and disagreements.** After five committed rows, replaying the previous batch returned the same frontier. A fresh Python process resumed the same SQLite database. Its report and every trace matched a separately initialized uninterrupted database. Reimport and another advance left one version, nine traces and one report. Reducing the C1 commission by one cent and correcting the two closing totals created v2 with three changes and preserved v1. Changing only reported cash to 1079.01 produced an unresolved −0.01 residual. Tests also cover hedged tickets, USDJPY conversion in both directions, stale/future/missing FX, missing opening inventory, unavailable marks, volume grid, duplicate IDs, currency rounding and interim balance contradictions.

## Verification and rollback

From the repository root:

```powershell
.venv/Scripts/python.exe -m pytest -q research_backend/tests/test_statement_reconciliation.py
.venv/Scripts/python.exe -m pytest -q
npm --prefix frontend test
npm --prefix frontend run build
$env:TRADEFORGE_E2E_API_PORT='8553'
$env:TRADEFORGE_E2E_UI_PORT='4553'
npm --prefix frontend run e2e -- statement-reconciliation.spec.ts --retries=0
npm --prefix frontend run e2e -- --retries=0
```

Focused API tests: 16 passed in 8.10 seconds. Chromium: one passed in 6.9 seconds, zero retries. Full backend suite: 1,102 passed, one skipped, one Starlette deprecation warning in 594.91 seconds. Full Chromium suite: 86 passed in 6.4 minutes with zero retries. Frontend unit tests: 13 passed. TypeScript and production build passed. Browser coverage includes importing through the Data Library, inspecting the reversal, reloading the selected revision, and investigating the one-cent disagreement.

Rollback by reverting the T08 commit and restarting the local application. Keep the four additive SQLite tables and existing sources intact. The prior application does not read these tables. For genuine acceptance, supply an authorized redacted export with the fields above and matching dated broker instrument/account specifications. Broker access, production recorded trading, and public replay admission were not enabled or expanded.
