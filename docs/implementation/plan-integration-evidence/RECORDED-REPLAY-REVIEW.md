# T09 · Reviewing a recorded replay price change

Base `80a5ff90eb0a731c77e95b7f8c98e2f5ddef0997`, PR #37. The acceptance machine used local Windows processes, an isolated research database, and two checksum-pinned Dukascopy EURUSD BI5 hours on November 13, 2025. A second application process reopened the same files and database.

## Operator steps

1. Start `.\start-api.ps1` and `.\start-terminal.ps1` in separate PowerShell windows. In **Strategy Lab → Monthly EURUSD replay**, inspect November 2025. Select the pinned session. Run or resume it if the desired observation has not yet been processed.
2. Under **Recorded replay evidence**, enter a UTC timestamp with milliseconds and export. The ZIP contains a manifest, component hashes, the frozen replay evidence, and a response template. Missing exits, incomplete replay progress and unavailable metrics have explicit reasons.
3. Inspect the template and record an independent assessment locally. Paste JSON or upload the original JSON file to retain its bytes. The importer validates structured metric references, decimal values and units. Free text remains subject to operator judgment.
4. Select the imported review and record an operator decision and rationale. Follow **Trace claim** to the metric. **Verify ENTRY quote** and **Verify STOP quote** read the pinned source again and compare the timestamp, ordinal, source hash, partition ID and hash, and both prices.
5. After restart, inspect the same month and session, choose the saved evidence package, and reopen the imported review. The AI Review Center also links a historical review to its frozen evidence and original JSON. PRE_TRADE packages retain their existing plan export, download and review paths.

## Recorded control

The accepted strategy and execution policy are versioned together as `EURUSD_FIRST_BID_RISE_10_POINTS_PER_UTC_DAY_V2`. Quote timestamps are recorded observations. Availability assumes zero delivery delay. Entry and exit are **simulated**, with observed spread, zero modeled fees and zero assumed slippage. There is no execution confirmation from a broker.

| Selected UTC cutoff | Package in this run | Eligible events | Completed price change |
|---|---|---|---|
| 13:31:38.102 | PKG-A4081CD66B8E | Qualification and ASK entry | Unavailable, no verified exit by cutoff |
| 13:31:38.103 | PKG-724FA1AC115F | Qualification, ASK entry and BID protective exit | −0.00100 USD per EUR |

The independent verifier hashes and decompresses the original BI5 file, decodes the provider's big-endian millisecond, ASK and BID fields, finds the first ten-point BID rise from the opening anchor, takes the first later ASK and finds the first closing BID that crosses the stop or target. It does not take these indices or values from the exported package.

| Event | UTC | Source ordinal | Executable side / value |
|---|---|---:|---|
| Qualification | 13:00:00.666 | 5 | BID 1.16239 |
| Simulated entry | 13:00:00.771 | 6 | ASK 1.16243 |
| Simulated stop | 13:31:38.103 | 1564 | BID 1.16143 |

At scale 100,000, (116143 − 116243) / 100000 = **−0.00100 USD per EUR**. The evidence interval runs from the entry observation to the exit observation. The exit is the first availability of the completed metric. Broker fees, account P&L and month-level expectancy remain unavailable.

The [two actual manifests and their evidence](recorded-review.json) include component SHA-256s, source and dataset pins, full metric ID, session, original event IDs and review hash. Package/review IDs are assigned on each independent export/import. Reopening an existing artifact preserves them. The 13:00 BI5 source SHA-256 is `2f47bcb14e0a2c4dcd6d65eb819b85f1b5168b656d1f5f67c3dcd2240569f014`, and its tick partition SHA-256 is `ca71608096bb6ec870c2a621d761910f0d9a45d7d9ce7fe9e99569ef983e6276`. The 14:00 control source is `9d40aa3147d93e6daa2218933877adb9471dc02ff08b19fc0d272f1eb781688f`.

## A19, compatibility and restart

- A controlled concurrency fixture pins a SQLite WAL snapshot, pauses the export, commits a qualifying exit on a different connection, then finishes the export. It returns the entry and an incomplete-frontier reason. A subsequent export at the same market cutoff contains the exit. Future checkpoint state and complete-batch counts are excluded from the package.
- Two reviews retained the valid metric ID but separately altered the value and the unit. Both returned 422 with zero stored reviews. Replacing an underlying Parquet file produced 409 on re-export, preserved the frozen package and created zero additional packages.
- Two concurrent, differently formatted valid uploads against the genuine package produced one new logical revision. Both input hashes and the first retained hash appear in the evidence file. This preserves the original importer policy of one byte-exact body per semantic revision. An earlier assessment remains a separate review.
- A new API process and Chromium session reopened review `REVIEW-076B0B0E3713`, checked its original bytes, matched both source quotes, and found the operator decision at sequence 1. Repeating the same decision request returned sequence 1 again.
- An existing seven-column PRE_TRADE package parent with real package, review, original and decision rows was upgraded twice. Its ZIP and review bytes were identical, decision history survived, duplicate import returned the same ID, and SQLite reported no foreign-key violations. New historical packages bind to replay sessions without creating a trade or plan.

## Commands

From the repository root:

```powershell
.venv/Scripts/python.exe -m pytest -q research_backend/tests/test_historical_packages.py research_backend/tests/test_ai_review_decisions.py
.venv/Scripts/python.exe -m scripts.verify_historical_package --output storage/historical-package-evidence.json
.venv/Scripts/python.exe -m pytest -q
npm --prefix frontend test
npm --prefix frontend run build
$env:TRADEFORGE_E2E_API_PORT='8553'
$env:TRADEFORGE_E2E_UI_PORT='4553'
npm --prefix frontend run e2e -- --retries=0
```

The genuine verifier starts two separate app/browser sessions and uses a third process for the concurrent revision check. It requires the original local files in `storage/t06-input` and performs no provider downloads. For normal use, register the files in the operator's workspace with `.venv/Scripts/python.exe -m scripts.month_archive 2025-11`.

## Final local results

The full backend run finished with **1,115 passed, one skipped** and one Starlette deprecation warning in 641.13 seconds. The focused package and operator-decision checks passed (3 tests). Frontend unit tests passed (13), TypeScript and the production build passed, and the standard Chromium suite passed all 86 tests in 7.1 minutes with zero retries. The separate genuine-data creation and process-restart journeys each passed with zero retries. The independent BI5 oracle and concurrent whitespace-variant upload check passed on that workspace.

## Rollback and scope

Back up the research database and export directory together. Revert the T09 commit and restart the local application. Keep existing ZIPs, package rows, originals, operator decisions and the additive association table. The older app can use its PRE_TRADE records; use the updated app again to inspect historical packages. Restoring a pre-upgrade backup rolls the entire workspace back to that backup's time.

This milestone covers the recorded EURUSD BID-rise price-change metric. November still lacks 480 open hours. T08 still requires an authorized redacted broker statement and dated specifications. Public historical admission remains 160 cutoffs and production recorded trading remains BLOCKED.

## Review selection when an original cannot be verified

On baseline `566dc22d48f0b810dc3240413f0fa665098cdf34`, Chromium showed the previous package's review comment and claim while waiting for a different package's original and after that request returned a checksum conflict. The review pane now clears review content before selection and shows decisions and the original link only after loading the selected review for the active package. A checksum error leaves no reviewed claims visible; select that review again to retry. A newly imported review opens directly rather than fetching an unrelated review first.

```sh
npm --prefix frontend run e2e -- recorded-review-identity --retries=0
```

The browser regression uses synthetic responses in the ordinary Strategy Lab UI. It failed before the fix and passed after it on Linux Chromium, confirming that the old comment, claim, link and controls are absent after a 409, and that retry opens only the selected package's claims and decisions. Independent component review found no blocking defect. No review bytes, metrics, trading logic, or API contracts changed. Reverting the pane change needs no migration, but restores the previous behavior on a failed original read.
