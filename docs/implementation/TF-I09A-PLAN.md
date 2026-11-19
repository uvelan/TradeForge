# TF-I09A implementation plan

The approved TF-I08E head was `07b4afd783b8ec9a066d51113025307ed5aaabaa`. Both hosted workflows passed, the PR was mergeable, and GitHub listed no blocking reviews. After direct authorization, GitHub merged it as `66dc0bf7f24ec5d97a8a17543b2be6f1bfca77b3`. The strategy branch starts at that merge.

## Boundaries

Trade plans and their risk calculator own per-trade inputs and remain independent. AI packages still pin a trade-plan version. The legacy sample backtest and its execution and cost policies remain separate. A strategy version defines reusable rules. A future evaluation pins that version, its fingerprint, purpose, exact frozen data and request-scoped evidence. Execution outcomes remain a different record. Existing storage, charts, journal, workspaces and replay stay in place.

## Contracts

Schema 1 covers named instrument and timeframe bindings, source and per-feed delay policy, versioned indicator configurations, four rule phases, explicit temporal relations and expiry, and typed risk, execution intent and management. Policies with unresolved mandatory choices can be saved as drafts. The ready status means definition validation only. Existing producer models validate effective configurations using the named defaults policy, and persisted versions materialize the effective configuration. The backend capability catalog lists the admitted event types, parameters, logical and observed timeframes, canonical authorities and required progenitors. It explicitly marks unsupported analysis projections unavailable.

The registry remains the authority. Future execution of a condition requires an exact evidence reference with calculation snapshot, source scope, configuration, purpose and timestamp. A raw ID alone cannot satisfy it. Current-state predicates require the current canonical projection. Missing or incomplete evidence stays unknown. Strong Kleene ALL, ANY and NOT define three-valued logic. Strategy-level source, coverage, registry and purpose gates precede optional conditions. Ordered sequence and temporal links use first causal availability at or before the decision cutoff. Equal timestamps cannot satisfy strict order. An interval's end is exclusive. Elapsed seconds and counted eligible bars are distinguishable. Resource envelopes cap payload bytes, graph nodes/edges, depth and lookback.

## Persistence and UI

Use an additive strategy version table with SQLite immutable triggers. Append and verify the expected version inside a writer transaction. Stable strategy IDs, independent version IDs and semantic SHA-256 are separate. Name, description and archival status still create a new version. Sequence array order is preserved. There is no foreign-key rewrite or in-place conversion of existing plans. Versioned JSON routes provide capabilities, templates, validation, list, create, read, revise, compare, duplicate and archive. The existing navigation gains a Strategy Builder with typed controls, catalog-driven rule types and version comparisons.

## Verification

Check policy/capability rejection, truth/temporal semantics, graph bounds, mirrored unresolved templates, archive and concurrency, old trade-plan compatibility, and real browser workflows. Keep the accepted one-session Dukascopy BID historical scope separate from recorded readiness. TF-I09B can consume the version and request-scoped evidence contracts incrementally, using producers per frozen partition/configuration, persistent cursors and bounded state rather than full recomputation at every bar. No market-driven decision, fill simulation or strategy recommendation belongs to this phase.
