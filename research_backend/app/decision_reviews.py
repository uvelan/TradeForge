"""Blind journal projections. Only an explicitly revealed attempt has outcome data."""
from datetime import UTC, date, datetime
import json
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from .db import transaction
from .domain import resolve_signal, resolve_snapshot
from .market import FRAME_MIN
from .plan_paper import history
from .schemas import SignalReference
from .storage import read_partition
from .strategy_evidence import fingerprint

router = APIRouter(prefix="/api/learning/reviews")
CASES = """SELECT v.* FROM plan_versions v WHERE
 EXISTS (SELECT 1 FROM plan_paper_events p WHERE p.trade_id=v.trade_id AND p.version=v.version
         AND json_extract(p.payload,'$.request.kind')='CLOSE')
 OR (EXISTS (SELECT 1 FROM trade_events e WHERE e.trade_id=v.trade_id AND e.kind IN ('NO_TRADE','TRADE_CLOSED'))
     AND NOT EXISTS (SELECT 1 FROM plan_paper_events p WHERE p.trade_id=v.trade_id)
     AND v.version=(SELECT MAX(version) FROM plan_versions WHERE trade_id=v.trade_id))"""


class Assessment(BaseModel, extra="forbid"):
    prompt_hash: str = Field(pattern="^[a-f0-9]{64}$")
    assessment: str = Field(min_length=1, max_length=4000)

    @field_validator("assessment")
    @classmethod
    def meaningful(cls, value):
        if not value.strip():
            raise ValueError("Enter an independent assessment")
        return value.strip()


class Practice(BaseModel, extra="forbid"):
    classification: Literal["RULE_FOLLOWED", "PROCESS_MISTAKE"]
    objective: str = Field(min_length=1, max_length=2000, pattern=r"\S")
    due: date


def case(db, trade, version):
    row = db.execute("SELECT * FROM (" + CASES + ") WHERE trade_id=? AND version=?", (trade, version)).fetchone()
    if not row:
        raise HTTPException(404, "Completed decision revision unavailable")
    return row


def steps(db, trade, version):
    return {r["step"]: json.loads(r["payload"]) for r in db.execute(
        "SELECT step,payload FROM decision_review_steps WHERE trade_id=? AND version=?", (trade, version))}


def phase(saved):
    if "completed" in saved:
        return "DRILL_COMPLETED"
    if "practice" in saved:
        return "DUE" if saved["practice"]["due"] <= datetime.now(UTC).date().isoformat() else "SCHEDULED"
    if "revealed" in saved:
        return "REVEALED"
    return "ASSESSED" if "assessed" in saved else "BLIND"


@router.get("")
def choices():
    with transaction() as db:
        return [dict(trade=r["trade_id"], version=r["version"], symbol=json.loads(r["payload"])["symbol"],
                     phase=phase(steps(db, r["trade_id"], r["version"])))
                for r in db.execute(CASES + " ORDER BY v.created_at DESC,v.id")]


def pinned_lane(db, lane, timeframe, cutoff, wanted=None, start=None, end=None):
    pins = lane.get("partitions") or [lane]
    pins = [{"id": p.get("id") or p.get("dataset_id"), "sha256": p.get("sha256")} for p in pins]
    bars = {}
    for pin in pins:
        row = db.execute("SELECT * FROM datasets WHERE id=?", (pin["id"],)).fetchone()
        if (not row or not pin["sha256"] or row["sha256"] != pin["sha256"] or row["timeframe"] != timeframe
                or any(row[k] != lane.get(k) for k in ("symbol", "feed_id", "price_basis"))):
            raise HTTPException(409, "Original candle pin unavailable")
        try:
            data = read_partition(dict(row))
        except (ValueError, OSError, RuntimeError) as exc:
            raise HTTPException(409, f"Original candle archive unavailable: {exc}") from exc
        for bar in data:
            t = bar["time"]
            available = bar.get("available_at")
            if available is None:
                available = t + FRAME_MIN[timeframe] * 60
            if available > cutoff or (wanted is not None and t not in wanted):
                continue
            if (start is not None and t < start) or (end is not None and t >= end):
                continue
            value = {k: bar[k] for k in ("time", "open", "high", "low", "close")}
            value["available_at"] = available
            if t in bars and bars[t] != value:
                raise HTTPException(409, "Conflicting archived candle")
            bars[t] = value
    return dict(symbol=lane["symbol"], timeframe=timeframe, feed_id=lane["feed_id"],
                price_basis=lane["price_basis"], partitions=pins, bars=[bars[t] for t in sorted(bars)])


def prompt(db, row):
    plan = json.loads(row["payload"])
    snapshot = resolve_snapshot(db, plan.get("snapshot_id"))
    ref = plan.get("signal_reference") or (snapshot[0].get("signal_reference") if snapshot else None)
    signal = resolve_signal(db, SignalReference(**ref)) if ref else None
    times = []
    if snapshot and snapshot[0].get("cutoff") is not None:
        times.append(snapshot[0]["cutoff"])
    signal_time = None
    if signal:
        transition = next((t for t in reversed(signal["transitions"]) if t["status"] == ref["status"]), None)
        if transition is None:
            raise HTTPException(409, "Original signal transition unavailable")
        signal_time = transition["time"]
        if not times:
            times.append(signal_time)
    if plan.get("knowledge_cutoff_utc"):
        t = datetime.fromisoformat(plan["knowledge_cutoff_utc"])
        if t.tzinfo:
            times.append(int(t.timestamp()))
    cutoff = min(times) if times else None
    lanes = []
    if cutoff is not None:
        if snapshot and snapshot[0].get("lanes"):
            for lane in snapshot[0]["lanes"]:
                lanes.append(pinned_lane(db, lane, lane["timeframe"], cutoff, set(lane["bar_times"])))
        elif signal and signal_time <= cutoff:
            source = signal["provenance"]
            for side in ("primary", "comparison"):
                lanes.append(pinned_lane(db, source[side], source["timeframe"], cutoff,
                                         start=source.get("watch_start"), end=source.get("watch_end")))
    fields = ("symbol", "direction", "strategy", "context", "observation", "evidence", "hypothesis", "setup", "trigger", "invalidation",
              "entry", "stop", "target", "equity_usd", "risk_percent", "management")
    return dict(plan_version_id=row["id"], plan={k: plan[k] for k in fields}, cutoff=cutoff, lanes=lanes,
                snapshot_id=plan.get("snapshot_id"), signal_reference=ref if signal_time is not None and cutoff is not None and signal_time <= cutoff else None)


def view(db, row):
    saved = steps(db, row["trade_id"], row["version"])
    evidence = saved["assessed"]["prompt"] if "assessed" in saved else prompt(db, row)
    result = dict(prompt=evidence, prompt_hash=fingerprint(evidence), phase=phase(saved))
    if "assessed" in saved:
        result["assessment"] = saved["assessed"]["assessment"]
    for key in ("revealed", "practice", "completed"):
        if key in saved:
            result[key] = saved[key]
    return result


@router.get("/{trade}/{version}")
def inspect(trade: str, version: int):
    with transaction() as db:
        return view(db, case(db, trade, version))


def append(db, row, key, value):
    prior = steps(db, row["trade_id"], row["version"])
    if key in prior:
        if prior[key] != value:
            raise HTTPException(409, "This review step already has a different immutable response")
    else:
        db.execute("INSERT INTO decision_review_steps VALUES(?,?,?,?,?)",
                   (row["trade_id"], row["version"], key, json.dumps(value), datetime.now(UTC).isoformat()))
    return view(db, row)


@router.post("/{trade}/{version}/assess")
def assess(trade: str, version: int, request: Assessment):
    with transaction() as db:
        db.execute("BEGIN IMMEDIATE")
        row = case(db, trade, version)
        current = view(db, row)
        if current["prompt_hash"] != request.prompt_hash:
            raise HTTPException(409, "Review evidence changed since opening")
        return append(db, row, "assessed", {"assessment": request.assessment, "prompt": current["prompt"]})


@router.post("/{trade}/{version}/reveal")
def reveal(trade: str, version: int):
    with transaction() as db:
        db.execute("BEGIN IMMEDIATE")
        row = case(db, trade, version)
        saved = steps(db, trade, version)
        if "assessed" not in saved:
            raise HTTPException(409, "Record the independent assessment first")
        if "revealed" in saved:
            return view(db, row)
        receipts = history(db, trade)
        if receipts:
            if any(r["plan_version_id"] != row["id"] or r["plan_sha256"] != fingerprint(json.loads(row["payload"])) for r in receipts):
                raise HTTPException(409, "Paper receipts disagree with the submitted revision")
            outcome = dict(kind="PAPER_CLOSED", receipts=receipts)
        else:
            event = db.execute("""SELECT id,kind,payload FROM trade_events WHERE trade_id=?
                                  AND kind IN ('NO_TRADE','TRADE_CLOSED')""", (trade,)).fetchone()
            outcome = dict(kind=event["kind"], event_id=event["id"], **json.loads(event["payload"]))
        ref = saved["assessed"]["prompt"]["signal_reference"]
        if ref:
            outcome["signal_history"] = [dict(r) for r in db.execute("""
                SELECT status,event_time FROM smt_event_history WHERE event_key=?
                UNION ALL SELECT 'SMT_SOURCE_CORRECTED',event_time FROM smt_source_corrections WHERE event_key=?
                ORDER BY event_time,status""", (ref["event_key"], ref["event_key"]))]
        return append(db, row, "revealed", outcome)


@router.post("/{trade}/{version}/practice")
def schedule(trade: str, version: int, request: Practice):
    with transaction() as db:
        db.execute("BEGIN IMMEDIATE")
        row = case(db, trade, version)
        if "revealed" not in steps(db, trade, version):
            raise HTTPException(409, "Reveal the decision before scheduling practice")
        return append(db, row, "practice", request.model_dump(mode="json"))


@router.post("/{trade}/{version}/complete")
def complete(trade: str, version: int):
    with transaction() as db:
        db.execute("BEGIN IMMEDIATE")
        row = case(db, trade, version)
        if "practice" not in steps(db, trade, version):
            raise HTTPException(409, "Schedule a practice drill first")
        return append(db, row, "completed", True)
