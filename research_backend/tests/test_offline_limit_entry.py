"""Verify LIMIT entry through completed historical intent publication."""
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

LIMIT = 1.10310
BASELINE = Path(__file__).resolve().parents[2]/"docs"/"implementation"/"evidence"/"TF-I10A2-limit-entry"/"merged-baseline.json"


def check(published, name, data, status, side, expected=None):
    identifier,intent = published
    actual = validate(identifier,data,intent["id"])
    result, = actual["results"]
    assert result["source"]==intent
    assert result["identity"]["execution_data_fingerprint"]==fingerprint(data)
    assert result["identity"]["policy"]["id"]=="OFFLINE_FIXED_PRICE_LIMIT_ENTRY"
    assert result["identity"]["policy"]["version"]==1
    assert result["status"]==status
    assert actual["simulated_fills"]==int(expected is not None)
    order = result["order"]
    if status=="UNSUPPORTED_LIMIT_PRICE_GRID":
        assert order is None and actual["simulated_orders"]==0
    else:
        assert order=={"kind":"LIMIT","executable_side":side,"strictly_after_ms":Q,
                       "expires_at_ms":EXPIRY,"limit_price":{"kind":"FIXED_ABSOLUTE","value":LIMIT,"basis":"BID"},
                       "limit_price_units":110310,"scale":100000}
    fill = result["fill"]
    if expected is None:
        assert fill is None
    else:
        chosen,units,price = expected
        assert (fill["quote"],fill["time_ms"],fill["side"],fill["price_units"],fill["price"])==(
            chosen | {"scale":100000},chosen["available_at_ms"],side,units,price)
    assert validate(identifier,data,intent["id"])==actual
    print(json.dumps({"case":name,"tape_sha256":fingerprint(data),"intent_id":intent["id"],
                      "result_id":result["id"],"expected":status,"observed":result["status"],
                      "orders":actual["simulated_orders"],"fills":actual["simulated_fills"],
                      "fill":fill},sort_keys=True))
    return result


@pytest.mark.parametrize("direction,side,wrong_side,exact,better,improved",[
    ("bullish","ASK",(110309,110312),(110307,110310),(110305,110308),"1.10308"),
    ("bearish","BID",(110308,110311),(110310,110313),(110312,110315),"1.10312"),
])
def test_executable_spread_exact_touch_and_improvement(publish,direction,side,wrong_side,exact,better,improved):
    published = publish(kind="LIMIT",direction=direction,entry_price=LIMIT)
    identifier,intent = published
    assert (intent["direction"],intent["qualified_at"],intent["status"])==(
        "LONG" if side=="ASK" else "SHORT",Q//1000,"AWAITING_EXECUTION_VALIDATION")
    before = source_records(identifier)
    miss = quote(Q+1,Q+1,4,*wrong_side)
    hit = quote(Q+2,Q+2,5,*exact)
    improved_hit = quote(Q+1,Q+1,4,*better)
    check(published,f"{side} wrong-side quote only",tape([miss],[(Q+1,Q+1)]),
          "INSUFFICIENT_QUOTE_COVERAGE",side)
    check(published,f"{side} exact touch after wrong-side quote",tape([miss,hit]),
          "SIMULATED_FILLED",side,(hit,110310,"1.10310"))
    check(published,f"{side} price improvement",tape([improved_hit,hit]),
          "SIMULATED_FILLED",side,(improved_hit,110308 if side=="ASK" else 110312,improved))
    with transaction() as db:
        assert db.execute("SELECT count(*) FROM offline_market_entry_results WHERE run_id=?",
                          (identifier,)).fetchone()[0]==3
    assert source_records(identifier)==before


def test_first_limit_touch_and_coverage(publish):
    published = publish(kind="LIMIT",direction="bullish",entry_price=LIMIT)
    before = source_records(published[0])
    miss = quote(Q+1,Q+1,4,110309,110312)
    early_touch = quote(Q-1,Q+1,2,110300,110305)
    at_qualification = quote(Q,Q+1,3,110300,110305)
    hit = quote(Q+2,Q+2,5,110307,110310)
    expiry_hit = quote(EXPIRY-1,EXPIRY,6,110307,110309)
    late = quote(EXPIRY,EXPIRY+1,7,110302,110305)
    check(published,"prequalification and equal-time touches delivered late",
          tape([early_touch,at_qualification,miss,hit]),"SIMULATED_FILLED","ASK",(hit,110310,"1.10310"))
    check(published,"touch delivered exactly at expiry",tape([miss,expiry_hit]),
          "SIMULATED_FILLED","ASK",(expiry_hit,110309,"1.10309"))
    check(published,"sealed no touch",tape([miss,late]),"VERIFIED_EXPIRED","ASK")
    check(published,"one-millisecond gap before apparent fill",tape([miss,hit],[(Q+2,EXPIRY)]),
          "INSUFFICIENT_QUOTE_COVERAGE","ASK")
    delayed = quote(Q+1,Q+4,1,110305,110308)
    check(published,"delivery wins over earlier observation",tape([delayed,hit]),
          "SIMULATED_FILLED","ASK",(hit,110310,"1.10310"))
    same_clock_later_sequence = quote(Q+2,Q+2,6,110304,110308)
    check(published,"source sequence breaks equal-clock touch tie",tape([same_clock_later_sequence,hit]),
          "SIMULATED_FILLED","ASK",(hit,110310,"1.10310"))
    earlier_observed = quote(Q+1,Q+2,6,110303,110307)
    check(published,"observation breaks equal-delivery touch tie",tape([hit,earlier_observed]),
          "SIMULATED_FILLED","ASK",(earlier_observed,110307,"1.10307"))

    touching_conflict = quote(Q+1,Q+1,4,110306,110310)
    later_touch = quote(Q+3,Q+3,6,110305,110308)
    later_miss = quote(Q+3,Q+3,6,110309,110312)
    for order,arrange in (("forward",list),("reverse",lambda rows:list(reversed(rows)))):
        check(published,f"touch/miss conflict first, {order}",tape(arrange([miss,touching_conflict,hit])),
              "AMBIGUOUS_QUOTE_ORDER","ASK")
        check(published,f"touch/miss conflict after definite fill, {order}",
              tape(arrange([hit,later_touch,later_miss])),"SIMULATED_FILLED","ASK",(hit,110310,"1.10310"))
    assert source_records(published[0])==before


def test_off_grid_limit_is_unsupported(publish):
    published = publish(kind="LIMIT",direction="bullish",entry_price=1.103101)
    check(published,"off-grid limit",tape([quote(Q+1,Q+1,4,110305,110308)]),
          "UNSUPPORTED_LIMIT_PRICE_GRID","ASK")


def test_market_v2_matches_merged_payload(publish,monkeypatch):
    ids = count(1)
    monkeypatch.setattr("research_backend.app.strategy_routes.uuid4",lambda:UUID(int=next(ids)))
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    identifier,intent = publish()
    before = source_records(identifier)
    data = baseline["tape"]
    assert fingerprint(data)==baseline["tape_sha256"]
    merged = baseline["cases"]["MARKET"]
    assert intent["id"]==merged["intent_id"]
    outcome, = validate(identifier,data,intent["id"])["results"]
    assert outcome["id"]==merged["result_id"] and outcome["fill"]["id"]==merged["fill_id"]
    assert canonical(outcome).encode()==merged["serialized_payload"].encode()
    with transaction() as db:
        stored, = db.execute("SELECT CAST(payload AS BLOB) FROM offline_market_entry_results WHERE id=?",
                             (outcome["id"],)).fetchone()
        assert stored==merged["serialized_payload"].encode()
    assert source_records(identifier)==before
    print(json.dumps({"case":"MARKET V2 compatibility","tape_sha256":fingerprint(data),
                      "result_id":outcome["id"],"fill_id":outcome["fill"]["id"],
                      "payload_bytes":len(stored),"byte_identical":True},sort_keys=True))

    old_limit = baseline["cases"]["LIMIT"]
    limit_run,limit_intent = publish(kind="LIMIT",direction="bullish",entry_price=LIMIT)
    assert limit_intent["id"]==old_limit["intent_id"]
    assert old_limit["status"]=="UNSUPPORTED_ENTRY_TYPE" and old_limit["fill_id"] is None
    second_quote = data["quotes"][1]
    corrected = check((limit_run,limit_intent),"merged LIMIT baseline replay",data,
                      "SIMULATED_FILLED","ASK",(second_quote,110310,"1.10310"))
    assert corrected["id"]!=old_limit["result_id"]
