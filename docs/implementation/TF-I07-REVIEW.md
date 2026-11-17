# TF-I07 independent review evidence

- TF-I06 merge and TF-I07 base: `3b3e01a9c66c4df4164b28d91d12c2da967180bd`.
- TF-I07 source and test implementation: `fc32052040f6dc2351b58f2a4cb7b107247342ce`. The branch tip adds this report and evidence.
- [PR #9](https://github.com/uvelan/TradeForge/pull/9), branch `feat/TF-I07-crt-engine`.
- Candle Range Theory research baseline algorithm v1 and identity policy v1. Tested pairs are H1→M5, H4→M15, D1→H1 and D1→M15.

## Mathematical contract and timing

The reference is the first open, maximum high, minimum low and last close across every expected lower-timeframe observation in a completed reference period. The midpoint is (high + low)/2 and width is high − low. A zero-width or malformed reference never activates. UTC boundaries are the default. The daily aggregation policy can use an IANA zone and configured wall clock. The spring DST fixture verifies a 23-hour New York day from exactly 23 H1 candles. Nonexistent, ambiguous and unaligned boundaries are refused. Forex weeks use the declared session schedule for coverage and gaps. An unknown schedule permits a completely observed grid, with an explicit schedule warning.

Reference availability is the maximum of its closing boundary and all required evidence times. Native mode uses the same source, instrument, price basis and verified history loader for the higher timeframe. It uses UTC boundaries and requires complete lower-timeframe coverage with exactly matching native OHLC. A mismatch is visible and suppresses the reference. Native and aggregated IDs and lineage remain separate. Frozen requests must include the selected manifests for both timeframes in native mode. The combined limit is 10,000 slots, 50 selected partitions and 30,000 decoded archive rows, with partition and decode admission checks before reading the native leg. The 2,000 caller-bar limit remains in place.

A high breach requires observation high greater than the reference high and penetration at least the configured minimum. A low breach uses the mirror inequalities. Reclaim requires a close strictly inside the range and a retreat from the breached edge at least the configured reclaim distance. Both minimum-distance comparisons are inclusive. A simple boundary touch is ordinary range contact.

For [100, 105], midpoint 102.5, a high of 106 and close 104 confirms a high-side observation. A low of 99 and close 101 confirms a low-side observation. A high-side candle closing at 106 stays a candidate. A high of 106 and low of 99 in the same candle produces terminal ambiguity with no directional assignment. Neither a midpoint nor an opposite edge in the reclaim candle is a later outcome.

The delayed fixture starts the H1 window at 10:00Z. The range closes 11:00Z and the final constituent becomes available at 11:02Z. The 11:00–11:05 M5 candle straddles release and cannot prove its sweep happened after availability. The later 11:05–11:10 observation is eligible at 11:10Z. Midpoint and opposite-boundary progression require a separate candle that starts at or after actual reclaim availability. Tests also reject a dual-side candle straddling a delayed reclaim. Replay takes the running maximum of availability and nominal close for the visible prefix.

## State transitions

| State | Rule |
| --- | --- |
| ACTIVE | Release a completed positive-width reference. Keep older live references up to the configured cap. Fixed-date mode tracks only the selected reference. |
| HIGH_SWEEP_CANDIDATE / LOW_SWEEP_CANDIDATE | An eligible strict sweep lacks same-candle reclaim. Preserve the unresolved candidate, even if a later candle closes inside. |
| HIGH_SWEPT_RECLAIMED / LOW_SWEPT_RECLAIMED | An eligible sweep and its strict same-candle reclaim are both observable by actual availability. |
| EXPANSION_OBSERVED | A separate eligible post-reclaim candle spans the midpoint. Repeat observations at the same progression stage are bounded. |
| OPPOSITE_BOUNDARY_REACHED | A separate eligible post-reclaim candle spans the opposite boundary. Terminal. A candle spanning both the midpoint and boundary records only the terminal boundary. |
| AMBIGUOUS | An eligible candle strictly sweeps both edges. Terminal, with neither direction assigned for this observation. |
| INVALIDATED | Unverified continuity gap, opposite sweep while a candidate is unresolved, unobserved gap across the opposite boundary, or configured invalidating close. A sweep-side new extreme or outside close can be recorded without invalidation where allowed by configuration. |
| EXPIRED | At the configured count of real eligible observations, or when the live book reaches capacity and a new reference replaces the oldest. Scheduled closures add no observations. |

Reference and candidate ages use accepted candles. Reference age begins with the first candle starting at or after release. Candidate age counts later accepted observations. At the limit, the event expires before evaluating any further interaction on that candle. A source integrity failure suppresses the snapshot and invalidates old cursors.

## Stable evidence and scope

Reference identity uses the source, logical dataset, feed, basis, both timeframes, exact reference constituents, and native evidence where applicable. Domain-separated range, sweep, reclaim and revision hashes include the policy or event chain. Request cutoff, archive manifest and evidence sequence are part of snapshot identity. Page sizes are independent of calculation and IDs. The 100-window fixture keeps both formation and sweep IDs and exact timestamps. Independent correction fixtures check relevant and unrelated candle changes, feed and price-basis changes, and stale cursors. Current ranges come from the live book, including when all three historical limits are zero.

Optional context reuses the canonical structure, liquidity, FVG, order block, session and previous-level algorithms. It records only the most recent matching observation of each supported type as of the event and identifies its timeframe and availability. Structure context carries a stable witness beside the raw event ID. Context is informational and excluded from the core identity. A later FVG or structure event cannot rewrite earlier CRT evidence. Cross-timeframe context beyond the CRT reference itself is not requested by this version.

## Verification

| Command | Result |
| --- | --- |
| `.venv/Scripts/python.exe -m pytest -q` | 266 passed including 37 CRT cases. [Backend log](evidence/TF-I07/pytest.txt) |
| `npm test` in frontend | 8 passed. [Vitest log](evidence/TF-I07/vitest.txt) |
| `npm run build` in frontend | TypeScript and Vite passed. [Build log](evidence/TF-I07/build.txt) |
| `npm run e2e -- --reporter=line` in frontend | 60 passed in one full local run. Targeted CRT run also passed 3/3. No full-run retries. [Full Chromium log](evidence/TF-I07/playwright.txt) |

The CRT tests cover the exact aggregation math for all pairs, native comparison and corruption, inclusive and exclusive thresholds, dual ambiguity, delayed cutoff and rewind, DST, scheduled closures, overlap, and history reuse. Chromium exercises the real evaluator at the delayed reference, reclaim, expansion and rewind, request cancellation on scope changes, and saved drawing manipulation with an active CRT range. The replay fixture runs CRT together with structure, liquidity, FVG and order blocks. [Before reference](evidence/TF-I07/crt-before-reference.png), [reference released](evidence/TF-I07/crt-reference-available.png), [after reclaim](evidence/TF-I07/crt-reclaimed.png).

## Performance

| Fixture | Calculation P50/P95 ms | JSON P50/P95 ms | API P50/P95 ms | Bytes | Python peak KiB |
| --- | ---: | ---: | ---: | ---: | ---: |
| 2,000 H1/M5 | 57.44/72.05 | 0.98/1.72 | 84.30/109.03 | 132295 | 521.9 |
| 2,000 H1/M5 combined | 222.65/288.43 | 3.04/4.31 | 276.70/430.47 | 282950 | 713.0 |
| 2,000 H1/M5 CRT contextual | 234.49/285.66 | 2.87/4.35 | not measured | 302772 | 1904.7 |
| 2,000 H1/M5 no sweep | 71.86/107.19 | 0.84/2.21 | not measured | 77720 | 459.7 |
| 2,000 H4/M15 | 73.61/137.20 | 1.39/1.65 | 88.50/106.40 | 133241 | 511.1 |
| 2,000 D1/H1 | 70.30/87.75 | 1.39/4.85 | 109.16/170.56 | 132288 | 500.1 |
| 10,000 archive first | 397.06/600.21 | 1.23/1.59 | 369.51/526.03 | 134664 | 15423.3 |
| 10,000 archive next | 417.72/511.94 | 1.30/2.17 | 376.56/519.20 | 134664 | 15421.3 |
| 10,000 archive combined | 1116.94/1431.99 | 3.55/6.55 | 1065.95/1318.96 | 305174 | 15421.8 |

2,000 H1/M5 advance + rewind calc 138.62/181.39 ms.

The final run was concurrent with Chromium regression testing and met the calculation P95 budgets in the plan. The benchmark uses the TF-I06 approach of timed calculation and serialization, separate traced Python allocation, and same-machine deterministic inputs. HTTP measurements exercise the actual route using TestClient in-process and exclude network latency. Native RSS was not measured. CRT uses the existing price-zone primitive, adds zero series, and draws at most eight ranges and 32 recent annotations. The combined workload runs all requested calculations. Matching archive windows share one request-scoped decode and verification pass. The archive fixture examines and verifies five partitions and decodes 10,000 constituent candles even with all five indicators enabled. Structure, liquidity, FVG and OB each still perform their own calculations, and CRT context opted in alongside those indicator selections repeats the required computations. There is no persistent cache. Run with `PYTHONPATH=.` and `.venv/Scripts/python.exe -u docs/implementation/evidence/TF-I07/benchmark.py`. [Raw benchmark](evidence/TF-I07/benchmark.txt).

## Limitations and rollback

- This is a baseline research indicator. OHLC cannot resolve crossing sequence, demonstrate order placement or fills, or support performance claims. The indicator creates no trade orders, entry recommendations, or probability scores.
- IANA daily references require boundaries aligned to the observation timeframe. Native mode intentionally requires full agreeing constituent evidence and currently uses UTC. Provider-specific native entitlement or broker access was not exercised in the offline tests.
- Optional context uses matching timeframe default rules with the CRT schedule. An empty context means no eligible matching event in the evaluated window. Enabling the corresponding chart indicators separately can add duplicated analysis work.
- Stale cursors are rejected after source changes. Previously exported evidence remains untouched. There is no persistent cross-snapshot correction journal.
- Disable Candle Range Theory in Indicator Library to hide its overlays. Revert the implementation commit and redeploy frontend and backend together to roll back. No migration or modification of saved drawings or market data is needed.
