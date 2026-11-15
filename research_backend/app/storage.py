"""Immutable Parquet partitions with separate SQLite metadata registration."""
from __future__ import annotations
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


def write_partition(provider:str,feed_id:str,symbol:str,kind:str,timeframe:str|None,price_basis:str,records:list[dict])->dict:
    if not records: raise ValueError('Empty market dataset cannot be committed')
    if provider not in ('MT5','DUKASCOPY') or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._#+-]{0,31}',symbol):
        raise ValueError('Unsupported provider or symbol')
    if kind not in ('candles','ticks'):raise ValueError('Invalid kind')
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
        pq.write_table(table,temp,compression='zstd')
        with temp.open('rb+') as f:
            digest = hashlib.file_digest(f, 'sha256').hexdigest()
            os.fsync(f.fileno())
        identity = json.dumps([provider, feed_id, symbol, kind, timeframe, price_basis, digest, 1])
        new_id = hashlib.sha256(identity.encode()).hexdigest()
        path=folder/f'part-{new_id}.parquet'
        relative=str(path.relative_to(MARKET_DIR))
        with transaction() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("""SELECT id, relative_path FROM datasets
                WHERE provider=? AND feed_id=? AND symbol=? AND kind=? AND timeframe IS ?
                AND price_basis=? AND sha256=? ORDER BY created_at LIMIT 1""",
                (provider,feed_id,symbol,kind,timeframe,price_basis,digest)).fetchone()
            if existing and (MARKET_DIR / existing['relative_path']).is_file():
                return {'id':existing['id'],'provider':provider,'symbol':symbol,'rows':len(records),
                        'relative_path':existing['relative_path'],'sha256':digest}
            if existing:
                raise RuntimeError("A cataloged copy is missing from disk")
            db.execute('''INSERT INTO datasets(id,provider,feed_id,symbol,kind,timeframe,price_basis,rows_count,first_time,last_time,relative_path,sha256,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''', (new_id,provider,feed_id,symbol,kind,timeframe,price_basis,len(records),
              datetime.fromtimestamp(int(records[0]['time']),timezone.utc).isoformat(),
              datetime.fromtimestamp(int(records[-1]['time']),timezone.utc).isoformat(),relative,digest,utc_now()))
            os.replace(temp,path)
        return {'id':new_id,'provider':provider,'symbol':symbol,'rows':len(records),'relative_path':relative,'sha256':digest}
    finally:
        temp.unlink(missing_ok=True)


def read_partition(dataset:dict,max_rows:int=2000)->list[dict]:
    import pyarrow.parquet as pq
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


def decode_bi5(payload:bytes, symbol:str,day:datetime,hour:int|None=None)->list[dict]:
    """Dukascopy big-endian .bi5 daily (current documented) or hourly (legacy).
    Recognizes symbol point-scale for supported instruments only. Bid/ask preserved.
    """
    if symbol not in ('EURUSD','GBPUSD','USDJPY','XAUUSD'):
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
    multiplier={'EURUSD':100000,'GBPUSD':100000,'USDJPY':1000,'XAUUSD':1000}[symbol]
    base=day.replace(hour=hour or 0,minute=0,second=0,microsecond=0,tzinfo=timezone.utc)
    records=[]
    max_ms=3600_000 if hour is not None else 86_400_000
    for index,(ms,ask,bid,ask_vol,bid_vol) in enumerate(struct.iter_unpack('>IIIff',raw)):
        if ms>=max_ms:raise ValueError('Record outside declared day/hour')
        if not (0<bid<=ask):raise ValueError('Invalid market bid/ask')
        if not all(math.isfinite(volume) and volume >= 0 for volume in (ask_vol, bid_vol)):
            raise ValueError('Invalid market volume')
        records.append({'time':int(base.timestamp())+ms//1000,'time_ms':int(base.timestamp())*1000+ms,
            'bid_units':int(bid),'ask_units':int(ask), 'scale':multiplier,'source_order':index,
            'ask_volume':float(ask_vol),'bid_volume':float(bid_vol)})
    return records


def aggregate_ticks(records:list[dict],timeframe_min:int)->list[dict]:
    if not records: return []
    groups={}
    for r in records:
        slot=(int(r['time'])//(timeframe_min*60))*(timeframe_min*60)
        g=groups.setdefault(slot,[]);g.append(r)
    result=[]
    for ts,g in sorted(groups.items()):
        scale=g[0]['scale']; bids=[r['bid_units']/scale for r in g]
        asks=[r['ask_units']/scale for r in g]
        result.append({'time':ts,'open':bids[0],'high':max(bids),'low':min(bids),'close':bids[-1],
          'ask_open':asks[0],'ask_high':max(asks),'ask_low':min(asks),'ask_close':asks[-1],'volume':len(g)})
    return result
