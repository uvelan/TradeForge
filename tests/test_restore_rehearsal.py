"""Rehearse offline recovery of two isolated, synthetically populated stores."""
import hashlib
import json
import shutil
import sqlite3
from contextlib import closing

import pytest

from research_backend.app import db, storage
from tradeforge.catalog import Catalog
from tradeforge.database import open_database
from tradeforge.market import Simulator, Timeframe


def _read_only(path):
    return closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True))


def _digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _snapshot(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    with _read_only(source) as reader, closing(sqlite3.connect(destination)) as writer:
        reader.backup(writer)


def test_restore_two_catalogs_from_verified_backup(tmp_path, monkeypatch):
    source = tmp_path / "source"
    backup = tmp_path / "backup"
    restored = tmp_path / "restored"
    catalog_db = "database/trading.sqlite3"
    research_db = "research/tradeforge.sqlite3"
    (source / "research").mkdir(parents=True)

    monkeypatch.setattr(db, "DATABASE", source / research_db)
    monkeypatch.setattr(storage, "MARKET_DIR", source / "research/market")
    engine = open_database(source)
    db.initialize()
    bar = dict(time=900, open=1.1, high=1.2, low=1.0, close=1.15, closed=True)
    try:
        # Hold readers on the previous snapshot to ensure commits stay in WAL.
        with _read_only(source / catalog_db) as fixed, _read_only(source / research_db) as research:
            for reader, table in ((fixed, "dataset_versions"), (research, "datasets")):
                reader.execute("BEGIN")
                assert reader.execute(f"SELECT count(*) FROM {table}").fetchone() == (0,)

            catalog = Catalog(source, engine)
            dataset_id = catalog.ingest(Simulator(), "EURUSD", Timeframe.M1, 900, 1080)
            expected = catalog.read(dataset_id, cutoff=None)
            part = storage.write_partition(
                "DUKASCOPY", "restore_fixture", "EURUSD", "candles", "M1", "BID", [bar]
            )

            assert fixed.execute("SELECT count(*) FROM dataset_versions").fetchone() == (0,)
            assert research.execute("SELECT count(*) FROM datasets").fetchone() == (0,)
            for relative in (catalog_db, research_db):
                assert (source / (relative + "-wal")).stat().st_size > 32
                _snapshot(source / relative, backup / relative)
    finally:
        engine.dispose()

    parquet_files = {
        expected["dataset"]["relative_path"]: expected["dataset"]["sha256"],
        "research/market/" + part["relative_path"]: part["sha256"],
    }
    for relative, checksum in parquet_files.items():
        assert _digest(source / relative) == checksum
        (backup / relative).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / relative, backup / relative)
    checksums = parquet_files | {
        relative: _digest(backup / relative) for relative in (catalog_db, research_db)
    }
    (backup / "manifest.json").write_text(json.dumps(checksums, sort_keys=True), encoding="utf-8")

    # Restore from the backup alone, with neither source files nor WAL sidecars.
    shutil.rmtree(source)
    manifest = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
    for relative, checksum in manifest.items():
        assert _digest(backup / relative) == checksum
        (restored / relative).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(backup / relative, restored / relative)
        assert _digest(restored / relative) == checksum

    for relative in (catalog_db, research_db):
        with _read_only(restored / relative) as conn:
            assert conn.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
            assert conn.execute("PRAGMA foreign_key_check").fetchall() == []

    engine = open_database(restored)
    try:
        catalog = Catalog(restored, engine)
        assert catalog.read(dataset_id, cutoff=None) == expected
        (restored / expected["dataset"]["relative_path"]).write_bytes(b"damaged fixture")
        with pytest.raises(ValueError, match="checksum mismatch"):
            catalog.read(dataset_id, cutoff=None)
    finally:
        engine.dispose()

    monkeypatch.setattr(db, "DATABASE", restored / research_db)
    monkeypatch.setattr(storage, "MARKET_DIR", restored / "research/market")
    partition = db.one("SELECT * FROM datasets WHERE id=?", (part["id"],))
    assert partition["sha256"] == part["sha256"]
    assert storage.read_partition(partition) == [bar]
    (storage.MARKET_DIR / part["relative_path"]).write_bytes(b"damaged fixture")
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        storage.read_partition(partition)
