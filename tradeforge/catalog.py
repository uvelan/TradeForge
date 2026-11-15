import hashlib
import json
import os
from dataclasses import asdict
from pathlib import Path
from tempfile import NamedTemporaryFile

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
                if result.rowcount:
                    os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        return dataset_id

    def list(self):
        with self.engine.connect() as connection:
            query = select(self.table).order_by(self.table.c.acquired_utc.desc()).limit(100)
            return [dict(row) for row in connection.execute(query).mappings()]

    def read(self, dataset_id: str, cutoff: int | None):
        with self.engine.connect() as connection:
            entry = connection.execute(select(self.table).where(self.table.c.id == dataset_id)).mappings().one_or_none()
        if entry is None:
            raise KeyError(dataset_id)
        path = (self.root / entry["relative_path"]).resolve()
        if not path.is_relative_to(self.root / "market-data"):
            raise ValueError("Invalid catalog path")
        with path.open("rb") as file:
            if hashlib.file_digest(file, "sha256").hexdigest() != entry["sha256"]:
                raise ValueError("Dataset checksum mismatch")
        candles = pl.scan_parquet(path).with_columns(
            pl.col("time", "available_at").dt.epoch("s")
        )
        if cutoff is not None:
            candles = candles.filter(pl.col("available_at") <= cutoff)
        return {"dataset": dict(entry), "candles": candles.collect().to_dicts()}
