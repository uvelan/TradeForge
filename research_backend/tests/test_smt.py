import pytest

from research_backend.app.indicators import Bar
from research_backend.app.smt import confirmation_timing, detect_smt


def bars(values):
    return [Bar(time=i * 60, open=value, high=value + 1, low=value - 1, close=value)
            for i, value in enumerate(values)]


def test_smt_retains_confirmation_after_invalidation():
    primary = bars([10, 11, 14, 11, 10, 11, 16, 11, 10, 11, 12, 11])
    other = bars([22, 24, 29, 24, 22, 24, 27, 24, 22, 24, 31, 24])

    def detect(count):
        return detect_smt(primary[:count], other[:count], 2, 60, alignment_bars=0)

    assert detect(8) == []
    candidate, = detect(9)
    confirmed, = detect(10)
    invalidated, = detect(11)
    assert candidate["status"] == "SMT_CANDIDATE"
    assert confirmed["status"] == "SMT_CONFIRMED"
    assert invalidated["status"] == "SMT_INVALIDATED"
    assert confirmed["id"] == invalidated["id"] == candidate["id"]
    assert invalidated["confirmed_at"] == confirmed["confirmed_at"] == 600
    assert invalidated["transitions"][:2] == confirmed["transitions"]
    assert confirmed["primary"]["current"]["time"] == confirmed["comparison"]["current"]["time"] == 360


@pytest.mark.parametrize("alignment, expected", [(0, 0), (1, 1)])
def test_smt_matches_independently_confirmed_utc_pivots(alignment, expected):
    primary = bars([10, 11, 14, 11, 10, 11, 16, 11, 10, 11, 12, 11])
    other = bars([22, 23, 24, 29, 24, 22, 24, 27, 24, 22, 23, 22])
    events = detect_smt(primary, other, 2, 60, alignment_bars=alignment, confirmation_bars=0)
    assert len(events) == expected
    if events:
        assert events[0]["comparison"]["current"]["time"] - events[0]["primary"]["current"]["time"] == 60
        assert events[0]["confirmed_at"] == 600


def test_smt_requires_opposite_moves_and_price_tolerance():
    primary = bars([10000, 10001, 10004, 10001, 10000, 10001, 10006, 10001, 10000])
    same = bars([10000, 10001, 10004, 10001, 10000, 10001, 10005, 10001, 10000])
    opposite = bars([10000, 10001, 10004, 10001, 10000, 10001, 10002, 10001, 10000])
    assert not detect_smt(primary, same, 2, 60, alignment_bars=0, tolerance_bps=0)
    assert not detect_smt(primary, opposite, 2, 60, alignment_bars=0, tolerance_bps=3)
    assert len(detect_smt(primary, opposite, 2, 60, alignment_bars=0, tolerance_bps=0)) == 1
    assert not detect_smt(primary, opposite[:5] + opposite[6:], 2, 60, alignment_bars=0)


def test_smt_api_records_bullish_swing_evidence(client):
    primary = bars([30, 29, 25, 29, 30, 29, 23, 29, 30])
    other = bars([40, 39, 35, 39, 40, 39, 36, 39, 40])
    body = {"period": 2, "timeframe": "M1", "alignment_bars": 0, "confirmation_bars": 0,
            "primary": [bar.model_dump() for bar in primary],
            "comparison": [bar.model_dump() for bar in other]}
    response = client.post("/api/indicators/smt", json=body)
    assert response.status_code == 200
    assert response.json()["version"] == 2
    event, = response.json()["events"]
    assert event["direction"] == "bullish"
    assert event["primary"]["current"]["price"] == 22
    assert event["comparison"]["current"]["price"] == 35


@pytest.mark.parametrize("direction", ["bearish", "bullish"])
def test_either_instrument_can_lead(direction):
    first = bars([10, 11, 14, 11, 10, 11, 16, 11, 10])
    second = bars([22, 24, 29, 24, 22, 24, 27, 24, 22])
    if direction == "bullish":
        first = bars([100 - bar.close for bar in first])
        second = bars([100 - bar.close for bar in second])
    for primary, comparison, leader in [(first, second, "primary"), (second, first, "comparison")]:
        event, = detect_smt(primary, comparison, 2, 60, alignment_bars=0, confirmation_bars=0)
        assert (event["direction"], event["leader"]) == (direction, leader)


def test_default_timing_keeps_confirmation_out_of_earlier_prefixes():
    first = bars([10, 11, 12, 16, 12, 11, 10, 11, 12, 18, 12, 11, 10, 11, 12, 10, 9])
    second = bars([30, 31, 32, 36, 32, 31, 30, 31, 32, 35, 32, 31, 30, 31, 32, 30, 29])
    assert not detect_smt(first[:14], second[:14], 3, 60)
    candidate, = detect_smt(first[:15], second[:15], 3, 60)
    assert candidate["status"] == "SMT_CANDIDATE"
    assert candidate["confirmed_at"] is None
    confirmed, = detect_smt(first[:16], second[:16], 3, 60)
    assert confirmed["status"] == "SMT_CONFIRMED"
    assert confirmed["confirmed_at"] == 16 * 60
    assert confirmed["confirmed_at"] == confirmed["primary"]["current"]["time"] + 7 * 60
    assert confirmed == detect_smt(first, second, 3, 60)[0]
    timing = confirmation_timing(confirmed, {"period": 3, "alignment_bars": 2, "confirmation_bars": 1})
    assert timing["all_pivots_confirmed_at"] == 13 * 60
    assert timing["matching_wait_seconds"] == 120
    assert timing["confirmation_wait_seconds"] == 60


def test_unverified_smt_cannot_write_or_alert(client):
    first = [10, 11, 14, 11, 10, 11, 16, 11, 10, 11, 12]
    second = [22, 24, 29, 24, 22, 24, 27, 24, 22, 24, 31]
    original = {
        "period": 2, "timeframe": "M1", "alignment_bars": 0, "confirmation_bars": 1, "alert_after": 540,
        "primary_feed": {"source": "MT5", "symbol": "EURUSD", "feed_id": "mt5:example", "price_basis": "BID"},
        "comparison_feed": {"source": "MT5", "symbol": "GBPUSD", "feed_id": "mt5:example", "price_basis": "BID"},
    }

    def request(left, right, **changes):
        return client.post("/api/indicators/smt", json={
            **original, "primary": [bar.model_dump() for bar in bars(left)],
            "comparison": [bar.model_dump() for bar in bars(right)], **changes,
        })

    assert request(first[:10], second[:10], replay=True).json()["alerts"] == []
    assert client.get("/api/indicators/smt/history").json() == []
    preview = request(first[:10], second[:10]).json()
    assert preview["authority"] == "CLIENT_SUPPLIED_UNVERIFIED"
    assert preview["events"][0]["confirmed_at"] == 600 and preview["alerts"] == []
    assert client.get("/api/indicators/smt/history").json() == []
    incompatible = {**original["comparison_feed"], "price_basis": "LAST_TRADE"}
    assert request(first, second, comparison_feed=incompatible).status_code == 422


def test_saved_smt_history_survives_source_mismatch_without_reusing_old_evidence(client):
    from research_backend.app.storage import write_partition

    first = [bar.model_dump() for bar in bars([10, 11, 14, 11, 10, 11, 16, 11, 10, 11])]
    second = [bar.model_dump() for bar in bars([22, 24, 29, 24, 22, 24, 27, 24, 22, 24])]
    request = {"period": 2, "timeframe": "M1", "alignment_bars": 0, "primary": first, "comparison": second}
    for key, symbol, series in [("primary_feed", "EURUSD", first), ("comparison_feed", "GBPUSD", second)]:
        saved = write_partition("MT5", "test", symbol, "candles", "M1", "BID", series)
        request[key] = {"source": "MT5_SAVED", "symbol": symbol, "feed_id": "test",
                        "price_basis": "BID", "dataset_id": saved["id"]}
    endpoint = "/api/indicators/smt"
    history = endpoint + "/history"
    verified = client.post(endpoint, json=request).json()
    assert verified["authority"] == "VERIFIED_SAVED_DATASET"
    assert verified["events"][0]["confirmed_at"] == 600
    ledger = client.get(history).json()
    assert len(ledger) == 2
    assert all(row["provenance"]["primary"]["sha256"] for row in ledger)
    corrected = [dict(row) for row in first]
    corrected[6]["high"] = 18
    mismatch = client.post(endpoint, json={**request, "primary": corrected})
    assert mismatch.status_code == 409 and "SOURCE_MISMATCH" in mismatch.json()["detail"]
    assert client.get(history).json() == ledger
    saved = write_partition("MT5", "test", "EURUSD", "candles", "M1", "BID", corrected)
    request["primary_feed"]["dataset_id"] = saved["id"]
    updated = client.post(endpoint, json={**request, "primary": corrected}).json()
    assert updated["events"][0]["primary"]["current"]["price"] == 18
    assert len(client.get(history).json()) == 4
