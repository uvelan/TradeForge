"""Offline validation of the first fixed stop or reward target after a persisted fill."""
import argparse
import json
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

from .analysis_evidence import canonical
from .db import initialize, transaction
from .offline_market_entry import (
    ENTRY_POLICIES, Quote, covered, pin_tape, published_entry_intents, quote_priority,
    read_tape_file, simulate, verify_run,
)
from .risk import PIP_SIZE
from .strategy_evidence import fingerprint
from .tick_replay import executable_side

POLICY = {
    "id":"OFFLINE_FIXED_FIRST_EXIT","version":1,
    "activation":"ENTRY_QUOTE_THEN_LATER_PRIORITY_OBSERVED_AT_OR_AFTER_ENTRY_DELIVERY",
    "cutoff":"INCLUSIVE_DELIVERY_UTC_MS",
    "coverage":"CONTINUOUS_FROM_ENTRY_DELIVERY",
    "levels":"EXACT_FILL_ANCHORED_FIXED_PIPS_AND_REWARD_MULTIPLE",
    "execution":"ACTUAL_CLOSING_QUOTE",
}
UNSUPPORTED_MANAGEMENT = ("break_even_at_r","trailing_pips","partial_exit","time_exit")


def verified_entry(db, run_id, entry_result_id, intents, model, tape, digest):
    record = db.execute("SELECT * FROM offline_market_entry_results WHERE id=? AND run_id=?",
                        (entry_result_id,run_id)).fetchone()
    if record is None:
        raise ValueError("Entry result does not belong to the completed historical run")
    intent = next((row for row in intents if row["id"]==record["intent_id"]),None)
    entry = json.loads(record["payload"])
    identity = entry["identity"]
    if (intent is None or entry["source"]!=intent or identity["intent"]!=intent["id"]
            or identity["execution_data_fingerprint"]!=digest
            or entry["id"]!=entry_result_id or entry_result_id!="ME-"+fingerprint(identity)
            or entry["source_execution_policy"]!=model.execution.model_dump(mode="json")
            or (tape is not None and tape.symbol!=intent["symbol"])):
        raise ValueError("Entry result does not match the source intent and pinned execution tape")
    if identity["policy"]==ENTRY_POLICIES.get(intent["entry"]["kind"]):
        if entry!=simulate(intent,model,tape,digest):
            raise ValueError("Persisted entry does not match verified replay")
    elif entry["fill"] is not None or entry["status"]=="SIMULATED_FILLED":
        raise ValueError("Filled entry uses an unsupported entry policy version")
    return entry


def price_text(units: int, scale: int) -> str:
    return format(Decimal(units)/scale,f".{len(str(scale))-1}f")


def protective_levels(fill_units, scale, pip_size, management, long):
    risk = Fraction(str(management.initial_stop.distance))*Fraction(pip_size)*scale
    reward = risk*Fraction(str(management.target.multiple))
    direction = 1 if long else -1
    stop = fill_units-direction*risk
    target = fill_units+direction*reward
    if any(value.denominator!=1 or not 0<value<=10**12 for value in (stop,target)):
        return None
    return {
        "stop_units":int(stop),"target_units":int(target),"scale":scale,
        "stop_price":price_text(int(stop),scale),"target_price":price_text(int(target),scale),
        "pip_size":str(pip_size),
    }


def closing_trigger(units, levels, long):
    stop,target = levels["stop_units"],levels["target_units"]
    if (units<=stop if long else units>=stop):
        return "PROTECTIVE_STOP",stop
    if (units>=target if long else units<=target):
        return "REWARD_TARGET",target
    return None


def simulate_first_exit(entry, management, tape, digest, cutoff_ms):
    fill = entry["fill"]
    intent = entry["source"]
    frozen = management.model_dump(mode="json")
    identity = {"entry_result_id":entry["id"],"entry_fill_id":fill["id"],
                "management_policy_fingerprint":fingerprint(frozen),"execution_data_fingerprint":digest,
                "cutoff_ms":cutoff_ms,"policy":dict(POLICY)}
    identifier = "FX-"+fingerprint(identity)
    result = {"id":identifier,"identity":identity,"intent_id":intent["id"],"symbol":intent["symbol"],
              "direction":intent["direction"],"management_policy":frozen,"levels":None,"exit":None}
    unsupported = [field for field in UNSUPPORTED_MANAGEMENT if frozen[field] is not None]
    if management.initial_stop is None or management.target is None or unsupported:
        return result | {"status":"UNSUPPORTED_EXIT_MANAGEMENT","unsupported_rules":unsupported}

    scale = tape.scale
    long = intent["direction"]=="LONG"
    levels = protective_levels(fill["price_units"],scale,PIP_SIZE[tape.symbol],management,long)
    if levels is None:
        return result | {"status":"UNREPRESENTABLE_EXIT_LEVELS"}
    result["levels"] = levels

    entry_quote = Quote.model_validate({key:value for key,value in fill["quote"].items() if key!="scale"})
    start = fill["time_ms"]
    side = executable_side(not long)
    price_key = f"{side}_units"
    relevant = [entry_quote] + [quote for quote in tape.quotes
                                if start<=quote.time_ms and quote.available_at_ms<=cutoff_ms
                                and quote_priority(quote)>quote_priority(entry_quote)]
    candidates = ((quote,closing_trigger(getattr(quote,price_key),levels,long)) for quote in relevant)
    winner = min((candidate for candidate in candidates if candidate[1] is not None),
                 key=lambda item:quote_priority(item[0]),default=None)
    end = winner[0].available_at_ms if winner else cutoff_ms
    if not covered(tape.complete_delivery_intervals,start,end):
        return result | {"status":"INSUFFICIENT_QUOTE_COVERAGE"}
    if winner is None:
        return result | {"status":"OPEN_AT_CUTOFF"}
    quote, (reason,threshold) = winner
    priority = quote_priority(quote)
    if any(quote_priority(row)==priority and row!=quote for row in relevant):
        return result | {"status":"AMBIGUOUS_QUOTE_ORDER"}
    units = getattr(quote,price_key)
    reference = quote.model_dump(mode="json") | {"scale":scale}
    result["exit"] = {"id":"XF-"+fingerprint([identifier,reason,reference]),
                      "reason":reason,"threshold_units":threshold,"threshold_price":price_text(threshold,scale),
                      "side":side.upper(),"price_units":units,"price":price_text(units,scale),
                      "time_ms":quote.available_at_ms,"quote":reference}
    return result | {"status":"SIMULATED_EXIT"}


def validate_first_exit(run_id, *, cutoff_ms, entry_result_id=None, tape=None, tape_sha256=None):
    """Validate at most one exit for a specifically selected persisted entry result."""
    if type(cutoff_ms) is not int or not 0<=cutoff_ms<=32503680000000:
        raise ValueError("Exit cutoff must be a UTC epoch millisecond integer")
    tape,digest = pin_tape(tape,tape_sha256)
    with transaction() as db:
        db.execute("BEGIN IMMEDIATE")
        identity,model = verify_run(db,run_id)
        attempts,qualified,intents = published_entry_intents(db,run_id,identity,model)
        summary = {"run_id":run_id,"historical_attempts":attempts,"qualified_intents":qualified,
                   "simulated_orders":0,"simulated_fills":0,"simulated_exits":0,"results":[]}
        if entry_result_id is None:
            if qualified:
                raise ValueError("Select a persisted entry result for exit validation")
            return summary

        entry = verified_entry(db,run_id,entry_result_id,intents,model,tape,digest)
        summary["simulated_orders"] = int(entry["order"] is not None)
        if entry["status"]!="SIMULATED_FILLED":
            summary["results"] = [{"status":"NO_ENTRY_FILL","entry_result_id":entry_result_id,
                                   "entry_status":entry["status"],"exit":None}]
            return summary
        if cutoff_ms<entry["fill"]["time_ms"]:
            raise ValueError("Exit cutoff precedes the entry fill")

        result = simulate_first_exit(entry,model.management,tape,digest,cutoff_ms)
        payload = canonical(result)
        prior = db.execute("SELECT payload FROM offline_first_exit_results WHERE id=?",
                           (result["id"],)).fetchone()
        if prior:
            if prior["payload"]!=payload:
                raise ValueError("Offline exit result identity conflict")
        else:
            db.execute("INSERT INTO offline_first_exit_results VALUES (?,?,?)",
                       (result["id"],entry_result_id,payload))
        summary.update(simulated_fills=1,simulated_exits=int(result["exit"] is not None),results=[result])
        return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run",required=True)
    parser.add_argument("--entry-result")
    parser.add_argument("--cutoff-ms",required=True,type=int)
    parser.add_argument("--tape",type=Path)
    parser.add_argument("--sha256",help="SHA-256 of the canonical validated synthetic tape")
    args = parser.parse_args()
    try:
        tape = read_tape_file(args.tape) if args.tape else None
        initialize()
        result = validate_first_exit(args.run,entry_result_id=args.entry_result,cutoff_ms=args.cutoff_ms,
                                     tape=tape,tape_sha256=args.sha256)
    except (ValueError,OSError) as error:
        parser.exit(2,f"{error}\n")
    print(json.dumps(result,indent=2))


if __name__=="__main__":
    main()
