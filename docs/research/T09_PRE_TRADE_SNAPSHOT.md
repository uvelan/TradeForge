# T09: atomic pre-trade evidence snapshot

The existing PRE_TRADE ZIP uses one explicit SQLite read transaction to capture the trade's open status, latest plan version, complete event prefix, and exact linked signal and chart revisions. The first trade read pins the WAL snapshot. A concurrent revision or closure is either already visible (and the version/status gate rejects it when applicable) or absent from the issued package. Same-second writes do not enter the already-pinned timeline. A detected future timestamp fails closed instead of omitting sequence entries.

The existing manifest version and paths remain usable. Its optional `evidence_snapshot` object identifies the read policy, last visible event sequence, and SHA-256 digest of every ZIP component except the manifest itself. The persisted ZIP digest continues to cover the entire archive, including the manifest. Existing package downloads and legacy response imports do not require the new manifest object. Raw review retention and semantic duplicate handling are unchanged.

Tests commit a competing closure, revision, or cancellation between the first and second export reads. The resulting archive must still represent the prior open plan, import reviews for that original version, and correctly reject a stale or finalized plan on a fresh request.

This is local pre-trade evidence only. It adds no paid service, trading authority, or historical metrics. Historical metric lineage needs separate acceptance. Reverting the code does not affect stored packages.


## Local operator decisions

`GET /api/ai/reviews/{review_id}/decisions` returns the immutable review revision's ordered local decisions and its latest sequence. The optional `after` cursor and bounded `limit` page through the history. Submit the decision, rationale and last-seen `expected_sequence` to the same path via POST. Concurrent different submissions at the same sequence conflict; an exact retry of the latest decision returns the original row. Decisions are `ACCEPTED`, `REJECTED` or `NEEDS_FOLLOW_UP`. They never change AI review bytes, structural verification, trade state or the immutable package. The ledger is not a multi-user identity system or a factual endorsement of an AI finding.

This slice adds API access and no paid service. The existing UI remains unchanged. Rollback should leave the additive decision table and its history intact.
