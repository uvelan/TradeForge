# T09: atomic pre-trade evidence snapshot

The existing PRE_TRADE ZIP uses one explicit SQLite read transaction to capture the trade's open status, latest plan version, complete event prefix, and exact linked signal and chart revisions. The first trade read pins the WAL snapshot. A concurrent revision or closure is either already visible (and the version/status gate rejects it when applicable) or absent from the issued package. Same-second writes do not enter the already-pinned timeline. A detected future timestamp fails closed instead of omitting sequence entries.

The existing manifest version and paths remain usable. Its optional `evidence_snapshot` object identifies the read policy, last visible event sequence, and SHA-256 digest of every ZIP component except the manifest itself. The persisted ZIP digest continues to cover the entire archive, including the manifest. Existing package downloads and legacy response imports do not require the new manifest object. Raw review retention and semantic duplicate handling are unchanged.

Tests commit a competing closure, revision, or cancellation between the first and second export reads. The resulting archive must still represent the prior open plan, import reviews for that original version, and correctly reject a stale or finalized plan on a fresh request.

This is local pre-trade evidence only. It adds no paid service, trading authority, historical metrics, or human review decisions. Those remaining T09 lanes need separate acceptance. Reverting the code needs no migration and does not affect existing package data.
