# TF-I06 independent review evidence

- TF-I05 merge and TF-I06 base: `fda26c7aff8d622c745b8c44d04b547f949defd4`.
- Implementation commit: `a9c7b63196f547deba0214a3e0ec3bf2133c283d`. The final source/test revision is `2ee0e9311c5b038ba3ed40420c06f8173676c83f`. The branch tip adds this report and executable evidence.
- PR #8, branch `feat/TF-I06-order-blocks`. Algorithm v1 and identity policy v1.
- Baseline is tree-identical to the approved TF-I05 head, which passed 198 backend, 8 Vitest, 54 Chromium and the production build.

## Order Block v1

The indicator consumes only confirmed canonical structure events. A bullish BOS selects the most recent bearish candle (`open > close`). A bearish BOS selects the most recent bullish candle (`close > open`). Search is bounded by the lesser of accepted-bar lookback and candidate age. The candidate must precede the break, meet the absolute minimum body size, and have been available by the actual BOS availability. Any candle discontinuity clears the candidate queue. The detector reports no eligible candidate or the failed minimum rule. Duplicate or overlapping input candles are rejected by the evaluation API. A strictly ordered timestamp and bounded reverse traversal determine the only most-recent candidate.

Break distance is the signed difference between the confirming close and the broken level. Optional directional displacement measures the signed difference between that candle's close and open. Both are absolute price units. Wick geometry uses the candidate's exact low and high. Body geometry uses the exact minimum and maximum of open and close. The engine does not infer or round to a symbol-specific tick grid. CHOCH is disabled by default and may explicitly qualify under the same numerical rules. Provisional wick events, countertrend breaks, and ambiguous structure never produce an OB.

The standard bullish fixture has a pivot high of 13, candidate OHLC of 11 / 12 / 9 / 10, and BOS close of 14. The wick block is 9–12 and the body block is 10–11. Mirroring prices around 25 gives a bearish BOS below 12, candidate OHLC 14 / 16 / 13 / 15, and wick geometry 13–16. In the deliberate delay fixture, candidate time is start+120 seconds, actual candidate availability is start+200, break time is start+180, nominal BOS close is start+240, and the first visible instant is start+270. The zone is absent at start+269, even though the candidate and nominal BOS close are in the past.

| State | Evidence |
| --- | --- |
| ACTIVE | An eligible confirmed structure break with a qualifying candidate. Existing zones are evaluated before formation. |
| TOUCHED | First observable overlap at the entry edge or below the configured reportable penetration. |
| PARTIALLY_MITIGATED | A later available wick or close establishes strictly greater accepted depth. Reported depth stays monotonic. |
| FULLY_MITIGATED | Observed depth reaches the far edge with no strict invalidating close. |
| INVALIDATED | Strict close beyond the far edge, an unobservable gap-through, or a continuity reset for missing or unverifiable open-market evidence. Unobservable gap-through retains prior depth. |
| EXPIRED | Accepted-bar age reaches the configured limit before same-observation mitigation. A scheduled closure adds no synthetic observations and keeps active zones. |

For a bullish zone, penetration is upper minus observed price. For a bearish zone, penetration is observed price minus lower. The reported percentage is 100 times accepted depth divided by width. The bullish 9–12 wick zone reports 25% with a wick of 11.25, 50% at 10.5, and 100% at 9. A close that stays above the zone contributes no close-based penetration. A candle entirely below 9 invalidates with zero additional depth. Repeated observations at the same accepted depth do not emit duplicate revisions.

## Availability, identity, and provenance

- The API returns exact structure event and swing IDs from the canonical stream, together with a stable local witness for the confirming pivot and break. The existing structure algorithm and published IDs are unchanged. Query-specific raw structure IDs may differ when the structure request prefix changes. The stable OB ID and revision chain use the local witness and candidate evidence.
- Domain-specific SHA-256 binds the market, logical dataset, feed, basis, rules, exact candidate and availability, and structural evidence. A revision also includes the previous event and the actual observation. The separate snapshot hash covers request cutoff, original archive lineage, and evidence sequence. Page sizes are excluded from identities.
- Optional FVG and liquidity context uses the same input stream. Every reference must have been available by formation. A later FVG does not rewrite the earlier OB. Context does not affect eligibility or identity. The no-future fixture and a positive formation-time FVG fixture exercise this distinction.
- The 100-advance fixture verifies stable formation and partial-mitigation IDs. Identical boundaries with distinct BOS evidence keep distinct IDs. Opposite blocks can coexist and may overlap.
- A single valid close cannot confirm both ordered high and low break levels on the same observation. A two-sided wick can yield two provisional structure events. The fixture checks that neither creates a block.
- Current state is sourced directly from the live book. The current-state regression traverses an older active block after more than 60 revisions and with historical limits set to zero. The 10,000-candle archive test crosses a partition boundary and checks that corruption suppresses results and makes the prior cursor return HTTP 409. Client candles remain CLIENT_SUPPLIED_UNVERIFIED.

## Verification

| Command | Result |
| --- | --- |
| `.venv/Scripts/python.exe -m pytest -q` | 229 passed including 31 OB numerical, structure, lifecycle and archive cases. [Backend log](evidence/TF-I06/pytest.txt) |
| `npm test` in frontend | 8 passed. [Vitest log](evidence/TF-I06/vitest.txt) |
| `npm run build` in frontend | TypeScript and production bundle passed. [Build log](evidence/TF-I06/build.txt) |
| `npm run e2e -- --reporter=line` in frontend | 53 passed and four drawing cases timed out during temporary local API unavailability. [Full local run](evidence/TF-I06/playwright.txt) |

All four timed-out drawing cases and the liquidity regression passed on an isolated retry. All 57 cases passed across the two local runs. The full log omits Chromium command-line dumps. [Retry log](evidence/TF-I06/playwright-retry.txt).

The initial full Chromium run exposed an encoding regression in preexisting liquidity chart labels. The API still returned the expected active pools. Restoring the original UTF-8 literals fixed the chart assertion. [Initial failure excerpt](evidence/TF-I06/playwright-initial-failure.txt).

The Chromium assignment adds a replay, an in-flight scope change, and a saved drawing drag with OB enabled. The replay verifies real canvas paint and coexists with FVG and liquidity evidence. [Candidate known, BOS unconfirmed](evidence/TF-I06/ob-before-bos.png), [BOS confirmed](evidence/TF-I06/ob-after-bos.png), [25% mitigation](evidence/TF-I06/ob-partial.png).

## Performance

Run `.venv/Scripts/python.exe -u docs/implementation/evidence/TF-I06/benchmark.py` from the repo root with PYTHONPATH set to the root. Windows, Python 3.13.14, 12 warm timing samples, JSON timing separate, one tracemalloc sample, concurrent verification activity. Native RSS was not measured. Both algorithms use the same fixture data. The archive tests verify and decode all five selected Parquet partitions. [Raw output](evidence/TF-I06/benchmark.txt).

| Fixture | OB calculation P50/P95 ms | FVG calculation P50/P95 ms | OB JSON P50/P95 ms | OB bytes | OB Python peak KiB |
| --- | ---: | ---: | ---: | ---: | ---: |
| 2,000 flat | 39.71/55.86 | 22.55/30.35 | 0.05/0.18 | 1411 | 339.4 |
| 2,000 dense | 209.45/344.99 | 118.06/174.04 | 1.98/9.65 | 182131 | 943.0 |
| 2,000 frequent partial mitigation | 264.52/474.10 | 46.69/67.29 | 1.89/2.82 | 183530 | 1243.3 |
| 10,000 archive, first page | 1496.88/1899.31 | 494.75/606.77 | 1.83/5.77 | 185203 | 16637.1 |
| 10,000 archive, next page | 1621.60/1810.39 | — | 2.14/12.06 | 185158 | 16639.3 |

Dense current delivery disabled measured 211.14/310.25 ms. The difference in medians is within run variability. Advance plus rewind measured 377.41/501.49 ms. OB adds no chart series and shares one zone primitive with FVG. Visible OB rectangles and terminal markers are individually capped at 12. OB costs more CPU on the fixture because it evaluates the structure stream and witness evidence, and publishes more events in the partial case. Archive pagination recomputes the bounded stream and re-verifies checksums. The tested 2,000- and 10,000-bar P95 values fit the documented budgets.

## Limitations and rollback

- Model-defined OHLC zones cannot identify institutional orders, order quantity, execution, or future returns. There are no signals or automatic entries.
- Structure v2 schedule declarations are assumptions. A scheduled closure can preserve existing zones, while the candidate lookup restarts at reopening. History must include the reconstructible candidate and structure and lifecycle evidence.
- Context uses the latest available default-rule FVG formation and confirmed liquidity sweep at formation. It is optional and does not exhaustively link all historical events.
- Integrity errors suppress the affected snapshot and stale cursors. This phase has no cross-snapshot persistent correction journal. Previously saved research remains untouched.
- Disable Confirmed Order Blocks in the indicator library to remove overlays. Revert the implementation commit and deploy both backend and frontend together to roll back. No data migration is involved.
