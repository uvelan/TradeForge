"""Exercise fixed-price stop-market entries through the published-run service."""
import json
from itertools import count
from pathlib import Path
from uuid import UUID

import pytest

from research_backend.app.analysis_evidence import canonical
from research_backend.app.db import transaction
from research_backend.app.strategy_evidence import fingerprint
from test_bounded_crt_smt_successors import canonical_successors
from test_offline_market_entry import EXPIRY, Q, publish, quote, source_records, tape, validate

STOP = 1.10310
BASELINE = Path(__file__).resolve().parents[2]/"docs"/"implementation"/"evidence"/"TF-I10A2-stop-entry"/"merged-baseline.json"


def stop_result(published, scenario, data, side, status, winning=None):
    run,intent = published
    result = validate(run,data,intent["id"])
    row, = result["results"]
    assert row["source"]==intent
    assert row["identity"]["execution_data_fingerprint"]==fingerprint(data)
    assert row["identity"]["policy"]["id"]=="OFFLINE_FIXED_PRICE_STOP_ENTRY"
    assert row["identity"]["policy"]["version"]==1
    assert row["status"]==status
    assert result["simulated_fills"]==int(winning is not None)
    if status=="UNSUPPORTED_STOP_PRICE_GRID":
        assert row["order"] is None and result["simulated_orders"]==0
    else:
        assert result["simulated_orders"]==1
        assert row["order"]=={
            "kind":"STOP","trigger_side":side,"executable_side":side,"strictly_after_ms":Q,
            "expires_at_ms":EXPIRY,"stop_price":{"kind":"FIXED_ABSOLUTE","value":STOP,"basis":"BID"},
            "stop_price_units":110310,"scale":100000,
        }
    fill = row["fill"]
    if winning is None:
        assert fill is None
    else:
        chosen,units,price = winning
        assert (fill["quote"],fill["time_ms"],fill["side"],fill["price_units"],fill["price"])==(
            chosen | {"scale":100000},chosen["available_at_ms"],side,units,price)
    assert validate(run,data,intent["id"])==result
    print(json.dumps({"case":scenario,"tape_sha256":fingerprint(data),"intent_id":intent["id"],
                      "result_id":row["id"],"expected_status":status,"observed_status":row["status"],
                      "expected_quote":winning[0] if winning else None,
                      "expected_price":winning[2] if winning else None,
                      "orders":result["simulated_orders"],"fills":result["simulated_fills"],
                      "fill":fill},sort_keys=True))
    return row


@pytest.mark.parametrize("direction,side,miss_prices,touch_prices,gap_prices,gap_units,gap_price",[
    ("bullish","ASK",(110306,110309),(110307,110310),(110308,110314),110314,"1.10314"),
    ("bearish","BID",(110311,110314),(110310,110313),(110306,110313),110306,"1.10306"),
])
def test_stop_exact_touch_and_adverse_gap(publish,direction,side,miss_prices,touch_prices,gap_prices,gap_units,gap_price):
    published = publish(kind="STOP",direction=direction,entry_price=STOP)
    run,intent = published
    assert (intent["direction"],intent["qualified_at"],intent["status"])==(
        "LONG" if side=="ASK" else "SHORT",Q//1000,"AWAITING_EXECUTION_VALIDATION")
    before = source_records(run)
    miss = quote(Q+1,Q+1,4,*miss_prices)
    touch = quote(Q+2,Q+2,5,*touch_prices)
    gap = quote(Q+3,Q+3,6,*gap_prices)
    stop_result(published,f"{side} miss prefix",tape([miss],[(Q+1,Q+1)]),
                side,"INSUFFICIENT_QUOTE_COVERAGE")
    stop_result(published,f"{side} exact stop touch",tape([miss,touch]),
                side,"SIMULATED_FILLED",(touch,110310,"1.10310"))
    original = stop_result(published,f"{side} adverse gap",tape([miss,gap]),
                           side,"SIMULATED_FILLED",(gap,gap_units,gap_price))
    with transaction() as db:
        assert db.execute("SELECT count(*) FROM offline_market_entry_results WHERE run_id=?",(run,)).fetchone()[0]==3
    assert source_records(run)==before
    assert original["fill"]["id"]


def test_stop_observation_delivery_expiry_and_conflicting_priority(publish):
    published = publish(kind="STOP",direction="bullish",entry_price=STOP)
    before = source_records(published[0])
    miss = quote(Q+1,Q+1,4,110306,110309)
    touch = quote(Q+2,Q+2,5,110307,110310)
    old_touch = quote(Q-1,Q+1,2,110311,110315)
    boundary_touch = quote(Q,Q+1,3,110312,110316)
    at_expiry = quote(EXPIRY-1,EXPIRY,7,110308,110311)
    late = quote(EXPIRY,EXPIRY+1,8,110312,110316)

    stop_result(published,"prequalification and qualification touches delivered late",
                tape([old_touch,boundary_touch,miss,touch]),"ASK","SIMULATED_FILLED",(touch,110310,"1.10310"))
    stop_result(published,"trigger delivered at expiry",tape([miss,at_expiry]),
                "ASK","SIMULATED_FILLED",(at_expiry,110311,"1.10311"))
    stop_result(published,"sealed window no eligible trigger",tape([miss,late]),"ASK","VERIFIED_EXPIRED")
    stop_result(published,"gap before apparent trigger",tape([miss,touch],[(Q+2,EXPIRY)]),
                "ASK","INSUFFICIENT_QUOTE_COVERAGE")
    later_delivered = quote(Q+1,Q+3,1,110312,110315)
    stop_result(published,"delivery before observation",tape([later_delivered,touch]),
                "ASK","SIMULATED_FILLED",(touch,110310,"1.10310"))
    earlier_observed = quote(Q+1,Q+2,6,110306,110311)
    stop_result(published,"earlier observation breaks delivery tie",tape([touch,earlier_observed]),
                "ASK","SIMULATED_FILLED",(earlier_observed,110311,"1.10311"))
    later_sequence = quote(Q+2,Q+2,6,110309,110314)
    stop_result(published,"source sequence breaks equal-clock tie",tape([later_sequence,touch]),
                "ASK","SIMULATED_FILLED",(touch,110310,"1.10310"))

    conflict = quote(Q+1,Q+1,4,110307,110311)
    later_priority = {"time_ms":Q+3,"available_at_ms":Q+3,"source_order":6}
    for order,arrange in (("forward",list),("reverse",lambda rows:list(reversed(rows)))):
        stop_result(published,f"trigger/miss first, {order}",tape(arrange([miss,conflict,touch])),
                    "ASK","AMBIGUOUS_QUOTE_ORDER")
        stop_result(published,f"trigger/miss after certain trigger, {order}",
                    tape(arrange([touch,miss | later_priority,conflict | later_priority])),
                    "ASK","SIMULATED_FILLED",(touch,110310,"1.10310"))
    assert source_records(published[0])==before


def test_off_grid_stop_returns_explicit_unsupported_result(publish):
    published = publish(kind="STOP",direction="bullish",entry_price=1.103101)
    stop_result(published,"off-grid stop",tape([quote(Q+1,Q+1,4,110312,110315)]),
                "ASK","UNSUPPORTED_STOP_PRICE_GRID")


def test_replay_matches_merged_market_and_limit_bytes_and_updates_stop(publish,monkeypatch):
    ids = count(1)
    monkeypatch.setattr("research_backend.app.strategy_routes.uuid4",lambda:UUID(int=next(ids)))
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    data = baseline["tape"]
    assert fingerprint(data)==baseline["tape_sha256"]
    for kind,direction,entry_price in (("MARKET","bearish",None),("LIMIT","bullish",STOP)):
        merged = baseline["cases"][kind]
        run,intent = publish(kind=kind,direction=direction,entry_price=entry_price)
        before = source_records(run)
        assert intent["id"]==merged["intent_id"]
        row, = validate(run,data,intent["id"])["results"]
        assert row["id"]==merged["result_id"] and row["fill"]["id"]==merged["fill_id"]
        expected_bytes = merged["serialized_payload"].encode()
        assert canonical(row).encode()==expected_bytes
        with transaction() as db:
            stored, = db.execute("SELECT CAST(payload AS BLOB) FROM offline_market_entry_results WHERE id=?",
                                 (row["id"],)).fetchone()
            assert stored==expected_bytes
        assert source_records(run)==before
        print(json.dumps({"case":f"merged {kind} compatibility","tape_sha256":fingerprint(data),
                          "result_id":row["id"],"fill_id":row["fill"]["id"],
                          "payload_bytes":len(stored),"byte_identical":True},sort_keys=True))

    merged = baseline["cases"]["STOP"]
    published = publish(kind="STOP",direction="bullish",entry_price=STOP)
    run,intent = published
    before = source_records(run)
    assert intent["id"]==merged["intent_id"]
    assert merged["status"]=="UNSUPPORTED_ENTRY_TYPE" and merged["fill_id"] is None
    with transaction() as db:
        db.execute("INSERT INTO offline_market_entry_results VALUES (?,?,?,?)",
                   (merged["result_id"],run,intent["id"],merged["serialized_payload"]))
    result = stop_result(published,"same STOP intent and tape as merged baseline",data,
                         "ASK","SIMULATED_FILLED",(data["quotes"][1],110310,"1.10310"))
    assert result["id"]!=merged["result_id"]
    with transaction() as db:
        stored = dict(db.execute("SELECT id,CAST(payload AS BLOB) FROM offline_market_entry_results WHERE run_id=?",
                                 (run,)))
        assert set(stored)=={merged["result_id"],result["id"]}
        assert stored[merged["result_id"]]==merged["serialized_payload"].encode()
        assert stored[result["id"]]==canonical(result).encode()
    assert source_records(run)==before
