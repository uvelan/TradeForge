"""Versioned, causal calculations over a single identified candle series."""

from datetime import UTC, date, datetime, time as wall_time, timedelta
from hashlib import sha256
from time import time
from typing import Annotated, Literal, TypedDict
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, StringConstraints, model_validator

from .indicators import Bar, REGISTRY, calculate
from .market import FRAME_MIN
from .causal_timing import STRICT, TimingMode, strategy_eligible
from .arrival_policy import AvailabilityMode, LEGACY_MODE, MODE_VERSIONS, SIMULATION_MODE, availability_mode
from .analysis_evidence import AnalysisCoordinator, AnalysisKey, EvidenceIndex, ResourceLimit, SCOPE_FIELDS, canonical, digest
from .market_structure import StructureRules, detect_structure
from .structure_pages import StructureDelivery
from .liquidity import LiquidityDelivery, LiquidityRules, LiquidityStream
from .crt import COMBINATIONS, FRAMES, CRTDelivery, CRTRules, evaluate_crt
from .order_blocks import OrderBlockRules, OrderBlockDelivery, OrderBlockStream
from .fvg import FVGDelivery, FVGRules, evaluate_fvg
from .indicator_coverage import DAY, WEEK, MarketSchedule, period_start, previous_levels

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


class ContextOptions(BaseModel):
    policy: Literal["shared", "independent"] = "independent"
    structure: StructureRules | None = None
    liquidity: LiquidityRules | None = None
    fvg: FVGRules | None = None
    order_blocks: OrderBlockRules | None = None

    @model_validator(mode="after")
    def validate_policy(self):
        if self.policy=="shared" and any((self.structure,self.liquidity,self.fvg,self.order_blocks)):
            raise ValueError("Shared context takes rules from the selected indicator configurations")
        return self


class IndicatorSpec(BaseModel):
    id: str = Field(max_length=40)
    period: int | None = Field(default=None, ge=2, le=200)
    session: Window | None = None
    schedule: MarketSchedule | None = None
    structure: StructureRules | None = None
    structure_delivery: StructureDelivery | None = None
    liquidity: LiquidityRules | None = None
    liquidity_delivery: LiquidityDelivery | None = None
    fvg: FVGRules | None = None
    fvg_delivery: FVGDelivery | None = None
    ob: OrderBlockRules | None = None
    ob_delivery: OrderBlockDelivery | None = None
    crt: CRTRules | None = None
    crt_delivery: CRTDelivery | None = None
    context: ContextOptions | None = None


class EvidenceBar(Bar):
    available_at: int | None = Field(default=None, ge=0)
    closed: bool = True

    model_config = {"frozen":True,"extra":"ignore"}


class Partition(BaseModel):
    id: str = Field(max_length=200)
    sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")


class Evaluation(BaseModel):
    timing_policy: TimingMode = "LEGACY_RESEARCH"
    availability_mode: AvailabilityMode | None = None
    availability_start: int | None = Field(default=None, ge=0, le=32_503_680_000)
    arrival_manifest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    native_arrival_manifests: dict[str,str] = Field(default_factory=dict,max_length=3)
    delay_seconds: int = Field(default=0,ge=0,le=86_400)
    calendar: MarketSchedule | None = None
    source: Literal["DEMO", "MT5", "MT5_SAVED", "DUKASCOPY_SAVED", "BINANCE_SPOT"]
    symbol: str = Field(min_length=1, max_length=64)
    timeframe: str
    feed_id: str | None = Field(default=None, max_length=200)
    dataset_id: str | None = Field(default=None, max_length=200)
    price_basis: str | None = Field(default=None, max_length=40)
    partitions: list[Partition] = Field(default_factory=list, max_length=500)
    load_reference: bool = False
    frozen: bool = False
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
            "id": key, "label": label, "version": 1 if session else 2, "render": "overlay",
            "period": None, "warmup_bars": None, "price_source": "wick",
            "required_fields": ["high", "low"], "unit": "price",
            "timeframes": [name for name, minutes in FRAME_MIN.items() if not session or minutes < 1440],
            "instruments": "any_valid_ohlc",
            "parameters": {"session": dict(zip(("timezone", "start", "end"), session[1:]))} if session else
            {"schedule": ["continuous", "forex_weekly", "unknown"]},
        })
    defaults = StructureRules()
    definitions.append({
        "id": "market_structure", "label": "Confirmed market structure", "version": 2,
        "render": "markers", "period": None, "warmup_bars": defaults.left+defaults.right+1,
        "parameters": {"structure": defaults.model_dump(), "algorithm_versions": [1, 2]}, "price_source": "wick_or_close",
        "required_fields": ["high", "low", "close"], "unit": "price", "timeframes": list(FRAME_MIN),
        "instruments": "any_valid_ohlc",
    })
    definitions.append({
        "id": "liquidity_sweeps", "label": "Liquidity pools and sweeps", "version": 1,
        "render": "markers", "period": None, "warmup_bars": defaults.left+defaults.right+1,
        "parameters": {"structure": defaults.model_dump(), "liquidity": LiquidityRules().identity()},
        "price_source": "wick_or_close", "required_fields": ["open", "high", "low", "close"],
        "unit": "price", "timeframes": list(FRAME_MIN), "instruments": "any_valid_ohlc",
    })
    definitions.append({
        "id":"fair_value_gap", "label":"Fair Value Gaps (FVG)", "version":1,
        "render":"markers", "period":None, "warmup_bars":3,
        "parameters":{"fvg":FVGRules().canonical()}, "price_source":"wick",
        "required_fields":["open","high","low","close"], "unit":"price",
        "timeframes":list(FRAME_MIN), "instruments":"any_valid_ohlc",
    })
    ob_structure = StructureRules(algorithm_version=2)
    definitions.append({
        "id":"order_blocks", "label":"Confirmed Order Blocks (OB)", "version":1,
        "render":"markers", "period":None, "warmup_bars":ob_structure.left+ob_structure.right+1,
        "parameters":{"ob":OrderBlockRules().model_dump(),"structure":ob_structure.model_dump()},
        "price_source":"wick_or_close", "required_fields":["open","high","low","close"],
        "unit":"price", "timeframes":list(FRAME_MIN), "instruments":"any_valid_ohlc",
    })
    definitions.append({
        "id":"candle_range_theory","label":"Candle Range Theory (CRT) Â· Baseline","version":1,
        "render":"markers","period":None,"warmup_bars":None,"timeframes":list(COMBINATIONS),
        "price_source":"wick_and_close","required_fields":["open","high","low","close"],
        "unit":"price","instruments":"any_valid_ohlc",
        "parameters":{"crt":CRTRules().model_dump(),"crt_frames":COMBINATIONS},
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
                 spec: IndicatorSpec, cutoff: int, *, on_reference=None) -> tuple[dict, list[str]]:
    session = SESSIONS.get(key)
    window = spec.session or Window(timezone=session[1], start=session[2], end=session[3])
    zone = ZoneInfo(window.timezone)
    windows = {}
    boundaries = {}
    warnings = set()
    for bar, available in rows:
        instant = datetime.fromtimestamp(bar.time, UTC)
        local = instant.astimezone(zone)
        overnight = window.end < window.start
        day = local.date()
        if overnight and local.strftime("%H:%M") < window.end:
            day -= timedelta(days=1)
        if day not in boundaries:
            boundaries[day] = (boundary(day, window.start, zone),
                               boundary(day + timedelta(days=overnight), window.end, zone))
        start, end = boundaries[day]
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
        if on_reference is not None:
            on_reference({"reference_start": start, "reference_end": end,
                          "status": "COMPLETE" if complete else "INCOMPLETE_RANGE_COVERAGE",
                          "expected_candles": (end-start)//step, "observed_candles": len(members),
                          "candidate_high": max(bar.high for bar, _ in members),
                          "candidate_low": min(bar.low for bar, _ in members),
                          "available_at": max(stamp for _, stamp in members)}, members, False)
        for index, (bar, available) in enumerate(members):
            high, low = max(high, bar.high), min(low, bar.low)
            confirmed = complete and index == len(members) - 1
            for label, value in (("High", high), ("Low", low)):
                series[label].append({"time": bar.time, "value": value, "available_at": available,
                                      "status": "confirmed" if confirmed else "provisional",
                                      "reference_start": start, "reference_end": end})
    return series, sorted(warnings)


def resolve_history(request, rows, step, rules, history_loader):
    server_evidence = request.frozen and (request.arrival_manifest or availability_mode(request)==SIMULATION_MODE)
    if rules.history_bars == 2000 and not server_evidence:
        return rows, {"context_authority": "CLIENT_SUPPLIED_UNVERIFIED"}
    if not history_loader:
        raise ValueError("Extended history requires the server history reader")
    end = request.cutoff // step * step
    start = rules.history_start if rules.history_start is not None else request.availability_start
    if start is None or start % step or end-start > rules.history_bars*step:
        raise ValueError("Extended history must be aligned and at most 10,000 slots")
    context = history_loader(request,start,end,step) if start<end else {
        "rows":[], "evidence":{"status":"HISTORY_UNAVAILABLE"}}
    selected_calendar = getattr(rules,"market_schedule",None)
    source_calendar = context["evidence"].get("capture_calendar") or context["evidence"].get("simulation_calendar")
    if (selected_calendar is not None and selected_calendar.kind != "unknown" and source_calendar
            and selected_calendar.model_dump() != source_calendar):
        return (), {**context["evidence"],"status":"UNKNOWN_COVERAGE","reason":"CALENDAR_POLICY_MISMATCH"}
    return context["rows"], context["evidence"]


def crt_history(request, rows, step, frame, rules, history_loader):
    crt_rows, provenance = resolve_history(request, rows, step, rules, history_loader)
    native_rows = ()
    if rules.reference_mode == "native":
        if rules.history_start is None or history_loader is None:
            raise ValueError("Native CRT requires extended same-source history")
        if frame == "D1" and (rules.daily_timezone,rules.daily_boundary)!=("UTC","00:00"):
            raise ValueError("Native CRT supports UTC boundaries only")
        stride = FRAMES[frame]
        start = rules.history_start//stride*stride
        end = request.cutoff//stride*stride
        if (request.cutoff//step*step-rules.history_start)//step+(end-start)//stride > 10000:
            raise ValueError("Combined CRT history exceeds 10,000 slots")
        if not provenance.get("status") and end>start:
            budget=provenance.get("retrieval",{})
            native_limits=(50-budget.get("partitions_examined",0),30000-budget.get("decoded_candles",0))
            native=history_loader(request.model_copy(update={"timeframe":frame}),start,end,stride,
                                  archive_limits=native_limits)
            evidence=native["evidence"]
            retrieval=[provenance.get("retrieval",{}),evidence.get("retrieval",{})]
            if (sum(r.get("partitions_examined",0) for r in retrieval)>50 or
                    sum(r.get("decoded_candles",0) for r in retrieval)>30000):
                raise ValueError("Combined CRT archive read budget exceeded")
            provenance={**provenance,"native_reference":evidence}
            if evidence.get("status"):
                provenance["status"]=evidence["status"]
            else:
                native_rows=native["rows"]
    return crt_rows, native_rows, provenance


def structure_scope(provenance):
    return {key:value for key,value in provenance.items()
            if key not in {"retrieval","context_partitions","partitions"}}


def level_rows_fingerprint(rows, encode):
    fingerprint=sha256()
    for bar,stamp in rows:
        fingerprint.update(encode(bar))
        fingerprint.update(f":{stamp};".encode())
    return fingerprint.hexdigest()


def contextual_reference(indicator,event,observed,provenance,configuration,snapshot,*,witness=None):
    stamp=event["available_at"]
    return {
        **({"evidence_reference":event["evidence_reference"]} if "evidence_reference" in event else {}),
        **({"timing":event["timing"],"strategy_eligibility":event["timing"]["strategy_eligibility"]}
           if "timing" in event else {"strategy_eligibility":"RESEARCH_ONLY_WHOLE_CANDLE_TIMING"}
           if indicator in {"liquidity_sweeps","order_blocks"} else {}),
        "contract_version":1,"indicator":indicator,"id":event["id"],"observation_id":event["id"],
        "witness_id":witness or event["id"],"algorithm_version":event.get("algorithm_version",1),
        "identity_policy_version":event.get("identity_policy_version",1),
        "configuration_fingerprint":configuration,"evidence_fingerprint":event.get(
            "evidence_fingerprint",event.get("input_fingerprint",event["id"])),
        "source_identity":{key:provenance.get(key) for key in SCOPE_FIELDS},
        "authority":provenance.get("context_authority","CLIENT_SUPPLIED_UNVERIFIED"),
        "timeframe":provenance["timeframe"],"observation_time":observed,
        "confirmation_time":event.get("confirmation_time"),"available_at":stamp,
        "first_eligible_at":stamp,"source_correction_time":None,"calculation_snapshot":snapshot,
    }


def build_crt_context(structure_events,products,level_sets,registry=None):
    index=EvidenceIndex()
    names={"fvg":"fair_value_gap","liquidity_sweep":"liquidity_sweeps","order_block":"order_blocks"}
    for category,events,source,configuration,snapshot in products:
        for event in events:
            observed=event.get("observation_time",event.get("c_time"))
            witness=event.get("local_witness") or event.get("fvg_id") or event.get("ob_id")
            if category=="liquidity_sweep":
                witness=digest([tuple(source.get(key) for key in SCOPE_FIELDS),event["liquidity_side"],
                                [(p["time"],p["price"],p["available_at"]) for p in event["constituents"]],
                                observed,event["available_at"],event["ohlc"]])
            index.add(category,contextual_reference(names[category],event,observed,source,configuration,snapshot,witness=witness))
    for category,event,observed,source,configuration,snapshot in structure_events:
        item=contextual_reference("market_structure",event,observed,source,configuration,snapshot,witness=event["local_witness"])
        item["price"]=event.get("price",event.get("level_price"))
        index.add(category,item)

    for name, product in level_sets.items():
        for reference in product["level_observations"]:
            index.add(name+"_"+reference["label"].lower(),reference)
    index.finish()
    if registry is None:
        return index.as_of
    def current_context(cutoff, observed):
        return {key:event for key,event in index.as_of(cutoff,observed).items()
                if key not in {"order_block","fvg"} or registry.current_at(event.get("evidence_reference"),cutoff)}
    return current_context


def evaluate(request: Evaluation, history_loader=None) -> dict:
    coordinator=AnalysisCoordinator(request,history_loader)
    history_loader=coordinator.history if history_loader is not None else None
    definitions = {item["id"]: item for item in catalog()}
    if len({spec.id for spec in request.indicators}) != len(request.indicators):
        raise ValueError("Duplicate indicator IDs")
    if request.timeframe not in FRAME_MIN:
        raise ValueError("Unsupported timeframe")
    step = FRAME_MIN[request.timeframe] * 60
    purpose = availability_mode(request)
    rows = []
    warnings = set()
    previous = None
    for bar in request.candles:
        if bar.time > 32_503_680_000:
            raise ValueError("Candle timestamp outside supported range")
        if (purpose!=LEGACY_MODE and bar.closed and bar.available_at is not None
                and bar.available_at<bar.time+step):
            raise ValueError("Recorded availability precedes the candle close")
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
    rows=tuple(rows)
    bars = [bar for bar, _ in rows]
    availability = {bar.time: instant for bar, instant in rows}
    fingerprint = sha256()
    serialized_bars = {}
    uses_levels = any(spec.id in SESSIONS or spec.id in PERIODS or spec.crt and spec.crt.include_context
                      for spec in request.indicators)
    latest = 0
    for bar, available in rows:
        encoded = bar.model_dump_json().encode()
        if uses_levels:
            serialized_bars[id(bar)] = encoded
        fingerprint.update(encoded)
        latest = max(latest, available)
        availability[bar.time] = latest

    input_identity = request.model_dump(
        include={"source", "symbol", "timeframe", "dataset_id", "feed_id", "price_basis", "partitions"})
    selected={spec.id:spec for spec in request.indicators}
    strict=purpose!=LEGACY_MODE
    timing_provenance={"timing_policy":STRICT,"timing_policy_version":3,"availability_mode":purpose,
                       "availability_mode_version":MODE_VERSIONS[purpose]} if strict else {}

    def resolved(rule):
        history,evidence=resolve_history(request,rows,step,rule,history_loader)
        provenance={**input_identity,**evidence,**timing_provenance,"schedule_completeness":"NOT_ESTABLISHED"}
        provenance["partitions"]=sorted(provenance["partitions"],key=lambda item:(item["id"],item["sha256"]))
        return history or (),provenance

    def structure_result(history,rule,provenance,delivery,consumer=None,consumer_registry=None):
        return coordinator.structure(detect_structure,history,step,rule,structure_scope(provenance),
            {"cutoff":request.cutoff,"frozen":request.frozen,"provenance":provenance},delivery,consumer,consumer_registry)

    def structure_key(rule):
        key=[rule.algorithm_version,rule.identity_parameters()]
        return [STRICT,3,*key] if strict else key

    def product_key(history,provenance,*rules):
        return (id(history),canonical(provenance),*(canonical(rule) for rule in rules))

    track_context=any(s.crt and s.crt.include_context or s.ob and s.ob.include_context for s in request.indicators)
    zero_fvg=FVGDelivery(current_limit=0,formation_limit=0,revision_limit=0)

    def fvg_product(history,provenance,rules):
        key=product_key(history,provenance,rules.canonical())

        def calculate_fvg():
            public=selected.get("fair_value_gap")
            delivery=zero_fvg
            if public and (public.fvg or FVGRules()).canonical()==rules.canonical():
                public_history,public_provenance=resolved(public.fvg or FVGRules())
                if key==product_key(public_history,public_provenance,rules.canonical()):
                    delivery=public.fvg_delivery or FVGDelivery()
            formations=[]
            result=evaluate_fvg(history,step,request.cutoff,rules,delivery,provenance,request.frozen,
                                on_formation=formations.append if track_context else None,
                                registry=coordinator.evidence_for("fvg",history,provenance,
                                    [step,rules.canonical(),request.frozen]))
            return result,formations
        return coordinator.run("fvg",key,history,calculate_fvg)

    def liquidity_product(history,provenance,structure,rules):
        key=product_key(history,provenance,structure_key(structure),rules.identity())

        def calculate_liquidity():
            sweeps=[]
            public=selected.get("liquidity_sweeps")
            delivery=LiquidityDelivery(pool_limit=0,sweep_limit=0,current_limit=0)
            if public:
                public_structure=public.structure or StructureRules()
                public_history,public_provenance=resolved(public_structure)
                if key==product_key(public_history,public_provenance,structure_key(public_structure),
                                    (public.liquidity or LiquidityRules()).identity()):
                    delivery=public.liquidity_delivery or LiquidityDelivery()
            liquid=LiquidityStream(rules,delivery,step,on_confirmed_sweep=sweeps.append if track_context else None,
                                   timing=(request.cutoff,provenance,structure.market_schedule) if strict else None, registry=coordinator.evidence_for(
                                       "liquidity",history,provenance,[step,structure_key(structure),rules.identity(),request.frozen]))
            detected=structure_result(history,structure,provenance,StructureDelivery(pivot_limit=0,break_limit=0),liquid.observe,liquid.registry if strict else None)
            return liquid.finish(detected["event_page"]["snapshot"],detected["provenance_id"]),sweeps,detected
        return coordinator.run("liquidity",key,history,calculate_liquidity)

    def history_options(rule):
        return {"history_bars":rule.history_bars,"history_start":rule.history_start}

    def effective_context(spec,parent,parent_structure,schedule):
        options=spec.context or ContextOptions()
        def selected_child(name):
            return selected.get(name) if options.policy=="shared" else None
        structure_spec=selected_child("market_structure")
        chart_liquidity=selected_child("liquidity_sweeps")
        chart_fvg=selected_child("fair_value_gap")
        chart_ob=selected_child("order_blocks")
        default_structure=parent_structure or StructureRules(algorithm_version=2,market_schedule=schedule,
                                                              **history_options(parent))
        structure=(structure_spec.structure or StructureRules()) if structure_spec else options.structure or default_structure
        liquidity_structure=(chart_liquidity.structure or StructureRules()) if chart_liquidity else structure
        fvg=(chart_fvg.fvg or FVGRules()) if chart_fvg else options.fvg or FVGRules(
            market_schedule=schedule,**history_options(parent))
        liquid=(chart_liquidity.liquidity or LiquidityRules()) if chart_liquidity else options.liquidity or LiquidityRules()
        ob=(chart_ob.ob or OrderBlockRules()) if chart_ob else options.order_blocks or OrderBlockRules()
        ob_structure=(chart_ob.structure or StructureRules(algorithm_version=2)) if chart_ob else structure
        return options.policy,structure,liquidity_structure,liquid,fvg,ob_structure,ob,chart_ob

    def ob_product(history,provenance,structure,rules,spec):
        options=spec.context or ContextOptions()
        key=product_key(history,provenance,structure_key(structure),rules.model_dump(),options.model_dump())

        def calculate_blocks():
            public=selected.get("order_blocks")
            delivery=OrderBlockDelivery(formation_limit=0,revision_limit=0,current_limit=0)
            if public:
                public_structure=public.structure or StructureRules(algorithm_version=2)
                public_history,public_provenance=resolved(public_structure)
                if key==product_key(public_history,public_provenance,structure_key(public_structure),
                                    (public.ob or OrderBlockRules()).model_dump(),
                                    (public.context or ContextOptions()).model_dump()):
                    delivery=public.ob_delivery or OrderBlockDelivery()
            forms=[]
            stream=OrderBlockStream(rules,structure,provenance,step,delivery,
                                    on_formation=forms.append if track_context else None,
                                    timing_cutoff=request.cutoff if strict else None, registry=coordinator.evidence)
            consumer=stream.observe
            metadata=None
            if rules.include_context:
                schedule=structure.market_schedule or MarketSchedule(kind="unknown")
                policy,_,liquid_structure,liquid,fvg,_,_,_=effective_context(spec,structure,structure,schedule)
                f_rows,f_source=resolved(fvg)
                f_result, f_events=fvg_product(f_rows,f_source,fvg)
                l_rows,l_source=resolved(liquid_structure)
                l_result,l_events,l_detected=liquidity_product(l_rows,l_source,liquid_structure,liquid)
                f_config=f_result["configuration_fingerprint"]
                l_config=digest([structure_key(liquid_structure),liquid.identity()])
                f_index=EvidenceIndex()
                l_index=EvidenceIndex()
                for event in f_events:
                    if strict and not strategy_eligible(event,request.cutoff,registry=coordinator.evidence,purpose=purpose):
                        continue
                    f_index.add("fvg",contextual_reference("fair_value_gap",event,event["c_time"],f_source,
                                      f_config,f_result["fvg_page"]["snapshot"],witness=event["fvg_id"]))
                for event in l_events:
                    if strict and not strategy_eligible(event,request.cutoff,registry=coordinator.evidence,purpose=purpose):
                        continue
                    ref=contextual_reference("liquidity_sweeps",event,event["observation_time"],l_source,
                                              l_config,l_result["liquidity_page"]["snapshot"])
                    ref["witness_id"]=digest([ref["source_identity"],event["liquidity_side"],
                        [(p["time"],p["price"],p["available_at"]) for p in event["constituents"]],
                        event["observation_time"],event["available_at"],event["ohlc"]])
                    l_index.add("sweep",ref)
                f_index.finish()
                l_index.finish()
                failures={name:value["status"] for name,value in (("fvg",f_source),("liquidity",l_source)) if value.get("status")}
                metadata={"policy":policy,"status":"DEPENDENCY_UNAVAILABLE" if failures else "EVALUATED",
                          "failures":failures,"fvg":fvg.canonical(),"liquidity":liquid.identity(),
                          "liquidity_structure":liquid_structure.identity_parameters(),
                          "dependency_snapshots":{"fvg":f_result["fvg_page"]["snapshot"],
                                                  "liquidity":l_result["liquidity_page"]["snapshot"]}}
                def context_at(available,observed):
                    limit=observed if strict else available
                    f=f_index.as_of(limit,observed).get("fvg")
                    l=l_index.as_of(limit,observed).get("sweep")
                    if strict and f and not coordinator.evidence.current_at(f.get("evidence_reference"),limit):
                        f=None
                    return {"fvg":f if strict else {**f,"id":f["witness_id"]} if f else None,"liquidity_sweep":l,
                            "status":"AVAILABLE" if f or l else "DEPENDENCY_UNAVAILABLE" if failures else "NO_AVAILABLE_CONTEXT"}
                stream.context_lookup=context_at
            if strict:
                stream.registry=coordinator.evidence_for("order_blocks",history,provenance,
                    [step,structure_key(structure),rules.model_dump(),options.model_dump(),metadata,request.frozen])
            detected=structure_result(history,structure,provenance,StructureDelivery(pivot_limit=0,break_limit=0),consumer,stream.registry if strict else None)
            result=stream.finish(detected["event_page"]["snapshot"],context_snapshot=digest(metadata) if metadata is not None else None)
            if metadata is not None:
                result["context_configuration"]=metadata
            return result,forms,detected
        return coordinator.run("order_blocks",key,history,calculate_blocks)

    caller_effective=rows if all(stamp==availability[bar.time] for bar,stamp in rows) else tuple(
        (bar,availability[bar.time]) for bar in bars)

    level_inputs = {}

    def encode_level_bar(bar):
        key = id(bar)
        if key not in serialized_bars:
            serialized_bars[key] = bar.model_dump_json().encode()
        return serialized_bars[key]

    def level_product(spec,history,observed,origin,load_external):
        schedule=spec.schedule or MarketSchedule(
            kind="continuous" if request.source in {"DEMO","BINANCE_SPOT"} else "unknown")
        params=({"schedule":schedule.model_dump()} if spec.id in PERIODS else
                {"session":spec.session.model_dump() if spec.session else definitions[spec.id]["parameters"]["session"]})
        key=(spec.id,id(history),id(observed),load_external,canonical(params),canonical(origin))

        def calculate_level():
            references={}
            length=None
            scope={name:origin.get(name) for name in SCOPE_FIELDS}
            observation_authority=origin.get("context_authority","CLIENT_SUPPLIED_UNVERIFIED")
            version=2 if spec.id in PERIODS else 1
            configuration=digest([spec.id,version,2,params])

            def record_reference(report, members, historical):
                evidence={name:value for name,value in report.items() if name!="retrieval"}
                if "context_partitions" in evidence:
                    evidence["context_partitions"]=sorted(evidence["context_partitions"],key=lambda part:part["id"])
                authority=evidence.get("context_authority","UNAVAILABLE") if historical else observation_authority
                evidence["reference_authority"]=authority
                evidence["bars_fingerprint"]=level_rows_fingerprint(members, encode_level_bar)
                references[evidence["reference_end"]]=evidence

            if spec.id in PERIODS:
                length=DAY if spec.id=="previous_day" else WEEK
                anchor=None
                if load_external and request.load_reference and history_loader and schedule.kind!="unknown":
                    end=period_start(request.cutoff,length)
                    if end>=length:
                        try:
                            anchor=history_loader(request,end-length,end,step)
                        except ResourceLimit:
                            anchor={"rows":(),"evidence":{"status":"HISTORY_LIMIT",
                                    "reason":"AGGREGATE_ARCHIVE_BUDGET_EXCEEDED","context_authority":"UNAVAILABLE"}}
                series,notes,coverage=previous_levels(history,step,length,schedule,request.cutoff,anchor,
                                                      on_reference=record_reference)
            else:
                series,notes=range_series(observed,step,spec.id,spec,request.cutoff,on_reference=record_reference)
                coverage=None
            manifest=sorted(origin.get("partitions",[]),key=lambda part:part["id"])
            history_identity={name:value for name,value in origin.items()
                              if name not in {"retrieval","partitions"}}
            history_identity["partitions"]=manifest
            if id(observed) not in level_inputs:
                level_inputs[id(observed)] = level_rows_fingerprint(observed, encode_level_bar)
            snapshot=digest([configuration,history_identity,request.cutoff,request.frozen,
                             level_inputs[id(observed)],references])
            reference_fingerprints={end:digest(evidence) for end,evidence in references.items()}
            observations=[]
            for end, evidence in sorted(references.items()):
                if evidence["status"]!="COMPLETE" or evidence["available_at"]>request.cutoff:
                    continue
                available=evidence["available_at"]
                observed_at=end if length is not None else end-step
                evidence_id=reference_fingerprints[end]
                for label in ("High","Low"):
                    value=evidence["candidate_"+label.lower()]
                    witness=digest([scope,configuration,label,evidence_id,value])
                    raw={"available_at":available,"id":digest([witness,observed_at,available]),
                         "confirmation_time":end,"algorithm_version":version,"identity_policy_version":2,
                         "evidence_fingerprint":evidence_id}
                    reference=contextual_reference(spec.id,raw,observed_at,origin,configuration,snapshot,witness=witness)
                    reference.update(label=label,price=value,reference_start=evidence["reference_start"],
                                     reference_end=end,observation_authority=observation_authority,
                                     reference_authority=evidence["reference_authority"],
                                     context_partitions=evidence.get("context_partitions",[]))
                    if evidence["reference_authority"]!=observation_authority:
                        reference["authority"]="MIXED_OBSERVATION_AND_REFERENCE"
                    if length is not None:
                        reference["valid_until"]=end+length
                    observations.append(reference)
            reference_authorities=sorted({ref["reference_authority"] for ref in references.values()})
            product={"version":1,"indicator":spec.id,"parameters":params,"source_identity":scope,
                     "cutoff":request.cutoff,"frozen":request.frozen,"algorithm_version":version,"identity_policy_version":2,
                     "dependencies":["candles","reference_windows"],"history":history_identity,
                     "authority":"MIXED_OBSERVATION_AND_REFERENCE" if any(
                         authority!=observation_authority for authority in reference_authorities) else observation_authority,
                     "observation_authority":observation_authority,"reference_authorities":reference_authorities,
                     "reference_windows":[{**evidence,"evidence_fingerprint":reference_fingerprints[end]}
                                          for end,evidence in sorted(references.items())],
                     "configuration_fingerprint":configuration,"calculation_snapshot":snapshot,
                     "calculation_id":snapshot,"level_observations":observations}
            return series,notes,coverage,params,product
        return coordinator.run("levels",key,history,calculate_level)

    def crt_context(spec,rules,crt_rows,provenance):
        policy,structure,liquid_structure,liquid,fvg,ob_structure,ob,ob_spec=effective_context(
            spec,rules,None,rules.market_schedule)
        s_rows,s_source=resolved(structure)
        canonical_events=[]
        def capture(bar,stamp,idx,gap,pivots,breaks,fp,authority):
            canonical_events.extend(("market_structure",event,bar.time) for event,_ in pivots)
            canonical_events.extend((event["type"],event,bar.time) for event in breaks
                                    if event["status"]=="confirmed" and event["type"] in {"BOS","CHOCH"})
        detection=structure_result(s_rows,structure,s_source,StructureDelivery(pivot_limit=0,break_limit=0),capture)
        s_config=digest(structure_key(structure))
        s_snapshot=detection["event_page"]["snapshot"]
        s_refs=[(*event,s_source,s_config,s_snapshot) for event in canonical_events]
        f_rows,f_source=resolved(fvg)
        f_result,f_events=fvg_product(f_rows,f_source,fvg)
        l_rows,l_source=resolved(liquid_structure)
        l_result,l_events,l_detected=liquidity_product(l_rows,l_source,liquid_structure,liquid)
        o_rows,o_source=resolved(ob_structure)
        o_result,o_events,_=ob_product(o_rows,o_source,ob_structure,ob,ob_spec or IndicatorSpec(id="order_blocks"))
        products=[("fvg",f_events,f_source,f_result["configuration_fingerprint"],f_result["fvg_page"]["snapshot"]),
                  ("liquidity_sweep",l_events,l_source,digest([structure_key(liquid_structure),liquid.identity()]),
                   l_result["liquidity_page"]["snapshot"]),
                  ("order_block",o_events,o_source,digest([structure_key(ob_structure),ob.model_dump()]),
                   o_result["ob_page"]["snapshot"])]
        failures={name:source["status"] for name,source in
                  (("structure",s_source),("liquidity",l_source),("fvg",f_source),("order_blocks",o_source))
                  if source.get("status")}
        metadata={"policy":policy,"status":"DEPENDENCY_UNAVAILABLE" if failures else "EVALUATED",
                  "failures":failures,"structure":structure.identity_parameters(),
                  "liquidity_structure":liquid_structure.identity_parameters(),"liquidity":liquid.identity(),
                  "fvg":fvg.canonical(),"order_block_structure":ob_structure.identity_parameters(),
                  "order_blocks":ob.model_dump(),
                  "dependency_snapshots":{"structure":s_snapshot,"fvg":f_result["fvg_page"]["snapshot"],
                         "liquidity":l_result["liquidity_page"]["snapshot"],"order_blocks":o_result["ob_page"]["snapshot"]}}
        known=0
        normalized=[]
        for bar,available in crt_rows:
            known=max(known,available)
            normalized.append((bar,known))
        effective=tuple(normalized)
        levels={}
        choices={}
        for name in (*SESSIONS,*PERIODS):
            public=selected.get(name) if policy=="shared" else None
            level_spec=public or IndicatorSpec(id=name,schedule=rules.market_schedule if name in PERIODS else None)
            origin=input_identity if public else provenance
            history=rows if public else effective
            _,notes,coverage,params,product=level_product(
                level_spec,history,caller_effective if public else effective,origin,bool(public))
            levels[name]=product
            choices[name]={"origin":"selected" if public else "baseline",**params}
            if coverage is not None and coverage["status"]!="COMPLETE":
                failures[name]={"status":coverage["status"],"reason":coverage.get("reason")}
            elif notes:
                failures[name]={"status":notes[0],"reason":None}
        metadata["status"]="DEPENDENCY_UNAVAILABLE" if failures else "EVALUATED"
        metadata["levels"]=choices
        metadata["strategy_eligibility"]="RESEARCH_ONLY_OPTIONAL_CONTEXT"
        metadata["dependency_snapshots"].update({name:item["calculation_snapshot"] for name,item in levels.items()})
        if strict:
            metadata["strategy_context_policy"]="ONLY_ELIGIBLE_AT_OBSERVATION_OPEN"
            metadata["excluded_unattested_levels"]=list(levels)
            s_refs=[item for item in s_refs if strategy_eligible(item[1],request.cutoff,registry=coordinator.evidence,purpose=purpose)]
            products=[(name,[event for event in events if strategy_eligible(event,request.cutoff,registry=coordinator.evidence,purpose=purpose)],source,config,snapshot)
                      for name,events,source,config,snapshot in products]
            # Level observations lack trusted availability attestations for the caller input.
            levels={}
        return build_crt_context(s_refs,products,levels,coordinator.evidence),metadata

    results = []
    for spec in request.indicators:
        definition = definitions.get(spec.id)
        if not definition or request.timeframe not in definition["timeframes"]:
            raise ValueError(f"Unsupported indicator or timeframe for {spec.id}")
        if spec.session is not None and spec.id not in SESSIONS:
            raise ValueError("Session options require a session indicator")
        if spec.schedule is not None and spec.id not in PERIODS:
            raise ValueError("Schedule options require a previous-period indicator")
        if (spec.fvg is not None or spec.fvg_delivery is not None) and spec.id != "fair_value_gap":
            raise ValueError("FVG options require the FVG indicator")
        if (spec.liquidity is not None or spec.liquidity_delivery is not None) and spec.id != "liquidity_sweeps":
            raise ValueError("Liquidity options require the liquidity indicator")
        if spec.context is not None and spec.id not in {"order_blocks","candle_range_theory"}:
            raise ValueError("Context options require Order Blocks or CRT")
        if (spec.crt is not None or spec.crt_delivery is not None) and spec.id!="candle_range_theory":
            raise ValueError("CRT options require the CRT indicator")
        if (spec.ob is not None or spec.ob_delivery is not None) and spec.id != "order_blocks":
            raise ValueError("OB options require the Order Blocks indicator")
        if spec.structure is not None and spec.id not in {"market_structure","liquidity_sweeps","order_blocks"}:
            raise ValueError("Structure settings require structure or liquidity")
        if spec.structure_delivery is not None and spec.id != "market_structure":
            raise ValueError("Structure options require the market structure indicator")
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
        elif spec.id in {"market_structure","liquidity_sweeps"}:
            rules=spec.structure or StructureRules()
            history,provenance=resolved(rules)
            if spec.id=="market_structure":
                detected=structure_result(history,rules,provenance,spec.structure_delivery or StructureDelivery())
                result={"id":spec.id,"version":rules.algorithm_version,"render":"markers","series":{},
                        "warmup_bars":rules.left+rules.right+1,"parameters":rules.identity_parameters(),
                        "provenance":provenance,**{key:value for key,value in detected.items() if key!="structure_warnings"}}
            else:
                liquid=spec.liquidity or LiquidityRules()
                output,_,detected=liquidity_product(history,provenance,rules,liquid)
                result={"id":spec.id,"version":1,"render":"markers","series":{},
                        "warmup_bars":rules.left+rules.right+1,"parameters":liquid.identity(),
                        "structure_version":rules.algorithm_version,"structure_parameters":rules.identity_parameters(),
                        "schedule_assumption":(detected.get("continuity") or {}).get("schedule"),
                        "provenance":provenance,**output}
            result_warnings=list(detected["structure_warnings"])
            if provenance.get("status"):
                result_warnings.append(provenance["status"])
            if not detected["pivots"] and len(history)<rules.left+rules.right+1:
                result_warnings.append("INSUFFICIENT_WARMUP")
        elif spec.id=="candle_range_theory":
            rules=spec.crt or CRTRules()
            allowed=COMBINATIONS[request.timeframe]
            frame=rules.reference_frame or allowed[0]
            if frame not in allowed:
                raise ValueError("Unsupported CRT timeframe combination")
            crt_rows,native,evidence=crt_history(request,rows,step,frame,rules,history_loader)
            provenance={**input_identity,**evidence,**timing_provenance}
            provenance["partitions"]=sorted(provenance["partitions"],key=lambda p:(p["id"],p["sha256"]))
            failed=bool(evidence.get("status"))
            context,metadata=None,None
            if rules.include_context:
                if failed:
                    metadata={"policy":(spec.context or ContextOptions()).policy,"status":"DEPENDENCY_UNAVAILABLE",
                              "failures":{"reference":evidence["status"]}}
                else:
                    context,metadata=crt_context(spec,rules,crt_rows,provenance)
            output=coordinator.run("crt",spec.id,crt_rows,lambda:evaluate_crt(
                [] if failed else crt_rows,step,request.cutoff,frame,rules,spec.crt_delivery or CRTDelivery(),
                provenance,native,context=context,context_snapshot=digest(metadata) if metadata is not None else None,
                registry=coordinator.evidence_for("crt",crt_rows,provenance,
                    [step,frame,rules.model_dump(),metadata,request.frozen],native=native)))
            result={"id":spec.id,"version":1,"render":"markers","series":{},"warmup_bars":None,
                    "parameters":rules.model_dump(),"provenance":provenance,**output}
            if metadata is not None:
                result["context_configuration"]=metadata
            result_warnings=list(result["quality"])
            if failed:
                result_warnings.append(evidence["status"])
        elif spec.id=="order_blocks":
            structure=spec.structure or StructureRules(algorithm_version=2)
            rules=spec.ob or OrderBlockRules()
            history,provenance=resolved(structure)
            output,_,detected=ob_product(history,provenance,structure,rules,spec)
            result_warnings=list(detected["structure_warnings"])
            if provenance.get("status"):
                result_warnings.append(provenance["status"])
            result={"id":spec.id,"version":1,"render":"markers","series":{},
                    "warmup_bars":structure.left+structure.right+1,"parameters":rules.model_dump(),
                    "structure_parameters":structure.identity_parameters(),"provenance":provenance,**output}
        elif spec.id=="fair_value_gap":
            rules=spec.fvg or FVGRules()
            history,provenance=resolved(rules)
            output,_=fvg_product(history,provenance,rules)
            result_warnings=list(output["warnings"])
            if provenance.get("status"):
                result_warnings.append(provenance["status"])
            result={"id":spec.id,"version":1,"render":"markers","series":{},
                    "warmup_bars":max(3,rules.atr_period if rules.atr_multiple else 0),
                    "parameters":rules.canonical(),"provenance":provenance,
                    **{key:value for key,value in output.items() if key!="warnings"}}
        else:
            series,result_warnings,coverage,parameters,product=level_product(spec,rows,caller_effective,input_identity,True)
            result={"id":spec.id,"version":2 if spec.id in PERIODS else 1,
                    "render":"overlay","series":series,"warmup_bars":None,"analysis":product,**parameters}
            if coverage is not None:
                result.update(coverage=coverage,coverage_policy_version=1)
        include_gaps = spec.id != "fair_value_gap" and spec.id not in PERIODS and not (spec.id == "market_structure" and result["version"] == 2) and not (
            spec.id == "liquidity_sweeps" and result["structure_version"] == 2)
        results.append({**result, "warnings": sorted((warnings if include_gaps else set()) | set(result_warnings)),
                        "unit": definition["unit"]})
    dependencies={"market_structure":("candles",),"liquidity_sweeps":("candles","market_structure"),
                  "order_blocks":("candles","market_structure"),"fair_value_gap":("candles",),
                  "candle_range_theory":("candles","higher_timeframe")}
    for result,spec in zip(results,request.indicators):
        if "analysis" in result:
            continue
        provenance=result.get("provenance",input_identity)
        parameters={key:result[key] for key in ("parameters","structure_parameters","schedule","session") if key in result}
        if "period" in result:
            parameters["period"]=result["period"]
        dep=dependencies.get(result["id"],("candles",))
        if strict and result["id"] in dependencies:
            parameters["timing_policy"]={"mode":STRICT,"version":3}
        if "context_configuration" in result:
            parameters["context"]={key:value for key,value in result["context_configuration"].items()
                                   if key not in {"status","failures","dependency_snapshots"}}
            dep+=("context",)
        window={key:provenance[key] for key in ("partitions","context_partitions","native_reference","retrieval")
                if key in provenance}
        history_rules=result.get("structure_parameters",result.get("parameters",{}))
        window.update({"mode":"extended" if history_rules.get("history_bars")==10000 else "caller",
                       "history_start":history_rules.get("history_start"),
                       "caller_first":bars[0].time if bars else None,
                       "caller_last":bars[-1].time if bars else None})
        key=AnalysisKey(result["id"],tuple(input_identity.get(name) for name in SCOPE_FIELDS),canonical(window),
                        request.cutoff,request.frozen,result.get("version",1),
                        result.get("identity_policy_version",2 if result["id"]=="fair_value_gap" else 1),
                        canonical(parameters),dep,provenance.get("context_authority","CLIENT_SUPPLIED_UNVERIFIED"))
        contract=key.contract()
        snapshot=next((result[page]["snapshot"] for page in
                       ("event_page","liquidity_page","fvg_page","ob_page","crt_page")
                       if page in result and "snapshot" in result[page]),None)
        if spec.id=="market_structure":
            configured=digest(structure_key(spec.structure or StructureRules()))
        elif spec.id in {"liquidity_sweeps","order_blocks"}:
            structure=spec.structure or StructureRules(algorithm_version=2 if spec.id=="order_blocks" else 1)
            configured=digest([structure_key(structure),result["parameters"]])
        else:
            configured=result.get("configuration_fingerprint") or digest(
                [contract["indicator"],contract["algorithm_version"],contract["identity_policy_version"],
                 contract["parameters"]])
        # Context-only settings must not alter stable engine reference fingerprints.
        calculation_id=digest([contract,snapshot or fingerprint.hexdigest()])
        result["analysis"]={**contract,"calculation_id":calculation_id,
                            "configuration_fingerprint":configured,
                            "calculation_snapshot":snapshot or calculation_id}
        if spec.id in {"liquidity_sweeps","order_blocks"}:
            result["analysis"]["strategy_eligibility"]="RESEARCH_ONLY_WHOLE_CANDLE_TIMING"
        if spec.id=="candle_range_theory":
            settings=spec.crt or CRTRules()
            result["analysis"]["timeframe_evidence"]={
                "version":1,"observation_frame":request.timeframe,"reference_frame":result["reference_frame"],
                "mode":settings.reference_mode,"boundary_timezone":settings.daily_timezone if result["reference_frame"]=="D1" else "UTC",
                "daily_boundary":settings.daily_boundary if result["reference_frame"]=="D1" else None,
                "calendar":settings.market_schedule.model_dump(),
                "reference_authority":provenance.get("native_reference",provenance).get("context_authority"),
                "eligibility":"closed_and_available_before_interaction",
            }
    for result in results:
        result["analysis"].update(timing_policy=STRICT if strict else request.timing_policy,timing_policy_version=3 if strict else 1)
        if strict:
            result["analysis"]["availability_mode"] = purpose
        if strict and result.get("provenance",{}).get("status"):
            result["analysis"]["strategy_eligibility"]=result["provenance"]["status"]
        if not strict:
            result["analysis"].setdefault("strategy_eligibility","RESEARCH_ONLY")
    return {"schema_version": 1, "diagnostics":coordinator.diagnostics(results), "calculated_at": int(time()), "cutoff": request.cutoff,
            "authority": "CLIENT_SUPPLIED_UNVERIFIED",
            "input": {**input_identity,
                      "sha256": fingerprint.hexdigest(), "candles": len(rows)},
            "results": results}
