"""Offline partial exit over normalized exposure; independent of account quantities."""
from fractions import Fraction
from itertools import groupby

from .offline_market_entry import covered, quote_priority
from .strategy_evidence import fingerprint


def ratio(value):
    return {"numerator":value.numerator,"denominator":value.denominator}


def simulate_partial_exit(*, result, identity, partial, fill, levels, long, side,
                          relevant, tape, cutoff_ms, closing_trigger, price_text):
    start = fill["time_ms"]
    scale = tape.scale
    entry_units = fill["price_units"]
    stop, target = levels["stop_units"], levels["target_units"]
    threshold = entry_units + (entry_units-stop)*Fraction(str(partial.at_r))
    if threshold.denominator!=1 or not min(entry_units,target)<threshold<max(entry_units,target):
        return result | {"status":"UNREPRESENTABLE_EXIT_LEVELS"}

    fraction = Fraction(str(partial.percent))/100
    remaining = Fraction(1)
    result.update(partial_threshold_units=int(threshold),partial_exit=None,remaining_fraction=ratio(remaining))
    stable_identity = {key:value for key,value in identity.items() if key!="cutoff_ms"}
    price_key = f"{side}_units"

    def event(quote, units, kind, trigger_level, closed):
        reference = quote.model_dump(mode="json") | {"scale":scale}
        return {"id":"XF-"+fingerprint([stable_identity,kind,reference]),
                "reason":kind,"threshold_units":trigger_level,"threshold_price":price_text(trigger_level,scale),
                "side":side.upper(),"price_units":units,"price":price_text(units,scale),
                "time_ms":quote.available_at_ms,"quote":reference,"closed_fraction":ratio(closed)}

    for _, group in groupby(sorted(relevant,key=quote_priority),key=quote_priority):
        rows = list(group)
        for quote in rows:
            units = getattr(quote,price_key)
            terminal = closing_trigger(units,stop,target,long)
            reduce = result["partial_exit"] is None and (units>=threshold if long else units<=threshold)
            if terminal or reduce:
                break
        else:
            continue
        if not covered(tape.complete_delivery_intervals,start,quote.available_at_ms):
            return result | {"status":"INSUFFICIENT_QUOTE_COVERAGE"}
        if any(row!=quote for row in rows):
            return result | {"status":"AMBIGUOUS_QUOTE_ORDER"}
        # A gap through both thresholds does not prove an intervening partial fill.
        if terminal:
            reason, trigger_level = terminal
            result["exit"] = event(quote,units,reason,trigger_level,remaining)
            result["remaining_fraction"] = ratio(Fraction(0))
            return result | {"status":"SIMULATED_EXIT"}
        result["partial_exit"] = event(quote,units,"PARTIAL_REWARD",int(threshold),fraction)
        remaining -= fraction
        result["remaining_fraction"] = ratio(remaining)

    if not covered(tape.complete_delivery_intervals,start,cutoff_ms):
        return result | {"status":"INSUFFICIENT_QUOTE_COVERAGE"}
    return result | {"status":"OPEN_AT_CUTOFF"}
