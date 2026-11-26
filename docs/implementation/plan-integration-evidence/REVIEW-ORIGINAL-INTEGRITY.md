# T09 · Review upload integrity and historical corrections

Requested base `9db563956dd6696bd6990a489566dbfcc790ca06`. Actual starting revision and previous writer `ab4ba424a7d1ee0ca619bbcdf8e11bf5c85def81` includes the contributor's review ZIP relocation correction. Tests used the public Windows-local API and the checksum-pinned Dukascopy November 13, 2025 EURUSD archive. The tested package contains the recorded BID-rise price-change metric of −0.00100 USD per EUR.

## Public API results

“Wrong first” means the disagreeing value appeared first and the valid value appeared last. The old writer parsed the last occurrence. The legacy matrix ran sequentially; its three successful responses shared a single canonical review. The fresh corrected workspace started empty. Row counts were read through separate SQLite connections after each request.

| Duplicate member | Previous HTTP wrong first / wrong last | Corrected HTTP wrong first / wrong last | Corrected stored reviews / originals |
|---|---|---|---|
| Claim value | 201 / 422 | 422 / 422 | 0 / 0 |
| Claim unit | 201 / 422 | 422 / 422 | 0 / 0 |
| Top-level package ID | 201 / 404 | 422 / 422 | 0 / 0 |

The shared upload route bounds the streamed body at one million bytes and checks every JSON object for repeated decoded member names before invoking FastAPI's typed schema handler. Tests include nested arrays, escaped key spelling, repeated identical values, and duplicates inside a PRE_TRADE finding. Newly accepted uploads retain the submitted bytes, including whitespace and trailing line endings.

A clean upload following the six rejections produced **one review and one original**. Its download matched the submission byte for byte, SHA-256 `580874e6fafd6d5d5311f11a3cd0a2d3c6dfed71d550aa1e5f55767093dbb016`. Two concurrent whitespace variants of a distinct valid assessment added **one** review and original, for a total of two of each. The [run receipts](review-integrity.json) record both input hashes and the winning original hash.

## Opening and correcting an older original

Startup classifies previously unaudited original bytes in committed batches of at most 16. Classifications and correction links are additive immutable records. Historical review rows, original files, canonical IDs and decision histories retain their existing identities. The recorded review pane suppresses parsed claims for an ambiguous original and offers its raw download. The AI Review Center also labels affected reviews.

Select the affected review in Strategy Lab or AI Review Center and choose **Prepare linked correction**. Check the package and enter the intended assessment. The template contains `corrects_review_id`. Importing it validates the clean bytes and claims and records a distinct review in the same package. Repeating it returns the correction ID. Resubmitting the old normalized assessment without the correction link returns 409 when it would reuse the ambiguous original. A clean prior review continues to deduplicate normally.

| Old-writer upgrade and restart | ID | Original SHA-256 |
|---|---|---|
| Ambiguous original retained | REVIEW-F2416D18831F | `1133942504d4f05810ce64726b2be574e03fdfa5df768582382d7efdd7b8c0e8` |
| Clean original retained | REVIEW-3778074A1EC3 | `222e3ca466433b9490056dec840381c66ae9ce7ae64ce0d0b8cd9e1c841ff9cd` |
| Linked clean correction | REVIEW-C4F00DB50BC1 | `eb09576765882f1f2a99c9855fe4b91c6fae386e59ca78c902cbb2fc806be3e7` |

The previous writer persisted both originals and decisions in one database. Chromium reopened it with the upgraded application, inspected the warning, attached the correction and reopened the clean review. A separate API process then compared both complete original decision histories and byte hashes to the previous writer's receipts. Correction and clean-review retries left exactly **three reviews and three originals**. Review and package IDs are newly allocated on an independent execution of this fixture.

## Reproduction

From the repository root in Windows PowerShell:

```powershell
.venv/Scripts/python.exe -m scripts.verify_review_integrity --baseline
.venv/Scripts/python.exe -m pytest -q research_backend/tests/test_review_integrity.py research_backend/tests/test_historical_packages.py research_backend/tests/test_review_package_relocation.py research_backend/tests/test_ai_review_decisions.py
.venv/Scripts/python.exe -m scripts.verify_review_integrity --output docs/implementation/plan-integration-evidence/review-integrity.json
npm --prefix frontend test
npm --prefix frontend run build
.venv/Scripts/python.exe -m pytest -q
```

The runner extracts the previous writer from the local Git object database and creates disposable workspaces. It includes the Chromium journey with zero retries. Package quotes come from the original files under `storage/t06-input`. Focused API checks passed (9); frontend unit tests passed (13). The initial production build found a TypeScript callback return mismatch in the recorded-package selector. The corrected production build passed. A preliminary backend run had one failure (1,120 passed, one skipped): the published PRE_TRADE schema lacked the new optional correction field. Regenerating the schema restored its exact match with the model, and the targeted schema round-trip passed.

A subsequent run passed all 1,121 assertions but hit a teardown error. The statement-reconstruction test temporarily exposed an empty SQLite file to the running historical worker before initializing its schema. The test passed in isolation (5.31 seconds). A local fixture correction passed the full suite with **1,121 passed, one skipped, one Starlette deprecation warning in 526.67 seconds**. While that suite ran, contributor commit `9a84bc5` independently fixed the race by moving the fresh replay into its own process. Integration preserves that contributor version in full.

## Rollback

Back up the database and review ZIP directory together. Revert the review-integrity correction and restart. Keep the additive audit table, original bytes, corrected reviews and existing decisions intact. The old writer can again accept duplicate members and lacks the ambiguity warning; suspend review imports under that version. Restoring the backup rolls the workspace back to its backup time.

T08 genuine statement acceptance and 480 missing November open hours remain outstanding. Public historical admission remains 160 cutoffs. Production recorded trading remains BLOCKED.
