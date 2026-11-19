# TF-I09A strategy intent and evidence integrity

Starting and reviewed HEAD was `5b0d93151e7451cabdc0274501a011cbc0579a25` on [PR #15](https://github.com/uvelan/TradeForge/pull/15). The implementation commit is `2db61934b97fa7fc1a128b6ba546343a40a8a153`. The evidence commit follows it. The final branch HEAD and both exact workflow runs are listed on that PR. [Plan](TF-I09A-INTEGRITY-PLAN.md) · [Actual baseline](evidence/TF-I09A-integrity/baseline.json) · [Baseline suite](evidence/TF-I09A-integrity/baseline-pytest.txt)

## Intent and readiness

Schema 2 uses `STRATEGY_DEFAULTS_V2`. A ready definition specifies LONG or SHORT, one traded role, a fully specified entry, the earliest execution boundary and the order expiry. Either bound role may be selected as the single traded asset. Templates explicitly choose PRIMARY. The selected role controls price basis, risk cap, and the clock used for eligible-bar bounds. Direction and traded role are independent of all indicator outputs.

| Entry | Definition contract |
| --- | --- |
| MARKET | Explicit intent without an entry-price promise. |
| LIMIT | Finite positive fixed absolute price with the traded instrument's basis. |
| STOP | Finite positive fixed absolute stop-entry price with the traded instrument's basis. |

A ready LIMIT or STOP requires a price. The rule accepts only absolute quote units and the declared basis. Price expressions, evidence-price offsets and quote-relative validation remain unavailable. Directional constraints around the then-observable quote belong to a future qualification or execution phase. The earliest permissible execution is after causal availability. The legacy fixture accepted both priced order intents with no price, and accepted a ready strategy without direction or traded role.

The Builder provides an authoritative Check readiness action. A failed check displays the returned field issues and leaves the definition in draft. Editing a definition returns it to draft for renewed validation. Templates specify fixed directions, pair roles, evidence aliases and selection policies. Their trigger, custom invalidation, order price and type, costs, stop, target and exposure choices remain configurable. [Templates and outstanding settings](evidence/TF-I09A-integrity/templates-v2.json) · [Fixed-price request and response](evidence/TF-I09A-integrity/api-example.json) · [Builder](evidence/TF-I09A-integrity/strategy-intent.png)

## Evidence and selection

An indicator dependency describes a configured producer. A runtime trace pins the producer calculation and exact snapshot, then records the exact event reference, stable market identity, applicable lifecycle family and owning attempt. The registry verifies the entire reference and the signed canonical event projection. Matching family keys use calculation identity, category, configuration, source tuple and family. An exact selected snapshot is required for each decision. A later authorized snapshot of the same calculation may advance a family after proving the original binding and the new exact revision. An unrelated calculation or a raw event ID cannot supply positive evidence. [Attempt trace schema](evidence/TF-I09A-integrity/attempt-trace-v2.json)

Rules declare EARLIEST_ELIGIBLE, LATEST_ELIGIBLE or BOUND_FAMILY. The first two select from a causally complete predicate-matched prefix ordered by availability, observation start and event ID. Latest takes the greatest tuple. Bound-family selection uses the earliest eligible matching revision, on or after the bound event's availability. Explicit strict temporal links can require it to occur later. Evidence age is measured from actual or modeled availability at the selection cutoff. Each rule search has an explicit attempt-level cap from 1 to 128. Overflow, conflicting projections, gaps and unresolved exact bindings yield UNKNOWN. Input order cannot change a valid winning event. Candidates are selected in dependency order within a single attempt. An incompatible selection produces UNKNOWN for that path instead of an exhaustive combination search or an independently mixed history.

Aliases and explicit temporal links require positive, guaranteed branches. Links into ANY or NOT branches are rejected. Same-family aliases additionally require the same producer dependency and supported lifecycle category. Cycles combining sequence, explicit time edges and binding edges are rejected. The existing three-valued logic and mandatory coverage gates apply.

## CRT lifecycle

The producer showed that ACTIVE and HIGH_SWEPT_RECLAIMED have different event IDs within one range. The earlier CURRENT ACTIVE test discarded the reference after its legitimate reclaim. Schema 2 templates establish ACTIVE as the initial prerequisite and latch the selected family. A reclaim and a strategy invalidation on that producer must reference the bound family. Normal reclaim and expansion preserve validity when the current revision is canonical. EXPIRED, INVALIDATED and OPPOSITE_BOUNDARY_REACHED close the pending attempt. AMBIGUOUS and UNCERTAIN block qualification with UNKNOWN. A terminal projection may block an entry without serving as positive rule evidence.

The deterministic two-range regression runs the actual CRT producer with both ranges present. Range A activates at 11:00 UTC, reclaims at 11:20, and receives separate canonical bearish SMT availability at 11:30. Range B remains ACTIVE under its own identity, with no applicable reclaim. The fixture verifies both initial selections, same-family reclaim, continued validity after the reclaim, terminal completion and invalidation, unknown ambiguity, preserved expiry origin and an explicitly new attempt identity. [Regression fixture](../../research_backend/tests/test_strategy_integrity.py)

For SMT, the canonical lowercase `leader` is `primary` or `comparison` on a confirmed revision. SMT_LEADER compares that field. SMT_LAGGARD projects the other participant of the same verified positive pair. Those filters are optional and off in the templates. Inverse SMT remains unsupported. CRT and SMT relate through explicit causal ordering and compatible sources, while each retains its own family. [Catalog](evidence/TF-I09A-integrity/catalog-v2.json)

## Risk and compatibility

Static limits reject intended account risk above the traded instrument cap, simultaneous exposure, or session loss limit. A supporting role's cap becomes relevant when that role is explicitly traded. No automatic clamping or new sizing engine was added. Break-even and partial-exit activation must precede the final target. The configured gross weighted partial-plus-final reward must meet the minimum R multiple. Trailing distance is typed and positive. Actual risk headroom, movement of stops, costs, fill modeling and outcome-dependent constraints belong to later phases.

The additive schema changes the definition format without a SQL migration. V1 payloads and stored fingerprints stay immutable. The v1 capability catalog remains available through `?schema_version=1`. Older definitions can be read and compared. A ready v1 submission is rejected with LEGACY_REQUIRES_REVISION. The Builder has a deliberate Revise as schema 2 action. It carries forward explicit old settings and leaves direction, role, selected prices, aliases and selection policy unresolved. Existing histories continue to use optimistic version enforcement, and schema downgrade after a v2 revision is rejected. Producer algorithm, identity, reference and timing versions are literal contract pins. Intent, binding, activation and selection changes affect semantic fingerprints. Name and description remain fingerprint-neutral. [Schema 2](evidence/TF-I09A-integrity/schema-v2.json)

## TF-I09B integration and boundaries

The future evaluator consumes one ready immutable schema 2 version, its fingerprint, explicit intent, frozen data selection, purpose, exact producer pins, selected rule bindings and declared attempt policies. The trace must retain context, setup and trigger activation times. Context can latch at initial successful qualification or apply continuously. The tracked CRT reference is always an initial selection with a separate continuous family validity gate. A setup activates when its positive requirements and explicit temporal links first become known. Trigger qualification uses the selected proof scoped to the attempt and never reassigns a bound reference.

The selected context or setup activation is the fixed setup-expiry origin. Seconds bounds include the final second. Eligible-bar bounds count complete observations of the explicitly traded role and declared timeframe strictly after the origin, using availability on that role's causal clock. Missing eligible windows yield UNKNOWN. At one cutoff, apply integrity and uncertainty gates first, then explicit invalidation and terminal lifecycle, then expiry, and finally a trigger. Trigger and order intent remain separate records. A terminal attempt is immutable. Permit one pending attempt per strategy version, purpose and frozen selection in the initial evaluator contract. A replacement family requires an explicitly new attempt with independent proof and expiry. Distinct pair participants can have different modeled delays.

The local host's recorded MT5 readiness remains blocked. The prior historical-simulation acceptance is restricted to the verified EURUSD and GBPUSD Dukascopy BID session. This change adds no market-driven evaluator, trade outcome, profitability score or broker execution.

## Rollback

Stop the local backend, preserve the SQLite database, and revert the integrity commits in a maintenance branch. The original application understands schema 1 only. Preserve v2 records for redeployment, and avoid opening or revising those versions from the earlier UI. Existing schema 1 versions and all other user data remain stored.

## Verification

[Commands and failure details](evidence/TF-I09A-integrity/commands.md) · [Before benchmark](evidence/TF-I09A/benchmark.json) · [After benchmark](evidence/TF-I09A-integrity/benchmark.json)

Both benchmark runs use 30 serial timed samples after three warmups on Windows 11 with Python 3.13.14. The maximum graph uses 96 nodes and depth six. The additional binding graph exercises 85 same-family edges. The measurements include latency, Python allocations and serialized response size. RSS and market strategy outcomes were outside this benchmark.

| Operation | Schema 1 P50 / P95 ms | Schema 2 P50 / P95 ms | Schema 2 peak Python bytes | Schema 2 response bytes |
| --- | ---: | ---: | ---: | ---: |
| Typed schema parse | 0.026 / 0.029 | 0.031 / 0.032 | 15,176 | — |
| Nested validation | 0.191 / 0.200 | 0.231 / 0.287 | 39,209 | 3,961 |
| Maximum graph | 0.831 / 1.116 | 1.054 / 1.388 | 311,411 | 27,885 |
| Maximum binding graph | — | 1.161 / 1.531 | 321,367 | 28,977 |
| Fingerprint hash | 0.038 / 0.040 | 0.044 / 0.049 | 8,896 | 64 |
| Create | 10.642 / 12.451 | 10.542 / 13.516 | 99,586 | 3,465 |
| Revise | 10.815 / 15.156 | 10.206 / 11.264 | 121,583 | 3,466 |

Backend passed **572 tests** and the focused lane passed **328**. The isolated Builder run passed **3**. Vitest and the production build passed. The workflow runs on the final SHA are linked from the PR. [Backend](evidence/TF-I09A-integrity/backend.txt) · [Focused](evidence/TF-I09A-integrity/focused.txt) · [Vitest](evidence/TF-I09A-integrity/vitest.txt) · [Build](evidence/TF-I09A-integrity/build.txt)


The complete Chromium suite passed **69 tests** with zero retries. The separate serial checkpoint run passed **20 consecutive repetitions**, and delayed-analysis drawing stress passed **1 test**. The full backend suite also preserved all **22** approved numerical fingerprints. [Chromium](evidence/TF-I09A-integrity/chromium.txt) · [Repetitions](evidence/TF-I09A-integrity/checkpoints.txt) · [Delayed-analysis stress](evidence/TF-I09A-integrity/delayed-stress.txt) · [Readiness screenshot](evidence/TF-I09A-integrity/strategy-validation.png) · [Version comparison screenshot](evidence/TF-I09A-integrity/strategy-versions.png)
