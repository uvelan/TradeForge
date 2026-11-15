import json
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from .indicators import Bar, REGISTRY, calculate
from .market import FRAME_MIN
from .smt import detect_smt
from .db import rows
from .smt_history import frozen_confirmations, record_smt, scope_key

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


class SmtCalculation(BaseModel):
    period: int = Field(default=3, ge=2, le=20)
    alignment_bars: int = Field(default=2, ge=0, le=10)
    tolerance_bps: float = Field(default=0.5, ge=0, le=100, allow_inf_nan=False)
    confirmation_bars: int = Field(default=1, ge=0, le=20)
    timeframe: str
    primary: list[Bar] = Field(max_length=2000)
    comparison: list[Bar] = Field(max_length=2000)
    primary_feed: SmtFeed | None = None
    comparison_feed: SmtFeed | None = None
    replay: bool = False
    alert_after: int | None = Field(default=None, ge=0)


@router.post("/smt")
def smt(request: SmtCalculation):
    if request.timeframe not in FRAME_MIN:
        raise HTTPException(422, "Unsupported timeframe")
    for series in (request.primary, request.comparison):
        if any(left.time >= right.time for left, right in zip(series, series[1:])):
            raise HTTPException(422, "Candle times must be strictly increasing")
    step = FRAME_MIN[request.timeframe] * 60
    rules = request.model_dump(include={"period", "alignment_bars", "tolerance_bps", "confirmation_bars"})
    alerts = []
    first, second = request.primary_feed, request.comparison_feed
    if first and second and (first.source != second.source or first.feed_id != second.feed_id
                             or first.price_basis != second.price_basis
                             or "UNSPECIFIED" in first.price_basis.upper()
                             or first.symbol.casefold() == second.symbol.casefold()):
        raise HTTPException(422, "SMT requires distinct instruments with matching feed and price basis")
    events = detect_smt(request.primary, request.comparison, **rules, step=step)
    if first and second and first.source != "DEMO":
        provenance = {"timeframe": request.timeframe, "primary": first.model_dump(), "comparison": second.model_dump()}
        scope = scope_key(provenance, rules)
        if events and not request.replay:
            alerts = record_smt(events, scope, provenance, rules, request.alert_after if first.source == "MT5" else None)
        events = frozen_confirmations(events, scope, request.primary, request.comparison, step)
    return {"version": 2, "rules": rules, "events": events, "alerts": alerts}


@router.get("/smt/history")
def smt_history(limit: int = Query(100, ge=1, le=500)):
    return [json.loads(row["payload"]) for row in rows(
        "SELECT payload FROM smt_event_history ORDER BY event_time DESC, event_key, status LIMIT ?", (limit,)
    )]


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
            "source": request.source, "timeframe": request.timeframe}
