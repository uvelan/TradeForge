"""Reproduce v1 and v2 rolling IDs and time identical requests from the repo root."""
import json
from math import ceil
from statistics import median
from subprocess import check_output
import sys
from time import perf_counter
import tracemalloc
from types import ModuleType

from research_backend.app import fvg
from research_backend.app.indicators import Bar

BASE = "71f5458ecb790d6e656329c28aec10281d4d999f"
FLAT = (99,100,98,99)
ABOVE = (106,107,106,106)
ABC = [(99,100,98,99),(100,106,100,105),(105,110,105,108)]
VALUES = [FLAT]*1800 + ABC + [(106,107,103.75,106)] + [ABOVE]*296


def load_baseline():
    name = "research_backend.app._fvg_identity_baseline"
    module = sys.modules[name] = ModuleType(name)
    module.__package__ = "research_backend.app"
    code = check_output(["git","show",f"{BASE}:research_backend/app/fvg.py"])
    exec(compile(code, "reviewed-fvg.py", "exec"), module.__dict__)
    return module


def evaluate(engine, advance, values=VALUES, **options):
    rows = [(Bar(time=i*60,open=o,high=h,low=l,close=c),(i+1)*60)
            for i,(o,h,l,c) in enumerate(values[advance:advance+2000],start=advance)]
    return engine.evaluate_fvg(rows,60,(advance+2000)*60,
                               engine.FVGRules(max_age_bars=500,**options),
                               engine.FVGDelivery(),{"feed_id":"fixture"},False)


def target(result):
    return next(zone for zone in result["current_fvgs"]["items"] if zone["c_time"]==1802*60)


def metrics(engine):
    elapsed,serialization=[],[]
    for advance in range(1,13):
        start=perf_counter()
        value=evaluate(engine,advance)
        elapsed.append((perf_counter()-start)*1000)
        start=perf_counter()
        payload=json.dumps(value,separators=(",",":")).encode()
        serialization.append((perf_counter()-start)*1000)
    tracemalloc.start()
    evaluate(engine,12)
    _,peak=tracemalloc.get_traced_memory()
    tracemalloc.stop()
    def stats(samples):
        return f"{median(samples):.2f}/{sorted(samples)[ceil(.95*len(samples))-1]:.2f}"
    print(f"P50/P95 calculation {stats(elapsed)} ms; JSON {stats(serialization)} ms; {len(payload)} bytes; Python peak {peak/1024:.1f} KiB")


if __name__=="__main__":
    for label,engine in (("policy v1",load_baseline()),("policy v2",fvg)):
        print(label)
        for advance in (0,1,100):
            result=evaluate(engine,advance)
            zone=target(result)
            active=next(event for event in result["formations"] if event["c_time"]==zone["c_time"])
            print(f"advance={advance} zone={zone['fvg_id']} formation={active['id']} revision={zone['id']}")
            print(f"  depth={zone['mitigation_pct']} timeframe=M1 snapshot={result['fvg_page']['snapshot']}")
        metrics(engine)
    corrected=VALUES.copy()
    corrected[1801]=(100,106,100,104)
    corrected_result=evaluate(fvg,0,corrected)
    print("Correction to B.close, 105 to 104:",target(corrected_result)["fvg_id"],target(corrected_result)["id"])
