# TF-I02 confirmed market structure review

TF-I01 merged at `a082ab7d9c74e2b8e50827c403227fc5a9ca8380`, the base of this branch. Structure implementation and tests are in `82e550ae8dad838e6563c12aa263d55b563e1446`. An evidence commit follows on the same branch.

## Rules and implementation

- `pivots.py` is the shared bounded-window predicate for the existing strict symmetric swings and the new `market_structure` indicator (algorithm version 1). The new definition supports 1–30 bars per side and wick or close input. Strict pivots must exceed every comparison in the correct direction by more than the configured basis-point band of the pivot price. Earliest ties permit right-side equality within the band and require a materially different left-side neighbor. A flat series supplies no pivot. Independent highs and lows can confirm on the same candle.
- `market_structure.py` compares each confirmed pivot with the previous confirmed one of the same side. The equality band uses the previous pivot's price. Outputs are HH, HL, LH, LL, EH, EL or unclassified. Two classified same-direction sides establish bullish (HH and HL) or bearish (LH and LL) structure. Mixed/equal evidence is neutral. Insufficient evidence is unconfirmed. A two-sided batch cannot establish a direction.
- Established structure persists through pullbacks and continuation breaks. The latest eligible confirmed high or low is the active break reference. It must be available strictly before the break evidence. A close beyond that level by more than its configured basis-point tolerance consumes it. A same-direction close is continuation BOS. A qualifying close in neutral or unconfirmed structure is BOS with that state recorded. An opposing close below the latest protected HL in bullish structure or above the latest protected LH in bearish structure is CHOCH. An opposing close through a different class is recorded as a countertrend break. Both opposing closes reset structure to neutral and require fresh pivots on both sides. Inverted active boundaries clear to neutral and emit a quality warning.
- When enabled, a wick that crosses without a qualifying close emits one provisional event for that level. A later candle whose wick is back within the band emits an invalidation linked to that candidate. Confirmation is a new record linked to an active candidate when applicable. The original candidate stays in the result. A new swing or a continuity gap retires the old reference. Confirmation cannot repeat on the same pivot.
- `indicator_engine.py` registers the definition and provides input source, price basis, dataset, partition and algorithm metadata. Each event carries its evidence-prefix fingerprint and a reference to the result's provenance. Browser inputs remain `CLIENT_SUPPLIED_UNVERIFIED`. A server history request may verify archive checksums for the selected input. No claim of market-schedule completeness is made from passing those checks or from a configured calendar. The engine is read-only and makes no modifications to saved research evidence or datasets.
- `indicators.tsx` persists rules and independent swing, break and candidate visibility. `components.tsx` plots swings at their confirming candle, break markers at the break candle, and confirmed horizontal level segments. The details list shows confirmation in UTC and a separate availability time if delayed. Symbols, timeframe changes and rewind discard old responses. Chart instance and drawing ownership stay with the existing chart.

## Replay and exact independent examples

For the fixture closes **10, 12, 10, 13, 11, 14, 12, 15, 13, 10, 11** at 5-minute intervals from 2026-11-02 00:00 UTC, use close-based pivots, one bar on each side, strict ties and zero tolerances. The bullish sequence is established by HH 13 and HL 11. Reflecting prices around 15 produces the corresponding bearish sequence.

| Event | Pivot timestamp and level | Pivot confirms and is available | Break candle and close | Break confirmation and first visible time |
| --- | --- | --- | --- | --- |
| Bullish continuation BOS | 00:25 UTC, high 14 | 00:35 UTC | 00:35 UTC, closes at 15 | 00:40 UTC |
| Bearish CHOCH | 00:30 UTC, protected HL 12 | 00:40 UTC | 00:45 UTC, closes at 10 | 00:50 UTC |
| Bearish continuation BOS on reflected prices | 00:25 UTC, low 16 | 00:35 UTC | 00:35 UTC, closes at 15 | 00:40 UTC |
| Bullish CHOCH on reflected prices | 00:30 UTC, protected LH 18 | 00:40 UTC | 00:45 UTC, closes at 20 | 00:50 UTC |

The tests also use the same sequence at M1 with distinct expected boundaries. Processing orders only closed candles at or before the cutoff by actual availability, breaking ties by candle time. Break evaluation precedes same-candle pivot registration. A gap clears the live structure and pivot window, and later-arriving older candles are flagged and excluded from backfilling the series. That policy preserves published prefixes. Tests compare previous events at the cutoff immediately before and exactly at BOS and CHOCH, check delayed evidence and same-availability rejection, and re-evaluate the earlier cutoff after advancing. Browser tests inspect the actually drawn BOS/CHOCH labels, take screenshots before and after CHOCH, toggle visibility, and rewind.

[Replay before CHOCH](evidence/TF-I02/structure-before-choch.png) · [Replay at CHOCH confirmation](evidence/TF-I02/structure-after-choch.png)

## Verification

| Check | Observed result |
| --- | --- |
| Backend | 119 passed, including 14 new market structure cases |
| Replay-specific new backend cases | 6 passed, included above |
| Vitest | 8 passed |
| TypeScript/Vite build | Passed |
| Chromium | 44 passed, including all 41 existing scenarios |

`test_market_structure.py` covers flat/equal prices, ties and tolerances, exact M1/H1 cutoffs, delayed/incomplete candles, gaps, double pivots, bullish and bearish BOS/CHOCH, wick candidates and reclaims, consumed levels, unprotected countertrend closes, rejected input order and duplicates, distinct source identities, and a 10,000-bar archive with five checked partitions. Playwright verifies replay rendering/rewind, configuration persistence and remount, asynchronous scope switches, and a saved drawing handle while structure is active.

Run `..venvScriptspython.exe -m pytest -q` from the repo root. From `frontend`, run `npm test`, `npm run build`, and `npm run e2e -- --reporter=line`. Browser validation used an isolated SQLite/Parquet workspace and ports 4198 and 8098. Hosted results for the evidence head live in the PR checks.

[Backend](evidence/TF-I02/backend.txt) · [Vitest](evidence/TF-I02/vitest.txt) · [Build](evidence/TF-I02/build.txt) · [Chromium](evidence/TF-I02/playwright.txt)

## Performance measurements

20 warm in-process samples per case on Windows and Python 3.13, while local verification was active. Timings exclude HTTP and rendering. The archived path includes five SHA-256 checks and decoding. A separate sample measures Python allocation peak. RSS and native Arrow allocations were not measured.

| Scenario | P50 | P95 | Python peak | Pivots / breaks |
| --- | ---: | ---: | ---: | ---: |
| 2,000 caller bars, active sequence | 99.71 ms | 135.59 ms | 2,098.7 KiB | 1,636 / 908 |
| 2,000 caller bars, flat | 29.20 ms | 36.68 ms | 327.2 KiB | 0 / 0 |
| 2,000 caller bars, sparse | 12.28 ms | 18.71 ms | 327.2 KiB | 0 / 0 |
| 10,000 archived bars, active sequence | 576.65 ms | 717.13 ms | 21,843.5 KiB | 8,180 / 4,544 |

Normal JSON requests stay capped at 2,000 candles. The optional 10,000-slot path requires an aligned fixed history start, rejects overlong windows and uses only the selected server source. It is exposed through the evaluator API. The chart uses its ordinary candle budget. Reproduce from the repo root with `Get-Content docs/implementation/evidence/TF-I02/benchmark.py -Raw | ..venvScriptspython.exe -`. [Benchmark script](evidence/TF-I02/benchmark.py) · [Measured output](evidence/TF-I02/benchmark.txt)

## Remaining limitations and rollback

- Every continuity gap resets structure, including scheduled closures. Late out-of-order candles carry a warning and require a new explicit evidence set to re-evaluate their historical effect. Source revisions can produce different fingerprints on a new evaluation.
- Large archives can produce a large event result even within the read budget. Chart drawing is capped to the most recent 40 pivots and 20 eligible break markers and has no tick-level execution interpretation. Calculations do not verify live MT5 delivery or exchange-specific hours.
- The classification and protected-swing rules are this version's working definitions. There is no automatic order handling, CRT or strategy recommendation.
- Roll back by reverting the implementation and evidence commits. The change adds no database or Parquet migration. Legacy swing settings and drawing records retain their format.
