"""Locally supplied, allowlisted provider files. File names never determine provenance."""
from datetime import UTC, datetime
from hashlib import sha256
from io import BytesIO
import json
from itertools import zip_longest

from .db import one, rows
from .historical_import import FEED, MAX_TICKS, import_bi5
from .storage import canonical_ticks, decode_bi5, fold_ticks, iter_complete_partition, publish_encoded_partition

BI5_HOURS = {
    "2f47bcb14e0a2c4dcd6d65eb819b85f1b5168b656d1f5f67c3dcd2240569f014": 13,
    "9d40aa3147d93e6daa2218933877adb9471dc02ff08b19fc0d272f1eb781688f": 14,
}
M5_BID = {
    "fa2af192aa86dc41bb20df51dffe95c29bc48cdf2fe38b643f699985c6b0e09d": "EURUSD",
    "aed73f09d78bb3df29d3678e0ace1f00949a673a920ed22dcd6d23cb7a173d02": "GBPUSD",
}


def import_reviewed(payload):
    digest = sha256(payload).hexdigest()
    if digest in BI5_HOURS:
        result = import_bi5(payload, "EURUSD", datetime(2025,11,13,tzinfo=UTC), BI5_HOURS[digest])
        parts = [result["tick_dataset"], *result["candle_datasets"]]
    elif digest in M5_BID:
        import pyarrow.parquet as pq
        spool = BytesIO(payload)
        times = pq.ParquetFile(spool).read(columns=["time"]).to_pylist()
        spool.seek(0)
        parts = [publish_encoded_partition("DUKASCOPY", "dukascopy-web-m1-v1", M5_BID[digest],
                                           "candles", "M5", "BID", times, spool, digest, None)]
    else:
        raise ValueError("File checksum does not match a reviewed local archive")
    return {"source_sha256":digest, "partitions":parts}


def candle_origin(dataset, bars, step):
    """Check a BI5 candle against every quote in its pinned import, independently of file-grid coverage."""
    result = {"source_completeness":"UNVERIFIED", "reconstruction":"CANDLE_ARCHIVE"}
    if dataset["feed_id"] != FEED:
        return result
    result["reconstruction"] = "NO_LINKED_QUOTE_MANIFEST"
    claims = rows("""SELECT i.manifest,i.source_sha256,i.source_start,i.source_end,i.feed_id,i.symbol,p.value AS pin
        FROM historical_imports i, json_each(i.manifest,'$.candle_datasets') p
        WHERE CASE WHEN p.type='object' THEN json_extract(p.value,'$.id') END=? LIMIT 2""",
        (dataset["id"],))
    if not claims:
        return result
    row = claims[0]
    manifest = json.loads(row["manifest"])
    pin = json.loads(row["pin"])
    source = manifest.get("source")
    if (len(claims) != 1 or not isinstance(source, dict)
            or any(row[k] != dataset[k] for k in ("feed_id","symbol"))
            or any(source.get(k) != dataset[k] for k in ("symbol","provider"))
            or manifest.get("feed_id") != row["feed_id"]
            or (source.get("start"),source.get("end"),source.get("sha256")) !=
               (row["source_start"],row["source_end"],row["source_sha256"])
            or any(pin.get(k) != dataset[k] for k in ("sha256","price_basis","timeframe"))
            or pin.get("rows") != dataset["rows_count"]):
        raise ValueError(f"CONFLICTING_IMPORT_CLAIM: {dataset['id']}")
    tick = manifest.get("tick_dataset")
    if not tick:
        return result
    origin = one("SELECT * FROM datasets WHERE id=?", (tick["id"],))
    if (not origin or origin["sha256"] != tick["sha256"] or origin["rows_count"] != tick["rows"]
            or any(origin[k] != dataset[k] for k in ("provider","feed_id","symbol"))
            or origin["kind"] != "ticks" or origin["price_basis"] != "BID_ASK_UNITS"):
        raise ValueError("Source tick partition contradicts its import manifest")
    ticks = iter_complete_partition(origin, max_rows=MAX_TICKS)
    raw = one("SELECT payload FROM historical_bi5_sources WHERE sha256=?", (row["source_sha256"],))
    if raw:
        if sha256(raw["payload"]).hexdigest() != row["source_sha256"]:
            raise ValueError("Retained BI5 source checksum mismatch")
        start = datetime.fromtimestamp(row["source_start"],UTC)
        hour = start.hour if row["source_end"]-row["source_start"] == 3600 else None
        expected = canonical_ticks(decode_bi5(raw["payload"], dataset["symbol"], start, hour))
        def matches_file(archived):
            for actual, original in zip_longest(archived, expected):
                if actual != original:
                    raise ValueError("Tick partition contradicts the retained BI5 source")
                yield actual
        ticks = matches_file(ticks)
    result["file_observations"] = "ALL_RECORDS_VERIFIED" if raw else "ORIGINAL_FILE_UNAVAILABLE"
    reconstructed = fold_ticks(ticks, step//60)
    prefix = "ask_" if dataset["price_basis"] == "ASK" else ""
    if (len(reconstructed) != dataset["rows_count"] or any(b["time"] != ref["time"] or
            any(b[k] != ref[prefix+k] for k in ("open","high","low","close"))
            for b,ref in zip(bars,reconstructed[-len(bars):]))):
        raise ValueError("Archived candle OHLC disagrees with the pinned source quotes")
    slots = set(range(row["source_start"],row["source_end"],step))
    observed = {b["time"] for b in reconstructed}
    if observed - slots:
        raise ValueError("Source quotes fall outside their registered interval")
    return {**result,"reconstruction":"MATCHES_ARCHIVED_QUOTES","source_sha256":row["source_sha256"],
            "tick_partition":{"id":origin["id"],"sha256":origin["sha256"]},
            "start":row["source_start"],"end":row["source_end"],"checked_bars":len(bars),
            "missing_slots":sorted(slots-observed)}
