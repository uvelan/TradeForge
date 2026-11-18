"""Versioned order blocks derived from confirmed structure observations."""
from bisect import bisect_right
from collections import Counter, deque
from dataclasses import dataclass
from decimal import Decimal
from heapq import heapify, heappop, heappush
from typing import Literal

from pydantic import BaseModel, Field, FiniteFloat

from .strategy_evidence import EvidenceRegistry
from .causal_timing import evidence as timing_evidence, strategy_eligible
from .fvg import identity
from .structure_pages import EventPage


ALGORITHM_VERSION = 1
IDENTITY_VERSION = 1
POLICY = (ALGORITHM_VERSION, IDENTITY_VERSION)
ZERO = Decimal(0)


def price(value):
    return Decimal(str(value))


class OrderBlockRules(BaseModel):
    lookback: int = Field(default=20, ge=1, le=100)
    candidate_age: int = Field(default=20, ge=1, le=100)
    min_body: FiniteFloat = Field(default=0, ge=0)
    min_break: FiniteFloat = Field(default=0, ge=0)
    min_displacement: FiniteFloat = Field(default=0, ge=0)
    geometry: Literal["wick", "body"] = "wick"
    allow_choch: bool = False
    include_context: bool = False
    mitigation: Literal["wick", "close"] = "wick"
    min_penetration_pct: FiniteFloat = Field(default=0, ge=0, le=100)
    max_age_bars: int = Field(default=250, ge=1, le=500)


class OrderBlockDelivery(BaseModel):
    formation_limit: int = Field(default=40, ge=0, le=250)
    revision_limit: int = Field(default=60, ge=0, le=250)
    current_limit: int = Field(default=12, ge=0, le=100)
    formation_cursor: str | None = Field(default=None, max_length=180)
    revision_cursor: str | None = Field(default=None, max_length=180)
    current_cursor: str | None = Field(default=None, max_length=180)


@dataclass
class Block:
    id: str
    sign: int
    lower: Decimal
    upper: Decimal
    expires: int
    formation: dict
    depth: Decimal = ZERO
    latest: dict | None = None
    touched: bool = False

    @property
    def width(self):
        return self.upper - self.lower

    @property
    def face(self):
        return self.upper if self.sign == 1 else -self.lower


class OrderBlockStream:
    def __init__(self, rules, structure, provenance, step, delivery, *, on_formation=None, timing_cutoff=None, registry=None):
        self.on_formation = on_formation
        self.timing_cutoff = timing_cutoff
        self.registry = registry if registry is not None else EvidenceRegistry()
        self.timing_scope = provenance
        self.segment_recorded = True
        self.timing_authority = provenance.get("context_authority","CLIENT_SUPPLIED_UNVERIFIED")
        self.policy = (*POLICY,"STRICT_CAUSAL",3) if timing_cutoff is not None else POLICY
        self.context_lookup = None
        self.rules = rules
        self.structure = structure
        self.step = step
        self.scope = {key: provenance.get(key) for key in
                      ("source", "symbol", "timeframe", "dataset_id", "feed_id", "price_basis")}
        self.source = identity("ob-market", self.scope, step)
        structure_params = structure.identity_parameters()
        for key in ("history_start", "history_bars"):
            structure_params.pop(key, None)
        self.config = identity("ob-config", self.policy, rules.model_dump(exclude={"include_context"}),
                               structure.algorithm_version, structure_params)
        self.formations = EventPage("ob_formations", delivery.formation_limit, delivery.formation_cursor)
        self.revisions = EventPage("ob_revisions", delivery.revision_limit, delivery.revision_cursor, max_before=10_000*500)
        self.current = EventPage("ob_current", delivery.current_limit, delivery.current_cursor)
        self.candidates = deque(maxlen=min(rules.lookback, rules.candidate_age))
        self.book = {}
        self.by_price = {1: [], -1: []}
        self.expiry = []
        self.previous_time = None
        self.latest_price = None
        self.rejections = Counter()
        self.penetration = price(rules.min_penetration_pct)/100
        self.minimum_body = price(rules.min_body)
        self.minimum_break = price(rules.min_break)
        self.minimum_displacement = price(rules.min_displacement)
        self.fvg_times = []
        self.fvg_references = []
        self.latest_sweep = None

    def record_fvg(self, event):
        self.fvg_times.append((event["available_at"], event["c_time"]))
        self.fvg_references.append({"id":event["fvg_id"], "available_at":event["available_at"]})

    def record_sweep(self, event):
        witness = identity("ob-sweep-reference",self.source,event["state"],event["liquidity_side"],
                           [(p["time"],p["price"],p["available_at"]) for p in event["constituents"]],
                           event["observation_time"],event["available_at"],event["ohlc"])
        self.latest_sweep = {"id":event["id"], "witness_id":witness, "available_at":event["available_at"]}

    def context(self, bar, available):
        if self.context_lookup is not None:
            return self.context_lookup(available,bar.time)
        if not self.rules.include_context:
            return {"fvg":None,"liquidity_sweep":None,"status":"NOT_REQUESTED"}
        index = bisect_right(self.fvg_times, (available,bar.time))-1
        gap = self.fvg_references[index] if index >= 0 else None
        return {"fvg":gap,"liquidity_sweep":self.latest_sweep,
                "status":"AVAILABLE" if gap or self.latest_sweep else "NO_AVAILABLE_CONTEXT"}

    def put(self, zone):
        threshold = zone.depth
        inclusive = not zone.touched
        minimum = zone.width*self.penetration
        if zone.touched and zone.depth < minimum:
            threshold = minimum
            inclusive = True
        heappush(self.by_price[zone.sign], (threshold-zone.face, not inclusive, zone.id))

    def publish(self, zone, state, reason, bar, available, *, observed=None, previous_time=None, timing_reason=None):
        prior = zone.latest
        evidence = zone.formation["formation_fingerprint"] if prior is None else identity(
            "ob-transition", POLICY, prior["id"], state, reason, bar.model_dump(),
            available, str(zone.depth), observed, previous_time,
        )
        revision = 1 if prior is None else prior["revision"] + 1
        event = {**zone.formation,
                 "id": identity("ob-event", self.policy, zone.id, revision, evidence),
                 "evidence_fingerprint": evidence, "state": state, "reason": reason, "revision": revision,
                 "observation_time": bar.time, "confirmation_time": bar.time+self.step,
                 "available_at": available, "observed_price": observed,
                 "mitigation_pct": float(100*zone.depth/zone.width),
                 "remaining_lower": float(zone.lower if zone.sign == 1 else zone.lower+zone.depth),
                 "remaining_upper": float(zone.upper-zone.depth if zone.sign == 1 else zone.upper)}
        if self.timing_cutoff is not None:
            decision=timing_reason or ("EXPIRED_REFERENCE" if state=="EXPIRED" else
                "INSUFFICIENT_HISTORY" if state=="INVALIDATED" else
                "UNVERIFIED_AVAILABILITY" if not zone.formation["timing_recorded"] or not self.segment_recorded else None)
            barrier=zone.formation["first_visibility_time"] if prior else 0
            event["timing"]=timing_evidence(bar,self.step,available,barrier,self.timing_cutoff,
                self.timing_authority).contract(reason=decision,references=[zone.formation["activation_id"]] if prior else [zone.formation["bos_id"]])
            if not prior:
                zone.formation["activation_id"]=event["id"]
            self.registry.publish(event,"order_block",self.timing_scope,self.config,family=zone.id,
                active=state in {"ACTIVE","TOUCHED","PARTIALLY_MITIGATED"},
                predicate="WHOLE_CANDLE" if prior else "FORMATION",
                dependencies=[prior["id"]] if prior else [],
                coverage=self.structure.market_schedule is not None and self.structure.market_schedule.kind!="unknown")
        if self.timing_cutoff is not None and prior:
            zone.formation["timing_recorded"] &= bar.available_at == available
        zone.latest = event
        if state in {"FULLY_MITIGATED", "INVALIDATED", "EXPIRED", "UNCERTAIN"}:
            del self.book[zone.id]
        (self.formations if prior is None else self.revisions).append(event)

    def advance(self, bar, available, index):
        pending_expiry = []
        while self.expiry and self.expiry[0][0] <= index:
            deadline, key = heappop(self.expiry)
            zone = self.book.get(key)
            if zone is None:
                continue
            if self.timing_cutoff is None and available <= zone.formation["first_visibility_time"]:
                pending_expiry.append((deadline,key))
            else:
                self.publish(zone, "EXPIRED", "MAX_AGE", bar, available)
        for item in pending_expiry:
            heappush(self.expiry,item)
        close, low, high = price(bar.close), price(bar.low), price(bar.high)
        for sign, extreme in ((1, low), (-1, high)):
            heap = self.by_price[sign]
            observed = close if self.rules.mitigation == "close" else extreme
            boundary = -sign*observed
            deferred = []
            while heap and (heap[0][0] < boundary or (heap[0][0] == boundary and not heap[0][1])):
                item = heappop(heap)
                zone = self.book.get(item[2])
                if zone is None:
                    continue
                if available <= zone.formation["first_visibility_time"]:
                    deferred.append(item)
                    continue
                if self.timing_cutoff is not None and bar.time < zone.formation["first_visibility_time"]:
                    if bar.time+self.step > zone.formation["first_visibility_time"]:
                        self.publish(zone,"UNCERTAIN","STRADDLING_OBSERVATION",bar,available,
                                     timing_reason="STRADDLING_OBSERVATION")
                    else:
                        deferred.append(item)
                    continue
                if high < zone.lower or low > zone.upper:
                    self.publish(zone, "INVALIDATED", "GAP_THROUGH_UNOBSERVED", bar, available,
                                 observed=float(observed))
                    continue
                depth = min(zone.width, zone.face-sign*observed)
                far = zone.lower if sign == 1 else zone.upper
                minimum = zone.width*self.penetration
                if sign*close < sign*far:
                    zone.depth = max(zone.depth, depth)
                    self.publish(zone, "INVALIDATED", "CLOSE_BEYOND_FAR_EDGE", bar, available,
                                 observed=float(observed))
                    continue
                if depth >= minimum:
                    zone.depth = max(zone.depth, depth)
                if zone.depth == zone.width:
                    self.publish(zone, "FULLY_MITIGATED", "OBSERVED_FULL_DEPTH", bar, available,
                                 observed=float(observed))
                    continue
                state = "PARTIALLY_MITIGATED" if zone.depth else "TOUCHED"
                reason = "OBSERVED_PENETRATION" if zone.depth else "OBSERVED_TOUCH"
                self.publish(zone, state, reason, bar, available, observed=float(observed))
                zone.touched = True
                deferred.append(zone)
            for item in deferred:
                if isinstance(item, Block):
                    self.put(item)
                else:
                    heappush(heap, item)

    def form(self, event, bar, available, index):
        if event["status"] != "confirmed" or event["type"] not in {"BOS", "CHOCH"}:
            return
        if self.timing_cutoff is not None and (not strategy_eligible(event, self.timing_cutoff, registry=self.registry) or
                self.minimum_displacement and bar.time<event["level_available_at"]):
            self.rejections["INELIGIBLE_STRUCTURAL_DEPENDENCY"] += 1
            return
        if event["type"] == "CHOCH" and not self.rules.allow_choch:
            self.rejections["CHOCH_DISABLED"] += 1
            return
        sign = 1 if event["direction"] == "BULLISH" else -1
        if sign*(price(event["break_price"])-price(event["level_price"])) < self.minimum_break:
            self.rejections["BREAK_DISTANCE"] += 1
            return
        if self.minimum_displacement and sign*(price(bar.close)-price(bar.open)) < self.minimum_displacement:
            self.rejections["DISPLACEMENT"] += 1
            return
        for candidate, stamp in reversed(self.candidates):
            body = sign*(price(candidate.open)-price(candidate.close))
            if body > 0 and body >= self.minimum_body and stamp <= (
                    available if self.timing_cutoff is None else bar.time):
                break
        else:
            self.rejections["NO_ELIGIBLE_OPPOSING_CANDLE"] += 1
            return
        lower, upper = (candidate.low, candidate.high) if self.rules.geometry == "wick" else (
            min(candidate.open, candidate.close), max(candidate.open, candidate.close))
        captured = candidate.model_dump()
        candle_id = identity("ob-candle", self.source, captured, stamp)
        evidence = identity("ob-formation", self.policy, candle_id, event["local_witness"])
        key = identity("ob-zone", self.policy, self.config, self.source, event["direction"], evidence)
        formation = {"ob_id":key, "direction":event["direction"], "break_direction":event["direction"],
                     "algorithm_version":ALGORITHM_VERSION, "identity_policy_version":IDENTITY_VERSION,
                     "structure_version":self.structure.algorithm_version, "configuration_fingerprint":self.config,
                     "formation_fingerprint":evidence, "provenance_id":self.source,
                     "candidate_id":candle_id, "candidate_time":candidate.time, "candidate_available_at":stamp,
                     "candidate_ohlc":[candidate.open,candidate.high,candidate.low,candidate.close],
                     "geometry":self.rules.geometry, "lower":lower, "upper":upper,
                     "bos_id":event["id"], "broken_pivot_id":event["broken_pivot_id"],
                     "structure_witness_id":event["local_witness"], "break_type":event["type"],
                     "break_time":event["break_time"], "break_price":event["break_price"],
                     "level_price":event["level_price"], "bos_confirmation_time":event["confirmation_time"],
                     "bos_available_at":available, "first_visibility_time":available,
                     "context":self.context(bar,available)}
        if self.timing_cutoff is not None:
            formation["timing_recorded"]=candidate.available_at==stamp
        zone = Block(key, sign, price(lower), price(upper), index+self.rules.max_age_bars, formation)
        self.book[key] = zone
        self.put(zone)
        heappush(self.expiry,(zone.expires,key))
        self.publish(zone, "ACTIVE", "CONFIRMED_"+event["type"], bar, available)
        if self.on_formation is not None:
            self.on_formation(zone.latest)

    def observe(self, bar, available, index, gap, _pivots, breaks, _fingerprint, _provenance):
        if self.previous_time is not None and self.previous_time+self.step != bar.time:
            self.candidates.clear()
        if gap:
            for zone in list(self.book.values()):
                self.publish(zone, "INVALIDATED", gap, bar, available, previous_time=self.previous_time)
            for heap in self.by_price.values():
                heap.clear()
            self.expiry.clear()
        if self.timing_cutoff is not None:
            self.segment_recorded = (self.segment_recorded or bool(gap)) and bar.available_at==available
            if bar.available_at!=available:
                self.registry.gap("order_block",self.config,self.timing_scope,available)
        self.advance(bar, available, index)
        for event in breaks:
            self.form(event, bar, available, index)
        # Retired price entries may otherwise remain out of reach until the end of a run.
        budget = max(64, len(self.book)*3)
        for heap in self.by_price.values():
            if len(heap) > budget:
                heap[:] = [entry for entry in heap if entry[2] in self.book]
                heapify(heap)
        self.candidates.append((bar, available))
        self.previous_time, self.latest_price = bar.time, price(bar.close)

    def finish(self, structure_snapshot, *, context_snapshot=None):
        snapshot = identity("ob-snapshot", self.policy, self.config, self.rules.include_context, structure_snapshot)
        if context_snapshot is not None:
            snapshot=identity("ob-context-snapshot",snapshot,context_snapshot)
        if self.current.limit:
            def priority(zone):
                return max(zone.lower-self.latest_price,self.latest_price-zone.upper,ZERO),zone.id
            for zone in sorted(self.book.values(),key=priority,reverse=True):
                self.current.append(zone.latest)
        else:
            self.current.total = len(self.book)
        return {"ob_formations":list(self.formations.events),"ob_revisions":list(self.revisions.events),
                "ob_page":{"snapshot":snapshot,"formations":self.formations.finish(snapshot),
                           "revisions":self.revisions.finish(snapshot)},
                "current_obs":{"snapshot":snapshot,**self.current.finish(snapshot),
                               "items":list(reversed(self.current.events))},
                "rejections":dict(self.rejections),"identity_policy_version":IDENTITY_VERSION}
