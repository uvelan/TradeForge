"""Local previous-writer reproduction and in-place upgrade using checksum-pinned EURUSD."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Barrier
import zipfile

ROOT=Path(__file__).resolve().parents[1]
OLD="ab4ba424a7d1ee0ca619bbcdf8e11bf5c85def81"
API="/api/ai/reviews"
HEADERS={"Content-Type":"application/json"}


def variants(review):
    clean=json.dumps(review,separators=(",",":"))
    for field,value,wrong in (("value",review["claims"][0]["value"],"99"),("unit","USD per EUR","USD"),
                              ("package_id",review["package_id"],"PKG-MISSING")):
        original=json.dumps(field)+":"+json.dumps(value)
        other=json.dumps(field)+":"+json.dumps(wrong)
        for wrong_first in (True,False):
            pair=[other,original] if wrong_first else [original,other]
            yield field+("/wrong-first" if wrong_first else "/wrong-last"),clean.replace(original,",".join(pair),1).encode()


def counts():
    from research_backend.app.db import transaction
    with transaction() as db:
        return [db.execute("SELECT count(*) FROM "+table).fetchone()[0] for table in ("ai_reviews","ai_review_originals")]


def setup(client):
    from research_backend.app.historical_import import import_bi5
    from scripts.benchmark_t06 import PINS
    for hour,digest in PINS.items():
        raw=(ROOT/f"storage/t06-input/{hour}h_ticks.bi5").read_bytes()
        assert sha256(raw).hexdigest()==digest
        import_bi5(raw,"EURUSD",datetime(2025,11,13,tzinfo=UTC),hour)
    route="/api/month-replay/2025-11"
    route+="/"+client.get(route).json()["session"]
    index=0
    while True:
        step=client.post(route+"/advance",params={"index":index})
        step.raise_for_status()
        body=step.json()
        if any(e["kind"]=="STOP" for e in body["events"]):
            break
        index=body["next_index"]
    package=client.post(route+"/packages",params={"cutoff_ms":1763040698103})
    package.raise_for_status()
    template=client.get("/api/ai/packages/"+package.json()["package_id"]+"/recorded").json()["template"]
    assert template["claims"][0]["value"]=="-0.00100"
    return dict(template,comment="Controlled integrity check")


def worker(stage):
    from fastapi.testclient import TestClient
    from research_backend.app.main import app
    file=Path(os.environ["TRADEFORGE_STORAGE"])/"old-reviews.json"
    with TestClient(app) as c:
        if stage in ("old","fresh"):
            review=setup(c)
            results={}
            for label,raw in variants(review):
                response=c.post(API,content=raw,headers=HEADERS)
                results[label]=[response.status_code,counts()]
                if stage=="fresh":
                    assert response.status_code==422 and counts()==[0,0]
                elif label.endswith("wrong-first"):
                    assert response.status_code==201
                    affected=response.json()["id"]
                else:
                    assert response.status_code==(404 if label.startswith("package_id") else 422)
            clean=dict(review,comment="Clean independent assessment")
            raw=json.dumps(clean,indent=4).encode()+b"\r\n"
            accepted=c.post(API,content=raw,headers=HEADERS)
            accepted.raise_for_status()
            cid=accepted.json()["id"]
            assert c.get(f"{API}/{cid}/original").content==raw
            result=dict(results=results,clean=cid,clean_sha256=sha256(raw).hexdigest(),counts=counts(),review=review)
            if stage=="old":
                result.update(affected=affected,affected_sha256=sha256(c.get(f"{API}/{affected}/original").content).hexdigest())
                result["decisions"]={}
                for rid in (affected,cid):
                    d=c.post(f"{API}/{rid}/decisions",json=dict(expected_sequence=0,decision="NEEDS_FOLLOW_UP",rationale="Prior operator decision"))
                    d.raise_for_status()
                    result["decisions"][rid]=c.get(f"{API}/{rid}/decisions").json()
                file.write_text(json.dumps(result))
            else:
                gate=Barrier(2)
                def send(n):
                    body=json.dumps(dict(review,comment="Concurrent valid variant"),indent=n).encode()
                    gate.wait(timeout=15)
                    r=c.post(API,content=body,headers=HEADERS)
                    r.raise_for_status()
                    return r.json()["id"],sha256(body).hexdigest()
                with ThreadPoolExecutor(2) as pool:
                    pairs=list(pool.map(send,[1,3]))
                rid,={r for r,_ in pairs}
                retained=sha256(c.get(f"{API}/{rid}/original").content).hexdigest()
                assert retained in [h for _,h in pairs] and counts()==[2,2]
                result["race"]=dict(id=rid,input_hashes=[h for _,h in pairs],retained=retained,counts=counts())
        else:
            old=json.loads(file.read_text())
            result={}
            for key in ("affected","clean"):
                rid=old[key]
                digest=sha256(c.get(f"{API}/{rid}/original").content).hexdigest()
                assert digest==old[key+"_sha256"]
                history=c.get(f"{API}/{rid}/decisions").json()
                assert history==old["decisions"][rid]
                assert history["current_sequence"]==1 and history["decisions"][0]["rationale"]=="Prior operator decision"
                result[key]=dict(id=rid,sha256=digest,decisions=history)
            assert c.post(API,json=old["review"]).status_code==409
            assert c.post(API,json=dict(old["review"],comment="Clean independent assessment")).json()["id"]==old["clean"]
            fixed=dict(old["review"],corrects_review_id=old["affected"])
            a=c.post(API,json=fixed)
            a.raise_for_status()
            rid=a.json()["id"]
            assert rid!=old["affected"] and c.post(API,json=fixed).json()["id"]==rid
            assert counts()==[3,3]
            result.update(correction=rid,counts=counts(),correction_sha256=sha256(c.get(f"{API}/{rid}/original").content).hexdigest())
        print(json.dumps(result))


def run(output,baseline):
    with TemporaryDirectory(prefix="tf-review-integrity-") as tmp:
        work=Path(tmp)
        checkout=work/"writer"
        with zipfile.ZipFile(io.BytesIO(subprocess.check_output(["git","archive","--format=zip",OLD,"research_backend/app","tradeforge","scripts"],cwd=ROOT))) as z:
            z.extractall(checkout)
        def env(location):
            return os.environ|dict(TRADEFORGE_STORAGE=str(location),TRADEFORGE_DATA_DIR=str(location/"research"),
                                   TRADEFORGE_PAPER_STORAGE=str(location),TRADEFORGE_PAPER_PHASE="integrity",
                                   TRADEFORGE_E2E_API_PORT="8564",TRADEFORGE_E2E_UI_PORT="4564")
        def invoke(stage,location,code):
            out=subprocess.check_output([sys.executable,str(Path(__file__).resolve()),"--stage",stage,"--code",str(code)],
                                        cwd=code,env=env(location))
            return json.loads(out)
        result={"old":invoke("old",work/"upgrade",checkout)}
        if not baseline:
            result["fresh"]=invoke("fresh",work/"fresh",ROOT)
            subprocess.run([shutil.which("npm.cmd") or "npm","run","e2e","--","--config","playwright.paper.config.ts","--retries=0"],
                           cwd=ROOT/"frontend",env=env(work/"upgrade"),check=True)
            result["restart"]=invoke("restart",work/"upgrade",ROOT)
        output.write_text(json.dumps(result,indent=2)+"\n",encoding="utf8")
        print(json.dumps(result))


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--stage")
    p.add_argument("--code")
    p.add_argument("--baseline",action="store_true")
    p.add_argument("--output",type=Path,default=ROOT/"storage/review-integrity.json")
    args=p.parse_args()
    if args.stage:
        sys.path.insert(0,args.code)
        worker(args.stage)
    else:
        run(args.output,args.baseline)
