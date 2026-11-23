"""Capture the offline entry service on the named pre-implementation revision."""
import json
from itertools import count
from uuid import UUID

from research_backend.app.db import transaction
from research_backend.app.strategy_evidence import fingerprint
from test_offline_market_entry import Q, publish, quote, tape, validate
from test_bounded_crt_smt_successors import canonical_successors


def capture(publish,monkeypatch,data,expected):
    ids = count(1)
    monkeypatch.setattr("research_backend.app.strategy_routes.uuid4",lambda:UUID(int=next(ids)))
    cases = {}
    for kind,direction,price,status in expected:
        run,intent = publish(kind=kind,direction=direction,entry_price=price)
        result = validate(run,data,intent["id"])["results"][0]
        assert result["status"]==status
        with transaction() as db:
            stored = db.execute("SELECT payload FROM offline_market_entry_results WHERE id=?",(result["id"],)).fetchone()[0]
        cases[kind] = {"intent_id":intent["id"],"result_id":result["id"],
                       "fill_id":result["fill"]["id"] if result["fill"] else None,"status":status,
                       "serialized_payload":stored}
    print(json.dumps({"tape":data,"tape_sha256":fingerprint(data),"cases":cases}))


def test_capture_limit_baseline(publish,monkeypatch):
    data = tape([quote(Q+1,Q+1,4,110309,110312),quote(Q+2,Q+2,5,110307,110310)])
    capture(publish,monkeypatch,data,(
        ("MARKET","bearish",None,"SIMULATED_FILLED"),
        ("LIMIT","bullish",1.10310,"UNSUPPORTED_ENTRY_TYPE"),
    ))


def test_capture_stop_baseline(publish,monkeypatch):
    data = tape([quote(Q+1,Q+1,4,110306,110309),quote(Q+2,Q+2,5,110307,110310)])
    capture(publish,monkeypatch,data,(
        ("MARKET","bearish",None,"SIMULATED_FILLED"),
        ("LIMIT","bullish",1.10310,"SIMULATED_FILLED"),
        ("STOP","bullish",1.10310,"UNSUPPORTED_ENTRY_TYPE"),
    ))
