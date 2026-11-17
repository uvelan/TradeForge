# TF-I08 independent review evidence

- TF-I07 merge and TF-I08 starting SHA: `2f5b76e9774bdd2ea20d989edc42d46809696bee`.
- TF-I08 implementation: `2eed6aabe6273679e879f674eda32523d7d4850b`. The final implementation and tests are at `1321dcfdf03dd777d1e4d54c981b7555a6f71fb6`. The branch tip adds the review record and logs.
- [PR #10](https://github.com/uvelan/TradeForge/pull/10), branch `feat/TF-I08-unified-evidence`.

## Dependency model

The previous request shared identical archive reads but reran structure for each consumer. CRT rebuilt context with its own provenance representation and default parameters. TF-I08 shares verified market observations, exact structure calculations, and compatible FVG, liquidity and order-block products. Liquidity and OB consume the same canonical sequence when their structure settings and evidence match. Independent projections retain individual cursor and delivery limits. Calculation identities exclude delivery options.

~~~text
caller candles ── normalize once ──┐
                           verified archive/history ─┤
                                                     ├─ structure ─┬─ structure pages
                                                     │             ├─ liquidity
                                                     │             └─ order blocks
                                                     ├─ FVG
                                                     └─ CRT reference aggregation / native validation

CRT context ← eligible structure, liquidity, FVG, OB, session and previous levels
OB context  ← eligible liquidity and FVG
~~~

The coordinator stores a bounded per-request structure observation transcript for repeat consumers. A single non-context consumer stays on the direct path. A public projection is populated from canonical events with witness fields removed from the legacy event payload. Product context records form independently of historical delivery. Recursion protection rejects a cycle. Calendar, timeframe, pivot parameters, archive window and source differences create separate computation keys.

## Configuration and evidence contracts

Each result has an additive `analysis` contract v1. It identifies indicator, algorithm and identity-policy version, full logical market scope, clock cutoff, frozen mode, effective parameters and dependencies, lineage and authority, configuration fingerprint, calculation ID, and calculation snapshot. CRT adds timeframe-evidence v1 with reference and observation frames, boundary policy, source mode and authority. The existing CRT aggregation and timeframe validation remain the authority for H1/M5, H4/M15, D1/H1 and D1/M15.

Context policy `shared` uses selected indicator rules and history when the dependency is present. In its absence the context uses declared baseline defaults. Policy `independent` accepts separately declared rules, with baseline defaults when omitted. Existing saved settings default to independent. The terminal shows the effective configuration and separate dependency failures. Hiding a visual overlay leaves its analysis specification and request body unchanged. A shared context history page retains the selected dependency configurations.

Evidence-reference v1 separates the raw observation ID from a stable local witness, logical market scope, configuration identity, evidence fingerprint, source authority, and calculation snapshot. It includes observation, nominal confirmation, actual availability, first eligible timestamp, and a nullable source correction time. CRT references resolve directly to canonical records in compatible shared snapshots. Structure's raw IDs remain prefix dependent. A local witness can match a reconstructed pivot or break with unchanged local evidence, while raw ID and calculation scope can differ. The witness is not an automatic event deduplication rule. A context configuration or dependency snapshot change makes prior contextual cursors stale without renaming the numerical observation.

The as-of index filters both observation time and actual availability and can restrict configuration and source. Equal availability and observation times are ordered by raw ID. Context may cite evidence confirmed by the same close and available at that instant. To use known evidence for a subsequent whole-bar OHLC interaction, the consumer must respect its first eligible timestamp as a bar-open floor where required. CRT v1 already excludes candles straddling reference availability and delayed reclaim. Legacy OB and liquidity state machines retain their published timing. Before those observations are used for mechanical entry evaluation, review their delayed-touch and expiry semantics separately. In particular, OB v1 gates penetration by the observation's actual availability, which can differ from the whole-candle open-time requirement.

## Read isolation and budgets

History windows key on source, feed, instrument, dataset, price basis, frame, UTC cutoff, frozen state, selected manifest digests, start, end, step, and archive limits. Partition keys also bind ID, path, raw SHA-256, provider, frame, and declared row count. A verified partition can satisfy multiple overlapping windows within one request. Filtering, closure, order, and availability are reapplied per window. Candle inputs and partition records are read-only to consumers. No shared cache survives a request, so a later request verifies file contents again.

Admission limits are 20,000 distinct requested history slots, 50 unique verified partitions, 30,000 decoded records, 30,000 normalized history records, 32 calculations, 160,000 calculation-row work units, and a 96 MiB estimated evidence-memory allowance. The request retains its eight enabled indicators and 2,000 caller candle limits. The archive loader retains its single-window limits. Resource exhaustion returns a validation error. Source corruption yields explicit affected dependency failure and no confirmed derived evidence. Optional dependency failures are distinguishable from an available but empty context. Archive diagnostics distinguish checksum attempts, physical decodes, normalized rows, read reuse and partition reuse.

The memory allowance uses conservative per-row accounting plus independent record caps. It does not measure or enforce process RSS. Per-node timings include nested work when a child is evaluated inside a parent and should not be summed.

## SMT preparation

SMT remains unchanged. The detector forms a directional observation ID from the previous and current pivot timestamps in both instruments. The journal's stable event key also includes the pair provenance scope, rule version and captured pivot evidence. The current detector assumes a positively related pair and looks for opposite signed pivot deltas. It has no separately validated inverse-pair transform. Same-symbol cache keys are unsuitable for pair evidence.

Matching uses UTC windows, independently confirmed pivots and shared closed timestamps. A missing candle prevents a pivot continuity window from qualifying. Confirmation waits for the pivot and matching windows and the configured shared closed bars. Existing fields describe nominal confirmation from the supplied series; a future adapter must preserve separate actual availability from each source rather than deriving it from observation time. Verified MT5 capture and checksum-verified saved histories have separate authority from client previews. New source-correction evidence is appended with its observed time. Existing immutable events remain in the journal. A later adapter would keep both frozen manifests, both feed/market identities, the relationship policy, and pair availability on a cross-symbol reference. No confluence or new SMT eligibility behavior is included.

## Verification

| Check | Result |
| --- | --- |
| Approved output fingerprints | Exact match for 12 standalone and 10 combined projections. Calculation metadata excluded. [Approved](evidence/TF-I08/golden-approved.json) · [Current](evidence/TF-I08/golden-current.json) |
| Causal and coordinator fixtures | 16 passed. Exact BOS price/time/raw ID and zero-delivery, distinct structure settings, as-of ordering, shared custom session, optional failure, stale context cursor, read isolation and overlap budgets |
| Backend pytest | 282 passed at the final implementation. [Full log](evidence/TF-I08/pytest.txt) |
| Vitest | 8 passed. [Full log](evidence/TF-I08/vitest.txt) |
| TypeScript/Vite | Passed. [Build log](evidence/TF-I08/build.txt) |
| Chromium | 61 passed in one local run on the initial implementation with the same frontend; no retries. The final backend-only follow-up updates history metadata and zero-limit projection work. [Full log](evidence/TF-I08/playwright.txt) |

Browser evidence tests agreement between chart structure and shared CRT references and zero new evaluation requests after toggling midpoint visibility. A pivot-width edit triggers a separate compatible calculation. [Shared context evidence](evidence/TF-I08/shared-context.png).

In the overlapping archive fixture, two queries touch three partition occurrences. Their overlap shares verification and decode, resulting in two checksum attempts and two physical decodes. [Physical read comparison](evidence/TF-I08/overlap.txt). Distinct combined configurations and invalid sources retain separate admission and authority records.

## Performance

| Fixture | Calculation P50/P95 baseline → final ms | API P50/P95 baseline → final ms | Payload bytes baseline → final | Python peak KiB baseline → final |
| --- | ---: | ---: | ---: | ---: |
| 2,000 standalone CRT | 36.39/37.43 → 36.48/38.62 | 50.63/70.25 → 52.65/78.08 | 71623 → 73407 | 451.5 → 455.6 |
| 2,000 standalone market_structure | 37.27/41.70 → 39.36/42.73 | 52.69/79.90 → 52.35/59.48 | 48061 → 49353 | 394.0 → 397.4 |
| 2,000 standalone liquidity_sweeps | 63.02/66.21 → 63.16/65.02 | 81.73/89.67 → 79.85/105.85 | 124228 → 125732 | 463.9 → 470.4 |
| 2,000 standalone fair_value_gap | 37.13/45.30 → 38.38/58.93 | 52.79/58.54 → 52.74/56.16 | 110510 → 111851 | 495.4 → 525.2 |
| 2,000 standalone order_blocks | 67.61/72.36 → 69.32/72.39 | 78.47/99.41 → 79.71/84.35 | 1776 → 3353 | 340.7 → 349.4 |
| 2,000 five compatible | 220.92/266.87 → 144.08/156.06 | 252.00/264.18 → 174.58/246.21 | 354790 → 361405 | 830.4 → 2092.2 |
| 2,000 context enabled | 399.86/416.29 → 168.03/249.11 | 436.43/503.18 → 199.78/214.40 | 354788 → 368888 | 2168.8 → 4549.3 |
| 10,000 archive CRT | 196.43/312.76 → 198.51/263.94 | 205.54/340.24 → 204.17/264.82 | 73987 → 77521 | 15422.7 → 15915.9 |
| 10,000 archive five compatible | 1104.54/1196.66 → 728.78/793.52 | 1115.43/1224.90 → 756.95/837.09 | 361754 → 376587 | 15421.7 → 22949.7 |

Median calculation time improved by 34.8% for the 2,000-candle compatible workload, 58.0% with context enabled, and 34.0% for the 10,000-candle combined archive workload. The 20% target was met on these fixtures. Standalone calculation median changes in the paired run were under 6%, with some P95 variation. The combined archive retained one physical read per partition and increased Python allocation peak by about 7.35 MiB. The added expense is attributable to retained analytical observations and reference metadata. [Interleaved calculation, serialization and API measurements](evidence/TF-I08/benchmark-paired.txt).

Run the paired benchmark from the repository root with `PYTHONPATH=.` and pass a worktree of the approved baseline to `.venv/Scripts/python.exe docs/implementation/evidence/TF-I08/benchmark-paired.py BASELINE_WORKTREE`. Add `--overlap-only` to count physical checksum-and-decode calls in the overlapping archive fixture.

Benchmarks use the same Windows host and fixtures, with separate calculation, JSON encoding and Python allocation sampling. ASGI TestClient covers the actual route and excludes network transit. The focused benchmark instruments execution counts separately from timing. Its sequential runs saw substantial machine load differences. Median claims below use the interleaved run on the final implementation. Native RSS was not measured. See the individual-engine, contextual, differing-configuration, replay-cutoff and resource observations in the raw logs. [Dependency baseline](evidence/TF-I08/dependencies-baseline.txt) · [Dependency reproduction on the initial implementation](evidence/TF-I08/dependencies-final.txt) · [TF-I07 baseline reproduction](evidence/TF-I08/benchmark-baseline.txt) · [TF-I07 corpus reproduction on the initial implementation](evidence/TF-I08/benchmark-final.txt).

## Outstanding risks and rollback

- The coordinator shares exact evidence and configuration. It cannot merge different structure policies, cross-symbol feeds, or incompatible windows. Overlapping archive windows still apply their own validation and normalization.
- Context reference metadata increases response size. Request-local transcripts increase allocation on combined work, and context may need independent dependencies. TestClient results do not characterize production network or broker performance.
- Legacy event snapshots stay compatible. Evaluations that include context use a wider dependency snapshot, so analysts must refresh older context-bearing page cursors. Prior exports are immutable.
- To roll back, revert the implementation commit and redeploy frontend and backend together. No archive, SQLite, replay, drawing or preference migration is required.
