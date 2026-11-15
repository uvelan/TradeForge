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
