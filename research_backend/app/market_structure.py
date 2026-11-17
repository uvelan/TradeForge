"""Versioned confirmed pivots and price structure over a causal candle stream."""
from collections import Counter, deque
from hashlib import sha256
import json
from typing import Literal

from pydantic import BaseModel, Field, FiniteFloat, model_validator

from .causal_timing import evidence
from .pivots import pivot_matches
from .indicator_coverage import MarketSchedule
from .structure_continuity import POLICY_VERSION, continuity_reason
from .structure_pages import EventPage, StructureDelivery


class StructureRules(BaseModel):
    algorithm_version: Literal[1, 2] = 1
    market_schedule: MarketSchedule | None = None
    left: int = Field(default=3, ge=1, le=30)
    right: int = Field(default=3, ge=1, le=30)
    price_source: Literal["wick", "close"] = "wick"
    ties: Literal["strict", "earliest"] = "strict"
    tolerance_bps: FiniteFloat = Field(default=0, ge=0, le=100)
    break_bps: FiniteFloat = Field(default=0, ge=0, le=100)
    wick_candidates: bool = False
    history_bars: Literal[2000, 10000] = 2000
    history_start: int | None = Field(default=None, ge=0, le=32_503_680_000)

    @model_validator(mode="after")
    def requires_fixed_history_window(self):
        if (self.history_bars == 10_000) != (self.history_start is not None):
            raise ValueError("Extended structure history requires a fixed history_start")
        if self.algorithm_version == 1 and self.market_schedule is not None:
            raise ValueError("Market continuity settings require structure version 2")
        return self

    def identity_parameters(self):
        if self.algorithm_version == 1:
            return self.model_dump(exclude={"algorithm_version", "market_schedule"})
        parameters = self.model_dump()
        parameters.update(tolerance_bps=float(self.tolerance_bps), break_bps=float(self.break_bps))
        parameters["market_schedule"] = (self.market_schedule or MarketSchedule(kind="unknown")).model_dump()
        return parameters


def classify(side, price, previous, bps):
    if previous is None:
        return "UNCLASSIFIED"
    delta = price - previous["price"]
    if abs(delta) <= previous["price"] * bps / 10_000:
        return "EH" if side == "HIGH" else "EL"
    if side == "HIGH":
        return "HH" if delta > 0 else "LH"
    return "HL" if delta > 0 else "LL"


def initial_direction(pivots):
    high, low = pivots.get("HIGH"), pivots.get("LOW")
    if not high or not low or "UNCLASSIFIED" in {high["classification"], low["classification"]}:
        return "UNCONFIRMED"
    if high["pivot_time"] == low["pivot_time"]:
        return "NEUTRAL"
    match = (high["classification"], low["classification"])
    if match == ("HH", "HL"):
        return "BULLISH"
    if match == ("LH", "LL"):
        return "BEARISH"
    return "NEUTRAL"


def detect_structure(rows, step: int, rules: StructureRules, provenance: dict,
                     delivery: StructureDelivery, snapshot_scope: dict, consumer=None, *, local_witness=False, public_event=None):
    """Rows contain closed bars and actual UTC availability, already cutoff-filtered."""
    pivots = EventPage("pivots", delivery.limit if delivery.pivot_limit is None else delivery.pivot_limit,
                       delivery.pivot_cursor)
    breaks = EventPage("breaks", delivery.limit if delivery.break_limit is None else delivery.break_limit,
                       delivery.break_cursor)
    schedule = rules.market_schedule or MarketSchedule(kind="unknown")
    continuity = deque(maxlen=40)
    reasons = Counter()
    active = {}
    consumed, candidates = set(), {}
    candidate_stamps = {}
    window = deque(maxlen=rules.left+rules.right+1)
    recorded_window = deque(maxlen=window.maxlen)
    strict = provenance.get("timing_policy") == "STRICT_CAUSAL"
    local_witness = local_witness or strict
    authority = provenance.get("context_authority","CLIENT_SUPPLIED_UNVERIFIED")
    segment_recorded = True
    cutoff = snapshot_scope["cutoff"] if strict else 0
    state = "UNCONFIRMED"
    last = None
    warnings = set()
    fingerprint = sha256()
    version = rules.algorithm_version
    scope = json.dumps([version, provenance, rules.identity_parameters()], sort_keys=True, separators=(",", ":"))
    snapshot = sha256(json.dumps([scope, step, snapshot_scope], sort_keys=True, separators=(",", ":")).encode())
    last_close = None

    provenance_id = sha256(json.dumps(provenance, sort_keys=True).encode()).hexdigest()

    def event(kind, side, event_time, available, **fields):
        evidence = fingerprint.hexdigest()
        identity_evidence = fields.get("local_witness", evidence) if strict else evidence
        identity = sha256(json.dumps([scope, kind, side, event_time, identity_evidence],
                                     separators=(",", ":")).encode()).hexdigest()
        return {"id": identity, "type": kind, "side": side, "algorithm_version": version,
                "confirmation_time": None if fields.get("status") != "confirmed" else event_time+step,
                "available_at": available, "input_fingerprint": evidence, "provenance_id": provenance_id, **fields}

    def witness(*values):
        payload = json.dumps(values, sort_keys=True, separators=(",", ":")).encode()
        return sha256(payload).hexdigest()

    observation_index = -1
    current_breaks = None

    def publish_break(value):
        projected={key:item for key,item in value.items() if key!="local_witness"} if local_witness else value
        breaks.append(projected)
        if public_event is not None:
            public_event("breaks",projected)
        if current_breaks is not None:
            current_breaks.append(value)

    for bar, available in sorted(rows, key=lambda row: (row[1], row[0].time)):
        encoded = bar.model_dump_json().encode()
        availability_bytes = f":{available};".encode()
        snapshot.update(encoded)
        snapshot.update(availability_bytes)
        if last is not None and bar.time <= last:
            warnings.add("LATE_STRUCTURE_CANDLE_IGNORED")
            continue
        observation_index += 1
        current_breaks = [] if consumer is not None else None
        reset_reason = None
        reopening = None
        if last is not None and bar.time != last + step:
            reason = continuity_reason(last, bar.time, step, schedule) if strict or version == 2 else "LEGACY_GAP"
            if strict or version == 2:
                reasons[reason] += 1
                record = {"reason": reason, "previous_time": last, "time": bar.time,
                          "available_at": available, "state_before": state,
                          "state_preserved": reason == "SCHEDULED_CLOSURE"}
                if reason == "SCHEDULED_CLOSURE":
                    reopening = {"previous_close": last_close, "open": bar.open, "high": bar.high,
                                 "low": bar.low, "close": bar.close, "crossings": []}
                    record["opening"] = reopening
                continuity.append(record)
            if reason != "SCHEDULED_CLOSURE":
                reset_reason = reason
                warnings.add("STRUCTURE_RESET_AT_GAP")
                window.clear()
                recorded_window.clear()
                segment_recorded = True
                active.clear()
                candidates.clear()
                candidate_stamps.clear()
                consumed.clear()
                state = "UNCONFIRMED"
        last, last_close = bar.time, bar.close
        fingerprint.update(encoded)
        fingerprint.update(availability_bytes)
        window.append(bar)
        if strict:
            recorded_window.append(bar.available_at == available)
            segment_recorded &= bar.available_at == available

        eligible = {side: level for side, level in active.items()
                    if level["available_at"] < available and level["id"] not in consumed}
        high, low = eligible.get("HIGH"), eligible.get("LOW")
        ambiguous = high and low and high["price"] <= low["price"]
        if ambiguous:
            warnings.add("STRUCTURE_AMBIGUOUS_LEVELS")
        reset = bool(ambiguous)
        for side, level in ({} if ambiguous else eligible).items():
            direction = "BULLISH" if side == "HIGH" else "BEARISH"
            sign = 1 if side == "HIGH" else -1
            price = level["price"]
            tolerance = price * rules.break_bps / 10_000
            wick = bar.high if side == "HIGH" else bar.low
            crossed = sign * (bar.close-price) > tolerance
            wick_crossed = sign * (wick-price) > tolerance
            opposing = state in {"BULLISH", "BEARISH"} and state != direction
            protected = level["classification"] == ("HL" if side == "LOW" else "LH")
            fields = dict(direction=direction, broken_pivot_id=level["id"], break_time=bar.time,
                          level_price=price, level_time=level["confirmation_bar_time"],
                          level_available_at=level["available_at"], state_before=state,
                          invalidation="Reclaim before a qualifying close, or a candle continuity gap")
            if reopening is not None:
                opening_crossed = sign * (bar.open-price) > tolerance
                crossing = {"side": side, "pivot_id": level["id"], "level_price": price,
                            "open_crossed": opening_crossed, "wick_crossed": wick_crossed,
                            "close_crossed": crossed}
                opening_status = ("CLOSE_CONFIRMED" if crossed else "OPEN_RECLAIMED" if opening_crossed
                                  else "WICK_ONLY" if wick_crossed else "INSIDE")
                crossing["opening_status"] = opening_status
                reopening["crossings"].append(crossing)
                fields["opening_status"] = opening_status
            candidate = candidates.get(level["id"])
            if strict:
                barrier=level["available_at"]
                references=[level["id"]]
                if candidate and not crossed and not wick_crossed:
                    barrier=max(barrier,candidate_stamps[candidate])
                    references.append(candidate)
                if candidate and crossed and candidate_stamps[candidate]>=bar.time+step:
                    candidate=None
                timing = evidence(bar,step,available,barrier,cutoff,authority)
                decision = timing.interaction(close_only=crossed)
                if decision != "ELIGIBLE":
                    if crossed or wick_crossed:
                        rejected = timing.contract(reason=decision,close_only=crossed,references=references)
                        publish_break(event("TIMING_REJECTED",side,bar.time,available,status="provisional",
                                            break_price=bar.close if crossed else wick,state_after=state,
                                            candidate_id=None,timing=rejected,**fields))
                    continue
                reason = "UNKNOWN_COVERAGE" if schedule.kind=="unknown" else (
                    "UNVERIFIED_AVAILABILITY" if not level["timing_recorded"] or not segment_recorded else None)
                fields["timing"] = timing.contract(reason=reason,close_only=crossed,references=references)
            if crossed:
                kind = "CHOCH" if opposing and protected else "COUNTERTREND_BREAK" if opposing else "BOS"
                after = "NEUTRAL" if opposing else state
                if local_witness:
                    fields["local_witness"] = witness(
                        "structure-break", version, kind, side, level["local_witness"],
                        state, after, bar.model_dump(), available,
                        *(("STRICT_CAUSAL",2) if strict else ()),
                    )
                publish_break(event(kind, side, bar.time, available, status="confirmed", break_price=bar.close,
                                    state_after=after, candidate_id=candidate, **fields))
                consumed.add(level["id"])
                if opposing:
                    reset = True
            elif candidate and not wick_crossed:
                publish_break(event("WICK_INVALIDATED", side, bar.time, available, status="invalidated",
                                    break_price=bar.close, state_after=state, candidate_id=candidate, **fields))
                candidates[level["id"]] = None
            elif rules.wick_candidates and wick_crossed and level["id"] not in candidates:
                kind = "COUNTERTREND_CANDIDATE" if opposing else "WICK_CANDIDATE"
                wick_event = event(kind, side, bar.time, available, status="provisional", break_price=wick,
                                   state_after=state, candidate_id=None, **fields)
                publish_break(wick_event)
                candidates[level["id"]] = wick_event["id"]
                if strict:
                    candidate_stamps[wick_event["id"]]=available
        if reset:
            state = "NEUTRAL"
            active.clear()
            consumed.clear()
            candidates.clear()
            candidate_stamps.clear()
            window.clear()
            window.append(bar)
            if strict:
                recorded_window.clear()
                recorded_window.append(bar.available_at == available)
        if len(window) != window.maxlen:
            if consumer is not None:
                consumer(bar, available, observation_index, reset_reason, (), current_breaks,
                         fingerprint.hexdigest(), provenance_id)
            continue

        pivot = window[rules.left]
        batch = []
        for side in ("HIGH", "LOW"):
            field = "close" if rules.price_source == "close" else side.lower()
            values = [getattr(item, field) for item in window]
            if not pivot_matches(values, rules.left, side, rules.tolerance_bps, rules.ties):
                continue
            previous = active.get(side)
            price = values[rules.left]
            found = event("PIVOT", side, bar.time, available, status="confirmed", price=price,
                          pivot_time=pivot.time, confirmation_bar_time=bar.time,
                          previous_pivot_id=previous["id"] if previous else None,
                          classification=classify(side, price, previous, rules.tolerance_bps))
            if strict:
                found["timing_recorded"]=segment_recorded and all(recorded_window)
                reason="UNKNOWN_COVERAGE" if schedule.kind=="unknown" else (
                    "UNVERIFIED_AVAILABILITY" if not found["timing_recorded"] else None)
                found["timing"]=evidence(bar,step,available,0,cutoff,authority).contract(reason=reason)
            if local_witness:
                found["local_witness"] = witness(
                    "structure-pivot", version, side, pivot.time, available,
                    [item.model_dump() for item in window],
                    *(("STRICT_CAUSAL",2) if strict else ()),
                )
            if strict:
                found["id"]=witness("strict-pivot-id",scope,found["local_witness"])
            batch.append(found)
        for found in batch:
            previous = active.get(found["side"])
            if previous:
                consumed.discard(previous["id"])
                candidates.pop(previous["id"], None)
            active[found["side"]] = found
        if batch and state not in {"BULLISH", "BEARISH"}:
            initial = initial_direction(active)
            if len(batch) == 2 and initial in {"BULLISH", "BEARISH"}:
                initial = "NEUTRAL"
            if state != "NEUTRAL" or initial != "UNCONFIRMED":
                state = initial
        for found in batch:
            projected={**found,"state_after":state}
            projected.pop("local_witness",None)
            pivots.append(projected)
            if public_event is not None:
                public_event("pivots",projected)
        if consumer is not None:
            consumer(bar, available, observation_index, reset_reason,
                     [(found, observation_index-rules.right) for found in batch], current_breaks,
                     fingerprint.hexdigest(), provenance_id)
    digest = snapshot.hexdigest()
    metadata = {"snapshot": digest, "pivots": pivots.finish(digest), "breaks": breaks.finish(digest)}
    result = {"pivots": list(pivots.events), "breaks": list(breaks.events), "structure_state": state,
              "structure_warnings": sorted(warnings), "provenance_id": provenance_id,
              "structure_input_fingerprint": fingerprint.hexdigest(), "event_page": metadata}
    if version == 2:
        result["continuity"] = {"policy_version": POLICY_VERSION, "schedule": schedule.model_dump(),
                                "schedule_authority": ("UNKNOWN" if schedule.kind == "unknown" else
                                                       "CONFIGURED_ASSUMPTION"),
                                "counts": dict(reasons), "recent": list(continuity)}
    return result
