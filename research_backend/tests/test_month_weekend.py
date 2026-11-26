"""Public API closure checks. Prices are controlled integer EURUSD points."""
from datetime import UTC, datetime, timedelta
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import lzma
import struct

import pytest

from research_backend.app.historical_import import import_bi5


def seed(close, sunday_hour, delta, kind):
    reopen = (close + timedelta(days=2)).replace(hour=sunday_hour)
    # Only the final observation time changes within each pair.
    friday = [(3599980,110000)]
    if kind == "open":
        friday.append((3599990,110010))
    friday.append((3600000-delta,110000 if kind=="flat" else 110010))
    for dt, values in [(close-timedelta(hours=1),friday),
                       (reopen,[(0,109900),(5,109902),(10,109910),(20,109911)]+[(ms,109911) for ms in range(50000,600001,50000)])]:
        raw=lzma.compress(b"".join(struct.pack(">IIIff",ms,bid+2,bid,1,1) for ms,bid in values))
        import_bi5(raw,"EURUSD",dt,dt.hour)
    return int(reopen.timestamp())


def advance(client, route, index):
    r=client.post(route+"/advance",params={"index":index})
    assert r.status_code==200,r.text
    return r.json()


@pytest.mark.parametrize("close,sunday_hour",[(datetime(2025,11,14,22,tzinfo=UTC),22),
                                              (datetime(2025,3,7,22,tzinfo=UTC),21)])
@pytest.mark.parametrize("delta",[1,2])
@pytest.mark.parametrize("kind",["open","pending","flat"])
def test_weekend(client,close,sunday_hour,delta,kind):
    reopen=seed(close,sunday_hour,delta,kind)
    path="/api/month-replay/"+close.strftime("%Y-%m")
    report=client.get(path).json()
    route=path+"/"+report["session"]
    i=0
    close_s=int(close.timestamp())
    events=[]
    closure=[]
    while True:
        step=advance(client,route,i)
        if step["through"]>=close_s:
            assert advance(client,route,i)==step
        events.extend(step["events"])
        closure.extend(c for c in step["coverage"] if close_s<=c["start"]<reopen)
        i=step["next_index"]
        if step["through"]>reopen:
            break
    kinds=[e["kind"] for e in events]
    assert [c["start"] for c in closure]==list(range(close_s,reopen,600))
    assert all(c["status"]=="SCHEDULED_CLOSURE" for c in closure)
    assert [g for c in closure for g in c["gaps"]]==([[close_s*1000-1,close_s*1000]] if delta==2 else [])
    if kind=="flat":
        assert kinds==["QUALIFIED","ENTRY"]
        assert events[0]["quote"]["time_ms"]==reopen*1000+10
    else:
        assert kinds==(["QUALIFIED","ENTRY","UNRESOLVED"] if kind=="open" else ["QUALIFIED","UNRESOLVED"])
        unresolved=events[-1]
        assert unresolved["quote"]["time_ms"]==close_s*1000-delta
        assert unresolved["remaining"]==int(kind=="open")
        assert unresolved["decision_id"]==events[0]["id"]
        assert unresolved["reason"]==("OPEN_SESSION_QUOTE_GAP" if delta==2 else "SCHEDULED_MARKET_CLOSURE")
        assert unresolved.get("missing_from_ms",unresolved.get("closure_at_ms"))==close_s*1000-(delta-1)
        assert ("fill" in step["state"]["position"])==(kind=="open")

def test_historical_writer_receipt_is_inspectable_only(client):
    from research_backend.app.db import transaction
    from research_backend.app.strategy_evidence import fingerprint

    seed(datetime(2025,11,14,22,tzinfo=UTC),22,1,"open")
    old=json.loads(Path(__file__).with_name("month_weekend_v1.json").read_text())
    path="/api/month-replay/2025-11"
    current=client.get(path).json()
    binding=current["binding"]|{"policy":old["policy"]}
    assert fingerprint(binding)==old["session"]!=current["session"]
    assert fingerprint(old["step"])==old["digest"]
    with transaction() as db:
        db.execute("INSERT INTO month_replay_manifests VALUES(?,?,?)",(old["session"],"2025-11",json.dumps(binding)))
        db.execute("INSERT INTO month_replay_steps VALUES(?,?,?,?)",
                   (old["session"],old["index"],json.dumps(old["step"]),old["digest"]))
    report=client.get(path,params={"session":old["session"]}).json()
    route=path+"/"+old["session"]
    assert report["checkpoint"]==old["step"] and not report["policy_current"]
    assert advance(client,route,old["index"])==old["step"]
    quote=old["step"]["state"]["position"]["fill"]
    assert client.get(route+"/quote",params={"time_ms":quote["time_ms"],"ordinal":quote["ordinal"]}).json()==quote
    rejected=client.post(route+"/advance",params={"index":old["step"]["next_index"]})
    assert rejected.status_code==409 and "read-only" in rejected.text
    assert client.get(route+"/steps").json()==[{"index":old["index"],**old["step"]}]


def drive_process(database, month, until):
    from fastapi.testclient import TestClient
    from research_backend.app import db
    from research_backend.app.main import app
    from research_backend.app.strategy_evidence import fingerprint

    db.DATABASE=Path(database)
    with TestClient(app) as api:
        path="/api/month-replay/"+month
        report=api.get(path).json()
        route=path+"/"+report["session"]
        frontier=report["checkpoint"]
        idx=frontier["next_index"] if frontier else 0
        pages=[]
        after=-1
        while batch:=api.get(route+"/steps",params={"after":after}).json():
            pages.extend(batch)
            after=batch[-1]["index"]
        if pages:
            last=pages[-1]
            assert advance(api,route,last["index"])==frontier
        while True:
            step=advance(api,route,idx)
            pages.append({"index":idx,**step})
            idx=step["next_index"]
            if step["through"]>=until or step["completed"]:
                break
        print(json.dumps({"through":step["through"],"digest":fingerprint(pages),"events":[e for p in pages for e in p["events"]]}))


@pytest.mark.parametrize("close,hour,delta",[(datetime(2025,11,14,22,tzinfo=UTC),22,2),
                                             (datetime(2025,3,7,22,tzinfo=UTC),21,1)])
def test_actual_process_restarts(client,tmp_path,close,hour,delta):
    from research_backend.app.db import DATABASE
    reopen=seed(close,hour,delta,"open")
    def child(file,until):
        env=os.environ|{"TRADEFORGE_DATA_DIR":str(tmp_path)}
        return json.loads(subprocess.check_output([sys.executable,"-m","research_backend.tests.test_month_weekend",
            str(file),close.strftime("%Y-%m"),str(until)],env=env,timeout=60))
    clone=tmp_path/"reference.sqlite3"
    with closing(sqlite3.connect(DATABASE)) as src,closing(sqlite3.connect(clone)) as dest:
        src.backup(dest)
    expected=child(clone,2**32)
    for frontier in (int(close.timestamp()),int(close.timestamp())+86400,reopen+600):
        assert child(DATABASE,frontier)["through"]==frontier
    actual=child(DATABASE,2**32)
    assert actual==expected
    assert [e["kind"] for e in actual["events"]]==["QUALIFIED","ENTRY","UNRESOLVED"]
    print("Restart ledger",close.date(),actual["digest"])


if __name__=="__main__":
    drive_process(sys.argv[1],sys.argv[2],int(sys.argv[3]))
