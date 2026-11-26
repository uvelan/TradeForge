# T08 closing valuation and V1 receipt compatibility

Starting revision `fdf01b1076a6b925055e4f1ae99ff237c9491e34`. The Windows-local API tests use the committed controlled USD netting statement. The historical probe loads the actual starting revision from Git into a temporary directory and submits each variant to its FastAPI registration and advance endpoints. The data file, broker totals, fills and inventory are identical apart from the specified timing or contract change.

## Expected and observed

Closing cutoff is 2025-11-15 00:00:00 UTC. The closing EURUSD ASK was observed on November 14 at 23:59:59. The open short is 0.01 lot with a 100,000-unit contract. At ASK 1.09800 and leverage 100, margin is USD 10.98. Broker balances are USD 1,079.00 cash and USD 1,080.00 equity.

| Input | Observed V1 verdict / terms / USD margin | Observed V2 verdict / terms / USD margin | Residuals and issues |
|---|---|---|---|
| Original statement | Reconciled / spec1 / 10.98 | Reconciled / spec1 / 10.98 | Both versions have zero cash, equity and margin differences. |
| Mark available exactly at cutoff | Reconciled / spec1 / 10.98 | Reconciled / spec1 / 10.98 | All zero. |
| Delivery one microsecond after cutoff | Unresolved / none / unknown | Unresolved / none / unknown | Cash difference zero. Stale or unavailable mark, missing equity and margin evidence. |
| Delivery 100 ns after cutoff | Reconciled / spec1 / 10.98 | HTTP 422 on new import | V1 had three zero residuals after truncation. V2 creates zero receipts. Historical assessment unavailable with exact original input shown. |
| spec1 ends at exclusive cutoff | Unresolved / none / unknown | Reconciled / spec1 / 10.98 | V1 reported missing terms and unknown equity/margin residuals, cash zero. V2 all zero. |
| 50:1 successor begins at cutoff | Unresolved / next / 21.96 | Reconciled / spec1 / 10.98 | V1 margin difference +10.98, cash/equity zero. V2 all zero. |
| Gap from mark through exclusive end, 100:1 successor at end | Reconciled / next / 10.98 | Unresolved / none / unknown | V1 all zero. V2 cash zero, unknown equity and margin differences. Position reports MISSING_OR_OVERLAPPING_DATED_SPEC. Closing totals report MISSING_EQUITY_EVIDENCE and MISSING_MARGIN_EVIDENCE. |
| 50:1 terms effective exactly at observed mark | Unresolved / next / 21.96 | Unresolved / next / 21.96 | Both cash and equity differences zero, margin difference +10.98. |
| 50:1 change between observation and delivery | V1 uses period-end lookup | Reconciled / spec1 / 10.98 | V2 all zero. Delivery at cutoff does not change the terms used for the observed price. |

V2 accepts explicit ISO date/time with seconds and a minute-resolution UTC offset or Z. Fractions must be exactly representable in microseconds. Trailing zeroes beyond six places preserve the exact instant. The same validator covers every timestamp, including FX observation and availability, both spec boundaries, and deal times. Rejection precedes the SQLite transaction.

Mark availability and age are checked at the reporting cutoff. Contract size, settlement currency and leverage are selected at the observed mark, within the half-open reporting period's upper boundary. An observation exactly at the closing instant uses the final representable instant of the closing period for terms. The receipt records the cutoff, mark and availability, terms lookup time, selected spec ID, validity interval, contract size and leverage. Conversion, when required for closing valuation, still requires eligible FX at the closing cutoff.

## Reopening a historical receipt

Data Library → Broker statement reconciliation → Reporting period and revision shows the current V2 assessment first. **Inspect original V1 verdict** exposes the complete earlier report. On the gap case the page shows **UNRESOLVED** and identifies EURUSD's missing dated specification. For the formerly accepted 100 ns delivery, the page shows **Current assessment unavailable**, the field, original timestamp and precision error. An operator can submit a corrected source as a new revision.

The original file fingerprint remains the receipt ID. The current assessment ID is SHA-256 of the ASCII file fingerprint, a colon, and `STATEMENT_CLOSING_VALUATION_V2`. New reports persist the policy. Completed V1 reports are reassessed on read from the immutable source, bounded by the statement's existing import limits. No schema migration or backfill is required. The old source, report and progress payloads remained byte-identical for all eight receipts after reopen, two application lifespans, repeat requests and a separate Python process. A new submission of the unrepresentable source still returns 422. Its original historical receipt remains readable by ID.

## Local verification

From the repository root in PowerShell:

```powershell
.venv/Scripts/python.exe research_backend/tests/statement_v1_probe.py
.venv/Scripts/python.exe -m pytest -q research_backend/tests/test_statement_closing.py research_backend/tests/test_statement_reconciliation.py
$env:TRADEFORGE_E2E_API_PORT='8553'
$env:TRADEFORGE_E2E_UI_PORT='4553'
npm --prefix frontend run e2e -- --config playwright.statement-upgrade.config.ts --retries=0
npm --prefix frontend run build
.venv/Scripts/python.exe -m pytest -q
```

Focused backend: 27 passed in 14.92 seconds. Chromium against the historically seeded database: one passed in 9.8 seconds, zero retries. Production frontend build passed. The full backend suite ran once on the final code: 1,113 passed, one skipped, one Starlette deprecation warning in 533.81 seconds.

Rollback by reverting this correction and restarting the local application. Preserve a copy of the database and all immutable source bytes. The previous application exposes historical V1 verdicts without the correction warning. Suspend closing-valuation use under that version until the correction is restored.

Genuine broker-statement acceptance still requires an authorized export, independent totals and dated specifications. The November EURUSD inventory still lacks 480 open hours. Public admission remains 160 cutoffs and production recorded trading remains BLOCKED.
