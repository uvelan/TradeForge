"""Positive evidence/export and preserved dangling-receipt controls."""
from contextlib import closing
from types import ModuleType
import json
import sqlite3
import subprocess

import pytest
from fastapi.testclient import TestClient

from research_backend.app import db, domain, storage
from research_backend.app.main import app
from scripts.t04_cutover_rehearsal import DATABASES, inventory, reconcile, snapshot
from tradeforge import publication
from tradeforge.database import open_database
from test_smt_authority import LEFT, RIGHT, RULES, candles


@pytest.fixture
def saved_signal(tmp_path, monkeypatch):
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
        url = "/api/indicators/signals/" + event["event_key"] + "/candles"
        response = client.get(url, params={"status": "SMT_CONFIRMED"})
        assert response.status_code == 200, response.text
        assert response.json() == {lane: request[lane] for lane in ("primary", "comparison")}
    return source, event, url, response.json()


def test_restored_signal_export_and_historical_dangling_receipt(tmp_path, monkeypatch, plan, saved_signal):
    source, event, _, _ = saved_signal
    with closing(TestClient(app, base_url="http://localhost")) as client:
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


@pytest.mark.parametrize("bad_pin", [False, True], ids=["verified", "comparison-checksum-mismatch"])
def test_restored_single_partition_signal(tmp_path, monkeypatch, saved_signal, bad_pin):
    source, event, url, candles_before = saved_signal
    root = tmp_path / "restore"
    snapshot(source, root)
    monkeypatch.setattr(db, "DATABASE", root / DATABASES[1])
    monkeypatch.setattr(storage, "MARKET_DIR", root / "research/market")
    files = inventory(root)
    catalog = db.rows("SELECT * FROM datasets ORDER BY id")
    key = event["event_key"]
    status = "SMT_CONFIRMED"
    with closing(sqlite3.connect(db.DATABASE)) as conn, conn:
        query = "SELECT payload FROM smt_event_history WHERE event_key=? AND status=?"
        payload = json.loads(conn.execute(query, (key, status)).fetchone()[0])
        lane = payload["provenance"]["comparison"]
        dataset = next(row for row in catalog if row["id"] == lane["dataset_id"])
        assert lane["sha256"] == dataset["sha256"]
        if bad_pin:
            # Corrupt one field in the disposable restore, retaining the schema guards.
            trigger = conn.execute("SELECT sql FROM sqlite_master WHERE "
                                   "name='protect_smt_event_history_update'").fetchone()[0]
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DROP TRIGGER protect_smt_event_history_update")
            conn.execute("UPDATE smt_event_history SET payload=json_set(payload, "
                         "'$.provenance.comparison.sha256', ?) WHERE event_key=? AND status=?",
                         ("0" * 64, key, status))
            conn.execute(trigger)
    with closing(TestClient(app, base_url="http://localhost")) as client:
        response = client.get(url, params={"status": status})
    assert inventory(root) == files and db.rows("SELECT * FROM datasets ORDER BY id") == catalog
    if bad_pin:
        assert response.status_code == 409 and "Original partition unavailable" in response.text
        with pytest.raises(ValueError) as failure:
            reconcile(root)
        assert all(value in str(failure.value) for value in (key, "comparison", dataset["id"]))
    else:
        assert response.status_code == 200 and response.json() == candles_before
        counts = reconcile(root)["counts"]
        assert counts["smt_revisions"] == 2 and counts["smt_refs"] == 4
        assert counts["unpinned_lanes"] == 0
