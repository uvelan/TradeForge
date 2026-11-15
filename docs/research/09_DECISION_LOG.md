# Architecture decision log

| Decision | State | Rationale / alternatives / revisit |
|---|---|---|
| D01 Keep local Windows React/Vite, FastAPI and SQLite | Adopt | Current regression suite and real scope favor one local app. Enterprise data services, workers in permanent separate stacks and language changes add operations and cannot substantiate correctness. Revisit if agreed concurrent load exceeds measured limits. |
| D02 One canonical market data contract over SQLite, Parquet, Polars | Adopt phased | Two legacy paths encode different identities and semantics. Shadow registration and adapters preserve both routes. Revisit cutover if restore or lineage parity fails. |
| D03 Improve simulator and borrow NautilusTrader patterns | Adopt | Small replay is auditable and a replacement would inherit bad input. Explicit orders, virtual ordering, cost and evidence fingerprints. An isolated optional engine has a correctness and measurable benefit gate. |
| D04 LEAN accounting patterns | Reference | Cash book, conversion freshness, order-event state and synchronized time apply. C# integration rejected for current one-rule scope. Revisit if broader assets and margin/order behavior become explicit and costed requirements. |
| D05 Freqtrade differential tests | Reference | Full/prefix and warm-up analysis address direct risks. Own fixtures and implementation; no GPL transplant or exchange bot dependency. |
| D06 BI5 provenance and download | Adopt decoder; optional pilot | Manual workflow remains; Python official interface or Node CLI only after endpoint-specific terms, completeness, exact timing/price and quota gates. Reject persistent Node service. |
| D07 Smart Money indicators | Reference | Retrospective labels can misrepresent decision time. Independent causal expressions and versioned zone/sweep confirmation, no library import. Keep SMT and CRT distinct. |
| D08 TA-Lib | Pilot as oracle | Independent numeric comparator makes regressions detectable. No runtime dependency until equivalence and install/licensing cost are demonstrated. |
| D09 Chart framework | Keep | Stable panes, crosshair and upstream native primitives. Profile annotation workload before changing framework. Keep attribution audited. |
| D10 DuckDB | Conditional pilot | Optional SQL for analytical joins under measured gates. No independent storage authority. Current Polars remains benchmark baseline. |
| D11 QuantStats | Conditional pilot | Reconciled returns only, guard missing data and annualization. Built-in basic metrics remain available and separate from journal value. |
| D12 Evidence and no live execution | Adopt | Treat calculations and audit records as read-only research. Trading authority remains broker records; market-data rights, account-spec evidence and AI raw responses are independent checks. No paid AI, order router, or automatic trade workflow. |

## Plan validation

- Preserved existing early-exit, source-order, quote-hole, weekend, verified multi-partition candle SMT, manual AI and simulated broker monitor regressions. The plan addresses residual incompatibilities, recovery, ordinary invalidation and cross-partition quotes.
- Causality is defined by a UTC availability frontier rather than visually anchored timestamps. Exact fixture timing, clock inputs and verification status remain distinguishable from known history.
- Immutable data stays readable after rollback. Parallel cutover requires backup/restore and checksums. No orders, credentials, historical records or on-disk schemas changed during this audit.
- Documented upstream license/release/source pins and package boundaries. Unresolved decisions are TradeForge outbound license, exact Dukascopy endpoint rights/charges and authoritative calendar, accepted market/limit fill policy, broker account/spec and statement acceptance, and the Windows resource profile.
- Proposed benchmark improvements are decision gates. An independent test compares current vs desired outcomes for each reproduced defect; no vendor or comparative engine result is inferred from synthetic replay.
