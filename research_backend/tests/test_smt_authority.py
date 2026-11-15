from datetime import UTC, datetime

from research_backend.app import indicator_routes
from research_backend.app.saved_history import saved_range
from research_backend.app.storage import write_partition


LEFT = [10, 11, 14, 11, 10, 11, 16, 11, 10, 11]
RIGHT = [22, 24, 29, 24, 22, 24, 27, 24, 22, 24]
RULES = {"period": 2, "timeframe": "M1", "alignment_bars": 0}


def candles(values, start):
    return [{"time":start + i * 60, "open":x, "high":x+1, "low":x-1, "close":x} for i,x in enumerate(values)]


def test_live_smt_captures_closed_broker_bars_and_alerts_once(client, monkeypatch):
    close = int(datetime.now(UTC).timestamp()) // 60 * 60
    start = close - 600

    def fetch(symbol, timeframe, limit, *, provenance):
        provenance.update(feed_id="mt5:test", price_basis="BID")
        return candles((LEFT if symbol == "EURUSD" else RIGHT) + [99], start)

    monkeypatch.setattr(indicator_routes, "mt5_rates", fetch)
    request = {**RULES, "primary_symbol":"EURUSD", "comparison_symbol":"GBPUSD", "available_through":close-60,
               "alert_after":close-60, "primary":candles([99]*10,start)}
    url = "/api/indicators/smt/live"
    candidate = client.post(url, json=request).json()
    assert candidate["events"][0]["status"] == "SMT_CANDIDATE"
    assert candidate["alerts"] == []
    request["available_through"] = close
    result = client.post(url, json=request).json()
    assert result["authority"] == "VERIFIED_MT5_CAPTURE"
    alert, = result["alerts"]
    assert alert["available_at"] == close
    assert alert["primary"]["current"]["price"] == 17
    assert client.post(url, json=request).json()["alerts"] == []
    journal = client.get("/api/indicators/smt/history").json()
    for event in journal:
        assert event["capture"]["primary"]["sha256"]
        assert all(bar["time"] + 60 <= event["transitions"][-1]["time"] for bar in event["capture"]["primary"]["bars"])


def test_smt_verifies_multiple_archive_partitions(client):
    request = dict(RULES)
    for side, symbol, prices in [("primary","EURUSD",LEFT),("comparison","GBPUSD",RIGHT)]:
        sequence = candles(prices, 0)
        for part in (sequence[:5],sequence[5:]):
            write_partition("DUKASCOPY", "test", symbol, "candles", "M1", "BID", part)
        archive = saved_range("DUKASCOPY",symbol,"M1",0,600,60,600)
        request[side] = archive["candles"]
        request[f"{side}_feed"] = {"symbol":symbol,"source":"DUKASCOPY_SAVED","feed_id":"test","price_basis":"BID",
                                   "archive_range":[0,600],"partitions":archive["partitions"]}
    url = "/api/indicators/smt"
    response = client.post(url,json=request)
    assert response.status_code == 200, response.text
    assert response.json()["authority"] == "VERIFIED_SAVED_DATASET"
    assert response.json()["events"][0]["confirmed_at"] == 600
    request["comparison_feed"]["partitions"][0]["sha256"] = "incorrect"
    assert client.post(url,json=request).status_code == 409
