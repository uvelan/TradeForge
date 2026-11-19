"""Local import of UTC M1 CSV from the official Dukascopy web export."""
import csv
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from io import StringIO
import json

from .db import transaction, utc_now
from .historical_import import existing_import
from .storage import POINT_SCALES, write_partition


CSV_FEED = {"BID":"dukascopy-web-m1-v1", "ASK":"dukascopy-web-ask-m1-v1"}
CSV_POLICY = "DUKASCOPY_WEB_M1_COMPLETE_M5_V1"
CSV_HEADER = ["Etc/UTC","Open","High","Low","Close","Volume"]
CSV_MAX_BYTES = 1_000_000
PRICE_FIELDS = ("open","high","low","close")


def source_name(symbol, day, side="BID"):
    return f"{symbol[:3]}-{symbol[3:]}_1Minute_{side}_{day.isoformat()}_00_00-23_59_Etc_UTC.csv"


def read_minutes(payload, symbol, day):
    if len(payload) > CSV_MAX_BYTES:
        raise ValueError("CSV exceeds the one-day file limit")
    if symbol not in ("EURUSD","GBPUSD"):
        raise ValueError("Unsupported web export instrument")
    start = int(datetime.combine(day,time(),UTC).timestamp())
    if datetime.fromtimestamp(start,UTC) + timedelta(days=1) > datetime.now(UTC):
        raise ValueError("Select a completed UTC export day")
    source = csv.reader(StringIO(payload.decode("utf-8-sig")),strict=True)
    if next(source,None) != CSV_HEADER:
        raise ValueError("Expected the official UTC M1 export header")
    candles = {}
    duplicates = 0
    try:
        for row in source:
            instant, *fields = row
            stamp = datetime.fromisoformat(instant)
            if stamp.utcoffset() != timedelta(0) or stamp.microsecond or stamp.second:
                raise ValueError("Export candle is outside the UTC minute grid")
            epoch = int(stamp.timestamp())
            if not start <= epoch < start+86400 or len(fields) != 5:
                raise ValueError("Export candle is outside the declared day or format")
            o,h,l,c,v = [Decimal(value) for value in fields]
            if (not all(value.is_finite() for value in (o,h,l,c,v))
                    or not 0 < l <= min(o,c) <= max(o,c) <= h or v < 0 or v != v.to_integral_value()
                    or v > 2**63-1 or h > 100_000
                    or any(value*POINT_SCALES[symbol] % 1 for value in (o,h,l,c))):
                raise ValueError("Invalid provider OHLC, point scale or volume")
            bar = {"time":epoch,"open":float(o),"high":float(h),"low":float(l),"close":float(c),"volume":int(v)}
            if epoch in candles:
                if candles[epoch] != bar:
                    raise ValueError("Conflicting provider M1 candles")
                duplicates += 1
            else:
                candles[epoch] = bar
    except (InvalidOperation,csv.Error,OverflowError) as error:
        raise ValueError("Invalid provider CSV value") from error
    if not candles:
        raise ValueError("No M1 candles in this export")
    return start,candles,duplicates


def complete_m5(candles):
    records = []
    for start in sorted({stamp//300*300 for stamp in candles}):
        members = [candles.get(stamp) for stamp in range(start,start+300,60)]
        if any(bar is None for bar in members):
            continue
        records.append({"time":start,"open":members[0]["open"],"high":max(bar["high"] for bar in members),
                        "low":min(bar["low"] for bar in members),"close":members[-1]["close"],
                        "volume":sum(bar["volume"] for bar in members)})
    return records


def import_csv(payload, symbol, day:date, original_name, *, side="BID"):
    if side not in CSV_FEED or original_name != source_name(symbol,day,side):
        raise ValueError("Original download name must declare the requested symbol, price side, M1 and UTC day")
    start,candles,duplicates = read_minutes(payload,symbol,day)
    end = start + 86400
    digest = sha256(payload).hexdigest()
    seen_at = utc_now()
    feed = CSV_FEED[side]
    scope = feed,symbol,start,end
    with transaction() as db:
        db.execute("BEGIN IMMEDIATE")
        existing = db.execute("""SELECT * FROM historical_imports WHERE feed_id=? AND symbol=?
            AND source_start < ? AND source_end > ?""",(feed,symbol,end,start)).fetchall()
        if existing:
            return existing_import(db,existing,scope,digest)
        parts = []
        for frame,values in (("M1",list(candles.values())),("M5",complete_m5(candles))):
            if values:
                item = write_partition("DUKASCOPY",feed,symbol,"candles",frame,side,values,connection=db)
                parts.append({**item,"timeframe":frame,"price_basis":side})
        result = {"feed_id":feed,"aggregation_policy":CSV_POLICY,"timestamp_domain":"UTC_EPOCH_SECONDS",
                  "source":{"provider":"DUKASCOPY","symbol":symbol,"start":start,"end":end,"day":day.isoformat(),
                            "sha256":digest,"format":"WEB_M1_CSV","price_basis":side,"original_name":original_name,
                            "scale":POINT_SCALES[symbol],"first_imported_at":seen_at},
                  "candle_datasets":parts,"quality":{"source_records":len(candles)+duplicates,
                  "valid_m1":len(candles),"duplicates":duplicates,"rejected_rows":0,
                  "first":min(candles),"last":max(candles)}}
        db.execute("INSERT INTO historical_imports VALUES (?,?,?,?,?,?,?)",(*scope,digest,seen_at,json.dumps(result,sort_keys=True)))
    return result
