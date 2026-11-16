from datetime import UTC, datetime

import pytest

from research_backend.app.indicator_engine import Evaluation, evaluate


def stamp(text):
    return int(datetime.fromisoformat(text).replace(tzinfo=UTC).timestamp())


def candle(when, price=10, width=1, **extra):
    return dict(time=when, open=price, close=price, high=price+width, low=price-width, **extra)


def request(bars, indicators, timeframe="M1", cutoff=None):
    return dict(source="DEMO", symbol="EURUSD", timeframe=timeframe, feed_id="fixture",
                dataset_id="archive", price_basis="bid", candles=bars, indicators=indicators,
                cutoff=cutoff if cutoff is not None else bars[-1]["time"] + 86400)


def calculate(bars, indicators, timeframe="M1", cutoff=None):
    return evaluate(Evaluation.model_validate(request(bars, indicators, timeframe, cutoff)))


def test_wilder_and_ema_have_independent_reference_values():
    bars = [candle(i*60, price) for i, price in enumerate([10, 12, 11, 14, 10])]
    spec = [dict(id=name, period=2) for name in ("ema", "rsi", "atr")]
    output = calculate(bars, spec)
    ema, rsi, atr = (result["series"][name] for result, name in zip(output["results"], ["EMA", "RSI", "ATR"]))
    assert [point["value"] for point in ema] == pytest.approx([11, 11, 13, 11])
    assert [point["value"] for point in rsi] == pytest.approx([100 * 2 / 3, 100 * 2 / 2.25, 32])
    assert [point["value"] for point in atr] == pytest.approx([2.5, 2.25, 3.125, 4.0625])
    assert [point["available_at"] for point in rsi] == [180, 240, 300]
    assert output["input"]["price_basis"] == "bid"
    assert output["authority"] == "CLIENT_SUPPLIED_UNVERIFIED"


@pytest.mark.parametrize(("values", "expected"), [([10, 10, 10], 50), ([10, 11, 12], 100), ([12, 11, 10], 0)])
def test_rsi_zero_cases_and_unavailable_before_warmup(values, expected):
    bars = [candle(i*60, price, width=0) for i, price in enumerate(values)]
    specs = [{"id": "rsi", "period": 2}]
    first = calculate(bars, specs, cutoff=120)["results"][0]
    assert first["series"] == {"RSI": []}
    assert first["warnings"] == ["INSUFFICIENT_WARMUP"]
    assert calculate(bars, specs)["results"][0]["series"]["RSI"][0]["value"] == expected


def test_higher_timeframe_cutoff_and_delayed_archive_are_causal():
    start = stamp("2026-11-10T00:00:00")
    bars = [candle(start, 10), candle(start+3600, 20, available_at=start+7500),
            candle(start+7200, 50), candle(start+10800, 95, closed=False)]
    specs = [{"id": "ema", "period": 2}]
    before = calculate(bars, specs, "H1", start+7400)
    assert before["results"][0]["series"]["EMA"] == []
    at = calculate(bars, specs, "H1", start+7500)
    assert at["results"][0]["series"]["EMA"] == [
        dict(time=start+3600, value=15, available_at=start+7500, status="confirmed")]
    assert at["input"]["sha256"] == calculate(bars[:2], specs, "H1", start+7500)["input"]["sha256"]
    assert at["input"]["sha256"] != before["input"]["sha256"]


def test_gap_warns_and_atr_uses_last_observed_close():
    result = calculate([candle(0, 10), candle(180, 20)], [{"id": "atr", "period": 2}])["results"][0]
    assert result["warnings"] == ["CANDLE_GAP"]
    assert result["series"]["ATR"][0]["value"] == 6.5


def test_duplicate_and_overlap_are_rejected_by_api(client):
    for candles in ([candle(60), candle(60)], [candle(60), candle(90)]):
        response = client.post("/api/indicators/evaluate", json=request(candles, [{"id": "ema"}]))
        assert response.status_code == 422
        assert "duplicate or overlap" in response.json()["detail"]


def test_previous_week_and_day_use_only_the_immediate_closed_reference():
    monday = stamp("2026-11-02T00:00:00")
    day = 86400
    candles = [candle(monday + index*day, 10+index) for index in range(8)]
    specs = [{"id": "previous_week"}, {"id": "previous_day"}]
    week, previous = calculate(candles, specs, "D1")["results"]
    assert week["series"]["High"] == [dict(time=monday+7*day, value=17, available_at=monday+8*day,
        status="confirmed", reference_start=monday, reference_end=monday+7*day)]
    assert previous["series"]["Low"][-1]["value"] == 15
    missing = calculate(candles[:3]+candles[4:], specs, "D1")["results"]
    assert missing[0]["series"]["High"] == []
    assert monday+4*day not in [point["time"] for point in missing[1]["series"]["High"]]


@pytest.mark.parametrize(("date", "hour"), [("2026-03-07", 13), ("2026-03-08", 12),
                                             ("2026-10-31", 12), ("2026-11-01", 13)])
def test_new_york_dst_clocks_and_provisional_ranges(date, hour):
    start = stamp(f"{date}T{hour:02}:00:00")
    specs = [dict(id="session_new_york", session=dict(timezone="America/New_York", start="08:00", end="10:00"))]
    bars = [candle(start-3600, 100), candle(start, 10), candle(start+3600, 12),
            candle(start+7200, 100)]
    developing = calculate(bars, specs, "H1", start+3600)["results"][0]
    assert developing["series"]["High"] == [dict(time=start, value=11, available_at=start+3600,
        status="provisional", reference_start=start, reference_end=start+7200)]
    confirmed = calculate(bars, specs, "H1", start+7200)["results"][0]
    assert confirmed["series"]["High"][0] == developing["series"]["High"][0]
    assert confirmed["series"]["High"][-1]["status"] == "confirmed"
    assert confirmed["series"]["High"][-1]["value"] == 13


def test_missing_session_bar_and_ambiguous_boundary_withhold_confirmation():
    start = stamp("2026-11-01T05:00:00")
    ambiguous = [dict(id="session_new_york", session=dict(timezone="America/New_York", start="01:00", end="03:00"))]
    result = calculate([candle(start), candle(start+3600)], ambiguous, "H1")["results"][0]
    assert result["warnings"] == ["AMBIGUOUS_OR_NONEXISTENT_SESSION_BOUNDARY"]
    assert result["series"]["High"] == []
    start = stamp("2026-11-01T13:00:00")
    specs = [dict(id="session_new_york", session=dict(timezone="America/New_York", start="08:00", end="11:00"))]
    result = calculate([candle(start), candle(start+7200)], specs, "H1")["results"][0]
    assert result["warnings"] == ["CANDLE_GAP", "INCOMPLETE_RANGE_COVERAGE"]
    assert all(point["status"] == "provisional" for point in result["series"]["High"])
