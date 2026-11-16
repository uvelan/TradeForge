"""Run from the repository root with the local Python virtual environment."""
from datetime import UTC, datetime
import json
from math import ceil
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory
from time import perf_counter
import tracemalloc

from research_backend.app import db, storage
from research_backend.app.indicator_engine import Evaluation, evaluate
from research_backend.app.indicator_history import load_reference

START=int(datetime(2026,11,2,tzinfo=UTC).timestamp())
RULES={"left":1,"right":1,"price_source":"wick"}
DENSE=[10,12,10,12,10,11,10,14,10,14,10,13]
CLUSTERS=[10,12,10,14,10,12,10,12,10,11]


def fixture(count,shape):
    result=[]
    for i in range(count):
        price=10 if shape=="flat" else (CLUSTERS if shape=="clusters" else DENSE)[i%12 if shape!="clusters" else i%10]
        high=price
        if shape=="clusters" and i%10==7:
            high=14
        if (shape=="clusters" and i%10==9) or (shape=="dense" and i%12 in (5,11)):
            high=15
        time=START+60*i*(3 if shape=="sparse" else 1)
        result.append(dict(time=time,open=price,high=high,low=price,close=price))
    return result


def measure(name,call):
    call()
    times,serial=[],[]
    for _ in range(12):
        start=perf_counter()
        value=call()
        times.append((perf_counter()-start)*1000)
        clock=perf_counter()
        data=json.dumps(value,separators=(",",":")).encode()
        serial.append((perf_counter()-clock)*1000)
    tracemalloc.start()
    call()
    _,peak=tracemalloc.get_traced_memory()
    tracemalloc.stop()
    def stats(x):
        return f"{median(x):.2f}/{sorted(x)[ceil(len(x)*.95)-1]:.2f}"
    page=value.get("liquidity_page",value.get("event_page",{}))
    counts={key:meta["total"] for key,meta in page.items() if isinstance(meta,dict)}
    print(f"{name}: calc P50/P95 {stats(times)} ms; JSON P50/P95 {stats(serial)} ms; "
          f"{len(data)} bytes; Python peak {peak/1024:.1f} KiB; {counts}")
    if value.get("provenance",{}).get("retrieval"):
        print(f"  {value['provenance']['retrieval']}")
    return value


print("Windows Python 3.13, 12 warm samples; Python allocation sampled separately; native RSS not measured")
for shape in ("dense","sparse","clusters","flat"):
    bars=fixture(2000,shape)
    request=Evaluation(source="DEMO",symbol="EURUSD",timeframe="M1",cutoff=bars[-1]["time"]+60,
        candles=bars,indicators=[{"id":"liquidity_sweeps","structure":RULES}])
    measure("2,000 "+shape,lambda: evaluate(request)["results"][0])
    if shape=="dense":
        base=request.model_dump()
        base["indicators"]=[{"id":"market_structure","structure":RULES}]
        comparison=Evaluation.model_validate(base)
        measure("2,000 dense, structure-only comparison",lambda: evaluate(comparison)["results"][0])
        old=request.model_copy(update={"cutoff":request.cutoff-60})
        first=evaluate(old)["results"][0]
        measure("2,000 dense, advance/rewind",lambda: (evaluate(request),evaluate(old))[-1]["results"][0])
        assert evaluate(old)["results"][0]==first

with TemporaryDirectory() as root:
    db.DATABASE=Path(root)/"bench.sqlite"
    storage.MARKET_DIR=Path(root)/"market"
    db.initialize()
    bars=fixture(10000,"clusters")
    parts=[]
    for offset in range(0,len(bars),2000):
        item=storage.write_partition("MT5","bench","EURUSD","candles","M1","BID",bars[offset:offset+2000])
        parts.append(dict(id=item["id"],sha256=item["sha256"]))
    request=Evaluation(source="MT5_SAVED",symbol="EURUSD",timeframe="M1",cutoff=START+600000,candles=[],
        feed_id="bench",price_basis="BID",partitions=parts,frozen=True,
        indicators=[{"id":"liquidity_sweeps","structure":{**RULES,"history_bars":10000,"history_start":START}}])
    first=measure("10,000 clusters archive, first page",lambda: evaluate(request,load_reference)["results"][0])
    args=request.model_dump(exclude_unset=True)
    args["indicators"][0]["liquidity_delivery"]={
        "pool_cursor":first["liquidity_page"]["pools"]["next_cursor"],
        "sweep_cursor":first["liquidity_page"]["sweeps"]["next_cursor"]}
    following=Evaluation.model_validate(args)
    measure("10,000 clusters archive, next page",lambda: evaluate(following,load_reference)["results"][0])
