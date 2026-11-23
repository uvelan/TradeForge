"""Local historical research API. Outputs end at entry-intent qualification."""
import json

from fastapi import APIRouter, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool

from .analysis_evidence import ResourceLimit
from .db import one, rows, transaction
from .historical_strategy_engine import successor_schedule, unresolved_reasons
from .historical_strategy_inputs import CRT_SMT_METHOD, RUN_VERSION, HistoricalInput, prepare_historical, verified_evidence, approved_partition
from .historical_strategy_store import cancel, enqueue, latest_checkpoint, resume
from .strategy_definitions import StrategyDefinitionV2
from .strategy_evaluation_budget import deadline
from .strategy_evaluation_routes import archived_evidence_chart, evaluation_lane
from .strategy_evaluation_sources import EVALUATOR_VERSION, selection_catalog
from .strategy_routes import envelope

router = APIRouter(prefix="/api/historical-strategy-runs/v1")


@router.get("/selections")
def selections():
    return {"approved":selection_catalog(),"purpose":"HISTORICAL_SIMULATION","recorded_readiness":"BLOCKED"}


@router.get("/partitions")
def partitions(feed_id:str=Query(min_length=1,max_length=128),symbol:str=Query(min_length=1,max_length=32),
               before:str=Query(default="9999",max_length=40)):
    candidates = rows("""SELECT * FROM datasets WHERE kind='candles' AND timeframe IN ('M1','M5')
                         AND feed_id=? AND symbol=? AND created_at<? ORDER BY created_at DESC,id LIMIT 48""",
                       (feed_id,symbol,before))
    return [{key:item[key] for key in ("id","provider","symbol","feed_id","timeframe","price_basis",
                                      "first_time","last_time","sha256","created_at")}
            for item in candidates if approved_partition(item)]


def checked(body, persist):
    with evaluation_lane():
        run = prepare_historical(body)
        identity = run["identity"]
        if persist and identity["method"]!=CRT_SMT_METHOD:
            return {"run_id":enqueue(run)}
        definition = StrategyDefinitionV2.model_validate(run["definition"])
        if identity["method"]==CRT_SMT_METHOD:
            try:
                evidence = verified_evidence(definition,run,deadline())
                successor_schedule(run,evidence,definition)
            except (ResourceLimit, ValueError) as error:
                raise HTTPException(422,str(error)) from error
            if persist:
                return {"run_id":enqueue(run)}
        else:
            evidence = verified_evidence(definition,run,deadline())
        return {"run_id":run["run_id"],"identity":identity,"selection":run["selection"],
                "coverage":{key:lane.at(identity["cutoff"]) for key,lane in evidence.coverage.items()}}


@router.post("/validate")
async def validate_run(request:Request):
    return await run_in_threadpool(checked,await envelope(request,HistoricalInput),False)


@router.post("",status_code=202)
async def create_run(request:Request):
    return await run_in_threadpool(checked,await envelope(request,HistoricalInput),True)


@router.get("")
def list_runs(strategy_version_id:str=Query(max_length=40),limit:int=Query(25,ge=1,le=50)):
    return rows("""SELECT r.id AS run_id,r.created_at,s.state,s.frontier FROM historical_strategy_runs r
                   JOIN historical_worker_state s ON s.run_id=r.id WHERE r.strategy_version_id=?
                   ORDER BY r.created_at DESC,r.id LIMIT ?""",(strategy_version_id,limit))


def run_state(run_id):
    with transaction() as db:
        row = db.execute("SELECT state FROM historical_worker_state WHERE run_id=?",(run_id,)).fetchone()
    if not row:
        raise HTTPException(404,"Historical run not found")
    return row["state"]


def require_published(run_id):
    if run_state(run_id)!="COMPLETED":
        raise HTTPException(409,"Historical results are available after the run completes")


@router.get("/{run_id}")
def get_run(run_id:str):
    with transaction() as db:
        db.execute("BEGIN")
        row = db.execute("SELECT payload,created_at FROM historical_strategy_runs WHERE id=?",(run_id,)).fetchone()
        if not row:
            raise HTTPException(404,"Historical run not found")
        result = json.loads(row["payload"])
        worker = dict(db.execute("""SELECT state,frontier,diagnostic,metrics FROM historical_worker_state
                                    WHERE run_id=?""",(run_id,)).fetchone())
        completed = worker["state"]=="COMPLETED"
        worker["metrics"] = json.loads(worker["metrics"]) if completed else {}
        counts = db.execute("""SELECT state,COUNT(*) AS count FROM historical_strategy_attempts
                               WHERE run_id=? GROUP BY state""",(run_id,)).fetchall() if completed else ()
        intent_count = db.execute("SELECT COUNT(*) FROM historical_strategy_intents WHERE run_id=?",
                                  (run_id,)).fetchone()[0] if completed else 0
        accounting = {"status":"NOT_PUBLISHED" if not completed else "NOT_COLLECTED"}
        if completed and result.get("opportunity_accounting_version"):
            final = db.execute("""SELECT payload FROM historical_strategy_checkpoints
                                  WHERE run_id=? ORDER BY frontier DESC LIMIT 1""",(run_id,)).fetchone()
            if final and (progress:=json.loads(final["payload"]).get("opportunity_accounting")) is not None:
                breakdown = db.execute("""SELECT disposition,reason,COUNT(*) AS total
                    FROM historical_current_opportunities WHERE run_id=? GROUP BY disposition,reason
                    ORDER BY disposition,reason""",(run_id,)).fetchall()
                dispositions = {key:0 for key in ("BOUND","SUPPRESSED","UNRESOLVED")}
                for item in breakdown:
                    dispositions[item["disposition"]] += item["total"]
                accounting = {"status":"COLLECTED","version":progress["version"],
                              "total":sum(dispositions.values()),"by_disposition":dispositions,
                              "by_reason":[dict(item) for item in breakdown],
                              "unresolved_inspections":progress["unresolved_inspections"],
                              "unresolved_by_reason":unresolved_reasons(progress)}
        return {key:result[key] for key in ("run_id","identity","selection")} | {
            "created_at":row["created_at"],"worker":worker,"results_published":completed,
            "attempt_counts":{entry["state"]:entry["count"] for entry in counts},
            "qualified_intents":intent_count,"opportunity_accounting":accounting}


@router.post("/{run_id}/cancel")
def cancel_run(run_id:str):
    get_run(run_id)
    if not cancel(run_id):
        raise HTTPException(409,"Historical run is not cancellable")
    return get_run(run_id)


@router.post("/{run_id}/resume")
def resume_run(run_id:str):
    run = get_run(run_id)
    if (run["identity"]["historical_version"],run["identity"]["evaluator_version"])!=(RUN_VERSION,EVALUATOR_VERSION):
        raise HTTPException(409,"Saved run uses an older evaluator. Create a new historical run.")
    with evaluation_lane():
        if not resume(run_id):
            raise HTTPException(409,"Historical run is not resumable")
    return get_run(run_id)


@router.get("/{run_id}/checkpoint")
def checkpoint(run_id:str):
    require_published(run_id)
    record = latest_checkpoint(run_id)
    if not record:
        raise HTTPException(404,"No committed checkpoint")
    return {"frontier":record["frontier"],"digest":record["digest"],"state":json.loads(record["payload"])}


@router.get("/{run_id}/decisions")
def decisions(run_id:str,after:int=Query(0,ge=0),limit:int=Query(30,ge=1,le=100)):
    if run_state(run_id)!="COMPLETED":
        return []
    return rows("""SELECT id,cutoff,attempt_id,state FROM historical_strategy_decisions
                   WHERE run_id=? AND cutoff>? ORDER BY cutoff LIMIT ?""",(run_id,after,limit))


@router.get("/{run_id}/attempts")
def attempts(run_id:str,after:int=Query(0,ge=0),limit:int=Query(30,ge=1,le=100)):
    if run_state(run_id)!="COMPLETED":
        return []
    return rows("""SELECT a.id,a.state,d.cutoff,d.id AS decision_id FROM historical_strategy_attempts a
                   JOIN historical_strategy_decisions d ON d.id=a.decision_id
                   WHERE a.run_id=? AND d.cutoff>? ORDER BY d.cutoff LIMIT ?""",(run_id,after,limit))


@router.get("/{run_id}/opportunities")
def opportunities(run_id:str,after:int=Query(0,ge=0),limit:int=Query(30,ge=1,le=100)):
    if run_state(run_id)!="COMPLETED":
        return []
    final = latest_checkpoint(run_id)
    if not final or json.loads(final["payload"]).get("opportunity_accounting") is None:
        return []
    result = []
    for row in rows("""SELECT payload,first_payload FROM historical_current_opportunities
                       WHERE run_id=? AND ordinal>? ORDER BY ordinal LIMIT ?""",(run_id,after,limit)):
        final,first = json.loads(row["payload"]),json.loads(row["first_payload"])
        result.append({**final,"first":{key:first[key] for key in
            ("cutoff","disposition","reason","evidence_reference")}})
    return result


@router.get("/{run_id}/intents")
def intents(run_id:str,after:int=Query(0,ge=0),limit:int=Query(30,ge=1,le=100)):
    if run_state(run_id)!="COMPLETED":
        return []
    return [json.loads(row["payload"]) for row in rows("""SELECT i.payload FROM historical_strategy_intents i
             JOIN historical_strategy_decisions d ON d.id=i.decision_id WHERE i.run_id=? AND d.cutoff>?
             ORDER BY d.cutoff LIMIT ?""",(run_id,after,limit))]


@router.get("/{run_id}/decisions/{cutoff}")
def decision(run_id:str,cutoff:int):
    require_published(run_id)
    row = one("SELECT * FROM historical_strategy_decisions WHERE run_id=? AND cutoff=?",(run_id,cutoff))
    if not row:
        raise HTTPException(404,"No saved decision at this cutoff")
    return {"decision_id":row["id"],**json.loads(row["payload"])}


@router.get("/{run_id}/decisions/{cutoff}/evidence/{snapshot}/{event_id}")
def evidence(run_id:str,cutoff:int,snapshot:str,event_id:str):
    event = decision(run_id,cutoff)["supporting_events"].get(f"{snapshot}:{event_id}")
    if not event:
        raise HTTPException(404,"Exact evidence not present in this decision")
    return event


@router.get("/{run_id}/decisions/{cutoff}/evidence/{snapshot}/{event_id}/chart")
def chart(run_id:str,cutoff:int,snapshot:str,event_id:str):
    return archived_evidence_chart(evidence(run_id,cutoff,snapshot,event_id),get_run(run_id)["selection"]["series"],cutoff)
