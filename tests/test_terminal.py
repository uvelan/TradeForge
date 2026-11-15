from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from tradeforge.api import create_app
from tradeforge.catalog import Catalog, validate
from tradeforge.database import open_database
from tradeforge.market import Candle, MT5Connection, MT5Unavailable, Simulator, Timeframe


def test_simulation_persists_and_excludes_unfinished_candles(tmp_path):
    with TestClient(create_app(tmp_path), base_url="http://localhost") as client:
        assert client.get("/api/health").json()["status"] == "ready"
        response = client.post("/api/datasets/simulation", json={})
        assert response.status_code == 200
        dataset = response.json()
        assert client.post("/api/datasets/simulation", json={}).json() == dataset
        assert len(client.get("/api/datasets").json()) == 1

    with TestClient(create_app(tmp_path), base_url="http://localhost") as client:
        url = "/api/datasets/" + dataset["id"]
        series = client.get(url).json()
        assert series["dataset"]["provider"] == "simulator"
        assert len(series["candles"]) == 192
        first = series["candles"][0]
        assert client.get(url, params={"cutoff": first["available_at"] - 1}).json()["candles"] == []
        assert client.get(url, params={"cutoff": first["available_at"]}).json()["candles"] == [first]
        path = tmp_path / series["dataset"]["relative_path"]
        path.write_bytes(b"corrupt")
        assert client.get(url).status_code == 409


def test_rejects_cross_origin_mutation(tmp_path):
    with TestClient(create_app(tmp_path), base_url="http://localhost") as client:
        response = client.post("/api/datasets/simulation", json={}, headers={"Origin": "https://example.org"})
        assert response.status_code == 403
        assert client.get("/api/datasets").json() == []


def test_concurrent_ingestion_publishes_one_verified_dataset(tmp_path):
    engine = open_database(tmp_path)
    try:
        catalog = Catalog(tmp_path, engine)

        def ingest():
            return catalog.ingest(Simulator(), "EURUSD", Timeframe.M1, 900, 1800)

        with ThreadPoolExecutor(max_workers=2) as executor:
            first, second = [executor.submit(ingest) for _ in range(2)]
            assert first.result() == second.result()
        assert len(catalog.list()) == 1
        assert len(catalog.read(first.result(), None)["candles"]) == 15
        assert list(tmp_path.rglob("*.tmp")) == []
    finally:
        engine.dispose()


def test_publication_failure_rolls_back_and_can_be_retried(tmp_path, monkeypatch):
    from tradeforge import catalog as catalog_module

    engine = open_database(tmp_path)
    try:
        catalog = Catalog(tmp_path, engine)
        args = (Simulator(), "EURUSD", Timeframe.M1, 900, 1800)

        def fail_publication(*_args):
            raise OSError("simulated interruption")

        with monkeypatch.context() as patch:
            patch.setattr(catalog_module.os, "replace", fail_publication)
            with pytest.raises(OSError, match="simulated interruption"):
                catalog.ingest(*args)
        assert catalog.list() == []
        assert list(tmp_path.rglob("*.tmp")) == []
        assert len(catalog.read(catalog.ingest(*args), None)["candles"]) == 15
    finally:
        engine.dispose()


@pytest.mark.parametrize("rows", [
    [],
    [Candle(900, 1800, 2, 3, 0, 2)],
    [Candle(900, 1799, 2, 3, 1, 2)],
    [Candle(901, 1801, 2, 3, 1, 2)],
])
def test_invalid_candle_series(rows):
    with pytest.raises(ValueError):
        validate(rows, 900, 1800, 900)


def test_mt5_reconnects_and_uses_utc_half_open_range():
    attempts = iter([False, True, True])
    shutdowns = []
    calls = []

    def fetch(symbol, timeframe, start, end):
        calls.append((symbol, timeframe, start.isoformat(), end.isoformat()))
        return [{"time": 900, "open": 1.05, "high": 1.06, "low": 1.04, "close": 1.055}]

    module = SimpleNamespace(
        initialize=lambda *args, **kwargs: next(attempts),
        terminal_info=lambda: SimpleNamespace(connected=True),
        shutdown=lambda: shutdowns.append(True),
        account_info=lambda: SimpleNamespace(server="Test server"),
        symbol_info=lambda symbol: SimpleNamespace(chart_mode=0, digits=5),
        copy_rates_range=fetch,
        TIMEFRAME_M15=15,
    )
    adapter = MT5Connection(load_module=lambda: module)
    assert not adapter.connected()
    assert adapter.connected()
    assert adapter.historical_candles("EURUSD", Timeframe.M15, 900, 1800) == (
        "Test server", 100_000, [Candle(900, 1800, 105000, 106000, 104000, 105500)]
    )
    assert calls == [("EURUSD", 15, "1970-01-01T00:15:00+00:00", "1970-01-01T00:29:59+00:00")]
    assert len(shutdowns) == 2


def test_broker_import_retains_gaps_and_provider(tmp_path):
    broker = Mock(spec=MT5Connection)
    broker.historical_candles.return_value = (
        "Demo server", 100_000, [Candle(1800, 2700, 105000, 106000, 104000, 105500)]
    )
    body = {"symbol": "EURUSD.a", "timeframe": "M15", "start": 900, "end": 3600}
    with TestClient(create_app(tmp_path, broker), base_url="http://localhost") as client:
        response = client.post("/api/datasets/mt5", json=body)
        assert response.status_code == 200
        series = client.get("/api/datasets/" + response.json()["id"]).json()
        assert series["dataset"]["provider"] == "mt5"
        assert series["dataset"]["feed"] == "Demo server"
        assert series["dataset"]["validation_status"] == "VERIFIED_WITH_GAPS"
        assert [row["time"] for row in series["candles"]] == [1800]
        assert client.post("/api/datasets/mt5", json={**body, "timeframe": "H1"}).status_code == 422
        broker.historical_candles.assert_called_once_with("EURUSD.a", Timeframe.M15, 900, 3600)
        broker.historical_candles.side_effect = MT5Unavailable("MT5 terminal disconnected")
        assert client.post("/api/datasets/mt5", json=body).status_code == 503
        assert len(client.get("/api/datasets").json()) == 1
