import asyncio
import json
import logging
from hashlib import sha256
from time import time_ns
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .indicators import Bar, REGISTRY, calculate
from .market import FRAME_MIN, mt5_rates
from .smt import confirmation_timing, detect_smt
from .db import one, rows, transaction
from .smt_history import event_identity, record_smt, scope_key
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


class LiveSmtWatch(SmtRules):
    primary_symbol: str = Field(min_length=1, max_length=64)
    comparison_symbol: str = Field(min_length=1, max_length=64)
    enabled: bool = True


class LiveSmtCalculation(LiveSmtWatch):
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


def validate_watch(request: LiveSmtWatch):
    if request.timeframe not in FRAME_MIN or request.primary_symbol.casefold() == request.comparison_symbol.casefold():
        raise HTTPException(422, "Use a supported timeframe and two different MT5 instruments")


@router.post("/smt/live")
def live_smt(request: LiveSmtCalculation):
    return capture_live_smt(request)


def capture_live_smt(request: LiveSmtCalculation, monitor_token: int | None = None):
    validate_watch(request)
    step = FRAME_MIN[request.timeframe] * 60
    now = int(datetime.now(UTC).timestamp())
    cutoff = min(now, request.available_through)

    def capture(symbol):
        metadata = {}
        values = mt5_rates(symbol, request.timeframe, 600, include_latest=True, provenance=metadata)
        candles = [Bar.model_validate(bar) for bar in values if bar["time"] + step <= cutoff]
        if not metadata.get("feed_id") or metadata.get("price_basis") not in {"BID", "LAST_TRADE"}:
            raise ValueError("MT5 feed or basis unavailable")
        if not candles or any(a.time >= b.time for a, b in zip(candles, candles[1:])):
            raise ValueError("MT5 returned no closed candles or unordered candles")
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
    scope = scope_key(provenance, rules)
    alerts, corrected = record_smt(
        events, scope, provenance, rules, alert_after=alert_after, capture_step=step,
        capture={"primary": [bar.model_dump() for bar in primary],
                 "comparison": [bar.model_dump() for bar in comparison]}, observed_at=now,
        monitor_token=monitor_token,
    )
    events = [event for event in events if event["id"] not in corrected]
    for event in events:
        event["event_key"] = event_identity(scope,event)
    for event in events + alerts:
        event.pop("capture", None)
    return {**response_for(events, alerts, rules, step, provenance["authority"]),
            "scope": scope, "corrected_ids": sorted(corrected)}


MONITOR_INTERVAL_SECONDS = 5


def monitor_configuration() -> dict | None:
    row = one("SELECT configuration, enabled_at FROM smt_monitor WHERE id=1")
    return {**json.loads(row["configuration"]), "enabled_at": row["enabled_at"]} if row else None


@router.get("/smt/monitor")
def monitor_status(request: Request):
    configuration = monitor_configuration()
    state = request.app.state.smt_monitor
    installed = {**configuration, "enabled_at": configuration["enabled_at"] // 1_000_000_000} if configuration else None
    if configuration is None or state.get("configuration") != configuration:
        return {"configuration": installed}
    result = state["result"]
    scope = result.get("scope")
    alert = next(iter(active_alerts(1, scope)), None) if scope else None
    correction = one("SELECT payload FROM smt_source_corrections WHERE scope_key=? ORDER BY event_time DESC, event_key LIMIT 1",
                     (scope,)) if scope else None
    return {"configuration": installed, **result, "latest_alert": alert,
            "latest_correction": json.loads(correction["payload"]) if correction else None}


@router.post("/smt/monitor")
def configure_monitor(watch: LiveSmtWatch):
    validate_watch(watch)
    with transaction() as db:
        if watch.enabled:
            db.execute(
                """INSERT INTO smt_monitor VALUES(1, ?, ?) ON CONFLICT(id) DO UPDATE
                   SET configuration=excluded.configuration, enabled_at=excluded.enabled_at
                   WHERE smt_monitor.configuration != excluded.configuration""",
                (watch.model_dump_json(), time_ns()),
            )
        else:
            db.execute("DELETE FROM smt_monitor WHERE id=1")
    return {"enabled": watch.enabled}


def active_alerts(limit: int, scope: str | None = None):
    query = """SELECT payload FROM smt_alerts AS alerts WHERE NOT EXISTS (
                   SELECT 1 FROM smt_source_corrections AS correction WHERE correction.event_key=alerts.event_key
               ) AND NOT EXISTS (
                   SELECT 1 FROM smt_event_history AS history
                   WHERE history.event_key=alerts.event_key AND history.status='SMT_INVALIDATED'
               )"""
    args = (limit,)
    if scope is not None:
        query += " AND scope_key=?"
        args = (scope, limit)
    return [json.loads(row["payload"]) for row in rows(query + " ORDER BY event_time DESC, event_key LIMIT ?", args)]


@router.get("/smt/alerts")
def smt_alerts(limit: int = Query(25, ge=1, le=100)):
    return active_alerts(limit)


def poll_monitor(state: dict):
    configuration = monitor_configuration()
    if configuration is None:
        return
    now = int(datetime.now(UTC).timestamp())
    try:
        token = configuration["enabled_at"]
        capture = LiveSmtCalculation(**{**configuration, "available_through": now,
                                       "alert_after": token // 1_000_000_000})
        response = capture_live_smt(capture, monitor_token=token)
        result = {"last_poll_at": now, "error": None, "scope": response["scope"]}
    except HTTPException as error:
        result = {"last_poll_at": now, "error": str(error.detail)}
    state.update(configuration=configuration, result=result)


async def run_smt_monitor(stop: asyncio.Event, state: dict):
    while not stop.is_set():
        try:
            await asyncio.to_thread(poll_monitor, state)
        except Exception:
            logging.getLogger(__name__).exception("SMT monitor poll failed")
        try:
            await asyncio.wait_for(stop.wait(), MONITOR_INTERVAL_SECONDS)
        except TimeoutError:
            continue


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
        for event in events:
            event["event_key"] = event_identity(scope,event)
    return response_for(events, alerts, rules, step, "VERIFIED_SAVED_DATASET" if provenance else "CLIENT_SUPPLIED_UNVERIFIED")


@router.get("/smt/history")
def smt_history(limit: int = Query(100, ge=1, le=500)):
    events = [json.loads(row["payload"]) for row in rows(
        """SELECT payload FROM (
            SELECT payload, event_time, event_key, status FROM smt_event_history
            UNION ALL
            SELECT payload, event_time, event_key, 'SMT_SOURCE_CORRECTED' FROM smt_source_corrections
        ) ORDER BY event_time DESC, event_key, status LIMIT ?""", (limit,)
    )]
    for event in events:
        event["authority"] = event.get("provenance", {}).get("authority", "CLIENT_SUPPLIED_UNVERIFIED")
        event["timing"] = confirmation_timing(event, event["rules"])
        event["available_at"] = event["confirmed_at"]
    return events


@router.get("/signals")
def signals(q: str = Query("", max_length=128), direction: Literal["bullish", "bearish"] | None = None,
            status: Literal["SMT_CANDIDATE", "SMT_CONFIRMED", "SMT_INVALIDATED", "SMT_SOURCE_CORRECTED"] | None = None,
            timeframe: str | None = Query(None, max_length=10), source: str | None = Query(None, max_length=30),
            instrument: str | None = Query(None, max_length=64), start: int | None = Query(None, ge=0),
            end: int | None = Query(None, ge=0), cursor: str | None = Query(None, max_length=90),
            limit: int = Query(25, ge=1, le=100)):
    if start is not None and end is not None and start >= end:
        raise HTTPException(422, "Choose an end time after the start")
    conditions, parameters = [], []
    for sql, value in (
        ("status=?", status), ("json_extract(payload,'$.direction')=?", direction),
        ("json_extract(payload,'$.provenance.timeframe')=?", timeframe),
        ("json_extract(payload,'$.provenance.primary.source')=?", source),
        ("event_time>=?", start), ("event_time<?", end),
    ):
        if value is not None:
            conditions.append(sql)
            parameters.append(value)
    if instrument:
        conditions.append("""lower(?) IN (lower(json_extract(payload,'$.provenance.primary.symbol')),
                                          lower(json_extract(payload,'$.provenance.comparison.symbol')))""")
        parameters.append(instrument)
    if q:
        conditions.append("""(instr(lower(event_key), lower(?)) OR instr(lower(json_extract(payload,'$.id')), lower(?))
            OR instr(lower(json_extract(payload,'$.provenance')), lower(?))
            OR EXISTS (SELECT 1 FROM plan_versions AS plan
                       WHERE json_extract(plan.payload,'$.signal_reference.event_key')=latest.event_key
                         AND instr(lower(plan.trade_id), lower(?))))""")
        parameters.extend([q] * 4)
    if cursor is not None:
        timestamp, _, key = cursor.partition(":")
        if not timestamp.isascii() or not timestamp.isdecimal() or int(timestamp) >= 2**63 or len(key) != 64 or any(char not in "0123456789abcdef" for char in key):
            raise HTTPException(422, "Invalid signal cursor")
        conditions.append("(event_time < ? OR (event_time = ? AND event_key > ?))")
        parameters.extend([int(timestamp), int(timestamp), key])
    where = " AND " + " AND ".join(conditions) if conditions else ""
    page = rows("""
        WITH history AS (
            SELECT event_key,event_time,status,payload,
                CASE status WHEN 'SMT_INVALIDATED' THEN 3 WHEN 'SMT_CONFIRMED' THEN 2 ELSE 1 END AS priority
            FROM smt_event_history
            UNION ALL
            SELECT event_key,event_time,'SMT_SOURCE_CORRECTED',payload,4 FROM smt_source_corrections
        ), latest AS (
            SELECT *, row_number() OVER(PARTITION BY event_key ORDER BY priority DESC) AS position FROM history
        )
        SELECT event_key,event_time,json_remove(payload,'$.capture') AS payload FROM latest WHERE position=1
    """ + where + " ORDER BY event_time DESC,event_key LIMIT ?", (*parameters, limit + 1))
    results = []
    for row in page[:limit]:
        event = json.loads(row["payload"])
        event.pop("capture", None)
        results.append(event)
    last = page[limit-1] if len(page) > limit else None
    return {"items": results, "next_cursor": f"{last['event_time']}:{last['event_key']}" if last else None}


@router.get("/signals/{event_key}")
def signal_revision(event_key: str, status: Literal["SMT_CANDIDATE", "SMT_CONFIRMED",
                    "SMT_INVALIDATED", "SMT_SOURCE_CORRECTED"]):
    if status == "SMT_SOURCE_CORRECTED":
        row = one("SELECT payload FROM smt_source_corrections WHERE event_key=?", (event_key,))
    else:
        row = one("SELECT payload FROM smt_event_history WHERE event_key=? AND status=?", (event_key, status))
    if not row:
        raise HTTPException(404, "Signal revision unavailable")
    event = json.loads(row["payload"])
    event.pop("capture", None)
    return event


@router.get("/signals/{event_key}/plans")
def signal_plans(event_key: str):
    return rows("""SELECT version.trade_id,version.version,version.created_at,
                          json_extract(version.payload,'$.signal_reference.status') AS evidence_status,
                          json_extract(version.payload,'$.strategy') AS strategy, json_extract(version.payload,'$.symbol') AS symbol
                   FROM plan_versions AS version
                   WHERE json_extract(version.payload,'$.signal_reference.event_key')=?
                   ORDER BY version.created_at DESC,version.id LIMIT 100""",(event_key,))


@router.get("/signals/{event_key}/candles")
def signal_candles(event_key: str, status: Literal["SMT_CANDIDATE", "SMT_CONFIRMED",
                   "SMT_INVALIDATED", "SMT_SOURCE_CORRECTED"]):
    if status == "SMT_SOURCE_CORRECTED":
        correction = one("SELECT payload FROM smt_source_corrections WHERE event_key=?", (event_key,))
        if not correction:
            raise HTTPException(404, "Correction unavailable")
        status = json.loads(correction["payload"])["source_correction"]["previous_status"]
    row = one("SELECT payload FROM smt_event_history WHERE event_key=? AND status=?", (event_key, status))
    if not row:
        raise HTTPException(404, "Signal evidence unavailable")
    event = json.loads(row["payload"])
    provenance = event["provenance"]
    timeframe = provenance["timeframe"]
    step = FRAME_MIN[timeframe] * 60
    if "capture" in event:
        captures = event["capture"]
        for side in ("primary", "comparison"):
            evidence = captures[side]
            if sha256(json.dumps(evidence["bars"], sort_keys=True).encode()).hexdigest() != evidence["sha256"]:
                raise HTTPException(409, "Recorded evidence checksum mismatch")
        return {side: captures[side]["bars"] for side in ("primary", "comparison")}
    result = {}
    try:
        for side in ("primary", "comparison"):
            feed = provenance[side]
            if feed.get("archive_range"):
                archive = saved_range(feed["source"].removesuffix("_SAVED"), feed["symbol"], timeframe,
                                      *feed["archive_range"], step, 2000)
                if archive["partitions"] != feed["partitions"] or archive["coverage"]["truncated"]:
                    raise ValueError("Original partition selection unavailable")
                values = archive["candles"]
            else:
                dataset = one("SELECT * FROM datasets WHERE id=?", (feed["dataset_id"],))
                if not dataset or dataset["sha256"] != feed["sha256"] or dataset["rows_count"] > 2000:
                    raise ValueError("Original partition unavailable")
                values = read_partition(dataset)
            result[side] = [Bar.model_validate(bar).model_dump() for bar in values
                            if bar["time"] + step <= event["transitions"][-1]["time"]]
    except (OSError, RuntimeError, ValueError, KeyError) as error:
        raise HTTPException(409, f"Recorded evidence unavailable: {error}") from error
    return result


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
