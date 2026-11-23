"""Opt-in service evidence. Historical writers are loaded from the named Git revisions."""
import sqlite3
import subprocess
from itertools import count
from types import ModuleType
from uuid import UUID


from research_backend.app import db
from research_backend.app.analysis_evidence import canonical
from research_backend.app.strategy_evidence import fingerprint
from test_bounded_crt_smt_successors import canonical_successors
from test_offline_first_exit import calculate, snapshots
from test_offline_market_entry import EXPIRY, Q, publish, quote, tape, validate


def historical_writer(sha):
    path = "research_backend/app/offline_market_entry.py"
    source = subprocess.check_output(["git","show",f"{sha}:{path}"])
    module = ModuleType("research_backend.app.historical_entry_writer")
    module.__package__ = "research_backend.app"
    exec(compile(source,path,"exec"),module.__dict__)
    return module.validate_market_entries


def observe(run,data,entry,cutoff):
    try:
        response = calculate(run,data,entry,cutoff)
    except ValueError as error:
        return {"error":str(error)}
    row, = response["results"]
    return {key:response[key] for key in ("simulated_orders","simulated_fills","simulated_exits")} | {
        "status":row["status"],"entry_status":row.get("entry_status"),"exit":row["exit"]}


def test_capture(publish,monkeypatch,tmp_path):
    ids = count(1)
    monkeypatch.setattr("research_backend.app.strategy_routes.uuid4",lambda:UUID(int=next(ids)))
    run,intent = publish()
    data = tape([quote(Q+1,Q+1,4,110306,110309),quote(Q+4,Q+4,7,110400,110406)])
    other = tmp_path/"contradictory.sqlite3"
    with db.transaction() as src, sqlite3.connect(other) as dest:
        src.backup(dest)
    old = historical_writer("a844bcbacf6b279104ccdbe6816756938d8dd73a")(
        run,tape=data,tape_sha256=fingerprint(data))["results"][0]
    original = snapshots(run,old)
    print("V1",old["id"],old["fill"]["id"],fingerprint(data),observe(run,data,old,Q+4))
    assert snapshots(run,old)==original
    with monkeypatch.context() as scope:
        scope.setattr(db,"DATABASE",other)
        altered = old | {"status":"VERIFIED_EXPIRED","fill":None}
        with db.transaction() as conn:
            conn.execute("INSERT INTO offline_market_entry_results VALUES (?,?,?,?)",
                         (old["id"],run,intent["id"],canonical(altered)))
        print("CONTRADICTORY",observe(run,data,altered,Q+4))
        with db.transaction() as conn:
            assert conn.execute("SELECT count(*) FROM offline_first_exit_results").fetchone()[0]==0
    data = tape([])
    empty, = validate(run,data)["results"]
    assert empty["status"]=="VERIFIED_EXPIRED"
    for label,cutoff in (("Q-1",Q-1),("Q+1",Q+1),("expiry-1",EXPIRY-1),("expiry",EXPIRY)):
        print(label,observe(run,data,empty,cutoff))


def test_archived_no_fill_writers(publish):
    data = tape([quote(Q+1,Q+1,4,110306,110309),quote(Q+4,Q+4,4,110400,110406)])
    for kind,sha,status in (
        ("MARKET","a844bcb","AMBIGUOUS_QUOTE_ORDER"),
        ("LIMIT","c74f113","UNSUPPORTED_ENTRY_TYPE"),
        ("STOP","1420e22","UNSUPPORTED_ENTRY_TYPE"),
    ):
        run,intent = publish(kind=kind,entry_price=None if kind=="MARKET" else 1.10310)
        old, = historical_writer(sha)(run,tape=data,tape_sha256=fingerprint(data))["results"]
        before = snapshots(run,old)
        result = calculate(run,data,old,EXPIRY)
        assert old["status"]==result["results"][0]["entry_status"]==status
        assert result["simulated_orders"]==int(kind=="MARKET")
        assert result["simulated_fills"]==result["simulated_exits"]==0
        assert snapshots(run,old)==before
        print("ARCHIVED",sha,kind,status,result["simulated_orders"],result["simulated_exits"])
    with db.transaction() as conn:
        assert conn.execute("SELECT count(*) FROM offline_first_exit_results").fetchone()[0]==0
