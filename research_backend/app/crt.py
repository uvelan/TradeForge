"""Causal Candle Range Theory research baseline. OHLC provides no intrabar sequence."""
from collections import Counter, defaultdict
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from hashlib import sha256
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, FiniteFloat, model_validator

from .fvg import identity
from .indicator_coverage import MarketSchedule, inspect_window
from .structure_continuity import continuity_reason
from .structure_pages import EventPage

VERSION = 1
IDENTITY_VERSION = 1
FRAMES = {"H1":3600, "H4":14400, "D1":86400}
COMBINATIONS = {"M5":("H1",), "M15":("H4","D1"), "H1":("D1",)}


class CRTRules(BaseModel):
    reference_frame: Literal["H1","H4","D1"] | None = None
    reference_mode: Literal["aggregated","native"] = "aggregated"
    daily_timezone: str = Field(default="UTC", max_length=64)
    daily_boundary: str = Field(default="00:00", pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")
    market_schedule: MarketSchedule = Field(default_factory=lambda:MarketSchedule(kind="unknown"))
    reference_start: int | None = Field(default=None, ge=0, le=32_503_680_000)
    history_bars: Literal[2000,10000] = 2000
    history_start: int | None = Field(default=None, ge=0, le=32_503_680_000)
    max_ranges: int = Field(default=2, ge=1, le=8)
    max_age: int = Field(default=96, ge=1, le=500)
    candidate_age: int = Field(default=3, ge=1, le=40)
    min_penetration: FiniteFloat = Field(default=0, ge=0)
    min_reclaim: FiniteFloat = Field(default=0, ge=0)
    invalidate: Literal["sweep_extreme","reference_boundary","none"] = "sweep_extreme"
    include_context: bool = False

    @model_validator(mode="after")
    def validate_contract(self):
        if (self.history_bars == 10000) != (self.history_start is not None):
            raise ValueError("Extended CRT history requires a fixed history_start")
        try:
            ZoneInfo(self.daily_timezone)
        except (ValueError, ZoneInfoNotFoundError) as error:
            raise ValueError("Unknown CRT IANA timezone") from error
        return self


class CRTDelivery(BaseModel):
    formation_limit: int = Field(default=24, ge=0, le=150)
    sweep_limit: int = Field(default=40, ge=0, le=150)
    revision_limit: int = Field(default=40, ge=0, le=150)
    current_limit: int = Field(default=8, ge=0, le=8)
    formation_cursor: str | None = Field(default=None, max_length=180)
    sweep_cursor: str | None = Field(default=None, max_length=180)
    revision_cursor: str | None = Field(default=None, max_length=180)
    current_cursor: str | None = Field(default=None, max_length=180)


def bounds(stamp, step, rules):
    if step != 86400 or (rules.daily_timezone,rules.daily_boundary)==("UTC","00:00"):
        return stamp//step*step, stamp//step*step+step
    zone = ZoneInfo(rules.daily_timezone)
    local = datetime.fromtimestamp(stamp,zone)
    clock = time.fromisoformat(rules.daily_boundary)
    date = local.date()-timedelta(days=local.time()<clock)
    bounds=[]
    for day in (date,date+timedelta(days=1)):
        wall = datetime.combine(day,clock)
        candidates = {int(wall.replace(tzinfo=zone,fold=fold).timestamp()) for fold in (0,1)}
        valid = {x for x in candidates if datetime.fromtimestamp(x,zone).replace(tzinfo=None)==wall}
        if len(valid)!=1:
            return None
        bounds.append(valid.pop())
    return tuple(bounds)


def ohlc(bar):
    return [bar.open,bar.high,bar.low,bar.close]


def references(rows, cutoff, step, frame, rules, native, quality):
    grouped=defaultdict(list)
    htf=FRAMES[frame]
    for row in rows:
        pair=bounds(row[0].time,htf,rules)
        if pair is None:
            quality["UNVERIFIABLE_LOCAL_BOUNDARY"]+=1
            continue
        grouped[pair].append(row)
    for (start,end), members in sorted(grouped.items()):
        if rules.reference_start is not None and start!=rules.reference_start:
            continue
        schedule=rules.market_schedule
        # A full uninterrupted grid is direct evidence even without a declared session.
        coverage_schedule=MarketSchedule(kind="continuous") if schedule.kind=="unknown" else schedule
        coverage=inspect_window(start,end,step,members,coverage_schedule,cutoff)
        status=coverage["status"]
        if end>cutoff:
            status="DEVELOPING_REFERENCE"
        if status!="COMPLETE":
            if schedule.kind=="unknown" and status=="MISSING_OPEN_CANDLES":
                status="UNVERIFIED_MARKET_SCHEDULE"
            quality[status]+=1
            continue
        values=[members[0][0].open,max(b.high for b,_ in members),min(b.low for b,_ in members),members[-1][0].close]
        if values[1]<=values[2]:
            quality["ZERO_WIDTH_REFERENCE"]+=1
            continue
        available=coverage["available_at"]
        raw=None
        if rules.reference_mode=="native":
            native_row=native.get(start)
            if native_row is None:
                quality["NATIVE_REFERENCE_UNAVAILABLE"]+=1
                continue
            bar,stamp=native_row
            if ohlc(bar)!=values:
                quality["NATIVE_AGGREGATE_MISMATCH"]+=1
                continue
            available=max(available,stamp)
            raw=[bar.model_dump(),stamp]
        yield available,start,end,values,len(members),identity(
            "crt-reference",[(bar.model_dump(),stamp) for bar,stamp in members],raw)


TERMINAL={"AMBIGUOUS","INVALIDATED","EXPIRED","OPPOSITE_BOUNDARY_REACHED"}


def evaluate_crt(rows, step, cutoff, frame, rules, delivery, provenance, native_rows=(), *, context=None):
    scope={key:provenance.get(key) for key in ("source","symbol","timeframe","feed_id","dataset_id","price_basis")}
    market=identity("crt-market",scope,frame,step)
    policy=(VERSION,IDENTITY_VERSION)
    config=identity("crt-rules",policy,rules.model_dump(exclude={
        "history_bars","history_start","reference_start","include_context"}),frame)
    snapshot=sha256(identity("crt-snapshot",policy,rules.model_dump(),cutoff,provenance).encode())
    for label, data in ((b"ltf",rows),(b"htf",native_rows)):
        snapshot.update(label)
        for bar,stamp in data:
            snapshot.update(bar.model_dump_json().encode()+f":{stamp};".encode())
    pages={name:EventPage("crt_"+name,getattr(delivery,name+"_limit"),getattr(delivery,name+"_cursor"),max_before=100000)
           for name in ("formation","sweep","revision","current")}
    quality=Counter()
    if rules.market_schedule.kind=="unknown":
        quality["UNVERIFIED_MARKET_SCHEDULE"]+=1
    refs=iter(sorted(references(rows,cutoff,step,frame,rules,{bar.time:(bar,stamp) for bar,stamp in native_rows},quality)))
    pending=next(refs,None)
    book={}
    index=0
    previous=None
    minimum=Decimal(str(rules.min_penetration))
    reclaim_minimum=Decimal(str(rules.min_reclaim))

    def publish(zone,state,reason,available,bar=None,*,event_time=None,confirmed_at=None,**details):
        prior=zone["latest"]
        sequence=prior["revision"]+1 if prior else 1
        observed=event_time if event_time is not None else zone["reference_start"] if bar is None else bar.time
        confirmed=confirmed_at if confirmed_at is not None else zone["reference_end"] if bar is None else bar.time+step
        exact=identity("crt-event-evidence",policy,zone["range_id"],prior["id"] if prior else None,
                       state,reason,observed,confirmed,available,bar.model_dump() if bar else None,details)
        event={**zone["base"],"id":identity("crt-event",policy,zone["range_id"],sequence,exact),
               "revision":sequence,"evidence_fingerprint":exact,"state":state,"reason":reason,
               "observation_time":observed,"available_at":available,
               "confirmation_time":confirmed,
               "observation_ohlc":ohlc(bar) if bar else None,
               "context":context(available,observed) if context and bar else {},
               **(zone.get("sweep_evidence",{}) if state!="AMBIGUOUS" else {}),**details}
        zone["latest"]=event
        if state in TERMINAL:
            del book[zone["range_id"]]
        collection="formation" if not prior else "sweep" if reason in {
            "NO_SAME_CANDLE_RECLAIM","SAME_CANDLE_RECLAIM","BOTH_BOUNDARIES_SWEPT"
        } else "revision"
        pages[collection].append(event)

    def release(ref):
        available,start,end,values,count,fingerprint=ref
        candle=identity("crt-reference-candle",policy,market,frame,rules.reference_mode,start,end,available,fingerprint)
        key=identity("crt-range",policy,config,candle)
        if len(book)>=rules.max_ranges:
            oldest=next(iter(book.values()))
            publish(oldest,"EXPIRED","REFERENCE_CAP_REPLACEMENT",available,event_time=start,
                    confirmed_at=end,replacement_reference=candle)
        high,low=Decimal(str(values[1])),Decimal(str(values[2]))
        base={"range_id":key,"reference_id":candle,"reference_start":start,"reference_end":end,
              "reference_available_at":available,"reference_ohlc":values,"reference_mode":rules.reference_mode,
              "reference_frame":frame,"constituents":count,"high":float(high),"low":float(low),
              "width":float(high-low),"midpoint":float((high+low)/2),"algorithm_version":VERSION,
              "identity_policy_version":IDENTITY_VERSION,"provenance_id":market,"configuration_fingerprint":config}
        zone={"base":base,"range_id":key,"reference_start":start,"reference_end":end,
              "high":high,"low":low,"age":0,"latest":None,"sweep":None,"candidate_due":None}
        book[key]=zone
        publish(zone,"ACTIVE","CLOSED_REFERENCE",available)

    for bar,available in sorted(rows,key=lambda row:(row[1],row[0].time)):
        if previous is not None and bar.time<=previous:
            quality["LATE_OBSERVATION_IGNORED"]+=1
            continue
        while pending is not None and pending[0]<=bar.time:
            release(pending)
            pending=next(refs,None)
        # Apply a newly known continuity break before activating a reference from the new segment.
        if previous is not None and bar.time!=previous+step:
            reason=continuity_reason(previous,bar.time,step,rules.market_schedule)
            quality[reason]+=1
            if reason!="SCHEDULED_CLOSURE":
                for zone in list(book.values()):
                    publish(zone,"INVALIDATED",reason,available,bar,preceding_time=previous)
        previous=bar.time
        while pending is not None and pending[0]<=available:
            release(pending)
            pending=next(refs,None)
        high,low,close=map(lambda v:Decimal(str(v)),(bar.high,bar.low,bar.close))
        for zone in list(book.values()):
            if bar.time<zone["base"]["reference_available_at"]:
                continue
            h,l=zone["high"],zone["low"]
            zone["age"]+=1
            if zone["age"]>=rules.max_age or (zone["candidate_due"] is not None and index>=zone["candidate_due"]):
                publish(zone,"EXPIRED","CANDIDATE_AGE" if zone["candidate_due"] is not None and index>=zone["candidate_due"]
                        else "REFERENCE_AGE",available,bar)
                continue
            tracked=zone["sweep"]
            if tracked and bar.time<tracked[2]:
                continue
            breach_high=high>h and high-h>=minimum
            breach_low=low<l and l-low>=minimum
            if breach_high and breach_low:
                publish(zone,"AMBIGUOUS","BOTH_BOUNDARIES_SWEPT",available,bar)
                continue
            if tracked:
                side, extreme, _=tracked
                beyond=(close>extreme if side=="HIGH" else close<extreme)
                boundary=(close>h if side=="HIGH" else close<l)
                if (rules.invalidate=="sweep_extreme" and beyond or
                    rules.invalidate=="reference_boundary" and boundary):
                    publish(zone,"INVALIDATED","CLOSE_BEYOND_INVALIDATION",available,bar,sweep_side=side)
                elif (high<l if side=="HIGH" else low>h):
                    publish(zone,"INVALIDATED","UNOBSERVED_OPPOSITE_GAP",available,bar,sweep_side=side)
                elif (low<=l<=high if side=="HIGH" else low<=h<=high):
                    publish(zone,"OPPOSITE_BOUNDARY_REACHED","POST_RECLAIM_BOUNDARY",available,bar,sweep_side=side)
                elif zone["latest"]["state"]!="EXPANSION_OBSERVED" and low<=(h+l)/2<=high:
                    publish(zone,"EXPANSION_OBSERVED","POST_RECLAIM_MIDPOINT",available,bar,sweep_side=side)
                elif ((high>extreme if side=="HIGH" else low<extreme) or close<l or close>h) and zone.get("last_extension")!=(
                    "EXTREME" if (high>extreme if side=="HIGH" else low<extreme) else "OUTSIDE"):
                    extension="EXTREME" if (high>extreme if side=="HIGH" else low<extreme) else "OUTSIDE"
                    zone["last_extension"]=extension
                    publish(zone,zone["latest"]["state"],"NEW_SWEEP_EXTREME" if extension=="EXTREME" else "OUTSIDE_CLOSE",
                            available,bar,sweep_side=side)
                continue
            if zone["candidate_due"] is not None:
                if (breach_high and zone["candidate_side"]=="LOW") or (breach_low and zone["candidate_side"]=="HIGH"):
                    publish(zone,"INVALIDATED","OPPOSITE_SWEEP_BEFORE_RECLAIM",available,bar)
                continue
            if not (breach_high or breach_low):
                continue
            side,extreme=("HIGH",high) if breach_high else ("LOW",low)
            distance=h-close if breach_high else close-l
            confirmed=l<close<h and distance>=reclaim_minimum
            details={"sweep_side":side,"swept_boundary":float(h if breach_high else l),
                     "sweep_extreme":float(extreme),"penetration":float(high-h if breach_high else l-low),
                     "reclaim_close":float(close) if confirmed else None,
                     "sweep_id":identity("crt-sweep",policy,zone["range_id"],side,bar.model_dump(),available)}
            if confirmed:
                zone["sweep"]=(side,extreme,available)
                details["reclaim_id"]=identity("crt-reclaim",policy,details["sweep_id"],str(close),available)
                zone["sweep_evidence"]=details
                publish(zone,side+"_SWEPT_RECLAIMED","SAME_CANDLE_RECLAIM",available,bar,**details)
            else:
                zone["candidate_due"]=index+rules.candidate_age
                zone["candidate_side"]=side
                zone["sweep_evidence"]=details
                publish(zone,side+"_SWEEP_CANDIDATE","NO_SAME_CANDLE_RECLAIM",available,bar,**details)
        index+=1
    while pending is not None:
        release(pending)
        pending=next(refs,None)

    for zone in book.values():
        pages["current"].append(zone["latest"])
    key=snapshot.hexdigest()
    return {"crt_"+name+"s":list(pages[name].events) for name in ("formation","sweep","revision")} | {
        "crt_page":{"snapshot":key,**{name+"s":pages[name].finish(key) for name in ("formation","sweep","revision")}},
        "current_crt":{"snapshot":key,**pages["current"].finish(key),"items":list(reversed(pages["current"].events))},
        "quality":dict(quality), "reference_frame":frame,
        "identity_policy_version":IDENTITY_VERSION}
