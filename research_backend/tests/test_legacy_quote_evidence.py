from datetime import UTC, datetime
import hashlib
import json

from research_backend.app.db import one, transaction
from research_backend.app.storage import write_partition
from test_tick_replay import inputs, replay


def test_saved_v2_quote_evidence_does_not_change_new_v3_validation(client):
    candles, ticks = inputs()
    start = int(datetime(2025, 3, 5, tzinfo=UTC).timestamp())
    for candle in candles:
        candle["time"] += start
    candles[3].update(high=106, low=98)
    for quote in ticks:
        quote["time_ms"] += start * 1000
        quote["time"] = quote["time_ms"] // 1000
    write_partition("DUKASCOPY", "legacy-quotes", "EURUSD", "candles", "M1", "BID", candles)
    partitions = [
        write_partition("DUKASCOPY", "legacy-quotes", "EURUSD", "ticks", None, "BID_ASK_UNITS", shard)
        for shard in (ticks[:3], ticks[3:])
    ]
    result = replay(candles, ticks)
    assert result["execution_model"]["version"] == 2
    assert len(result["trades"]) == 1
    result["lineage"] = {"tick_partitions": [{k: part[k] for k in ("id", "sha256")} for part in partitions]}
    payload = json.dumps({"result": result}, sort_keys=True, separators=(",", ":"), allow_nan=False)
    run = hashlib.sha256(payload.encode()).hexdigest()
    with transaction() as db:
        db.execute("INSERT INTO backtest_runs VALUES (?,?,?,?)", (run, "{}", payload, "2025-03-05T00:00:00Z"))

    route = f"/api/backtest/runs/{run}/trades/0/quotes"
    first, second = [client.get(route, params={"limit": 1, "offset": offset}) for offset in (0, 1)]
    assert first.status_code == second.status_code == 200
    assert first.headers["X-TradeForge-Quote-Order"] == "historical-v2"
    assert first.json() == {"items": [ticks[1]], "total": 2, "next_offset": 1}
    assert second.json() == {"items": [ticks[2]], "total": 2, "next_offset": None}
    assert one("SELECT payload FROM backtest_runs WHERE id=?", (run,))["payload"] == payload
    assert client.get("/api/backtest/runs/" + run).json()["result"] == result

    params = {"source": "DUKASCOPY_SAVED", "timeframe": "M1", "tick_archive_range": True}
    fresh = client.post("/api/backtest/runs", json=params)
    assert fresh.status_code == 422 and "Ambiguous tick source order" in fresh.json()["detail"]

    with transaction() as db:
        db.execute("UPDATE datasets SET sha256=? WHERE id=?", ("0" * 64, partitions[0]["id"]))
    assert client.get(route).status_code == 409
    assert one("SELECT payload FROM backtest_runs WHERE id=?", (run,))["payload"] == payload
