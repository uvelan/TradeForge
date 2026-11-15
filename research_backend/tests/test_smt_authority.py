from datetime import UTC, datetime
from time import monotonic, sleep

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

    def fetch(symbol, timeframe, limit, *, include_latest, provenance):
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


def test_worker_creates_and_retracts_alert_without_chart_requests(client, monkeypatch):
    close = (int(datetime.now(UTC).timestamp()) // 60 + 1) * 60
    start = close - 600
    clock = [close - 60]
    corrected = [False]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls.fromtimestamp(clock[0], tz)

    def fetch(symbol, timeframe, limit, *, include_latest, provenance):
        assert include_latest
        provenance.update(feed_id="mt5:test", price_basis="BID")
        values = list(LEFT if symbol == "EURUSD" else RIGHT)
        if corrected[0] and symbol == "GBPUSD":
            values[6] = 30
        return candles(values, start)

    def until(predicate):
        deadline = monotonic() + 8
        while monotonic() < deadline:
            if predicate():
                return
            sleep(0.05)
        raise AssertionError("Background SMT poll did not produce the expected state")

    monkeypatch.setattr(indicator_routes, "datetime", Clock)
    monkeypatch.setattr(indicator_routes, "MONITOR_INTERVAL_SECONDS", 0.05)
    monkeypatch.setattr(indicator_routes, "mt5_rates", fetch)
    path = "/api/indicators/smt"
    config = {**RULES, "primary_symbol": "EURUSD", "comparison_symbol": "GBPUSD"}
    assert client.post(path + "/monitor", json=config).json() == {"enabled": True}
    until(lambda: any(event["status"] == "SMT_CANDIDATE" for event in client.get(path + "/history").json()))
    assert client.get(path + "/alerts").json() == []
    clock[0] = close
    until(lambda: bool(client.get(path + "/alerts").json()))
    alert, = client.get(path + "/alerts").json()
    assert alert["confirmed_at"] == close
    assert client.get(path + "/monitor").json()["latest_alert"]["event_key"] == alert["event_key"]

    corrected[0] = True
    clock[0] += 1
    until(lambda: not client.get(path + "/alerts").json())
    journal = client.get(path + "/history").json()
    correction, = [event for event in journal if event["status"] == "SMT_SOURCE_CORRECTED"]
    assert correction["event_key"] == alert["event_key"]
    assert correction["confirmed_at"] == alert["confirmed_at"]
    assert correction["source_correction"]["previous_status"] == "SMT_CONFIRMED"
    change, = correction["source_correction"]["changes"]
    assert change["side"] == "comparison" and change["time"] == start + 360
    assert (change["previous"]["high"], change["observed"]["high"]) == (28, 31)
    assert any(event["status"] == "SMT_CONFIRMED" for event in journal)
    assert client.post(path + "/monitor", json={**config, "enabled": False}).json() == {"enabled": False}
    assert client.get(path + "/monitor").json()["configuration"] is None
