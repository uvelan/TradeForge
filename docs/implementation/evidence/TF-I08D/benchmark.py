"""Bounded, isolated arrival capture and paired analytical benchmark."""
from datetime import UTC, datetime
import json
from pathlib import Path
import platform
import runpy
from statistics import median, quantiles
from tempfile import TemporaryDirectory
from time import perf_counter, sleep
import tracemalloc

from fastapi.testclient import TestClient
from research_backend.app import config, db, storage
from research_backend.app.arrival_capture import CaptureConfig, Worker, claim, observe, seal
from research_backend.app.arrival_policy import RECORDED_MODE, SIMULATION_MODE
from research_backend.app.main import app
from research_backend.app.smt_causal import StrictSmtRequest, evaluate_strict_smt

ROOT=Path(__file__).resolve().parents[4]
rss_peak=runpy.run_path(str(ROOT/"docs/implementation/evidence/TF-I08C/benchmark.py"))["rss_peak"]
trusted_archive=runpy.run_path(str(ROOT/"research_backend/tests/arrival_fixtures.py"))["trusted_archive"]
START=int(datetime(2025,1,6,tzinfo=UTC).timestamp())


class Clock:
    def __init__(self,now):
        self.now=now

    def sample(self):
        return self.now,self.now-START*1000


def sample(callback):
    began=perf_counter()
    result=callback()
    return (perf_counter()-began)*1000,result


def stats(values):
    return [round(median(values),3),round(quantiles(values,n=100,method="inclusive")[94],3)]


def capture_workload():
    scopes=["EURUSD","GBPUSD"]
    clock=Clock(START*1000)
    cfg=CaptureConfig(symbols=scopes,timeframe="M1",calendar={"kind":"continuous"})
    session=Worker(cfg,claim(cfg,clock.now),clock.now,clock)
    current=START
    sleep_in_provider=False

    def feed(symbol,timeframe,limit,*,include_latest,provenance):
        if sleep_in_provider:
            sleep(.005)
        provenance.update(feed_id="mt5:synthetic-benchmark",price_basis="BID")
        return [{"time":stamp,"open":12,"high":13,"low":11,"close":12,"volume":9}
                for stamp in range(max(START,current-31*60),current+1,60)]

    writes,duplicates=[],[]
    published=0
    for slot in range(33):
        current=START+slot*60
        clock.now=(current+60)*1000+50
        for lane in (writes,duplicates):
            delta,batch=sample(lambda:[observe(session,feed,symbol) for symbol in scopes])
            if slot>=3:
                lane.append(delta)
            published+=sum(item["writes"] for item in batch)
    sleep_in_provider=True
    slow,_=sample(lambda:[observe(session,feed,symbol) for symbol in scopes])
    scope={"provider":"MT5","symbol":"EURUSD","timeframe":"M1","price_basis":"BID",
           "feed_id":batch[0]["feed_id"]}
    elapsed,archived=sample(lambda:seal(scope,START,current+60,current+62,cfg.calendar))
    # Release the process-owned lease, then verify a new session cannot
    # duplicate the latest already receipted observation.
    with db.transaction() as sql:
        sql.execute("DELETE FROM arrival_leases WHERE session_id IN (?,?)",tuple(session.sessions.values()))
    restarted=Worker(cfg,claim(cfg,clock.now),clock.now,clock)
    restart_ms,repeat=sample(lambda:[observe(restarted,feed,symbol) for symbol in scopes])
    assert published==66 and not any(item["writes"] for item in repeat)
    return {"scenario":"capture_two_scopes","samples":30,"write_pair_ms":stats(writes),
            "duplicate_pair_ms":stats(duplicates),"observation_count":published,"queue_depth":0,
            "single_seal_ms":round(elapsed,3),"sealed_rows":archived["data_quality"]["observations"],
            "injected_5ms_provider_delay_pair_ms":round(slow,3),"restart_poll_ms":round(restart_ms,3)}


def analytical_input(count,spacing,mode):
    step=300
    end=START+count*step
    body={"cutoff":end+3,"period":2,"alignment_bars":1,"expiry_bars":12,"limit":8,"availability_mode":mode}
    for index,(side,symbol) in enumerate((("primary","EURUSD"),("comparison","GBPUSD"))):
        candles=[]
        for i in range(count):
            high=(14,16) if index==0 else (29,27)
            price=high[(i//spacing)%2] if i%spacing==3 else 10+12*index
            stamp=START+i*step
            candles.append(dict(time=stamp,open=price,high=price+1,low=price-1,close=price,closed=True,
                                volume=9,available_at=stamp+step))
        name=f"bench-{count}-{spacing}-{mode.lower()}"
        if mode==RECORDED_MODE:
            frozen=trusted_archive(candles,provider="MT5",feed=name,symbol=symbol,timeframe="M5",
                                   start=START,end=end,cutoff=end+3)
            manifest={"arrival_manifest":frozen["id"],"partitions":frozen["partitions"]}
        else:
            parts=[storage.write_partition("MT5",name,symbol,"candles","M5","BID",candles[i:i+2000])
                   for i in range(0,count,2000)]
            manifest={"partitions":[{k:part[k] for k in ("id","sha256")} for part in parts]}
        body[side]=dict(source="MT5_SAVED",symbol=symbol,timeframe="M5",feed_id=name,price_basis="BID",
                        calendar={"kind":"continuous"},start=START,end=end,**manifest)
    return body


def analytical_workload(client,count,spacing,mode):
    body=analytical_input(count,spacing,mode)
    request=StrictSmtRequest.model_validate(body)
    endpoint="/api/indicators/smt/strict"
    calc,api=[],[]
    for trial in range(33):
        internal,output=sample(lambda:evaluate_strict_smt(request))
        external,response=sample(lambda:client.post(endpoint,json=body))
        if not response.is_success or not output.get("snapshot"):
            raise RuntimeError(response.text)
        if trial>=3:
            calc.append(internal)
            api.append(external)
    tracemalloc.start()
    evaluate_strict_smt(request)
    _,peak=tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return {"scenario":"dual_feed_smt","mode":mode,"n_per_feed":count,"swing_spacing":spacing,"samples":30,
            "calculation_ms":stats(calc),"api_ms":stats(api),"python_peak_bytes":peak,
            "native_high_water_kib":rss_peak(),"response_bytes":len(response.content),
            "events":output["delivery"]["total"],"resources":output["resources"]}


def main():
    print(platform.platform(),platform.python_version(),flush=True)
    with TemporaryDirectory(prefix="tradeforge-arrival-bench-") as scratch:
        root=Path(scratch)
        db.DATABASE=config.DATABASE=root/"isolated.sqlite3"
        storage.MARKET_DIR=root/"market"
        with TestClient(app) as client:
            print(json.dumps(capture_workload(),sort_keys=True),flush=True)
            for count,spacing in [(2000,6),(10000,100),(10000,6)]:
                for mode in (RECORDED_MODE,SIMULATION_MODE):
                    print(json.dumps(analytical_workload(client,count,spacing,mode),sort_keys=True),flush=True)


if __name__=="__main__":
    main()
