# Status ledger

| Task | State | Evidence |
|---|---|---|
| TF-000 (TF-D01 preflight) | DONE for isolated drawing fixture | Fetched main, recorded baseline results, backed up both SQLite stores online, copied and verified Parquet, and rehearsed isolated restore. Private live-browser preferences were not exported. |
| TF-D01 | DONE locally, pending review | Actual pointer placement, preview, cancellation and four golden shapes using existing saved scene format. See the [acceptance notes](TF-D01-ACCEPTANCE.md). |
| TF-D01 review feedback | DONE locally, pending re-review | Six more browser tests and local validation for invalid risk/reward edits. [Evidence](TF-D01-REVIEW-FOLLOWUP.md). |
| TF-D01 final review findings | DONE locally, pending re-review | Exclusive chart modes and single-anchor drag placement. [Verification](TF-D01-FINAL-REVIEW.md). |
| Adjacent tasks | Outside this PR | Full freehand selection and drag/resize lifecycle are deferred to TF-D02. |

Hosted CI for this change is attached to the open PR.
