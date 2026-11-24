# Status ledger

| Task | State | Evidence |
|---|---|---|
| TF-000 (TF-D01 preflight) | DONE for isolated drawing fixture | Fetched main, recorded baseline results, backed up both SQLite stores online, copied and verified Parquet, and rehearsed isolated restore. Private live-browser preferences were not exported. |
| TF-D01 | DONE locally, pending review | Actual pointer placement, preview, cancellation and four golden shapes using existing saved scene format. See the [acceptance notes](TF-D01-ACCEPTANCE.md). |
| TF-D01 review feedback | DONE locally, pending re-review | Six more browser tests and local validation for invalid risk/reward edits. [Evidence](TF-D01-REVIEW-FOLLOWUP.md). |
| TF-D01 final review findings | DONE locally, pending re-review | Exclusive chart modes and single-anchor drag placement. [Verification](TF-D01-FINAL-REVIEW.md). |
| Adjacent tasks | Outside this PR | Full freehand selection and drag/resize lifecycle are deferred to TF-D02. |

Hosted CI for this change is attached to the open PR.

## 2026-11-24: superseding status

The table above records the original review state. Use the following entries for current status.

| Task | State | Evidence |
|---|---|---|
| TF-D01 | Merged in [PR #1](https://github.com/uvelan/TradeForge/pull/1) at [`65e5827`](https://github.com/uvelan/TradeForge/commit/65e5827698640d739b749bb0748e36c271d9971f) | [Final placement review](TF-D01-FINAL-REVIEW.md). The historical pending-review rows above are superseded. |
| TF-D02 | Merged in [PR #2](https://github.com/uvelan/TradeForge/pull/2) at [`9ec151e`](https://github.com/uvelan/TradeForge/commit/9ec151e401a74352c78e77d441ede1dc0ff56f0d) | [Direct body drag and resize of the four golden tools](TF-D02-REVIEW.md). This supersedes the deferral above for that scope, not all drawing tools. |
| Master-plan integration | [Draft #37](https://github.com/uvelan/TradeForge/pull/37); not merged or release-approved | [Feature matrix](plan-integration-evidence/FEATURE-MATRIX.md), [backlog](plan-integration-evidence/BACKLOG.json), and [CI workflow history](https://github.com/uvelan/TradeForge/actions/workflows/checks.yml?query=branch%3Afeature%2Ftradeforge-plan-integration). The [PR](https://github.com/uvelan/TradeForge/actions/runs/45464677023) and [push](https://github.com/uvelan/TradeForge/actions/runs/45464663949) Windows runs passed for `9cadc38c297b18256e355248e47f66f392bd9083`; verify subsequent heads independently. |
