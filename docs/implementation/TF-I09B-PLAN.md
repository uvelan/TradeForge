# TF-I09B implementation plan

PR #15 merged at `f5693c54bead5d62a99e52e34e2d4a66e0345cc7` after checking both workflows and obtaining direct user authorization. The evaluator branch starts at that exact commit.

## Inputs and authority

An admitted run pins an immutable ready schema 2 definition, its exact semantic fingerprint, defaults v2, binding policy 2, evaluator v1, historical-simulation purpose and an allowlisted frozen selection. Bind the requested warm-up and evaluation range explicitly to the selection. Verify every partition ID, checksum, feed, calendar, timeframe and basis through the canonical archive loader. The initial production allowlist is the accepted Dukascopy BID session on November 13, 2026. Independent delays come from the stored definition. Recorded and live evaluation are outside this contract.

Preserve the six synthetic capture sessions. Record their exact provenance, partition IDs and manifest IDs. Exclude these identities from recorded archive and market-analysis entry points. Keep the historical evaluation allowlist server-owned. Initialize tests in fresh storage before application imports and verify the real archive with a read-only before-and-after inventory.

## Producers and rules

Reuse the indicator engine and strict SMT with an internal shared request coordinator, preverified archive cache and exact registry. Add an opt-in, resource-accounted internal publication sink independent of delivery pagination. The dependency graph pins each root and its structure dependencies, and strict SMT continues to pin both participants. Identical source and effective settings share a computation. Different settings retain separate snapshots.

Compile positive alias, temporal and sequence predecessors. Apply eligibility, age, exact snapshot, purpose, current validity, predicate and causal bounds before selection. Preserve candidate overflow as UNKNOWN. Apply three-valued ALL, ANY and NOT. Confirm absence only for a complete applicable lane; partial SMT matching and unresolved family state remain unknown. Missing mandatory source coverage gates the entire decision. Track source observations independently of when they become available.

## Lifecycle and replay

Calculate producers once at the requested horizon. Use the canonical publication availability prefixes and independent feed availability clocks to process a bounded chronological timeline. Replay begins at the declared evaluation boundary after warm-up. Context activation pins the exact usable CRT family. Setup and trigger use causal lower bounds, with stricter AFTER and SEQUENCE constraints. Explicit invalidation and family terminals precede expiry and trigger. UNKNOWN yields an explainable decision without reviving terminal attempts. A run has one pending attempt and terminal outcomes stop it. A qualified entry intent records declared direction, role, execution parameters and policy references. Quote and execution validation remain separate.

Reconstruct against the original inputs and requested cutoff without persistence. Advance transactionally with an expected decision identity. The reference implementation rebuilds the producer graph once per advance or reconstruction, with bounded cache reuse inside the call. It must never rerun indicators per bar, rule or candidate. A future whole-history backtest needs separately verified incremental producer equivalence.

## Persistence and application

Add immutable SQLite records for frozen runs and decisions using the existing initializer and triggers. Use deterministic semantic IDs, source and strategy digests, cutoff and predecessor, and optimistic atomic writes. Keep endpoints bounded for validation, create, advance, reconstruct, detail, attempt, decisions and evidence. Add the Evaluate action and an evidence detail view to Strategy Builder. Reuse existing source scope navigation when inspecting a chart observation.

## Verification

Use canonical fixtures for authority, configuration, snapshot isolation, pagination, family binding, causal transitions, ambiguity, expiry, idempotency, concurrency and replay. Verify the accepted market session in mirrored directions with four explicit latency assumptions. Record actual outcomes independently of profitability. Preserve all approved fingerprints and run the backend and browser regression lanes serially, plus warmed benchmarks and exact push and PR checks. Record practical bounds and unsupported conditions in the review.
