# PR #6 active liquidity pool visibility

The reviewed and actual starting SHA is `a1e57d488aba7465fe578dce8934116da7357fa3`. The implementation and tests are in `4edccfc0ff4fcecb2ccf82182517b872d42e2dab`. The evidence commit follows on the same feature branch.

## Reproduction and correction

The chart reconstructed open pools from the latest bounded revision page. The fixture has 180 candles. Wick highs of 20 and lows of 5 on candles 1 and 3 confirm separate BSL and SSL pools. Their second confirmations become available at epoch 1793577900 in the M1 backend fixture. The subsequent 87 unrelated revisions form distinct groups without reaching either active pool's price. There are 91 total historical revisions and 89 current pools. The latest 60 historical revisions contain neither active pool. The original Chromium screenshot uses the same price fixture at M5, with the last candle's high and low set to the two thresholds to keep both prices in the chart scale. The original canvas contains neither active label.

[Fixture](evidence/TF-I04-active-pools/fixture.py) · [Original backend payload excerpt](evidence/TF-I04-active-pools/before.json) · [Corrected backend payload excerpt](evidence/TF-I04-active-pools/after.json) · [Original chart](evidence/TF-I04-active-pools/active-pools-before.png) · [Corrected chart](evidence/TF-I04-active-pools/active-pools-after.png)

`liquidity.py` adds a projection directly from the complete live pool book. A compact live revision reference is updated when the state machine emits a revision. The projection serializes the live fields and clones the constituent list. It does not modify previous event objects. The browser now reads this projection in `components.tsx`. Historical revisions and crossings still supply their own historical markers and evidence. `indicators.tsx` reports current selection and adds an independent current-pool browser. Page requests use the same scope/cutoff key as existing evaluations, and browsing never writes the chart's latest response.

## API contract and compatibility

- The additive `current_pools` response contains `snapshot`, `selection`, `total`, `returned`, `truncated`, page bounds, `next_cursor` and `items`. Each item contains stable pool ID, current revision ID, classification and side, prices, constituents and touch count, first and latest confirming observation times, activation, confirmation, actual availability, current state, remaining real observations until expiry, algorithm version and provenance/fingerprint. The snapshot applies to every item in the page.
- Default selection is 12. Confirmed active pools and candidates come before FORMING pools. Within each group, sort by distance from the latest accepted close to the pool price interval, then stable ID. Use `liquidity_delivery.current_limit` from 0 to 100 and `current_cursor` to inspect more. A zero view limit counts without projecting prices or constituents. Pages do not duplicate IDs. Changing evidence invalidates the cursor with HTTP 409.
- The only current states are FORMING, ACTIVE and SWEEP_CANDIDATE. The chart omits forming candidates from confirmed zones and distinguishes pending sweeps. Terminal markers read separate historical revisions. Current lines clip to available primary chart time when old activation is outside loaded history, and current labels use the latest primary candle. This preserves the chart time and price scales. The view explains truncation and forming status. Delivery and chart line counts are bounded separately.
- The existing `pools`, `sweeps`, and `liquidity_page` contracts and cursor digests remain unchanged. The fixture checks original exact BSL and SSL revision IDs. The same-run benchmark strips the single additive key and asserts complete equality with the reviewed implementation for caller, first archive page and older archive page. Neither v1 price decisions nor provenance change.

## Regression evidence

| Check | Result |
| --- | --- |
| Full backend | 156 passed |
| Liquidity tests | 23 passed, including 8 new current-pool cases |
| Vitest | 8 passed |
| Chromium | 50 passed, including 49 existing cases |
| TypeScript/Vite production build | Passed |

`test_liquidity.py` verifies both old pools after 87 revisions, a zero history limit, page-size independence, exact active IDs, current-view truncation and cursors, distinct forming/candidate states, sweep and break timing, exact expiry, scheduled closure and missing open evidence, archived checksum failure, and replay rewind/re-advance. `liquidity-active.spec.ts` asserts backend state and intercepts real canvas text draws for the two current labels. It checks both labels remain while browsing historical and current pages. The complete suite also covers liquidity scope-switch and saved drawing-handle behavior.

[Backend](evidence/TF-I04-active-pools/backend.txt) · [Vitest](evidence/TF-I04-active-pools/vitest.txt) · [Build](evidence/TF-I04-active-pools/build.txt) · [Chromium](evidence/TF-I04-active-pools/playwright.txt)

## Performance and resources

The benchmark uses the original TF-I04 2,000-candle and 10,000-candle fixtures and invokes the reviewed and current streams in the same process. The archive verifies all five Parquet partitions on each request. Compact JSON time is measured outside calculation. The traced Python peak is sampled separately and excludes native Arrow memory and RSS. Projected chart lines are counted from the delivered pool geometries with all fixture bars visible. The browser separately verifies the two actual rendered labels.

| Fixture | Calculation median / P95 before to after | Projection median / P95 | Compact JSON median / P95 before to after | Payload before to after |
| --- | --- | --- | --- | --- |
| 2,000 caller, first history page | 123.13 / 139.36 to 120.30 / 123.31 ms | 0.02 / 0.02 ms | 0.68 / 0.95 to 0.74 / 0.78 ms | 101,167 to 107,017 bytes |
| 10,000 archive, first history page | 680.24 / 776.06 to 724.87 / 787.40 ms | 0.02 / 0.03 ms | 0.72 / 0.88 to 0.83 / 1.05 ms | 104,467 to 110,317 bytes |
| 10,000 archive, next history page | 1,334.57 / 1,520.19 to 1,484.71 / 1,859.72 ms | 0.04 / 0.07 ms | 1.86 / 3.66 to 1.83 / 3.85 ms | 115,417 to 121,267 bytes |

The runs were sequential with other local verification active. Whole archive times rose in this sample, and timed projection itself was below 0.1 ms. The original benchmark fixtures delivered two current pools and two projected lines, with no truncation. The crowded regression delivers 12 of 89 current states and shows both confirmed zones. First-archive traced Python peak was 15,416.6 to 15,417.3 KiB. The measured log contains all counts and peaks. Reproduce from the root with `Get-Content docs/implementation/evidence/TF-I04-active-pools/benchmark.py -Raw | ..venvScriptspython.exe -`. [Benchmark source](evidence/TF-I04-active-pools/benchmark.py) · [Measured output](evidence/TF-I04-active-pools/benchmark.txt)

## Limitations and rollback

The chart uses the 12-pool default view and up to two horizontal lines per confirmed/candidate pool. It preserves the existing price scale, so a zone outside that scale needs a chart scale adjustment. Additional current pages are for inspection. Archive requests still recompute and verify the same snapshot, and the bounded current response adds payload. Live MT5 and physical devices were outside verification.

Revert the implementation and evidence commits to roll back. The API addition requires no migration of historical evidence, market data or user drawing records.
