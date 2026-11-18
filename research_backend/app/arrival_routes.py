"""Local controls for capture, frozen manifests, and availability readiness."""
import json
from time import time

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .analysis_evidence import ArchiveSession, ResourceLimit
from .arrival_capture import CaptureConfig, aggregate_m1, seal
from .arrival_policy import RECORDED_AUTHORITY, RECORDED_MODE, SIMULATION_AUTHORITY, SIMULATION_MODE
from .db import one, rows
from .indicator_coverage import inspect_window, open_intervals
from .indicator_history import load_reference
from .market import FRAME_MIN
from .smt_causal import ArchiveRequest, FrozenFeed

router = APIRouter(prefix="/api/market/arrival")


class SealRequest(BaseModel, extra="forbid"):
    symbol: str = Field(min_length=1,max_length=32)
    feed_id: str = Field(pattern=r"^capture-[a-f0-9]{40}$")
    timeframe: str = Field(pattern=r"^M[15]$")
    price_basis: str = Field(pattern=r"^(BID|LAST_TRADE)$")
    start: int = Field(ge=0,le=32_503_680_000)
    end: int = Field(gt=0,le=32_503_680_000)
    cutoff: int | None = Field(default=None,ge=0,le=32_503_680_000)


class ReadinessRequest(BaseModel, extra="forbid"):
    primary: FrozenFeed
    comparison: FrozenFeed | None = None
    cutoff: int = Field(ge=0,le=32_503_680_000)


@router.get("/capture")
def capture_status(request: Request):
    return request.app.state.arrival_capture.status()


@router.post("/capture/start")
async def capture_start(config: CaptureConfig, request: Request):
    try:
        return await request.app.state.arrival_capture.start(config)
    except ValueError as error:
        raise HTTPException(409,str(error)) from error


@router.post("/capture/stop")
async def capture_stop(request: Request):
    return await request.app.state.arrival_capture.stop()


@router.post("/manifests")
def freeze_capture(body: SealRequest, request: Request):
    from .indicator_coverage import MarketSchedule

    status = request.app.state.arrival_capture.status()
    current = status.get("scopes",{}).get(body.symbol,{})
    cutoff = body.cutoff if body.cutoff is not None else int(time())
    if cutoff > int(time()) or body.end > cutoff:
        raise HTTPException(422,"Freeze only completed intervals at a known UTC cutoff")
    scope=body.model_dump(include={"symbol","feed_id","timeframe","price_basis"})
    session = one("""SELECT s.calendar FROM arrival_sessions s JOIN arrival_observations o ON o.session_id=s.id
        WHERE o.feed_id=? AND o.symbol=? AND o.timeframe=? AND o.price_basis=? ORDER BY o.sequence DESC LIMIT 1""",
                  (body.feed_id,body.symbol,body.timeframe,body.price_basis))
    calendar = status["calendar"] if current.get("feed_id")==body.feed_id else (
        json.loads(session["calendar"]) if session else None)
    if calendar is None:
        raise HTTPException(409,"No recorded capture session for this scope")
    try:
        return seal({"provider":"MT5",**scope},body.start,body.end,cutoff,MarketSchedule.model_validate(calendar))
    except (RuntimeError, ValueError, OSError) as error:
        raise HTTPException(409,str(error)) from error


@router.get("/manifests")
def manifests(limit: int = Query(30,ge=1,le=100)):
    return [{"id":item["id"],**json.loads(item["payload"])}
            for item in rows("SELECT id,payload FROM arrival_manifests ORDER BY rowid DESC LIMIT ?",(limit,))]


@router.get("/manifests/{manifest_id}")
def manifest_detail(manifest_id: str):
    item = one("SELECT payload FROM arrival_manifests WHERE id=?",(manifest_id,))
    if not item:
        raise HTTPException(404,"Frozen arrival manifest unavailable")
    return {"id":manifest_id,**json.loads(item["payload"])}


@router.post("/manifests/{manifest_id}/aggregate-m5")
def aggregate_manifest(manifest_id: str):
    try:
        return aggregate_m1(manifest_detail(manifest_id))
    except (RuntimeError, ValueError, OSError) as error:
        raise HTTPException(409,str(error)) from error


def assess(feed, cutoff, archive=None):
    step = FRAME_MIN[feed.timeframe]*60
    paths = {}
    ready = []
    for mode, authority in ((RECORDED_MODE,RECORDED_AUTHORITY),(SIMULATION_MODE,SIMULATION_AUTHORITY)):
        if mode == SIMULATION_MODE and feed.arrival_manifest:
            continue
        query = ArchiveRequest(**feed.model_dump(),cutoff=cutoff,availability_mode=mode)
        history = load_reference(query,feed.start,feed.end,step,archive_session=archive)
        data = history["evidence"]
        admitted = [(bar,at) for bar,at in history["rows"]
                    if bar.available_at==at and data["context_authority"]==authority]
        observed = [(bar,at) for bar,at in history["rows"]]
        coverage = inspect_window(feed.start,feed.end,step,admitted,feed.calendar,cutoff)
        complete = coverage["status"] == "COMPLETE" and not data.get("status")
        if complete:
            ready.append(mode)
        quality = {}
        if data.get("arrival_manifest"):
            sealed = manifest_detail(data["arrival_manifest"])
            quality = sealed["data_quality"]
        delays = sorted(at-bar.time-step for bar,at in admitted)
        paths[mode] = {
            "authority":data["context_authority"], "status":data.get("status",coverage["status"]),
            "blocking_reason":data.get("reason") or (None if complete else
                "UNVERIFIED_AVAILABILITY" if observed and not admitted else coverage["status"]),
            "total_candles":len(observed), "eligible_candles":len(admitted), "unverified_candles":len(observed)-len(admitted),
            "first_eligible":min((bar.time for bar,_ in admitted),default=None),
            "last_eligible":max((bar.time for bar,_ in admitted),default=None),
            "coverage":coverage,"coverage_intervals":open_intervals(feed.start,feed.end,feed.calendar),
            "missing_slots":None if coverage["expected_candles"] is None else coverage["expected_candles"]-len(admitted),
            "arrival_delay_seconds":{key:delays[int((len(delays)-1)*percentile)]
                                     for key,percentile in (("min",0),("p50",.5),("p95",.95),("max",1))} if delays else None,
            "duplicate_observation_detected":data.get("reason")=="DUPLICATE_ARCHIVE_CANDLE",
            "source_corrections":data.get("source_corrections",0),
            "recovery_observations":data.get("recovery_observations",0),
            "clock_warnings":[data["reason"]] if data.get("reason")=="INVALID_CAPTURE_ATTESTATION" else [],
            "frozen_quality":quality,
        }
    state = ("READY_RECORDED" if RECORDED_MODE in ready else "READY_SIMULATION" if SIMULATION_MODE in ready
             else "SOURCE_INTEGRITY_FAILURE" if any(path["status"]=="SOURCE_INTEGRITY_FAILURE" for path in paths.values())
             else "INSUFFICIENT_COVERAGE" if any(path["eligible_candles"] for path in paths.values())
             else "UNVERIFIED_AVAILABILITY")
    return {"symbol":feed.symbol,"status":state,"supported_modes":["LEGACY_RESEARCH",*ready],"modes":paths}


@router.post("/readiness")
def readiness(body: ReadinessRequest):
    try:
        return assess_request(body,ArchiveSession())
    except ResourceLimit as error:
        raise HTTPException(422,str(error)) from error


def assess_request(body, archive):
    primary = assess(body.primary,body.cutoff,archive)
    if not body.comparison:
        return {"primary":primary}
    comparison = assess(body.comparison,body.cutoff,archive)
    a,b = body.primary,body.comparison
    start,end = max(a.start,b.start),min(a.end,b.end)
    compatible = (a.symbol.casefold()!=b.symbol.casefold() and start<end and
                  (a.source,a.price_basis,a.timeframe,a.calendar)==(b.source,b.price_basis,b.timeframe,b.calendar))
    joint = []
    if compatible:
        left = primary if (start,end)==(a.start,a.end) else assess(a.model_copy(update={"start":start,"end":end}),body.cutoff,archive)
        right = comparison if (start,end)==(b.start,b.end) else assess(b.model_copy(update={"start":start,"end":end}),body.cutoff,archive)
        joint = [mode for mode in left["supported_modes"] if mode in right["supported_modes"]]
    return {"primary":primary,"comparison":comparison,
            "joint_modes":joint,"joint_window":[start,end] if compatible else None,
            "joint_blocking_reason":None if compatible else "INCOMPATIBLE_JOINT_SCOPE"}
