"""Offline, immutable entry validation for published historical intents."""
import argparse
import json
from pathlib import Path
from typing import Literal

from pydantic import Field, StrictInt, model_validator

from .analysis_evidence import canonical
from .arrival_policy import SIMULATION_AUTHORITY, SIMULATION_MODE
from .db import initialize, transaction
from .historical_strategy_engine import historical_decision_id
from .storage import POINT_SCALES
from .strategy_definitions import Contract, ExactEvidenceReference, StrategyDefinitionV2, validate_definition
from .strategy_evaluator import qualified_entry_intent
from .strategy_evidence import fingerprint
from .tick_replay import executable_price, executable_side

TAPE_LIMIT_BYTES = 4 * 1024**2
POLICY = {
    "id":"OFFLINE_MARKET_ENTRY", "version":2,
    "quote_order":"DELIVERY_OBSERVATION_SOURCE_SEQUENCE",
    "availability":"OBSERVED_AND_DELIVERED_STRICTLY_AFTER_QUALIFICATION",
    "fill_clock":"DELIVERY_UTC_MS", "expiry":"INCLUSIVE_SECONDS",
    "coverage":"SEALED_SYNTHETIC_DELIVERY_INTERVALS",
    "pricing":"EXACT_EXECUTABLE_SIDE_POINTS_ZERO_SLIPPAGE",
}


class Quote(Contract):
    time_ms: StrictInt = Field(ge=0,le=32503680000000)
    available_at_ms: StrictInt = Field(ge=0,le=32503680000000)
    source_order: StrictInt = Field(ge=0,le=2**53-1)
    bid_units: StrictInt = Field(gt=0,le=10**12)
    ask_units: StrictInt = Field(gt=0,le=10**12)

    @model_validator(mode="after")
    def coherent(self):
        if self.available_at_ms<self.time_ms or self.ask_units<self.bid_units:
            raise ValueError("Invalid quote timing or bid/ask")
        return self


class DeliveryInterval(Contract):
    start_ms: StrictInt = Field(ge=0,le=32503680000000)
    end_ms: StrictInt = Field(ge=0,le=32503680000000)

    @model_validator(mode="after")
    def ordered(self):
        if self.end_ms<self.start_ms:
            raise ValueError("Coverage interval is reversed")
        return self


class SyntheticTape(Contract):
    kind: Literal["SYNTHETIC_EXECUTABLE_QUOTES_V1"]
    source_id: str = Field(min_length=1,max_length=128)
    symbol: str = Field(min_length=1,max_length=16)
    scale: StrictInt = Field(gt=0)
    complete_delivery_intervals: list[DeliveryInterval] = Field(max_length=64)
    quotes: list[Quote] = Field(max_length=10000)

    @model_validator(mode="after")
    def point_scale(self):
        if POINT_SCALES.get(self.symbol)!=self.scale:
            raise ValueError("Execution tape point scale does not match the symbol specification")
        return self


def covered(intervals, start, end):
    for interval in sorted(intervals,key=lambda item:item.start_ms):
        if interval.start_ms>start:
            break
        start = max(start,interval.end_ms+1)
        if start>end:
            return True
    return False


def verify_run(db, run_id):
    record = db.execute("""SELECT r.payload,r.strategy_version_id,w.state,w.frontier
                           FROM historical_strategy_runs r JOIN historical_worker_state w ON w.run_id=r.id
                           WHERE r.id=?""",(run_id,)).fetchone()
    if record is None or record["state"]!="COMPLETED":
        raise ValueError("Execution validation requires a completed published historical run")
    run = json.loads(record["payload"])
    identity = run["identity"]
    if (run["run_id"]!=run_id or run_id!="HR-"+fingerprint(identity)
            or record["frontier"]!=identity["cutoff"]
            or identity["purpose"]!=SIMULATION_MODE or identity["availability"]!=SIMULATION_MODE
            or record["strategy_version_id"]!=identity["strategy_version_id"]
            or identity["frozen_definition"]!=fingerprint(run["definition"])
            or identity["frozen_selection"]!=fingerprint(run["selection"])):
        raise ValueError("Historical run identity or pinned inputs do not match")
    version = db.execute("SELECT * FROM strategy_versions WHERE version_id=?",
                         (identity["strategy_version_id"],)).fetchone()
    if (version is None or version["strategy_id"]!=identity["strategy_id"]
            or version["status"]!="READY_FOR_EVALUATION"
            or version["configuration_fingerprint"]!=identity["configuration_fingerprint"]):
        raise ValueError("Pinned strategy version does not match the run")
    checked = validate_definition(json.loads(version["definition"]))
    if (not checked["valid"] or checked["configuration_fingerprint"]!=identity["configuration_fingerprint"]
            or checked["definition"]!=run["definition"]):
        raise ValueError("Pinned strategy policies do not match the immutable version")
    return identity,StrategyDefinitionV2.model_validate(run["definition"])


def verify_intent(run_id, identity, model, row):
    intent = json.loads(row["intent"])
    trace = json.loads(row["decision"])
    qualified = trace["trigger_activated_at"]
    context = trace["context_activated_at"]
    attempt = "ATT-"+fingerprint([identity,context])
    if (row["attempt_run"]!=run_id or row["decision_run"]!=run_id or row["decision_attempt"]!=attempt
            or row["attempt_state"]!="TRIGGER_QUALIFIED" or row["decision_state"]!="TRIGGER_QUALIFIED"
            or trace["state"]!="TRIGGER_QUALIFIED" or trace["attempt_id"]!=attempt or row["attempt_id"]!=attempt
            or row["cutoff"]!=trace["cutoff"] or type(qualified) is not int or type(context) is not int
            or not identity["evaluation_start"]<=context<=qualified<=row["cutoff"]<=identity["cutoff"]
            or row["decision_id"]!=historical_decision_id(run_id,trace)):
        raise ValueError("Intent attempt and decision do not belong to the published run")
    references = []
    for key,event in sorted(trace["supporting_events"].items()):
        ref = event["evidence_reference"]
        parsed = ExactEvidenceReference.model_validate(ref)
        if (key!=f"{parsed.calculation_snapshot}:{parsed.event_id}" or event["id"]!=parsed.event_id
                or event["available_at"]!=parsed.available_at or parsed.available_at>qualified
                or parsed.availability_mode!=SIMULATION_MODE or parsed.source_authority!=SIMULATION_AUTHORITY):
            raise ValueError("Published intent contains mismatched supporting proofs")
        references.append(ref)
    if not references:
        raise ValueError("Published intent has no exact supporting proofs")
    expected = qualified_entry_intent(model,identity,attempt,qualified) | {
        "historical_run_id":run_id,"decision_id":row["decision_id"],"evidence_references":references}
    if row["intent_id"]!=expected["id"] or intent!=expected or trace["entry_intent"]!=intent:
        raise ValueError("Published intent does not match its source decision and pinned policies")
    return intent


def quote_priority(quote: Quote) -> tuple[int,int,int]:
    return quote.available_at_ms,quote.time_ms,quote.source_order


def simulate(intent, model, tape, tape_digest):
    identity = {"intent":intent["id"],"execution_data_fingerprint":tape_digest,"policy":dict(POLICY)}
    identifier = "ME-"+fingerprint(identity)
    result = {"id":identifier,"identity":identity,"source":intent,
              "source_execution_policy":model.execution.model_dump(mode="json"),
              "order":None,"fill":None}
    if intent["entry"]["kind"]!="MARKET":
        return result | {"status":"UNSUPPORTED_ENTRY_TYPE"}
    expiry = intent["order_expiry"]
    if expiry["unit"]!="SECONDS" or model.execution.slippage_bps!=0 or intent["symbol"] not in POINT_SCALES:
        return result | {"status":"UNSUPPORTED_EXECUTION_POLICY"}

    begin = intent["qualified_at"]*1000
    end = begin+expiry["count"]*1000
    buy = intent["direction"]=="LONG"
    side = executable_side(buy)
    result["order"] = {"kind":"MARKET","executable_side":side.upper(),
                       "strictly_after_ms":begin,"expires_at_ms":end}
    if tape is None:
        return result | {"status":"INSUFFICIENT_QUOTE_COVERAGE"}
    if tape.symbol!=intent["symbol"]:
        raise ValueError("Execution quote symbol does not match the published intent")
    relevant = [quote for quote in tape.quotes if begin<quote.time_ms<=quote.available_at_ms<=end]
    chosen = min(relevant,key=quote_priority,default=None)
    needed = chosen.available_at_ms if chosen else end
    if not covered(tape.complete_delivery_intervals,begin+1,needed):
        return result | {"status":"INSUFFICIENT_QUOTE_COVERAGE"}
    if chosen is None:
        return result | {"status":"VERIFIED_EXPIRED"}

    winning_priority = quote_priority(chosen)
    if any(quote_priority(quote)==winning_priority and quote!=chosen for quote in relevant):
        return result | {"status":"AMBIGUOUS_QUOTE_ORDER"}

    quote = chosen.model_dump(mode="json") | {"scale":tape.scale}
    price = executable_price(quote,buy)
    result["fill"] = {"id":"MF-"+fingerprint([identifier,quote]),"status":"OFFLINE_SIMULATED_FILL",
                      "time_ms":chosen.available_at_ms,"side":side.upper(),
                      "price":format(price,f".{len(str(tape.scale))-1}f"),
                      "price_units":quote[f"{side}_units"],"quote":quote}
    return result | {"status":"SIMULATED_FILLED"}


def validate_market_entries(run_id, *, tape=None, tape_sha256=None, intent_id=None):
    """Validate published intents without changing their ledger or qualification status."""
    if tape is None:
        if tape_sha256 is not None:
            raise ValueError("Execution fingerprint requires a synthetic quote tape")
        digest = fingerprint(None)
    else:
        tape = SyntheticTape.model_validate(tape)
        digest = fingerprint(tape.model_dump(mode="json"))
        if digest!=tape_sha256:
            raise ValueError("Synthetic execution tape fingerprint mismatch")

    with transaction() as db:
        db.execute("BEGIN IMMEDIATE")
        identity,model = verify_run(db,run_id)
        attempts,qualified = db.execute("""SELECT COUNT(*),COUNT(*) FILTER (WHERE state='TRIGGER_QUALIFIED')
                                          FROM historical_strategy_attempts WHERE run_id=?""",(run_id,)).fetchone()
        rows = db.execute("""SELECT i.id AS intent_id,i.payload AS intent,i.attempt_id,i.decision_id,
                    a.run_id AS attempt_run,a.state AS attempt_state,a.decision_id AS attempt_decision,
                    d.run_id AS decision_run,d.attempt_id AS decision_attempt,d.state AS decision_state,
                    d.cutoff,d.payload AS decision
                    FROM historical_strategy_intents i
                    JOIN historical_strategy_attempts a ON a.id=i.attempt_id
                    JOIN historical_strategy_decisions d ON d.id=i.decision_id
                    WHERE i.run_id=? ORDER BY d.cutoff""",(run_id,)).fetchall()
        if len(rows)!=qualified or any(row["decision_id"]!=row["attempt_decision"] for row in rows):
            raise ValueError("Published run qualification ledger does not reconcile")
        intents = [verify_intent(run_id,identity,model,row) for row in rows]
        selected = [intent for intent in intents if intent_id is None or intent["id"]==intent_id]
        if intent_id is not None and not selected:
            raise ValueError("Intent does not belong to the published run")
        results = []
        for intent in selected:
            result = simulate(intent,model,tape,digest)
            payload = canonical(result)
            prior = db.execute("SELECT payload FROM offline_market_entry_results WHERE id=?",
                               (result["id"],)).fetchone()
            if prior:
                if prior["payload"]!=payload:
                    raise ValueError("Offline execution result identity conflict")
            else:
                db.execute("INSERT INTO offline_market_entry_results VALUES (?,?,?,?)",
                           (result["id"],run_id,intent["id"],payload))
            results.append(result)
    return {"run_id":run_id,"historical_attempts":attempts,"qualified_intents":qualified,
            "simulated_orders":sum(item["order"] is not None for item in results),
            "simulated_fills":sum(item["fill"] is not None for item in results),"results":results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run",required=True)
    parser.add_argument("--intent")
    parser.add_argument("--tape",type=Path)
    parser.add_argument("--sha256",help="SHA-256 of the canonical validated synthetic tape")
    args = parser.parse_args()
    try:
        tape = None
        if args.tape:
            with args.tape.open("rb") as stream:
                raw = stream.read(TAPE_LIMIT_BYTES+1)
            if len(raw)>TAPE_LIMIT_BYTES:
                raise ValueError("Synthetic quote tape exceeds the offline file limit")
            tape = SyntheticTape.model_validate_json(raw)
        initialize()
        result = validate_market_entries(args.run,tape=tape,tape_sha256=args.sha256,intent_id=args.intent)
    except (ValueError,OSError) as error:
        parser.exit(2,f"{error}\n")
    print(json.dumps(result,indent=2))


if __name__=="__main__":
    main()
