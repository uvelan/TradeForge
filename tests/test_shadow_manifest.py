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
        return (conn.execute("SELECT catalog,legacy_id,legacy_record,revision,reason FROM mappings ORDER BY catalog,legacy_id,diagnostic_rowid").fetchall(),
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


@pytest.fixture
def catalog_pair(tmp_path, monkeypatch):
    """Quiescent catalogs with real checksummed candles and known source claims."""
    fixed = tmp_path / "fixed"
    market = tmp_path / "research-market"
    rdb = tmp_path / "research.sqlite"
    monkeypatch.setattr(db, "DATABASE", rdb)
    monkeypatch.setattr(storage, "MARKET_DIR", market)
    db.initialize()
    engine = open_database(fixed)
    try:
        catalog = Catalog(fixed, engine)
        candles = Simulator().candles("EURUSD", Timeframe.M1, 900, 1080)
        fid = catalog.ingest(Simulator(), "EURUSD", Timeframe.M1, 900, 1080)
        gaps = [catalog.publish("mt5", "fixture", "bid", 100000, "EURUSD", Timeframe.M1,
                                900, 1080, candles[:i] + candles[i+1:]) for i in range(3)]
    finally:
        engine.dispose()
    bar = dict(time=900, open=1.1, high=1.2, low=1., close=1.15)
    rid = storage.write_partition("DUKASCOPY", "fixture", "EURUSD", "candles", "M1", "BID", [bar])["id"]
    fdb = fixed / "database/trading.sqlite3"
    with closing(sqlite3.connect(fdb)) as conn, conn:
        conn.row_factory = sqlite3.Row
        for original, alias, status in [(fid, "same-origin", "VERIFIED")] + [
            (dataset, f"false-complete-{i}", "VERIFIED") for i, dataset in enumerate(gaps)
        ]:
            path = conn.execute("SELECT relative_path FROM dataset_versions WHERE id=?", (original,)).fetchone()[0]
            dest = f"market-data/{alias}.parquet"
            shutil.copyfile(fixed / path, fixed / dest)
            clone_row(conn, "dataset_versions", original, id=alias, relative_path=dest, validation_status=status)
    with closing(sqlite3.connect(rdb)) as conn, conn:
        conn.row_factory = sqlite3.Row
        path, checksum = conn.execute("SELECT relative_path, sha256 FROM datasets WHERE id=?", (rid,)).fetchone()
        for alias in ("wrong-checksum", "two-parents"):
            dest = f"{alias}.parquet"
            shutil.copyfile(market / path, market / dest)
            clone_row(conn, "datasets", rid, id=alias, relative_path=dest)
        for i, (identifier, digest, parent) in enumerate([
            (rid, checksum, "parent"), ("wrong-checksum", "0"*64, "parent"),
            ("two-parents", checksum, "first"), ("two-parents", checksum, "second")
        ]):
            claim = {"candle_datasets":[{"id":identifier, "sha256":digest}], "tick_dataset":{"id":parent}}
            conn.execute("INSERT INTO historical_imports VALUES (?,?,?,?,?,?,?)",
                         ("fixture","EURUSD",900+i,960+i,"a"*64,"fixture",json.dumps(claim)))
    return (fixed, rdb, market), fid, gaps, rid


def source_hashes(args):
    fixed, research, market = args
    files = [fixed/"database/trading.sqlite3", research, *(fixed/"market-data").rglob("*.parquet"),
             *market.rglob("*.parquet")]
    return {f:_digest(f) for f in files}


def test_fixed_grid_and_import_lineage(catalog_pair, tmp_path):
    args, fid, gaps, rid = catalog_pair
    clean = tmp_path/"clean.sqlite"
    register(*args, clean)
    def lookup(manifest, key):
        with closing(sqlite3.connect(manifest)) as conn:
            return conn.execute("SELECT revision,reason,contract FROM mappings LEFT JOIN artifacts USING(revision) "
                                "WHERE legacy_id=?", (key,)).fetchone()
    original = lookup(clean, rid)
    assert json.loads(original[2])["parents"] == ["parent"]
    with closing(sqlite3.connect(args[1])) as conn, conn:
        conn.execute("INSERT INTO historical_imports VALUES(?,?,?,?,?,?,?)",
                     ("unrelated","EURUSD",0,60,"b"*64,"fixture",'{"candle_datasets":["bad-shape"]}'))
    before = source_hashes(args)
    manifest = tmp_path/"shadow.sqlite"
    expected = {"fixed":{"inspected":8,"mapped":5,"unresolved":3},
                "research":{"inspected":3,"mapped":1,"unresolved":2}}
    assert register(*args, manifest) == expected
    assert lookup(manifest, rid) == original
    assert lookup(manifest, "wrong-checksum")[1] == "IMPORT_LINEAGE_MISMATCH"
    assert lookup(manifest, "two-parents")[1] == "AMBIGUOUS_IMPORT_LINEAGE"
    assert lookup(manifest, "same-origin") == lookup(manifest, fid)
    for i, dataset in enumerate(gaps):
        assert lookup(manifest, f"false-complete-{i}")[0:2] == (None, "FIXED_COVERAGE_MISMATCH")
        claim = json.loads(lookup(manifest, dataset)[2])
        assert claim["scheduled_coverage"] is None
        assert claim["event_bounds_utc_s"] == ([960,1020], [900,1020], [900,960])[i]
        assert claim["request_grid_gaps"] == dict(leading=int(i==0), interior=int(i==1), trailing=int(i==2))
    assert register(*args, manifest, batch_size=1) == expected
    assert source_hashes(args) == before


@pytest.mark.parametrize("stop_at", ["after_open", "after_first_ddl"])
def test_bootstrap_crash_and_retry(catalog_pair, tmp_path, stop_at):
    import subprocess
    import sys
    args, *_ = catalog_pair
    output, clean = tmp_path/"interrupted.sqlite", tmp_path/"clean.sqlite"
    # Terminate a real child without connection cleanup or transaction rollback.
    child = """
import os, sqlite3, sys
from pathlib import Path
from tradeforge.shadow_manifest import register
fixed, research, market, output = map(Path, sys.argv[1:5])
connect = sqlite3.connect
class Connection(sqlite3.Connection):
    def execute(self, sql, *args):
        result = super().execute(sql, *args)
        if sql.startswith('CREATE TABLE sources') and sys.argv[5]=='after_first_ddl':
            os._exit(75)
        return result
def crashable_connect(path, *args, **kwargs):
    if path == output:
        conn = connect(path, *args, factory=Connection, **kwargs)
        if sys.argv[5]=='after_open':
            os._exit(75)
        return conn
    return connect(path, *args, **kwargs)
sqlite3.connect = crashable_connect
register(fixed,research,market,output)
"""
    before = source_hashes(args)
    run = subprocess.run([sys.executable,"-c",child,*map(str,args),str(output),stop_at])
    assert run.returncode == 75 and output.exists()
    assert register(*args, output) == register(*args, clean)
    assert dump(output) == dump(clean)
    with closing(sqlite3.connect(output)) as a, closing(sqlite3.connect(clean)) as b:
        assert a.execute("SELECT * FROM sources ORDER BY catalog").fetchall() == b.execute(
            "SELECT * FROM sources ORDER BY catalog").fetchall()
    assert source_hashes(args) == before


def test_refuse_foreign_database_and_changed_inputs(catalog_pair, tmp_path):
    args, *_ = catalog_pair
    unrelated, manifest = tmp_path/"other.sqlite", tmp_path/"shadow.sqlite"
    with closing(sqlite3.connect(unrelated)) as conn, conn:
        conn.execute("CREATE TABLE keep(value TEXT)")
        conn.execute("INSERT INTO keep VALUES('untouched')")
    digest = _digest(unrelated)
    with pytest.raises(ValueError, match="snapshot mismatch"):
        register(*args, unrelated)
    assert _digest(unrelated) == digest
    register(*args, manifest)
    digest = _digest(manifest)
    with closing(sqlite3.connect(args[1])) as conn:
        conn.execute("CREATE TABLE intervening(value TEXT)")
    with pytest.raises(ValueError, match="snapshot mismatch"):
        register(*args, manifest)
    assert _digest(manifest) == digest


def test_registration_surfaces_operational_failures(catalog_pair, tmp_path, monkeypatch):
    from tradeforge import shadow_manifest

    args, *_ = catalog_pair
    manifest = tmp_path/"shadow.sqlite"
    with monkeypatch.context() as patch:
        def disk_full(*_):
            raise OSError("fixture snapshot storage exhausted")
        patch.setattr(shadow_manifest, "TemporaryFile", disk_full)
        with pytest.raises(OSError, match="storage exhausted"):
            register(*args, manifest)
    with closing(sqlite3.connect(args[1])) as conn:
        conn.execute("ALTER TABLE historical_imports DROP COLUMN acquired_at")
    with pytest.raises(sqlite3.OperationalError, match="acquired_at"):
        register(*args, tmp_path/"new-snapshot.sqlite")

def malformed_clone(args, rid, key, *, blob=False, label="malformed"):
    _, database, market = args
    with closing(sqlite3.connect(database)) as conn, conn:
        conn.row_factory = sqlite3.Row
        relative, = conn.execute("SELECT relative_path FROM datasets WHERE id=?", (rid,)).fetchone()
        dest = f"{label}.parquet"
        shutil.copyfile(market / relative, market / dest)
        clone_row(conn, "datasets", rid, id=key, relative_path=dest,
                  price_basis=b"BID" if blob else "BID")

@pytest.mark.parametrize("case", ["null", "two-null", "blob"])
def test_unidentified_and_typed_rows_reconcile_on_retry(catalog_pair, tmp_path, case):
    args, _, _, rid = catalog_pair
    blob = case == "blob"
    malformed_clone(args, rid, "blob" if blob else None, blob=blob)
    if case == "two-null":
        malformed_clone(args, rid, None, label="second-null")
    before = source_hashes(args)
    clean, resumed = tmp_path / "fresh.sqlite", tmp_path / "resumed.sqlite"
    databases = {"fixed": (args[0] / "database/trading.sqlite3", "dataset_versions"),
                 "research": (args[1], "datasets")}
    counts = {}
    for catalog, (path, table) in databases.items():
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)) as src:
            counts[catalog], = src.execute(f"SELECT count(*) FROM {table}").fetchone()
    assert counts == {"fixed": 8, "research": 5 if case == "two-null" else 4}

    def check(output):
        summary = register(*args, output, batch_size=1)
        for name, count in counts.items():
            stat = summary[name]
            assert count == stat["inspected"] == stat["mapped"] + stat["unresolved"]
        assert summary["fixed"]["mapped"] == 5
        assert summary["research"]["mapped"] == 1
        with closing(sqlite3.connect(output)) as conn:
            rows = conn.execute("""SELECT legacy_id, diagnostic_rowid, legacy_record, reason FROM mappings
                WHERE catalog='research' AND (legacy_id IS NULL OR legacy_id='blob')
                ORDER BY diagnostic_rowid""").fetchall()
        assert len(rows) == (2 if case == "two-null" else 1)
        for key, diagnostic, raw, reason in rows:
            record = json.loads(raw)
            if blob:
                assert key == record["id"] == "blob" and diagnostic is None
                assert reason == "INVALID_PRICE_BASIS_TYPE"
                assert record["price_basis"] == {"sqlite_type":"blob", "hex":"424944"}
            else:
                assert key is record["id"] is None and reason == "MISSING_LEGACY_ID"
                with closing(sqlite3.connect(args[1].as_uri()+"?mode=ro&immutable=1", uri=True)) as src:
                    assert src.execute("SELECT relative_path FROM datasets WHERE rowid=? AND id IS NULL",
                                       (diagnostic,)).fetchone() == (record["relative_path"],)
        return rows, dump(output)

    expected = check(clean)
    def stop_after_malformed(catalog, key):
        if catalog == "research" and (key is None or key == "blob"):
            raise InterruptedError
    with pytest.raises(InterruptedError):
        register(*args, resumed, batch_size=1, after_batch=stop_after_malformed)
    for _ in range(3):  # resume plus two identical retries
        assert check(resumed) == expected
    assert source_hashes(args) == before


def test_raw_record_sqlite_scalar_types():
    from tradeforge.shadow_manifest import raw_record
    tagged_text = '{"sqlite_type":"blob","hex":"424944"}'
    result = json.loads(raw_record({"raw":b"\x00\xff", "text":tagged_text, "real":float("-inf")}))
    assert bytes.fromhex(result["raw"]["hex"]) == b"\x00\xff"
    assert result["text"] == tagged_text
    assert result["real"] == {"sqlite_type":"real", "hex":"-inf"}
