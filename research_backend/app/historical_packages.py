"""Cutoff-scoped review packages for the recorded monthly BID-rise replay."""
from datetime import UTC, datetime
from decimal import Decimal
import hashlib
import io
import json
from typing import Literal
import zipfile

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from . import domain, month_replay
from .db import one, rows, transaction
from .strategy_evidence import fingerprint
from .schemas import CorrectableReview

router = APIRouter(prefix="/api")
TYPE = "RECORDED_REPLAY_REVIEW_V1"
UNIT = "USD per EUR"


class MetricClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metric_id: str = Field(min_length=1,max_length=128)
    value: str = Field(pattern=r"^-?[0-9]+(?:\.[0-9]+)?$",max_length=64)
    unit: str = Field(min_length=1,max_length=80)


class HistoricalReview(CorrectableReview):
    model_config = ConfigDict(extra="forbid")
    review_type: Literal["RECORDED_REPLAY_REVIEW_V1"]
    package_id: str = Field(pattern=r"^PKG-[A-Za-z0-9-]+$",max_length=120)
    session: str = Field(min_length=1,max_length=128)
    assessment: Literal["SUPPORTED","CONDITIONAL","REJECTED","INSUFFICIENT_DATA"]
    comment: str = Field(max_length=4000)
    claims: list[MetricClaim] = Field(max_length=100)


def snapshot(db, month, session, cutoff):
    binding = month_replay.load_manifest(db,month,session)
    if not binding:
        raise HTTPException(404,"Run the recorded replay before exporting its evidence")
    if binding["policy"] != month_replay.POLICY:
        raise HTTPException(422,"Historical package policy does not cover this replay version")
    if not binding["start"]*1000 <= cutoff < binding["end"]*1000:
        raise HTTPException(422,"Cutoff outside this pinned UTC month")
    events, metrics, entries, unavailable = [], {}, {}, {}
    through = binding["start"]*1000
    for row in db.execute("SELECT payload,digest FROM month_replay_steps WHERE session=? ORDER BY idx",(session,)):
        step = json.loads(row["payload"])
        if fingerprint(step) != row["digest"]:
            raise HTTPException(409,"Replay receipt checksum mismatch")
        through = step["through"]*1000
        for event in step["events"]:
            # Gap observations can only be asserted once that batch has established them.
            available = through if event["kind"]=="UNRESOLVED" else event["quote"]["time_ms"]
            if available > cutoff:
                continue
            for quote in (event["quote"],event.get("anchor")):
                if quote is None:
                    continue
                if quote["time_ms"]>cutoff or month_replay.verified_quote(db,binding,quote["time_ms"],quote["ordinal"])!=quote:
                    raise HTTPException(409,"Replay evidence differs from its pinned recorded quote")
            events.append(dict(event,available_at_ms=available))
            kind = event["kind"]
            if kind=="ENTRY":
                entries[event["decision_id"]]=event
            if kind in ("STOP","TARGET"):
                entry=entries.pop(event["decision_id"],None)
                if entry is None:
                    raise HTTPException(409,"Closing receipt lacks its earlier entry")
                ask,bid=entry["quote"]["ask_units"],event["quote"]["bid_units"]
                if (entry["side"],entry["price_units"],event["side"],event["price_units"])!=("ASK",ask,"BID",bid):
                    raise HTTPException(409,"Simulated execution disagrees with recorded executable sides")
                metric_id="PRICE_CHANGE-"+event["id"]
                metrics[metric_id]=dict(metric_id=metric_id,value=format(Decimal(bid-ask)/binding["scale"],".5f"),
                    unit=UNIT,available_at_ms=available,interval_ms=[entry["quote"]["time_ms"],available],
                    entry_ref=entry["id"],exit_ref=event["id"],decision_ref=event["decision_id"],
                    calculation="exit BID minus entry ASK",policy=binding["policy"])
            if kind=="UNRESOLVED":
                unavailable[event["decision_id"]]=event["reason"]
        if through>cutoff:
            break
    for decision in entries:
        unavailable.setdefault(decision,"NO_VERIFIED_EXIT_BY_CUTOFF")
    if through<=cutoff:
        unavailable["replay_frontier"]="REPLAY_NOT_COMMITTED_THROUGH_CUTOFF"
    if not metrics:
        unavailable["completed_price_change"]="NO_COMPLETED_EXIT_IN_SNAPSHOT"
    unavailable.update(broker_fees="NO_BROKER_COST_EVIDENCE",account_pnl="NO_ACCOUNT_LEDGER",
                       month_expectancy="NO_VALIDATED_MONTH_PERFORMANCE")
    return dict(session=session,month=month,cutoff_ms=cutoff,committed_before_ms=min(through,cutoff+1),
                strategy_policy=binding["policy"],execution_policy=binding["policy"],symbol=binding["symbol"],
                feed=binding["feed"],scale=binding["scale"],execution="SIMULATED_RECORDED_BID_ASK",
                modeled_delivery_delay_ms=binding["delivery_delay_ms"],modeled_fees=binding["fees"],
                assumed_slippage_points=binding["slippage_points"],events=events,metrics=metrics,unavailable=unavailable)


@router.post("/month-replay/{month}/{session}/packages",status_code=201)
def export(month:str, session:str, cutoff_ms:int=Query(ge=0,le=32503680000000)):
    with transaction() as db:
        db.execute("BEGIN")
        evidence=snapshot(db,month,session,cutoff_ms)
    uid=domain._id("PKG")
    cutoff=datetime.fromtimestamp(cutoff_ms//1000,UTC).replace(microsecond=cutoff_ms%1000*1000).isoformat(timespec="milliseconds")
    claims=[{k:m[k] for k in ("metric_id","value","unit")} for m in evidence["metrics"].values()]
    template=HistoricalReview(review_type=TYPE,package_id=uid,session=session,assessment="INSUFFICIENT_DATA",comment="",claims=claims)
    files={"data/replay.json":json.dumps(evidence,indent=2).encode(),
           "response/AI_REVIEW_TEMPLATE.json":template.model_dump_json(indent=2,exclude={"corrects_review_id"}).encode()}
    manifest=dict(review_type=TYPE,package_id=uid,session=session,knowledge_cutoff_utc=cutoff,
                  evidence_snapshot=dict(policy="SQLITE_READ_SNAPSHOT_V1",components_sha256={
                      name:hashlib.sha256(body).hexdigest() for name,body in files.items()}))
    files["manifest.json"]=json.dumps(manifest,indent=2).encode()
    return domain.publish_package(files,uid,cutoff,replay_session=session)


@router.get("/month-replay/{month}/{session}/packages")
def package_history(month:str,session:str):
    return rows("""SELECT p.id,p.knowledge_cutoff,p.sha256 FROM ai_packages p JOIN recorded_package_sources s ON s.package_id=p.id
                   JOIN month_replay_manifests m ON m.session=s.replay_session WHERE m.month=? AND m.session=? ORDER BY p.rowid DESC""",(month,session))


@router.get("/ai/packages/{package_id}/recorded")
def contents(package_id:str):
    pkg=one("SELECT p.*,s.replay_session FROM ai_packages p JOIN recorded_package_sources s ON p.id=s.package_id WHERE p.id=?",(package_id,))
    if pkg is None:
        raise HTTPException(404,"Recorded replay package not found")
    raw=domain.read_package_bytes(pkg,missing_detail="Recorded package file unavailable",
                                  checksum_detail="Recorded package checksum mismatch")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        manifest=json.loads(archive.read("manifest.json"))
        if (manifest["package_id"],manifest["session"],manifest["review_type"])!=(package_id,pkg["replay_session"],TYPE):
            raise HTTPException(409,"Package identity differs from the saved replay association")
        return dict(manifest=manifest,evidence=json.loads(archive.read("data/replay.json")),
                    template=json.loads(archive.read("response/AI_REVIEW_TEMPLATE.json")))


def import_historical(review,original):
    package=contents(review.package_id)
    if review.session!=package["manifest"]["session"]:
        raise HTTPException(422,"Review session differs from the package")
    for claim in review.claims:
        metric=package["evidence"]["metrics"].get(claim.metric_id)
        if not metric or claim.unit!=metric["unit"] or Decimal(claim.value)!=Decimal(metric["value"]):
            raise HTTPException(422,"Metric reference, value or unit differs from the frozen replay evidence")
    return domain.store_review(review,original)
