"""CRT benchmark. Run from repository root with PYTHONPATH=."""
from datetime import UTC, datetime
import json
from math import ceil
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory
from time import perf_counter
import tracemalloc

from fastapi import FastAPI
from fastapi.testclient import TestClient
from research_backend.app import db, storage
from research_backend.app.indicator_engine import Evaluation, evaluate
from research_backend.app.indicator_history import load_reference
from research_backend.app.indicator_routes import router


START=int(datetime(2026,11,2,tzinfo=UTC).timestamp())
SAMPLES=10


def candles(count,step,group,frequent):
    result=[]
    for index in range(count):
        price=100+(index//group)%3
        high=price+5
        low=price
        if frequent and index%group==0 and index:
            high+=1
            low+=1
        result.append(dict(time=START+step*index,open=price+2,high=high,low=low,close=price+3))
    return result


def metrics(values):
    return f"{median(values):.2f}/{sorted(values)[ceil(.95*len(values))-1]:.2f}"


def run(name,request,client=None):
    raw=request.model_dump_json(exclude_unset=True)
    calculation,serialization,http=[],[],[]
    value=evaluate(request,load_reference)
    for _ in range(SAMPLES):
        start=perf_counter()
        value=evaluate(request,load_reference)
        calculation.append((perf_counter()-start)*1000)
        start=perf_counter()
        body=json.dumps(value,separators=(",",":")).encode()
        serialization.append((perf_counter()-start)*1000)
    if client is not None:
        for _ in range(SAMPLES):
            start=perf_counter()
            response=client.post("/api/indicators/evaluate",content=raw,headers={"content-type":"application/json"})
            http.append((perf_counter()-start)*1000)
            assert response.status_code==200,response.text
    tracemalloc.start()
    evaluate(request,load_reference)
    _,peak=tracemalloc.get_traced_memory()
    tracemalloc.stop()
    result=value["results"][0]
    counters=result["provenance"].get("retrieval",{})
    events={kind:result["crt_page"][kind]["total"] for kind in ("formations","sweeps","revisions")}
    print(f"{name} | calc {metrics(calculation)} ms | JSON {metrics(serialization)} ms | "
          f"API {metrics(http) if http else 'not measured'} | {len(body)} bytes | "
          f"peak {peak/1024:.1f} KiB | {events} | {counters}",flush=True)
    return result


def specs(frame,history,combined,context=False):
    crt={"id":"candle_range_theory","crt":{"reference_frame":frame,"max_ranges":8,"max_age":500,
                                          "include_context":context,**history}}
    structure={**history,"left":1,"right":1,"algorithm_version":2}
    return [crt]+([
        {"id":"market_structure","structure":structure},
        {"id":"liquidity_sweeps","structure":structure},
        {"id":"fair_value_gap","fvg":history},
        {"id":"order_blocks","structure":structure},
    ] if combined else [])


def input_for(rows,ltf,step,frame,combined=False,context=False):
    return Evaluation(source="DEMO",feed_id="demo-v1",price_basis="SIMULATED",symbol="EURUSD",
                      timeframe=ltf,cutoff=rows[-1]["time"]+step,candles=rows,
                      indicators=specs(frame,{},combined,context))


def main():
    app=FastAPI()
    app.include_router(router)
    print("Windows, ten samples. API uses ASGI TestClient with the actual route; network latency excluded.")
    print("One shared price-zone primitive, zero added series; maximum eight CRT ranges. Native RSS unmeasured.")
    with TestClient(app) as client:
        for ltf,step,frame,size in (("M5",300,"H1",12),("M15",900,"H4",16),("H1",3600,"D1",24)):
            rows=candles(2000,step,size,True)
            request=input_for(rows,ltf,step,frame)
            run(f"2,000 {frame}/{ltf}",request,client)
            if ltf=="M5":
                run("2,000 H1/M5 combined",input_for(rows,ltf,step,frame,True),client)
                run("2,000 H1/M5 CRT contextual",input_for(rows,ltf,step,frame,False,True))
                run("2,000 H1/M5 no sweep",input_for([
                    {**bar,"open":102,"high":105,"low":100,"close":103} for bar in rows],ltf,step,frame))
                prior=request.model_copy(update={"cutoff":request.cutoff-300})
                reference=evaluate(prior,load_reference)["results"][0]
                traces=[]
                for _ in range(SAMPLES):
                    start=perf_counter()
                    evaluate(request,load_reference)
                    assert evaluate(prior,load_reference)["results"][0]==reference
                    traces.append((perf_counter()-start)*1000)
                print("2,000 H1/M5 advance + rewind calc",metrics(traces),"ms")
        with TemporaryDirectory() as workspace:
            db.DATABASE=Path(workspace)/"bench.sqlite"
            storage.MARKET_DIR=Path(workspace)/"market"
            db.initialize()
            rows=candles(10000,300,12,True)
            parts=[]
            for offset in range(0,10000,2000):
                part=storage.write_partition("MT5","crt-bench","EURUSD","candles","M5","BID",rows[offset:offset+2000])
                parts.append({key:part[key] for key in ("id","sha256")})
            history={"history_bars":10000,"history_start":START}
            args=dict(source="MT5_SAVED",feed_id="crt-bench",price_basis="BID",symbol="EURUSD",
                      timeframe="M5",cutoff=START+3000000,candles=[],partitions=parts,frozen=True)
            initial=Evaluation(**args,indicators=specs("H1",history,False))
            first=run("10,000 archive first",initial,client)
            requests=specs("H1",history,False)
            requests[0]["crt_delivery"]={"formation_cursor":first["crt_page"]["formations"]["next_cursor"]}
            run("10,000 archive next",Evaluation(**args,indicators=requests),client)
            run("10,000 archive combined",Evaluation(**args,indicators=specs("H1",history,True)),client)


if __name__=="__main__":
    main()
