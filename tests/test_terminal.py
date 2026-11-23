import builtins
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, update

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


@pytest.mark.parametrize("damage", ["corrupt", "missing"])
@pytest.mark.parametrize("endpoint", ["simulation", "mt5"])
def test_duplicate_requires_intact_registered_artifact(tmp_path, damage, endpoint):
    broker = Mock(spec=MT5Connection)
    broker.historical_candles.return_value = (
        "Demo server", 100_000, [Candle(900, 1800, 105000, 106000, 104000, 105500)]
    )
    body = {} if endpoint == "simulation" else {
        "symbol": "EURUSD", "timeframe": "M15", "start": 900, "end": 1800,
    }
    route = "/api/datasets/" + endpoint
    with TestClient(create_app(tmp_path, broker), base_url="http://localhost") as client:
        first = client.post(route, json=body)
        assert first.status_code == 200
        entries = client.get("/api/datasets").json()
        assert len(entries) == 1 and entries[0]["id"] == first.json()["id"]
        path = tmp_path / entries[0]["relative_path"]
        assert client.post(route, json=body).json() == first.json()
        assert client.get("/api/datasets").json() == entries

        if damage == "corrupt":
            path.write_bytes(b"corrupt")
        else:
            path.unlink()

        duplicate = client.post(route, json=body)
        assert duplicate.status_code == 409
        assert duplicate.json() == {"detail": "Dataset integrity check failed"}
        assert client.get("/api/datasets").json() == entries
        assert client.get("/api/datasets/" + first.json()["id"]).status_code == 409
        if damage == "corrupt":
            assert path.read_bytes() == b"corrupt"
        else:
            assert not path.exists()
        assert list(tmp_path.rglob("*.tmp")) == []


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


@pytest.mark.parametrize("conflicting", [False, True], ids=["exact", "conflicting"])
def test_publication_preserves_file_only_orphan(tmp_path, monkeypatch, conflicting):
    from tradeforge import catalog as catalog_module

    engine = open_database(tmp_path)
    try:
        catalog = Catalog(tmp_path, engine)
        args = (Simulator(), "EURUSD", Timeframe.M1, 900, 1800)
        dataset_id = catalog.ingest(*args)
        entry, = catalog.list()
        path = tmp_path / entry["relative_path"]
        if conflicting:
            path.write_bytes(b"different bytes")
        original = path.read_bytes()
        with engine.begin() as connection:
            connection.execute(delete(catalog.table).where(catalog.table.c.id == dataset_id))
        replace = Mock(wraps=catalog_module.os.replace)
        monkeypatch.setattr(catalog_module.os, "replace", replace)
        if conflicting:
            with pytest.raises(ValueError, match="Dataset checksum mismatch"):
                catalog.ingest(*args)
            assert catalog.list() == []
        else:
            assert catalog.ingest(*args) == dataset_id
            assert len(catalog.read(dataset_id, None)["candles"]) == 15
        replace.assert_not_called()
        assert path.read_bytes() == original
        assert list(tmp_path.rglob("*.tmp")) == []
    finally:
        engine.dispose()


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX FIFO fixture")
def test_duplicate_rejects_nonregular_artifact_without_opening(tmp_path, monkeypatch):
    engine = open_database(tmp_path)
    try:
        catalog = Catalog(tmp_path, engine)
        args = (Simulator(), "EURUSD", Timeframe.M1, 900, 1800)
        catalog.ingest(*args)
        entry, = catalog.list()
        path = tmp_path / entry["relative_path"]
        path.unlink()
        os.mkfifo(path)

        def guard(original):
            def fail_if_opened(filename, *args, **kwargs):
                if isinstance(filename, (str, os.PathLike)) and Path(filename) == path:
                    pytest.fail("An invalid catalog node must never be opened")
                return original(filename, *args, **kwargs)
            return fail_if_opened

        monkeypatch.setattr(builtins, "open", guard(builtins.open))
        monkeypatch.setattr(Path, "open", guard(Path.open))
        with pytest.raises(ValueError, match="not a regular file"):
            catalog.ingest(*args)
        assert path.is_fifo()
        assert catalog.list() == [entry]
        assert list(tmp_path.rglob("*.tmp")) == []
    finally:
        engine.dispose()


def test_duplicate_rejects_path_outside_artifact_root(tmp_path):
    engine = open_database(tmp_path)
    try:
        catalog = Catalog(tmp_path, engine)
        args = (Simulator(), "EURUSD", Timeframe.M1, 900, 1800)
        dataset_id = catalog.ingest(*args)
        entry, = catalog.list()
        path = tmp_path / entry["relative_path"]
        outside = tmp_path / "outside.parquet"
        outside.write_bytes(path.read_bytes())
        with engine.begin() as connection:
            connection.execute(update(catalog.table).where(catalog.table.c.id == dataset_id)
                               .values(relative_path=outside.name))
        with pytest.raises(ValueError, match="Invalid catalog path"):
            catalog.ingest(*args)
        assert outside.read_bytes() == path.read_bytes()
        assert catalog.list()[0]["relative_path"] == outside.name
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
