"""Offline first-exit validation with fixed levels and optional research management rules."""
import argparse
from bisect import bisect_right
import json
from decimal import Decimal
from fractions import Fraction
from itertools import groupby
from pathlib import Path

from .analysis_evidence import canonical
from .db import initialize, transaction
from .offline_market_entry import (
    Quote, covered, pin_tape, published_entry_intents, quote_priority,
    read_tape_file, simulate, verify_run,
)
from .offline_partial_exit import ratio
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
BREAK_EVEN_POLICY = POLICY | {
    "id":"OFFLINE_BREAK_EVEN_FIRST_EXIT","version":2,
    "stop_clock":"MOVE_DELIVERY_THROUGH_OBSERVATION_STRICT_LATER_PRIORITY",
    "stop_activation":"EXIT_FIRST_THEN_ARM_AT_R_EFFECTIVE_NEXT_PRIORITY",
    "armed_stop":"VERIFIED_ENTRY_PRICE_BEFORE_FEES",
}
TRAILING_POLICY = POLICY | {
    "id":"OFFLINE_TRAILING_FIRST_EXIT","version":2,
    "stop_clock":"MOVE_DELIVERY_THROUGH_OBSERVATION_STRICT_LATER_PRIORITY",
    "stop_update":"EXIT_FIRST_THEN_RATCHET_FROM_CLOSING_QUOTE_EFFECTIVE_NEXT_PRIORITY",
    "distance":"EXACT_PIPS_NO_ROUNDING",
}
TIME_POLICY = POLICY | {
    "id":"OFFLINE_ELAPSED_TIME_FIRST_EXIT",
    "deadline":"FILL_DELIVERY_PLUS_ELAPSED_SECONDS",
    "execution_at_deadline":"PROTECTION_FIRST_THEN_FIRST_CLOSING_QUOTE_OBSERVED_AT_OR_AFTER_DEADLINE",
}
PARTIAL_POLICY = POLICY | {
    "id":"OFFLINE_PARTIAL_EXIT",
    "precedence":"TERMINAL_QUOTE_FIRST_THEN_ONE_PARTIAL",
    "quantity":"EXACT_FRACTION_OF_INITIAL_NORMALIZED_EXPOSURE",
}
COMBINED_POLICY = POLICY | {
    "id":"OFFLINE_COMBINED_MANAGEMENT",
    "precedence":"OBSERVATION_STOP_TARGET_TIME_THEN_PARTIAL_THEN_TIGHTEST_MOVE",
}


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
    expected = simulate(intent,model,tape,digest,policy=identity["policy"])
    if canonical(entry)!=canonical(expected):
        raise ValueError("Persisted entry does not match verified replay")
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


def closing_trigger(units, stop, target, long):
    if (units<=stop if long else units>=stop):
        return "PROTECTIVE_STOP",stop
    if (units>=target if long else units<=target):
        return "REWARD_TARGET",target
    return None


def simulate_first_exit(entry, management, tape, digest, cutoff_ms):
    fill = entry["fill"]
    intent = entry["source"]
    frozen = management.model_dump(mode="json")
    activation_r = management.break_even_at_r
    partial = management.partial_exit
    trailing = management.trailing_pips
    timer = management.time_exit
    duration = timer.count if timer and timer.unit=="SECONDS" and not timer.timeframe else None
    policies = [policy for value,policy in ((activation_r,BREAK_EVEN_POLICY),(partial,PARTIAL_POLICY),
                (trailing,TRAILING_POLICY),(duration,TIME_POLICY)) if value is not None]
    policy = COMBINED_POLICY if len(policies)>1 else next(iter(policies),POLICY)
    identity = {"entry_result_id":entry["id"],"entry_fill_id":fill["id"],
                "management_policy_fingerprint":fingerprint(frozen),"execution_data_fingerprint":digest,
                "cutoff_ms":cutoff_ms,"policy":dict(policy)}
    identifier = "FX-"+fingerprint(identity)
    result = {"id":identifier,"identity":identity,"intent_id":intent["id"],"symbol":intent["symbol"],
              "direction":intent["direction"],"management_policy":frozen,"levels":None,"exit":None}
    unsupported = ["time_exit"] if timer and duration is None else []
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
    deadline = None if duration is None else start+duration*1000
    if deadline is not None:
        result["deadline_ms"] = deadline
    side = executable_side(not long)
    price_key = f"{side}_units"
    relevant = [entry_quote] + [quote for quote in tape.quotes
                                if start<=quote.time_ms and quote.available_at_ms<=cutoff_ms
                                and quote_priority(quote)>quote_priority(entry_quote)]
    tracks_exposure = partial is not None or policy==COMBINED_POLICY
    remaining = Fraction(1)
    if policy==COMBINED_POLICY:
        result["remaining_fraction"] = ratio(remaining)
    partial_threshold = None
    if partial:
        threshold = fill["price_units"]+(fill["price_units"]-levels["stop_units"])*Fraction(str(partial.at_r))
        if threshold.denominator!=1 or not min(fill["price_units"],levels["target_units"])<threshold<max(fill["price_units"],levels["target_units"]):
            return result | {"status":"UNREPRESENTABLE_EXIT_LEVELS"}
        partial_threshold = int(threshold)
        # Partial-only V1 records exposure only after validating its threshold.
        result.update(partial_threshold_units=partial_threshold,partial_exit=None,remaining_fraction=ratio(remaining))
    active_stop = levels["stop_units"]
    if policy==COMBINED_POLICY:
        result["active_stop_units"] = active_stop
    target = levels["target_units"]
    arm_threshold = None
    event_identity = {key:value for key,value in identity.items() if key!="cutoff_ms"}
    if activation_r is not None:
        entry_units = fill["price_units"]
        threshold = entry_units + (entry_units-active_stop)*Fraction(str(activation_r))
        if threshold.denominator!=1:
            return result | {"status":"UNREPRESENTABLE_EXIT_LEVELS"}
        arm_threshold = int(threshold)
        result.update(arm_threshold_units=arm_threshold,active_stop_units=active_stop,activation=None)

    trail_distance = None
    if trailing is not None:
        distance = Fraction(str(trailing))*Fraction(PIP_SIZE[tape.symbol])*scale
        if distance.denominator!=1 or not 0<distance<=10**12:
            return result | {"status":"UNREPRESENTABLE_EXIT_LEVELS"}
        trail_distance = int(distance)
        result.update(trailing_distance_units=trail_distance,active_stop_units=active_stop,ratchet=None)

    def event(reason,threshold,quote):
        reference = quote.model_dump(mode="json") | {"scale":scale}
        units = getattr(quote,price_key)
        return {"id":"XF-"+fingerprint([event_identity,reason,reference]),"reason":reason,
                "threshold_units":threshold,"threshold_price":None if threshold is None else price_text(threshold,scale),
                "side":side.upper(),"price_units":units,"price":price_text(units,scale),
                "time_ms":quote.available_at_ms,"quote":reference}

    # Only previously processed priorities enter the timeline. At equal milliseconds
    # bisect_right includes all moves already delivered at that observation time.
    move_times = [start]
    stops = [(active_stop,"PROTECTIVE_STOP",fill["quote"])]
    for _,group in groupby(sorted(relevant,key=quote_priority),key=quote_priority):
        rows = list(group)
        for quote in rows:
            units = getattr(quote,price_key)
            applicable,stop_reason,stop_quote = stops[bisect_right(move_times,quote.time_ms)-1]
            trigger = closing_trigger(units,applicable,target,long)
            if trigger is None and deadline is not None and quote.time_ms>=deadline:
                trigger = "TIME_EXIT",None
            arms = arm_threshold is not None and (units>=arm_threshold if long else units<=arm_threshold)
            trail = (units-trail_distance if long else units+trail_distance) if trail_distance else None
            tightens = (trail is not None and quote!=entry_quote
                        and (trail>active_stop if long else trail<active_stop))
            reduce = partial_threshold is not None and (units>=partial_threshold if long else units<=partial_threshold)
            if trigger or reduce or arms or tightens:
                break
        else:
            continue
        if not covered(tape.complete_delivery_intervals,start,quote.available_at_ms):
            return result | {"status":"INSUFFICIENT_QUOTE_COVERAGE"}
        if any(row!=quote for row in rows):
            return result | {"status":"AMBIGUOUS_QUOTE_ORDER"}
        reference = quote.model_dump(mode="json") | {"scale":scale}
        if trigger:
            reason,threshold = trigger
            stop_hit = reason=="PROTECTIVE_STOP"
            final = event(stop_reason if stop_hit else reason,threshold,quote)
            if stop_hit and (trailing or activation_r or policy==COMBINED_POLICY):
                final["stop_quote"] = stop_quote
            if tracks_exposure:
                final["closed_fraction"] = ratio(remaining)
                result["remaining_fraction"] = ratio(Fraction(0))
            result["exit"] = final
            return result | {"status":"SIMULATED_EXIT"}

        if reduce:
            closed = Fraction(str(partial.percent))/100
            result["partial_exit"] = event("PARTIAL_REWARD",partial_threshold,quote) | {"closed_fraction":ratio(closed)}
            remaining -= closed
            result["remaining_fraction"] = ratio(remaining)
            partial_threshold = None

        new_stop = active_stop
        if arms:
            result["activation"] = {"id":"BA-"+fingerprint([event_identity,reference]),
                                    "side":side.upper(),"quote":reference}
            arm_threshold = None
            improves = fill["price_units"]>active_stop if long else fill["price_units"]<active_stop
            if improves:
                new_stop,reason = fill["price_units"],"BREAK_EVEN_STOP"
        if tightens and (trail>new_stop if long else trail<new_stop):
            new_stop,reason = trail,"TRAILING_STOP"
            result["ratchet"] = {"id":"TR-"+fingerprint([event_identity,trail,reference]),
                                 "stop_units":trail,"side":side.upper(),"quote":reference}
        if new_stop!=active_stop:
            active_stop = new_stop
            result["active_stop_units"] = active_stop
            move_times.append(quote.available_at_ms)
            stops.append((active_stop,reason,reference))
    if not covered(tape.complete_delivery_intervals,start,cutoff_ms):
        return result | {"status":"INSUFFICIENT_QUOTE_COVERAGE"}
    status = "AWAITING_TIME_EXIT_QUOTE" if deadline is not None and cutoff_ms>=deadline else "OPEN_AT_CUTOFF"
    return result | {"status":status}


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
        if cutoff_ms<entry["source"]["qualified_at"]*1000:
            raise ValueError("Exit cutoff precedes intent qualification")
        if entry["fill"] is not None and cutoff_ms<entry["fill"]["time_ms"]:
            raise ValueError("Exit cutoff precedes the entry fill")
        visible = simulate(entry["source"],model,tape,digest,policy=entry["identity"]["policy"],
                           cutoff_ms=cutoff_ms)
        summary["simulated_orders"] = int(visible["order"] is not None)
        if visible["fill"] is None:
            summary["results"] = [{"status":"NO_ENTRY_FILL","entry_result_id":entry_result_id,
                                   "entry_status":visible["status"],"exit":None}]
            return summary
        if visible["fill"]!=entry["fill"]:
            raise ValueError("Cutoff replay fill does not match the verified entry")

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
        if model.management.partial_exit is not None:
            summary["simulated_partial_exits"] = int(result.get("partial_exit") is not None)
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
