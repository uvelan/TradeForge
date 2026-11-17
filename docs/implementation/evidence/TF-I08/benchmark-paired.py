"""Interleave approved and candidate measurements in one process. Pass the baseline worktree path."""
from ctypes import WinDLL, Structure, byref, sizeof
from ctypes import POINTER, c_size_t
from ctypes.wintypes import DWORD, HANDLE
from datetime import UTC, datetime
from importlib import import_module
import json
from math import ceil
from pathlib import Path
from statistics import median
import sys
from tempfile import TemporaryDirectory
from time import perf_counter
import tracemalloc
from types import ModuleType
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

START=int(datetime(2026,11,2,tzinfo=UTC).timestamp())


def baseline_package(root):
    package=ModuleType("tfi08_approved")
    package.__path__=[str(root/"research_backend")]
    sys.modules[package.__name__]=package
    return package.__name__+".app"


def dataset(count):
    phrases=[(100,101,99,100),(101,104,100,103),(102,103,100,101),(103,106,102,105),
             (104,106,103,105),(105,107,104,104),(103,104,98,99),(100,102,99,101)]
    return [dict(time=START+300*i,**dict(zip(("open","high","low","close"),phrases[i%8]))) for i in range(count)]


def specifications(context=False,archive=False,combined=True):
    history={"history_bars":10000,"history_start":START} if archive else {}
    structure={"algorithm_version":2,"left":3,"right":3,**history}
    crt={"id":"candle_range_theory","crt":{"include_context":context,**history}}
    return [crt]+([
        {"id":"market_structure","structure":structure},
        {"id":"liquidity_sweeps","structure":structure},
        {"id":"fair_value_gap","fvg":history},
        {"id":"order_blocks","structure":structure,"ob":{"include_context":context}},
    ] if combined else [])


def working_set():
    class Counters(Structure):
        _fields_=[("size",DWORD),("page_faults",DWORD),("peak",c_size_t),("current",c_size_t),
                  ("paged_peak",c_size_t),("paged",c_size_t),("nonpaged_peak",c_size_t),("nonpaged",c_size_t),
                  ("pagefile",c_size_t),("pagefile_peak",c_size_t)]

    process=WinDLL("kernel32").GetCurrentProcess
    process.restype=HANDLE
    query=WinDLL("psapi").GetProcessMemoryInfo
    query.argtypes=[HANDLE,POINTER(Counters),DWORD]
    counters=Counters()
    if not query(process(),byref(counters),sizeof(counters)):
        raise OSError("GetProcessMemoryInfo failed")
    return counters.current/1024


def pair(label, versions, args):
    contexts=[]
    for name,engine,loader,http in versions:
        params={**args,"timing_policy":"STRICT_CAUSAL"} if name=="strict" else args
        request=engine.Evaluation.model_validate(params)
        contexts.append((name,lambda e=engine,r=request,l=loader:e.evaluate(r,l),
                         http,request.model_dump_json(exclude_unset=True)))
    timings={name:[[],[],[]] for name,*_ in contexts}
    results={}
    for _,calc,_,_ in contexts:
        calc()
    for i in range(12):
        order=contexts if i%2==0 else list(reversed(contexts))
        for name,calc,http,body in order:
            start=perf_counter()
            result=calc()
            timings[name][0].append((perf_counter()-start)*1000)
            start=perf_counter()
            size=len(json.dumps(result,separators=(",",":")).encode())
            timings[name][1].append((perf_counter()-start)*1000)
            start=perf_counter()
            response=http.post("/api/indicators/evaluate",content=body,headers={"content-type":"application/json"})
            timings[name][2].append((perf_counter()-start)*1000)
            assert response.status_code==200,response.text
            results[name]=size,result.get("diagnostics",{})
    for name,calc,_,_ in contexts:
        rss_before=working_set()
        tracemalloc.start()
        calc()
        _,peak=tracemalloc.get_traced_memory()
        tracemalloc.stop()
        rss_after=working_set()
        stats=["{:.2f}/{:.2f}".format(median(x),sorted(x)[ceil(len(x)*.95)-1]) for x in timings[name]]
        size,diag=results[name]
        print(f"{label} | {name} | calc {stats[0]} | JSON {stats[1]} | API {stats[2]} | "
              f"bytes {size} | peak {peak/1024:.1f} KiB | RSS {rss_before:.0f}->{rss_after:.0f} KiB | {diag.get('executions',{})} | {diag.get('archive',{})}",flush=True)


def overlap_fixture(versions, storage):
    start=START//86400*86400-86400
    bars=[{**bar,"time":start+300*i} for i,bar in enumerate(dataset(432))]
    manifest=[]
    for chunk in (bars[:300],bars[300:]):
        item=storage.write_partition("MT5","overlap-bench","EURUSD","candles","M5","BID",chunk)
        manifest.append({key:item[key] for key in ("id","sha256")})
    args=dict(source="MT5_SAVED",symbol="EURUSD",timeframe="M5",feed_id="overlap-bench",price_basis="BID",
              cutoff=start+432*300,frozen=True,candles=[],partitions=manifest,load_reference=True,
              indicators=[{"id":"candle_range_theory","crt":{"history_bars":10000,"history_start":start}},
                          {"id":"previous_day","schedule":{"kind":"continuous"}}])
    for tag,engine,loader,_ in versions:
        history=import_module(loader.__module__)
        with patch.object(history,"read_partition",wraps=history.read_partition) as read:
            value=engine.evaluate(engine.Evaluation.model_validate(args),loader)
        assert value["results"][1]["coverage"]["status"]=="COMPLETE"
        print(f"overlapping archive windows | {tag} | physical verified reads {read.call_count} | "
              f"{value.get('diagnostics',{}).get('archive',{})}",flush=True)


def main():
    paths=[("baseline",baseline_package(Path(sys.argv[1]))),("final","research_backend.app")]
    with TemporaryDirectory() as root:
        stores=[]
        versions=[]
        for tag,prefix in paths:
            db=import_module(prefix+".db")
            db.DATABASE=Path(root)/"archive.sqlite"
            storage=import_module(prefix+".storage")
            storage.MARKET_DIR=Path(root)/"market"
            db.initialize()
            stores.append(storage)
            engine=import_module(prefix+".indicator_engine")
            loader=import_module(prefix+".indicator_history").load_reference
            app=FastAPI()
            app.include_router(import_module(prefix+".indicator_routes").router)
            versions.append((tag,engine,loader,TestClient(app)))
        if "--overlap-only" in sys.argv:
            overlap_fixture(versions,stores[-1])
            return
        print("Windows, same process, alternating run order. 12 samples per version, P50/P95 in milliseconds.",flush=True)
        common={"symbol":"EURUSD","timeframe":"M5","feed_id":"demo-v1","price_basis":"SIMULATED",
                "source":"DEMO","cutoff":START+600000,"candles":dataset(2000)}
        if "--causal" in sys.argv:
            causal_bench(versions,stores[-1],common)
            return
        pair("2,000 standalone CRT",versions,{**common,"indicators":specifications(combined=False)})
        for isolated in specifications()[1:]:
            pair("2,000 standalone "+isolated["id"],versions,{**common,"indicators":[isolated]})
        pair("2,000 five compatible",versions,{**common,"indicators":specifications()})
        pair("2,000 context enabled",versions,{**common,"indicators":specifications(context=True)})
        if "--integrity" in sys.argv:
            levels=[{"id":"previous_day"},{"id":"previous_week"},{"id":"session_asia"},
                    {"id":"session_london"},{"id":"session_new_york"}]
            for level in levels:
                pair("2,000 standalone "+level["id"],versions,{**common,"load_reference":True,"indicators":[level]})
            crt=specifications(context=True,combined=False)[0]
            pair("2,000 shared selected levels",versions,
                 {**common,"indicators":[{**crt,"context":{"policy":"shared"}},*levels]})
            overlap_fixture(versions,stores[-1])
        records=dataset(10000)
        manifest=[]
        for offset in range(0,len(records),2000):
            item=stores[-1].write_partition("MT5","pairbench","EURUSD","candles","M5","BID",records[offset:offset+2000])
            manifest.append({k:item[k] for k in ("id","sha256")})
        saved={**common,"source":"MT5_SAVED","feed_id":"pairbench","price_basis":"BID","frozen":True,
               "cutoff":START+3000000,"candles":[],"partitions":manifest}
        pair("10,000 archive CRT",versions,{**saved,"indicators":specifications(archive=True,combined=False)})
        pair("10,000 archive five compatible",versions,{**saved,"indicators":specifications(archive=True)})


def causal_bench(versions,storage,common):
    versions=[*versions,("strict",*versions[-1][1:])]
    combined=specifications()
    pair("2,000 caller combined",versions,{**common,"indicators":combined})
    pair("2,000 caller context",versions,{**common,"indicators":specifications(context=True)})
    pair("2,000 standalone previous-day",versions[:2],{**common,"load_reference":True,
         "indicators":[{"id":"previous_day"}]})
    delayed=[{**bar,"available_at":bar["time"]+300+(750 if i%11==0 else 120 if i%2 else 0)}
             for i,bar in enumerate(common["candles"])]
    pair("2,000 delayed and out-of-order",versions,{**common,"candles":delayed,"indicators":combined})
    for count,label in ((1300,"rewind"),(1320,"advance")):
        pair("Replay "+label,versions,{**common,"candles":delayed[:count],
             "cutoff":START+count*300,"frozen":True,"indicators":combined})
    records=[{**bar,"available_at":bar["time"]+300} for bar in dataset(10000)]
    manifest=[]
    for offset in range(0,len(records),2000):
        item=storage.write_partition("MT5","causal-bench","EURUSD","candles","M5","BID",records[offset:offset+2000])
        manifest.append({key:item[key] for key in ("id","sha256")})
    specs=specifications(archive=True)
    for spec in specs:
        for field in ("crt","fvg","structure"):
            if field in spec:
                spec[field]["market_schedule"]={"kind":"continuous"}
    pair("10,000 archive combined",versions,{**common,"source":"MT5_SAVED","feed_id":"causal-bench",
         "price_basis":"BID","frozen":True,"cutoff":START+3000000,"candles":[],"partitions":manifest,
         "indicators":specs})


if __name__=="__main__":
    main()
