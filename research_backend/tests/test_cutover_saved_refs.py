"""Positive evidence/export and preserved dangling-receipt controls."""
from contextlib import closing
from types import ModuleType
import subprocess

import pytest
from fastapi.testclient import TestClient

from research_backend.app import db, domain, storage
from research_backend.app.main import app
from scripts.t04_cutover_rehearsal import DATABASES, reconcile, snapshot
from tradeforge import publication
from tradeforge.database import open_database
from test_smt_authority import LEFT, RIGHT, RULES, candles


def test_restored_signal_export_and_historical_dangling_receipt(tmp_path, monkeypatch, plan):
    source = tmp_path / "source"
    open_database(source).dispose()
    research = source / "research"
    research.mkdir()
    monkeypatch.setattr(db, "DATABASE", source / DATABASES[1])
    monkeypatch.setattr(storage, "MARKET_DIR", research / "market")
    monkeypatch.setattr(domain, "EXPORT_DIR", research / "ai-exports")
    domain.EXPORT_DIR.mkdir()
    db.initialize()
    request = dict(RULES)
    for lane, symbol, prices in (("primary", "EURUSD", LEFT), ("comparison", "GBPUSD", RIGHT)):
        bars = candles(prices, 0)
        partition = storage.write_partition("DUKASCOPY", "test", symbol, "candles", "M1", "BID", bars)
        request[lane] = bars
        request[lane + "_feed"] = dict(symbol=symbol, source="DUKASCOPY_SAVED", price_basis="BID",
                                       feed_id="test", dataset_id=partition["id"])
    with closing(TestClient(app, base_url="http://localhost")) as client:
        result = client.post("/api/indicators/smt", json=request)
        assert result.status_code == 200, result.text
        event = result.json()["events"][0]
        trade = client.post("/api/trades", json={**plan, "timeframe": "M1", "signal_reference": {
            "event_key": event["event_key"], "status": event["status"]}})
        assert trade.status_code == 201, trade.text
        issued = client.post("/api/ai/exports/" + trade.json()["id"])
        assert issued.status_code < 300, issued.text
    module = ModuleType("research_backend.app.phase_c_reviewed_storage")
    module.__package__ = "research_backend.app"
    name = "research_backend/app/storage.py"
    code = subprocess.check_output(["git", "show", "d77143fa7e0b73acbfd3c74dd61c51b112b908a0:" + name])
    exec(compile(code, name, "exec"), module.__dict__)
    module.MARKET_DIR = storage.MARKET_DIR

    def fail(*args, **kwargs):
        raise OSError("injected staging failure")

    with db.transaction() as conn, monkeypatch.context() as patch:
        patch.setattr(publication.shutil, "copyfileobj", fail)
        with pytest.raises(OSError, match="staging failure"):
            module.write_partition("DUKASCOPY", "torn", "EURUSD", "candles", "M1", "BID",
                                   candles(LEFT, 0), connection=conn)
    restored = tmp_path / "restore"
    snapshot(source, restored)
    result = reconcile(restored)
    assert result == reconcile(source)
    assert result["counts"]["smt_refs"] >= 2
    assert result["counts"]["exports"] == 1
    assert result["counts"]["receipts"] == 3 and result["counts"]["research"] == 2
    dangling, = result["dangling_receipts"]
    assert dangling[0] == "research"
