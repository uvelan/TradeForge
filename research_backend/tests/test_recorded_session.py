from hashlib import sha256
import os
import json
import sys
import subprocess
from pathlib import Path

import pytest

from research_backend.app.db import transaction
from research_backend.app.recorded_session import replay
from test_bounded_crt_smt_successors import canonical_successors
from test_offline_market_entry import publish, source_records
from test_recorded_market_entry import controlled, prepare, shard, Q, REAL_PIN
from test_recorded_first_exit import FIXED


def test_open_session_gap_blocks_later_close(client, controlled):
    run, intent = controlled(direction="bullish",management=FIXED)
    from test_recorded_limit_entry import bi5
    spec,_ = prepare(bi5([(-1000,116170,116174),(400,116170,116174),(60401,116000,116004)]))
    result = replay(run,spec["start"],spec["end"],Q+60401)
    pos, = result["positions"]
    assert pos["entry"]["fill"]["price_units"]==116174 and pos["exit"] is None
    assert pos["status"]=="INSUFFICIENT_QUOTE_COVERAGE"


def test_session_checkpoint_partition_and_reference(client, controlled):
    path=os.environ.get("TRADEFORGE_T07_REAL_BI5")
    if path:
        raw = Path(path).read_bytes()
        assert sha256(raw).hexdigest()==REAL_PIN
    else:
        from test_recorded_limit_entry import bi5
        raw = bi5([(-1000,116172,116174),(1403,116170,116174),(39647,116176,116178),
                   (55600,116190,116192),(61606,116183,116185)])
    spec,rows = prepare(raw)
    management = FIXED | {"initial_stop":{"kind":"FIXED_PIPS","distance":1},
              "target":{"kind":"REWARD_MULTIPLE","multiple":4},"break_even_at_r":0.2,
              "partial_exit":{"at_r":0.2,"percent":25},"trailing_pips":1.5,"time_exit":{"unit":"SECONDS","count":60}}
    run,intent = controlled(direction="bullish",management=management)
    source = source_records(run)
    route = f"/api/historical-strategy-runs/v1/{run}/recorded-replay"
    def call(ms,parts=(),resume=None):
        with client.stream("POST",route,json=dict(start=spec["start"],end=spec["end"],cutoff_ms=Q+ms,
                           partitions=list(parts),resume=resume)) as res:
            res.read()
            assert res.status_code==200,res.text
            return res.json()
    fresh = call(61606)
    p, = fresh["positions"]
    assert p["entry"]["source"]==intent
    assert any(d["id"]==p["decision_id"] for d in fresh["decisions"])
    assert (p["entry"]["fill"]["price_units"],p["entry"]["fill"]["time_ms"])==(116174,Q+1403)
    assert (p["exit"]["reason"],p["exit"]["price_units"],p["exit"]["time_ms"])==("TIME_EXIT",116183,Q+61606)
    assert p["events"]["partial_exit"]["closed_fraction"]=={"numerator":1,"denominator":4}
    assert p["exit"]["closed_fraction"]=={"numerator":3,"denominator":4}
    assert p["events"]["activation"] and p["events"]["ratchet"]
    if path:
        assert [p["entry"]["fill"]["quote"]["source_order"],p["events"]["partial_exit"]["quote"]["source_order"],
                p["exit"]["quote"]["source_order"]]==[2971,2982,3004]
    middle=len(rows)//2
    split=[shard(rows[:middle+1]),shard(rows[middle:])]
    from research_backend.app.db import DATABASE
    env = os.environ | {"TRADEFORGE_STORAGE":str(DATABASE.parent), "TRADEFORGE_DATA_DIR":str(DATABASE.parent)}
    def process(ms, resume=None, interrupt=False):
        code = """import json,os,sys
from pathlib import Path
from research_backend.app import db
db.DATABASE=Path(sys.argv[1])
from fastapi.testclient import TestClient
from research_backend.app.main import app
with TestClient(app,base_url="http://localhost") as client:
    r=client.post(sys.argv[2],json=json.loads(sys.argv[3]))
    r.raise_for_status()
    print(json.dumps(r.json()),flush=True)
    if sys.argv[4]=="interrupt": os._exit(75)
"""
        spec_body=dict(start=spec["start"],end=spec["end"],cutoff_ms=Q+ms,partitions=split[::-1],resume=resume)
        child=subprocess.run([sys.executable,"-c",code,str(DATABASE),route,json.dumps(spec_body),
                              "interrupt" if interrupt else "complete"],capture_output=True,text=True,env=env,timeout=45)
        assert child.returncode==(75 if interrupt else 0),child.stderr
        return json.loads(child.stdout)
    early=process(39647,interrupt=True)
    assert early["positions"][0]["remaining"]=={"numerator":3,"denominator":4}
    assert early["positions"][0]["exit"] is None
    resumed=process(61606,early["session_id"])
    for key in ("decisions","positions","binding","intervals"):
        assert resumed[key]==fresh[key]
    assert call(61606,split,resumed["session_id"])==resumed
    assert call(39647,split)==early
    # A new, earlier save arrives after the resumed session has already reached its horizon.
    delayed = call(55600, split, resumed["session_id"])
    def reopen(session, **extra):
        return client.get(route,params={"session_id":session,**extra}).json()
    def persisted(receipt):
        return {k:v for k,v in receipt.items() if k not in ("bi5_reads","parquet_reads")}
    assert reopen(resumed["session_id"])==persisted(resumed)
    assert reopen(resumed["session_id"],cutoff_ms=Q+55600)==persisted(delayed)
    assert reopen(fresh["session_id"])==persisted(fresh)
    assert fresh["session_id"]!=resumed["session_id"]
    assert client.get(route).json()==persisted(resumed)
    rejected=client.post(route,json=dict(start=spec["start"],end=spec["end"],cutoff_ms=Q+61606,
                                         partitions=[split[0]]))
    assert rejected.status_code==422 and "omit" in rejected.text
    assert source_records(run)==source
    with transaction() as db:
        assert db.execute("SELECT count(*) FROM recorded_strategy_checkpoints").fetchone()[0]==4

@pytest.mark.parametrize("kind,offset,units", [("LIMIT",17210,116274),("STOP",449,116276)])
@pytest.mark.parametrize("controlled", [(int(Q//1000)-14120+3600,90)],indirect=True)
def test_fixed_price_entries_on_session_path(controlled,kind,offset,units):
    from test_recorded_limit_entry import bi5,QUAL,PIN
    run,intent = controlled(direction="bullish",kind=kind,entry_price=(1.16274 if kind=="LIMIT" else 1.16276),
                            management=FIXED)
    path=os.environ.get("TRADEFORGE_T07_LIMIT_BI5")
    raw=Path(path).read_bytes() if path else bi5([(-361,116270,116274),(86,116271,116275),
                           (449,116271,116276),(17210,116271,116274)])
    if path:
        assert sha256(raw).hexdigest()==PIN
    spec,_=prepare(raw,14,QUAL)
    result=replay(run,spec["start"],spec["end"],QUAL+offset)
    pos,=result["positions"]
    assert pos["entry"]["source"]==intent
    fill=pos["entry"]["fill"]
    assert (fill["side"],fill["price_units"],fill["time_ms"])==("ASK",units,QUAL+offset)
    assert pos["closed"]["numerator"]==0 and pos["remaining"]=={"numerator":1,"denominator":1}
