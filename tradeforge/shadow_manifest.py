"""Offline Phase A inventory. Run python -m tradeforge.shadow_manifest --help."""
import argparse
from contextlib import closing
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path, PureWindowsPath
import re
import sqlite3
from tempfile import TemporaryFile

import pyarrow as pa
import pyarrow.parquet as pq

from research_backend.app.archive_provenance import SYNTHETIC_PARTITIONS
from .catalog import CatalogIntegrityError, verify_artifact
from .market import Timeframe

CONTRACT_VERSION = 1
MANIFEST_VERSION = 3
APP_ID = 0x5446534D
TABLES = {"fixed": "dataset_versions", "research": "datasets"}
SCHEMA = (
    "CREATE TABLE sources(catalog TEXT PRIMARY KEY, database_sha256 TEXT, archive_root TEXT)",
    "CREATE TABLE artifacts(revision TEXT PRIMARY KEY, contract TEXT NOT NULL)",
    """CREATE TABLE mappings(catalog TEXT REFERENCES sources, legacy_id TEXT,
        legacy_record TEXT NOT NULL, revision TEXT REFERENCES artifacts, reason TEXT,
        PRIMARY KEY(catalog,legacy_id), CHECK ((revision IS NULL) != (reason IS NULL)))""",
)


def packed(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def raw_record(row):
    # SQLite columns contain scalars. An object in a column value therefore
    # cannot collide with legitimate text, including JSON-looking text.
    def encode(value):
        if isinstance(value, bytes):
            return {"sqlite_type": "blob", "hex": value.hex()}
        if isinstance(value, float) and not math.isfinite(value):
            return {"sqlite_type": "real", "hex": value.hex()}
        return value

    return packed({name: encode(value) for name, value in row.items()})


class Unresolved(ValueError):
    pass


def require(condition, reason):
    if not condition:
        raise Unresolved(reason)


def utc_seconds(value):
    stamp = datetime.fromisoformat(value) if isinstance(value, str) else value
    require(isinstance(stamp, datetime) and stamp.utcoffset() is not None
            and not stamp.microsecond, "INVALID_UTC_TIMESTAMP")
    return int(stamp.timestamp())


def import_claim(reader, row):
    """Optional companion attestation, joined on partition ID, never on time alone."""
    matches = reader.execute("""SELECT i.source_sha256, i.source_start, i.source_end, i.acquired_at,
        i.feed_id, i.symbol, json_extract(i.manifest,'$.tick_dataset.id'),
        json_extract(i.manifest,'$.aggregation_policy'), json_extract(part.value,'$.sha256')
        FROM historical_imports i, json_each(i.manifest,'$.candle_datasets') part
        WHERE json_type(i.manifest,'$.candle_datasets')='array'
        AND CASE WHEN part.type='object' THEN json_extract(part.value,'$.id') END=?
        ORDER BY i.source_sha256 LIMIT 2""", (row["id"],)).fetchall()
    if not matches:
        return None
    require(len(matches) == 1, "AMBIGUOUS_IMPORT_LINEAGE")
    digest, start, end, acquired, feed, symbol, parent, policy, checksum = matches[0]
    require((feed, symbol, checksum) == (row["feed_id"], row["symbol"], row["sha256"]),
            "IMPORT_LINEAGE_MISMATCH")
    return dict(raw_source_sha256=digest, source_interval=[start,end], acquired_at=acquired,
                parents=[parent] if parent else None, aggregation_policy=policy)


def describe(source, row, archive_root, reader, has_imports):
    fixed = source == "fixed"
    checksum = row["sha256"]
    require(isinstance(checksum, str) and re.fullmatch("[0-9a-f]{64}", checksum),
            "INVALID_REGISTERED_SHA256")
    locator = row["relative_path"]
    require(isinstance(locator, str) and locator.strip(), "MISSING_PATH")
    relative = PureWindowsPath(locator)
    require(not relative.anchor and ".." not in relative.parts, "UNSAFE_PATH")
    parts = relative.parts
    if fixed:
        require(parts and parts[0] == "market-data", "UNSAFE_PATH")
        parts = parts[1:]
    path = archive_root.joinpath(*parts).resolve()
    require(path.is_relative_to(archive_root), "UNSAFE_PATH")
    require(path.exists(), "MISSING_FILE")
    with TemporaryFile() as snapshot:
        try:
            verify_artifact(path, checksum, snapshot)
        except CatalogIntegrityError as error:
            if isinstance(error.__cause__, OSError) and not isinstance(
                    error.__cause__, (FileNotFoundError, PermissionError)):
                raise error.__cause__ from None
            raise Unresolved({"Dataset checksum mismatch": "CHECKSUM_MISMATCH",
                              "Dataset artifact is not a regular file": "WRONG_FILE_TYPE"}
                             .get(str(error), "UNREADABLE_FILE")) from error
        snapshot.seek(0)
        kind = "candles" if fixed else row["kind"]
        require(kind == "candles", "UNSUPPORTED_KIND")
        require(fixed or row["id"] not in SYNTHETIC_PARTITIONS, "QUARANTINED_SYNTHETIC_CAPTURE")
        feed = row["feed" if fixed else "feed_id"]
        for name, value in (("FEED", feed), ("PROVIDER", row["provider"]),
                            ("SYMBOL", row["symbol"]), ("PRICE_BASIS", row["price_basis"])):
            require(value is None or isinstance(value, str), "INVALID_" + name + "_TYPE")
            require(isinstance(value, str) and value.strip(), "MISSING_" + name)
        require(row["price_basis"].lower() in {"bid", "ask", "synthetic_bid"}, "UNSUPPORTED_BASIS")
        require(row["timeframe"] in Timeframe._value2member_map_, "UNSUPPORTED_TIMEFRAME")
        step = Timeframe(row["timeframe"]).seconds
        if fixed:
            require(row["schema_version"] == 1 and row["validation_status"] in
                    {"VERIFIED", "VERIFIED_WITH_GAPS"}, "UNSUPPORTED_SOURCE_VERSION")
            require(isinstance(row["price_scale"], int) and row["price_scale"] > 0,
                    "INVALID_PRICE_SCALE")
        parquet = pq.ParquetFile(snapshot)
        count = parquet.metadata.num_rows
        require(count > 0 and count == row["row_count" if fixed else "rows_count"], "ROW_COUNT_MISMATCH")
        columns = ["time", "open", "high", "low", "close"] + (["available_at"] if fixed else [])
        schema = parquet.schema_arrow
        require(all(name in schema.names for name in columns), "MISSING_CANDLE_COLUMN")
        if fixed:
            require(all(pa.types.is_int64(schema.field(name).type) for name in columns[1:5])
                    and all(pa.types.is_timestamp(schema.field(name).type)
                            and schema.field(name).type.tz == "UTC" for name in ("time", "available_at")),
                    "INVALID_FIXED_SCHEMA")
        first = last = None
        missing_grid_slots = 0
        for batch in parquet.iter_batches(batch_size=1024, columns=columns):
            for candle in batch.to_pylist():
                event = utc_seconds(candle["time"]) if fixed else candle["time"]
                require(type(event) is int and event % step == 0, "OFF_GRID_EVENT")
                require(last is None or event > last, "NON_INCREASING_EVENT")
                if last is not None:
                    missing_grid_slots += (event - last) // step - 1
                if fixed:
                    require(utc_seconds(candle["available_at"]) == event + step, "INVALID_AVAILABILITY")
                o, h, l, c = (candle[name] for name in columns[1:5])
                require(all(type(price) in (float, int) and math.isfinite(price) for price in (o,h,l,c))
                        and l <= min(o,c) <= max(o,c) <= h, "INVALID_OHLC")
                if first is None:
                    first = event
                last = event
        request_gaps = None
        if fixed:
            start, end = row["start_utc"], row["end_utc"]
            require(type(start) is int and type(end) is int and 0 < start < end
                    and start % step == end % step == 0, "INVALID_REQUEST_GRID")
            require(start <= first <= last < end, "EVENT_RANGE_MISMATCH")
            missing = (end - start) // step - count
            expected_status = "VERIFIED_WITH_GAPS" if missing else "VERIFIED"
            require(row["validation_status"] == expected_status and (not missing or row["provider"] == "mt5"),
                    "FIXED_COVERAGE_MISMATCH")
            if missing:
                request_gaps = dict(leading=(first-start)//step, interior=missing_grid_slots,
                                    trailing=(end-last)//step-1)
        else:
            require((utc_seconds(row["first_time"]), utc_seconds(row["last_time"])) == (first, last),
                    "EVENT_RANGE_MISMATCH")
    contract = {key: row[key] for key in ("provider", "symbol", "timeframe", "price_basis", "sha256")}
    contract.update(version=CONTRACT_VERSION, source_catalog=source, feed=feed, kind=kind, rows=count,
                    event_bounds_utc_s=[first, last], missing_interior_grid_slots=missing_grid_slots,
                    availability_bounds_utc_s=[first + step, last + step] if fixed else None,
                    availability_policy="BAR_CLOSE_MODEL" if fixed else None,
                    request_half_open_utc_s=[row["start_utc"], row["end_utc"]] if fixed else None,
                    price_scale=row.get("price_scale"), scheduled_coverage=None,
                    parents=None, raw_source_sha256=None, instrument_spec_version=None,
                    schema_version=row.get("schema_version"))
    if request_gaps is not None:
        contract["request_grid_gaps"] = request_gaps
    # Only the fixed deterministic producer attests reproducible origin from the
    # same feed, interval and contents. Other legacy claims keep their own ID.
    if not (fixed and row["provider"] == "simulator" and feed == "deterministic-eurusd-v1"
            and row["price_basis"] == "synthetic_bid" and row["symbol"] == "EURUSD"):
        contract["source_claim_id"] = row["id"]
    if has_imports:
        claim = import_claim(reader, row)
        if claim:
            contract.update(claim)
    return contract


def claim_generation(out, generation):
    # A retry can supersede an interrupted writer. Fence both batch and final
    # publication under the write lock, including the empty-catalog case.
    out.execute("BEGIN IMMEDIATE")
    if out.execute("SELECT generation FROM registration WHERE id=1").fetchone() != (generation,):
        raise ValueError("Registration superseded by another run")


def register(fixed_root, research_db, research_market, manifest, batch_size=64, *, after_batch=None):
    if not 1 <= batch_size <= 1024:
        raise ValueError("batch size must be between 1 and 1024")
    sources = {"fixed": (fixed_root / "database/trading.sqlite3", fixed_root / "market-data"),
               "research": (research_db, research_market)}
    manifest = manifest.resolve()
    pins = {}
    for name, (db, root) in sources.items():
        db = db.resolve(strict=True)
        root = root.resolve()
        if manifest.is_relative_to(root) or manifest == db or str(manifest).startswith(str(db) + "-"):
            raise ValueError("Use a separate shadow manifest outside the archive")
        if any(Path(str(db) + suffix).exists() for suffix in ("-wal", "-journal")):
            raise ValueError("Use a quiescent SQLite backup without WAL or rollback journals")
        with db.open("rb") as handle:
            pins[name] = (name, hashlib.file_digest(handle, "sha256").hexdigest(), str(root))
    with closing(sqlite3.connect(manifest)) as out:
        out.execute("PRAGMA foreign_keys=ON")
        with out:
            out.execute("BEGIN IMMEDIATE")
            # A crash before the schema commit leaves an empty file. Decide
            # after SQLite recovers any journal and serializes initializers.
            if manifest.stat().st_size == 0:
                for sql in SCHEMA:
                    out.execute(sql)
                out.execute(f"PRAGMA application_id={APP_ID}")
                out.execute("PRAGMA user_version=1")
                out.executemany("INSERT INTO sources VALUES(?,?,?)", pins.values())
            schema_version = out.execute("PRAGMA user_version").fetchone()[0]
            if (out.execute("PRAGMA application_id").fetchone()[0] != APP_ID
                    or schema_version not in (1, 2, MANIFEST_VERSION)
                    or sorted(out.execute("SELECT * FROM sources")) != sorted(pins.values())):
                raise ValueError("Manifest version or source snapshot mismatch")
            if schema_version == 1:
                if out.execute("SELECT 1 FROM mappings WHERE legacy_id IS NULL LIMIT 1").fetchone():
                    raise ValueError("V1 mappings contain unidentified rows; use a fresh shadow output")
                out.execute("""ALTER TABLE mappings ADD COLUMN diagnostic_rowid INTEGER
                    CHECK ((legacy_id IS NULL) = (diagnostic_rowid IS NOT NULL))""")
                out.execute("CREATE UNIQUE INDEX diagnostic_identity ON mappings(catalog, diagnostic_rowid)")
            if schema_version < MANIFEST_VERSION:
                out.execute("""CREATE TABLE registration(
                    id INTEGER PRIMARY KEY CHECK(id=1), generation INTEGER NOT NULL,
                    complete INTEGER NOT NULL CHECK(complete IN (0,1)))""")
                out.execute("INSERT INTO registration VALUES(1,0,0)")
                out.execute(f"PRAGMA user_version={MANIFEST_VERSION}")
            generation, = out.execute("""UPDATE registration SET generation=generation+1, complete=0
                WHERE id=1 RETURNING generation""").fetchone()
        for name, (db, root) in sources.items():
            with closing(sqlite3.connect(db.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)) as reader:
                reader.row_factory = sqlite3.Row
                has_imports = name == "research" and reader.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='historical_imports'"
                ).fetchone()
                cursor = reader.execute(f"SELECT rowid AS _snapshot_rowid, * FROM {TABLES[name]} ORDER BY id, rowid")
                while batch := cursor.fetchmany(batch_size):
                    with out:
                        claim_generation(out, generation)
                        for record in batch:
                            row = dict(record)
                            source_rowid = row.pop("_snapshot_rowid")
                            unidentified = row["id"] is None
                            revision = reason = None
                            try:
                                require(not unidentified, "MISSING_LEGACY_ID")
                                contract = packed(describe(name, row, root.resolve(), reader, has_imports))
                            except Unresolved as error:
                                reason = str(error)
                            except (pa.ArrowInvalid, pa.ArrowNotImplementedError):
                                reason = "INVALID_PARQUET"
                            except (ValueError, TypeError, OverflowError):
                                reason = "INVALID_METADATA"
                            else:
                                revision = hashlib.sha256(contract.encode()).hexdigest()
                                out.execute("INSERT OR IGNORE INTO artifacts VALUES(?,?)", (revision, contract))
                            out.execute("""INSERT INTO mappings
                                (catalog, legacy_id, legacy_record, revision, reason, diagnostic_rowid)
                                VALUES(?,?,?,?,?,?)
                                ON CONFLICT DO UPDATE SET
                                legacy_record=excluded.legacy_record, revision=excluded.revision, reason=excluded.reason""",
                                        (name, row["id"], raw_record(row), revision, reason,
                                         source_rowid if unidentified else None))
                    if after_batch:
                        after_batch(name, batch[-1]["id"])
        with out:
            claim_generation(out, generation)
            summary = {name: dict(zip(("inspected","mapped","unresolved"), out.execute(
                "SELECT count(*), count(revision), count(reason) FROM mappings WHERE catalog=?", (name,)
            ).fetchone())) for name in sources}
            out.execute("UPDATE registration SET complete=1 WHERE id=1")
        return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ("fixed-root", "research-db", "research-market", "manifest"):
        parser.add_argument("--" + option, required=True, type=Path)
    parser.add_argument("--batch-size", type=int, default=64)
    print(json.dumps(register(**vars(parser.parse_args())), sort_keys=True))


if __name__ == "__main__":
    main()
