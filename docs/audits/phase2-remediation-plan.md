# Phase 2 remediation plan

Starting point: 565143014c1e482ad4e652b5cdceada64b2ce27a, clean local and remote fix/tick-replay-audit-hardening. Baseline: 87 pytest and 4 Vitest tests passed, TypeScript/Vite build and pip check passed. Phase 1 already validates complete archive overlaps during replay and verifies quote-page checksums. It only redacts selected legacy aggregates.

1. Add one versioned trust projection for detail, summaries and optional AI attachments. Prove tick archives against feed, symbol, basis, model and pinned checksums, with an execution proof for new runs and revalidation for older candidates. Keep original payloads intact behind a named audit download. Keep explicit research-only OHLC and synthetic categories and exclude them from validated comparisons.
2. Present persisted trust states in Strategy Lab. Disable ineligible comparisons and suppress performance for restricted runs. Offer a rerun from saved parameters and a separate audit download.
3. Prune cold quote reads only after whole-run ordering has been validated. Rehash all pinned inputs and retain full millisecond buckets before applying entry/exit ordinals. Benchmark equivalent paths at three sizes and partition counts.
4. Test profitable and losing legacy fixtures, valid/synthetic/incomplete and corruption states, restart, export, mixed comparisons, concurrent pages and immutable records. Run local Python, frontend and browser checks, push code and evidence on the review branch, and verify exact-SHA hosted CI.

Risks: old files may be unavailable; mark their integrity failure. Cold revalidation of older runs can remain expensive. Checksums establish local integrity, and model results cannot establish broker execution, liquidity or market-calendar authority.
