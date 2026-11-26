"""A19 uses real transactions and controlled BI5 rows to force a competing exit."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from hashlib import sha256
import json
from threading import Event

from research_backend.app import month_replay
from research_backend.app.db import transaction
from research_backend.tests.test_month_replay import source


def test_exit_snapshot_claims_and_duplicate_delivery(client,monkeypatch):
    source(13,[(3595000,110000),(3596000,110012),(3597000,110013)])
    source(14,[(100,110230)])
    root="/api/month-replay/2025-11"
    session=client.get(root).json()["session"]
    route=f"{root}/{session}"
    index=0
    while True:
        step=client.post(route+"/advance",params={"index":index}).json()
        index=step["next_index"]
        if step["events"]:
            break
    assert [e["kind"] for e in step["events"]]==["QUALIFIED","ENTRY"]
    exit_ms=step["through"]*1000+100
    def export(ms):
        response=client.post(route+"/packages",params={"cutoff_ms":ms})
        assert response.status_code==201,response.text
        return client.get("/api/ai/packages/"+response.json()["package_id"]+"/recorded").json()

    first_read,resume=Event(),Event()
    original=month_replay.load_manifest
    def pause(*args):
        binding=original(*args)
        if not first_read.is_set():
            first_read.set()
            assert resume.wait(15)
        return binding
    monkeypatch.setattr(month_replay,"load_manifest",pause)
    with ThreadPoolExecutor(2) as threads:
        future=threads.submit(export,exit_ms)
        try:
            assert first_read.wait(15)
            closed=client.post(route+"/advance",params={"index":index}).json()
            assert closed["events"][0]["quote"]["time_ms"]==exit_ms
        finally:
            resume.set()
        racing=future.result()["evidence"]
    assert racing["metrics"]=={} and "replay_frontier" in racing["unavailable"]
    assert [e["kind"] for e in racing["events"]]==["QUALIFIED","ENTRY"]
    before=export(exit_ms-1)["evidence"]
    assert before["metrics"]=={} and "replay_frontier" not in before["unavailable"]
    package=export(exit_ms)
    evidence=package["evidence"]
    metric,=evidence["metrics"].values()
    assert (metric["value"],metric["unit"],metric["available_at_ms"])==("0.00215","USD per EUR",exit_ms)
    assert [e["kind"] for e in evidence["events"]]==["QUALIFIED","ENTRY","TARGET"]
    valid=package["template"]
    for key,wrong in (("value","0.00216"),("unit","USD")):
        altered=deepcopy(valid)
        altered["claims"][0][key]=wrong
        assert client.post("/api/ai/reviews",json=altered).status_code==422
    assert not client.get("/api/ai/reviews").json()
    raw=json.dumps(valid,indent=4).encode()+b"\r\n"
    with ThreadPoolExecutor(2) as threads:
        responses=list(threads.map(lambda _:client.post("/api/ai/reviews",content=raw,headers={"Content-Type":"application/json"}),range(2)))
    assert [r.status_code for r in responses]==[201,201]
    rid,={r.json()["id"] for r in responses}
    assert client.post("/api/ai/reviews",json=valid).json()["id"]==rid
    path=f"/api/ai/reviews/{rid}"
    assert client.get(path+"/original").content==raw
    with transaction() as db:
        assert db.execute("SELECT sha256 FROM ai_review_originals").fetchall()[0][0]==sha256(raw).hexdigest()
        assert db.execute("SELECT count(*) FROM ai_reviews").fetchone()[0]==1
    decision={"expected_sequence":0,"decision":"ACCEPTED","rationale":"Checked both recorded sides"}
    assert client.post(path+"/decisions",json=decision).json()==client.post(path+"/decisions",json=decision).json()
    assert client.get(path+"/decisions").json()["current_sequence"]==1
    # The frozen evidence survives local archive loss; a new export must not pretend to verify it.
    from research_backend.app import storage
    with transaction() as db:
        relative=db.execute("SELECT relative_path FROM datasets WHERE id=?",
                            (evidence["events"][-1]["quote"]["partition"]["id"],)).fetchone()[0]
    (storage.MARKET_DIR/relative).write_bytes(b"changed")
    count=len(client.get(route+"/packages").json())
    failure=client.post(route+"/packages",params={"cutoff_ms":exit_ms})
    assert failure.status_code==409 and "verification failed" in failure.json()["detail"]
    assert len(client.get(route+"/packages").json())==count
    assert client.get("/api/ai/packages/"+valid["package_id"]+"/recorded").json()==package

def test_legacy_package_migration_preserves_zip_reviews_and_decisions(client,plan):
    from io import BytesIO
    import zipfile
    from research_backend.app import db
    uid=client.post("/api/trades",json=plan).json()["id"]
    pkg=client.post(f"/api/ai/exports/{uid}").json()
    path=pkg["download_url"].removeprefix("/research")
    original=client.get(path).content
    with zipfile.ZipFile(BytesIO(original)) as z:
        review=z.read("response/AI_REVIEW_TEMPLATE.json")
    rid=client.post("/api/ai/reviews",content=review,headers={"Content-Type":"application/json"}).json()["id"]
    log=f"/api/ai/reviews/{rid}/decisions"
    receipt=client.post(log,json=dict(expected_sequence=0,decision="ACCEPTED",rationale="Existing V1 decision")).json()
    # Recreate the exact old seven-column parent, retaining real ZIP and all child receipts.
    with transaction() as conn:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("""CREATE TABLE old_packages (id TEXT PRIMARY KEY,trade_id TEXT NOT NULL REFERENCES trades(id),
            plan_version INTEGER NOT NULL,knowledge_cutoff TEXT NOT NULL,location TEXT NOT NULL,
            sha256 TEXT NOT NULL,created_at TEXT NOT NULL)""")
        conn.execute("INSERT INTO old_packages SELECT * FROM ai_packages")
        conn.execute("DROP TABLE ai_packages")
        conn.execute("ALTER TABLE old_packages RENAME TO ai_packages")
    db.initialize()
    db.initialize()
    assert client.get(path).content==original
    assert client.get("/api/ai/reviews/"+rid+"/original").content==review
    assert client.get(log).json()["decisions"]==[receipt]
    assert client.post("/api/ai/reviews",content=review,headers={"Content-Type":"application/json"}).json()["id"]==rid
    with transaction() as conn:
        assert list(conn.execute("PRAGMA foreign_key_check"))==[]
