# TF-I04 liquidity evidence review

TF-I03 merged at `2aed3936e233677c523a84943fa0dc84958e8c7a`. The implementation commit is `b9f1eef807b2301780edb0117f7489258e8a6921`, followed by adversarial cases in `f3221f3352a7cbafc8fb26b1fc9f90d923d86595` and this evidence commit.

## Liquidity algorithm v1

- `market_structure.py` offers an optional per-observation consumer. `liquidity.py` consumes only the accepted closed bar, canonical confirmed pivots and that bar's structure events. It supplies no different pivot formula and does not read the paged pivot response. Structure v1 and v2 identity rules remain intact. `indicator_engine.py` registers the new evaluator and resolves the same bounded source and archive checks used by structure.
- EQH/BSL members are confirmed highs. EQL/SSL members are confirmed lows. Each group has a fixed first-pivot anchor. Candidate price p can join anchor a only when absolute(p − a) is at most a × equality_bps / 10,000. Decimal price comparisons include the exact endpoint. A pivot goes to the nearest eligible anchor. Identical distance breaks ties by stable pool ID. Touches must be separated by the configured number of real observations. A matching pivot that arrives too soon or after the touch cap is ignored. The revision boundaries are the actual minimum and maximum constituent prices.
- A group activates with the configured count of confirmed distinct pivots. The same bar cannot then sweep its newly activated pool. Older pools are checked before new pivots are added, and the pool must have been available strictly before the crossing evidence.
- BSL requires high > upper + penetration and close < lower − reclaim. SSL requires low < lower − penetration and close > upper + reclaim. Each margin is basis points of its own boundary. A close strictly outside the outward bound emits BROKEN. A penetrating wick with a close inside the unreclaimed band emits one candidate. A later close can confirm a sweep only if that same later candle supplies both penetration and reclaim. Incomplete and not-yet-available candles contribute no observation. Raw open and crossing OHLC are retained without interpolation.
- Same-bar BOS/CHOCH references are linked only on the same side and within the pool price band. Independent pool events do not alter structure events. A subsequent BOS cannot rewrite a preceding sweep. User-supplied OHLC retains its unverified authority. No quantity or intent is inferred.

| Current state | Next revision and cause |
| --- | --- |
| New | FORMING at its first eligible confirmed pivot |
| FORMING | FORMING on an accepted additional touch or ACTIVE at the configured minimum |
| ACTIVE | ACTIVE on a further accepted touch, SWEEP_CANDIDATE on a wick without reclaim, SWEPT on a qualifying candle, or BROKEN on an outside close |
| SWEEP_CANDIDATE | Updated candidate revision on a further accepted touch, SWEPT on independently qualifying evidence, or BROKEN on an outside close |
| FORMING, ACTIVE or SWEEP_CANDIDATE | INVALIDATED by a continuity reset or EXPIRED when its real-bar age from first touch reaches the configured maximum |
| SWEPT, BROKEN, INVALIDATED, EXPIRED | Terminal for that pool ID |

Pool IDs derive from the first pivot and the rules. Each immutable revision and crossing has its own deterministic ID, observation time, nominal confirmation, actual availability, and effective evidence cutoff equal to that availability. Input prefix fingerprints and a provenance reference are included. Structure v2 preserves the pool only across its accepted configured closure; unknown or missing open evidence retires the pool. Chart projection bounds its own latest zones and markers.

## Independent numerical and replay examples

M1 fixture uses relative UTC seconds. One closed right bar confirms each pivot. At zero penetration and reclaim margins:

| Example | Constituent pivots | Pool active | Observation | First available |
| --- | --- | --- | --- | --- |
| EQH and buy-side sweep | High 12 at 60 s available 180 s; high 12 at 180 s available 300 s | 300 s, bounds 12–12 | At 300 s, OHLC 11 / 13 / 11 / 11 | 360 s |
| EQL and sell-side sweep | Low 18 at 60 s available 180 s; low 18 at 180 s available 300 s | 300 s, bounds 18–18 | At 300 s, OHLC 19 / 19 / 17 / 19 | 360 s |

At cutoff 299 there is only one confirmed touch per target pool. At 300 it is active. At 359 there is no sweep. At 360 the same-candle crossing is confirmed. Rewinding yields the exact earlier IDs and state. A buy-side candle at the same level with close 12.2 instead yields BROKEN. When that close is also a canonical structure break, its ID is linked on that candle. A separate fixture confirms a sweep and then a later BOS, without attributing anticipation to the sweep.

[Replay with an active pool](evidence/TF-I04/liquidity-before.png) · [Replay at sweep confirmation](evidence/TF-I04/liquidity-after.png)

## Paging, chart and source integrity

The evaluation accepts up to 2,000 caller candles or a fixed window of up to 10,000 slots from the existing server reader. An older cursor is scoped to a fixed cutoff, source context, rules and eligible observations. Pools and sweeps have independent limits, totals, truncation and cursors. Older pages cannot alter terminal state or the latest chart. Same-timestamp events retain deterministic order. Source correction and corrupt archived inputs reject old cursors with 409. The benchmark opens five actual Parquet partitions with SHA-256 verification and reports retrieval counters.

The terminal has independent zone, sweep, candidate and terminal visibility. It draws at most 12 latest pool zones, with up to two lines per zone, and 20 recent crossing markers. Earlier events are pageable in the evidence panel. Selection shows boundaries, constituents, activation, actual availability, crossing OHLC, source authority and same-bar references when present. Scope-change and drawing-handle browser cases exercise the existing chart infrastructure.

## Verification

| Check | Actual result |
| --- | --- |
| Backend | 148 passed, including 15 new liquidity fixture cases |
| Replay or delayed-evidence assertions | 5 new backend cases and 1 new Chromium replay case passed |
| Vitest | 8 passed |
| Chromium | 49 passed, including 46 existing and 3 new |
| TypeScript/Vite | Production build passed |

The backend fixture file exercises equality endpoints and overlap assignment, simultaneous high/low pivots, duplicate candles, activation, both sweep directions, candidate/reclaim, outside closes, opening gaps, calendar resets, expiry, multiple pools on one candle, same-candle BOS references, later BOS, CHOCH without a sweep, delayed evidence, archived integrity and bounded dense delivery. The same-candle fixture pages the two sweep IDs independently and checks a source revision returns 409.

Run `..venvScriptspython.exe -m pytest -q` at the root. From `frontend`, run `npm test`, `npm run build` and `npm run e2e -- --reporter=line`. The local browser run used test ports 4198 and 8098 and isolated storage.

[Backend](evidence/TF-I04/backend.txt) · [Vitest](evidence/TF-I04/vitest.txt) · [Build](evidence/TF-I04/build.txt) · [Chromium](evidence/TF-I04/playwright.txt)

## Performance

Windows and Python 3.13, 12 warm samples per case, with local verification active. The JSON figures time compact result serialization. Python peak is measured separately and excludes Arrow native allocations. Process RSS was not measured. Archive calculation covers five checked partitions and 10,000 decoded candles per page.

| Fixture | Calculation P50/P95, ms | JSON P50/P95, ms | Bytes | Python peak, KiB | Pool revisions / crossings |
| --- | --- | --- | ---: | ---: | --- |
| 2,000 dense | 192.94 / 248.45 | 1.13 / 2.92 | 101,167 | 566.0 | 1,269 / 333 |
| 2,000 dense, structure only | 68.51 / 96.26 | 0.31 / 0.45 | 47,328 | 384.8 | No liquidity consumer |
| 2,000 advance and rewind pair | 360.07 / 458.39 | 1.01 / 4.11 | 101,167 | 823.0 | 1,269 / 333 |
| 2,000 sparse | 48.53 / 63.46 | 0.08 / 0.29 | 1,133 | 334.8 | 0 / 0 |
| 2,000 clusters | 238.98 / 300.01 | 1.20 / 3.51 | 102,838 | 562.7 | 1,670 / 600 |
| 2,000 flat | 49.03 / 70.75 | 0.05 / 0.29 | 1,096 | 334.7 | 0 / 0 |
| 10,000 cluster archive, first page | 1,110.56 / 1,290.86 | 1.22 / 2.32 | 104,467 | 15,417.6 | 8,358 / 3,000 |
| 10,000 cluster archive, next page | 1,277.27 / 1,580.31 | 1.40 / 2.39 | 115,417 | 15,420.1 | 8,358 / 3,000 |

The same-input structure-only case is the direct baseline. TF-I03 previously measured 512.78 ms for a different dense archive fixture. That figure provides context and is not a same-input comparison. Price buckets limit matching to nearby anchors, while threshold and expiry heaps avoid scanning the whole active pool book for an ordinary candle. A wide candle can still touch many unresolved pools. Pages retain only bounded outputs and re-evaluate the same input.

Run `Get-Content docs/implementation/evidence/TF-I04/benchmark.py -Raw | ..venvScriptspython.exe -` from the repository root. [Benchmark code](evidence/TF-I04/benchmark.py) · [Measured log](evidence/TF-I04/benchmark.txt)

## Limitations and rollback

Candidates require a closed candle in v1. Intrabar display and multi-candle reclaim classification are outside this version. A candle can cross several prices, and OHLC supplies no ordering of those intrabar touches. Formation, retirement, and confirmation are local to the supplied evidence window. Broker holidays, live MT5 delivery, and physical touch devices were not verified. Archive pages pay the checksum and recalculation cost on every request.

Reverting the TF-I04 implementation, test and evidence commits rolls back the feature without a database or Parquet migration. Existing structure and drawing formats are unchanged.
