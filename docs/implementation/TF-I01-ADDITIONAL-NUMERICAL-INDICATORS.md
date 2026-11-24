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
