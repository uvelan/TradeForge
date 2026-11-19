# TF-I09A review

The strategy branch starts from TF-I08E merge `66dc0bf7f24ec5d97a8a17543b2be6f1bfca77b3`. The TF-I09A implementation is `2872fab50b27724e251f9064c015911afaef1253`. The later evidence commit contains this report, the exact definition contract and the test evidence. The latest PR head and the two checks for that head are visible on [PR #15](https://github.com/uvelan/TradeForge/pull/15).

## Definition and evidence

Schema 1 uses `STRATEGY_DEFAULTS_V1`. Definitions materialize effective producer settings. A change to defaults or the pinned algorithms needs an explicit policy or schema revision. [Schema](evidence/TF-I09A/strategy-schema.json) · [Capability and unsupported capability inventory](evidence/TF-I09A/condition-catalog.json) · [Templates](evidence/TF-I09A/templates.json)

| Producer | Algorithm / identity policy | Supported conditions |
| --- | --- | --- |
| CRT | 1 / 1 | CRT_REFERENCE, CRT_RECLAIM, CRT_STATE, CRT_MIDPOINT |
| SMT | 2 / 2 | SMT_CONFIRMED |
| Structure | 2 / 2 | STRUCTURE_PIVOT, STRUCTURE_BREAK, BOS_DIRECTION, CHOCH_DIRECTION, STRUCTURE_BREAK_PRICE |
| Liquidity | 1 / 1 | LIQUIDITY_POOL, LIQUIDITY_ACTIVE_POOL, LIQUIDITY_SWEEP |
| Fair value gap | 1 / 2 | FVG_ACTIVE, FVG_STATE |
| Order block | 1 / 1 | ORDER_BLOCK_ACTIVE, ORDER_BLOCK_STATE |

Causal timing uses policy 3 and exact references use schema 1. Liquidity and order blocks require the catalog-declared same-role, same-frame structure producer. SMT requires EURUSD and GBPUSD in either role order with a positive relationship and the same source and price basis. CRT consumes complete M5 inputs for aggregated H1. Other producer definitions admit M1 or M5 with explicit context and trigger bindings. Temporal links do not upsample, forward-fill or convert prices between bases. Unsupported products and projections remain catalogued with the individual restriction.

A rule selects a producer dependency, its purpose, observation, operator, expected value, age bound and historical/current requirement. Recorded authority is `CHECKSUM_VERIFIED_ARCHIVE` with verified arrival evidence. Simulation authority is `VERIFIED_ARCHIVE_SIMULATED_AVAILABILITY`. Future evaluation must resolve each exact reference against that calculation's registry and validate lineage, identity, purpose, source, scope, authority and cutoff. A current predicate additionally requires the active canonical projection at that cutoff. An old revision can remain valid historical evidence. An event ID cannot establish which calculation produced it. These are consumer requirements on the existing registry. The definition save validates the contract without looking up events.

## Logic and time

Each rule yields SATISFIED, NOT_SATISFIED or UNKNOWN. The tables use S, N and U in the order of arguments shown.

| Left | Right | ALL | ANY |
| --- | --- | --- | --- |
| S | S | S | S |
| S | N | N | S |
| S | U | U | S |
| N | S | N | S |
| N | N | N | N |
| N | U | N | U |
| U | S | U | S |
| U | N | N | U |
| U | U | U | U |

NOT(S) gives N, NOT(N) gives S and NOT(U) gives U. Runtime no-evidence is U. A definition with an empty group is invalid. Required frozen-source integrity, coverage, availability purpose and canonical registry eligibility gate optional expressions. Unknown mandatory evidence propagates U even if an optional expression is satisfied. Incomplete SMT matching cannot prove absence.

Use UTC interval membership with an inclusive start and exclusive end. Observation time locates the event. Actual availability in recorded mode comes from the existing timing policy and verified receipt evidence. Historical simulation uses policy-derived modeled availability and the declared feed delay. A condition activates when all its dependencies and applicable state requirements are satisfied as of the cutoff. Use this causal activation for ordered comparisons. A decision may use availability equal to its cutoff. A later receipt cannot satisfy an earlier cutoff.

`{first: A, then: B, relation: AFTER}` requires B strictly after A. BEFORE requires B strictly before A. WITHIN_AFTER additionally requires the bound to be met. SEQUENCE applies strict adjacent ordering and an optional bound from first activation to last. Equal timestamps fail strict ordering. An elapsed upper bound includes its final second. Eligible-bar limits count complete calendar-eligible observations after activation through the final allowed observation and exclude closed sessions. Missing eligible bars cause an unknown coverage result. Links require positive evidence for both referenced leaves. A negated group or a satisfied alternative cannot supply a linked leaf's missing activation. The validator rejects invalid endpoints, arity and cycles. It caps the expression at 96 nodes, depth six, 24 children per group, 96 explicit temporal links, 24 producers, 500 eligible bars and 86,400 elapsed seconds. The JSON boundary caps bytes, nodes and depth before model validation.

## TF-I09B integration contract

The version, semantic fingerprint, purpose, frozen data selection fingerprint and UTC cutoff identify an evaluation. The trace carries its lifecycle, invalidation and expiry results, per-rule outcomes, exact evidence references, missing dependencies and rejection reasons.

- WAITING_FOR_CONTEXT can advance to WAITING_FOR_SETUP when mandatory gates and context pass.
- WAITING_FOR_SETUP advances to SETUP_OBSERVED on causal setup completion. SETUP_OBSERVED advances to WAITING_FOR_TRIGGER while its context and lifecycle permit it.
- A satisfied trigger can reach TRIGGER_QUALIFIED. Explicit invalidation wins over a same-cutoff trigger. Expiry limits bound the setup. A trigger at the last allowed observed bar or second is within the limit if the setup remains valid.
- INVALIDATED, EXPIRED, NO_TRADE, INSUFFICIENT_EVIDENCE and TRIGGER_QUALIFIED are terminal for the qualification attempt or cutoff record. Archive end without a qualifying trigger can yield NO_TRADE only given sufficient coverage. An unresolved gate yields INSUFFICIENT_EVIDENCE. New data may support a later cutoff while earlier traces stay fixed. TRIGGER_QUALIFIED is a statement about rule satisfaction. Execution belongs to a separate record and model.

Pin the version and frozen selection at evaluation creation. Process sources chronologically through an append-only availability cursor. Reuse producer state keyed by source scope, lineage, timing, effective configuration and algorithm versions. Maintain bounded active candidates, an expiry queue and the registry frontier. Reuse immutable snapshots and invalidate frontiers on identity or lineage changes. Preserve exact locations for audit pagination and deterministic replay. Complete archive snapshots per candle would repeat work and obscure availability boundaries. This phase ships contracts for those decisions and no market-data state machine.

## Templates and policy

Both templates select a current H1 reference folded from M5, then compare a confirmed reclaim to later confirmed SMT. Bearish uses the swept high and bearish SMT, bullish uses the swept low and bullish SMT. Defaults document the positive EURUSD/GBPUSD historical-simulation scope. The builder can change the pair order, temporal relationship and declared delay. No optional analytical filters are enabled. The required trigger, invalidation, setup and order expiry, initial stop, target, risk limits, entry intent, execution basis, costs and ambiguity policy are unresolved. Both are drafts. The rulebook for the named CRT mechanical model was unavailable as an executable, verified specification.

Account risk may not exceed the existing 2% limit. Simultaneous exposure must cover a single trade and the target must meet the configured risk/reward minimum. Execution contracts record market, limit or stop intent and explicit cost, basis and ambiguity assumptions. The earliest window follows causal availability. Fixed-pip stops and reward-multiple targets are supported. Break-even, partial exit, trailing distance and time exits are optional typed policies. A candle close establishes no fill price.

## Version storage and API

Startup adds the `strategy_versions` table and UPDATE/DELETE guards. No backfill is required. Every valid edit appends a record in an expected-version SQLite writer transaction. An exact historical version remains readable after revision or archive. Concurrent edits from the same version receive one new revision and one 409. Stable IDs, version IDs and semantic SHA-256 serve independent purposes. Names, descriptions and status are metadata for fingerprint purposes. Rules under ALL and ANY are ordered by stable ID for hashing. SEQUENCE preserves order. Archive appends a terminal version. Duplication starts a new draft and identity.

At `/api/strategies/v1`, GET capabilities and templates are public to the local client. POST validate returns structured validation. GET/POST at the collection list and create. GET by ID optionally takes `version`. GET history supports cursor pagination. GET compare takes `from_version` and `to_version`. POST revisions and archive take `expected_version`. POST duplicate can select an immutable prior version. [Example request and response](evidence/TF-I09A/api-example.json)

Strategy Builder exposes the library, typed editor, history, comparison and validation. It labels the distinction between a valid definition, operational recorded readiness and a future evaluation. The browser checks cover invalid evidence age, unresolved readiness, immutable revisions, page reload, and stale editors. [Validation screenshot](evidence/TF-I09A/strategy-validation.png) · [Version comparison screenshot](evidence/TF-I09A/strategy-versions.png)

## Data boundaries and rollback

The accepted historical-simulation interval remains EURUSD/GBPUSD Dukascopy BID on 2026-11-13 from 00:00 inclusive to 22:00 exclusive UTC, with 1,320 M1 and 264 M5 bars per symbol. It lives in the TF-I08E acceptance evidence. Definitions bind general source/market contracts and future evaluations will freeze actual partitions. Other archived sessions require their own completeness and integrity verification. Recorded MT5 operational readiness stays blocked by unresolved clock and arrival evidence. There are no performance or profitability results from the accepted session. No source archives, local databases or credentials are included in this PR.

To roll back after deployment, stop the backend, take a SQLite backup, revert the TF-I09A commits and rebuild the frontend. Older code ignores the additive strategy table. Retain the table and backup to preserve definitions for redeployment. Existing trade plans, drawing records, replay checkpoints and market-data archives require no reverse migration.

## Verification and measurements

See [command logs and failure notes](evidence/TF-I09A/commands.md) and [measured latency, allocations and payload sizes](evidence/TF-I09A/benchmark.json). Windows 11 and Python 3.13.14 were used for 30 measured iterations per operation after three warmups. API measurements use an isolated temporary SQLite database and a local in-process ASGI client. Allocation peaks use Python tracemalloc and exclude native allocations and process RSS. The maximum graph fixture reaches 96 nodes and depth six. No strategy outcomes were computed. The status and attempt of both hosted runs for the final PR head are available on the [Actions tab](https://github.com/uvelan/TradeForge/actions/workflows/checks.yml?query=branch%3Afeat%2FTF-I09A-strategy-definition).

### Local results

| Check | Result |
| --- | --- |
| Complete backend | 536 passed. [Log](evidence/TF-I09A/backend.txt) |
| Focused evidence, causal, SMT, historical and arrival regressions | 277 passed. [Log](evidence/TF-I09A/focused.txt) |
| Strategy contracts and approved legacy fingerprints | 55 passed, including 54 strategy cases and the case comparing all 22 approved fingerprints. [Log](evidence/TF-I09A/strategy-and-goldens.txt) |
| Vitest | 8 passed across three files. [Log](evidence/TF-I09A/vitest.txt) |
| Python compilation and TypeScript/Vite build | Passed. [Compilation](evidence/TF-I09A/syntax.txt) · [Production build](evidence/TF-I09A/build.txt) |
| Full Chromium | 68 passed, zero retries. [Log](evidence/TF-I09A/chromium.txt) |
| Delayed-analysis and persistence stress | Passed in isolation. [Log](evidence/TF-I09A/delayed-stress.txt) · [First attempt](evidence/TF-I09A/delayed-stress-first.txt) |
| Checkpoint stress | 20 passed consecutively with zero retries. See the [isolated 20-run log](evidence/TF-I09A/checkpoints.txt) and the [first attempt](evidence/TF-I09A/checkpoints-first.txt). |

The initial implementation predates Unicode input hardening commit `3de3de4e699713967d84b9d8d883ff9a3c7439ae`. Valid Unicode round-trips through the validation API, and unpaired surrogates in keys or values return 422. The golden and strategy lane covers that change.
