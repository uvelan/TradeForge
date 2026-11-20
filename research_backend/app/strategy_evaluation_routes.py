"""Local strategy evaluation API and immutable replay persistence."""
from contextlib import contextmanager
import json
from threading import BoundedSemaphore

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import Field, StrictInt
from starlette.concurrency import run_in_threadpool

from .analysis_evidence import ResourceLimit, canonical
from .arrival_policy import SIMULATION_MODE
from .db import one, rows, transaction, utc_now
from .indicator_history import load_reference
from .market import FRAME_MIN
from .smt_causal import ArchiveRequest
from .strategy_catalog import BINDING_POLICY_VERSION
from .strategy_definitions import Contract, Digest, StrategyDefinitionV2, validate_definition
from .strategy_evaluation_sources import EVALUATOR_VERSION, EvaluationInput, collect, freeze_selection, selection_catalog
from .strategy_evaluator import EvaluationMachine
from .strategy_evaluation_budget import deadline
from .strategy_evidence import fingerprint
from .strategy_routes import current, envelope

router = APIRouter(prefix="/api/strategy-evaluations/v1")
MAX_DECISIONS = 256
MAX_RUNS = 512
MAX_SERIALIZED_DECISION = 256 * 1024
LANES = BoundedSemaphore(2)


class Cutoff(Contract):
    cutoff: StrictInt = Field(ge=0,le=32503680000)


class Advance(Cutoff):
    expected_decision_id: Digest


@contextmanager
def evaluation_lane():
    if not LANES.acquire(blocking=False):
        raise HTTPException(429,"Concurrent strategy evaluation budget exceeded")
    try:
        yield
    except ResourceLimit as error:
        raise HTTPException(422,{"code":"RESOURCE_LIMIT","reason":str(error)}) from error
    except ValueError as error:
        raise HTTPException(422,{"code":"EVALUATION_UNAVAILABLE","reason":str(error)}) from error
    finally:
        LANES.release()


def load_definition(request):
    with transaction() as db:
        latest = current(db,request.strategy_id)
        stored = db.execute("SELECT * FROM strategy_versions WHERE strategy_id=? AND version_id=?",
                            (request.strategy_id,request.strategy_version_id)).fetchone()
    if stored is None:
        raise HTTPException(404,"Immutable strategy version not found")
    if latest["status"]=="ARCHIVED" or stored["status"]!="READY_FOR_EVALUATION":
        raise ValueError("A ready version of a non-archived strategy is required")
    if stored["configuration_fingerprint"] != request.configuration_fingerprint:
        raise ValueError("Immutable strategy fingerprint mismatch")
    payload = json.loads(stored["definition"])
    checked = validate_definition(payload)
    if (not checked["valid"] or payload["schema_version"]!=2 or
            checked["configuration_fingerprint"]!=request.configuration_fingerprint or
            checked["restrictions"]["binding_policy_version"]!=BINDING_POLICY_VERSION):
        raise ValueError("Schema 2 binding policy 2 revalidation failed")
    definition = StrategyDefinitionV2.model_validate(checked["definition"])
    if definition.availability_modes!=[SIMULATION_MODE]:
        raise ValueError("Recorded purpose is blocked")
    return definition


def prepare(request):
    definition = load_definition(request)
    selection = freeze_selection(request,definition)
    identity = {**request.model_dump(mode="json",exclude={"cutoff"}),
                "binding_policy_version":BINDING_POLICY_VERSION,
                "frozen_selection_fingerprint":fingerprint(selection)}
    return {"run_id":"EV-"+fingerprint(identity), "identity":identity,"selection":selection,
            "definition":definition.model_dump(mode="json")}


def calculate(run, cutoff):
    identity = run["identity"]
    if identity["evaluator_version"]!=EVALUATOR_VERSION:
        raise HTTPException(409,{"code":"LEGACY_EVALUATION_READ_ONLY","reason":"Saved v1 through v3 decisions remain readable. Create a v4 evaluation to recalculate."})
    if not identity["evaluation_start"] <= cutoff <= run["selection"]["end"]:
        raise ValueError("Cutoff lies outside the frozen evaluation range")
    definition = StrategyDefinitionV2.model_validate(run["definition"])
    check = deadline()
    observed = collect(definition,run["selection"],cutoff,identity["evaluation_start"],check_budget=check)
    decision = EvaluationMachine(definition,observed,identity,identity["evaluation_start"],
                                 run["selection"]["end"],check_budget=check).run(cutoff)
    check()
    decision["evaluation"] = {**identity,"evaluation_id":run["run_id"],"cutoff_utc":cutoff}
    decision["decision_id"] = fingerprint({key:value for key,value in decision.items() if key!="resource_counts"})
    if len(canonical(decision).encode())>MAX_SERIALIZED_DECISION:
        raise ResourceLimit("Decision exceeds the serialized audit budget")
    return decision


def load_run(run_id):
    row = one("SELECT payload,created_at FROM strategy_evaluations WHERE id=?",(run_id,))
    if row is None:
        raise HTTPException(404,"Strategy evaluation not found")
    return {**json.loads(row["payload"]),"created_at":row["created_at"]}


def decode_decision(row):
    return {**json.loads(row["payload"]),"previous_decision_id":row["previous_id"]}


def find_decision(run_id, cutoff=None):
    query = "SELECT * FROM strategy_decisions WHERE run_id=?"
    row = one(query+" ORDER BY cutoff DESC LIMIT 1",(run_id,)) if cutoff is None else one(
        query+" AND cutoff=?",(run_id,cutoff))
    return decode_decision(row) if row else None


def store_decision(db, run_id, decision, previous):
    db.execute("INSERT INTO strategy_decisions VALUES(?,?,?,?,?,?)",
               (run_id,decision["cutoff"],decision["decision_id"],previous,canonical(decision),utc_now()))
    return {**decision,"previous_decision_id":previous}


def existing_creation(run, cutoff, latest, lookup):
    exact = lookup() if latest["cutoff"] != cutoff else latest
    if exact is None:
        raise HTTPException(409,{"code":"EXACT_CUTOFF_RECONSTRUCTION_REQUIRED",
                                 "reason":"This run already exists. Reconstruct the requested cutoff or advance the latest checkpoint.",
                                 "run_id":run["run_id"],"requested_cutoff":cutoff,
                                 "latest_cutoff":latest["cutoff"]})
    return {"run_id":run["run_id"],"selection":run["selection"],"decision":exact}


def create_run(request):
    with evaluation_lane():
        run = prepare(request)
        run_id = run["run_id"]
        existing = find_decision(run_id)
        if existing:
            return existing_creation(run,request.cutoff,existing,lambda:find_decision(run_id,request.cutoff))
        decision = calculate(run,request.cutoff)
        with transaction() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT * FROM strategy_decisions WHERE run_id=? ORDER BY cutoff DESC LIMIT 1",
                                  (run_id,)).fetchone()
            if existing is not None:
                def exact():
                    row = db.execute("SELECT * FROM strategy_decisions WHERE run_id=? AND cutoff=?",
                                     (run_id,request.cutoff)).fetchone()
                    return decode_decision(row) if row else None
                return existing_creation(run,request.cutoff,decode_decision(existing),exact)
            if current(db,request.strategy_id)["status"]=="ARCHIVED":
                raise HTTPException(409,"Strategy archived while evaluation was running")
            if db.execute("SELECT COUNT(*) FROM strategy_evaluations").fetchone()[0]>=MAX_RUNS:
                raise ResourceLimit("Saved evaluation run budget exceeded")
            db.execute("INSERT INTO strategy_evaluations VALUES(?,?,?,?)",
                       (run_id,request.strategy_version_id,canonical(run),utc_now()))
            result = store_decision(db,run_id,decision,None)
        return {"run_id":run_id,"selection":run["selection"],"decision":result}


def advance_run(run_id, request):
    run = load_run(run_id)
    if run["identity"]["evaluator_version"]!=EVALUATOR_VERSION:
        raise HTTPException(409,{"code":"LEGACY_EVALUATION_READ_ONLY","reason":"Create a v4 evaluation to advance."})
    latest = find_decision(run_id)
    known = find_decision(run_id,request.cutoff)
    if known and request.expected_decision_id in {known["previous_decision_id"],known["decision_id"]}:
        return known
    if (request.expected_decision_id!=latest["decision_id"] or request.cutoff<=latest["cutoff"]):
        raise HTTPException(409,"Stale evaluation checkpoint")
    with evaluation_lane():
        decision = calculate(run,request.cutoff)
        with transaction() as db:
            db.execute("BEGIN IMMEDIATE")
            latest = db.execute("SELECT * FROM strategy_decisions WHERE run_id=? ORDER BY cutoff DESC LIMIT 1",
                                (run_id,)).fetchone()
            if latest["id"]!=request.expected_decision_id:
                if latest["cutoff"]==request.cutoff and latest["previous_id"]==request.expected_decision_id:
                    return decode_decision(latest)
                raise HTTPException(409,"Stale evaluation checkpoint")
            if db.execute("SELECT COUNT(*) FROM strategy_decisions WHERE run_id=?",(run_id,)).fetchone()[0]>=MAX_DECISIONS:
                raise ResourceLimit("Saved decisions for this run exceeded the history budget")
            return store_decision(db,run_id,decision,latest["id"])


@router.get("/selections")
def selections():
    return {"evaluator_version":EVALUATOR_VERSION,"purpose":SIMULATION_MODE,"recorded_readiness":"BLOCKED","selections":selection_catalog()}


def validation(request):
    with evaluation_lane():
        run = prepare(request)
        data = collect(StrategyDefinitionV2.model_validate(run["definition"]),run["selection"],request.cutoff,
                       run["identity"]["evaluation_start"],check_budget=deadline())
        coverage = [lane.at(request.cutoff) for lane in data.coverage.values()]
        complete = all(lane["status"]=="COMPLETE" for lane in coverage)
        return {"valid":True,"status":"VERIFIED_FROZEN_HISTORICAL_SELECTION" if complete else "INSUFFICIENT_COVERAGE",
                "run_id":run["run_id"],"selection":run["selection"],"coverage":coverage}


@router.post("/validate")
async def validate_evaluation(request:Request):
    return await run_in_threadpool(validation,await envelope(request,EvaluationInput))


@router.post("",status_code=201)
async def start_evaluation(request:Request):
    return await run_in_threadpool(create_run,await envelope(request,EvaluationInput))


@router.get("/runs")
def evaluation_runs(strategy_version_id: str, limit:int=Query(30,ge=1,le=50)):
    return rows("""SELECT r.id AS run_id,d.cutoff,json_extract(d.payload,'$.state') AS state
                   FROM strategy_evaluations r JOIN strategy_decisions d ON d.run_id=r.id
                   WHERE r.strategy_version_id=? AND d.cutoff=(
                       SELECT MAX(cutoff) FROM strategy_decisions WHERE run_id=r.id)
                   ORDER BY r.created_at DESC,r.id LIMIT ?""",(strategy_version_id,limit))


@router.get("/{run_id}")
def get_evaluation(run_id:str):
    run = load_run(run_id)
    return {key:value for key,value in run.items() if key!="definition"} | {"decision":find_decision(run_id)}


@router.post("/{run_id}/advance")
async def advance(run_id:str, request:Request):
    return await run_in_threadpool(advance_run,run_id,await envelope(request,Advance))


def reconstruct_run(run_id, cutoff):
    run = load_run(run_id)
    with evaluation_lane():
        return {**calculate(run,cutoff),"persisted":False}


@router.post("/{run_id}/reconstruct")
async def reconstruct(run_id:str, request:Request):
    args = await envelope(request,Cutoff)
    return await run_in_threadpool(reconstruct_run,run_id,args.cutoff)


@router.get("/{run_id}/decisions")
def decisions(run_id:str, before:int=Query(32503680001,ge=1), limit:int=Query(40,ge=1,le=100)):
    load_run(run_id)
    return rows("""SELECT cutoff,id AS decision_id,previous_id AS previous_decision_id,created_at,
                   json_extract(payload,'$.state') AS state,json_extract(payload,'$.attempt_id') AS attempt_id
                   FROM strategy_decisions WHERE run_id=? AND cutoff<? ORDER BY cutoff DESC LIMIT ?""",
                (run_id,before,limit))


@router.get("/{run_id}/decisions/{cutoff}")
def get_decision(run_id:str,cutoff:int):
    decision = find_decision(run_id,cutoff)
    if not decision:
        raise HTTPException(404,"Saved strategy decision not found")
    return decision


@router.get("/{run_id}/attempts/{attempt_id}")
def get_attempt(run_id:str,attempt_id:str):
    found = one("""SELECT * FROM strategy_decisions WHERE run_id=? AND
                   json_extract(payload,'$.attempt_id')=? ORDER BY cutoff DESC LIMIT 1""",(run_id,attempt_id))
    if not found:
        raise HTTPException(404,"Strategy attempt not found")
    return decode_decision(found)


@router.get("/{run_id}/decisions/{cutoff}/evidence/{snapshot}/{event_id}")
def get_evidence(run_id:str,cutoff:int,snapshot:str,event_id:str):
    decision = get_decision(run_id,cutoff)
    event = decision["supporting_events"].get(f"{snapshot}:{event_id}")
    if not event:
        raise HTTPException(404,"Exact evidence is not part of this decision")
    return event


@router.get("/{run_id}/decisions/{cutoff}/evidence/{snapshot}/{event_id}/chart")
def evidence_chart(run_id:str,cutoff:int,snapshot:str,event_id:str):
    event = get_evidence(run_id,cutoff,snapshot,event_id)
    return archived_evidence_chart(event,load_run(run_id)["selection"]["series"],cutoff)


def archived_evidence_chart(event,pinned,cutoff):
    ref = event["evidence_reference"]
    identity = ref.get("participants",{}).get("primary",ref["source"])
    provider,symbol,frame,feed_id,basis,dataset_id = identity
    selected = next((item for item in pinned.values() if
                     (item["source"],item["symbol"],item["timeframe"],item["feed_id"],item["price_basis"])==
                     (provider,symbol,frame,feed_id,basis)),None)
    if selected is None:
        raise HTTPException(404,"The frozen chart source is unavailable for this evidence")
    step = FRAME_MIN[frame]*60
    anchor = event["timing"]["observation_start"]//step*step
    start = max(selected["start"],anchor-15*step)
    end = min(selected["end"],cutoff//step*step,anchor+17*step)
    if end<=start:
        raise HTTPException(422,"No chart history at this evidence cutoff")
    request = ArchiveRequest(**selected,cutoff=cutoff,availability_mode=SIMULATION_MODE)
    source = load_reference(request,start,end,step)
    if source["evidence"].get("status"):
        raise HTTPException(422,source["evidence"].get("reason","Frozen chart source unavailable"))
    return {"source":provider,"symbol":symbol,"timeframe":frame,"feed_id":feed_id,
            "dataset_id":dataset_id,"price_basis":basis,"synthetic":False,"evidence_time":anchor,
            "partitions":source["evidence"]["context_partitions"],
            "candles":[bar.model_dump(mode="json") for bar,_ in source["rows"]]}
