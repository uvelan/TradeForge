# TF-I05 verification — Fair Value Gap and Imbalance Engine

- TF-I04 merged base: `ba1284784013354989f7e2620766f798124cb807` (PR #6).
- TF-I05 implementation: `f4367c8a742cc3576ba9adc131b93a8262ef36c8`. The branch tip includes this report and its evidence.
- Branch: `feat/TF-I05-fvg-imbalance`. Rollback and verification below.
- Baseline: 156 backend checks, 8 Vitest checks and 50 browser checks. The base had a manual FVG drawing and no automatic FVG calculation.

## FVG v1 mathematical contract

For three contiguous, closed candles A, B and C in the same series, bullish boundaries are `[A.high, C.low]` when `A.high < C.low`. Bearish boundaries are `[C.high, A.low]` when `C.high < A.low`. Equality and overlap produce no formation. The candle's nominal close determines confirmation time. Replay can first expose it at actual availability, which may be later.

Width must meet the maximum of the independent configured thresholds: absolute price units, bps of the lower boundary, declared tick size times minimum ticks, and Wilder ATR times its multiplier. ATR includes C when available and starts its warm-up again after discontinuity. For A/B/C true ranges 2, 7 and 5, ATR(2) at C is 4.75. Optional directional body v1 requires B's signed body toward the gap direction to be positive and meet its bps threshold relative to B's open.

Bullish penetration is `upper - observed`. Bearish penetration is `observed - lower`. The observed price is the wick extreme by default or the candle close in close mode. The maximum observed penetration is retained and clamped to the original width. Mitigation percent is `100 × depth / width`. A bullish 100–105 zone with subsequent lows of 103.75, 102.5 and 100 has 25%, 50% and 100% mitigation. The remaining upper edge is 103.75 and 102.5 after the first two observations. Mirrored bearish fixtures exercise the other side.

| Condition | Status and evidence |
| --- | --- |
| Valid A/B/C and filters are satisfied | ACTIVE. Record all three OHLC observations and the original bounds. |
| A later available candle intersects the zone and strictly increases depth | PARTIALLY_MITIGATED. Record depth, surviving bounds and observation times. |
| The far boundary is reached with no strictly invalidating close | FULLY_MITIGATED. Original event remains available. |
| Candle intersects the zone and closes strictly past the far boundary | INVALIDATED with CLOSE_BEYOND_OPPOSITE_BOUNDARY. |
| Candle is entirely beyond the zone | INVALIDATED with GAP_THROUGH_UNOBSERVED. Keep the previous depth. |
| Missing observations outside a recognized closure | INVALIDATED with the continuity reason. Reset formation and ATR. |
| Configured calendar identifies a scheduled closure | Keep existing zones and reset formation and ATR. Evaluate the first actual reopening candle. |
| The accepted-candle age reaches the configured limit | EXPIRED before evaluating penetration on that candle. |
| Archive verification fails or evidence disappears | Suppress derived zones for that requested snapshot, report the source failure, and reject stale pagination. |

The API separates formations, revisions, and the current book. The first two are bounded historical views. The chart paints up to 12 current zones using one Lightweight Charts primitive and bounded terminal markers. Event IDs use version, rules, source scope and causal input; cursors additionally bind to the requested snapshot. The archive reader checks Parquet hashes and rejects duplicates and overlapping timestamps. Source authority is explicit. Weekday schedule configuration alone does not establish broker coverage.

## Independent verification

- `test_fvg.py` checks exact bullish/bearish bounds and mirrored depths, threshold edges, delayed availability, expiry, scheduled closure, missing data, overlap, simultaneous revisions, a 2,000-candle dense run, and live-book independence from historical delivery limits.
- The archive API fixture spans two partitions through the A/B/C formation. Modifying a Parquet file empties the current book, marks integrity failure, and makes the prior cursor return HTTP 409.
- `indicator-engine.spec.ts` checks formation and mitigation across replay, confirmation-time gating, rewind, coexistence with liquidity, canvas paint, and scope isolation. `drawing-edit.spec.ts` checks the saved drawing handle gesture with FVG enabled.
- Replay evidence: [before C closes](evidence/TF-I05/fvg-before.png), [after formation](evidence/TF-I05/fvg-formed.png), [after 25% mitigation](evidence/TF-I05/fvg-mitigated.png).

## Local commands and results

Windows, Python 3.13.14, Node 24.14.0, Vitest 5.1.0, Vite 8.3.8.

| Command | Evidence |
| --- | --- |
| `.venv/Scripts/python.exe -m pytest -q` | 187 passed, including 31 FVG cases. [Full backend run](evidence/TF-I05/pytest.txt) |
| `npm test` in frontend | 8 passed. [Vitest run](evidence/TF-I05/vitest.txt) |
| `npm run build` in frontend | Passed. [TypeScript and Vite build](evidence/TF-I05/build.txt) |
| `npm run e2e -- --reporter=line` in frontend | 53 passed. [Chromium run](evidence/TF-I05/playwright.txt) |
| `.venv/Scripts/python.exe -u docs/implementation/evidence/TF-I05/benchmark.py` with PYTHONPATH=. | [Benchmark command output](evidence/TF-I05/benchmark.txt) |

## Performance

Twelve warm calculations per case and an independent tracemalloc sample. JSON encoding is measured separately. Archive cases include decoding and SHA-256 verification for each of the five partitions. Native RSS was not measured. TF-I04 and FVG used identical inputs; the dense ascending fixture generates 1,998 FVG formations but no liquidity pools.

| Input | FVG calculation P50 / P95 | TF-I04 calculation P50 / P95 |
| --- | ---: | ---: |
| 2,000 flat candles | 13.43 / 15.82 ms | 31.83 / 32.95 ms |
| 2,000 dense candles | 158.92 / 183.20 ms | 33.44 / 39.96 ms |
| 2,000 repetitive partial mitigations | 59.46 / 63.12 ms | 63.77 / 67.18 ms |
| 10,000 archived candles, first page | 313.10 / 368.47 ms | 356.29 / 395.26 ms |
| 10,000 archived candles, subsequent page | 312.54 / 332.21 ms | — |

Dense FVG time is dominated by event construction and bounded live-overlap classification. With current delivery disabled, the dense case measured 155.85 / 163.82 ms. Archive calculation peaked at about 15 MiB of Python allocations. Pagination protects response size and reruns checksum validation and the bounded evaluation. See the log for serialized sizes, encoding time, allocation peaks and source counters.

## Limitations and rollback

- OHLC establishes observed price ranges. Tick-level crossing sequence, liquidity size, fills, and strategy performance cannot be inferred.
- The chart uses the last 2,000 caller bars by default. Saved sources can specify an aligned UTC start for up to 10,000 slots. The archive read budget and maximum FVG age remain bounded. Browser-supplied evidence remains unverified, and broker schedule declarations are assumptions.
- Source correction produces a different evidence snapshot. This evaluator does not persist a cross-snapshot correction journal. Historical confirmation IDs are stable for unchanged causal evidence, cutoff re-advance, and pagination. Sliding the input's earliest bar changes the prefix and may change IDs.
- Overlap annotations retain at most eight intersecting live zones per formation and identify truncation. The chart labels are bounded independently from fully pageable evidence.
- Disable Fair Value Gaps in the library to remove its overlays. To roll back the implementation on an integration branch, revert the implementation commit and redeploy together. No database migration or changes to saved drawing records are required.
