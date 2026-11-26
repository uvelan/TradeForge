"""Local recorded-data acceptance. Use the two pinned BI5 files; never fetch data."""
import argparse
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
import json
import lzma
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
from tempfile import TemporaryDirectory

from scripts.benchmark_t06 import PINS

ROOT=Path(__file__).resolve().parents[1]


def verify_racing_submissions():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from fastapi.testclient import TestClient
    from research_backend.app.main import app
    from research_backend.app.db import transaction

    receipts=json.loads((Path(os.environ["TRADEFORGE_STORAGE"])/"review-receipts.json").read_text())
    review=dict(receipts["exported"][1]["template"],assessment="CONDITIONAL",comment="Independent second assessment")
    variants=[json.dumps(review,indent=2).encode()+b"\r\n",json.dumps(review,separators=(",",":")).encode()]
    gate=Barrier(2)
    with TestClient(app) as client:
        def upload(body):
            gate.wait(timeout=15)
            response=client.post("/api/ai/reviews",content=body,headers={"Content-Type":"application/json"})
            response.raise_for_status()
            return response.json()["id"]
        with ThreadPoolExecutor(2) as workers:
            rid,=set(workers.map(upload,variants))
        assert rid!=receipts["review"]
        stored=client.get("/api/ai/reviews/"+rid+"/original").content
        assert stored in variants
        with transaction() as db:
            row=db.execute("SELECT body,sha256 FROM ai_review_originals WHERE review_id=?",(rid,)).fetchone()
            assert row["body"]==stored and row["sha256"]==sha256(stored).hexdigest()
            assert db.execute("SELECT COUNT(*) FROM ai_reviews WHERE package_id=?",(review["package_id"],)).fetchone()[0]==2
        print(json.dumps(dict(revision=rid,input_sha256=[sha256(v).hexdigest() for v in variants],
                              retained_sha256=sha256(stored).hexdigest(),new_canonical_reviews=1)))


def main(output):
    for hour in (13,14):
        raw=(ROOT/f"storage/t06-input/{hour}h_ticks.bi5").read_bytes()
        assert sha256(raw).hexdigest()==PINS[hour]
        if hour==13:
            ticks=list(struct.iter_unpack(">IIIff",lzma.decompress(raw)))
    qualified=next(i for i,r in enumerate(ticks) if r[2]>=ticks[0][2]+10)
    entry=next(i for i,r in enumerate(ticks) if r[0]>ticks[qualified][0])
    exit_index=next(i for i,r in enumerate(ticks) if i>=entry and (r[2]<=ticks[entry][1]-100 or r[2]>=ticks[entry][1]+200))
    assert (qualified,entry,exit_index,ticks[entry][1],ticks[exit_index][2])==(5,6,1564,116243,116143)
    start=int(datetime(2025,11,13,13,tzinfo=UTC).timestamp())*1000
    with TemporaryDirectory(prefix="tf-recorded-review-") as tmp:
        env=os.environ|dict(TRADEFORGE_STORAGE=tmp,TRADEFORGE_PAPER_STORAGE=tmp,
                            TRADEFORGE_DATA_DIR=str(Path(tmp)/"research"),
                            TRADEFORGE_E2E_API_PORT="8564",TRADEFORGE_E2E_UI_PORT="4564")
        subprocess.run([sys.executable,"-m","scripts.month_archive","2025-11"],cwd=ROOT,env=env,check=True,stdout=subprocess.DEVNULL)
        for phase in ("package-create","package-restart"):
            subprocess.run([shutil.which("npm.cmd") or "npm","run","e2e","--","--config","playwright.paper.config.ts","--retries=0"],
                           cwd=ROOT/"frontend",env=env|{"TRADEFORGE_PAPER_PHASE":phase},check=True)
        result=json.loads((Path(tmp)/"review-receipts.json").read_text())
        result["concurrent_review"]=json.loads(subprocess.check_output(
            [sys.executable,"-m","scripts.verify_historical_package","--race"],cwd=ROOT,env=env))
        before,after=(p["evidence"] for p in result["exported"])
        exit_ms=start+ticks[exit_index][0]
        assert (before["cutoff_ms"],after["cutoff_ms"])==(exit_ms-1,exit_ms)
        assert not before["metrics"] and before["events"]==after["events"][:-1]
        metric,=after["metrics"].values()
        expected=Decimal(ticks[exit_index][2]-ticks[entry][1])/100000
        assert metric["value"]==format(expected,".5f")=="-0.00100"
        assert metric["available_at_ms"]==exit_ms and metric["interval_ms"]==[start+ticks[entry][0],exit_ms]
        for e,i,side in zip(after["events"][1:],(entry,exit_index),("ASK","BID"),strict=True):
            q=e["quote"]
            assert (q["ordinal"],q["time_ms"],q["source_sha256"],q["ask_units"],q["bid_units"])==(i,start+ticks[i][0],PINS[13],ticks[i][1],ticks[i][2])
            assert e["side"]==side
        output.write_text(json.dumps(result,indent=2)+"\n",encoding="utf8")
        print("Verified",metric["value"],metric["unit"],"at",exit_ms,"from ordinals",entry,exit_index)


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path,default=ROOT/"storage/historical-package-evidence.json")
    parser.add_argument("--race",action="store_true",help=argparse.SUPPRESS)
    args=parser.parse_args()
    if args.race:
        verify_racing_submissions()
    else:
        main(args.output)
