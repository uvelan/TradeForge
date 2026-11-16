"""Versioned confirmed pivots and price structure over a causal candle stream."""
from collections import deque
from hashlib import sha256
import json
from typing import Literal

from pydantic import BaseModel, Field, FiniteFloat, model_validator

from .pivots import pivot_matches

VERSION = 1


class StructureRules(BaseModel):
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
        return self


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


def detect_structure(rows, step: int, rules: StructureRules, provenance: dict):
    """Rows contain closed bars and actual UTC availability, already cutoff-filtered."""
    pivots, breaks = [], []
    active = {}
    consumed, candidates = set(), {}
    window = deque(maxlen=rules.left+rules.right+1)
    state = "UNCONFIRMED"
    last = None
    warnings = set()
    fingerprint = sha256()
    scope = json.dumps([VERSION, provenance, rules.model_dump()], sort_keys=True, separators=(",", ":"))

    provenance_id = sha256(json.dumps(provenance, sort_keys=True).encode()).hexdigest()

    def event(kind, side, event_time, available, **fields):
        evidence = fingerprint.hexdigest()
        identity = sha256(json.dumps([scope, kind, side, event_time, evidence],
                                     separators=(",", ":")).encode()).hexdigest()
        return {"id": identity, "type": kind, "side": side, "algorithm_version": VERSION,
                "confirmation_time": None if fields.get("status") != "confirmed" else event_time+step,
                "available_at": available, "input_fingerprint": evidence, "provenance_id": provenance_id, **fields}

    for bar, available in sorted(rows, key=lambda row: (row[1], row[0].time)):
        if last is not None and bar.time <= last:
            warnings.add("LATE_STRUCTURE_CANDLE_IGNORED")
            continue
        if last is not None and bar.time != last + step:
            warnings.add("STRUCTURE_RESET_AT_GAP")
            window.clear()
            active.clear()
            candidates.clear()
            consumed.clear()
            state = "UNCONFIRMED"
        last = bar.time
        fingerprint.update(bar.model_dump_json().encode())
        fingerprint.update(f":{available};".encode())
        window.append(bar)

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
            candidate = candidates.get(level["id"])
            if crossed:
                kind = "CHOCH" if opposing and protected else "COUNTERTREND_BREAK" if opposing else "BOS"
                after = "NEUTRAL" if opposing else state
                breaks.append(event(kind, side, bar.time, available, status="confirmed", break_price=bar.close,
                                    state_after=after, candidate_id=candidate, **fields))
                consumed.add(level["id"])
                if opposing:
                    reset = True
            elif candidate and not wick_crossed:
                breaks.append(event("WICK_INVALIDATED", side, bar.time, available, status="invalidated",
                                    break_price=bar.close, state_after=state, candidate_id=candidate, **fields))
                candidates[level["id"]] = None
            elif rules.wick_candidates and wick_crossed and level["id"] not in candidates:
                kind = "COUNTERTREND_CANDIDATE" if opposing else "WICK_CANDIDATE"
                wick_event = event(kind, side, bar.time, available, status="provisional", break_price=wick,
                                   state_after=state, candidate_id=None, **fields)
                breaks.append(wick_event)
                candidates[level["id"]] = wick_event["id"]
        if reset:
            state = "NEUTRAL"
            active.clear()
            consumed.clear()
            candidates.clear()
            window.clear()
            window.append(bar)
        if len(window) != window.maxlen:
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
            pivots.append({**found, "state_after": state})
    return {"pivots": pivots, "breaks": breaks, "structure_state": state,
            "structure_warnings": sorted(warnings), "provenance_id": provenance_id,
            "structure_input_fingerprint": fingerprint.hexdigest()}
