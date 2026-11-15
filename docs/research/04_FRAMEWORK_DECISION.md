# Framework decision

**KEEP** React/TypeScript, Vite, Lightweight Charts, FastAPI, SQLite, Parquet and Polars. Improve execution and data integrity locally. A planned catalog-contract cutover keeps both existing read workflows and the present storage technologies. All effort ranges are planning estimates for one engineer, familiar with the code and Windows tooling, in focused engineer-days. Include tests in the estimate. Exclude feed access, real MT5 acceptance and license lead time.

| Criterion | A Keep existing stack / ADOPT | B Add selected tools / PILOT | C Optional NautilusTrader / gated PILOT | D Replace major components / REJECT |
|---|---|---|---|---|
| Technical benefit | Enforce candle/quote compatibility, complete coverage and immutable lifecycle. Bounded Parquet scanning. | TA-Lib oracle, authorized BI5 CLI, returns report or ad hoc SQL solve discrete problems. | Order lifecycle, liquidity, venue, fees and math differential over a deterministic fixture. | Potential integrated accounting and multi-asset backtest for expanded scope only. |
| Trading correctness | Local UTC frontier, fill semantics and coverage directly fix identified failure paths. | Detects formula drift, input quality or returns errors when the source contracts hold. | Fills are richer when inputs describe order intent and liquidity. Does not validate missing ticks or broker reconciliation. | Does not inherently fix causality, incomplete data, licensing or archive identity. |
| Performance | Measured 500k in-memory prototype ~0.35 s and ~216 MiB peak. Batched IO/memory impact not measured. | Measure equal outputs, storage and RSS before adding tools. DuckDB may help joins. | No TradeForge benchmark exists; measure explicit end-to-end conversion plus replay, not upstream marketing. | No measured Windows benefit. Conversion and parallel cutover add IO and memory. |
| Complexity and migration | 25-45 days for correctness, canonical archive and simulator core (T01-T07/T11 roughly, phased). No extra service. | TA-Lib 1-2 days; Python/CLI downloader spike 2-4; DuckDB 1-3; QuantStats 2-4 after accounting. Optional QA/process only. | 5-10 day parity spike after clean M2. Separate venv on Windows, one offline subprocess, Python/Rust native wheel/runtime and bridge. | 30-60+ days for LEAN/engine swap, unpriced chart/data-layer rewrites. .NET 10 or changing dependencies/data models and duplicate support. |
| Data compatibility | Read existing immutable datasets, adapt into one canonical contract. Keep SQLite IDs, MT5 and BI5 paths. | Existing Parquet for SQL, deterministic fixture interchange for oracle, validated raw BI5 for acquisition. | Immutable snapshot adapter to quote events and instrument model; precision/time mapping and stable tie ordering required. | Full mapping of providers, symbols, feeds, interval evidence, formats, APIs and reports. |
| Windows / license | Current Windows CI with pinned environment. Review repository outbound license and Lightweight Charts notices. | Pin Windows wheel/binary plus hashes; comply with BSD/MIT/Apache and feed restrictions separately. | Stable cp313 x64 wheel exists. Do not infer 2.x behavior for 1.x. LGPL redistribution gate plus transitive provenance. | LEAN source builds require SDK; vendor local workflow adds Docker. Apache obligations and data agreements persist. |
| Regression and reversibility | Preserve existing API/cache/preferences and immutable artifact reads. Feature flags and parallel reads. | Golden fixtures remain core. Remove optional tool or adapter with no runtime data dependency. | Exact row/fill parity for supported strategy; freeze release and contract; delete venv with baseline unaffected. | Dual operation, round-trip on both catalogs, revalidate 66 backend tests and UI workflows. Harder rollback. |
| Maintenance | One team, one runtime, SQLite backup-and-restore, predictable local processes. | Small isolated tools, one owner and documented expiry trial. | Upstream version drift, dependency licensing, Windows compatibility and order-model translations. | Long-lived migrations and continuous two-stack expertise. |

## NautilusTrader decision within the execution layer

1. **Keep the simulator.** Adopt for the single next-bar breakout after trust and order rules are fixed. A small engine is independently auditable.
2. **Integrate optionally.** Pilot only for required position lifecycle or explicit model parity after M2. Keep SQLite/Parquet canonical and disable live/network adapters. Windows wheel and official baseline must be pinned together.
3. **Replace the core.** Reject now. Data incompleteness and accounting gaps remain, and no comparative results show sufficient benefit.
4. **Reuse patterns.** Adopt typed execution events, virtual time, order state, instrument constraints, model seeds and input fingerprints without copying source.

An optional engine progresses only if it passes all identical-data causality/fill goldens and either supplies a required model unavailable economically in-house or beats the existing path by at least 2x end-to-end runtime or 30% peak RSS on two representative Windows datasets with equivalent outputs. Thresholds are proposed decision gates, not promises.

## Explicit stack decisions

- **FastAPI** stays. Typed validation and mounted lifespans already work. An optional isolated research job can use a process under the current control plane.
- **SQLite** stays. Metadata, immutable history, leases and ledger events fit a local transactional writer. Long analytics belong in columnar scans. Load-test busy retries and backups before adding a server database.
- **Parquet and Polars** stay. Use UTC predicates, projected columns and batched reads. Keep disk constraints separate from query-engine choice.
- **Existing simulator** improves first. The evidence defect is a join and validation problem. Additional order and accounting semantics warrant a small versioned contract.
- **NautilusTrader** is a reference and conditional execution pilot. It does not replace quote coverage, data authority or local product constraints.
- **LEAN** is a cash-book, order event and frontier reference. Its .NET adapter work is excessive for the current one-rule, local-only scope.
- **DuckDB** is optional read-only SQL over the same verified Parquet, never a second catalog or the trade ledger. Pilot only if a representative join gets at least 25% lower p95 wall time or materially simpler audited SQL under the same memory cap. [Parquet pushdown](https://duckdb.org/docs/stable/data/parquet/overview)
- **Lightweight Charts** stays. Existing panes, overlays and official trendline/rectangle primitives fit. Profile before adding a renderer or porting the UI.

No proposed chart improvement, cross-engine win or long-horizon storage speedup has been measured. [Executed and proposed benchmarks](06_TEST_AND_BENCHMARK_PLAN.md)
