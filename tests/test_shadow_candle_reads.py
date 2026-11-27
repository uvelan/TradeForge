"""HTTP-level parity on immutable local fixture catalogs."""
from contextlib import closing
import json
from pathlib import Path
import shutil
import sqlite3

import pytest
from fastapi.testclient import TestClient

from research_backend.app import db, storage
from research_backend.app.main import app as research_app
from tradeforge.api import create_app
from tradeforge.catalog import Catalog
from tradeforge.database import open_database
from tradeforge.market import Candle, Timeframe
from tradeforge.shadow_manifest import register
from test_shadow_manifest import clone_row, source_hashes

T = 36000  # 1970-01-01 10:00:00 UTC
FLAG = "TRADEFORGE_CANDLE_SHADOW_MANIFEST"


def fixed_bar(t):
    return Candle(time=t, available_at=t+300, open=110310, high=110312, low=110308, close=110311)


def research_bar(t):
    return dict(time=t, open=1.10310, high=1.10312, low=1.10308, close=1.10311)


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    monkeypatch.delenv(FLAG, raising=False)
    root, rdb, market = tmp_path/"fixed", tmp_path/"research.sqlite", tmp_path/"market"
    monkeypatch.setattr(db, "DATABASE", rdb)
    monkeypatch.setattr(storage, "MARKET_DIR", market)
    db.initialize()
    engine = open_database(root)
    try:
        catalog = Catalog(root,engine)
        fixed = catalog.publish("simulator","deterministic-eurusd-v1","synthetic_bid",100000,
                                "EURUSD",Timeframe.M5,T,T+600,[fixed_bar(T),fixed_bar(T+300)])
    finally:
        engine.dispose()
    part = storage.write_partition("DUKASCOPY","fixture","EURUSD","candles","M5","BID",
                                   [research_bar(T+300),research_bar(T)])
    return (root,rdb,market), fixed, part, tmp_path/"shadow.sqlite"


def request_range(client, start, end, **overrides):
    params = dict(source="DUKASCOPY_SAVED", symbol="EURUSD", timeframe="M5", start=start,end=end)
    return client.get("/api/market/candles",params=params | overrides)


def boundary(fixture):
    args, fixed, _, _ = fixture
    with TestClient(create_app(args[0]),base_url="http://localhost") as api:
        first, exact = [api.get(f"/api/datasets/{fixed}",params={"cutoff":t})
                        for t in (T+299,T+300)]
    with TestClient(research_app,base_url="http://localhost") as research:
        window = request_range(research,T,T+300)
    assert first.status_code == exact.status_code == window.status_code == 200
    assert first.json()["candles"] == []
    assert exact.json()["candles"] == [vars(fixed_bar(T))]
    assert window.json()["candles"] == [research_bar(T)]
    return [r.json() for r in (first,exact,window)]


def test_boundary_legacy(fixture):
    baseline = boundary(fixture)
    print(json.dumps([r["candles"] for r in baseline]))

def test_shadow_boundary(fixture, monkeypatch):
    args, _, _, manifest = fixture
    expected = boundary(fixture)
    register(*args,manifest)
    before = source_hashes(args)
    monkeypatch.setenv(FLAG,str(manifest))
    forbid_legacy(monkeypatch)
    assert boundary(fixture) == expected
    assert source_hashes(args) == before

@pytest.mark.parametrize("case", ["clean-disappears", "active-before", "active-after", "unreadable"])
def test_research_wal_observation_is_fail_closed(fixture, monkeypatch, case):
    from tradeforge import shadow_reads

    args, _, _, manifest = fixture
    _, database, _ = args
    # Exercise the HTTP route without a background reader racing the fixture.
    with closing(TestClient(research_app, base_url="http://localhost")) as client:
        legacy = request_range(client, T, T+300)
        assert legacy.status_code == 200
        register(*args, manifest)
        monkeypatch.setenv(FLAG, str(manifest))
        forbid_legacy(monkeypatch)

        wal = Path(str(database) + "-wal")
        removed = []
        if case == "clean-disappears":
            wal.touch(exist_ok=False)
            stat = Path.stat

            def close_reader(path, *args, **kwargs):
                result = stat(path, *args, **kwargs)
                if path == wal:
                    assert result.st_size == 0
                    path.unlink()
                    removed.append(path)
                return result

            monkeypatch.setattr(Path, "stat", close_reader)
        elif case == "active-before":
            wal.write_bytes(b"synthetic nonempty WAL")
        elif case == "active-after":
            verify = shadow_reads.verify_artifact

            def create_wal_after_verified_copy(path, *args, **kwargs):
                verify(path, *args, **kwargs)
                if path == database:
                    wal.write_bytes(b"synthetic nonempty WAL")

            monkeypatch.setattr(shadow_reads, "verify_artifact", create_wal_after_verified_copy)
        else:
            stat = Path.stat

            def unreadable(path, *args, **kwargs):
                if path == wal:
                    raise PermissionError("synthetic inaccessible WAL")
                return stat(path, *args, **kwargs)

            monkeypatch.setattr(Path, "stat", unreadable)

        result = request_range(client, T, T+300)
        if case == "clean-disappears":
            assert removed == [wal]
            assert result.status_code == 200, result.text
            assert result.json() == legacy.json()
        else:
            stage = "after" if case == "active-after" else "before"
            assert result.status_code == 409, result.text
            detail = result.json()["detail"]
            assert detail.startswith("SHADOW_SOURCE_SNAPSHOT_MISMATCH:")
            assert f"{stage} source copy" in detail


def fixed_request(args, key, **params):
    with TestClient(create_app(args[0]),base_url="http://localhost") as client:
        return client.get(f"/api/datasets/{key}",params=params)


def forbid_legacy(patch):
    def fallback(*args, **kwargs):
        pytest.fail("Opt-in request called the legacy artifact reader")
    patch.setattr(Catalog,"_verify_artifact",fallback)
    patch.setattr("research_backend.app.saved_history.read_partition",fallback)


def run_pair(request, manifest, monkeypatch):
    legacy = request()
    with monkeypatch.context() as scoped:
        scoped.setenv(FLAG,str(manifest))
        forbid_legacy(scoped)
        shadow = request()
    return legacy, shadow


@pytest.mark.parametrize("case", ["alias","other-feed","gap","unresolved","replacement",
                                  "snapshot","missing-mapping","wrong-revision"])
def test_fixed_matrix(fixture, monkeypatch, case):
    args, key, _, manifest = fixture
    root = args[0]
    database = root/"database/trading.sqlite3"
    with closing(sqlite3.connect(database)) as conn, conn:
        conn.row_factory = sqlite3.Row
        original = dict(conn.execute("SELECT * FROM dataset_versions WHERE id=?", (key,)).fetchone())
        if case in ("alias","other-feed","unresolved"):
            path = f"market-data/{case}.parquet"
            shutil.copyfile(root/original["relative_path"],root/path)
            changes = {"other-feed":{"feed":"different"}, "unresolved":{"price_basis":"unknown"}}.get(case,{})
            clone_row(conn,"dataset_versions",key,id=case,relative_path=path,**changes)
            key = case
    if case == "gap":
        engine = open_database(root)
        try:
            key = Catalog(root,engine).publish("mt5","fixture","bid",100000,"EURUSD",Timeframe.M5,
                T,T+900,[fixed_bar(T),fixed_bar(T+600)])
        finally:
            engine.dispose()
    register(*args,manifest)
    if case == "replacement":
        (root/original["relative_path"]).write_bytes(b"fixture replacement")
    elif case == "snapshot":
        with closing(sqlite3.connect(database)) as conn:
            conn.execute("CREATE TABLE new_source_revision(i)")
    elif case in ("missing-mapping","wrong-revision"):
        with closing(sqlite3.connect(manifest)) as conn, conn:
            if case == "missing-mapping":
                conn.execute("DELETE FROM mappings WHERE catalog='fixed' AND legacy_id=?",(key,))
            else:
                conn.execute("UPDATE artifacts SET contract='{}' WHERE revision IN "
                             "(SELECT revision FROM mappings WHERE catalog='fixed' AND legacy_id=?)",(key,))
    before = source_hashes(args)
    legacy, shadow = run_pair(lambda:fixed_request(args,key),manifest,monkeypatch)
    errors = {"unresolved":"UNSUPPORTED_BASIS","replacement":"checksum mismatch",
              "snapshot":"SHADOW_SOURCE_SNAPSHOT_MISMATCH","missing-mapping":"SHADOW_MAPPING_MISSING",
              "wrong-revision":"SHADOW_REVISION_MISMATCH"}
    if case in errors:
        assert legacy.status_code == (409 if case == "replacement" else 200)
        assert shadow.status_code == 409 and errors[case] in shadow.json()["detail"]
    else:
        assert shadow.status_code == legacy.status_code == 200
        assert shadow.json() == legacy.json()
        actual = shadow.json()
        assert actual["candles"] == [vars(fixed_bar(T)),vars(fixed_bar(T+(600 if case=="gap" else 300)))]
        assert actual["dataset"]["price_scale"] == 100000
        assert actual["dataset"]["id"] == key
        with closing(sqlite3.connect(manifest)) as conn:
            def rev(k):
                return conn.execute("SELECT revision FROM mappings WHERE catalog='fixed' AND legacy_id=?",
                                    (k,)).fetchone()[0]
            assert (rev(key) == rev(original["id"])) == (case == "alias")
    assert source_hashes(args) == before


@pytest.mark.parametrize("case", ["overlap-limit","recent","gap","conflicting-overlap","mixed-feed",
                                  "null-id","blob-basis","replacement","snapshot","missing-mapping"])
def test_research_matrix(fixture, monkeypatch, case):
    args, _, part, manifest = fixture
    _, database, market = args
    a, end = T, T+3900
    provider = "DUKASCOPY"
    def publish(times, feed="fixture", altered=False):
        values = [research_bar(t) for t in times]
        if altered:
            values[0]["close"] = values[0]["low"]
        return storage.write_partition(provider,feed,"EURUSD","candles","M5","BID",values)
    if case in ("overlap-limit","recent"):
        publish(range(T+300,end,300))
    elif case == "gap":
        publish([T+900])
    elif case in ("mixed-feed","conflicting-overlap"):
        publish([T+300],feed="different" if case=="mixed-feed" else "fixture",altered=True)
    elif case in ("null-id","blob-basis"):
        provider = "MT5"
        with closing(sqlite3.connect(database)) as conn, conn:
            conn.row_factory = sqlite3.Row
            path = f"{case}.parquet"
            shutil.copyfile(market/part["relative_path"],market/path)
            clone_row(conn,"datasets",part["id"],provider=provider,id=None if case=="null-id" else case,
                      price_basis=b"BID" if case=="blob-basis" else "BID",relative_path=path)
    register(*args,manifest)
    if case == "replacement":
        (market/part["relative_path"]).write_bytes(b"fixture replacement")
    elif case == "snapshot":
        with closing(sqlite3.connect(database)) as conn:
            conn.execute("CREATE TABLE new_source_revision(i)")
    elif case == "missing-mapping":
        with closing(sqlite3.connect(manifest)) as conn, conn:
            conn.execute("DELETE FROM mappings WHERE catalog='research'")
    before = source_hashes(args)
    def request():
        with TestClient(research_app,base_url="http://localhost") as client:
            if case == "recent":
                return client.get("/api/market/candles",params=dict(source="DUKASCOPY_SAVED",limit=10))
            return request_range(client,a,end,source=provider+"_SAVED",limit=10)
    legacy, shadow = run_pair(request,manifest,monkeypatch)
    errors = {"conflicting-overlap":"Conflicting candles","mixed-feed":"incompatible feeds",
              "null-id":"MISSING_LEGACY_ID","blob-basis":"INVALID_PRICE_BASIS_TYPE",
              "replacement":"checksum mismatch","snapshot":"SHADOW_SOURCE_SNAPSHOT_MISMATCH",
              "missing-mapping":"SHADOW_MAPPING_MISSING"}
    if case in errors:
        assert legacy.status_code == (409 if case in ("conflicting-overlap","mixed-feed","replacement") else 200)
        assert shadow.status_code == 409 and errors[case] in shadow.json()["detail"]
    else:
        assert shadow.status_code == legacy.status_code == 200
        assert shadow.json() == legacy.json()
        data = shadow.json()
        assert data["price_basis"] == "BID" and data["feed_id"] == "fixture"
        if case == "gap":
            assert data["coverage"]["gaps"] == [dict(start=T+600,end=T+900),dict(start=T+1200,end=end)]
        else:
            assert data["candles"] == [research_bar(t) for t in range(T+900,end,300)]
            assert data["coverage"]["truncated"] and data["coverage"]["duplicate_bars"] == 1
    assert source_hashes(args) == before


@pytest.mark.parametrize("corrupt", [False, True])
def test_native_shadow_window_verifies_source_files_without_legacy_fallback(fixture, monkeypatch, corrupt):
    args, _, part, manifest = fixture
    register(*args, manifest)
    monkeypatch.setenv(FLAG, str(manifest))
    forbid_legacy(monkeypatch)
    def legacy_file(*args, **kwargs):
        pytest.fail("Native shadow replay fell back to a live catalog partition")
    monkeypatch.setattr(storage, "verified_partition_file", legacy_file)
    if corrupt:
        (args[2] / part["relative_path"]).write_bytes(b"corrupted shadow candle fixture")
    with closing(TestClient(research_app)) as client:
        response = client.post("/api/replay/sessions", json={"source":"DUKASCOPY_SAVED", "symbol":"EURUSD",
                               "timeframe":"M5", "window_bars":5000, "start":T, "end":T+600})
    if corrupt:
        assert response.status_code == 409 and "SHADOW_ARTIFACT_INTEGRITY" in response.json()["detail"]
        assert db.one("SELECT count(*) AS n FROM replay_sessions")["n"] == 0
    else:
        assert response.status_code == 201, response.text
        market = response.json()["market"]
        assert market["partitions"] == [{"id":part["id"], "sha256":part["sha256"]}]
        assert [bar["time"] for bar in market["candles"]] == [T, T+300]
