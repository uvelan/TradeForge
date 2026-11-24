"""Phase A acceptance on SQLite backups and byte-for-byte copies of writer artifacts."""
from contextlib import closing
import json
import shutil
import sqlite3

import pytest

from research_backend.app import db, storage
from research_backend.app.saved_history import saved_range
from tradeforge.catalog import Catalog
from tradeforge.database import open_database
from tradeforge.market import Simulator, Timeframe
from tradeforge.shadow_manifest import register
from test_restore_rehearsal import _digest, _snapshot


def clone_row(conn, table, original, **changes):
    row = dict(conn.execute(f"SELECT * FROM {table} WHERE id=?", (original,)).fetchone())
    row.update(changes)
    conn.execute(f"INSERT INTO {table} ({','.join(row)}) VALUES ({','.join('?' for _ in row)})",
                 tuple(row.values()))


def dump(path):
    with closing(sqlite3.connect(path)) as conn:
        return (conn.execute("SELECT * FROM mappings ORDER BY catalog, legacy_id").fetchall(),
                conn.execute("SELECT * FROM artifacts ORDER BY revision").fetchall())


def test_restored_catalogs_interruption_retry_and_reader_parity(tmp_path, monkeypatch):
    src, copy = tmp_path / "source", tmp_path / "restore"
    rdb, market = "research/tradeforge.sqlite3", "research/market"
    (src / "research").mkdir(parents=True)
    monkeypatch.setattr(db, "DATABASE", src / rdb)
    monkeypatch.setattr(storage, "MARKET_DIR", src / market)
    db.initialize()
    engine = open_database(src)
    try:
        catalog = Catalog(src, engine)
        fid = catalog.ingest(Simulator(), "EURUSD", Timeframe.M1, 900, 1080)
        expected_fixed = catalog.read(fid, 1020)
    finally:
        engine.dispose()
    bars = [dict(time=t, open=1.1, high=1.2, low=1., close=1.15) for t in (900,960)]
    rid = storage.write_partition("DUKASCOPY","test_feed","EURUSD","candles","M1","BID",bars)["id"]
    def read_range():
        return saved_range("DUKASCOPY","EURUSD","M1",900,960,60,20)
    expected_research = read_range()
    for path in ("database/trading.sqlite3", rdb):
        _snapshot(src / path, copy / path)
    for path in ("market-data", market):
        shutil.copytree(src / path, copy / path)
    fdb = copy / "database/trading.sqlite3"
    with closing(sqlite3.connect(fdb)) as conn, conn:
        conn.row_factory = sqlite3.Row
        for alias in ("same-origin", "other-feed", "mismatch"):
            shutil.copyfile(copy / expected_fixed["dataset"]["relative_path"],
                            copy / f"market-data/{alias}.parquet")
        clone_row(conn,"dataset_versions",fid,id="same-origin",relative_path="market-data/same-origin.parquet")
        clone_row(conn,"dataset_versions",fid,id="other-feed",relative_path="market-data/other-feed.parquet",feed="different-feed")
        clone_row(conn,"dataset_versions",fid,id="missing",relative_path="market-data/absent.parquet")
        clone_row(conn,"dataset_versions",fid,id="mismatch",relative_path="market-data/mismatch.parquet",sha256="0"*64)
    with closing(sqlite3.connect(copy / rdb)) as conn, conn:
        conn.row_factory = sqlite3.Row
        relative = conn.execute("SELECT relative_path FROM datasets WHERE id=?", (rid,)).fetchone()[0]
        for alias in ("other-basis", "incomplete"):
            shutil.copyfile(copy / market / relative, copy / market / f"{alias}.parquet")
        clone_row(conn,"datasets",rid,id="other-basis",relative_path="other-basis.parquet",price_basis="ASK")
        clone_row(conn,"datasets",rid,id="incomplete",relative_path="incomplete.parquet",price_basis="")
        conn.execute("INSERT INTO historical_imports VALUES (?,?,?,?,?,?,?)",
                     ("test_feed","EURUSD",900,1020,"a"*64,"fixture",
                      json.dumps({"candle_datasets":[{"id":rid,"sha256":conn.execute(
                          "SELECT sha256 FROM datasets WHERE id=?",(rid,)).fetchone()[0]}],
                                  "tick_dataset":{"id":"parent-fixture"}})))
    def inventory():
        return {p.relative_to(copy):_digest(p) for p in copy.rglob("*") if p.is_file()}
    before = inventory()
    args = (copy, copy / rdb, copy / market)
    expected = {"fixed":{"inspected":5,"mapped":3,"unresolved":2},
                "research":{"inspected":3,"mapped":2,"unresolved":1}}
    clean, resumed = tmp_path / "clean.sqlite", tmp_path / "resume.sqlite"
    assert register(*args, clean, 3) == expected
    def interrupt(*_):
        raise InterruptedError
    with pytest.raises(InterruptedError):
        register(*args, resumed, 2, after_batch=interrupt)
    assert len(dump(resumed)[0]) == 2
    for size in (1, 4):
        assert register(*args, resumed, size) == expected
        assert dump(resumed) == dump(clean)
    mappings, artifacts = dump(resumed)
    outcomes = {identifier:(revision, reason) for _,identifier,_,revision,reason in mappings}
    assert outcomes["same-origin"] == outcomes[fid]
    assert outcomes["other-feed"][0] != outcomes[fid][0]
    assert outcomes["other-basis"][0] != outcomes[rid][0]
    assert outcomes["missing"] == (None, "MISSING_FILE")
    assert outcomes["mismatch"] == (None, "CHECKSUM_MISMATCH")
    assert outcomes["incomplete"] == (None, "MISSING_PRICE_BASIS")
    claims = {key:json.loads(value) for key,value in artifacts}
    fixed, research = (claims[outcomes[key][0]] for key in (fid,rid))
    assert fixed["event_bounds_utc_s"] == [900,1020]
    assert fixed["availability_bounds_utc_s"] == [960,1080]
    assert research["event_bounds_utc_s"] == [900,960]
    assert research["availability_bounds_utc_s"] is research["scheduled_coverage"] is None
    assert research["parents"] == ["parent-fixture"]
    assert research["raw_source_sha256"] == "a"*64
    assert inventory() == before
    engine = open_database(copy)
    try:
        assert Catalog(copy,engine).read(fid,1020) == expected_fixed
    finally:
        engine.dispose()
    monkeypatch.setattr(db,"DATABASE",copy / rdb)
    monkeypatch.setattr(storage,"MARKET_DIR",copy / market)
    assert read_range() == expected_research
