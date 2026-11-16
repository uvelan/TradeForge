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


START = int(datetime(2026, 11, 2, tzinfo=UTC).timestamp())


def fixture(count, shape):
    result = []
    partial = [(99,100,98,99), (100,106,100,105), (105,110,105,108),
               (106,107,104,106), (106,107,102.5,106), (105,107,100,105)]
    for i in range(count):
        n = 100 + 2*i
        o,h,l,c = {"flat": (100,100,100,100), "dense": (n,n+1,n,n+1),
                   "partial": partial[i%len(partial)]}[shape]
        result.append(dict(time=START+60*i,open=o,high=h,low=l,close=c))
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
    page=value.get("fvg_page",value.get("liquidity_page",{}))
    counts={key:meta["total"] for key,meta in page.items() if isinstance(meta,dict)}
    print(f"{name}: calc P50/P95 {stats(times)} ms; JSON P50/P95 {stats(serial)} ms; "
          f"{len(data)} bytes; Python peak {peak/1024:.1f} KiB; {counts}")
    if value.get("provenance",{}).get("retrieval"):
        print(f"  {value['provenance']['retrieval']}")
    return value



print("Windows Python 3.13, 12 warm samples, tracemalloc separate. RSS not measured.")
print("FVG uses one attached primitive and zero additional chart series.")
for shape in ("flat", "dense", "partial"):
    bars = fixture(2000, shape)
    request = Evaluation(source="DEMO",symbol="EURUSD",timeframe="M1",cutoff=START+120000,
                         candles=bars,indicators=[{"id":"fair_value_gap"}])
    measure("2,000 "+shape+" FVG",lambda: evaluate(request)["results"][0])
    comparison = request.model_dump(exclude_unset=True)
    comparison["indicators"] = [{"id":"liquidity_sweeps"}]
    liquid = Evaluation.model_validate(comparison)
    measure("2,000 "+shape+" TF-I04",lambda: evaluate(liquid)["results"][0])
    if shape == "dense":
        comparison["indicators"] = [{"id":"fair_value_gap","fvg_delivery":{"current_limit":0}}]
        without = Evaluation.model_validate(comparison)
        measure("2,000 dense FVG, current projection disabled",lambda: evaluate(without)["results"][0])
        prior = request.model_copy(update={"cutoff":request.cutoff-60})
        before = evaluate(prior)["results"][0]
        measure("2,000 dense FVG, advance/rewind",lambda: (evaluate(request),evaluate(prior))[-1]["results"][0])
        assert evaluate(prior)["results"][0] == before

with TemporaryDirectory() as root:
    db.DATABASE=Path(root)/"bench.sqlite"
    storage.MARKET_DIR=Path(root)/"market"
    db.initialize()
    bars=fixture(10000,"partial")
    parts=[]
    for offset in range(0,len(bars),2000):
        item=storage.write_partition("MT5","bench","EURUSD","candles","M1","BID",bars[offset:offset+2000])
        parts.append(dict(id=item["id"],sha256=item["sha256"]))
    request = Evaluation(source="MT5_SAVED",symbol="EURUSD",timeframe="M1",cutoff=START+600000,candles=[],
        feed_id="bench",price_basis="BID",partitions=parts,frozen=True,
        indicators=[{"id":"fair_value_gap","fvg":{"history_bars":10000,"history_start":START}}])
    first=measure("10,000 partial archive FVG, first page",lambda: evaluate(request,load_reference)["results"][0])
    args=request.model_dump(exclude_unset=True)
    args["indicators"][0]["fvg_delivery"]={
        "formation_cursor":first["fvg_page"]["formations"]["next_cursor"],
        "revision_cursor":first["fvg_page"]["revisions"]["next_cursor"]}
    following=Evaluation.model_validate(args)
    measure("10,000 partial archive FVG, next page",lambda: evaluate(following,load_reference)["results"][0])
    args["indicators"]=[{"id":"liquidity_sweeps","structure":{"history_bars":10000,"history_start":START}}]
    liquid=Evaluation.model_validate(args)
    measure("10,000 partial archive TF-I04",lambda: evaluate(liquid,load_reference)["results"][0])
