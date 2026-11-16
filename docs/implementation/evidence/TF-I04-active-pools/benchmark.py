"""Compare the reviewed liquidity stream with the additive live-book projection."""
from datetime import UTC, datetime
import json
from math import ceil
from pathlib import Path
from statistics import median
import subprocess
from tempfile import TemporaryDirectory
from time import perf_counter
import tracemalloc
from unittest.mock import patch

from research_backend.app import db, indicator_engine as engine, storage
from research_backend.app.indicator_history import load_reference
from research_backend.app.liquidity import LiquidityStream

OLD="a1e57d488aba7465fe578dce8934116da7357fa3"
legacy={"__name__":"legacy_liquidity","__package__":"research_backend.app"}
exec(subprocess.check_output(["git","show",OLD+":research_backend/app/liquidity.py"]),legacy)
START=int(datetime(2026,11,2,tzinfo=UTC).timestamp())
DENSE=[10,12,10,12,10,11,10,14,10,14,10,13]
CLUSTERS=[10,12,10,14,10,12,10,12,10,11]
RULES={"left":1,"right":1,"price_source":"wick"}
projection=[]


class TimedProjection(LiquidityStream):
    def current_projection(self,snapshot):
        start=perf_counter()
        page=super().current_projection(snapshot)
        projection.append((perf_counter()-start)*1000)
        return page


def fixture(count,shape):
    series=DENSE if shape=="dense" else CLUSTERS
    output=[]
    for i in range(count):
        price=series[i%len(series)]
        high=price
        if shape=="clusters" and i%10==7:
            high=14
        if (shape=="clusters" and i%10==9) or (shape=="dense" and i%12 in (5,11)):
            high=15
        output.append(dict(time=START+60*i,open=price,high=high,low=price,close=price))
    return output


def stats(values):
    return f"{median(values):.2f}/{sorted(values)[ceil(len(values)*.95)-1]:.2f}"


def measure(label,request,stream):
    with patch.object(engine,"LiquidityStream",stream):
        def call():
            return engine.evaluate(request,load_reference)["results"][0]
        call()
        projection.clear()
        calc,serial=[],[]
        for _ in range(10):
            start=perf_counter()
            result=call()
            calc.append((perf_counter()-start)*1000)
            now=perf_counter()
            data=json.dumps(result,separators=(",",":")).encode()
            serial.append((perf_counter()-now)*1000)
        measured=stats(projection) if projection else "n/a"
        tracemalloc.start()
        call()
        _,peak=tracemalloc.get_traced_memory()
        tracemalloc.stop()
    page=result.get("current_pools",{})
    chart_lines=sum(1 if pool["lower"]==pool["upper"] else 2 for pool in page.get("items",[])[:12]
                    if pool["state"]!="FORMING" and pool["activation_observation_time"]<request.cutoff-60)
    print(f"{label}: calc P50/P95 {stats(calc)} ms; projection {measured} ms; JSON {stats(serial)} ms; "
          f"{len(data)} bytes; Python peak {peak/1024:.1f} KiB")
    if page:
        print(f"  current total={page['total']} returned={page['returned']} truncated={page['truncated']}; "
              f"eligible live {sum(pool['state']!='FORMING' for pool in page['items'])}; "
              f"projected chart lines with all bars visible {chart_lines}")
    if result["provenance"].get("retrieval"):
        print(f"  {result['provenance']['retrieval']}")
    return result


def compare(label,request):
    before=measure(label+" before",request,legacy["LiquidityStream"])
    after=measure(label+" after",request,TimedProjection)
    assert before=={key:value for key,value in after.items() if key!="current_pools"}
    return after


print("Windows Python 3.13, 10 warm calls. Same TF-I04 fixtures. Python allocation sampled separately.")
bars=fixture(2000,"dense")
request=engine.Evaluation(source="DEMO",symbol="EURUSD",timeframe="M1",cutoff=START+120_000,candles=bars,
    indicators=[dict(id="liquidity_sweeps",structure=RULES)])
compare("2,000 caller, first history page",request)

with TemporaryDirectory() as root:
    db.DATABASE=Path(root)/"bench.sqlite"
    storage.MARKET_DIR=Path(root)/"market"
    db.initialize()
    bars=fixture(10000,"clusters")
    manifest=[]
    for offset in range(0,len(bars),2000):
        part=storage.write_partition("MT5","bench","EURUSD","candles","M1","BID",bars[offset:offset+2000])
        manifest.append(dict(id=part["id"],sha256=part["sha256"]))
    request=engine.Evaluation(source="MT5_SAVED",symbol="EURUSD",timeframe="M1",cutoff=START+600000,
        candles=[],feed_id="bench",price_basis="BID",partitions=manifest,frozen=True,
        indicators=[dict(id="liquidity_sweeps",structure={**RULES,"history_bars":10000,"history_start":START})])
    first=compare("10,000 archive, first history page",request)
    values=request.model_dump(exclude_unset=True)
    values["indicators"][0]["liquidity_delivery"]={
        "pool_cursor":first["liquidity_page"]["pools"]["next_cursor"],
        "sweep_cursor":first["liquidity_page"]["sweeps"]["next_cursor"]}
    compare("10,000 archive, next history page",engine.Evaluation.model_validate(values))
