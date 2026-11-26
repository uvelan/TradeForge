"""Replay an exact-time paired watch from two immutable M5 candle partitions."""
from datetime import UTC,datetime
import json
from typing import Literal
from pydantic import BaseModel, Field, model_validator

from .db import one,transaction
from .indicator_engine import Partition
from .smt_causal import FrozenFeed, StrictSmtRequest, evaluate_strict_smt
from .smt_history import record_smt,save_progress,scope_key,WatchLeaseLost
from .smt_monitor_lease import owns
from .storage import read_partition

STEP=300
SIDES=("primary","comparison")


class ArchiveWatch(BaseModel,extra="forbid"):
    primary: Partition
    comparison: Partition
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    period: int = Field(2,ge=2,le=20)
    tolerance_bps: float = Field(0,ge=0,le=100,allow_inf_nan=False)
    confirmation_bars: int = Field(1,ge=0,le=20)
    alignment_bars: Literal[0]=0

    @model_validator(mode="after")
    def window(self):
        if self.start%STEP or self.end%STEP or not 0<self.end-self.start<=60*STEP:
            raise ValueError("Select 1 to 60 aligned M5 bars within one open session")
        from .indicator_coverage import MarketSchedule,open_intervals
        if open_intervals(self.start,self.end,MarketSchedule(kind="forex_weekly"))!=[(self.start,self.end)]:
            raise ValueError("Select a single open FX interval")
        return self


def inputs(watch):
    feeds,bars={},{}
    for side in SIDES:
        pin=getattr(watch,side)
        row=one("SELECT * FROM datasets WHERE id=?",(pin.id,))
        if (not row or row["sha256"]!=pin.sha256 or row["kind"]!="candles"
                or row["provider"]!="DUKASCOPY" or row["timeframe"]!="M5" or row["price_basis"]!="BID"
                or row["rows_count"]>2000):
            raise ValueError(f"{side} requires its checksum-pinned Dukascopy M5 BID partition")
        values=read_partition(row)
        if len(values)!=row["rows_count"] or any(a["time"]>=b["time"] for a,b in zip(values,values[1:])):
            raise ValueError(f"{side} partition row count or order mismatch")
        bars[side]={b["time"]:b for b in values if watch.start<=b["time"]<watch.end}
        feeds[side]=FrozenFeed(source="DUKASCOPY_SAVED",symbol=row["symbol"],timeframe="M5",
            feed_id=row["feed_id"],price_basis="BID",dataset_id=pin.id,partitions=[pin],
            calendar={"kind":"forex_weekly"},start=watch.start,end=watch.end)
    if {f.symbol for f in feeds.values()}!={"EURUSD","GBPUSD"} or feeds["primary"].feed_id!=feeds["comparison"].feed_id:
        raise ValueError("Select EURUSD and GBPUSD from one feed")
    return feeds,bars


def checkpoint(token):
    row=one("SELECT payload FROM smt_watch_checkpoint WHERE generation=?",(token,))
    return json.loads(row["payload"]) if row else {}


def advance(watch,lease,*,batch=8,through=None):
    previous=checkpoint(lease.generation)
    frontier=previous.get("verified_through",watch.start)
    if frontier==watch.end:
        return previous
    feeds,bars=inputs(watch)
    rules=watch.model_dump(include={"period","alignment_bars","tolerance_bps","confirmation_bars"})
    provenance={"timeframe":"M5","authority":"HISTORICAL_SIMULATION","watch_start":watch.start,"watch_end":watch.end}
    for side,feed in feeds.items():
        provenance[side]=feed.model_dump(include={"source","symbol","dataset_id","price_basis","feed_id"}) | {
            "sha256":feed.partitions[0].sha256,"partitions":[feed.partitions[0].model_dump()]}
    scope=scope_key(provenance,rules)
    pending=[]
    gap=None
    for stamp in range(frontier,min(watch.end,frontier+batch*STEP,through if through is not None else watch.end),STEP):
        close=stamp+STEP
        if through is not None and close>through:
            break
        missing=[feeds[k].symbol for k in SIDES if stamp not in bars[k]]
        if missing:
            gap={"open_time":stamp,"symbols":missing}
            break
        request=StrictSmtRequest(**feeds,**rules,cutoff=close,availability_mode="HISTORICAL_SIMULATION",limit=100)
        response=evaluate_strict_smt(request)
        if response["status"] not in ("EVALUATED","PARTIAL") or not response.get("snapshot"):
            raise ValueError("Strict SMT archive unavailable: "+str(response.get("reason")))
        for event in response["events"]:
            changes=[]
            for revision in event["revisions"]:
                changes.append({"status":revision["state"],"time":revision["available_at"]})
                if revision["available_at"]!=close:
                    continue
                pivots={k:{which:dict(time=p["time"],price=p["price"],confirmed_at=p["available_at"])
                           for which,p in lane.items()} for k,lane in event["pivots"].items()}
                pending.append(dict(id=event["id"],direction=event["direction"],leader=event["leader"],**pivots,
                    confirmed_at=event["confirmed_at"],status=revision["state"],transitions=list(changes),
                    strategy_revision=revision,matching_window=event["matching_window"],chart_cutoff=close))
        frontier=close
    now=int(datetime.now(UTC).timestamp())
    result=dict(scope=scope,last_poll_at=now,verified_through=frontier,gap=gap,
                status="DEGRADED" if gap else "COMPLETE" if frontier==watch.end else "CATCHING_UP",
                last_observation={k:bars[k].get(frontier-STEP) for k in SIDES})
    with transaction() as db:
        db.execute("BEGIN IMMEDIATE")
        if not owns(db,lease):
            raise WatchLeaseLost("Archive watch lease lost")
        record_smt(pending,scope,provenance,rules,alert_after=watch.start,observed_at=now,
                   monitor_lease=lease,connection=db)
        save_progress(db,lease,result)
    return result
