"""Completed Dukascopy UTC ranges, with independent candle and execution evidence."""
from datetime import UTC, date, datetime, time
from hashlib import sha256
from typing import Literal
import json

from pydantic import BaseModel, Field

from fastapi import APIRouter, File, HTTPException, UploadFile

from . import config
from .historical_csv import CSV_FEED, CSV_MAX_BYTES, import_csv
from .historical_import import FEED, import_bi5
from .indicator_coverage import open_intervals
from .month_replay import CALENDAR
from .recorded_market_entry import MAX_GAP_MS
from .recorded_session import read_session
from .replay_archive import replay_catalog, saved_replay_window, utc
from .reviewed_archives import BI5_HOURS, candle_import_claim

router = APIRouter(prefix="/api/forex")


def midnight(day):
    return int(datetime.combine(day, time(), UTC).timestamp())


def runs(stamps, step):
    result = []
    for stamp in sorted(stamps):
        if result and result[-1][1] == stamp:
            result[-1][1] += step
        else:
            result.append([stamp, stamp + step])
    return result


def candle_parts(catalog, symbol, start, end):
    return catalog[0].execute("""SELECT * FROM datasets WHERE provider='DUKASCOPY' AND
        symbol=? AND timeframe='M5' AND kind='candles' AND price_basis='BID'
        AND first_time<? AND last_time>=? ORDER BY id""", (symbol, utc(end), utc(start))).fetchall()


class CoverageSelection(BaseModel):
    feed_id: str = Field(min_length=1,max_length=200)
    EURUSD: str = Field(pattern=r"^[0-9a-f]{64}$")
    GBPUSD: str = Field(pattern=r"^[0-9a-f]{64}$")


def pin_digest(feed, symbol, start, end, pins):
    identity = [feed,symbol,start,end,sorted((pin["id"],pin["sha256"]) for pin in pins)]
    return sha256(json.dumps(identity,separators=(",",":")).encode()).hexdigest()


def pinned_pane(catalog, symbol, start, end, feed, expected, limit):
    try:
        pane = saved_replay_window(catalog,"DUKASCOPY",symbol,"M5",start,end,300,limit,end,feed_id=feed)
        if pin_digest(feed,symbol,start,end,pane["partitions"]) != expected:
            raise ValueError("Partition identities differ from the inspected range")
        for pin in pane["partitions"]:
            row = catalog[0].execute("SELECT * FROM datasets WHERE id=?",(pin["id"],)).fetchone()
            candle_import_claim(catalog[0].connection,row)
    except (OSError,RuntimeError,ValueError) as exc:
        raise HTTPException(409,f"{symbol} chart evidence changed or is unavailable. Inspect coverage again. {exc}") from exc
    return dict(**pane,symbol=symbol,timeframe="M5",source="DUKASCOPY_SAVED",source_completeness="UNVERIFIED",
                synthetic=False,latest_unconfirmed=False)


@router.get("/chart")
def preview(symbol: Literal["EURUSD","GBPUSD"], start: int, end: int, feed_id: str, pin: str):
    if not 0<=start<end<=datetime.now(UTC).timestamp() or end-start>3600 or start%300 or end%300:
        raise HTTPException(422,"Select a completed M5 interval of at most one hour")
    with replay_catalog() as catalog:
        return pinned_pane(catalog,symbol,start,end,feed_id,pin,12)


def candle_lane(catalog, symbol, start, opened, observed_by, feed, parts):
    end = start + 3600
    result = dict(feed=feed, pins=[], count=0, missing=[], error=None)
    seen = {}
    try:
        for part in parts:
            result["pins"].append(dict(id=part["id"], sha256=part["sha256"], feed=part["feed_id"]))
            claim = candle_import_claim(catalog[0].connection, part)
            if claim:
                result["pins"][-1]["source_sha256"] = claim[0]["source_sha256"]
        if parts:
            pane = saved_replay_window(catalog, "DUKASCOPY", symbol, "M5", start, end, 300, 12, end, observed_by=observed_by,feed_id=feed)
            seen = {bar["time"]: bar["available_at"] for bar in pane["candles"]}
            result.update(feed=pane["feed_id"], count=len(seen))
            if seen.keys() - opened:
                raise ValueError("Candle contradicts the scheduled closure")
    except (ValueError, RuntimeError, OSError) as error:
        result["error"] = str(error)
        seen.clear()
    result["missing"] = runs(opened - seen.keys(), 300)
    return result, seen


def replay_ranges(both, primary, peer):
    # Check backward: removing a late candle shortens the cutoff for all
    # preceding candles, while the already checked suffix keeps its own end.
    intervals = []
    for left,right in runs(both,300):
        end = right
        for stamp in range(right-300,left-1,-300):
            if max(primary[stamp],peer[stamp])>end:
                if stamp+300<end:
                    intervals.append([stamp+300,end])
                end = stamp
        if left<end:
            intervals.append([left,end])
    return sorted(intervals)


def verify_control(symbol, day, digest):
    control = symbol=="EURUSD" and day.date()==date(2025,11,13)
    if digest in BI5_HOURS or (control and day.hour in BI5_HOURS.values()):
        if not control or BI5_HOURS.get(digest)!=day.hour:
            raise ValueError("Selected symbol, date, hour or checksum conflicts with the pinned EURUSD control")


def quote_inventory(db, start, end):
    """Read each registered source once; gaps are half-open millisecond intervals."""
    hours = {t:dict(count=0, pins=[], gaps=[], error=None, registered=False) for t in range(start,end,3600)}
    segments = []
    market = open_intervals(start,end,CALENDAR)
    open_hours = {t for a,b in market for t in range(a,b,3600)}
    for item in db.execute("""SELECT source_start,source_end,source_sha256 FROM historical_imports
            WHERE symbol='EURUSD' AND feed_id=? AND source_start<? AND source_end>? ORDER BY source_start""",
                           (FEED,end,start)).fetchall():
        left, right = max(start,item["source_start"]), min(end,item["source_end"])
        affected = [hours[t] for t in range(left//3600*3600,right,3600)]
        for hour in affected:
            hour["registered"] = True
        try:
            if item["source_end"]-item["source_start"]==3600:
                verify_control("EURUSD",datetime.fromtimestamp(left,UTC),item["source_sha256"])
            records, pins, _, _ = read_session(db,"EURUSD",left,right,None)
            if len(pins) != 1:
                raise ValueError("Overlapping BI5 source claims")
            for hour in affected:
                hour["pins"].append(dict(**pins[0]["partition"], source_sha256=item["source_sha256"],feed=FEED))
            for row in records:
                ms = row["time_ms"]
                bucket = ms//3600000*3600
                hour = hours[bucket]
                hour["count"] += 1
                if bucket not in open_hours:
                    continue
                if segments and ms - (segments[-1][1]-1) <= MAX_GAP_MS:
                    segments[-1][1] = ms+1
                else:
                    segments.append([ms,ms+1])
        except (ValueError, RuntimeError, OSError) as error:
            for hour in affected:
                hour["error"] = f"{item['source_sha256']}: {error}"
    idx = 0
    for t, hour in hours.items():
        cursor, limit = t*1000, (t+3600)*1000
        while idx<len(segments) and segments[idx][1]<=cursor:
            idx += 1
        pos = idx
        while pos<len(segments) and segments[pos][0]<limit:
            a,b = segments[pos]
            if cursor<a:
                hour["gaps"].append([cursor,a])
            cursor = min(limit,b)
            pos += 1
        if cursor<limit:
            hour["gaps"].append([cursor,limit])
    return hours


def chart_sources(catalog,t,opened,right):
    parts = {symbol:candle_parts(catalog,symbol,t,t+3600) for symbol in ("EURUSD","GBPUSD")}
    sources = {}
    for symbol, rows in parts.items():
        sources[symbol] = {feed:candle_lane(catalog,symbol,t,opened,right,feed,
                             [row for row in rows if row["feed_id"]==feed])
                           for feed in sorted({row["feed_id"] for row in rows})}
    shared = sources["EURUSD"].keys() & sources["GBPUSD"].keys()
    def rank(feed):
        primary,peer = sources["EURUSD"][feed][1],sources["GBPUSD"][feed][1]
        shared_opens = primary.keys() & peer.keys()
        duration = sum(end-start for start,end in replay_ranges(shared_opens,primary,peer))
        return -duration,-len(shared_opens),feed!=CSV_FEED["BID"],feed

    feed = min(shared,key=rank,default=None)
    def selected(symbol):
        options = sources[symbol]
        chosen = feed or min(options,key=lambda f:(-options[f][0]["count"],f),default=None)
        lane, times = options[chosen] if chosen else candle_lane(catalog,symbol,t,opened,right,None,[])
        lane["alternatives"] = [value[0] for key,value in options.items() if key!=chosen]
        return lane,times
    eur,primary = selected("EURUSD")
    gbp,peer = selected("GBPUSD")
    both = primary.keys() & peer.keys()
    error = "No common M5 BID candle feed for this pair" if all(sources.values()) and not shared else None
    ranges = replay_ranges(both,primary,peer) if feed else []
    selections = []
    for start_time,end_time in ranges:
        binding = {"feed_id":feed}
        for symbol,rows in parts.items():
            scoped = [row for row in rows if row["feed_id"]==feed and row["first_time"]<utc(end_time)
                      and row["last_time"]>=utc(start_time)]
            binding[symbol] = pin_digest(feed,symbol,start_time,end_time,scoped)
        selections.append(binding)
    return eur,gbp,both if feed else set(),ranges,selections,error


@router.get("/coverage")
def coverage(start: date, end: date):
    left,right = midnight(start),midnight(end)
    if not 0 < right-left <= 31*86400 or right>datetime.now(UTC).timestamp():
        raise HTTPException(422,"Select up to 31 completed UTC days; end is exclusive")
    sessions = open_intervals(left,right,CALENDAR)
    result = []
    with replay_catalog() as catalog:
        quotes = quote_inventory(catalog[0].connection,left,right)
        for t,q in quotes.items():
            opened = {v for v in range(t,t+3600,300) if any(a<=v<b for a,b in sessions)}
            eur,gbp,both,ranges,selections,error = chart_sources(catalog,t,opened,right)
            if not opened:
                q["gaps"] = []
                if q["count"]:
                    q["error"] = "Recorded quotes contradict the scheduled closure"
            if error or eur["error"] or gbp["error"] or q["error"]:
                status = "Conflicting evidence"
            elif not opened:
                status = "Scheduled closure"
            elif both==opened and not q["gaps"]:
                status = "Healthy"
            elif eur["count"] or gbp["count"] or q["count"]:
                status = "Partial"
            else:
                status = "Missing open time"
            result.append(dict(start=t,end=t+3600,status=status,expected=len(opened),eur=eur,gbp=gbp,
                               paired=len(both),paired_ranges=ranges,chart_selections=selections,
                               pair_error=error,quotes=q))
    return dict(start=left,end=right,provider="DUKASCOPY",max_gap_ms=MAX_GAP_MS,hours=result)


@router.post("/retry")
def retry(hour: int):
    from scripts import month_archive
    from .db import transaction
    if hour%3600 or not 0<=hour<=datetime.now(UTC).timestamp()-3600:
        raise HTTPException(422,"Select a completed UTC hour")
    if not open_intervals(hour,hour+3600,CALENDAR):
        raise HTTPException(422,"Hour is a scheduled closure")
    day = datetime.fromtimestamp(hour,UTC)
    folder = config.STORAGE_ROOT / "t06-input"
    target = folder / day.date().isoformat() / f"{day.hour:02}h_ticks.bi5"
    expected = month_archive.PINS.get(day.hour) if day.date().isoformat()=="2025-11-13" else None
    cached = folder/target.name if expected and not target.exists() else target
    with transaction() as db:
        existing = db.execute("""SELECT 1 FROM historical_imports WHERE feed_id=? AND symbol='EURUSD'
                    AND source_start<? AND source_end>?""",(FEED,hour+3600,hour)).fetchone()
        if existing:
            try:
                _, pins, _, _ = read_session(db,"EURUSD",hour,hour+3600,None)
                if len(pins)!=1:
                    raise ValueError("Conflicting recorded source intervals")
                pin = pins[0]
                if expected and pin["end"]-pin["start"]==3600 and pin["source_sha256"]!=expected:
                    raise ValueError("Registered source differs from approved control")
                if cached.is_file():
                    if (pin["start"],pin["end"])!=(hour,hour+3600):
                        raise ValueError("Cached hourly file overlaps a different registered interval")
                    month_archive.verify_digest(cached.read_bytes(),pin["source_sha256"],day)
            except (ValueError,RuntimeError,OSError) as exc:
                raise HTTPException(409,str(exc)) from exc
            return dict(hour=hour,symbol="EURUSD",status="Already registered",pins=pins)
    attempts = []
    try:
        if cached.is_file():
            raw = cached.read_bytes()
            verify_control("EURUSD",day,month_archive.verify_digest(raw,expected,day))
            import_bi5(raw,"EURUSD",day,day.hour)
            status = "Imported cached hour"
        else:
            status = month_archive.acquire([(day,target,expected)],folder/(day.strftime("%Y-%m")+"-downloads.jsonl"), observations=attempts)
    except (ValueError,RuntimeError,OSError) as exc:
        raise HTTPException(409,str(exc)) from exc
    return dict(hour=hour,symbol="EURUSD",status=status,attempts=attempts)


@router.post("/candles")
def csv_import(symbol: Literal["EURUSD","GBPUSD"], day: date, file: UploadFile = File(...)):
    try:
        result = import_csv(file.file.read(CSV_MAX_BYTES+1),symbol,day,file.filename)
    except (ValueError,RuntimeError,OSError) as exc:
        raise HTTPException(409,str(exc)) from exc
    return dict(symbol=symbol,day=day,source=result["source"],partitions=result["candle_datasets"])


@router.post("/bi5")
def local_hour(symbol: Literal["EURUSD","GBPUSD"], hour: int, file: UploadFile = File(...)):
    if hour%3600 or not 0<=hour<=datetime.now(UTC).timestamp()-3600:
        raise HTTPException(422,"Select a completed UTC hour")
    day = datetime.fromtimestamp(hour,UTC)
    if file.filename != f"{day.hour:02}h_ticks.bi5":
        raise HTTPException(422,"Hourly BI5 filename must match the selected UTC hour")
    raw = file.file.read(40_000_001)
    if len(raw)>40_000_000:
        raise HTTPException(413,"Compressed BI5 file exceeds 40 MB")
    digest = sha256(raw).hexdigest()
    try:
        verify_control(symbol,day,digest)
        return import_bi5(raw,symbol,day,day.hour)
    except (ValueError,RuntimeError,OSError) as exc:
        raise HTTPException(409,str(exc)) from exc
