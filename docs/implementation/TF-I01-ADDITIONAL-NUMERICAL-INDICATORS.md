# TF-I01: six additional OHLC indicators

The existing instance picker consumes backend definitions. This increment adds six version-1 definitions and neither changes the eleven existing definitions nor changes saved instances. All calculations consume only the supplied closed-bar prefix; the client-supplied preview stays unverified. Gaps are not filled or interpreted as validated coverage by these calculations.

| ID | Version 1 convention | First full output (bars) |
| --- | --- | ---: |
| adx | Wilder-smoothed positive/negative directional movement and previous-close true range; tied moves are both zero. DX is zero if both DIs are zero. ADX smooths seeded DX; DIs appear at period + 1. | 2 × period |
| stochastic | Raw %K uses period high/low and close; %D is SMA(3). A flat window returns 50. | period + 2 |
| stochrsi | Existing Wilder RSI, then raw %K over the same period of RSI, with SMA(3) %D. A flat RSI window returns 50. | 2 × period + 2 |
| cci | Typical price (H+L+C)/3 with rolling SMA and mean absolute deviation about that SMA; divisor 0.015 × deviation. Flat returns zero. | period |
| keltner | Existing close EMA and Wilder ATR of the same period; channel width is 2 × ATR above/below. | period |
| supertrend | Midpoint ± 3 × existing Wilder ATR, trailing final bands, seeded to the upper band; only a strict close through the active band flips direction. | period |

Run `python -m pytest -q research_backend/tests/test_indicators.py research_backend/tests/test_indicator_engine.py`. Hand-derived fixtures exercise directional seeds, first and subsequent values, both SuperTrend flips, flat and empty data, and the public API. Prefix checks also cover the old registry. TA-Lib is not adopted as runtime or asserted equivalent where seeding conventions differ. VWAP remains withheld because the OHLC contract has no verified traded-volume basis; optional HMA, broader oracle parity and UI affordances remain separate.

## CCI flat-window precision correction

On exact baseline `3ee38870ef86e3896039e28eea595bff8108ce57`, both the direct
calculator and the ordinary preview POST returned `66.66666666666667` twice
for each flat fixture. With M5 timestamps starting 2024-01-01 00:00:00 UTC,
the corrected results (direct and HTTP 200) are:

| Constant OHLC | Period / bars | First emitted UTC (Unix) | Second emitted UTC (Unix) | Both values |
| --- | --- | --- | --- | --- |
| 1.60003 | 20 / 21 | 01:35:00 (1704072900) | 01:40:00 (1704073200) | 0.0 |
| 1.14290 | 14 / 15 | 01:05:00 (1704071100) | 01:10:00 (1704071400) | 0.0 |

The prior floating-point mean could differ from every identical typical
price by one rounding step, producing a spurious nonzero deviation. CCI now
uses exact integer totals from the shortest round-trip decimal spellings of the
validated positive floats. Both the
integer scale and division by three in the typical price cancel. Centered
deviations, and the `0.015 = 3/200` multiplier, remain exact until conversion
of the final output ratio to a float. A truly flat window emits exactly zero;
no tolerance suppresses real variation. Window timing, warmup, authority,
registry and overflow rejection on unrepresentable OHLC totals are unchanged.

The public-preview regression compares every timestamp and output to an
independent `Fraction` oracle. It covers both flat fixtures, a one-tick rise
and fall, reflattened and reopened prefixes, equal typical prices from
different OHLC inputs (including 1.3 + 1.1 + 1.2 versus 3 × 1.2), a
representable one-ULP change and mixed exponents.

```text
python -m pytest -q research_backend/tests/test_cci_precision.py research_backend/tests/test_indicators.py research_backend/tests/test_indicator_engine.py
python -m scripts.benchmark_cci_precision
```

On this Linux test host (Python 3.12.14), median calculator times over 20
measurements with six warmups and alternating baseline/fix order:

| Fixture | Baseline | Corrected |
| --- | ---: | ---: |
| 2 bars, period 2 | 0.00114 ms | 0.01154 ms |
| 2,000 bars, period 20 | 2.77 ms | 16.06 ms |
| API caps: 2,000 bars, period 200 | 18.27 ms | 46.81 ms |
| API caps, synthetic wide-exponent stress | 18.31 ms | 105.73 ms |

These are calculator measurements, not route or Windows latency guarantees.
The largest observed corrected sample was 126.05 ms in the stress fixture.

## Independent numerical acceptance for the six added indicators

The checked reference is the version-1 TradeForge convention, not a claim
that every third-party seed and flat policy is identical. Bars below are
one-based; `p` is the requested period. Every reference point must have the
expected exact ordered UTC timestamp. Numeric tolerances are `abs=1e-10`,
`rel=1e-12`, except that CCI flat/tick regression fixtures compare exactly.
The dependency-free reference uses rational closed-form EMA/Wilder weights,
exact OHLC window calculations, and hand-derived SuperTrend vectors.

| Indicator | Published series / first valid bar | Independent reference | Max absolute error on checked fixtures |
| --- | --- | --- | --- |
| ADX / Directional Movement | ADX: 2p; +DI and −DI: p+1 | Rational Wilder convolution of directional moves and true range, then DX; TA-Lib seed diagnostic only | ADX 1.43e-14; +DI and −DI 2.14e-14 exact-reference comparison |
| Stochastic | %K: p; %D: p+2 | Exact min/max and 3-point SMA; TA-Lib STOCHF on the common, nondegenerate interval | %K 1.43e-14; %D 7.11e-14 TA-Lib |
| Stochastic RSI | %K: 2p; %D: 2p+2 | Rational Wilder RSI, exact %K and SMA; TA-Lib STOCHRSI only where %K inputs are nondegenerate | %K 3.98e-13; %D 2.99e-13 TA-Lib |
| CCI | CCI: p | Exact Fraction CCI, including decimal flat and one-tick regressions; TA-Lib CCI on ordinary non-flat inputs | 0 on exact mixed reference, 7.68e-13 TA-Lib |
| Keltner Channels | Basis, Upper, Lower: p | Exact EMA plus/minus 2× exact Wilder ATR; TA-Lib EMA for Basis only, ATR seed diagnostic for bands | 0 TA-Lib Basis, 1.43e-14 exact Basis, 2.85e-14 exact Upper/Lower |
| SuperTrend | SuperTrend: p | Exact hand-derived period-2 flip and touch vectors, plus flat vectors including default period 10 | 0 |

Non-flat fixtures cover periods 3, 5, 10, 14 and 20 where applicable. The
flat vectors exercise periods 2 and 200, including the 402nd-bar %D warmup
for period-200 StochRSI. TA-Lib's two outputs from STOCHF/STOCHRSI begin at
its joint lookback; the earlier TradeForge %K points are checked by the
independent rational reference. At period 14 the observed TA-Lib diagnostics
differ by up to 0.109 for ADX, 0.363 for directional indices, and 0.794 for
Keltner bands composed from TA-Lib ATR (first band at p+1 instead of p). For
StochRSI, TA-Lib uses 0 where TradeForge specifies 50 in a degenerate range;
its lead-in can change an affected %D by 50/3. These are documented convention
differences, not accepted numerical drift on matching windows.

```text
python -m scripts.tf_i01_numerical_oracle
# In a separate environment only: TA-Lib==0.6.8, NumPy==2.5.4
python -m scripts.tf_i01_numerical_oracle --talib
python -m pytest -q research_backend/tests/test_cci_precision.py research_backend/tests/test_indicators.py research_backend/tests/test_indicator_engine.py research_backend/tests/test_tf_i01_numerical_oracle.py
python -m coverage run --branch --source=tradeforge,research_backend/app -m pytest -q --tb=short
# In frontend/
npm ci
npm run build
npm test
npm run e2e -- e2e/indicator-engine.spec.ts
```

[TA-Lib momentum API](https://ta-lib.github.io/ta-lib-python/func_groups/momentum_indicators.html)
and [Python wrapper/lookback documentation](https://github.com/TA-Lib/ta-lib-python/blob/master/README.md)
describe the optional library interface. The isolated check used TA-Lib
Python 0.6.8, C 0.6.4 and NumPy 2.5.4. The standard tests do not require
either optional package beyond the project's own locked requirements. No
legacy registry callable, default or schema changed. Baseline and revised
definitions and point payloads were also compared for each original eleven
indicators using the same 77- and 120-bar prefixes and were byte-identical.

Scope: this is synthetic numerical evidence, not exhaustive market-distribution
or all-period parity, a verified feed result, a full chart-lifecycle sign-off,
VWAP without verified traded volume, or optional HMA. No fee, risk, replay
admission or execution logic changes. Public admission remains 160 cutoffs;
historical intents stay `AWAITING_EXECUTION_VALIDATION`, and production
recorded trading remains `BLOCKED`.

Rollback: revert only the TF-I01 correction and its tests/docs on the
integration branch (or via a reviewed revert if subsequently merged). No
migration, saved-state rewrite, or deployment is required. An older build
retains the known flat-CCI defect.
