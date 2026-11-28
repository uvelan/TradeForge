import asyncio

from fastapi.testclient import TestClient

from research_backend.app.main import app
from research_backend.app.mt5_stream import Mt5StreamHub


def _parse_sse_chunk(chunk: str) -> tuple[str, dict]:
    import json

    event_name = ""
    payload = {}
    for line in chunk.strip().splitlines():
        if line.startswith("event: "):
            event_name = line[7:]
        elif line.startswith("data: "):
            payload = json.loads(line[6:])
    return event_name, payload


def test_mt5_hub_shares_cache_across_subscribers_handles_revisions_errors_and_cleanup():
    async def scenario():
        bars = [
            {"time": 1700000400, "open": 1.08, "high": 1.081, "low": 1.079, "close": 1.0802, "volume": 10},
            {"time": 1700000700, "open": 1.0802, "high": 1.0815, "low": 1.08, "close": 1.0805, "volume": 12},
        ]
        quote_bid = {"EURUSD": 1.0805}
        candle_calls = 0
        fail_next = False

        def read_candles(symbol, timeframe, limit=10, *, include_latest=True, provenance=None):
            nonlocal candle_calls
            candle_calls += 1
            if fail_next:
                raise RuntimeError("MT5 terminal temporarily disconnected")
            if provenance is not None:
                provenance.update({"feed_id": "mt5:test-server", "price_basis": "BID"})
            return [dict(row) for row in bars]

        def read_quote(symbol):
            return {"bid": quote_bid.get(symbol, 1.25), "ask": 1.2501}

        hub = Mt5StreamHub(
            candle_reader=read_candles,
            quote_reader=read_quote,
            cadence_seconds=0.05,
            cache_ttl_seconds=0.04,
        )

        gen1 = await hub.open_stream(
            symbol="EURUSD", timeframe="M5", limit=10, include_latest=True, quote_symbols=("EURUSD",)
        )
        gen2 = await hub.open_stream(
            symbol="EURUSD", timeframe="M5", limit=10, include_latest=True, quote_symbols=("EURUSD",)
        )
        # Second subscriber hits the short-TTL snapshot cache instead of polling MT5 again.
        assert candle_calls == 1
        assert hub.subscriber_count == 2
        assert hub.worker_running is True

        ev1, p1 = _parse_sse_chunk(await anext(gen1))
        ev2, p2 = _parse_sse_chunk(await anext(gen2))
        assert ev1 == "snapshot"
        assert ev2 == "snapshot"
        assert p1["candles"]["candles"][-1]["close"] == 1.0805
        assert p2["quotes"]["EURUSD"] == 1.0805

        # Same-bar revision pushes to both subscribers cleanly.
        bars[-1]["close"] = 1.0811
        ev1_rev, p1_rev = _parse_sse_chunk(await anext(gen1))
        ev2_rev, p2_rev = _parse_sse_chunk(await anext(gen2))
        assert ev1_rev == "candles" and ev2_rev == "candles"
        assert p1_rev["candles"]["candles"][-1]["close"] == 1.0811
        assert p2_rev["candles"]["candles"][-1]["close"] == 1.0811

        # New bar + quote update.
        bars.append({"time": 1700001000, "open": 1.0811, "high": 1.082, "low": 1.081, "close": 1.0818, "volume": 5})
        quote_bid["EURUSD"] = 1.0818
        ev1_new, p1_new = _parse_sse_chunk(await anext(gen1))
        assert ev1_new == "candles"
        assert len(p1_new["candles"]["candles"]) == 3
        ev1_q, p1_q = _parse_sse_chunk(await anext(gen1))
        assert ev1_q == "quote"
        assert p1_q["quotes"]["EURUSD"] == 1.0818

        # Mid-stream MT5 disconnect emits recoverable error, then reconnect recovers.
        fail_next = True
        ev1_err, p1_err = _parse_sse_chunk(await anext(gen1))
        assert ev1_err == "mt5_error"
        assert "disconnected" in p1_err["error"]

        fail_next = False
        bars[-1]["close"] = 1.0819
        ev1_rec, p1_rec = _parse_sse_chunk(await anext(gen1))
        assert ev1_rec == "candles"
        assert p1_rec["candles"]["candles"][-1]["close"] == 1.0819

        # Cleanup on unsubscribe stops the background worker task.
        await gen1.aclose()
        await gen2.aclose()
        assert hub.subscriber_count == 0
        assert hub.worker_running is False

    asyncio.run(scenario())


def test_mt5_stream_endpoint_returns_503_when_mt5_unavailable(monkeypatch):
    def fail_rates(*args, **kwargs):
        raise RuntimeError("MT5 terminal not connected")

    monkeypatch.setattr("research_backend.app.mt5_stream.mt5_stream_hub._candle_reader", fail_rates)
    client = TestClient(app)
    res = client.get("/api/market/mt5/stream?symbol=EURUSD&timeframe=M5")
    assert res.status_code == 503
    assert "MT5 terminal not connected" in res.json()["detail"]
