"""Crash the real publishers at filesystem and SQLite boundaries, then retry."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
import errno
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from research_backend.app import db, storage
from tradeforge import publication
from tradeforge.catalog import Catalog, CatalogIntegrityError
from tradeforge.database import open_database
from tradeforge.market import Simulator, Timeframe


def configure(root, monkeypatch):
    base = root / "research"
    base.mkdir(exist_ok=True)
    monkeypatch.setattr(db, "DATABASE", base / "tradeforge.sqlite3")
    monkeypatch.setattr(storage, "MARKET_DIR", base / "market")
    db.initialize()


def write(root, lane, connection=None):
    if lane == "research":
        bar = dict(time=900, open=1.1, high=1.2, low=1.0, close=1.1)
        return storage.write_partition("DUKASCOPY", "fixture", "EURUSD", "candles", "M1",
                                       "BID", [bar], connection=connection)["id"]
    engine = open_database(root)
    try:
        return Catalog(root, engine).ingest(Simulator(), "EURUSD", Timeframe.M1, 900, 1080)
    finally:
        engine.dispose()


def counts(root, lane):
    with closing(db.connect()) as owner:
        exists = owner.execute("SELECT 1 FROM sqlite_master WHERE name='partition_publications_v1'").fetchone()
        published = owner.execute("SELECT count(*) FROM partition_publications_v1 WHERE namespace=?",
                                  (lane,)).fetchone()[0] if exists else 0
        if lane == "research":
            return published, owner.execute("SELECT count(*) FROM datasets").fetchone()[0]
    with closing(sqlite3.connect(root/"database/trading.sqlite3")) as fixed:
        return published, fixed.execute("SELECT count(*) FROM dataset_versions").fetchone()[0]


def read(root, lane, key):
    if lane == "research":
        record = db.one("SELECT * FROM datasets WHERE id=?", (key,))
        return record, storage.read_partition(record)
    engine = open_database(root)
    try:
        result = Catalog(root,engine).read(key,1080)
        return result["dataset"], result["candles"]
    finally:
        engine.dispose()


def child():
    root, lane, point = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
    db.DATABASE = root/"research/tradeforge.sqlite3"
    storage.MARKET_DIR = root/"research/market"
    rename = os.replace
    def crash_at_rename(src, dest):
        if point == "before_rename":
            os._exit(75)
        rename(src, dest)
        if point == "after_rename":
            os._exit(75)
    os.replace = crash_at_rename
    if point == "after_owner_commit":
        transaction = publication.owner_transaction
        @contextmanager
        def crash_between_commits(database):
            with transaction(database) as conn:
                yield conn
            os._exit(75)
        publication.owner_transaction = crash_between_commits
    write(root,lane)
    # Publish returned after the actual commit. Lose the reply to the client.
    assert point == "lost_reply"
    os._exit(75)


@pytest.mark.parametrize("lane,point", [
    (lane,point) for lane in ("fixed","research")
    for point in ("before_rename","after_rename","lost_reply")
] + [("fixed","after_owner_commit")])
def test_crash_retry(tmp_path,monkeypatch,lane,point):
    configure(tmp_path,monkeypatch)
    code = "import runpy; runpy.run_path('tests/test_partition_publication.py',run_name='__main__')"
    result = subprocess.run([sys.executable,"-c",code,str(tmp_path),lane,point])
    assert result.returncode == 75
    assert counts(tmp_path,lane) == {"lost_reply":(1,1),"after_owner_commit":(1,0)}.get(point,(0,0))
    files = list(tmp_path.rglob("*.pending" if point=="before_rename" else "*.parquet"))
    assert len(files) == 1
    digest = hashlib.sha256(files[0].read_bytes()).hexdigest()
    if point == "lost_reply":
        record, = db.rows("SELECT metadata FROM partition_publications_v1 WHERE namespace=?", (lane,))
        original = json.loads(record["metadata"])
        assert read(tmp_path,lane,original["id"])[0] == original
    key = write(tmp_path,lane)
    assert write(tmp_path,lane) == key
    record, bars = read(tmp_path,lane,key)
    assert counts(tmp_path,lane) == (1,1) and record["sha256"] == digest
    assert [bar["time"] for bar in bars] == ([900,960,1020] if lane=="fixed" else [900])
    assert list(tmp_path.rglob("*.pending")) == []
    assert hashlib.sha256(next(tmp_path.rglob("*.parquet")).read_bytes()).hexdigest() == digest
    assert json.loads(db.one("SELECT metadata FROM partition_publications_v1")["metadata"]) == record


@pytest.mark.parametrize("lane", ["fixed","research"])
@pytest.mark.parametrize("failure", ["disk-full","checksum"])
def test_stage_failure(tmp_path,monkeypatch,lane,failure):
    configure(tmp_path,monkeypatch)
    def damage(src,dst,**_):
        dst.write(b"partial")
        if failure == "disk-full":
            raise OSError(errno.ENOSPC, "fixture disk full")
    with monkeypatch.context() as patch:
        patch.setattr(publication.shutil,"copyfileobj",damage)
        with pytest.raises((OSError,CatalogIntegrityError,RuntimeError),
                           match="disk full" if failure=="disk-full" else "checksum mismatch"):
            write(tmp_path,lane)
    assert counts(tmp_path,lane) == (0,0)
    assert not list(tmp_path.rglob("*.parquet")) and not list(tmp_path.rglob("*.pending"))
    key = write(tmp_path,lane)
    assert read(tmp_path,lane,key)[1] and counts(tmp_path,lane) == (1,1)


def test_sqlite_full_aborts_outer_import(tmp_path,monkeypatch):
    configure(tmp_path,monkeypatch)
    with pytest.raises(sqlite3.OperationalError,match="full"):
        with db.transaction() as conn:
            conn.execute("CREATE TABLE import_payload(data BLOB)")
            limit = conn.execute("PRAGMA page_count").fetchone()[0] + 8
            conn.execute(f"PRAGMA max_page_count={limit}")
            conn.execute("BEGIN IMMEDIATE")
            key = write(tmp_path,"research",connection=conn)
            conn.execute("INSERT INTO import_payload VALUES(zeroblob(1000000))")
    assert counts(tmp_path,"research") == (0,0)
    assert write(tmp_path,"research") == key
    assert counts(tmp_path,"research") == (1,1)


def test_two_adapters_share_one_owner_under_duplicate_delivery(tmp_path,monkeypatch):
    configure(tmp_path,monkeypatch)
    engine = open_database(tmp_path)
    try:
        catalog = Catalog(tmp_path,engine)
        def submit(lane):
            if lane == "fixed":
                return catalog.ingest(Simulator(),"EURUSD",Timeframe.M1,900,1080)
            return write(tmp_path,lane)
        with ThreadPoolExecutor(4) as pool:
            results = list(pool.map(submit, ["fixed","research"]*2))
    finally:
        engine.dispose()
    assert results[:2] == results[2:]
    assert counts(tmp_path,"fixed") == counts(tmp_path,"research") == (1,1)
    assert len(db.rows("SELECT * FROM partition_publications_v1")) == 2
    with pytest.raises(sqlite3.IntegrityError,match="Immutable partition publication"):
        with db.transaction() as conn:
            conn.execute("DELETE FROM partition_publications_v1")
    assert len(db.rows("SELECT * FROM partition_publications_v1")) == 2


def test_historical_fixed_id_and_source_hash(tmp_path,monkeypatch):
    from types import ModuleType
    configure(tmp_path,monkeypatch)
    path = "tradeforge/catalog.py"
    historical = ModuleType("tradeforge.phase_b_catalog")
    historical.__package__ = "tradeforge"
    source = subprocess.check_output(["git","show","a632a002d82162a50079c188aba1a5e507ccc2c4:"+path])
    exec(compile(source,path,"exec"),historical.__dict__)
    engine = open_database(tmp_path)
    try:
        catalog = historical.Catalog(tmp_path,engine)
        key = catalog.ingest(Simulator(),"EURUSD",Timeframe.M1,900,1080)
        old = catalog.read(key,1080)
    finally:
        engine.dispose()
    assert counts(tmp_path,"fixed") == (0,1)
    path = tmp_path/old["dataset"]["relative_path"]
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    assert write(tmp_path,"fixed") == key
    assert read(tmp_path,"fixed",key) == (old["dataset"],old["candles"])
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest == old["dataset"]["sha256"]
    print("historical_fixed",key,digest)


if __name__ == "__main__":
    child()
