"""Local recorded strategy execution. Immutable checkpoints are reproducible as-of projections."""
from datetime import UTC, datetime
from fractions import Fraction
from hashlib import sha256
import json

from .analysis_evidence import canonical
from .db import transaction
from .historical_import import FEED
from .indicator_coverage import MarketSchedule, open_intervals
from .offline_first_exit import simulate_first_exit
from .offline_market_entry import DeliveryInterval, RECORDED_POLICIES, published_entry_intents, simulate, verify_run
from .offline_partial_exit import ratio
from .recorded_market_entry import MAX_GAP_MS, ExecutionWindow, PartitionPin, execution_quote, recorded_partition
from .saved_history import iter_tick_partitions, tick_source_manifests, TICK_REPLAY_LIMIT
from .storage import canonical_ticks, decode_bi5, POINT_SCALES
from .strategy_evidence import fingerprint

POLICY = "RECORDED_STRATEGY_POSITION_V1"


class SessionTape(ExecutionWindow):
    """Full-session evidence with recorded bid/ask and modeled zero delivery delay."""
    recorded_management = True


def read_session(db, symbol, start, end, selected):
    if not 0 <= start < end <= 32503680000 or end-start > 31*86400:
        raise ValueError("Select a positive UTC window of at most 31 days")
    imports = db.execute("""SELECT source_start,source_end,source_sha256,manifest FROM historical_imports
        WHERE symbol=? AND feed_id=? AND source_start<? AND source_end>? ORDER BY source_start LIMIT 51""",
        (symbol, FEED, end, start)).fetchall()
    if not 1 <= len(imports) <= 50:
        raise ValueError("Select a window with 1 to 50 recorded BI5 imports")
    pins, original = [], []
    last = -1
    total = 0
    for item in imports:
        left,right,digest = (item[k] for k in ("source_start","source_end","source_sha256"))
        info = json.loads(item["manifest"])
        if not isinstance(info,dict) or not isinstance(info.get("source"),dict) or not isinstance(info.get("tick_dataset"),dict):
            raise ValueError("Selected import lacks a verified source and tick partition")
        source = info["source"]
        if (tuple(source.get(k) for k in ("start","end","sha256","symbol","provider")) !=
                (left,right,digest,symbol,"DUKASCOPY") or left < last or right-left not in (3600,86400)):
            raise ValueError("Conflicting recorded source interval or identity")
        last = right
        pin = PartitionPin(**{k:info["tick_dataset"].get(k) for k in ("id","sha256")})
        part = recorded_partition(db,pin,symbol)
        total += part["rows_count"]
        if total > TICK_REPLAY_LIMIT:
            raise ValueError("Source imports exceed the 500,000 quote replay limit")
        raw = db.execute("SELECT payload FROM historical_bi5_sources WHERE sha256=?", (digest,)).fetchone()
        if not raw or sha256(raw[0]).hexdigest()!=digest:
            raise ValueError("Original BI5 bytes unavailable or corrupt; reimport the pinned file")
        day = datetime.fromtimestamp(left,UTC)
        decoded = decode_bi5(raw[0], symbol, day, day.hour if right-left==3600 else None)
        if len(decoded)!=part["rows_count"]:
            raise ValueError("BI5 record count disagrees with tick partition")
        original.extend(r | {"source_sha256":digest} for r in canonical_ticks(decoded) if start<=r["time"]<end)
        pins.append(dict(partition=pin.model_dump(),source_sha256=digest,start=left,end=right))
    def verify(selection):
        parts = [recorded_partition(db,PartitionPin.model_validate(p),symbol) for p in selection]
        if len({p["id"] for p in parts})!=len(parts):
            raise ValueError("Duplicate partition selection")
        # Compare the entire ordered sequence with the independently decoded originals.
        count = 0
        for tick in iter_tick_partitions(parts, source_manifests=tick_source_manifests(parts),
                                        period=(start,end), annotate_source=True):
            if count>=len(original) or tick!=original[count]:
                raise ValueError("Selected ticks disagree with original BI5 sequence")
            count += 1
        if count!=len(original):
            raise ValueError("Selected ticks omit original BI5 observations")
    primary = [p["partition"] for p in pins]
    verify(primary)
    selected = sorted(selected or primary,key=lambda p:p["id"])
    extra_reads = 0
    if selected!=sorted(primary,key=lambda p:p["id"]):
        verify(selected)
        extra_reads = len(selected)
    return original,pins,selected,len(primary)+extra_reads


def asof_tape(symbol, records, frontier):
    quotes,intervals = [],[]
    previous = None
    scale = POINT_SCALES[symbol]
    market = iter(open_intervals(records[0]["time"],min(records[-1]["time"],frontier//1000)+1,
                                 MarketSchedule(kind="forex_weekly"))) if records and frontier//1000>=records[0]["time"] else iter(())
    slot = next(market,None)
    for row in records:
        ms = row["time_ms"]
        if ms>frontier:
            break
        while slot and slot[1]<=row["time"]:
            slot = next(market,None)
        if not slot or row["time"]<slot[0]:
            raise ValueError("Recorded observation contradicts the pinned FX calendar")
        quotes.append(execution_quote(row,scale))
        if previous is None or ms-previous>MAX_GAP_MS:
            intervals.append([ms,ms])
        elif previous//1000 < slot[0]:
            intervals.append([ms,ms])
        else:
            intervals[-1][1] = ms
        previous = ms
    return SessionTape(symbol,scale,quotes,[DeliveryInterval(start_ms=a,end_ms=b) for a,b in intervals])


def replay(run_id, start, end, cutoff_ms, *, selected=None, resume=None):
    if not start*1000<=cutoff_ms<=end*1000:
        raise ValueError("Checkpoint must be inside the quote window")
    with transaction() as db:
        db.execute("BEGIN")
        identity,model = verify_run(db,run_id)
        _,_,intents = published_entry_intents(db,run_id,identity,model)
        symbol = getattr(model.instruments,model.trade_intent.role.lower()).symbol
        if any(i["symbol"]!=symbol for i in intents) or any(i["qualified_at"]<start for i in intents):
            raise ValueError("Quote window must cover all prior qualified intents for the execution symbol")
        records,pins,layout,reads = read_session(db,symbol,start,end,selected)
        ledger = db.execute("""SELECT id,attempt_id,state,cutoff,
            json_extract(payload,'$.reason') AS reason FROM historical_strategy_decisions
            WHERE run_id=? ORDER BY cutoff,id""",(run_id,)).fetchall()
        qualified = {i["decision_id"]:i for i in intents}
        decisions = []
        for row in ledger:
            intent = qualified.get(row["id"])
            # A SINGLE_ATTEMPT receipt may be recorded at the run horizon. verify_intent
            # checks every cited proof against its earlier trigger activation.
            effective = intent["qualified_at"] if intent else row["cutoff"]
            if effective*1000<=cutoff_ms:
                decisions.append(dict(row) | {"available_at":effective,
                    "reason":"TRIGGER_QUALIFIED" if intent else row["reason"]})
    binding = dict(policy=POLICY,run_id=run_id,symbol=symbol,feed=FEED,basis="BID_ASK_UNITS",start=start,end=end,
                   sources=pins,delay_ms=0,spread="OBSERVED_BID_ASK",slippage_bps=model.execution.slippage_bps,
                   declared_commission_bps=model.execution.commission_bps,quantity_unit="FRACTION_OF_INITIAL_POSITION",
                   max_gap_ms=MAX_GAP_MS,calendar="DUKASCOPY_FX_NY17_V1")
    digest = fingerprint(binding)
    session = "RS-"+fingerprint([digest,layout])
    if resume and resume!=session:
        raise ValueError("Checkpoint source, layout or strategy differs")
    tape = asof_tape(symbol,records,cutoff_ms)
    previous = None
    if resume:
        with transaction() as db:
            saved = db.execute("""SELECT cutoff_ms,payload,payload_hash FROM recorded_strategy_checkpoints
                WHERE session_id=? AND cutoff_ms<=? ORDER BY cutoff_ms DESC LIMIT 1""",(session,cutoff_ms)).fetchone()
            if not saved or sha256(saved["payload"].encode()).hexdigest()!=saved["payload_hash"]:
                raise ValueError("Matching intact execution checkpoint required")
            previous = json.loads(saved["payload"])
        if previous["binding"]!=binding or previous["layout"]!=layout or previous["session_id"]!=session:
            raise ValueError("Checkpoint does not match pinned replay")
    completed = {p["intent_id"]:p for p in previous["positions"] if p.get("exit")} if previous else {}
    positions = []
    def quote_source(quote):
        origin = next(p for p in pins if p["start"]*1000<=quote["time_ms"]<p["end"]*1000)
        return {"source_sha256":origin["source_sha256"],"partition":origin["partition"],
                "time_ms":quote["time_ms"],"ordinal":quote["source_order"]}
    for intent in intents:
        if intent["qualified_at"]*1000>cutoff_ms:
            continue
        if intent["id"] in completed:
            positions.append(completed[intent["id"]])
            continue
        entry = simulate(intent,model,tape,digest,policy=RECORDED_POLICIES.get(intent["entry"]["kind"]),cutoff_ms=cutoff_ms)
        position = dict(intent_id=intent["id"],decision_id=intent["decision_id"],entry=entry,exit=None,
                        status=entry["status"],closed=ratio(Fraction(0)),remaining=ratio(Fraction(0)))
        if entry["fill"]:
            managed = simulate_first_exit(entry,model.management,tape,digest,cutoff_ms)
            # The final event is horizon independent. Keep the as-of projection outside the event.
            position.update(status=managed["status"],exit=managed["exit"],management=model.management.model_dump(mode="json"),
                            levels=managed["levels"],management_policy=managed["identity"]["policy"],
                            events={k:managed[k] for k in ("activation","ratchet","partial_exit","active_stop_units","deadline_ms") if k in managed})
            qty = managed.get("remaining_fraction",ratio(Fraction(0 if managed["exit"] else 1)))
            position.update(remaining=qty,closed=ratio(1-Fraction(qty["numerator"],qty["denominator"])))
            if position["exit"]:
                position["exit"] = {"closed_fraction":ratio(Fraction(1)),**position["exit"]}
            refs = {"entry":quote_source(entry["fill"]["quote"])}
            for key in ("activation","ratchet","partial_exit","exit"):
                if event:=managed.get(key):
                    refs[key] = quote_source(event["quote"])
            position["quote_references"] = refs
        positions.append(position)
    gaps = []
    cursor = start*1000
    for interval in tape.complete_delivery_intervals:
        if cursor < interval.start_ms:
            gaps.append([cursor,interval.start_ms])
        cursor = interval.end_ms+1
    if cursor < min(end*1000,cutoff_ms+1):
        gaps.append([cursor,min(end*1000,cutoff_ms+1)])
    payload = dict(unverified_intervals_ms=gaps,session_id=session,binding=binding,layout=layout,cutoff_ms=cutoff_ms,decisions=decisions,positions=positions,
                   intervals=[i.model_dump() for i in tape.complete_delivery_intervals],observed_quotes=len(tape.quotes))
    data = canonical(payload)
    with transaction() as db:
        db.execute("BEGIN IMMEDIATE")
        same = db.execute("SELECT payload FROM recorded_strategy_checkpoints WHERE session_id=? AND cutoff_ms=?",
                          (session,cutoff_ms)).fetchone()
        if not same and db.execute("SELECT count(*) FROM recorded_strategy_checkpoints WHERE session_id=?",
                                   (session,)).fetchone()[0] >= 160:
            raise ValueError("Recorded session exceeds 160 checkpoints")
        db.execute("INSERT OR IGNORE INTO recorded_strategy_checkpoints VALUES (?,?,?,?)",
                   (session,cutoff_ms,data,sha256(data.encode()).hexdigest()))
        if db.execute("SELECT payload FROM recorded_strategy_checkpoints WHERE session_id=? AND cutoff_ms=?",
                      (session,cutoff_ms)).fetchone()[0]!=data:
            raise ValueError("Execution checkpoint identity conflict")
    return payload | {"parquet_reads":reads,"bi5_reads":len(pins)}
