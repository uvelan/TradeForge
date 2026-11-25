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
    missing = [False]

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
        bars = candles(values, start)
        if missing[0] and symbol == "GBPUSD":
            bars.pop(5)
        return bars

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

    before_gap = client.get(path + "/history").json()
    missing[0] = True
    clock[0] += 1
    until(lambda: client.get(path + "/monitor").json().get("coverage", {}).get("status") == "DEGRADED")
    assert client.get(path + "/history").json() == before_gap
    missing[0] = False
    clock[0] += 1
    until(lambda: client.get(path + "/monitor").json().get("coverage", {}).get("status") == "CONTIGUOUS")
    assert client.get(path + "/alerts").json() == [alert]
    assert client.get(path + "/history").json() == before_gap

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


def test_signal_explorer_returns_latest_and_pins_requested_revision(client, plan):
    import io
    import json
    import zipfile

    import hashlib
    import subprocess
    from types import ModuleType
    from research_backend.app import storage
    path = "research_backend/app/storage.py"
    legacy = ModuleType("research_backend.app.phase_b_storage")
    legacy.__package__ = "research_backend.app"
    source = subprocess.check_output(["git","show","a632a002d82162a50079c188aba1a5e507ccc2c4:"+path])
    exec(compile(source,path,"exec"),legacy.__dict__)
    legacy.MARKET_DIR = storage.MARKET_DIR
    historical = []
    request = dict(RULES)
    for side, symbol, prices in [("primary","EURUSD",LEFT+[12]),("comparison","GBPUSD",RIGHT+[31])]:
        sequence = candles(prices, 0)
        dataset = legacy.write_partition("DUKASCOPY", "test", symbol, "candles", "M1", "BID", sequence)
        historical.append((symbol, sequence, dataset))
        request[side] = sequence
        request[f"{side}_feed"] = {"symbol":symbol,"source":"DUKASCOPY_SAVED","feed_id":"test","price_basis":"BID",
                                  "dataset_id":dataset["id"]}
        coverage = client.get(f"/api/datasets/{dataset['id']}/coverage").json()
        assert coverage["coverage"]["gaps"] == []
        assert coverage["candles"][-1]["time"] == sequence[-1]["time"]
    base = "/api/indicators"
    assert client.post(base+"/smt",json=request).status_code == 200
    page = client.get(base+"/signals",params={"direction":"bearish","instrument":"GBPUSD"}).json()
    signal, = page["items"]
    assert signal["status"] == "SMT_INVALIDATED"
    assert signal["confirmed_at"] == 600
    key = signal["event_key"]
    original = client.get(f"{base}/signals/{key}/candles",params={"status":"SMT_CONFIRMED"}).json()
    assert original["primary"][-1]["time"] == 540
    link = {"event_key":key,"status":"SMT_INVALIDATED"}
    created = client.post("/api/trades",json={**plan,"timeframe":"M1","signal_reference":link})
    assert created.status_code == 201, created.text
    assert created.json()["versions"][0]["plan"]["signal_reference"] == link
    trade_id = created.json()["id"]
    assert client.post("/api/ai/exports/"+trade_id, params={"plan_version": 2}).status_code == 409
    package = client.post("/api/ai/exports/"+trade_id, params={"plan_version": 1}).json()
    response = client.get(f"/api/ai/packages/{package['package_id']}/download")
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert json.loads(archive.read("data/signal.json"))["event_key"] == key
    for symbol, sequence, dataset in historical:
        artifact = storage.MARKET_DIR/dataset["relative_path"]
        assert hashlib.sha256(artifact.read_bytes()).hexdigest() == dataset["sha256"]
        assert write_partition("DUKASCOPY","test",symbol,"candles","M1","BID",sequence) == dataset
        assert hashlib.sha256(artifact.read_bytes()).hexdigest() == dataset["sha256"]
        print("historical_signal_partition", symbol, dataset["id"], dataset["sha256"])
    assert client.get(f"{base}/signals/{key}/candles",params={"status":"SMT_CONFIRMED"}).json() == original
    replayed = client.get(f"/api/ai/packages/{package['package_id']}/download")
    assert replayed.content == response.content
    assert hashlib.sha256(replayed.content).hexdigest() == package["sha256"]
    assert client.get(base+"/signals",params={"q":trade_id}).json()["items"][0]["event_key"] == key
    journal = client.post("/api/journal", json={"kind":"CHART","title":"SMT observation",
                          "content":"The later candle invalidated the signal.", "signal_reference":link})
    assert journal.status_code == 201, journal.text
    assert client.get("/api/journal").json()[0]["signal_reference"] == link
    assert client.get(f"{base}/signals/{key}",params={"status":link["status"]}).json()["status"] == link["status"]
    assert key in client.get("/api/journal/download").text


def test_invalidated_alert_is_not_active(client):
    import json

    from research_backend.app.db import transaction

    with transaction() as db:
        db.execute("INSERT INTO smt_alerts VALUES ('event','scope',60,?)", (json.dumps({"confirmed_at":60}),))
        db.execute("INSERT INTO smt_event_history VALUES ('event','scope','SMT_INVALIDATED',120,'{}')")
    assert client.get("/api/indicators/smt/alerts").json() == []


def test_signal_keyset_paginates_tied_transitions(client):
    import json

    from research_backend.app.db import transaction

    keys = [f"{i:064x}" for i in range(3)]
    with transaction() as db:
        for key in reversed(keys):
            db.execute("INSERT INTO smt_event_history VALUES (?,?,?,?,?)",
                       (key, "scope", "SMT_CANDIDATE", 60, json.dumps({"event_key": key})))
    path = "/api/indicators/signals"
    first = client.get(path, params={"limit": 2}).json()
    second = client.get(path, params={"limit": 2, "cursor": first["next_cursor"]}).json()
    assert [row["event_key"] for row in first["items"] + second["items"]] == keys
    assert second["next_cursor"] is None
    assert client.get(path, params={"cursor": "1:"+"z"*64}).status_code == 422


def test_live_gap_recovers_without_rewriting_prior_alert(client, monkeypatch):
    close = int(datetime.now(UTC).timestamp()) // 60 * 60
    start = close - 600
    mode = "complete"

    def fetch(symbol, timeframe, limit, *, include_latest, provenance):
        provenance.update(feed_id="mt5:gap-fixture", price_basis="BID")
        bars = candles(LEFT if symbol == "EURUSD" else RIGHT, start)
        if symbol == "GBPUSD" and mode == "interior":
            bars.pop(5)
        elif symbol == "GBPUSD" and mode == "changed":
            bars[6]["high"] += 2
            bars.pop(5)
        return bars

    monkeypatch.setattr(indicator_routes, "mt5_rates", fetch)
    base = "/api/indicators/smt"
    request = {**RULES, "primary_symbol": "EURUSD", "comparison_symbol": "GBPUSD",
               "available_through": close, "alert_after": close-120}
    initial = client.post(base+"/live", json=request).json()
    alert, = initial["alerts"]
    original_history = client.get(base+"/history").json()

    mode = "interior"
    gap = client.post(base+"/live", json=request).json()
    assert client.get(base+"/history").json() == original_history
    assert gap["coverage"]["status"] == "DEGRADED"
    assert gap["alerts"] == []

    mode = "complete"
    recovered = client.post(base+"/live", json=request).json()
    assert recovered["coverage"]["status"] == "CONTIGUOUS"
    assert recovered["alerts"] == []
    assert client.get(base+"/history").json() == original_history
    assert client.get(base+"/alerts").json()[0]["event_key"] == alert["event_key"]

    mode = "changed"
    changed = client.post(base+"/live", json=request).json()
    assert changed["coverage"]["status"] == "DEGRADED"
    assert client.get(base+"/alerts").json() == []
    corrections = [e for e in client.get(base+"/history").json() if e["status"] == "SMT_SOURCE_CORRECTED"]
    correction, = corrections
    assert correction["event_key"] == alert["event_key"]
    change, = correction["source_correction"]["changes"]
    assert change["time"] == start+360 and change["observed"] is not None


def test_live_gap_cannot_replace_missing_confirmation_or_use_stale_tail(client, monkeypatch):
    now = int(datetime.now(UTC).timestamp()) // 60 * 60
    start = now - 660
    mode = "interior"

    def fetch(symbol, timeframe, limit, *, include_latest, provenance):
        provenance.update(feed_id="mt5:gap-confirmation", price_basis="BID")
        bars = candles((LEFT if symbol == "EURUSD" else RIGHT)+[11 if symbol == "EURUSD" else 24], start)
        if symbol == "GBPUSD" and mode == "interior":
            bars.pop(9)
        elif mode == "stale_both" or (symbol == "GBPUSD" and mode == "stale"):
            bars = bars[:10]
        return bars

    monkeypatch.setattr(indicator_routes, "mt5_rates", fetch)
    base = "/api/indicators/smt"
    request = {**RULES, "primary_symbol": "EURUSD", "comparison_symbol": "GBPUSD",
               "available_through": now, "alert_after": now-120}
    result = client.post(base+"/live", json=request).json()
    assert result["alerts"] == []
    assert not any(e["status"] == "SMT_CONFIRMED" for e in client.get(base+"/history").json())
    assert result["coverage"]["status"] == "DEGRADED"

    for mode in ("stale", "stale_both"):
        result = client.post(base+"/live", json=request).json()
        assert result["alerts"] == []
        assert result["coverage"]["reason"] == "PAIRED_TAIL_UNAVAILABLE"

    mode = "complete"
    result = client.post(base+"/live", json=request).json()
    alert, = result["alerts"]
    assert alert["confirmed_at"] == now-60
    assert alert["alert_recorded_at"] >= now
