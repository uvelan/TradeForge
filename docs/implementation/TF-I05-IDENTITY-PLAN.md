# TF-I05 identity policy plan

Reviewed and starting HEAD: 71f5458ecb790d6e656329c28aec10281d4d999f. The current evaluator feeds a running hash of the whole request prefix into formation and transition IDs. Ordinary candle refresh also scopes frontend selection to the entire request body.

Define identity policy v2 with domain-tagged SHA-256 over canonical JSON. A formation uses data-market identity, algorithm configuration, exact A/B/C candles and effective availability, and the causal true-range history used by the recursive Wilder ATR when enabled. History start, history budget, paging limits, cutoff, archive membership and authority are retrieval context. Exact archive manifests and source authority remain on the result and participate in the snapshot hash. Semantically equal market observations can be retrieved from different partitions without altering event identity.

A revision hashes the prior event, the observation candle and availability, reason, depth and transition sequence. Unexpected gaps include the preceding timestamp. Non-observing candles leave no revision. Overlap annotations describe the evaluated snapshot. The full-request snapshot still binds cursors to the complete cutoff-eligible candle sequence, source corrections, and context, including cutoff. Dedicated fixtures will verify rolling stability, relevant corrections, archive integrity and browser selection.

The mathematical engine remains FVG v1. Results are computed on demand and there is no migration of persisted research records. Existing evidence retains its original policy v1 IDs. Re-evaluating it explicitly emits v2 IDs, without aliasing v1 cursors. ATR warm-up changes can legitimately change identity. A rolling result still requires reconstructible formation and transition evidence.
