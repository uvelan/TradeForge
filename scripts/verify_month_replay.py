"""Reproducible short-archive acceptance and timing; no network or live broker."""
import argparse
from contextlib import closing
from datetime import UTC,datetime
import hashlib
import json
import lzma
import os
import random
from pathlib import Path
import shutil
import sqlite3
import struct
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import perf_counter

from scripts.benchmark_t06 import PINS
from scripts.verify_real_strategy_evaluation import peak_rss_bytes

ROOT=Path(__file__).resolve().parents[1]
START=int(datetime(2025,11,13,13,tzinfo=UTC).timestamp())


def worker(mode):
    from fastapi.testclient import TestClient
    from research_backend.app.main import app
    from research_backend.app.db import transaction
    from research_backend.app.recorded_session import read_session
    from research_backend.app import storage
    from statistics import median
    with TestClient(app,base_url="http://localhost") as c:
        path="/api/month-replay/2025-11"
        if mode=="warm":
            # Prime both verified source readers in this process without generating any checkpoints.
            with transaction() as db:
                for h in (START,START+3600):
                    read_session(db,"EURUSD",h,h+3600,[])
        before=perf_counter()
        manifest=c.get(path).json()
        url=path+"/"+manifest["session"]
        pin_hours=[h for h in manifest["binding"]["hours"] if h["pin"]]
        assert len(pin_hours)==2
        checkpoint=manifest["checkpoint"]
        cursor=checkpoint["next_index"] if checkpoint else 0
        calls=0
        while True:
            res=c.post(url+"/advance",params={"index":cursor})
            res.raise_for_status()
            step=res.json()
            calls+=1
            if mode=="interrupt" and step["state"]["position"] and "fill" in step["state"]["position"]:
                print(json.dumps(step));return
            cursor=step["next_index"]
            if step["completed"]:
                break
        elapsed=perf_counter()-before
        pages=[];after=-1
        while part:=c.get(url+"/steps",params={"after":after}).json():
            pages.extend(part);after=part[-1]["index"]
        events=[e for p in pages for e in p["events"]]
        assert [(e["kind"],e["quote"]["ordinal"]) for e in events]==[("QUALIFIED",5),("ENTRY",6),("STOP",1564)]
        assert [e["price_units"] for e in events[1:]]==[116243,116143]
        assert [e["quote"]["time_ms"]-START*1000 for e in events]==[666,771,1898103]
        timings=[];processed=0
        with transaction() as db:
            for h in pin_hours:
                size,path_name=db.execute("""SELECT length(b.payload),d.relative_path FROM historical_bi5_sources b,datasets d
                                    WHERE b.sha256=? AND d.id=?""",(h["pin"]["source_sha256"],h["pin"]["partition"]["id"])).fetchone()
                h["bi5_bytes"]=size
                h["parquet_bytes"]=(storage.MARKET_DIR/path_name).stat().st_size
                processed+=6*(size+h["parquet_bytes"])
            for offset in random.Random(20251113).sample(range(7200),7):
                t=START+offset
                begin=perf_counter()
                quotes,_,_,_=read_session(db,"EURUSD",t,t+1,[])
                timings.append((perf_counter()-begin)*1000)
                hour=13+offset//3600
                raw=(ROOT/f"storage/t06-input/{hour}h_ticks.bi5").read_bytes()
                assert hashlib.sha256(raw).hexdigest()==PINS[hour]
                fresh=[(i,r[2],r[1]) for i,r in enumerate(struct.iter_unpack(">IIIff",lzma.decompress(raw)))
                       if r[0]//1000==offset%3600]
                assert [(q["source_order"],q["bid_units"],q["ask_units"]) for q in quotes]==fresh
        received=[h for p in pages for h in p["coverage"]]
        counts={k:sum(h["status"]==k for h in manifest["binding"]["hours"])
                for k in ("REGISTERED","SCHEDULED_CLOSURE","MISSING_OPEN")}
        print(json.dumps(dict(mode=mode,seconds=elapsed,peak_bytes=peak_rss_bytes(),requests=calls,session=manifest["session"],
                    ledger_sha256=hashlib.sha256(json.dumps(pages,sort_keys=True).encode()).hexdigest(),
                    quotes=step["quote_count"],full_session_bi5_reads=step["hour_reads"],full_session_parquet_verifications=step["hour_reads"],
                    logical_pinned_bytes=processed,counts=counts,inputs=pin_hours,events=events,
                    random_one_second_lookup_ms=timings,lookup_median_ms=median(timings),
                    observed_missing_intervals_ms=[gap for h in received if h["status"]=="OBSERVED" for gap in h["gaps"]])))


def compare_accepted_control(report):
    baseline=json.loads((ROOT/"docs/implementation/plan-integration-evidence/month-replay.json").read_text())
    old,new=baseline["cold"],report["cold"]
    assert baseline["missing_open_utc_hours"]==report["missing_open_utc_hours"]
    for field in ("quotes","inputs","counts","observed_missing_intervals_ms"):
        assert old[field]==new[field],field
    assert old["session"]!=new["session"]
    old_decision,new_decision=old["events"][0]["id"],new["events"][0]["id"]
    identities={}
    for before,after in zip(old["events"],new["events"],strict=True):
        expected=dict(before)
        if "decision_id" in expected:
            assert expected["decision_id"]==old_decision
            expected["decision_id"]=new_decision
        assert before["id"]!=after["id"]
        expected["id"]=after["id"]
        assert expected==after
        identities[after["kind"]]={"v1":before["id"],"v2":after["id"]}
    assert baseline["restart"]["frontier"]==report["restart"]["frontier"]
    assert baseline["restart"]["position"]|{"decision_id":new_decision}==report["restart"]["position"]
    return identities


def main(output,browser=False):
    # Raw-format oracle for qualification, the next executable ask, and first closing-side threshold.
    raw=(ROOT/"storage/t06-input/13h_ticks.bi5").read_bytes()
    assert hashlib.sha256(raw).hexdigest()==PINS[13]
    ticks=list(struct.iter_unpack(">IIIff",lzma.decompress(raw)))
    q=next(i for i,r in enumerate(ticks) if r[2]>=ticks[0][2]+10)
    f=next(i for i,r in enumerate(ticks) if r[0]>ticks[q][0])
    x=next(i for i,r in enumerate(ticks) if i>f and (r[2]<=ticks[f][1]-100 or r[2]>=ticks[f][1]+200))
    assert (q,f,x,ticks[f][1],ticks[x][2])==(5,6,1564,116243,116143)
    with TemporaryDirectory(prefix="tf-month-") as tmp:
        root=Path(tmp)
        env=os.environ|{"TRADEFORGE_DATA_DIR":tmp}
        manifest=json.loads(subprocess.check_output([sys.executable,"-m","scripts.month_archive","2025-11"],env=env))
        base=root/"tradeforge.sqlite3"
        with closing(sqlite3.connect(base)) as db,closing(sqlite3.connect(root/"clean.db")) as clean:
            db.backup(clean)
        def run(mode):
            return json.loads(subprocess.check_output([sys.executable,"-m","scripts.verify_month_replay","--worker",mode],env=env))
        def restore():
            with closing(sqlite3.connect(root/"clean.db")) as clean,closing(sqlite3.connect(base)) as db:
                clean.backup(db)
        cold=run("cold");restore()
        warm=run("warm");restore()
        stop=run("interrupt");resume=run("resume")
        assert stop["state"]["position"]["fill"]==cold["events"][1]["quote"]
        assert cold["ledger_sha256"]==warm["ledger_sha256"]==resume["ledger_sha256"]
        assert cold["quotes"]==8636 and cold["full_session_bi5_reads"]==12
        report=dict(cold=cold,warm=warm,restart=dict(frontier=stop["through"],position=stop["state"]["position"],
                    final_ledger=resume["ledger_sha256"]),missing_open_utc_hours=[
                    datetime.fromtimestamp(h["start"],UTC).isoformat() for h in manifest["hours"] if h["status"]=="MISSING_OPEN"])
        report["accepted_control_event_ids"]=compare_accepted_control(report)
        output.write_text(json.dumps(report,indent=2)+"\n",encoding="utf8")
        if browser:
            webroot=root/"browser"
            webenv=env|{"TRADEFORGE_PAPER_STORAGE":str(webroot),"TRADEFORGE_DATA_DIR":str(webroot/"research"),
                        "TRADEFORGE_PAPER_PHASE":"month","TRADEFORGE_E2E_API_PORT":"8564","TRADEFORGE_E2E_UI_PORT":"4564"}
            subprocess.run([sys.executable,"-m","scripts.month_archive","2025-11"],env=webenv,stdout=subprocess.DEVNULL,check=True)
            subprocess.run([shutil.which("npm.cmd") or "npm","run","e2e","--","--config","playwright.paper.config.ts","--retries=0"],
                           cwd=ROOT/"frontend",env=webenv,check=True)
        print(cold["counts"],cold["seconds"],warm["seconds"],cold["peak_bytes"],warm["peak_bytes"])


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--worker",choices=["cold","warm","interrupt","resume"])
    p.add_argument("--output",type=Path,default=Path("storage/month-results.json"))
    p.add_argument("--browser",action="store_true")
    args=p.parse_args()
    if args.worker: worker(args.worker)
    else: main(args.output,args.browser)
