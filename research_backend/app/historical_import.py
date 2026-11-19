"""Versioned, atomic onboarding for completed Dukascopy BI5 source intervals."""
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json

from .db import transaction, utc_now
from .storage import POINT_SCALES, canonical_ticks, decode_bi5, fold_ticks, iter_complete_partition, write_partition


FEED = "dukascopy-bi5-v2"
POLICY = "UTC_TICK_MS_FILE_ORDINAL_V2"
MAX_TICKS = 500_000


def import_bi5(payload, symbol, day, hour=None):
    start = day.replace(hour=hour or 0, minute=0, second=0, microsecond=0)
    end = start + (timedelta(hours=1) if hour is not None else timedelta(days=1))
    if start.utcoffset() != timedelta(0) or end > datetime.now(UTC):
        raise ValueError("Select a completed UTC hour or day")
    start, end = int(start.timestamp()), int(end.timestamp())
    digest = sha256(payload).hexdigest()
    scope = FEED, symbol, start, end
    sql = """SELECT * FROM historical_imports WHERE feed_id=? AND symbol=?
             AND source_start < ? AND source_end > ?"""
    with transaction() as db:
        existing = db.execute(sql,(FEED,symbol,end,start)).fetchall()
        if existing:
            return existing_import(db, existing, scope, digest)
    decoded = decode_bi5(payload,symbol,day,hour)
    ticks = list(canonical_ticks(decoded,scale=POINT_SCALES[symbol]))
    if not ticks:
        raise ValueError("No ticks present in supplied source file")
    derived = {frame:fold_ticks(ticks, minutes) for frame,minutes in (("M1",1),("M5",5))}
    spreads = [tick["ask_units"] - tick["bid_units"] for tick in ticks]
    seen_at = utc_now()
    with transaction() as db:
        db.execute("BEGIN IMMEDIATE")
        existing = db.execute(sql,(FEED,symbol,end,start)).fetchall()
        if existing:
            return existing_import(db, existing, scope, digest)
        tick_part = write_partition("DUKASCOPY",FEED,symbol,"ticks",None,"BID_ASK_UNITS",ticks,
                                    connection=db,row_group_size=8192)
        parts = []
        for frame,bars in derived.items():
            ask_bars = [{"time":bar["time"],"volume":bar["volume"],
                         **{field:bar["ask_"+field] for field in ("open","high","low","close")}}
                        for bar in bars]
            for side,values in (("BID",bars),("ASK",ask_bars)):
                partition = write_partition("DUKASCOPY",FEED,symbol,"candles",frame,side,values,connection=db)
                parts.append({**partition,"timeframe":frame,"price_basis":side})
        result = {"tick_dataset":tick_part,"candle_rows":len(derived["M5"]),"candle_datasets":parts,
                  "feed_id":FEED,"aggregation_policy":POLICY,"timestamp_domain":"UTC_EPOCH_MILLISECONDS",
                  "source":{"provider":"DUKASCOPY","symbol":symbol,"start":start,"end":end,"sha256":digest,
                            "format":"BI5","scale":POINT_SCALES[symbol],"first_imported_at":seen_at},
                  "quality":{"source_records":len(decoded),"valid_ticks":len(ticks),
                             "duplicate_identities":len(decoded)-len(ticks),"rejected_ticks":0,
                             "first_ms":ticks[0]["time_ms"],"last_ms":ticks[-1]["time_ms"],
                             "spread_points":{"min":min(spreads),"max":max(spreads),"mean":sum(spreads)/len(spreads)}},
                  "note":"Source coverage must be checked against the selected market calendar."}
        db.execute("INSERT INTO historical_imports VALUES (?,?,?,?,?,?,?)",
                   (*scope,digest,seen_at,json.dumps(result,sort_keys=True)))
    return result


def existing_import(db, existing, scope, digest):
    first = existing[0]
    if (len(existing) != 1 or (first["source_start"],first["source_end"],first["source_sha256"])
            != (*scope[2:],digest)):
        raise ValueError("Overlapping historical source interval requires independent review")
    result = json.loads(first["manifest"])
    partitions = result["candle_datasets"]
    if "tick_dataset" in result:
        partitions = [result["tick_dataset"], *partitions]
    for part in partitions:
        catalog = db.execute("SELECT * FROM datasets WHERE id=?", (part["id"],)).fetchone()
        if not catalog or catalog["sha256"] != part["sha256"] or catalog["rows_count"] != part["rows"]:
            raise ValueError("Registered import partition metadata mismatch")
        for _ in iter_complete_partition(catalog,max_rows=MAX_TICKS):
            pass
    return result
