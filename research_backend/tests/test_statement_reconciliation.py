"""Public API, independent cash and inventory expectations from a controlled export."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from research_backend.app import db

EXAMPLE = Path(__file__).resolve().parents[2]/"frontend/public/examples/controlled-statement.json"
ROOT = "/api/statements"


def register(client, payload):
    response = client.post(ROOT,content=payload if isinstance(payload,bytes) else json.dumps(payload).encode())
    assert response.status_code == 201, response.text
    return response.json()


def advance(client, receipt, limit=250):
    response = client.post(f"{ROOT}/{receipt['id']}/advance",params={"cursor":receipt["cursor"],"limit":limit})
    assert response.status_code == 200,response.text
    return response.json()


def example():
    return json.loads(EXAMPLE.read_bytes())


def test_reconstruction_resume_reimport_and_amendment(client):
    raw=EXAMPLE.read_bytes()
    first=register(client,raw)
    assert first["id"]==hashlib.sha256(raw).hexdigest()
    mid=advance(client,first,5)
    assert mid["cursor"]==5 and mid["report"] is None
    assert advance(client,first,5)==mid
    # A new interpreter, new server app and new SQLite connections use only the original DB.
    code="""import os,json
from pathlib import Path
from fastapi.testclient import TestClient
from research_backend.app import db
db.DATABASE=Path(os.environ['CASE_DB'])
from research_backend.app.main import app
with TestClient(app) as c:
 r=c.post('/api/statements/'+os.environ['CASE_ID']+'/advance?cursor=5')
 r.raise_for_status()
 print(json.dumps(r.json()))
"""
    proc=subprocess.run([sys.executable,"-c",code],env={**os.environ,"CASE_DB":str(db.DATABASE),"CASE_ID":first["id"],
                        "TRADEFORGE_DATA_DIR":str(db.DATABASE.parent)},capture_output=True,text=True,check=True)
    finished=json.loads(proc.stdout.strip().splitlines()[-1])
    assert advance(client,mid)==finished
    assert register(client,raw)==finished
    r=finished["report"]
    assert (r["status"],r["external_known_minor"],r["realized_known_minor"],r["charges_known_minor"],
            r["closing_minor"],r["equity_minor"],r["margin_minor"],r["balance_residual_minor"])==(
            "RECONCILED",8000,100,-200,107900,108000,1098,0)
    assert r["loss_limits"]["daily"]["loss_minor"]==100
    rows=client.get(f"{ROOT}/{first['id']}/rows").json()
    assert [x["cash_after_minor"] for x in rows]==[110000,110000,110000,109875,110175,109975,109950,109900,107900]
    assert (rows[5]["closed_lots"],rows[5]["opened_lots"],rows[5]["after_lots"],rows[5]["native_realized"])==("1/100","1/100","-1/100","-2")
    with db.transaction() as con:
        assert [con.execute("select count(*) from "+t).fetchone()[0] for t in ("statement_versions","statement_traces","statement_reports")]==[1,9,1]
    # A separate workspace processes exactly the same bytes uninterrupted.
    from unittest.mock import patch
    with patch.object(db,"DATABASE",db.DATABASE.with_name("fresh.sqlite3")):
        db.initialize()
        assert advance(client,register(client,raw))==finished
        assert client.get(f"{ROOT}/{first['id']}/rows").json()==rows
    amended=example()
    amended["rows"][3]["postings"][0]["amount"]="-1.26"
    amended.update(closing_balance="1078.99",closing_equity="1079.99")
    second=advance(client,register(client,amended))
    assert second["previous"]==first["id"] and second["version"]==2
    assert second["report"]["status"]=="RECONCILED"
    changes=client.get(f"{ROOT}/{second['id']}/evidence").json()["changes"]
    assert [c.get("row_id",c.get("field")) for c in changes]==["closing_balance","closing_equity","C1"]
    assert client.get(f"{ROOT}/{first['id']}").json()==finished


def test_one_cent_remains_visible(client):
    s=example()
    s["closing_balance"]="1079.01"
    r=advance(client,register(client,s))["report"]
    assert (r["status"],r["balance_residual_minor"],r["issues"])==("UNRESOLVED",-1,[{"source":"closing_balance","reason":"BALANCE_MISMATCH"}])
    assert r["loss_limits"]["daily"]["status"]=="UNAVAILABLE"


@pytest.mark.parametrize("rate,reason",[
    (None,"MISSING_FX"),("stale","STALE_OR_UNAVAILABLE_FX"),("future","STALE_OR_UNAVAILABLE_FX"),
    ("inverse",None),("direct",None)])
def test_jpy_profit_and_causal_fx(client,rate,reason):
    s=example()
    a,b=s["rows"][1],s["rows"][4]
    for row,price in ((a,"100"),(b,"101")):
        row.update(symbol="USDJPY",price=price)
    b.pop("realized")
    b["fx"]="FX1"
    s["specs"][0].update(symbol="USDJPY",quote_currency="JPY",tick="0.001")
    s.update(rows=[a,b],marks=[],closing_positions=[],closing_balance="1010",closing_equity="1010",closing_margin="0")
    if rate:
        s["rates"]=[dict(id="FX1",base="JPY" if rate=="direct" else "USD",quote="USD" if rate=="direct" else "JPY",
                         source="controlled-fx",rate="0.01" if rate=="direct" else "100",
                         at=a["at"] if rate=="stale" else b["at"],
                         available="2025-11-14T10:05:00Z" if rate=="future" else b["at"])]
    rec=advance(client,register(client,s))
    trace=client.get(f"{ROOT}/{rec['id']}/rows").json()[-1]
    assert (trace["native_realized"],trace["native_currency"])==("1000","JPY")
    if reason:
        assert rec["report"]["closing_minor"] is None
        assert {"source":"F3","reason":reason} in rec["report"]["issues"]
    else:
        assert rec["report"]["status"]=="RECONCILED" and trace["realized_minor"]==1000
        assert trace["fx"]["source"]=="controlled-fx"


def test_hedge_by_ticket_and_inline_charge(client):
    s=example()
    a,b,c=s["rows"][1],s["rows"][2],s["rows"][4]
    a.update(ticket="L",effect="IN",lots="0.02")
    b.update(ticket="S",effect="IN",kind="SELL")
    c.update(ticket="L",effect="OUT",realized={"amount":"4","currency":"USD"},postings=s["rows"][3]["postings"])
    s.update(mode="HEDGING",rows=[a,b,c],closing_balance="1002.75",closing_positions=[
        dict(ticket="L",symbol="EURUSD",lots="0.01",average="1.10000"),
        dict(ticket="S",symbol="EURUSD",lots="-0.01",average="1.10200")],closing_equity="1004.73",closing_margin="21.96")
    assert advance(client,register(client,s))["report"]["status"]=="RECONCILED"
    c["ticket"]="S"
    r=advance(client,register(client,s))["report"]
    assert r["closing_minor"] is None and {"source":"F3","reason":"POSITION_EFFECT_MISMATCH"} in r["issues"]


def test_input_rejections_no_receipt(client):
    s=example()
    s["rows"].append(s["rows"][0])
    assert client.post(ROOT,json=s).status_code==422
    assert client.post(ROOT,content=b'{"rows":[],"rows":[]}').status_code==422
    assert client.post(ROOT,content=b' '*2097153).status_code==413
    assert client.get(ROOT).json()==[]

@pytest.mark.parametrize("mutation,expected", [
    ("lots", "OFF_GRID_EXECUTION"), ("spec", "MISSING_OR_OVERLAPPING_DATED_SPEC"),
    ("mark", "STALE_OR_UNAVAILABLE_MARK"), ("history", "INCOMPLETE_HISTORY"),
    ("balance", "RUNNING_BALANCE_MISMATCH"), ("opening", "MISSING_OPENING_INVENTORY"),
])
def test_missing_or_contradictory_inputs(client,mutation,expected):
    s=example()
    if mutation=="lots":
        s["rows"][1]["lots"]="0.015"
    elif mutation=="spec":
        s["specs"].clear()
    elif mutation=="mark":
        s["marks"][0]["available"]="2025-11-15T00:00:01Z"
    elif mutation=="history":
        s["complete_history"]=False
    elif mutation=="opening":
        s["opening_positions"]=None
    else:
        s["rows"][0]["balance_after"]="1099.99"
    report=advance(client,register(client,s))["report"]
    assert report["status"]=="UNRESOLVED"
    assert expected in [issue["reason"] for issue in report["issues"]]


def test_round_each_posting_to_account_minor_unit(client):
    s=example()
    s.update(rows=[s["rows"][3]],closing_balance="999.98",closing_equity="999.98",closing_positions=[],closing_margin="0")
    postings=s["rows"][0]["postings"]
    postings[0]["amount"]="-0.005"
    postings.append(dict(postings[0],id="fee2",amount="-0.015"))
    rec=advance(client,register(client,s))
    assert rec["report"]["status"]=="RECONCILED"
    assert rec["report"]["charges_known_minor"]==-2
    trace=client.get(f"{ROOT}/{rec['id']}/rows").json()[0]
    assert [p["account_minor"] for p in trace["postings"]]==[0,-2]
