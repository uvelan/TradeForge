"""TF-I06 benchmark. Run with PYTHONPATH=. from the repository root."""
from datetime import UTC, datetime
import json
from math import ceil
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory
from time import perf_counter
import tracemalloc

from research_backend.app import db,storage
from research_backend.app.indicator_engine import Evaluation,evaluate
from research_backend.app.indicator_history import load_reference


START=int(datetime(2026,11,2,tzinfo=UTC).timestamp())
STRUCTURE={"left":1,"right":1,"algorithm_version":2}


def fixture(count,shape):
    rows=[]
    width=5 if shape=="partial" else 4
    for i in range(count):
        cycle,position=divmod(i,width)
        n=9+5*cycle
        options=[(n,n+1,n-1,n),(n+2,n+4,n+1,n+3),(n+2,n+3,n,n+1),
                 (n+3,n+6,n+2,n+5),(n+5,n+5,n+2,n+5)]
        o,h,l,c=(100,100,100,100) if shape=="flat" else options[position]
        rows.append(dict(time=START+60*i,open=o,high=h,low=l,close=c))
    return rows


def measure(label,call):
    call()
    times,serialization=[],[]
    for _ in range(12):
        start=perf_counter()
        value=call()
        times.append((perf_counter()-start)*1000)
        start=perf_counter()
        data=json.dumps(value,separators=(",",":")).encode()
        serialization.append((perf_counter()-start)*1000)
    tracemalloc.start()
    call()
    _,peak=tracemalloc.get_traced_memory()
    tracemalloc.stop()

    def stats(samples):
        return f"{median(samples):.2f}/{sorted(samples)[ceil(.95*len(samples))-1]:.2f}"

    page=value.get("ob_page",value.get("fvg_page",{}))
    counts={key:meta["total"] for key,meta in page.items() if isinstance(meta,dict)}
    current=value.get("current_obs",value.get("current_fvgs",{}))
    counters=value.get("provenance",{}).get("retrieval")
    print(f"{label}: calc {stats(times)} ms; JSON {stats(serialization)} ms; {len(data)} bytes; "
          f"peak {peak/1024:.1f} KiB; {counts}; current={current.get('total')}")
    if counters:
        print(f"  {counters}")
    return value


def project(request,loader=None):
    return evaluate(request,loader)["results"][0]


def request_for(bars,kind,**options):
    params={"source":"DEMO","symbol":"EURUSD","timeframe":"M1","cutoff":bars[-1]["time"]+60,
            "candles":bars,"indicators":[kind]}
    params.update(options)
    return Evaluation.model_validate(params)


print("Windows Python 3.13.12 warm samples. Tracemalloc sampled separately. Native RSS not measured.")
print("OB and FVG share one price-zone primitive; OB adds zero chart series and at most 12 rectangles.")
for shape in ("flat","dense","partial"):
    rows=fixture(2000,shape)
    kind={"id":"order_blocks","structure":STRUCTURE,"ob":{"max_age_bars":500}}
    ob=request_for(rows,kind)
    fvg=request_for(rows,{"id":"fair_value_gap","fvg":{"max_age_bars":500}})
    measure(f"2,000 {shape} OB",lambda:project(ob))
    measure(f"2,000 {shape} FVG",lambda:project(fvg))
    if shape=="dense":
        no_current=request_for(rows,{**kind,"ob_delivery":{"current_limit":0}})
        measure("2,000 dense OB, current delivery disabled",lambda:project(no_current))
        prior=ob.model_copy(update={"cutoff":ob.cutoff-60})
        reference=project(prior)
        measure("2,000 dense OB, advance + rewind",lambda:(project(ob),project(prior))[1])
        assert project(prior)==reference

with TemporaryDirectory() as root:
    root=Path(root)
    db.DATABASE=root/"bench.sqlite"
    storage.MARKET_DIR=root/"market"
    db.initialize()
    rows=fixture(10000,"partial")
    parts=[]
    for offset in range(0,len(rows),2000):
        part=storage.write_partition("MT5","ob-bench","EURUSD","candles","M1","BID",rows[offset:offset+2000])
        parts.append({key:part[key] for key in ("id","sha256")})
    source={"source":"MT5_SAVED","feed_id":"ob-bench","price_basis":"BID","partitions":parts,
            "frozen":True,"candles":[]}
    archive={"history_bars":10000,"history_start":START}
    kind={"id":"order_blocks","structure":{**STRUCTURE,**archive},"ob":{"max_age_bars":500}}
    ob=request_for(rows,kind,**source)
    first=measure("10,000 partial archive OB, first page",lambda:project(ob,load_reference))
    paged={**kind,"ob_delivery":{
        "formation_cursor": first["ob_page"]["formations"]["next_cursor"],
        "revision_cursor": first["ob_page"]["revisions"]["next_cursor"]}}
    following=request_for(rows,paged,**source)
    measure("10,000 partial archive OB, next page",lambda:project(following,load_reference))
    kind={"id":"fair_value_gap","fvg":{**archive,"max_age_bars":500}}
    fvg=request_for(rows,kind,**source)
    measure("10,000 partial archive FVG, first page",lambda:project(fvg,load_reference))
