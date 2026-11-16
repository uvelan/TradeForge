"""Liquidity observations derived from canonical closed-candle structure evidence."""
from dataclasses import dataclass, field
from decimal import Decimal
from hashlib import sha256
from heapq import heappop, heappush
import json
from math import floor, log, log1p

from pydantic import BaseModel, Field, FiniteFloat, model_validator

from .structure_pages import EventPage

VERSION = 1
BPS = Decimal(10_000)


class LiquidityRules(BaseModel):
    equality_bps: FiniteFloat = Field(default=5.0, ge=0, le=100)
    penetration_bps: FiniteFloat = Field(default=0.0, ge=0, le=100)
    reclaim_bps: FiniteFloat = Field(default=0.0, ge=0, le=100)
    min_touches: int = Field(default=2, ge=2, le=16)
    max_touches: int = Field(default=16, ge=2, le=32)
    min_separation_bars: int = Field(default=2, ge=1, le=200)
    max_age_bars: int = Field(default=250, ge=2, le=4000)

    @model_validator(mode="after")
    def valid_touches(self):
        if self.min_touches > self.max_touches:
            raise ValueError("Minimum liquidity touches must be within the maximum")
        return self

    def identity(self):
        return {**self.model_dump(), **{key: float(getattr(self, key))
                for key in ("equality_bps", "penetration_bps", "reclaim_bps")}}


class LiquidityDelivery(BaseModel):
    pool_limit: int = Field(default=60, ge=0, le=500)
    sweep_limit: int = Field(default=40, ge=0, le=500)
    pool_cursor: str | None = Field(default=None, max_length=180)
    sweep_cursor: str | None = Field(default=None, max_length=180)


def digest(*fields):
    return sha256(json.dumps(fields, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass
class Pool:
    id: str
    side: str
    anchor: Decimal
    lower: Decimal
    upper: Decimal
    expires: int
    last_pivot_index: int
    members: list[dict] = field(default_factory=list)
    state: str = "FORMING"
    revision: int = 0
    activated_at: int | None = None
    activation_observation_time: int | None = None
    price_generation: int = 0
    candidate_id: str | None = None


class LiquidityStream:
    def __init__(self, rules: LiquidityRules, delivery: LiquidityDelivery, step: int):
        self.rules, self.step = rules, step
        self.pools = EventPage("pools", delivery.pool_limit, delivery.pool_cursor)
        self.sweeps = EventPage("sweeps", delivery.sweep_limit, delivery.sweep_cursor)
        self.active: dict[str, Pool] = {}
        self.buckets: dict[tuple, set[str]] = {}
        self.eq = Decimal(str(rules.equality_bps))/BPS
        self.penetration = Decimal(str(rules.penetration_bps))/BPS
        self.reclaim = Decimal(str(rules.reclaim_bps))/BPS
        width = log1p(float(self.eq))
        self.width = max(1e-12, width) if self.eq else 0
        self.expirations = []
        self.wick = {"HIGH": [], "LOW": []}
        self.close = {"HIGH": [], "LOW": []}

    def bucket(self, side, price):
        return side, floor(log(float(price))/self.width) if self.width else price

    def near(self, side, price):
        _, key = self.bucket(side, price)
        keys = (key,) if not self.width else range(key-2,key+3)
        matches = (self.active[id_] for index in keys for id_ in self.buckets.get((side,index), ()))
        return min((pool for pool in matches if abs(price-pool.anchor)<=pool.anchor*self.eq),
                   key=lambda pool: (abs(price-pool.anchor), pool.id), default=None)

    def transition(self, pool, state, bar, available, fingerprint, provenance_id, reason):
        pool.state = state
        pool.revision += 1
        entry = {"id":digest(VERSION,pool.id,pool.revision,state,bar.time,fingerprint),
                 "pool_id":pool.id,"revision":pool.revision,"state":state,"reason":reason,
                 "classification":"EQH" if pool.side=="HIGH" else "EQL",
                 "liquidity_side":"BSL" if pool.side=="HIGH" else "SSL",
                 "lower":float(pool.lower),"upper":float(pool.upper),"touches":len(pool.members),
                 "constituents":list(pool.members),"activated_at":pool.activated_at,
                 "activation_observation_time":pool.activation_observation_time,
                 "observation_time":bar.time,"confirmation_time":bar.time+self.step,"available_at":available,
                 "evidence_cutoff":available,"input_fingerprint":fingerprint,"algorithm_version":VERSION,
                 "provenance_id":provenance_id,"status":"provisional" if state in {"FORMING","SWEEP_CANDIDATE"} else
                 "invalidated" if state in {"INVALIDATED","EXPIRED"} else "confirmed"}
        self.pools.append(entry)
        if state in {"SWEPT","BROKEN","EXPIRED","INVALIDATED"}:
            self.active.pop(pool.id)
            key = self.bucket(pool.side,pool.anchor)
            self.buckets[key].remove(pool.id)
            if not self.buckets[key]:
                del self.buckets[key]
        return entry

    def index(self,pool):
        pool.price_generation+=1
        high=pool.side=="HIGH"
        boundary=pool.upper if high else -pool.lower
        threshold=boundary+abs(boundary)*self.penetration
        heappush(self.wick[pool.side],(threshold,pool.id,pool.price_generation))
        heappush(self.close[pool.side],(boundary,pool.id,pool.price_generation))

    def crossed(self, heaps, side, value):
        heap=heaps[side]
        touched=[]
        while heap and heap[0][0]<value:
            _,id_,generation=heappop(heap)
            pool=self.active.get(id_)
            if pool is not None and pool.price_generation==generation:
                touched.append(id_)
        return touched

    def observe(self,bar,available,index,gap,pivots,breaks,fingerprint,provenance_id):
        # Decisions use the pools available before this observation.
        if gap:
            for pool in list(self.active.values()):
                self.transition(pool,"INVALIDATED",bar,available,fingerprint,provenance_id,gap)
            self.expirations.clear()
            for heap in (*self.wick.values(),*self.close.values()):
                heap.clear()
        while self.expirations and self.expirations[0][0]<=index:
            _,id_=heappop(self.expirations)
            if id_ in self.active:
                self.transition(self.active[id_],"EXPIRED",bar,available,fingerprint,provenance_id,"MAX_AGE")
        for side,extreme in (("HIGH",bar.high),("LOW",bar.low)):
            sign=1 if side=="HIGH" else -1
            close=sign*Decimal(str(bar.close))
            wick=sign*Decimal(str(extreme))
            wick_hits=set(self.crossed(self.wick,side,wick))
            close_hits=set(self.crossed(self.close,side,close))
            for id_ in sorted(wick_hits | close_hits):
                pool=self.active[id_]
                signed_inner=pool.lower if sign==1 else -pool.upper
                signed_outer=pool.upper if sign==1 else -pool.lower
                reclaim=signed_inner-abs(signed_inner)*self.reclaim
                event_state=None
                if pool.activated_at is not None and pool.activated_at<available:
                    if id_ in close_hits:
                        event_state="BROKEN"
                    elif id_ in wick_hits and close<reclaim:
                        event_state="SWEPT"
                    elif id_ in wick_hits and pool.state=="ACTIVE":
                        event_state="SWEEP_CANDIDATE"
                if event_state:
                    related=[event["id"] for event in breaks
                             if event["side"]==side and pool.lower<=Decimal(str(event["level_price"]))<=pool.upper]
                    revision=self.transition(pool,event_state,bar,available,fingerprint,provenance_id,"CLOSED_CANDLE")
                    crossing={"id":digest(VERSION,revision["id"],"CROSSING"),"pool_id":pool.id,
                              "pool_revision_id":revision["id"],"state":event_state,
                              "status":"provisional" if event_state=="SWEEP_CANDIDATE" else "confirmed",
                              "liquidity_side":revision["liquidity_side"],"lower":revision["lower"],
                              "upper":revision["upper"],"activated_at":pool.activated_at,
                              "constituents":revision["constituents"],
                              "observation_time":bar.time,"confirmation_time":bar.time+self.step,
                              "available_at":available,"evidence_cutoff":available,
                              "ohlc":{key:getattr(bar,key) for key in ("open","high","low","close")},
                              "opening_crossed":sign*Decimal(str(bar.open))>signed_outer,
                              "penetration_threshold":float(sign*(signed_outer+abs(signed_outer)*self.penetration)),
                              "reclaim_boundary":float(sign*reclaim),
                              "related_structure_ids":related,"candidate_id":pool.candidate_id,
                              "algorithm_version":VERSION,"provenance_id":provenance_id,
                              "input_fingerprint":fingerprint}
                    self.sweeps.append(crossing)
                    if event_state=="SWEEP_CANDIDATE":
                        pool.candidate_id=crossing["id"]
                if id_ in self.active:
                    # Reinsert only the thresholds removed by this observation.
                    if id_ in wick_hits:
                        heappush(self.wick[side],(signed_outer+abs(signed_outer)*self.penetration,id_,pool.price_generation))
                    if id_ in close_hits:
                        heappush(self.close[side],(signed_outer,id_,pool.price_generation))
        for pivot,pivot_index in pivots:
            price=Decimal(str(pivot["price"]))
            side=pivot["side"]
            pool=self.near(side,price)
            if pool is not None and (pivot_index-pool.last_pivot_index<self.rules.min_separation_bars
                                    or len(pool.members)>=self.rules.max_touches):
                continue
            if pool is None:
                id_=digest(VERSION,self.rules.identity(),pivot["id"])
                pool=Pool(id_,side,price,price,price,index+self.rules.max_age_bars,pivot_index)
                self.active[id_]=pool
                self.buckets.setdefault(self.bucket(side,price),set()).add(id_)
                heappush(self.expirations,(pool.expires,id_))
            pool.last_pivot_index=pivot_index
            pool.members.append({"id":pivot["id"],"price":pivot["price"],"time":pivot["pivot_time"],
                                 "available_at":pivot["available_at"]})
            pool.lower=min(pool.lower,price)
            pool.upper=max(pool.upper,price)
            state=pool.state
            if state=="FORMING" and len(pool.members)>=self.rules.min_touches:
                state="ACTIVE"
                pool.activated_at=available
                pool.activation_observation_time=bar.time
            self.transition(pool,state,bar,available,fingerprint,provenance_id,"CONFIRMED_PIVOT")
            if state!="FORMING":
                self.index(pool)

    def finish(self, structure_snapshot, provenance_id):
        snapshot=digest(VERSION,structure_snapshot,self.rules.identity())
        return {"pools":list(self.pools.events),"sweeps":list(self.sweeps.events),
                "liquidity_page":{"snapshot":snapshot,"pools":self.pools.finish(snapshot),
                                  "sweeps":self.sweeps.finish(snapshot)},
                "provenance_id":provenance_id}
