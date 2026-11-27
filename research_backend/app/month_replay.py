"""Hour-at-a-time local EURUSD research. Every step is a pinned, immutable transaction."""
from calendar import monthrange
from datetime import UTC, datetime
import json
import re

from fastapi import APIRouter, HTTPException, Query

from .db import transaction
from .historical_import import FEED
from .indicator_coverage import MarketSchedule, open_intervals
from .recorded_market_entry import MAX_GAP_MS
from .recorded_session import read_session
from .strategy_evidence import fingerprint

router = APIRouter(prefix="/api/month-replay")
POLICY = "EURUSD_FIRST_BID_RISE_10_POINTS_PER_UTC_DAY_V2"
BATCH_SECONDS = 600
CALENDAR = MarketSchedule(kind="forex_weekly")


def inventory(db, month):
    if not re.fullmatch(r"20\d\d-(0[1-9]|1[0-2])", month):
        raise HTTPException(422, "Select a UTC calendar month")
    first = datetime.fromisoformat(month + "-01").replace(tzinfo=UTC)
    start = int(first.timestamp())
    end = start + monthrange(first.year, first.month)[1]*86400
    if end > datetime.now(UTC).timestamp():
        raise HTTPException(422, "Select a completed historical month")
    sources = {}
    for item in db.execute("""SELECT source_start,source_end,source_sha256,manifest FROM historical_imports
        WHERE feed_id=? AND symbol='EURUSD' AND source_start<? AND source_end>? ORDER BY source_start""",
                           (FEED, end, start)):
        t = item["source_start"]
        if t % 3600 or item["source_end"] != t+3600 or t in sources:
            raise HTTPException(409, "Monthly replay requires distinct hourly BI5 sources")
        try:
            pin = json.loads(item["manifest"])["tick_dataset"]
            sources[t] = dict(start=t,end=t+3600,source_sha256=item["source_sha256"],
                              partition={"id":pin["id"],"sha256":pin["sha256"]})
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(409, f"Invalid hourly import at {t}") from exc
    sessions = open_intervals(start,end,CALENDAR)
    hours = []
    for t in range(start,end,3600):
        opened = any(a < t+3600 and b > t for a,b in sessions)
        pin = sources.get(t)
        if pin and not opened:
            raise HTTPException(409, f"Registered EURUSD source during scheduled closure at {t}")
        hours.append(dict(start=t,status="REGISTERED" if pin else "MISSING_OPEN" if opened else "SCHEDULED_CLOSURE",pin=pin))
    binding = dict(month=month,start=start,end=end,policy=POLICY,symbol="EURUSD",feed=FEED,
                   price_basis="BID_ASK_UNITS",scale=100000,calendar=CALENDAR.model_dump(),
                   batch_seconds=BATCH_SECONDS,max_gap_ms=MAX_GAP_MS,delivery_delay_ms=0,fees=0,slippage_points=0,hours=hours)
    return binding


def initial():
    return dict(last=None, pending_since=None, anchor=None, spent_day=None, position=None, unresolved=False)


def evidence(row, pin):
    return dict(source_sha256=pin["source_sha256"],partition=pin["partition"],ordinal=row["source_order"],
                time_ms=row["time_ms"],bid_units=row["bid_units"],ask_units=row["ask_units"])


def run_batch(state, hour, quotes, session):
    """One long setup per UTC day; never carry a fill across insufficient evidence."""
    t = hour["start"]
    events, gaps = [], []
    def event(kind, quote, **fields):
        record = dict(kind=kind,quote=quote,**fields)
        record["id"] = fingerprint([session,record])
        events.append(record)
        return record["id"]
    end = hour["end"] * 1000
    def unresolved(reason, **timing):
        position = state["position"]
        if position and not state["unresolved"]:
            event("UNRESOLVED", state["last"], reason=reason, decision_id=position["decision_id"],
                  remaining=int("fill" in position), **timing)
            state["unresolved"] = True

    def gap(left,right):
        if left >= right:
            return
        gaps.append([left,right])
        unresolved("OPEN_SESSION_QUOTE_GAP", missing_from_ms=left)
        state["anchor"] = None
    cursor = state["pending_since"]
    if hour["status"] == "SCHEDULED_CLOSURE":
        if cursor is not None:
            gap(cursor,t*1000)
        unresolved("SCHEDULED_MARKET_CLOSURE", closure_at_ms=t*1000)
        state["pending_since"] = None
        state["anchor"] = None
        return events,gaps
    if not quotes:
        gap(cursor if cursor is not None else t*1000,end)
        state["pending_since"] = None
        return events,gaps
    for row in quotes:
        q = evidence(row,hour["pin"])
        ms = q["time_ms"]
        if cursor is None:
            gap(t*1000,ms)
        elif ms-cursor+1>MAX_GAP_MS:
            gap(cursor,ms)
        cursor = ms+1
        state["last"] = q
        if state["unresolved"]:
            continue
        day = t//86400
        pos = state["position"]
        if pos:
            if "fill" not in pos and ms>pos["qualified_at"]:
                pos["fill"] = q
                event("ENTRY",q,decision_id=pos["decision_id"],side="ASK",price_units=q["ask_units"],remaining=1)
            if "fill" in pos:
                price = q["bid_units"]
                diff = price-pos["fill"]["ask_units"]
                if diff<=-100 or diff>=200:
                    event("STOP" if diff<=-100 else "TARGET",q,decision_id=pos["decision_id"],side="BID",
                          price_units=price,remaining=0)
                    state["position"] = None
        else:
            anchor = state["anchor"]
            if anchor is None or anchor["time_ms"]//86400000 != day:
                state["anchor"] = q
            elif state["spent_day"] != day and q["bid_units"] >= anchor["bid_units"]+10:
                decision = event("QUALIFIED",q,anchor=anchor)
                state["spent_day"] = day
                state["position"] = dict(decision_id=decision,qualified_at=ms)
    if end-cursor>MAX_GAP_MS or hour.get("final"):
        gap(cursor,end)
        cursor = None
    state["pending_since"] = cursor
    return events,gaps


def latest(db, sid):
    row = db.execute("SELECT payload,digest FROM month_replay_steps WHERE session=? ORDER BY idx DESC LIMIT 1",(sid,)).fetchone()
    if not row:
        return None
    step=json.loads(row["payload"])
    if fingerprint(step)!=row["digest"]:
        raise HTTPException(409,"Month checkpoint failed integrity verification")
    return step


def committed_open_gaps(db, sid):
    """Count recorded gaps across the committed ledger, regardless of receipt paging."""
    count = 0
    for row in db.execute("SELECT payload,digest FROM month_replay_steps WHERE session=? ORDER BY idx", (sid,)):
        step = json.loads(row["payload"])
        if fingerprint(step) != row["digest"]:
            raise HTTPException(409, "Month receipt integrity failure")
        # A scheduled-closure receipt can record a missing tail of the
        # preceding open session. Closed time never appears in gaps.
        count += sum(len(batch["gaps"]) for batch in step["coverage"])
    return count


@router.get("/{month}")
def inspect(month: str, session: str | None = None):
    with transaction() as db:
        db.execute("BEGIN")
        binding=load_manifest(db,month,session) if session else inventory(db,month)
        if not binding:
            raise HTTPException(404,"Unknown pinned monthly session")
        sid=fingerprint(binding)
        sessions=[r[0] for r in db.execute("SELECT session FROM month_replay_manifests WHERE month=? ORDER BY rowid DESC",(month,))]
        checkpoint = latest(db, sid)
        return dict(session=sid,binding=binding,policy_current=binding["policy"]==POLICY,checkpoint=checkpoint,
                    sessions=sessions,open_gap_intervals=committed_open_gaps(db,sid) if checkpoint else 0)


def load_manifest(db,month,sid):
    row=db.execute("SELECT binding FROM month_replay_manifests WHERE month=? AND session=?",(month,sid)).fetchone()
    if row:
        binding=json.loads(row[0])
        if fingerprint(binding)!=sid:
            raise HTTPException(409,"Monthly source manifest failed verification")
        return binding


@router.post("/{month}/{session}/advance")
def advance(month: str,session: str, index: int=Query(ge=0,le=4463)):
    with transaction() as db:
        db.execute("BEGIN IMMEDIATE")
        binding=load_manifest(db,month,session)
        if binding is None:
            binding=inventory(db,month)
            if session!=fingerprint(binding):
                raise HTTPException(409,"Source inventory changed; inspect the new manifest")
            db.execute("INSERT INTO month_replay_manifests VALUES(?,?,?)",(session,month,json.dumps(binding)))
        prior=latest(db,session)
        next_index=prior["next_index"] if prior else 0
        if index<next_index:
            old=db.execute("SELECT payload,digest FROM month_replay_steps WHERE session=? AND idx=?",(session,index)).fetchone()
            if old and fingerprint(json.loads(old[0]))==old[1]:
                return json.loads(old[0])
            raise HTTPException(409,"Not an intact committed batch boundary")
        if binding["policy"] != POLICY:
            raise HTTPException(409,"Historical monthly policy is read-only; start a new replay for this month")
        total=(binding["end"]-binding["start"])//BATCH_SECONDS
        if index!=next_index or index>=total:
            raise HTTPException(409,"Invalid replay frontier")
        state=prior["state"] if prior else initial()
        count=prior["quote_count"] if prior else 0
        reads=prior["hour_reads"] if prior else 0
        events=[]
        coverage=[]
        # Skip adjacent empty hours without ever decoding two hourly archives in one transaction.
        for slot in range(index,total):
            t = binding["start"] + slot*BATCH_SECONDS
            hour = dict(binding["hours"][slot//6],start=t,end=t+BATCH_SECONDS,final=slot+1==total)
            quotes=[]
            if hour["pin"]:
                if coverage:
                    break
                t=hour["start"]
                try:
                    quotes,pins,_,_=read_session(db,"EURUSD",t,hour["end"],[])
                except (ValueError,RuntimeError,OSError) as exc:
                    raise HTTPException(409,f"Invalid hourly source at {t}: {exc}") from exc
                if pins!=[hour["pin"]]:
                    raise HTTPException(409,"Hourly pins changed during verification")
                reads+=1
            emitted,gaps=run_batch(state,hour,quotes,session)
            events.extend(emitted)
            count+=len(quotes)
            coverage.append(dict(start=hour["start"],status="OBSERVED" if quotes else "SOURCE_WITHOUT_QUOTES" if hour["pin"] else hour["status"],
                                 count=len(quotes),first_ms=quotes[0]["time_ms"] if quotes else None,
                                 last_ms=quotes[-1]["time_ms"] if quotes else None,gaps=gaps))
            if hour["pin"] or len(coverage)>=144:
                break
        frontier=index+len(coverage)
        step=dict(next_index=frontier,through=binding["start"]+frontier*BATCH_SECONDS,state=state,events=events,
                  coverage=coverage,quote_count=count,hour_reads=reads,completed=frontier==total)
        db.execute("INSERT INTO month_replay_steps VALUES(?,?,?,?)",(session,index,json.dumps(step),fingerprint(step)))
        return step


@router.get("/{month}/{session}/steps")
def receipts(month:str,session:str,after:int=Query(-1,ge=-1),limit:int=Query(24,ge=1,le=48)):
    with transaction() as db:
        if not db.execute("SELECT 1 FROM month_replay_manifests WHERE month=? AND session=?",(month,session)).fetchone():
            if session!=fingerprint(inventory(db,month)):
                raise HTTPException(404,"Unknown monthly session")
        result=[]
        for row in db.execute("SELECT idx,payload,digest FROM month_replay_steps WHERE session=? AND idx>? ORDER BY idx LIMIT ?",
                              (session,after,limit)):
            value=json.loads(row["payload"])
            if fingerprint(value)!=row["digest"]:
                raise HTTPException(409,"Month receipt integrity failure")
            result.append({"index":row["idx"],**value})
        return result


@router.get("/{month}/{session}/quote")
def source_quote(month:str, session:str, time_ms:int=Query(ge=0), ordinal:int=Query(ge=0)):
    with transaction() as db:
        db.execute("BEGIN")
        binding=load_manifest(db,month,session)
        checkpoint=latest(db,session) if binding else None
        if not checkpoint or not binding["start"]*1000<=time_ms<checkpoint["through"]*1000:
            raise HTTPException(404,"Quote outside the committed replay frontier")
        return verified_quote(db, binding, time_ms, ordinal)


def verified_quote(db, binding, time_ms, ordinal):
    second=time_ms//1000
    pin=binding["hours"][(second-binding["start"])//3600]["pin"]
    if not pin:
        raise HTTPException(404,"No pinned source at that time")
    try:
        records,pins,_,_=read_session(db,"EURUSD",second,second+1,[])
    except (ValueError,RuntimeError,OSError) as exc:
        raise HTTPException(409,f"Source verification failed: {exc}") from exc
    if pins!=[pin]:
        raise HTTPException(409,"Source differs from the frozen monthly manifest")
    for record in records:
        if (record["time_ms"],record["source_order"])==(time_ms,ordinal):
            return evidence(record,pin)
    raise HTTPException(404,"No observation at the requested time and source ordinal")
