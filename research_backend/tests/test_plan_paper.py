"""Controlled fault and concurrency checks. Genuine-price acceptance lives in verify_plan_paper."""
import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
import lzma
import struct

import pytest

from research_backend.app.db import rows
from research_backend.app.historical_import import FEED,import_bi5
from research_backend.app.storage import read_partition
from test_chart_snapshot_lanes import png_bytes


@pytest.fixture
def journey(client,plan):
    day=datetime(2025,11,13,tzinfo=UTC)
    hour=int(day.replace(hour=13).timestamp())
    prices=[(299500,112003,112000),(300000,112004,112001),(300005,112007,112003),
            (301500,112010,112008),(301700,112005,112000),(400000,112006,112002)]
    raw=lzma.compress(b"".join(struct.pack(">IIIff",ms,ask,bid,1,1) for ms,ask,bid in prices))
    imported=import_bi5(raw,"EURUSD",day,13)
    part=next(p for p in imported["candle_datasets"] if p["timeframe"]=="M5" and p["price_basis"]=="BID")
    bar=read_partition(part)[0]
    pin={k:part[k] for k in ("id","sha256")}
    captured=client.post("/api/chart-snapshots",json=dict(symbol="EURUSD",timeframe="M5",source="DUKASCOPY_SAVED",
        cutoff=hour+300,png=base64.b64encode(png_bytes()).decode(),lanes=[dict(symbol="EURUSD",timeframe="M5",
        role="primary",feed_id=FEED,price_basis="BID",partitions=[pin],bars=[bar])]))
    assert captured.status_code==201,captured.text
    plan=plan|dict(snapshot_id=captured.json()["id"],entry="1.12000",stop="1.11900",target="1.12200")
    trade=client.post("/api/trades",json=plan).json()
    return trade,plan,dict(version=1,sequence=1,quantity_eur=1000,kind="ENTRY",decision_ms=(hour+300)*1000)


def test_paper_compare_and_append(client,journey):
    trade,plan,cmd=journey
    path="/api/paper/plans/"+trade["id"]
    def post(command):
        return client.post(path+"/actions",json=command)
    assert client.get(path).json()["events"]==[]
    with ThreadPoolExecutor(2) as pool:
        replies=list(pool.map(post,[cmd,cmd]))
    a,b=replies
    assert a.status_code==b.status_code==200,a.text
    assert a.json()==b.json()
    assert (a.json()["side"],a.json()["event"]["price_units"],a.json()["quote"]["source_order"])==("ASK",112007,2)
    assert post(cmd|{"quantity_eur":900}).status_code==409
    assert client.post("/api/trades/"+trade["id"]+"/decline",json={"reason":"Retrospective no trade"}).status_code==409
    assert client.post("/api/trades/"+trade["id"] + "/revisions?expected_version=1",json=plan|{"hypothesis":"Different thesis after entry"}).is_success
    reduce=cmd|dict(sequence=2,kind="REDUCE",quantity_eur=400,decision_ms=cmd["decision_ms"]+1000)
    for bad in ({"version":2},{"quantity_eur":1001},{"quantity_eur":1000},{"sequence":3},{"kind":"CLOSE"},
                {"decision_ms":cmd["decision_ms"]},{"decision_ms":cmd["decision_ms"]+99999}):
        assert post(reduce|bad).status_code==409
    assert post(reduce).json()["event"]["position_units"]==600
    final=post(reduce|dict(sequence=3,kind="CLOSE",decision_ms=cmd["decision_ms"]+1600,quantity_eur=600)).json()
    assert final["realized_usd_units"]==-3800
    assert final["event"]["cash_units"]==999996200
    assert len(client.get(path).json()["events"])==3
    assert post(cmd).json()==a.json()
    assert len(rows("SELECT * FROM plan_paper_events"))==3


def test_unsupported_sizing_chronology_and_no_trade(client,journey):
    trade,plan,cmd=journey
    path="/api/paper/plans/"+trade["id"]+"/actions"
    for change in ({"decision_ms":cmd["decision_ms"]-1},{"quantity_eur":100000},{"kind":"CLOSE"}):
        assert client.post(path,json=cmd|change).status_code==409
    assert client.post("/api/trades/"+trade["id"]+"/decline",json={"reason":"Entry conditions did not hold"}).is_success
    assert client.post(path,json=cmd).status_code==409
    assert rows("SELECT * FROM plan_paper_events")==[]
