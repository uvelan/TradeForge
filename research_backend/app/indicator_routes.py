import json
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from .indicators import Bar, REGISTRY, calculate
from .market import FRAME_MIN, mt5_rates
from .smt import confirmation_timing, detect_smt
from .db import one, rows
from .smt_history import record_smt, scope_key
from .storage import read_partition
from .saved_history import saved_range

router = APIRouter(prefix="/api/indicators")


class Calculation(BaseModel):
    indicator: str
    period: int = Field(ge=2, le=200)
    source: str = Field(max_length=40)
    timeframe: str = Field(max_length=10)
    candles: list[Bar] = Field(max_length=2000)


class SmtFeed(BaseModel):
    source: Literal["MT5", "MT5_SAVED", "DUKASCOPY_SAVED", "DEMO", "BINANCE_SPOT"]
    symbol: str = Field(min_length=1, max_length=64)
    feed_id: str = Field(min_length=1, max_length=200)
    price_basis: str = Field(min_length=1, max_length=100)
    dataset_id: str | None = Field(default=None, max_length=100)
    archive_range: tuple[int, int] | None = None
    partitions: list[dict[str, str]] = Field(default_factory=list, max_length=50)


class SmtRules(BaseModel):
    period: int = Field(default=3, ge=2, le=20)
    alignment_bars: int = Field(default=2, ge=0, le=10)
    tolerance_bps: float = Field(default=0.5, ge=0, le=100, allow_inf_nan=False)
    confirmation_bars: int = Field(default=1, ge=0, le=20)
    timeframe: str


class SmtCalculation(SmtRules):
    primary: list[Bar] = Field(max_length=2000)
    comparison: list[Bar] = Field(max_length=2000)
    primary_feed: SmtFeed | None = None
    comparison_feed: SmtFeed | None = None
    replay: bool = False
    alert_after: int | None = Field(default=None, ge=0)


class LiveSmtCalculation(SmtRules):
    primary_symbol: str = Field(min_length=1, max_length=64)
    comparison_symbol: str = Field(min_length=1, max_length=64)
    available_through: int = Field(ge=0)
    alert_after: int | None = Field(default=None, ge=0)


def response_for(events: list[dict], alerts: list[dict], rules: dict, step: int, authority: str):
    for event in events + alerts:
        event["timing"] = confirmation_timing(event, rules)
        event["available_at"] = event["confirmed_at"]
        event["confirmation_bar_time"] = event["confirmed_at"] - step if event["confirmed_at"] is not None else None
    return {"version": 2, "rules": rules, "events": events, "alerts": alerts, "authority": authority}


def rule_parameters(request: SmtRules) -> dict:
    return request.model_dump(include={"period", "alignment_bars", "tolerance_bps", "confirmation_bars"})


@router.post("/smt/live")
def live_smt(request: LiveSmtCalculation):
    if request.timeframe not in FRAME_MIN or request.primary_symbol.casefold() == request.comparison_symbol.casefold():
        raise HTTPException(422, "Use a supported timeframe and two different MT5 instruments")
    step = FRAME_MIN[request.timeframe] * 60
    now = int(datetime.now(UTC).timestamp())
    cutoff = min(now, request.available_through)

    def capture(symbol):
        metadata = {}
        values = mt5_rates(symbol, request.timeframe, 600, provenance=metadata)
        candles = [Bar.model_validate(bar) for bar in values if bar["time"] + step <= cutoff]
        if not metadata.get("feed_id") or metadata.get("price_basis") not in {"BID", "LAST_TRADE"}:
            raise ValueError("MT5 feed or basis unavailable")
        if any(a.time >= b.time for a, b in zip(candles, candles[1:])):
            raise ValueError("MT5 returned unordered candles")
        return candles, {"symbol": symbol, "source": "MT5", **metadata}

    try:
        primary, left = capture(request.primary_symbol)
        comparison, right = capture(request.comparison_symbol)
        if (left["feed_id"], left["price_basis"]) != (right["feed_id"], right["price_basis"]):
            raise ValueError("MT5 feeds or price bases do not match")
    except (RuntimeError, ValueError) as error:
        raise HTTPException(503, str(error)) from error
    provenance = {"timeframe": request.timeframe, "primary": left, "comparison": right,
                  "authority": "VERIFIED_MT5_CAPTURE"}
    rules = rule_parameters(request)
    events = detect_smt(primary, comparison, **rules, step=step)
    for event in events:
        capture_evidence = {}
        for side, bars in (("primary", primary), ("comparison", comparison)):
            start = event[side]["previous"]["time"] - rules["period"] * step
            evidence = [bar.model_dump() for bar in bars if bar.time >= start]
            capture_evidence[side] = evidence
        event["capture"] = capture_evidence
    alert_after = max(request.alert_after, now - step * 2) if request.alert_after is not None else None
    alerts = record_smt(events, scope_key(provenance, rules), provenance, rules,
                        alert_after=alert_after, capture_step=step) if events else []
    for event in events + alerts:
        event.pop("capture", None)
    return response_for(events, alerts, rules, step, provenance["authority"])


def resolve_saved_feed(feed: SmtFeed, timeframe: str, supplied: list[Bar], step: int) -> tuple[list[Bar], dict]:
    if feed.archive_range is not None:
        try:
            archive = saved_range(feed.source.removesuffix("_SAVED"), feed.symbol, timeframe, *feed.archive_range, step, 2000)
            if (archive["feed_id"] != feed.feed_id or archive["price_basis"] != feed.price_basis
                    or archive["partitions"] != feed.partitions):
                raise ValueError("Archive partition lineage changed")
            canonical = [Bar.model_validate(bar) for bar in archive["candles"]]
            window = [bar for bar in canonical if supplied[0].time <= bar.time <= supplied[-1].time]
            if window != supplied:
                raise ValueError("Chart candles differ from archive")
        except (ValueError, OSError, RuntimeError) as error:
            raise HTTPException(409, f"SOURCE_MISMATCH: {error}") from error
        return window, {**feed.model_dump(), "coverage": archive["coverage"]}
    dataset = one("SELECT * FROM datasets WHERE id = ?", (feed.dataset_id,))
    if (not dataset or dataset["kind"] != "candles" or dataset["timeframe"] != timeframe
            or dataset["provider"] + "_SAVED" != feed.source or dataset["symbol"] != feed.symbol
            or dataset["feed_id"] != feed.feed_id or dataset["price_basis"] != feed.price_basis):
        raise HTTPException(409, "SOURCE_MISMATCH: saved dataset metadata differs")
    try:
        canonical = [Bar.model_validate(bar) for bar in read_partition(dataset, max_rows=2000)]
    except (ValueError, OSError, RuntimeError) as error:
        raise HTTPException(409, "SOURCE_MISMATCH: saved dataset failed verification") from error
    clock = int(datetime.now(UTC).timestamp())
    if (not canonical or any(bar.time % step or bar.time + step > clock for bar in canonical)
            or any(a.time >= b.time for a, b in zip(canonical, canonical[1:]))):
        raise HTTPException(409, "SOURCE_MISMATCH: saved candles must be aligned, closed and ordered")
    if not supplied:
        return [], {}
    window = [bar for bar in canonical if supplied[0].time <= bar.time <= supplied[-1].time]
    if window != supplied:
        raise HTTPException(409, "SOURCE_MISMATCH: chart candles differ from the saved dataset")
    coverage = "GAPPED" if any(b.time - a.time != step for a, b in zip(canonical, canonical[1:])) else "CONTIGUOUS"
    return window, {**feed.model_dump(), "sha256": dataset["sha256"], "coverage": coverage}


@router.post("/smt")
def smt(request: SmtCalculation):
    if request.timeframe not in FRAME_MIN:
        raise HTTPException(422, "Unsupported timeframe")
    for series in (request.primary, request.comparison):
        if any(left.time >= right.time for left, right in zip(series, series[1:])):
            raise HTTPException(422, "Candle times must be strictly increasing")
    step = FRAME_MIN[request.timeframe] * 60
    rules = rule_parameters(request)
    alerts = []
    first, second = request.primary_feed, request.comparison_feed
    if first and second and (first.source != second.source or first.feed_id != second.feed_id
                             or first.price_basis != second.price_basis
                             or "UNSPECIFIED" in first.price_basis.upper()
                             or first.symbol.casefold() == second.symbol.casefold()):
        raise HTTPException(422, "SMT requires distinct instruments with matching feed and price basis")
    primary, comparison = request.primary, request.comparison
    provenance = None
    if first and second and first.source in {"MT5_SAVED", "DUKASCOPY_SAVED"} and primary and comparison:
        primary, left = resolve_saved_feed(first, request.timeframe, primary, step)
        comparison, right = resolve_saved_feed(second, request.timeframe, comparison, step)
        provenance = {"timeframe": request.timeframe, "primary": left, "comparison": right,
                      "authority": "VERIFIED_SAVED_DATASET"}
    events = detect_smt(primary, comparison, **rules, step=step)
    if provenance and events and not request.replay:
        scope = scope_key(provenance, rules)
        record_smt(events, scope, provenance, rules)
    return response_for(events, alerts, rules, step, "VERIFIED_SAVED_DATASET" if provenance else "CLIENT_SUPPLIED_UNVERIFIED")


@router.get("/smt/history")
def smt_history(limit: int = Query(100, ge=1, le=500)):
    events = [json.loads(row["payload"]) for row in rows(
        "SELECT payload FROM smt_event_history ORDER BY event_time DESC, event_key, status LIMIT ?", (limit,)
    )]
    for event in events:
        event["authority"] = event.get("provenance", {}).get("authority", "CLIENT_SUPPLIED_UNVERIFIED")
        event["timing"] = confirmation_timing(event, event["rules"])
        event["available_at"] = event["confirmed_at"]
    return events


@router.get("")
def definitions():
    return [{"id": key, "label": definition.label, "render": definition.render,
             "period": definition.period, "version": 1}
            for key, definition in REGISTRY.items()]


@router.post("/calculate")
def indicator(request: Calculation):
    if request.indicator not in REGISTRY:
        raise HTTPException(422, "Unknown indicator")
    if any(left.time >= right.time for left, right in zip(request.candles, request.candles[1:])):
        raise HTTPException(422, "Candle times must be strictly increasing")
    return {**calculate(request.candles, request.indicator, request.period),
            "source": request.source, "timeframe": request.timeframe, "authority": "CLIENT_SUPPLIED_UNVERIFIED"}
