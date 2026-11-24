"""Causal indicator definitions for ordered OHLC bars."""

from dataclasses import dataclass
from math import isfinite, sqrt
from typing import Callable

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, model_validator

from .pivots import pivot_matches


class Bar(BaseModel):
    model_config = ConfigDict(extra="ignore")
    time: int = Field(ge=0)
    open: FiniteFloat = Field(gt=0)
    high: FiniteFloat = Field(gt=0)
    low: FiniteFloat = Field(gt=0)
    close: FiniteFloat = Field(gt=0)

    @model_validator(mode="after")
    def bounds(self):
        if not self.low <= min(self.open, self.close) <= max(self.open, self.close) <= self.high:
            raise ValueError("Invalid OHLC bounds")
        return self


Values = list[float | None]


def average(values: list[float], period: int, kind: str) -> Values:
    result: Values = [None] * min(len(values), period - 1)
    state = None
    for index in range(period - 1, len(values)):
        if kind == "EMA" and state is not None:
            state += 2 / (period + 1) * (values[index] - state)
        elif kind == "WILDER" and state is not None:
            state += (values[index] - state) / period
        else:
            window = values[index - period + 1:index + 1]
            state = (sum(weight * value for weight, value in enumerate(window, 1)) / (period * (period + 1) / 2)
                     if kind == "WMA" else sum(window) / period)
        result.append(state)
    return result


def moving(kind: str):
    return lambda bars, period: {kind: average([bar.close for bar in bars], period, kind)}


def true_range(bar: Bar, previous: float | None) -> float:
    return bar.high - bar.low if previous is None else max(
        bar.high - bar.low, abs(bar.high - previous), abs(bar.low - previous))


def atr(bars: list[Bar], period: int) -> dict[str, Values]:
    ranges = []
    previous = None
    for bar in bars:
        ranges.append(true_range(bar, previous))
        previous = bar.close
    return {"ATR": average(ranges, period, "WILDER")}


def rsi(bars: list[Bar], period: int) -> dict[str, Values]:
    changes = [right.close - left.close for left, right in zip(bars, bars[1:])]
    gains = average([max(delta, 0) for delta in changes], period, "WILDER")
    losses = average([max(-delta, 0) for delta in changes], period, "WILDER")
    return {"RSI": [None, *(None if gain is None or loss is None else
                           50 if gain == loss == 0 else 100 * gain / (gain + loss)
                           for gain, loss in zip(gains, losses))][:len(bars)]}


def bands(bars: list[Bar], period: int) -> dict[str, Values]:
    prices = [bar.close for bar in bars]
    middle = average(prices, period, "SMA")
    upper: Values = []
    lower: Values = []
    for index, mean in enumerate(middle):
        deviation = sqrt(sum((price - mean) ** 2 for price in prices[index-period+1:index+1]) / period) if mean is not None else None
        upper.append(mean + 2 * deviation if deviation is not None else None)
        lower.append(mean - 2 * deviation if deviation is not None else None)
    return {"Upper": upper, "Basis": middle, "Lower": lower}


def macd(bars: list[Bar], slow_period: int) -> dict[str, Values]:
    fast_period = max(1, round(slow_period * 12 / 26))
    prices = [bar.close for bar in bars]
    fast, slow = average(prices, fast_period, "EMA"), average(prices, slow_period, "EMA")
    values = [a - b for a, b in zip(fast, slow) if a is not None and b is not None]
    signal = average(values, 9, "EMA")
    warmup = [None] * min(len(bars), slow_period - 1)
    return {"MACD": warmup + values, "Signal": warmup + signal,
            "Histogram": warmup + [None if line is None else value - line for value, line in zip(values, signal)]}


def donchian(bars: list[Bar], period: int) -> dict[str, Values]:
    upper: Values = [None] * min(len(bars), period - 1)
    lower = upper.copy()
    for index in range(period - 1, len(bars)):
        window = bars[index-period+1:index+1]
        upper.append(max(bar.high for bar in window))
        lower.append(min(bar.low for bar in window))
    return {"Upper": upper, "Lower": lower}


def swing_points(bars: list[Bar], period: int) -> dict[str, Values]:
    highs: Values = [None] * len(bars)
    lows = highs.copy()
    for index in range(period, len(bars) - period):
        pivot = bars[index]
        window = bars[index-period:index+period+1]
        confirmed = index + period
        if pivot_matches([bar.high for bar in window], period, "HIGH"):
            highs[confirmed] = pivot.high
        if pivot_matches([bar.low for bar in window], period, "LOW"):
            lows[confirmed] = pivot.low
    return {"Swing high": highs, "Swing low": lows}


def rate_of_change(bars: list[Bar], period: int) -> dict[str, Values]:
    return {"ROC": [None] * min(period, len(bars)) + [
        100 * (bar.close / earlier.close - 1) for earlier, bar in zip(bars, bars[period:])
    ]}


def normalized_atr(bars: list[Bar], period: int) -> dict[str, Values]:
    return {"NATR": [None if value is None else 100 * value / bar.close
                     for bar, value in zip(bars, atr(bars, period)["ATR"])]}


def directional_movement(bars: list[Bar], period: int) -> dict[str, Values]:
    up_moves, down_moves, ranges = [], [], []
    for previous, bar in zip(bars, bars[1:]):
        up, down = bar.high - previous.high, previous.low - bar.low
        up_moves.append(up if up > max(down, 0) else 0.0)
        down_moves.append(down if down > max(up, 0) else 0.0)
        ranges.append(true_range(bar, previous.close))
    smoothed = [average(values, period, "WILDER") for values in (up_moves, down_moves, ranges)]
    plus_di, minus_di = [], []
    dx = []
    for up, down, span in zip(*smoothed):
        if up is None or down is None or span is None:
            continue
        plus, minus = (100 * up / span, 100 * down / span) if span else (0.0, 0.0)
        plus_di.append(plus)
        minus_di.append(minus)
        dx.append(100 * abs(plus - minus) / (plus + minus) if plus + minus else 0.0)
    seed = [None] * min(period, len(bars))
    return {"ADX": seed + average(dx, period, "WILDER"), "+DI": seed + plus_di, "-DI": seed + minus_di}


def range_percent(value: float, low: float, high: float) -> float:
    return 50.0 if high == low else 100 * (value - low) / (high - low)


def stochastic(bars: list[Bar], period: int) -> dict[str, Values]:
    values = []
    for index in range(period - 1, len(bars)):
        window = bars[index-period+1:index+1]
        values.append(range_percent(bars[index].close, min(bar.low for bar in window),
                                    max(bar.high for bar in window)))
    seed = [None] * min(period - 1, len(bars))
    return {"%K": seed + values, "%D": seed + average(values, 3, "SMA")}


def stochastic_rsi(bars: list[Bar], period: int) -> dict[str, Values]:
    strength = [v for v in rsi(bars, period)["RSI"] if v is not None]
    values = []
    for index in range(period - 1, len(strength)):
        window = strength[index-period+1:index+1]
        values.append(range_percent(strength[index], min(window), max(window)))
    seed = [None] * min(2 * period - 1, len(bars))
    return {"%K": seed + values, "%D": seed + average(values, 3, "SMA")}


def commodity_channel(bars: list[Bar], period: int) -> dict[str, Values]:
    typical = [(bar.high + bar.low + bar.close) / 3 for bar in bars]
    values: Values = [None] * min(period - 1, len(bars))
    for index in range(period - 1, len(bars)):
        window = typical[index-period+1:index+1]
        mean = sum(window) / period
        deviation = sum(abs(value - mean) for value in window) / period
        values.append((typical[index] - mean) / (0.015 * deviation) if deviation else 0.0)
    return {"CCI": values}


def keltner(bars: list[Bar], period: int) -> dict[str, Values]:
    basis = average([bar.close for bar in bars], period, "EMA")
    ranges = atr(bars, period)["ATR"]
    widths = [(mid, 2 * span) if mid is not None and span is not None else None
              for mid, span in zip(basis, ranges)]
    return {"Upper": [point[0] + point[1] if point else None for point in widths],
            "Basis": basis,
            "Lower": [point[0] - point[1] if point else None for point in widths]}


def supertrend(bars: list[Bar], period: int) -> dict[str, Values]:
    line: Values = [None] * min(period - 1, len(bars))
    upper = lower = None
    bullish = False
    for index, span in enumerate(atr(bars, period)["ATR"]):
        if span is None:
            continue
        bar = bars[index]
        mid = (bar.high + bar.low) / 2
        top, bottom = mid + 3 * span, mid - 3 * span
        if upper is None or lower is None:
            upper, lower = top, bottom
        else:
            previous = bars[index - 1].close
            upper = top if top < upper or previous > upper else upper
            lower = bottom if bottom > lower or previous < lower else lower
            # A touch does not switch direction; the current closed bar decides.
            bullish = bar.close >= lower if bullish else bar.close > upper
        line.append(lower if bullish else upper)
    return {"SuperTrend": line}


@dataclass(frozen=True)
class Definition:
    label: str
    render: str
    period: int
    warmup: Callable[[int], int]
    calculate: Callable[[list[Bar], int], dict[str, Values]]


def window_bars(period: int) -> int:
    return period


REGISTRY = {
    "sma": Definition("Simple moving average", "overlay", 20, window_bars, moving("SMA")),
    "ema": Definition("Exponential moving average", "overlay", 20, window_bars, moving("EMA")),
    "wma": Definition("Weighted moving average", "overlay", 20, window_bars, moving("WMA")),
    "bollinger": Definition("Bollinger Bands", "overlay", 20, window_bars, bands),
    "donchian": Definition("Donchian Channels", "overlay", 20, window_bars, donchian),
    "rsi": Definition("Relative Strength Index", "pane", 14, lambda p: p + 1, rsi),
    "atr": Definition("Average True Range", "pane", 14, window_bars, atr),
    "macd": Definition("MACD", "pane", 26, lambda p: p + 8, macd),
    "swings": Definition("Confirmed swing levels", "markers", 3, lambda p: 2 * p + 1, swing_points),
    "roc": Definition("Rate of change", "pane", 12, lambda p: p + 1, rate_of_change),
    "natr": Definition("Normalized ATR", "pane", 14, window_bars, normalized_atr),
    "adx": Definition("ADX / Directional Movement", "pane", 14, lambda p: 2 * p, directional_movement),
    "stochastic": Definition("Stochastic", "pane", 14, lambda p: p + 2, stochastic),
    "stochrsi": Definition("Stochastic RSI", "pane", 14, lambda p: 2 * p + 2, stochastic_rsi),
    "cci": Definition("Commodity Channel Index", "pane", 20, window_bars, commodity_channel),
    "keltner": Definition("Keltner Channels", "overlay", 20, window_bars, keltner),
    "supertrend": Definition("SuperTrend", "overlay", 10, window_bars, supertrend),
}


def calculate(bars: list[Bar], name: str, period: int) -> dict:
    definition = REGISTRY[name]
    series = {}
    try:
        calculated = definition.calculate(bars, period)
    except (OverflowError, ZeroDivisionError) as error:
        raise ValueError("Indicator calculation exceeded supported numeric range") from error
    for label, values in calculated.items():
        points = []
        for index, value in enumerate(values):
            if value is None:
                continue
            if not isfinite(value):
                raise ValueError("Indicator calculation exceeded supported numeric range")
            point = {"time": bars[index].time, "value": value}
            if name == "swings":
                point["pivot_time"] = bars[index - period].time
            points.append(point)
        series[label] = points
    return {
        "id": name, "version": 1, "period": period, "render": definition.render,
        "warmup_bars": definition.warmup(period), "availability": "confirmed_input_close",
        "series": series,
    }
