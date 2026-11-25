"""Public and direct CCI checks against an independent exact arithmetic oracle."""
from fractions import Fraction
from math import nextafter

import pytest

from research_backend.app.indicators import Bar, calculate


START = 1_704_067_200  # 2024-01-01 00:00:00 UTC.
STEP = 300
ENDPOINT = "/api/indicators/calculate"


def raw_bars(rows):
    return [dict(time=START+i*STEP, open=float(close), high=float(high), low=float(low), close=float(close))
            for i, (high, low, close) in enumerate(rows)]


def oracle(rows, period):
    typical = [sum((Fraction(str(value)) for value in row), Fraction(0)) / 3 for row in rows]
    points = []
    for i in range(period-1, len(rows)):
        window = typical[i-period+1:i+1]
        average = sum(window) / period
        deviation = sum(abs(value-average) for value in window) / period
        cci = (typical[i]-average) / (Fraction("0.015") * deviation) if deviation else Fraction(0)
        points.append({"time": START+i*STEP, "value": float(cci)})
    return points


def both_paths(client, rows, period):
    candles = raw_bars(rows)
    direct = calculate([Bar.model_validate(row) for row in candles], "cci", period)
    response = client.post(ENDPOINT, json={
        "indicator": "cci", "period": period, "source": "DEMO", "timeframe": "M5",
        "candles": candles,
    })
    assert response.status_code == 200, response.text
    public = response.json()
    assert public["authority"] == "CLIENT_SUPPLIED_UNVERIFIED"
    assert public["version"] == direct["version"] == 1
    assert public["warmup_bars"] == direct["warmup_bars"] == period
    assert public["series"] == direct["series"]
    return public["series"]["CCI"]


@pytest.mark.parametrize("price,period,first_two", [
    ("1.60003", 20, [1_704_072_900, 1_704_073_200]),
    ("1.14290", 14, [1_704_071_100, 1_704_071_400]),
])
def test_flat_forex_prices_are_exactly_zero(client, price, period, first_two):
    rows = [(price,)*3] * (period+1)
    points = both_paths(client, rows, period)
    assert [point["time"] for point in points] == first_two
    assert points == oracle(rows, period)
    assert points == [{"time": timestamp, "value": 0.0} for timestamp in first_two]
    assert both_paths(client, rows[:period-1], period) == []


@pytest.mark.parametrize("tick", ["1.60004", "1.60002"])
def test_one_tick_is_not_flat_then_window_flattens_and_replays(client, tick):
    period = 20
    flat = ("1.60003",)*3
    rows = [flat]*(period+1) + [(tick,)*3]*period
    # Reopen the earlier prefix after the last bar removes its only old price.
    prefix = both_paths(client, rows[:-1], period)
    complete = both_paths(client, rows, period)
    assert complete == oracle(rows, period)
    assert complete[2]["value"] != 0.0
    assert complete[-2]["value"] != 0.0
    assert complete[-1]["value"] == 0.0
    assert both_paths(client, rows[:-1], period) == prefix == complete[:-1]


def test_distinct_ohlc_even_when_rounded_typical_would_match(client):
    high = nextafter(1.60003, 2.0)
    rows = [(1.60003,)*3] * 19 + [(high, 1.60003, 1.60003)]
    points = both_paths(client, rows, 20)
    assert points == oracle(rows, 20)
    assert points[0]["value"] > 0


@pytest.mark.parametrize("rows", [
    [("1.75", "1.25", "1.5"), ("1.625", "1.375", "1.5"), ("1.875", "1.125", "1.5")],
    [("1.2", "1.2", "1.2"), ("1.3", "1.1", "1.2"), ("1.2", "1.2", "1.2")],
])
def test_equal_typical_prices_from_distinct_ohlc_are_flat(client, rows):
    points = both_paths(client, rows, 2)
    assert points == oracle(rows, 2)
    assert points == [{"time":START+STEP, "value":0.0}, {"time":START+2*STEP, "value":0.0}]


def test_wide_exponents_do_not_lose_prefix_precision(client):
    rows = [("1e100",)*3, ("2e100",)*3, ("1.14290",)*3, ("1e-300",)*3]
    period = 3
    before = both_paths(client, rows[:-1], period)
    after = both_paths(client, rows, period)
    assert after == oracle(rows, period)
    assert before == after[:-1]
