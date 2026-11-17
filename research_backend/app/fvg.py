"""Causal FVG v1. Input is one validated, cutoff-filtered candle series."""
from collections import deque
from dataclasses import dataclass
from decimal import Decimal as D
from hashlib import sha256
from heapq import heappop, heappush
import json
from typing import Literal

from pydantic import BaseModel, Field, FiniteFloat, model_validator

from .indicator_coverage import MarketSchedule
from .structure_continuity import continuity_reason
from .causal_timing import evidence as timing_evidence
from .structure_pages import EventPage
from .indicators import true_range

VERSION = 1
IDENTITY_POLICY_VERSION = 2
BPS = D(10_000)
EPS = D(0)


class FVGRules(BaseModel):
    min_price: FiniteFloat = Field(default=0, ge=0)
    min_bps: FiniteFloat = Field(default=0, ge=0, le=10_000)
    min_ticks: int = Field(default=0, ge=0, le=10000)
    tick_size: FiniteFloat | None = Field(default=None, gt=0)
    tick_size_reference: str | None = Field(default=None, min_length=1, max_length=120)
    atr_multiple: FiniteFloat = Field(default=0, ge=0, le=100)
    atr_period: int = Field(default=14, ge=2, le=100)
    displacement: Literal["none", "directional_body_bps_v1"] = "none"
    min_body_bps: FiniteFloat = Field(default=0, ge=0, le=10_000)
    mitigation: Literal["wick","close"] = "wick"
    max_age_bars: int = Field(default=250, ge=1, le=500)
    market_schedule: MarketSchedule = Field(default_factory=lambda: MarketSchedule(kind="unknown"))
    history_bars: Literal[2000,10000] = 2000
    history_start: int | None = Field(default=None, ge=0, le=32_503_680_000)

    @model_validator(mode="after")
    def check_options(self):
        if (self.tick_size is None) != (self.tick_size_reference is None):
            raise ValueError("Tick size and its declared reference must be supplied together")
        if (self.history_bars == 10000) != (self.history_start is not None):
            raise ValueError("Extended FVG history requires a fixed history_start")
        return self

    def canonical(self):
        data=self.model_dump()
        for key in ("min_price","min_bps","tick_size","atr_multiple","min_body_bps"):
            if data[key] is not None:
                data[key]=float(data[key])
        return data


class FVGDelivery(BaseModel):
    formation_limit: int = Field(default=40, ge=0, le=250)
    revision_limit: int = Field(default=60, ge=0, le=250)
    current_limit: int = Field(default=12, ge=0, le=100)
    formation_cursor: str | None = Field(default=None, max_length=180)
    revision_cursor: str | None = Field(default=None, max_length=180)
    current_cursor: str | None = Field(default=None, max_length=180)


def identity(*values):
    return sha256(json.dumps(values,sort_keys=True,separators=(",",":")).encode()).hexdigest()


@dataclass
class Zone:
    id: str
    side: str
    lower: D
    upper: D
    dates: tuple
    formation_time: int
    formation_available_at: int
    expires: int
    formation_ohlc: tuple
    formation_fingerprint: str
    depth: D = EPS
    revision: int = 0
    latest: dict | None = None
    recorded_dependencies: bool = False

    @property
    def face(self):
        return self.upper-self.depth if self.side=="BULLISH" else -(self.lower+self.depth)


def overlap_sample(book, zone):
    matches=[]
    for item in book.values():
        if item.lower>=zone.upper or item.upper<=zone.lower:
            continue
        if len(matches)==8:
            return matches, True
        nested=(item.lower<=zone.lower and zone.upper<=item.upper) or (zone.lower<=item.lower and item.upper<=zone.upper)
        matches.append({"id":item.id,"direction":"SAME" if item.side==zone.side else "OPPOSITE",
                        "geometry":"NESTED" if nested else "PARTIAL"})
    return matches, False


def evaluate_fvg(rows, step, cutoff, rules, delivery, provenance, frozen, *, on_formation=None):
    source_keys = ("source", "symbol", "timeframe", "feed_id", "dataset_id", "price_basis")
    scope = {key: provenance.get(key) for key in source_keys}
    parameters = rules.canonical()
    event_parameters = {key: value for key, value in parameters.items() if key not in {"history_start", "history_bars"}}
    strict=provenance.get("timing_policy")=="STRICT_CAUSAL"
    authority=provenance.get("context_authority","CLIENT_SUPPLIED_UNVERIFIED")
    policy = (VERSION, IDENTITY_POLICY_VERSION, "STRICT_CAUSAL", 2) if strict else (VERSION, IDENTITY_POLICY_VERSION)
    config = identity("fvg-configuration", policy, event_parameters)
    source = identity("fvg-market", scope, step)
    snapshot = sha256(identity("fvg-snapshot", policy, parameters, cutoff, frozen, provenance, step).encode())
    formations=EventPage("fvg_formations",delivery.formation_limit,delivery.formation_cursor)
    revisions=EventPage("fvg_revisions",delivery.revision_limit,delivery.revision_cursor)
    current=EventPage("fvg_current",delivery.current_limit,delivery.current_cursor)
    book={}
    heaps={"BULLISH":[],"BEARISH":[]}
    expires=[]
    window=deque(maxlen=3)
    previous=None
    index=-1
    atr_evidence = sha256()
    atr_count=0
    atr_total=0.0
    atr_value=0.0
    warnings=set()
    if rules.min_ticks and rules.tick_size is None:
        warnings.add("FVG_TICK_SIZE_UNAVAILABLE")
    if rules.min_ticks and rules.tick_size is not None:
        warnings.add("FVG_TICK_SIZE_DECLARED")

    def publish(zone,state,reason,bar,available,candle_fingerprint,observed,*,continuity_from=None,**extra):
        prior = zone.latest
        zone.revision+=1
        fingerprint = zone.formation_fingerprint if prior is None else identity(
            "fvg-transition-evidence", policy, prior["id"], candle_fingerprint,
            state, reason, str(zone.depth), observed, continuity_from,
        )
        event={"id":identity("fvg-event",policy,zone.id,zone.revision,fingerprint),
               "fvg_id":zone.id,"algorithm_version":VERSION,"identity_policy_version":IDENTITY_POLICY_VERSION,
               "configuration_fingerprint":config,
               "input_fingerprint":fingerprint,"provenance_id":source,"direction":zone.side,
               "state":state,"reason":reason,"revision":zone.revision,
               "a_time":zone.dates[0],"b_time":zone.dates[1],"c_time":zone.dates[2],
               "formation_ohlc":[list(candle) for candle in zone.formation_ohlc],"lower":float(zone.lower),"upper":float(zone.upper),"width":float(zone.upper-zone.lower),
               "remaining_lower":float(zone.lower if zone.side=="BULLISH" else zone.lower+zone.depth),
               "remaining_upper":float(zone.upper-zone.depth if zone.side=="BULLISH" else zone.upper),
               "mitigation_pct":float(100*zone.depth/(zone.upper-zone.lower)),
               "formation_confirmation_time":zone.formation_time,
               "formation_available_at":zone.formation_available_at,"observation_time":bar.time,
               "confirmation_time":bar.time+step,"available_at":available,"evidence_cutoff":available,
               "observed_price":observed,"warnings":sorted(warnings),**extra}
        if strict:
            decision=("STRADDLING_OBSERVATION" if state=="UNCERTAIN" else
                      "EXPIRED_REFERENCE" if state=="EXPIRED" else
                      "AMBIGUOUS_INTRABAR_ORDER" if reason=="GAP_THROUGH_UNOBSERVED" else
                      "INSUFFICIENT_HISTORY" if continuity_from is not None else
                      "UNKNOWN_COVERAGE" if rules.market_schedule.kind=="unknown" else
                      "UNVERIFIED_AVAILABILITY" if not zone.recorded_dependencies or rules.atr_multiple else None)
            event["timing"]=timing_evidence(bar,step,available,zone.formation_available_at if prior else 0,
                cutoff,authority).contract(reason=decision,references=[prior["id"]] if prior else [])
        if strict and prior:
            zone.recorded_dependencies &= bar.available_at == available
        zone.latest=event
        if state in {"FULLY_MITIGATED","INVALIDATED","EXPIRED","UNCERTAIN"}:
            del book[zone.id]
        (formations if state=="ACTIVE" else revisions).append(event)
        if state=="ACTIVE" and on_formation is not None:
            on_formation(event)

    for bar,available in sorted(rows,key=lambda row:(row[1],row[0].time)):
        entry=bar.model_dump_json().encode()+f":{available};".encode()
        snapshot.update(entry)
        if previous is not None and bar.time<=previous.time:
            warnings.add("FVG_LATE_CANDLE_IGNORED")
            continue
        candle_fingerprint = sha256(entry).hexdigest()
        index+=1
        last_close=D(str(bar.close))
        contiguous=previous is not None and bar.time==previous.time+step
        if previous is not None and not contiguous:
            window.clear()
            atr_evidence = sha256()
            atr_count,atr_total,atr_value=0,0.0,0.0
            reason=continuity_reason(previous.time,bar.time,step,rules.market_schedule)
            if reason!="SCHEDULED_CLOSURE":
                warnings.add("FVG_CONTINUITY_"+reason)
                for zone in list(book.values()):
                    publish(zone,"INVALIDATED",reason,bar,available,candle_fingerprint,None,continuity_from=previous.time)
                for heap in heaps.values():
                    heap.clear()
                expires.clear()
        if rules.atr_multiple:
            height=true_range(bar,previous.close if contiguous else None)
            # Wilder ATR depends on the entire uninterrupted range sequence since its seed.
            atr_evidence.update(json.dumps((bar.time, available, height), separators=(",", ":")).encode())
            if atr_count<rules.atr_period:
                atr_count+=1
                atr_total+=height
                atr_value=atr_total/rules.atr_period
            else:
                atr_value+=(height-atr_value)/rules.atr_period
        previous=bar
        while expires and expires[0][0]<=index:
            _,id_=heappop(expires)
            if id_ in book:
                publish(book[id_],"EXPIRED","MAX_AGE",bar,available,candle_fingerprint,None)

        low,high=D(str(bar.low)),D(str(bar.high))
        for side,observed in (("BULLISH",low),("BEARISH",high)):
            sign=1 if side=="BULLISH" else -1
            if rules.mitigation=="close":
                observed=last_close
            heap=heaps[side]
            deferred=[]
            while heap and -heap[0][0]>sign*observed:
                _,id_=heappop(heap)
                zone=book.get(id_)
                if zone is None:
                    continue
                if available<=zone.formation_available_at:
                    deferred.append((-zone.face,id_))
                    # No older zone can be blocked at the same price threshold.
                    continue
                if strict and bar.time<zone.formation_available_at:
                    if bar.time+step>zone.formation_available_at:
                        publish(zone,"UNCERTAIN","STRADDLING_OBSERVATION",bar,available,candle_fingerprint,float(observed))
                    else:
                        deferred.append((-zone.face,id_))
                    continue
                far=zone.lower if sign==1 else zone.upper
                overlap=high>=zone.lower and low<=zone.upper
                if not overlap:
                    publish(zone,"INVALIDATED","GAP_THROUGH_UNOBSERVED",bar,available,candle_fingerprint,float(observed))
                    continue
                penetration=zone.upper-observed if sign==1 else observed-zone.lower
                zone.depth=max(zone.depth,min(zone.upper-zone.lower,penetration))
                if sign*last_close<sign*far:
                    publish(zone,"INVALIDATED","CLOSE_BEYOND_OPPOSITE_BOUNDARY",bar,available,candle_fingerprint,float(observed))
                elif zone.depth==zone.upper-zone.lower:
                    publish(zone,"FULLY_MITIGATED","OBSERVED_FULL_DEPTH",bar,available,candle_fingerprint,float(observed))
                else:
                    publish(zone,"PARTIALLY_MITIGATED","OBSERVED_DEPTH",bar,available,candle_fingerprint,float(observed))
                    deferred.append((-zone.face,id_))
            for item in deferred:
                heappush(heap,item)

        window.append((bar,candle_fingerprint))
        if len(window)!=3:
            continue
        a,b,c=(row[0] for row in window)
        if a.high<c.low:
            side,lo,hi="BULLISH",D(str(a.high)),low
        elif a.low>c.high:
            side,lo,hi="BEARISH",high,D(str(a.low))
        else:
            continue
        if rules.min_ticks and rules.tick_size is None:
            continue
        if rules.atr_multiple and atr_count<rules.atr_period:
            warnings.add("FVG_ATR_WARMUP")
            continue
        width=hi-lo
        minimum=max(D(str(rules.min_price)),lo*D(str(rules.min_bps))/BPS,
                    D(str(rules.tick_size or 0))*rules.min_ticks,D(str(atr_value))*D(str(rules.atr_multiple)))
        if width<minimum:
            continue
        if rules.displacement!="none":
            middle=(D(str(b.close))-D(str(b.open)))*(1 if side=="BULLISH" else -1)
            if middle<=0 or middle<D(str(b.open))*D(str(rules.min_body_bps))/BPS:
                continue
        dates=a.time,b.time,c.time
        evidence = identity("fvg-formation-evidence", policy,
                            [fingerprint for _,fingerprint in window],
                            (atr_evidence.hexdigest(),atr_value) if rules.atr_multiple else None)
        zone=Zone(identity("fvg-zone",policy,config,source,side,evidence),side,lo,hi,
                  dates,c.time+step,available,index+rules.max_age_bars,
                  tuple((item.open,item.high,item.low,item.close) for item,_ in window),evidence)
        if strict:
            zone.recorded_dependencies=all(item.available_at is not None and item.available_at>=item.time+step
                                           for item,_ in window)
        overlaps,truncated=overlap_sample(book,zone)
        book[zone.id]=zone
        heappush(heaps[side],(-zone.face,zone.id))
        heappush(expires,(zone.expires,zone.id))
        publish(zone,"ACTIVE","THREE_CLOSED_CANDLES",bar,available,candle_fingerprint,None,overlaps=overlaps,
                overlaps_truncated=truncated,atr_at_formation=atr_value if rules.atr_multiple else None)

    key=snapshot.hexdigest()
    if current.limit:
        def priority(zone):
            distance=max(zone.lower-last_close,last_close-zone.upper,EPS)
            return distance,zone.id
        for zone in sorted(book.values(),key=priority,reverse=True):
            current.append(zone)
    else:
        current.total=len(book)
    return {"formations":list(formations.events),"revisions":list(revisions.events),
            "fvg_page":{"snapshot":key,"formations":formations.finish(key),"revisions":revisions.finish(key)},
            "current_fvgs":{"snapshot":key,"selection":"nearest_close",**current.finish(key),
                            "items":[zone.latest for zone in reversed(current.events)]},
            "identity_policy_version":IDENTITY_POLICY_VERSION, "configuration_fingerprint":config,
            "provenance_id":source,"warnings":sorted(warnings)}
