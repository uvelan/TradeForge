"""Public replay behavior when a selected source contains a malformed SQLite value."""
from contextlib import closing
from pathlib import Path
import sqlite3

import pytest
from fastapi.testclient import TestClient

from research_backend.app import db, storage
from tradeforge import catalog
from tradeforge.api import create_app
from tradeforge.database import open_database
from tradeforge.shadow_manifest import register

START = 1_577_836_800
STEP = 300
SESSIONS = "/research/api/replay/sessions"
FLAG = "TRADEFORGE_CANDLE_SHADOW_MANIFEST"
REASONS = {"first_time": "INVALID_UTC_TIMESTAMP", "last_time": "INVALID_UTC_TIMESTAMP",
           "rows_count": "ROW_COUNT_MISMATCH"}


def publish(symbol, offset):
    bar = dict(time=START + offset, open=1.10, high=1.12, low=1.09, close=1.11)
    part = storage.write_partition("DUKASCOPY", "fixture", symbol, "candles", "M5", "BID", [bar])
    return part, bar


def scenario(tmp_path, monkeypatch, field, shadow):
    root, rdb, market = tmp_path / "fixed", tmp_path / "research.sqlite", tmp_path / "market"
    monkeypatch.delenv(FLAG, raising=False)
    monkeypatch.setattr(db, "DATABASE", rdb)
    monkeypatch.setattr(storage, "MARKET_DIR", market)
    db.initialize()
    open_database(root).dispose()
    bad, _ = publish("EURUSD", STEP)
    good = publish("EURUSD", 0)
    peer = publish("GBPUSD", 0)
    with closing(sqlite3.connect(rdb)) as conn, conn:
        conn.execute(f"UPDATE datasets SET {field}=CAST({field} AS BLOB) WHERE id=?", (bad["id"],))
    manifest = tmp_path / "shadow.sqlite"
    assert register(root, rdb, market, manifest)["research"] == {"inspected": 3, "mapped": 2, "unresolved": 1}
    with closing(sqlite3.connect(manifest)) as conn:
        assert conn.execute("SELECT reason FROM mappings WHERE catalog='research' AND legacy_id=?",
                            (bad["id"],)).fetchone() == (REASONS[field],)
    if shadow:
        monkeypatch.setenv(FLAG, str(manifest))

    opened = []

    def track_open(path, *args, **kwargs):
        opened.append(Path(path).resolve())
        return open(path, *args, **kwargs)

    # Both readers authenticate and copy original file bytes using this open.
    monkeypatch.setattr(catalog, "open", track_open, raising=False)
    return bad, good, peer, opened


def session_count():
    with closing(sqlite3.connect(db.DATABASE)) as conn:
        return conn.execute("SELECT COUNT(*) FROM replay_sessions").fetchone()[0]


def replay(client, symbol, explicit, **extra):
    request = {"symbol": symbol, "timeframe": "M5", "source": "DUKASCOPY_SAVED", "window_bars": 5000}
    if explicit:
        request.update(start=START, end=START + 2 * STEP)
    return client.post(SESSIONS, json=request | extra)


def assert_neighbor(market, expected, explicit):
    part, bar = expected
    assert market["partitions"] == [{key: part[key] for key in ("id", "sha256")}]
    assert market["candles"] == [{**bar, "available_at": START + STEP,
                                  "availability_basis": "MODELED_NOMINAL_CLOSE", "source_partition_indexes": [0]}]
    assert market["replay_cutoffs"] == ([START + STEP, START + 2 * STEP] if explicit else [START + STEP])


@pytest.mark.parametrize("field", ["last_time", "rows_count"])
@pytest.mark.parametrize("explicit", [True, False], ids=["explicit", "recent"])
@pytest.mark.parametrize("shadow", [True, False], ids=["shadow", "default"])
def test_malformed_selected_replay_partition(tmp_path, monkeypatch, field, explicit, shadow):
    bad, _, good_peer, opened = scenario(tmp_path, monkeypatch, field, shadow)
    with closing(TestClient(create_app(), base_url="http://localhost", raise_server_exceptions=False)) as client:
        assert session_count() == 0
        primary = replay(client, "EURUSD", explicit)
        after_primary = session_count()
        companion = replay(client, "GBPUSD", explicit, views=[{"view": "2", "symbol": "EURUSD", "timeframe": "M5"}])
        after_companion = session_count()
    assert (storage.MARKET_DIR / bad["relative_path"]).resolve() not in opened
    assert (primary.status_code, after_primary, companion.status_code, after_companion) == (409, 0, 201, 1), (
        bad["id"], primary.text, companion.text
    )
    primary_reason = primary.json()["detail"]
    error_pane = companion.json()["views"]["2"]
    assert error_pane == {"symbol": "EURUSD", "timeframe": "M5", "error": primary_reason}
    assert bad["id"] in primary_reason
    if shadow:
        assert primary_reason == f"SHADOW_UNRESOLVED: {REASONS[field]} (research, {bad['id']})"
    else:
        assert primary_reason.startswith(REASONS[field] + ":")
        assert field in primary_reason
    assert_neighbor(companion.json()["market"], good_peer, explicit)
    assert (storage.MARKET_DIR / good_peer[0]["relative_path"]).resolve() in opened


@pytest.mark.parametrize("explicit", [True, False], ids=["explicit", "recent"])
@pytest.mark.parametrize("shadow", [True, False], ids=["shadow", "default"])
def test_blob_first_time_is_outside_native_sql_selection(tmp_path, monkeypatch, explicit, shadow):
    bad, good, _, opened = scenario(tmp_path, monkeypatch, "first_time", shadow)
    # SQLite orders BLOBs after TEXT. first_time < TEXT end excludes this row,
    # even when first_time's original ISO bytes describe an in-window candle.
    with closing(TestClient(create_app(), base_url="http://localhost")) as client:
        response = replay(client, "EURUSD", explicit)
    assert response.status_code == 201, response.text
    assert_neighbor(response.json()["market"], good, explicit)
    assert session_count() == 1
    assert (storage.MARKET_DIR / bad["relative_path"]).resolve() not in opened

@pytest.mark.parametrize("shadow", [False, True], ids=["default", "shadow"])
@pytest.mark.parametrize("upper", ["before", "equal", "malformed"])
def test_full_recent_window_selection_boundary(tmp_path, monkeypatch, shadow, upper):
    root, market_dir = tmp_path / "fixed", tmp_path / "market"
    monkeypatch.delenv(FLAG, raising=False)
    monkeypatch.setattr(db, "DATABASE", tmp_path / "catalog.sqlite")
    monkeypatch.setattr(storage, "MARKET_DIR", market_dir)
    db.initialize()
    open_database(root).dispose()
    minute = 60
    bars = [dict(time=START + i * minute, open=1.10, high=1.12, low=1.09, close=1.11)
            for i in range(5000)]
    def write(records):
        return storage.write_partition("DUKASCOPY", "fixture", "EURUSD", "candles", "M1", "BID", records)

    good = write(bars)
    bad_time = START if upper == "equal" else START - minute
    bad = write([{**bars[0], "time": bad_time}])
    with closing(sqlite3.connect(db.DATABASE)) as conn, conn:
        conn.execute("UPDATE datasets SET rows_count=CAST(rows_count AS BLOB) WHERE id=?", (bad["id"],))
        if upper == "malformed":
            # Text sorts after the newer partition in this descending scan, but lacks a UTC offset.
            conn.execute("UPDATE datasets SET last_time='2019-12-31T23:59:00' WHERE id=?", (bad["id"],))
    manifest = tmp_path / "manifest.sqlite"
    assert register(root, db.DATABASE, market_dir, manifest)["research"] == {
        "inspected": 2, "mapped": 1, "unresolved": 1}
    if shadow:
        monkeypatch.setenv(FLAG, str(manifest))

    opened = []
    def track_open(path, *args, **kwargs):
        opened.append(Path(path).resolve())
        return open(path, *args, **kwargs)

    monkeypatch.setattr(catalog, "open", track_open, raising=False)
    assert session_count() == 0
    with closing(TestClient(create_app(), base_url="http://localhost", raise_server_exceptions=False)) as client:
        response = replay(client, "EURUSD", False, timeframe="M1")
    assert (market_dir / bad["relative_path"]).resolve() not in opened
    assert (market_dir / good["relative_path"]).resolve() in opened
    if upper == "before":
        assert response.status_code == 201, (response.text, session_count())
        assert session_count() == 1
        market = response.json()["market"]
        assert market["partitions"] == [{"id": good["id"], "sha256": good["sha256"]}]
        assert market["candles"] == [
            {**bar, "available_at": bar["time"] + minute, "availability_basis": "MODELED_NOMINAL_CLOSE",
             "source_partition_indexes": [0]} for bar in bars]
        assert market["replay_cutoffs"] == [bar["time"] + minute for bar in bars]
        assert market["coverage"] == {"start": START, "end": START + 5000 * minute, "gaps": [],
                                      "duplicate_bars": 0, "truncated": True}
    else:
        assert response.status_code == 409, response.text
        assert session_count() == 0
        detail = response.json()["detail"]
        assert bad["id"] in detail
        assert ("ROW_COUNT_MISMATCH" if upper == "equal" else "INVALID_UTC_TIMESTAMP") in detail
