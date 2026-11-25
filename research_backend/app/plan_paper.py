"""Explicit, long-only EURUSD paper decisions on local verified BI5 quotes."""
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
import json
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, StrictInt

from tradeforge.paper_ledger import PaperLedger, PaperOrder, PaperQuote, PaperSide
from .db import transaction
from .domain import resolve_snapshot, resolve_signal
from .schemas import SignalReference
from .reviewed_archives import candle_import_claim
from .historical_import import FEED
from .recorded_session import asof_tape, read_session
from .strategy_evidence import fingerprint

router = APIRouter(prefix="/api/paper/plans")
SCALE = 100_000
POLICY = "PLAN_EURUSD_CASH_MARKET_V1"


class Action(BaseModel, extra="forbid"):
    version: StrictInt = Field(ge=1)
    sequence: StrictInt = Field(ge=1, le=1024)
    kind: Literal["ENTRY","REDUCE","CLOSE"]
    quantity_eur: StrictInt = Field(gt=0, le=10_000_000)
    decision_ms: StrictInt = Field(ge=0, le=32_503_680_000_000)


def units(value):
    scaled = Decimal(value)*SCALE
    if scaled != scaled.to_integral_value():
        raise ValueError("Amount must be representable in USD units of 0.00001")
    return int(scaled)


def history(db, trade):
    return [json.loads(row[0]) for row in db.execute(
        "SELECT payload FROM plan_paper_events WHERE trade_id=? ORDER BY sequence",(trade,))]


@router.get("/{trade}")
def review(trade: str):
    with transaction() as db:
        return {"policy":POLICY,"events":history(db,trade)}


@router.get("/{trade}/fills/{fill_id}")
def fill_receipt(trade: str, fill_id: str):
    with transaction() as db:
        for event in history(db, trade):
            if event["fill_id"] == fill_id:
                return event
    raise HTTPException(404, "Paper fill unavailable")


def frozen_cutoff(db, plan):
    captured = resolve_snapshot(db,plan.get("snapshot_id"))
    if not captured:
        raise ValueError("Save a frozen recorded EURUSD chart and link it to the plan")
    meta = captured[0]
    lanes = meta.get("lanes") or []
    if (meta["cutoff"] is None or meta["source"]!="DUKASCOPY_SAVED" or not lanes
            or lanes[0]["feed_id"]!=FEED or lanes[0]["price_basis"]!="BID"):
        raise ValueError("Paper replay requires a frozen chart with recorded Dukascopy BI5 BID pins")
    # A free-form feed label alone cannot attest a chart to a verified BI5 import.
    for pin in lanes[0]["partitions"]:
        dataset = db.execute("SELECT * FROM datasets WHERE id=?", (pin["id"],)).fetchone()
        if (not dataset or dataset["sha256"] != pin["sha256"] or dataset["symbol"] != "EURUSD"
                or dataset["timeframe"] != meta["timeframe"] or dataset["feed_id"] != FEED
                or dataset["price_basis"] != "BID" or not candle_import_claim(db, dataset)):
            raise ValueError("Chart partition lacks a matching recorded BI5 import")
    cutoff = meta["cutoff"]*1000
    for ref in (meta.get("signal_reference"),plan.get("signal_reference")):
        if ref:
            event=resolve_signal(db,SignalReference(**ref))
            if event["transitions"][-1]["time"]*1000>cutoff:
                raise ValueError("Linked signal became available after the chart cutoff")
    if plan.get("knowledge_cutoff_utc"):
        t=datetime.fromisoformat(plan["knowledge_cutoff_utc"])
        if t.tzinfo is None:
            raise ValueError("Plan knowledge time requires an explicit UTC offset")
        cutoff=max(cutoff,int(t.timestamp()*1000))
    return cutoff


def winning_quote(db, anchor, decision):
    if decision<anchor:
        raise ValueError("Action precedes the frozen plan or previous fill")
    ticks,pins,_,_=read_session(db,"EURUSD",max(0,anchor//1000-60),decision//1000+62,[])
    winner=next((tick for tick in ticks if tick["time_ms"]>decision),None)
    if winner is None:
        raise ValueError("No later verified executable quote")
    ms=winner["time_ms"]
    tape=asof_tape("EURUSD",ticks,ms)
    if not any(i.start_ms<=anchor and ms<=i.end_ms for i in tape.complete_delivery_intervals):
        raise ValueError("Incomplete quote coverage before paper fill")
    evidence={key:winner[key] for key in ("time_ms","source_order","source_sha256","bid_units","ask_units")}
    return PaperQuote(id=fingerprint(evidence),symbol="EURUSD",observed_at_ms=ms,
                      bid_units=winner["bid_units"],ask_units=winner["ask_units"]),evidence,pins


def apply_action(db,trade,request):
    cmd=request.model_dump()
    events=history(db,trade)
    if request.sequence<=len(events):
        saved=events[request.sequence-1]
        if saved["request"]!=cmd:
            raise ValueError("Paper sequence already binds another action")
        return saved
    if request.sequence!=len(events)+1:
        raise ValueError("Refresh the paper position before submitting")
    row=db.execute("""SELECT v.id,v.payload,t.status FROM plan_versions v JOIN trades t ON t.id=v.trade_id
        WHERE v.trade_id=? AND v.version=?""",(trade,request.version)).fetchone()
    if not row:
        raise HTTPException(404,"Plan revision unavailable")
    plan=json.loads(row["payload"])
    if plan["symbol"]!="EURUSD" or plan["direction"]!="BUY":
        raise ValueError("Only cash-funded long EURUSD in a USD practice account is supported")
    version_id=row["id"]
    opening=units(plan["equity_usd"])
    ledger=PaperLedger(symbol="EURUSD",opening_cash_units=opening,max_quote_age_ms=0)
    for saved in events:
        if saved["plan_version_id"]!=version_id:
            raise ValueError("Management requires the submitted plan revision")
        previous=saved["event"]
        order=PaperOrder(**(previous["order"]|{"side":PaperSide(previous["order"]["side"])}))
        quote=PaperQuote(**previous["quote"])
        ledger,restored=ledger.apply(order,quote,decision_at_ms=quote.observed_at_ms)
        if asdict(restored)!=previous:
            raise ValueError("Stored paper ledger failed reconstruction")
    if not events:
        if request.kind!="ENTRY" or row["status"]!="PLANNED":
            raise ValueError("Entry requires an explicitly submitted active plan")
        cutoff=frozen_cutoff(db,plan)
        anchor=cutoff
        side=PaperSide.BUY
    else:
        remaining=ledger.position_units
        if (request.kind=="ENTRY" or request.quantity_eur>remaining or
                (request.kind=="CLOSE")!=(request.quantity_eur==remaining)):
            raise ValueError("Reduction must leave exposure; final close must consume exactly the remaining EUR")
        cutoff=events[0]["plan_cutoff_ms"]
        anchor=events[-1]["quote"]["time_ms"]
        side=PaperSide.SELL
    quote,evidence,pins=winning_quote(db,anchor,request.decision_ms)
    if side==PaperSide.BUY:
        stop,target=units(plan["stop"]),units(plan["target"])
        if not stop<quote.ask_units<target:
            raise ValueError("Executable entry is outside planned stop and target")
        if (quote.ask_units-stop)*request.quantity_eur>opening*Decimal(plan["risk_percent"])/100:
            raise ValueError("Selected EUR quantity exceeds the frozen risk budget")
    order=PaperOrder(id=fingerprint([POLICY,trade,version_id,cmd]),symbol="EURUSD",side=side,quantity_units=request.quantity_eur)
    updated,event=ledger.apply(order,quote,decision_at_ms=quote.observed_at_ms)
    if event.status!="PAPER_FILLED":
        raise ValueError(event.reason)
    entry=events[0]["event"]["price_units"] if events else event.price_units
    realized=updated.cash_units-opening+updated.position_units*entry
    receipt=dict(policy=POLICY,trade_id=trade,plan_version_id=version_id,plan_sha256=fingerprint(plan),
                 plan_cutoff_ms=cutoff,snapshot_id=plan["snapshot_id"],signal_reference=plan.get("signal_reference"),
                 request=cmd,event=asdict(event),fill_id=fingerprint([order.id,quote.id]),quote=evidence,
                 sources=pins,feed_id=FEED,side="ASK" if side==PaperSide.BUY else "BID",
                 fee_units=0,scale=SCALE,delivery_delay_ms=0,realized_usd_units=realized,
                 planned={k:plan[k] for k in ("entry","stop","target","management","risk_percent","equity_usd")})
    db.execute("INSERT INTO plan_paper_events VALUES(?,?,?,?)",
               (trade,request.version,request.sequence,json.dumps(receipt,sort_keys=True)))
    return receipt


@router.post("/{trade}/actions")
def submit(trade:str,request:Action):
    try:
        with transaction() as db:
            db.execute("BEGIN IMMEDIATE")
            return apply_action(db,trade,request)
    except (ValueError,RuntimeError,OSError) as exc:
        raise HTTPException(409,str(exc)) from exc
