"""Run this fixture from repository root. See TF-I03-REVIEW.md."""
from datetime import UTC, datetime
import json
from math import ceil
from pathlib import Path
from statistics import median
import subprocess
from tempfile import TemporaryDirectory
from time import perf_counter
import tracemalloc

from research_backend.app import db, storage
from research_backend.app.indicator_engine import Evaluation, EvidenceBar, evaluate
from research_backend.app.indicator_history import load_reference

BASE = "bb84f60a539fe962d56de6a819ba04a57151d0c6"
legacy = {"__name__": "legacy_structure", "__package__": "research_backend.app"}
exec(subprocess.check_output(["git", "show", BASE+":research_backend/app/market_structure.py"]), legacy)
start = int(datetime(2026, 11, 2, tzinfo=UTC).timestamp())
friday = int(datetime(2026,11,6,22,tzinfo=UTC).timestamp())
sunday = int(datetime(2026,11,8,22,tzinfo=UTC).timestamp())
pattern = [10,12,10,13,11,14,12,15,13,10,11]
basic = {"left": 1, "right": 1, "price_source": "close"}


def fixture(count, kind):
    result = []
    for i in range(count):
        stamp = start+i*60
        if kind == "missing":
            stamp += i*60
        if kind == "weekend":
            stamp = friday+(i-count//2)*60 if i<count//2 else sunday+(i-count//2)*60
        price = 10 if kind == "flat" else pattern[i%len(pattern)]
        result.append(dict(time=stamp,open=price,high=price,low=price,close=price))
    return result


def measure(call):
    call()
    calc, encode = [], []
    for _ in range(12):
        now = perf_counter()
        result = call()
        calc.append((perf_counter()-now)*1000)
        now = perf_counter()
        data = json.dumps(result, separators=(",", ":")).encode()
        encode.append((perf_counter()-now)*1000)
    tracemalloc.start()
    call()
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    def stats(values):
        return f"{median(values):.2f}/{sorted(values)[ceil(.95*len(values))-1]:.2f}"
    page = result.get("event_page", {})
    counts = [page[kind]["total"] if page else len(result[kind]) for kind in ("pivots","breaks")]
    print(f"calc p50/p95 {stats(calc)} ms, JSON p50/p95 {stats(encode)} ms, "
          f"{len(data)} bytes, peak {peak/1024:.1f} KiB, totals {counts}, "
          f"returned {[len(result[key]) for key in ('pivots','breaks')]}")
    return result


print("Windows Python 3.13, warm filesystem, 12 samples per case; scoped structure-result JSON; RSS not measured")
for shape in ("dense","flat","weekend","missing"):
    rows = fixture(2000,shape)
    prefix = [(EvidenceBar.model_validate(row),row["time"]+60) for row in rows]
    market = {"kind":"forex_weekly"} if shape=="weekend" else {"kind":"continuous"}
    rules = {**basic,"algorithm_version":2,"market_schedule":market}
    request = Evaluation(source="DEMO",symbol="EURUSD",timeframe="M1",
        cutoff=rows[-1]["time"]+60,candles=rows,indicators=[dict(id="market_structure",structure=rules)])
    print(f"2,000 {shape}, TF-I02 unbounded detector")
    measure(lambda: legacy["detect_structure"](prefix,60,legacy["StructureRules"](**basic),{}))
    print(f"2,000 {shape}, TF-I03 latest page")
    measure(lambda: evaluate(request)["results"][0])

with TemporaryDirectory() as root:
    db.DATABASE=Path(root)/"bench.sqlite"
    storage.MARKET_DIR=Path(root)/"market"
    db.initialize()
    rows=fixture(10_000,"dense")
    for offset in range(0,len(rows),2000):
        storage.write_partition("MT5","dense","EURUSD","candles","M1","BID",rows[offset:offset+2000])
    rules={**basic,"history_bars":10_000,"history_start":start}
    request=Evaluation(source="MT5_SAVED",symbol="EURUSD",timeframe="M1",price_basis="BID",feed_id="dense",
        cutoff=rows[-1]["time"]+60,candles=[],indicators=[dict(id="market_structure",
            structure={**rules,"algorithm_version":2,"market_schedule":{"kind":"continuous"}})])
    print("10,000 archive TF-I02 unbounded detector + verified read")
    measure(lambda: legacy["detect_structure"](load_reference(request,start,request.cutoff,60)["rows"],
        60,legacy["StructureRules"](**rules),{}))
    print("10,000 archive TF-I03 first page + verified read")
    first=measure(lambda: evaluate(request,load_reference)["results"][0])
    print("10,000 archive TF-I03 next page + verified read")
    args=request.model_dump()
    args["indicators"][0]["structure_delivery"]={
        "pivot_cursor":first["event_page"]["pivots"]["next_cursor"],
        "break_cursor":first["event_page"]["breaks"]["next_cursor"]}
    follow=Evaluation.model_validate(args)
    measure(lambda: evaluate(follow,load_reference)["results"][0])
    print(first["provenance"]["retrieval"])
