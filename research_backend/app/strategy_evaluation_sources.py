"""Approved archive selection and canonical producer integration for evaluator v1."""
from bisect import bisect_left, bisect_right
from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Literal

from pydantic import Field, StrictInt

from .analysis_evidence import AnalysisCoordinator, ResourceLimit, canonical
from .archive_provenance import require_market_partition
from .arrival_policy import SIMULATED_ARRIVAL, SIMULATION_AUTHORITY, SIMULATION_MODE
from .db import one
from .indicator_coverage import open_intervals
from .indicator_engine import Evaluation, IndicatorSpec, evaluate
from .indicator_history import load_reference
from .market import FRAME_MIN
from .smt_causal import ArchiveRequest, FrozenFeed, StrictSmtRequest, evaluate_strict_smt
from .storage import read_partition
from .strategy_definitions import Contract, Digest
from .strategy_evidence import fingerprint

EVALUATOR_VERSION = 2
MAX_TIMELINE = 4096
MAX_SELECTION_SLOTS = 6000
MAX_PUBLICATIONS = 16_000
CHART_IDS = {
    "CRT":"candle_range_theory", "STRUCTURE":"market_structure", "LIQUIDITY":"liquidity_sweeps",
    "FVG":"fair_value_gap", "ORDER_BLOCK":"order_blocks",
}
VIEW_KIND = {
    "CRT":"crt", "STRUCTURE":"structure", "LIQUIDITY":"liquidity",
    "FVG":"fvg", "ORDER_BLOCK":"order_blocks", "SMT":"smt",
}


class EvaluationInput(Contract):
    evaluator_version: Literal[2]
    strategy_id: str = Field(pattern=r"^ST-[a-f0-9]{32}$")
    strategy_version_id: str = Field(pattern=r"^SV-[a-f0-9]{32}$")
    configuration_fingerprint: Digest
    purpose: Literal["HISTORICAL_SIMULATION"]
    selection_id: str = Field(min_length=1, max_length=96)
    selection_fingerprint: Digest
    warmup_start: StrictInt = Field(ge=0, le=32503680000)
    evaluation_start: StrictInt = Field(ge=0, le=32503680000)
    cutoff: StrictInt = Field(ge=0, le=32503680000)


def selection_catalog():
    value = json.loads(Path(__file__).with_name("strategy_sources.json").read_text(encoding="utf-8"))
    return [{**value,"fingerprint":fingerprint(value),"authority":SIMULATION_AUTHORITY,
             "availability_policy":SIMULATED_ARRIVAL}]


def required_series(definition):
    keys = set()
    roles = {"PRIMARY":definition.instruments.primary,"COMPARISON":definition.instruments.comparison}
    for dependency in definition.indicators:
        for role in roles if dependency.role == "PAIR" else (dependency.role,):
            keys.add((role,dependency.timeframe))
    role = definition.trade_intent.role
    keys.add((role,definition.timeframes.trigger))

    def trace(group):
        if group is not None and group.type=="GROUP":
            if group.within and group.within.timeframe:
                keys.add((role,group.within.timeframe))
            for child in group.children:
                trace(child)

    for bound in [definition.expiry,definition.trade_intent.expiry,definition.management.time_exit,
                  *(link.within for link in definition.temporal)]:
        if bound and bound.timeframe:
            keys.add((role,bound.timeframe))
    for phase in ("context","setup","trigger","invalidation"):
        trace(getattr(definition,phase))
    return keys


def freeze_selection(request, definition):
    source = next((item for item in selection_catalog() if item["id"]==request.selection_id),None)
    if source is None or source["fingerprint"] != request.selection_fingerprint:
        raise ValueError("Selection must match an approved frozen archive fingerprint")
    if not source["start"] <= request.warmup_start < request.evaluation_start <= request.cutoff <= source["end"]:
        raise ValueError("Evaluate inside the approved UTC range with explicit preceding warm-up")
    lookup = {(item["symbol"],item["timeframe"]):item for item in source["series"]}
    bindings = {"PRIMARY":definition.instruments.primary,"COMPARISON":definition.instruments.comparison}
    calendar = definition.instruments.calendar.model_dump()
    feeds = {}
    for role,frame in sorted(required_series(definition)):
        binding = bindings[role]
        selected = lookup.get((binding.symbol,frame)) if binding else None
        if selected is None or (selected["source"],selected["price_basis"]) != (binding.source,binding.price_basis):
            raise ValueError(f"Unsupported approved source for {role} {frame}")
        for partition in selected["partitions"]:
            require_market_partition(partition["id"])
        dataset_id = selected["partitions"][0]["id"] if len(selected["partitions"])==1 else None
        feed = FrozenFeed.model_validate({**selected,"dataset_id":dataset_id,"start":request.warmup_start,"end":source["end"],
                                         "delay_seconds":binding.modeled_delay_seconds})
        if feed.calendar.model_dump() != calendar or request.evaluation_start % (FRAME_MIN[frame]*60):
            raise ValueError("Approved calendar and aligned evaluation start are required on each series")
        feeds[f"{role}:{frame}"] = feed
    slots = sum((feed.end-feed.start)//(FRAME_MIN[feed.timeframe]*60) for feed in feeds.values())
    if slots>MAX_SELECTION_SLOTS:
        raise ResourceLimit("Frozen selection exceeds the combined slot budget")
    return {"id":source["id"],"catalog_fingerprint":source["fingerprint"],"start":source["start"],
            "end":source["end"],"authority":SIMULATION_AUTHORITY,"availability_policy":SIMULATED_ARRIVAL,
            "series":{key:feed.model_dump(mode="json") for key,feed in feeds.items()}}


@dataclass(frozen=True)
class CoverageLane:
    key: str
    due: tuple
    observed: tuple
    missing: tuple
    provenance: dict

    def at(self, cutoff):
        expected = bisect_right(self.due,cutoff)
        received = bisect_right(self.observed,cutoff)
        missing = bisect_right(self.missing,cutoff)
        return {"series":self.key,"status":"COMPLETE" if expected and not missing else "UNKNOWN",
                "expected":expected,"observed":received,"missing":missing,
                "first_due":self.due[0] if self.due else None,
                "last_observed":self.observed[received-1] if received else None}


@dataclass(frozen=True)
class AnalyticalEvidence:
    registry: object
    views: dict
    events: dict
    coverage: dict
    timeline: tuple
    metrics: dict


class StrategyCoordinator(AnalysisCoordinator):
    def __init__(self, request):
        super().__init__(request,load_reference,event_sink=self.capture)
        self.events = defaultdict(list)
        self.views = {}
        self.last_views = {}
        self.publications = 0

    def capture(self, view, event):
        self.publications += 1
        if self.publications>MAX_PUBLICATIONS:
            raise ResourceLimit("Strategy publication budget exceeded")
        self.reserve(len(canonical(event).encode())*2+256)
        self.events[view.snapshot].append(deepcopy(event))

    def evidence_for(self, engine, rows, provenance, configuration, *, native=()):
        view = super().evidence_for(engine,rows,provenance,configuration,native=native)
        self.last_views[engine] = view
        return view


def producer_order(dependencies):
    by_id = {item.id:item for item in dependencies}
    completed = set()
    ordered = []

    def visit(item):
        if item.id in completed:
            return
        for parent in item.producers:
            visit(by_id[parent])
        completed.add(item.id)
        ordered.append(item)

    for item in sorted(dependencies,key=lambda dep:dep.id):
        visit(item)
    return ordered


def indicator_spec(dep, dependencies, calendar):
    rules = {**dep.settings,"market_schedule":calendar}
    if dep.indicator == "CRT":
        return IndicatorSpec(id=CHART_IDS["CRT"],crt=rules,crt_delivery={
            "formation_limit":0,"sweep_limit":0,"revision_limit":0,"current_limit":0})
    if dep.indicator == "FVG":
        return IndicatorSpec(id=CHART_IDS["FVG"],fvg=rules,fvg_delivery={
            "formation_limit":0,"revision_limit":0,"current_limit":0})
    if dep.indicator == "STRUCTURE":
        return IndicatorSpec(id=CHART_IDS["STRUCTURE"],structure=rules,
                             structure_delivery={"pivot_limit":0,"break_limit":0})
    parent, = dep.producers
    structure = {**dependencies[parent].settings,"market_schedule":calendar}
    if dep.indicator == "LIQUIDITY":
        return IndicatorSpec(id=CHART_IDS["LIQUIDITY"],structure=structure,liquidity=dep.settings,
                             liquidity_delivery={"pool_limit":0,"sweep_limit":0,"current_limit":0})
    return IndicatorSpec(id=CHART_IDS["ORDER_BLOCK"],structure=structure,ob=dep.settings,
                         ob_delivery={"formation_limit":0,"revision_limit":0,"current_limit":0})


def indicator_request(feed, cutoff, spec):
    return Evaluation(source=feed.source,symbol=feed.symbol,timeframe=feed.timeframe,feed_id=feed.feed_id,
                      dataset_id=feed.partitions[0].id if len(feed.partitions)==1 else None,
                      price_basis=feed.price_basis,partitions=feed.partitions,availability_mode=SIMULATION_MODE,
                      availability_start=feed.start,delay_seconds=feed.delay_seconds,calendar=feed.calendar,
                      cutoff=cutoff,frozen=True,load_reference=True,indicators=[spec],candles=[])


def collect(definition, selection, cutoff, evaluation_start, *, check_budget=None):
    feeds = {key:FrozenFeed.model_validate(feed) for key,feed in selection["series"].items()}
    coordinator = StrategyCoordinator(indicator_request(next(iter(feeds.values())),cutoff,
                                                         IndicatorSpec(id="market_structure")))
    coverage = {}
    for key,feed in feeds.items():
        if check_budget:
            check_budget()
        for partition in feed.partitions:
            stored = one("SELECT * FROM datasets WHERE id=?",(partition.id,))
            actual = tuple(stored[name] for name in ("provider","feed_id","symbol","kind","timeframe","price_basis","sha256")) if stored else None
            expected = (feed.source.removesuffix("_SAVED"),feed.feed_id,feed.symbol,"candles",
                        feed.timeframe,feed.price_basis,partition.sha256)
            if actual!=expected or not 0 < stored["rows_count"] <= 2000:
                raise ValueError(f"Frozen dataset scope or checksum metadata mismatch for {key}")
            try:
                coordinator.archive.read(stored,read_partition)
            except (FileNotFoundError, RuntimeError) as error:
                raise ValueError(f"Frozen partition failed verification for {key}: {error}") from error
        step = FRAME_MIN[feed.timeframe]*60
        end = min(feed.end,cutoff//step*step)
        if end <= feed.start:
            raise ValueError(f"No approved warm-up coverage for {key}")
        history = coordinator.history(ArchiveRequest(**feed.model_dump(),cutoff=cutoff,availability_mode=SIMULATION_MODE),
                                      feed.start,end,step)
        provenance = history["evidence"]
        if provenance.get("status") or provenance["context_authority"]!=SIMULATION_AUTHORITY:
            raise ValueError(f"{key}: {provenance.get('reason','Untrusted archive coverage')}")
        expected = tuple(stamp+step+feed.delay_seconds for begin,stop in open_intervals(feed.start,end,feed.calendar)
                         for stamp in range(begin,stop,step))
        visible = tuple(sorted(available for _,available in history["rows"]))
        missing = tuple(sorted(set(expected).difference(visible)))
        lane = CoverageLane(key,expected,visible,missing,provenance)
        if lane.at(evaluation_start)["status"]!="COMPLETE":
            raise ValueError(f"Incomplete required warm-up for {key}")
        coverage[key] = lane

    by_id = {dep.id:dep for dep in definition.indicators}
    computed = {}
    for dep in producer_order(definition.indicators):
        if check_budget:
            check_budget()
        keys = ([f"PRIMARY:{dep.timeframe}",f"COMPARISON:{dep.timeframe}"] if dep.role=="PAIR" else
                [f"{dep.role}:{dep.timeframe}"])
        settings = dep.settings
        structural = by_id[dep.producers[0]].settings if dep.producers else settings
        minimum = (5 if dep.indicator=="FVG" else settings["period"]*2+1 if dep.indicator=="SMT" else
                   12 if dep.indicator=="CRT" else structural["left"]+structural["right"]+1)
        if any(coverage[key].at(evaluation_start)["observed"]<minimum for key in keys):
            raise ValueError(f"Insufficient producer warm-up for {dep.id}: require {minimum} observed bars")
        identity = (dep.indicator, tuple(keys),canonical(dep.settings),
                    tuple(coordinator.views[parent].snapshot for parent in dep.producers))
        if identity not in computed:
            coordinator.last_views.clear()
            if dep.indicator=="SMT":
                data = [feeds[key] for key in keys]
                request = StrictSmtRequest(primary=data[0],comparison=data[1],cutoff=cutoff,
                                           availability_mode=SIMULATION_MODE,limit=0,**dep.settings)
                output = evaluate_strict_smt(request,coordinator=coordinator)
                if output.get("status") not in {"VERIFIED", "EVALUATED", None} and not coordinator.last_views.get("smt"):
                    raise ValueError(f"SMT evidence unavailable: {output.get('reason',output.get('status'))}")
            else:
                spec = indicator_spec(dep,by_id,definition.instruments.calendar.model_dump())
                output = evaluate(indicator_request(feeds[keys[0]],cutoff,spec),load_reference,coordinator=coordinator)
                if output["results"][0]["provenance"].get("context_authority")!=SIMULATION_AUTHORITY:
                    raise ValueError(f"Unsupported producer authority for {dep.id}")
            view = coordinator.last_views.get(VIEW_KIND[dep.indicator])
            if view is None:
                raise ValueError(f"Canonical producer unavailable for {dep.id}")
            computed[identity] = view
        coordinator.views[dep.id] = computed[identity]

    events = {}
    timeline = {cutoff,evaluation_start}
    for view in {value.snapshot:value for value in coordinator.views.values()}.values():
        published = sorted(coordinator.events[view.snapshot],
                           key=lambda event:(event["available_at"],event["timing"]["observation_start"],event["id"]))
        lanes = defaultdict(list)
        for event in published:
            lanes[event["evidence_reference"]["category"]].append(event)
            if evaluation_start<=event["available_at"]<=cutoff:
                timeline.add(event["available_at"])
        events[view.snapshot] = {category:(tuple(row["available_at"] for row in values),tuple(values))
                                 for category,values in lanes.items()}
    for lane in coverage.values():
        timeline.update(stamp for stamp in lane.due if evaluation_start<=stamp<=cutoff)
    if len(timeline)>MAX_TIMELINE:
        raise ResourceLimit("Strategy decision timeline budget exceeded")
    metrics = {"unique_producers":len(computed),"registry_publications":coordinator.publications,
               "archive":dict(coordinator.archive.counts),"analytical_work":coordinator.work,
               "estimated_retained_bytes":coordinator.reserved_bytes}
    return AnalyticalEvidence(coordinator.evidence,coordinator.views,events,coverage,tuple(sorted(timeline)),metrics)
