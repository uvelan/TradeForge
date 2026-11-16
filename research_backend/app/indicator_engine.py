"""Versioned, causal calculations over a single identified candle series."""

from datetime import UTC, date, datetime, time as wall_time, timedelta
from hashlib import sha256
from time import time
from typing import Annotated, Literal, TypedDict
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, StringConstraints, model_validator

from .indicators import Bar, REGISTRY, calculate
from .market import FRAME_MIN

SESSIONS = {
    "session_asia": ("Asia session range", "Asia/Tokyo", "09:00", "18:00"),
    "session_london": ("London session range", "Europe/London", "08:00", "17:00"),
    "session_new_york": ("New York session range", "America/New_York", "08:00", "17:00"),
}
PERIODS = {"previous_day": "Previous UTC day", "previous_week": "Previous UTC week"}
Clock = Annotated[str, StringConstraints(pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")]


class Window(BaseModel):
    timezone: str = Field(max_length=64)
    start: Clock
    end: Clock

    @model_validator(mode="after")
    def valid_window(self):
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ValueError("Unknown IANA timezone") from error
        if self.start == self.end:
            raise ValueError("Session start and end must differ")
        return self


class IndicatorSpec(BaseModel):
    id: str = Field(max_length=40)
    period: int | None = Field(default=None, ge=2, le=200)
    session: Window | None = None


class EvidenceBar(Bar):
    available_at: int | None = Field(default=None, ge=0)
    closed: bool = True


class Partition(BaseModel):
    id: str = Field(max_length=200)
    sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")


class Evaluation(BaseModel):
    source: Literal["DEMO", "MT5", "MT5_SAVED", "DUKASCOPY_SAVED", "BINANCE_SPOT"]
    symbol: str = Field(min_length=1, max_length=64)
    timeframe: str
    feed_id: str | None = Field(default=None, max_length=200)
    dataset_id: str | None = Field(default=None, max_length=200)
    price_basis: str | None = Field(default=None, max_length=40)
    partitions: list[Partition] = Field(default_factory=list, max_length=500)
    cutoff: int = Field(ge=0, le=32_503_680_000)
    indicators: list[IndicatorSpec] = Field(min_length=1, max_length=8)
    candles: list[EvidenceBar] = Field(max_length=2000)


class IndicatorDefinition(TypedDict):
    id: str
    label: str
    version: int
    render: str
    period: int | None
    warmup_bars: int | None
    parameters: dict
    price_source: str
    required_fields: list[str]
    unit: str
    timeframes: list[str]
    instruments: str


def catalog() -> list[IndicatorDefinition]:
    definitions: list[IndicatorDefinition] = []
    for key, item in REGISTRY.items():
        fields = ["high", "low", "close"] if key in {"atr", "natr"} else (
            ["high", "low"] if key in {"swings", "donchian"} else ["close"]
        )
        price_source = "wick_and_close" if "close" in fields and len(fields) > 1 else (
            "close" if fields == ["close"] else "wick"
        )
        unit = "percent" if key in {"rsi", "roc", "natr"} else "price"
        definitions.append({
            "id": key, "label": item.label, "version": 1, "render": item.render,
            "period": item.period, "warmup_bars": item.warmup(item.period),
            "parameters": {"period": {"min": 2, "max": 200}},
            "price_source": price_source,
            "required_fields": fields,
            "unit": unit,
            "timeframes": list(FRAME_MIN), "instruments": "any_valid_ohlc",
        })
    for key, label in {**PERIODS, **{key: item[0] for key, item in SESSIONS.items()}}.items():
        session = SESSIONS.get(key)
        definitions.append({
            "id": key, "label": label, "version": 1, "render": "overlay",
            "period": None, "warmup_bars": None, "price_source": "wick",
            "required_fields": ["high", "low"], "unit": "price",
            "timeframes": [name for name, minutes in FRAME_MIN.items() if not session or minutes < 1440],
            "instruments": "any_valid_ohlc",
            "parameters": {"session": dict(zip(("timezone", "start", "end"), session[1:]))} if session else {},
        })
    return definitions


def boundary(day: date, clock: str, zone: ZoneInfo) -> int | None:
    local = datetime.combine(day, wall_time.fromisoformat(clock))
    options = set()
    for fold in (0, 1):
        stamp = int(local.replace(tzinfo=zone, fold=fold).timestamp())
        if datetime.fromtimestamp(stamp, zone).replace(tzinfo=None) == local:
            options.add(stamp)
    return options.pop() if len(options) == 1 else None


def range_series(rows: list[tuple[EvidenceBar, int]], step: int, key: str,
                 spec: IndicatorSpec, cutoff: int) -> tuple[dict, list[str]]:
    session = SESSIONS.get(key)
    window = (spec.session or Window(timezone=session[1], start=session[2], end=session[3])) if session else None
    zone = ZoneInfo(window.timezone) if window else None
    windows = {}
    boundaries = {}
    warnings = set()
    for bar, available in rows:
        instant = datetime.fromtimestamp(bar.time, UTC)
        if window:
            local = instant.astimezone(zone)
            overnight = window.end < window.start
            day = local.date()
            if overnight and local.strftime("%H:%M") < window.end:
                day -= timedelta(days=1)
            if day not in boundaries:
                boundaries[day] = (boundary(day, window.start, zone),
                                   boundary(day + timedelta(days=overnight), window.end, zone))
            start, end = boundaries[day]
        else:
            day = instant.date()
            length = 1 if key == "previous_day" else 7
            day -= timedelta(days=day.weekday() if length == 7 else 0)
            start = int(datetime.combine(day, wall_time(), UTC).timestamp())
            end = start + length * 86400
        if start is None or end is None:
            warnings.add("AMBIGUOUS_OR_NONEXISTENT_SESSION_BOUNDARY")
            continue
        if start <= bar.time and bar.time + step <= end:
            windows.setdefault((start, end), []).append((bar, available))

    series = {"High": [], "Low": []}
    for (start, end), members in windows.items():
        high, low = float("-inf"), float("inf")
        complete = len(members) * step == end - start and members[0][0].time == start
        # Strictly nonoverlapping bars and the containment check prove full coverage.
        if not complete and end <= cutoff:
            warnings.add("INCOMPLETE_RANGE_COVERAGE")
        if session:
            for index, (bar, available) in enumerate(members):
                high, low = max(high, bar.high), min(low, bar.low)
                confirmed = complete and index == len(members) - 1
                for label, value in (("High", high), ("Low", low)):
                    series[label].append({"time": bar.time, "value": value, "available_at": available,
                                          "status": "confirmed" if confirmed else "provisional",
                                          "reference_start": start, "reference_end": end})
        elif (end, end + (end-start)) not in windows:
            continue
        elif complete:
            high = max(bar.high for bar, _ in members)
            low = min(bar.low for bar, _ in members)
            first = windows[(end, end + (end-start))]
            reference_available = max(available for _, available in members)
            for bar, available in first:
                if reference_available > available:
                    continue
                for label, value in (("High", high), ("Low", low)):
                    series[label].append({"time": bar.time, "value": value, "available_at": available,
                                          "status": "confirmed",
                                          "reference_start": start, "reference_end": end})
    if not session and (not series["High"] or len(series["High"]) < len(rows)):
        warnings.add("PREVIOUS_PERIOD_UNAVAILABLE")
    return series, sorted(warnings)


def evaluate(request: Evaluation) -> dict:
    definitions = {item["id"]: item for item in catalog()}
    if len({spec.id for spec in request.indicators}) != len(request.indicators):
        raise ValueError("Duplicate indicator IDs")
    if request.timeframe not in FRAME_MIN:
        raise ValueError("Unsupported timeframe")
    step = FRAME_MIN[request.timeframe] * 60
    rows = []
    warnings = set()
    previous = None
    for bar in request.candles:
        if bar.time > 32_503_680_000:
            raise ValueError("Candle timestamp outside supported range")
        available = max(bar.time + step, bar.available_at or 0)
        if not bar.closed or available > request.cutoff:
            continue
        if previous is not None:
            if bar.time < previous + step:
                raise ValueError("Candles must be ordered and cannot duplicate or overlap")
            if bar.time > previous + step:
                warnings.add("CANDLE_GAP")
        rows.append((bar, available))
        previous = bar.time
    bars = [bar for bar, _ in rows]
    availability = {bar.time: instant for bar, instant in rows}
    fingerprint = sha256()
    latest = 0
    for bar, available in rows:
        fingerprint.update(bar.model_dump_json().encode())
        latest = max(latest, available)
        availability[bar.time] = latest

    results = []
    for spec in request.indicators:
        definition = definitions.get(spec.id)
        if not definition or request.timeframe not in definition["timeframes"]:
            raise ValueError(f"Unsupported indicator or timeframe for {spec.id}")
        if spec.session is not None and spec.id not in SESSIONS:
            raise ValueError("Session options require a session indicator")
        if spec.period is not None and spec.id not in REGISTRY:
            raise ValueError("Period is only supported for numerical indicators")
        if spec.id in REGISTRY:
            result = calculate(bars, spec.id, spec.period or REGISTRY[spec.id].period)
            for points in result["series"].values():
                for point in points:
                    point.update(available_at=availability[point["time"]], status="confirmed")
            result_warnings = []
            if len(bars) < result["warmup_bars"]:
                result_warnings.append("INSUFFICIENT_WARMUP")
        else:
            series, result_warnings = range_series([(bar, availability[bar.time]) for bar in bars], step, spec.id, spec, request.cutoff)
            result = {"id": spec.id, "version": 1, "render": "overlay", "warmup_bars": None, "series": series}
            if spec.id in SESSIONS:
                result["session"] = spec.session.model_dump() if spec.session else definition["parameters"]["session"]
        results.append({**result, "warnings": sorted(warnings | set(result_warnings)), "unit": definition["unit"]})
    return {"schema_version": 1, "calculated_at": int(time()), "cutoff": request.cutoff,
            "authority": "CLIENT_SUPPLIED_UNVERIFIED",
            "input": {**request.model_dump(include={"source", "symbol", "timeframe", "dataset_id", "feed_id", "price_basis", "partitions"}),
                      "sha256": fingerprint.hexdigest(), "candles": len(rows)},
            "results": results}
