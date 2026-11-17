"""Reproduce TF-I08 focused dependencies. Select source revision using PYTHONPATH."""
from datetime import UTC,datetime
import json
from math import ceil
from statistics import median
from time import perf_counter
import tracemalloc
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from research_backend.app import indicator_engine as engine
from research_backend.app.indicator_routes import router


START=int(datetime(2026,11,2,tzinfo=UTC).timestamp())
BASE_STRUCTURE={"algorithm_version":2,"left":3,"right":3}
SAMPLES=8


def candles():
    prices=[(100,101,99,100),(101,104,100,103),(102,103,100,101),(103,106,102,105),
            (104,106,103,105),(105,107,104,104),(103,104,98,99),(100,102,99,101)]
    return [dict(time=START+300*i,open=prices[i%8][0],high=prices[i%8][1],
                 low=prices[i%8][2],close=prices[i%8][3]) for i in range(2000)]


def inputs(specs):
    return engine.Evaluation(source="DEMO",symbol="EURUSD",timeframe="M5",price_basis="SIMULATED",
        feed_id="demo-v1",candles=candles(),cutoff=START+300*2000,indicators=specs)


def percentile(times):
    return f"{median(times):.2f}/{sorted(times)[ceil(.95*len(times))-1]:.2f}"


def counts(request):
    names={"structure":"detect_structure","fvg":"evaluate_fvg","liquidity":"LiquidityStream",
           "order_blocks":"OrderBlockStream"}
    with patch.multiple(engine,**{symbol:Mock(wraps=getattr(engine,symbol))
                                  for symbol in names.values()}):
        value=engine.evaluate(request)
        return {name:getattr(engine,symbol).call_count for name,symbol in names.items()},value.get("diagnostics",{})


def measure(name,specs,client):
    request=inputs(specs)
    raw=request.model_dump_json(exclude_unset=True)
    times,serial,http=[],[],[]
    for _ in range(SAMPLES):
        start=perf_counter()
        value=engine.evaluate(request)
        times.append((perf_counter()-start)*1000)
        start=perf_counter()
        result=json.dumps(value,separators=(",",":")).encode()
        serial.append((perf_counter()-start)*1000)
        start=perf_counter()
        response=client.post("/api/indicators/evaluate",content=raw,headers={"content-type":"application/json"})
        http.append((perf_counter()-start)*1000)
        assert response.status_code==200,response.text
    tracemalloc.start()
    engine.evaluate(request)
    _,peak=tracemalloc.get_traced_memory()
    tracemalloc.stop()
    executions,diagnostics=counts(request)
    print(f"{name} | calc {percentile(times)} | JSON {percentile(serial)} | API {percentile(http)} | "
          f"bytes {len(result)} | peak {peak/1024:.1f} KiB | {executions} | reuse {diagnostics.get('reuse',{})}",flush=True)
    return request


def main():
    structure={"id":"market_structure","structure":BASE_STRUCTURE}
    liquidity={"id":"liquidity_sweeps","structure":BASE_STRUCTURE}
    order_blocks={"id":"order_blocks","structure":BASE_STRUCTURE}
    fvg={"id":"fair_value_gap"}
    crt={"id":"candle_range_theory"}
    crt_context={"id":"candle_range_theory","crt":{"include_context":True}}
    ob_context={**order_blocks,"ob":{"include_context":True}}
    app=FastAPI()
    app.include_router(router)
    print("Eight samples, Windows. Milliseconds. ASGI TestClient route latency excludes network.",flush=True)
    with TestClient(app) as client:
        for spec in (structure,liquidity,fvg,order_blocks,crt,crt_context,ob_context):
            tag=spec["id"]+(" context" if spec.get("crt") or spec.get("ob") else "")
            measure(tag,[spec],client)
        measure("structure + liquidity + OB",[structure,liquidity,order_blocks],client)
        measure("five engines compatible",[structure,liquidity,fvg,order_blocks,crt],client)
        different={**liquidity,"structure":{**BASE_STRUCTURE,"left":5,"right":5}}
        measure("five engines differing",[structure,different,fvg,order_blocks,crt],client)
        request=measure("five engines context enabled",[structure,liquidity,fvg,ob_context,crt_context],client)
        costs=[]
        for _ in range(SAMPLES):
            start=perf_counter()
            for offset in (900,600,300,0,300,600):
                engine.evaluate(request.model_copy(update={"cutoff":request.cutoff-offset}))
            costs.append((perf_counter()-start)*1000)
        print("Three forward steps plus two rewinds with starting evaluation, calculation P50/P95",percentile(costs),flush=True)


if __name__=="__main__":
    main()
