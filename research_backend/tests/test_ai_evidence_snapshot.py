from hashlib import sha256
from io import BytesIO
import json
import zipfile

import pytest

from research_backend.app import db, domain
from research_backend.app.schemas import Outcome, TradePlan


@pytest.mark.parametrize("action",["close","revise","cancel"])
def test_export_pins_one_snapshot_during_concurrent_trade_change(client, plan, monkeypatch, action):
    # Wall-clock equality is not a valid substitute for the pinned SQLite sequence.
    monkeypatch.setattr(domain,"utc_now",lambda: "2026-11-24T14:00:00+00:00")
    trade=client.post("/api/trades",json=plan).json()
    uid=trade["id"]
    original_connect=db.connect
    attempted=[]
    failures=[]

    def connect():
        conn=original_connect()
        def before_query(statement):
            if attempted or not statement.startswith(("SELECT version,payload,created_at FROM plan_versions",
                                                     "SELECT id,version,payload,created_at FROM plan_versions")):
                return
            attempted.append(True)
            try:
                if action=="close":
                    domain.close_trade(uid,Outcome(pnl_usd="99",result_r="2"))
                elif action=="revise":
                    domain.revise(uid,TradePlan.model_validate(plan),expected_version=1)
                else:
                    domain.decline_trade(uid,"Concurrent cancellation")
            except Exception as error:
                failures.append(error)
        conn.set_trace_callback(before_query)
        return conn

    monkeypatch.setattr(db,"connect",connect)
    exported=client.post(f"/api/ai/exports/{uid}",params={"plan_version":1})
    assert attempted and not failures
    assert exported.status_code==201,exported.text
    package=exported.json()
    data=client.get(package["download_url"].removeprefix("/research")).content
    assert sha256(data).hexdigest()==package["sha256"]
    with zipfile.ZipFile(BytesIO(data)) as archive:
        assert [json.loads(line)["kind"] for line in archive.read("data/timeline.jsonl").splitlines()]==["PLAN_CREATED"]
        manifest=json.loads(archive.read("manifest.json"))
        evidence=manifest["evidence_snapshot"]
        assert evidence["through_event_sequence"]==1
        assert evidence["policy"]=="SQLITE_READ_SNAPSHOT_V1"
        assert set(evidence["components_sha256"])==set(archive.namelist())-{"manifest.json"}
        for name,digest in evidence["components_sha256"].items():
            assert sha256(archive.read(name)).hexdigest()==digest
        assert manifest["plan_version"]==1
        review=json.loads(archive.read("response/AI_REVIEW_TEMPLATE.json"))
    assert client.post("/api/ai/reviews",json=review).status_code==201
    actual=client.get(f"/api/trades/{uid}").json()
    assert len(actual["events"])==2
    if action=="revise":
        assert actual["versions"][0]["version"]==2
        assert client.post(f"/api/ai/exports/{uid}",params={"plan_version":1}).status_code==409
    else:
        assert actual["status"]==("CLOSED" if action=="close" else "CANCELLED")
        assert client.post(f"/api/ai/exports/{uid}").status_code==422
