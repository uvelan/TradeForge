"""Immutable Parquet partitions with separate SQLite metadata registration."""
from __future__ import annotations
from contextlib import nullcontext
from datetime import datetime,timezone,timedelta
import hashlib
import json
import os
import struct
import lzma
import math
import re
import uuid
from pathlib import Path
from .config import MARKET_DIR
from .db import transaction, utc_now


def write_partition(provider:str,feed_id:str,symbol:str,kind:str,timeframe:str|None,price_basis:str,records:list[dict], *, connection=None, row_group_size=None)->dict:
    if not records: raise ValueError('Empty market dataset cannot be committed')
    if provider not in ('MT5','DUKASCOPY') or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._#+-]{0,31}',symbol):
        raise ValueError('Unsupported provider or symbol')
    if kind not in ('candles','ticks','arrival_candles'):raise ValueError('Invalid kind')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,60}',feed_id):raise ValueError('Invalid feed identifier')
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise RuntimeError('pyarrow is required for Parquet import: pip install -r requirements.txt') from exc
    records=sorted(records,key=lambda r:r['time'])
    dt=datetime.fromtimestamp(int(records[0]['time']),timezone.utc)
    folder=MARKET_DIR/provider.lower()/f'feed={feed_id}'/f'symbol={symbol}'/kind/f'year={dt.year:04d}'/f'month={dt.month:02d}'
    if timeframe:
        if timeframe not in {"M1", "M3", "M5", "M15", "M30", "H1", "H4", "D1"}:
            raise ValueError('Invalid timeframe')
        folder=folder/f'timeframe={timeframe}'
    folder.mkdir(parents=True,exist_ok=True)
    temp=folder/f'.part-{uuid.uuid4()}.pending'
    table=pa.Table.from_pylist(records)
    try:
        pq.write_table(table,temp,compression='zstd',row_group_size=row_group_size)
        with temp.open('rb+') as f:
            digest = hashlib.file_digest(f, 'sha256').hexdigest()
            os.fsync(f.fileno())
        identity = json.dumps([provider, feed_id, symbol, kind, timeframe, price_basis, digest, 1])
        new_id = hashlib.sha256(identity.encode()).hexdigest()
        path=folder/f'part-{new_id}.parquet'
        relative=str(path.relative_to(MARKET_DIR))
        with (transaction() if connection is None else nullcontext(connection)) as db:
            if connection is None:
                db.execute("BEGIN IMMEDIATE")
            existing = db.execute("""SELECT id, relative_path FROM datasets
                WHERE provider=? AND feed_id=? AND symbol=? AND kind=? AND timeframe IS ?
                AND price_basis=? AND sha256=? ORDER BY created_at LIMIT 1""",
                (provider,feed_id,symbol,kind,timeframe,price_basis,digest)).fetchone()
            if existing and (MARKET_DIR / existing['relative_path']).is_file():
                with (MARKET_DIR / existing['relative_path']).open('rb') as published:
                    if hashlib.file_digest(published, 'sha256').hexdigest() != digest:
                        raise RuntimeError("UNTRUSTED: existing partition checksum mismatch")
                return {'id':existing['id'],'provider':provider,'symbol':symbol,'rows':len(records),
                        'relative_path':existing['relative_path'],'sha256':digest}
            if existing:
                raise RuntimeError("A cataloged copy is missing from disk")
            db.execute('''INSERT INTO datasets(id,provider,feed_id,symbol,kind,timeframe,price_basis,rows_count,first_time,last_time,relative_path,sha256,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''', (new_id,provider,feed_id,symbol,kind,timeframe,price_basis,len(records),
              datetime.fromtimestamp(int(records[0]['time']),timezone.utc).isoformat(),
              datetime.fromtimestamp(int(records[-1]['time']),timezone.utc).isoformat(),relative,digest,utc_now()))
            if path.exists():
                # Recover a complete file left by a rolled-back catalog
                # commit. A different file at the immutable path is refused.
                with path.open('rb') as orphan:
                    if hashlib.file_digest(orphan, 'sha256').hexdigest() != digest:
                        raise RuntimeError("UNTRUSTED: orphan partition checksum mismatch")
            else:
                os.replace(temp,path)
        return {'id':new_id,'provider':provider,'symbol':symbol,'rows':len(records),'relative_path':relative,'sha256':digest}
    finally:
        temp.unlink(missing_ok=True)


def read_partition(dataset:dict,max_rows:int=2000)->list[dict]:
    import pyarrow.parquet as pq
    from .archive_provenance import require_market_partition

    require_market_partition(dataset["id"])
    path=(MARKET_DIR/dataset['relative_path']).resolve()
    if not path.is_relative_to(MARKET_DIR.resolve()):raise ValueError('Invalid stored dataset path')
    if not path.exists():raise FileNotFoundError('Dataset missing from disk')
    with path.open('rb') as file:
        if hashlib.file_digest(file, 'sha256').hexdigest() != dataset['sha256']:
            raise RuntimeError('Dataset checksum mismatch')
        file.seek(0)
        parquet = pq.ParquetFile(file)
        groups = []
        remaining = max_rows
        for index in reversed(range(parquet.num_row_groups)):
            if remaining <= 0:
                break
            groups.append(index)
            remaining -= parquet.metadata.row_group(index).num_rows
        table = parquet.read_row_groups(list(reversed(groups)))
        return table.slice(max(0, table.num_rows - max_rows)).to_pylist()


def iter_complete_partition(dataset, *, max_rows, batch_size=8192):
    """Verify and read every row in bounded batches, without the chart tail limit."""
    import pyarrow.parquet as pq

    from .archive_provenance import require_market_partition

    require_market_partition(dataset["id"])
    expected = dataset["rows_count"]
    if not 0 < expected <= max_rows or not 0 < batch_size <= 8192:
        raise ValueError("Partition exceeds the complete-read budget")
    path = (MARKET_DIR / dataset["relative_path"]).resolve()
    if not path.is_relative_to(MARKET_DIR.resolve()):
        raise ValueError("Invalid stored dataset path")
    with path.open("rb") as file:
        if hashlib.file_digest(file, "sha256").hexdigest() != dataset["sha256"]:
            raise RuntimeError("Dataset checksum mismatch")
        file.seek(0)
        parquet = pq.ParquetFile(file)
        if parquet.metadata.num_rows != expected:
            raise ValueError("Partition row count differs from its manifest")
        count = 0
        for batch in parquet.iter_batches(batch_size=batch_size):
            count += batch.num_rows
            if count > expected:
                raise ValueError("Partition row count differs from its manifest")
            yield from batch.to_pylist()
        if count != expected:
            raise ValueError("Incomplete partition read")


POINT_SCALES = {"EURUSD":100000, "GBPUSD":100000, "USDJPY":1000, "XAUUSD":1000}


def decode_bi5(payload:bytes, symbol:str,day:datetime,hour:int|None=None)->list[dict]:
    """Dukascopy big-endian .bi5 daily (current documented) or hourly (legacy).
    Recognizes symbol point-scale for supported instruments only. Bid/ask preserved.
    """
    if symbol not in POINT_SCALES:
        raise ValueError('Unsupported instrument; point scaling must be explicitly defined')
    if len(payload)>40_000_000:raise ValueError('Compressed file exceeds 40MB safety limit')
    maximum = 10_000_000
    try:
        decoder = lzma.LZMADecompressor(format=lzma.FORMAT_AUTO, memlimit=64 * 1024 * 1024)
        raw = decoder.decompress(payload, max_length=maximum + 1)
    except lzma.LZMAError as error:
        raise ValueError('Invalid or unsupported BI5 compression') from error
    if len(raw) > maximum:
        raise ValueError('Decompressed BI5 exceeds the 500,000-tick import limit')
    if not decoder.eof or decoder.unused_data:
        raise ValueError('Truncated BI5 stream or unexpected trailing data')
    if len(raw)%20:raise ValueError('Invalid bi5 record size')
    if day.utcoffset() != timedelta(0) or hour is not None and not 0 <= hour < 24:
        raise ValueError("Use a UTC day and a valid optional hour")
    multiplier = POINT_SCALES[symbol]
    base = int(day.replace(hour=hour or 0,minute=0,second=0,microsecond=0).timestamp())
    records=[]
    max_ms=3600_000 if hour is not None else 86_400_000
    for index,(ms,ask,bid,ask_vol,bid_vol) in enumerate(struct.iter_unpack('>IIIff',raw)):
        if ms>=max_ms:raise ValueError('Record outside declared day/hour')
        if not (0<bid<=ask):raise ValueError('Invalid market bid/ask')
        if not all(math.isfinite(volume) and volume >= 0 for volume in (ask_vol, bid_vol)):
            raise ValueError('Invalid market volume')
        records.append({'time':base+ms//1000,'time_ms':base*1000+ms,
            'bid_units':int(bid),'ask_units':int(ask), 'scale':multiplier,'source_order':index,
            'ask_volume':float(ask_vol),'bid_volume':float(bid_vol)})
    return records


def canonical_ticks(records, *, scale=None):
    """Use one source scope. Its ordinal breaks ties at identical milliseconds."""
    ordered = sorted(records, key=lambda tick: (tick["time_ms"], tick["source_order"]))
    previous = None
    for tick in ordered:
        ms, sequence, units = tick["time_ms"], tick["source_order"], tick["scale"]
        if (type(ms) is not int or ms < 0 or tick["time"] != ms // 1000
                or type(sequence) is not int or sequence < 0):
            raise ValueError("Invalid tick timestamp or source order")
        if scale is None:
            scale = units
        if (type(units) is not int or units <= 0 or units != scale
                or not 0 < tick["bid_units"] <= tick["ask_units"]):
            raise ValueError("Invalid bid/ask or inconsistent point scale")
        if previous and (ms, sequence) == (previous["time_ms"], previous["source_order"]):
            if tick != previous:
                raise ValueError("Conflicting tick quotes for the same source identity")
            continue
        previous = tick
        yield tick


def aggregate_ticks(records, timeframe_min):
    if timeframe_min not in (1, 5):
        raise ValueError("Use M1 or M5 tick aggregation")
    return fold_ticks(canonical_ticks(records), timeframe_min)


def fold_ticks(ordered, timeframe_min):
    """Fold an already verified and canonically ordered tick stream."""
    step = timeframe_min * 60
    result = []
    bar = None
    for tick in ordered:
        slot = tick["time"] // step * step
        bid = tick["bid_units"] / tick["scale"]
        ask = tick["ask_units"] / tick["scale"]
        if bar is None or bar["time"] != slot:
            bar = {"time":slot,"open":bid,"high":bid,"low":bid,"close":bid,
                   "ask_open":ask,"ask_high":ask,"ask_low":ask,"ask_close":ask,"volume":1}
            result.append(bar)
        else:
            bar["high"] = max(bar["high"], bid)
            bar["low"] = min(bar["low"], bid)
            bar["close"] = bid
            bar["ask_high"] = max(bar["ask_high"], ask)
            bar["ask_low"] = min(bar["ask_low"], ask)
            bar["ask_close"] = ask
            bar["volume"] += 1
    return result
