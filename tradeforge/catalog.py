import hashlib
import json
import os
import stat
from dataclasses import asdict
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryFile

import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy import MetaData, Table, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.engine import Engine

from .market import Candle, CandleSource, Timeframe, utc_now


CANDLE_SCHEMA = pa.schema([
    pa.field("time", pa.timestamp("s", tz="UTC"), nullable=False),
    pa.field("available_at", pa.timestamp("s", tz="UTC"), nullable=False),
    *(pa.field(name, pa.int64(), nullable=False) for name in ("open", "high", "low", "close")),
])


def validate(rows: list[Candle], start: int, end: int, step: int, *, allow_gaps=False) -> None:
    if not rows or (not allow_gaps and len(rows) != (end - start) // step):
        raise ValueError("Incomplete candle coverage")
    previous = start - step
    for row in rows:
        if not (start <= row.time < end and previous < row.time
                and row.time % step == 0 and row.available_at == row.time + step):
            raise ValueError("Candle times do not match the requested interval")
        if not (0 < row.low <= min(row.open, row.close) <= max(row.open, row.close) <= row.high):
            raise ValueError("Invalid candle price bounds")
        previous = row.time


class CatalogIntegrityError(ValueError):
    """A registered or pre-existing immutable catalog artifact cannot be verified."""


def verify_artifact(path: Path, checksum: str, snapshot=None):
    try:
        if not path.is_file():
            raise CatalogIntegrityError("Dataset artifact is not a regular file")
        # POSIX nonblocking protects the SQLite writer if a FIFO replaces the
        # path between the preliminary type check and the open.
        def nonblocking(name, flags):
            return os.open(name, flags | getattr(os, "O_NONBLOCK", 0))

        with open(path, "rb", opener=nonblocking) as file:
            if not stat.S_ISREG(os.fstat(file.fileno()).st_mode):
                raise CatalogIntegrityError("Dataset artifact is not a regular file")
            digest = hashlib.sha256()
            while block := file.read(1024 * 1024):
                digest.update(block)
                if snapshot is not None:
                    snapshot.write(block)
            actual = digest.hexdigest()
    except OSError as error:
        raise CatalogIntegrityError("Dataset artifact cannot be read") from error
    if actual != checksum:
        raise CatalogIntegrityError("Dataset checksum mismatch")


class Catalog:
    def __init__(self, root: Path, engine: Engine):
        self.root = root.resolve()
        self.engine = engine
        self.table = Table("dataset_versions", MetaData(), autoload_with=engine)

    def ingest(self, source: CandleSource, symbol: str, timeframe: Timeframe, start: int, end: int):
        self.validate_range(timeframe, start, end)
        rows = source.candles(symbol, timeframe, start, end)
        return self.publish(source.provider, source.feed, source.price_basis, source.price_scale,
                            symbol, timeframe, start, end, rows)

    @staticmethod
    def validate_range(timeframe: Timeframe, start: int, end: int):
        step = timeframe.seconds
        if not (0 < start < end <= int(utc_now().timestamp())) or start % step or end % step:
            raise ValueError("Use aligned UTC intervals that end in the past")
        if (end - start) // step > 10_000:
            raise ValueError("A dataset can contain at most 10,000 candles")

    def publish(self, provider: str, feed: str, basis: str, scale: int, symbol: str,
                timeframe: Timeframe, start: int, end: int, rows: list[Candle]):
        self.validate_range(timeframe, start, end)
        step = timeframe.seconds
        validate(rows, start, end, step, allow_gaps=provider == "mt5")
        status = "VERIFIED_WITH_GAPS" if len(rows) != (end - start) // step else "VERIFIED"
        candles = [asdict(row) for row in rows]
        identity = [provider, feed, basis, scale,
                    symbol, timeframe, start, end, 1, candles]
        dataset_id = hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()
        relative = Path("market-data", dataset_id[:2], f"{dataset_id}.parquet")
        path = self.root / relative
        self._artifact_path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=path.parent, delete=False, suffix=".tmp") as file:
            temporary = Path(file.name)
        try:
            table = pa.Table.from_pylist(candles, schema=CANDLE_SCHEMA)
            pq.write_table(table, temporary, compression="zstd")
            with temporary.open("rb+") as file:
                checksum = hashlib.file_digest(file, "sha256").hexdigest()
                os.fsync(file.fileno())
            with self.engine.begin() as connection:
                result = connection.execute(insert(self.table).values(
                    id=dataset_id, provider=provider, feed=feed,
                    symbol=symbol, timeframe=timeframe, price_basis=basis,
                    price_scale=scale, start_utc=start, end_utc=end,
                    acquired_utc=utc_now().isoformat(), schema_version=1,
                    relative_path=relative.as_posix(), sha256=checksum,
                    row_count=len(rows), validation_status=status,
                ).on_conflict_do_nothing(index_elements=["id"]))
                if not result.rowcount:
                    # The writer transaction serializes registration and validation.
                    entry = self._entry(connection, dataset_id)
                    if entry is None:
                        raise CatalogIntegrityError("Dataset registration cannot be verified")
                    self._verify_artifact(entry["relative_path"], entry["sha256"])
                elif path.exists() or path.is_symlink():
                    # A rename can survive an interrupted SQLite commit. Retain the
                    # original bytes and claim only an exact match to the staged file.
                    self._verify_artifact(relative, checksum)
                else:
                    os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        return dataset_id

    def list(self):
        with self.engine.connect() as connection:
            query = select(self.table).order_by(self.table.c.acquired_utc.desc()).limit(100)
            return [dict(row) for row in connection.execute(query).mappings()]

    def _entry(self, connection, dataset_id):
        query = select(self.table).where(self.table.c.id == dataset_id)
        return connection.execute(query).mappings().one_or_none()

    def _artifact_path(self, relative):
        try:
            path = (self.root / relative).resolve()
        except OSError as error:
            raise CatalogIntegrityError("Invalid catalog path") from error
        if not path.is_relative_to(self.root / "market-data"):
            raise CatalogIntegrityError("Invalid catalog path")
        return path

    def _verify_artifact(self, relative, checksum, snapshot=None):
        path = self._artifact_path(relative)
        verify_artifact(path, checksum, snapshot)

    def read(self, dataset_id: str, cutoff: int | None):
        from .shadow_reads import manifest_path, open_resolver

        path = manifest_path()
        if path is not None:
            with open_resolver(path, "fixed", self.root / "database/trading.sqlite3",
                               self.root / "market-data") as resolver:
                entries = resolver.rows("SELECT * FROM dataset_versions WHERE id=?", (dataset_id,))
                if not entries:
                    raise KeyError(dataset_id)
                with resolver.artifact(entries[0]) as snapshot:
                    return self._project(snapshot, entries[0], cutoff)
        with self.engine.connect() as connection:
            entry = self._entry(connection, dataset_id)
        if entry is None:
            raise KeyError(dataset_id)
        with TemporaryFile() as snapshot:
            self._verify_artifact(entry["relative_path"], entry["sha256"], snapshot)
            snapshot.seek(0)
            return self._project(snapshot, entry, cutoff)

    @staticmethod
    def _project(snapshot, entry, cutoff):
        candles = pl.scan_parquet(snapshot).with_columns(
            pl.col("time", "available_at").dt.epoch("s")
        )
        if cutoff is not None:
            candles = candles.filter(pl.col("available_at") <= cutoff)
        return {"dataset": dict(entry), "candles": candles.collect().to_dicts()}
