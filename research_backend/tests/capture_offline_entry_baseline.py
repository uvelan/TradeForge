"""Capture the merged offline implementation before changing its behavior."""
import json
from itertools import count
from pathlib import Path
from uuid import UUID

from research_backend.app.db import transaction
from research_backend.app.strategy_evidence import fingerprint
from test_offline_market_entry import Q, publish, quote, tape, validate
from test_bounded_crt_smt_successors import canonical_successors


def test_capture_baselines(publish,monkeypatch):
    ids = count(1)
    monkeypatch.setattr("research_backend.app.strategy_routes.uuid4",lambda:UUID(int=next(ids)))
    data = tape([quote(Q+1,Q+1,4,110309,110312),quote(Q+2,Q+2,5,110307,110310)])
    cases = {}
    for kind,direction,price,status in (
        ("MARKET","bearish",None,"SIMULATED_FILLED"),
        ("LIMIT","bullish",1.10310,"UNSUPPORTED_ENTRY_TYPE"),
    ):
        run,intent = publish(kind=kind,direction=direction,entry_price=price)
        result = validate(run,data,intent["id"])["results"][0]
        assert result["status"]==status
        with transaction() as db:
            stored = db.execute("SELECT payload FROM offline_market_entry_results WHERE id=?",(result["id"],)).fetchone()[0]
        cases[kind] = {"intent_id":intent["id"],"result_id":result["id"],
                       "fill_id":result["fill"]["id"] if result["fill"] else None,"status":status,
                       "serialized_payload":stored}
    print(json.dumps({"tape":data,"tape_sha256":fingerprint(data),"cases":cases}))
