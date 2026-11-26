import pytest

from research_backend.app.indicators import Bar, REGISTRY, calculate


def bars(values):
    return [Bar(time=i * 60, open=value, high=value + 1, low=value - 1, close=value)
            for i, value in enumerate(values)]


def test_indicator_reference_values():
    data = bars([10, 12, 14, 16])
    expected = {
        "sma": ("SMA", 15), "ema": ("EMA", 15), "wma": ("WMA", 46 / 3),
        "rsi": ("RSI", 100), "atr": ("ATR", 2.875),
        "bollinger": ("Upper", 17), "donchian": ("Upper", 17),
        "macd": ("MACD", 1),
        "roc": ("ROC", 100 * (16 / 12 - 1)), "natr": ("NATR", 100 * 2.875 / 16),
    }
    for name, (line, value) in expected.items():
        assert calculate(data, name, 2)["series"][line][-1]["value"] == pytest.approx(value)
    assert calculate(bars([10, 10, 10]), "rsi", 2)["series"]["RSI"][0]["value"] == 50


def test_swings_use_confirmation_bar():
    data = bars([5, 7, 10, 8, 7])
    assert calculate(data[:-1], "swings", 2)["series"]["Swing high"] == []
    assert calculate(data, "swings", 2)["series"]["Swing high"] == [{"time": 240, "pivot_time": 120, "value": 11}]
    reverse = bars([10, 8, 5, 7, 8])
    assert calculate(reverse[:-1], "swings", 2)["series"]["Swing low"] == []
    assert calculate(reverse, "swings", 2)["series"]["Swing low"] == [{"time": 240, "pivot_time": 120, "value": 4}]


@pytest.mark.parametrize("name", list(REGISTRY))
def test_indicator_prefix_is_unchanged_by_future_bars(name):
    data = bars([10, 15, 12, 11, 16, 13, 9, 14, 10, 17, 11, 12, 17, 10, 18])
    prefix = data[:12]
    short = calculate(prefix, name, 2)["series"]
    long = calculate(data, name, 2)["series"]
    assert short == {line: [point for point in points if point["time"] <= prefix[-1].time]
                     for line, points in long.items()}


def test_indicator_api_rejects_nonordered_candles(client):
    body = {"indicator": "ema", "period": 2, "source": "DEMO", "timeframe": "M1",
            "candles": [bar.model_dump() for bar in bars([10, 12, 14])]}
    response = client.post("/api/indicators/calculate", json=body)
    assert response.status_code == 200
    assert response.json()["series"]["EMA"] == [{"time": 60, "value": 11}, {"time": 120, "value": 13}]
    body["candles"].reverse()
    assert client.post("/api/indicators/calculate", json=body).status_code == 422


def test_new_indicator_hand_calculated_goldens():
    data = bars([10, 12, 14, 16, 12, 7])
    periods = 2
    expected = {
        "adx": {"ADX": [(180, 100.0)], "+DI": [(120, 200 / 3)], "-DI": [(120, 0)]},
        "stochastic": {"%K": [(60, 75), (240, 100 / 6)], "%D": [(180, 75)]},
        "stochrsi": {"%K": [(180, 50), (240, 0), (300, 0)], "%D": [(300, 50 / 3)]},
        "cci": {"CCI": [(60, 200 / 3), (240, -200 / 3)]},
        "keltner": {"Basis": [(60, 11), (120, 13)], "Upper": [(60, 16), (120, 18.5)],
                    "Lower": [(60, 6), (120, 7.5)]},
    }
    # Close=20 first crosses the still-current 19.5 stop. The active long
    # line ratchets to 11.59375 before the close at 10 switches it back.
    trend = calculate(bars([10, 12, 14, 16, 20, 22, 10]), "supertrend", periods)["series"]
    assert [p["time"] for p in trend["SuperTrend"]] == [60, 120, 180, 240, 300, 360]
    assert [p["value"] for p in trend["SuperTrend"]] == pytest.approx(
        [19.5, 19.5, 19.5, 8.1875, 11.59375, 32.40625])
    for name, series in expected.items():
        observed = calculate(data, name, periods)["series"]
        for line, values in series.items():
            points = {p["time"]: p["value"] for p in observed[line]}
            for timestamp, target in values:
                assert points[timestamp] == pytest.approx(target), (name, line, timestamp)
            assert min(points) == values[0][0]


@pytest.mark.parametrize("name", ["adx", "stochastic", "stochrsi", "cci", "keltner", "supertrend"])
def test_new_indicators_flat_short_and_empty(name):
    flat = [Bar(time=60*i, open=10, high=10, low=10, close=10) for i in range(8)]
    expected = {"adx": 0, "stochastic": 50, "stochrsi": 50, "cci": 0, "keltner": 10, "supertrend": 10}[name]
    assert all(points == [] for points in calculate([], name, 2)["series"].values())
    assert all(points == [] for points in calculate(flat[:1], name, 2)["series"].values())
    assert all(points and all(p["value"] == expected for p in points)
               for points in calculate(flat, name, 2)["series"].values())


def test_new_indicators_remain_unverified_in_public_preview(client):
    definition = client.get("/api/indicators").json()
    by_id = {entry["id"]:entry for entry in definition}
    for name in ["adx", "stochastic", "stochrsi", "cci", "keltner", "supertrend"]:
        assert by_id[name]["version"] == 1
        assert "volume" not in by_id[name]["required_fields"]
        payload = {"indicator":name,"period":2,"source":"DEMO","timeframe":"M1",
                   "candles":[bar.model_dump() for bar in bars([10, 12, 14, 16, 12, 7])]}
        response = client.post("/api/indicators/calculate", json=payload)
        assert response.status_code == 200
        assert response.json()["authority"] == "CLIENT_SUPPLIED_UNVERIFIED"
    assert "vwap" not in by_id


@pytest.mark.parametrize("name", ["adx", "stochastic", "cci", "keltner", "supertrend"])
def test_preview_rejects_nonfinite_calculated_quotes(client, name):
    payload = {"indicator": name, "period": 2, "source": "DEMO", "timeframe": "M1",
               "candles": [{"time": 60 * i, "open": 1e307, "low": 1e307,
                            "high": 1e308, "close": 1e308} for i in range(5)]}
    response = client.post("/api/indicators/calculate", json=payload)
    assert response.status_code == 422, response.text
    assert response.json()["detail"] == "Indicator calculation exceeded supported numeric range"
