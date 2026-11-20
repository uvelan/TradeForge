"""Pinned market inputs and policy for historical strategy qualification."""
from typing import Literal

from pydantic import Field

from .analysis_evidence import ResourceLimit
from .arrival_policy import SIMULATION_MODE
from .db import one
from .market import FRAME_MIN
from .strategy_definitions import Contract, Digest, Key
from .strategy_evaluation_routes import load_definition
from .strategy_evaluation_sources import EvaluationInput, collect, freeze_selection, selection_catalog
from .strategy_evaluator import RuleGraph
from .strategy_evidence import fingerprint

RUN_VERSION = 2
CHECKPOINT_VERSION = 2
MAX_ATTEMPTS = 32
MAX_ORACLE_CUTOFFS = 160
MAX_WORKERS = 1
MAX_QUEUE = 8
MAX_SAVED_RUNS = 256
HARD_SECONDS = 150
MEMORY_BYTES = 768 * 1024 * 1024
MAX_CHECKPOINT_BYTES = 256 * 1024
MAX_LEDGER_BYTES = 16 * 1024 * 1024
LEASE_SECONDS = 12


class HistoricalInput(EvaluationInput):
    historical_version: Literal[2] = RUN_VERSION
    rearm: Literal["SINGLE_ATTEMPT","NEW_CONTEXT_FAMILY"] = "SINGLE_ATTEMPT"
    context_alias: Key | None = None
    max_attempts: int = Field(default=1,strict=True,ge=1,le=MAX_ATTEMPTS)
    partitions: list[Digest] = Field(default_factory=list,max_length=24)


def approved_partition(row):
    # A hash in the catalog alone is not a market authority. Require a bound
    # immutable import manifest, or an exact reviewed catalog commitment.
    for approved in selection_catalog():
        for lane in approved["series"]:
            if (row["provider"],row["feed_id"],row["symbol"],row["timeframe"],row["price_basis"]) != (
                    lane["source"].removesuffix("_SAVED"),lane["feed_id"],lane["symbol"],lane["timeframe"],lane["price_basis"]):
                continue
            if {"id":row["id"],"sha256":row["sha256"]} in lane["partitions"]:
                return True
    if row["provider"] != "DUKASCOPY":
        return False
    record = one("""SELECT 1 FROM historical_imports i, json_each(i.manifest,'$.candle_datasets') p
                    WHERE i.feed_id=? AND i.symbol=? AND json_extract(i.manifest,'$.source.provider')=?
                    AND json_extract(i.manifest,'$.source.sha256')=i.source_sha256
                    AND json_extract(p.value,'$.id')=? AND json_extract(p.value,'$.sha256')=?
                    AND json_extract(p.value,'$.timeframe')=? AND json_extract(p.value,'$.price_basis')=?
                    AND json_extract(p.value,'$.rows')=? LIMIT 1""",
                 (row["feed_id"],row["symbol"],row["provider"],row["id"],row["sha256"],
                  row["timeframe"],row["price_basis"],row["rows_count"]))
    return record is not None


def workspace_selection(request, definition):
    if len(request.partitions)!=len(set(request.partitions)):
        raise ValueError("Duplicate source partition")
    grouped = {}
    for identifier in sorted(request.partitions):
        row = one("SELECT * FROM datasets WHERE id=?",(identifier,))
        if not row or row["kind"]!="candles" or row["timeframe"] not in {"M1","M5"} or not approved_partition(row):
            raise ValueError("Partition has no admitted historical provenance")
        key = (row["symbol"],row["timeframe"])
        scope = {"symbol":row["symbol"],"timeframe":row["timeframe"],"source":row["provider"]+"_SAVED",
                 "feed_id":row["feed_id"],"price_basis":row["price_basis"],
                 "calendar":definition.instruments.calendar.model_dump(),"partitions":[]}
        existing = grouped.setdefault(key,scope)
        if any(scope[field]!=existing[field] for field in ("source","feed_id","price_basis")):
            raise ValueError("Overlapping source identities for one required series")
        existing["partitions"].append({"id":identifier,"sha256":row["sha256"]})
    selection = {"id":"WORKSPACE","start":request.warmup_start,"end":request.cutoff,
                 "series":[grouped[key] for key in sorted(grouped)]}
    return {**selection,"fingerprint":fingerprint(selection)}


def mandatory_alias(group, alias):
    if group.type=="RULE":
        return group if group.alias==alias else None
    if group.operator=="ALL":
        return next((found for child in group.children if (found:=mandatory_alias(child,alias))),None)
    return None


def prepare_historical(request):
    definition = load_definition(request)
    source = workspace_selection(request,definition) if request.partitions else None
    if source and (request.selection_id!="WORKSPACE" or
                   request.selection_fingerprint not in {"0"*64,source["fingerprint"]}):
        raise ValueError("Workspace selection fingerprint changed after validation")
    if source:
        request = request.model_copy(update={"selection_fingerprint":source["fingerprint"]})
    selection = freeze_selection(request,definition,source=source)
    if source and {part["id"] for item in selection["series"].values() for part in item["partitions"]}!=set(request.partitions):
        raise ValueError("The selection contains an unrelated partition")
    # Keep the end pinned to the requested interval, including for reviewed catalogs.
    selection = {**selection,"end":request.cutoff,
                 "series":{key:{**feed,"end":request.cutoff} for key,feed in selection["series"].items()}}
    for feed in selection["series"].values():
        if request.cutoff% (FRAME_MIN[feed["timeframe"]]*60):
            raise ValueError("Historical end must align to each required timeframe")
    graph = RuleGraph(definition)
    if request.rearm=="NEW_CONTEXT_FAMILY":
        if not request.context_alias:
            raise ValueError("Rearm requires an explicit mandatory positive context-family alias")
        rule = mandatory_alias(graph.groups["context"],request.context_alias)
        from .strategy_catalog import FAMILY_CATEGORIES, OBSERVATIONS, ROLE_OBSERVATIONS
        if not rule or (OBSERVATIONS|ROLE_OBSERVATIONS)[rule.observation]["category"] not in FAMILY_CATEGORIES:
            raise ValueError("Rearm requires a mandatory positive context-family alias in ALL groups")
        only_fvg = len(definition.indicators)==1 and definition.indicators[0]
        if (not only_fvg or (only_fvg.indicator,only_fvg.role,only_fvg.timeframe)!=("FVG","PRIMARY","M5")
                or definition.instruments.primary.symbol!="EURUSD" or definition.trade_intent.role!="PRIMARY"
                or set(selection["series"])!={"PRIMARY:M5"} or rule.dependency!=only_fvg.id
                or rule.observation not in {"FVG_STATE","FVG_ACTIVE"} or rule.expected!="ACTIVE"):
            raise ValueError("Successor publication supports one primary EURUSD M5 FVG producer with an ACTIVE context alias. "
                             "Use SINGLE_ATTEMPT for CRT, SMT and other producer combinations.")
        # Family identity follows the pinned feed rather than the archive file
        # count. Exact contributing partition digests remain in each proof.
        selection["series"]["PRIMARY:M5"]["dataset_id"] = None
    else:
        rule = None
        if request.context_alias is not None or request.max_attempts!=1:
            raise ValueError("Single-attempt runs have one attempt and no rearm alias")
    producers = {dep.id:{"kind":dep.indicator,"algorithm_version":dep.algorithm_version} for dep in definition.indicators}
    method = "REFERENCE_SINGLE" if request.rearm=="SINGLE_ATTEMPT" else "FVG_ASOF_REFERENCE"
    frozen_definition = definition.model_dump(mode="json")
    identity = {**request.model_dump(mode="json"),"frozen_selection":fingerprint(selection),
                "frozen_definition":fingerprint(frozen_definition),"rearm_rule":rule.id if rule else None,
                "binding_policy_version":2,"strategy_schema":2,"availability":SIMULATION_MODE,
                "producer_versions":producers,"method":method,"checkpoint_version":CHECKPOINT_VERSION,
                "budgets":{"oracle_cutoffs":MAX_ORACLE_CUTOFFS,"hard_seconds":HARD_SECONDS,
                           "memory_bytes":MEMORY_BYTES,"checkpoint_bytes":MAX_CHECKPOINT_BYTES,
                           "ledger_bytes":MAX_LEDGER_BYTES}}
    run_id = "HR-"+fingerprint(identity)
    return {"run_id":run_id,"identity":identity,"selection":selection,
            "definition":frozen_definition,"rearm_rule":identity["rearm_rule"]}


def verified_evidence(definition, run, check_budget=None):
    identity = run["identity"]
    # At archive end a modeled delay can place a final close beyond the
    # decision horizon. Check that coverage through the last modeled arrival
    # is complete, then collect the actual decision horizon separately.
    evidence = collect(definition,run["selection"],identity["cutoff"],identity["evaluation_start"],
                       check_budget=check_budget)
    for feed in run["selection"]["series"].values():
        if feed["delay_seconds"]:
            complete_cutoff = identity["cutoff"]+max(item["delay_seconds"] for item in run["selection"]["series"].values())
            complete = collect(definition,run["selection"],complete_cutoff,identity["evaluation_start"],
                               check_budget=check_budget)
            break
    else:
        complete = evidence
    if any(not lane.due or lane.at(lane.due[-1])["status"]!="COMPLETE" for lane in complete.coverage.values()):
        raise ValueError("Historical window has missing required candles")
    return evidence
