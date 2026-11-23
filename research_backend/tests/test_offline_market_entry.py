"""Exercise execution against ordinary published historical ledgers."""
import json
from sqlite3 import IntegrityError

import pytest

from research_backend.app import historical_strategy_engine as engine
from research_backend.app import historical_strategy_store as store
from research_backend.app.db import transaction
from research_backend.app.historical_strategy_inputs import HistoricalInput, prepare_historical, verified_evidence
from research_backend.app.offline_market_entry import validate_market_entries
from research_backend.app.strategy_evidence import fingerprint
from test_bounded_crt_smt_successors import canonical_successors, successor_request
from test_historical_strategy_runs import API
from test_strategy_evaluator import BASE

Q = (BASE+14120)*1000
EXPIRY = Q+300000


def quote(time, delivery, order, bid=110301, ask=110314):
    return dict(time_ms=time,available_at_ms=delivery,source_order=order,bid_units=bid,ask_units=ask)


def tape(quotes, intervals=((Q+1,EXPIRY),)):
    return dict(kind="SYNTHETIC_EXECUTABLE_QUOTES_V1",source_id="TF-I10A2-synthetic-entry-quotes",
                symbol="EURUSD",scale=100000,quotes=quotes,
                complete_delivery_intervals=[dict(start_ms=start,end_ms=end) for start,end in intervals])


@pytest.fixture
def publish(client,canonical_successors):
    def run(*, direction="bearish",kind="MARKET", incomplete=False, bad_proof=False):
        request,model,_ = canonical_successors(direction=direction,kind=kind)
        if direction=="bearish" and kind=="MARKET":
            request = successor_request(request)
        else:
            request.update(evaluation_start=BASE+11100)
        response = client.post(API,json=request)
        assert response.status_code==202,response.text
        identifier = response.json()["run_id"]
        prepared = prepare_historical(HistoricalInput.model_validate(request))
        assert identifier==prepared["run_id"]
        if incomplete:
            return identifier,None
        owner = "entry-fixture"
        assert store.claim(owner)==identifier

        def commit(snapshot,frontier,entries,completed,metrics):
            if bad_proof:
                for entry in entries:
                    if entry["intent"] is not None:
                        entry["intent"]["evidence_references"][0] = {
                            **entry["intent"]["evidence_references"][0],"event_id":"unrelated-proof"}
            store.publish(identifier,owner,snapshot,frontier,entries,completed,metrics)

        engine.run_chunks(prepared,verified_evidence(model,prepared),model,commit,chunk_size=7)
        response = client.get(f"{API}/{identifier}/intents")
        assert response.status_code==200,response.text
        rows = response.json()
        assert len(rows)==1,client.get(f"{API}/{identifier}/decisions").json()
        return identifier,rows[0]
    return run


def validate(identifier, data, intent_id=None):
    return validate_market_entries(identifier,tape=data,tape_sha256=fingerprint(data),intent_id=intent_id)


@pytest.mark.parametrize("direction,position,side,units,price",[
    ("bearish","SHORT","BID",110301,"1.10301"),
    ("bullish","LONG","ASK",110314,"1.10314"),
])
def test_published_market_intent_exact_fill_and_replay(publish,client,direction,position,side,units,price):
    identifier,intent = publish(direction=direction)
    assert (intent["direction"],intent["qualified_at"],intent["status"])==(
        position,Q//1000,"AWAITING_EXECUTION_VALIDATION")
    data = tape([
        quote(Q-1,Q-1,1,110001,110044),
        quote(Q,Q,2,110102,110155),
        quote(Q-2,Q+1,3,110203,110236),
        quote(Q+1,Q+1,4),
        quote(Q+2,Q+2,5,110405,110468),
    ])
    first = validate(identifier,data,intent["id"])
    assert (first["qualified_intents"],first["simulated_orders"],first["simulated_fills"])==(1,1,1)
    result, = first["results"]
    assert result["status"]=="SIMULATED_FILLED" and result["source"]==intent
    assert result["identity"]["execution_data_fingerprint"]==fingerprint(data)
    assert result["order"]["expires_at_ms"]==EXPIRY
    fill = result["fill"]
    assert (fill["time_ms"],fill["side"],fill["price_units"],fill["price"])==(Q+1,side,units,price)
    assert fill["quote"]==data["quotes"][3] | {"scale":100000}
    assert validate(identifier,data,intent["id"])==first
    assert client.get(f"{API}/{identifier}/intents").json()==[intent]
    with transaction() as db:
        assert db.execute("SELECT count(*) FROM offline_market_entry_results").fetchone()[0]==1
        with pytest.raises(IntegrityError):
            db.execute("DELETE FROM offline_market_entry_results WHERE id=?",(result["id"],))
    print(json.dumps({"direction":position,"intent":intent["id"],"qualified_at_ms":Q,
                      "quote":fill["quote"],"side":side,"price":price,"expiry":EXPIRY,
                      "result":result["id"],"fill":fill["id"]},sort_keys=True))


def test_quote_delivery_ties_expiry_and_coverage(publish):
    identifier,_ = publish()
    scenarios = [
        ("ordered tie",[quote(Q+1,Q+2,9,111001,111002),quote(Q+1,Q+2,8)],
         [(Q+1,EXPIRY)],"SIMULATED_FILLED",Q+2),
        ("ambiguous tie",[quote(Q+1,Q+1,8),quote(Q+1,Q+1,8,111001,111002)],
         [(Q+1,EXPIRY)],"AMBIGUOUS_QUOTE_ORDER",None),
        ("exact expiry",[quote(EXPIRY,EXPIRY,1)],
         [(Q+1,EXPIRY)],"SIMULATED_FILLED",EXPIRY),
        ("delivered after expiry",[quote(EXPIRY,EXPIRY+1,1)],
         [(Q+1,EXPIRY)],"VERIFIED_EXPIRED",None),
        ("sealed empty tape",[],[(Q+1,EXPIRY)],"VERIFIED_EXPIRED",None),
        ("unsealed expiry",[],[(Q+1,EXPIRY-1)],"INSUFFICIENT_QUOTE_COVERAGE",None),
        ("gap before quote",[quote(Q+3,Q+3,1)],[(Q+1,Q+1),(Q+3,EXPIRY)],
         "INSUFFICIENT_QUOTE_COVERAGE",None),
        ("delivered late",[quote(Q+1,Q+5,1,110101,110118),quote(Q+2,Q+3,2)],
         [(Q+1,EXPIRY)],"SIMULATED_FILLED",Q+3),
    ]
    for name,quotes,intervals,status,when in scenarios:
        observed, = validate(identifier,tape(quotes,intervals))["results"]
        assert observed["status"]==status,name
        if when is None:
            assert observed["fill"] is None,name
        else:
            assert (observed["fill"]["time_ms"],observed["fill"]["price_units"])==(when,110301),name
        print(json.dumps({"case":name,"status":status,"result":observed["id"],"fill":observed["fill"]},sort_keys=True))
    assert validate_market_entries(identifier)["results"][0]["status"]=="INSUFFICIENT_QUOTE_COVERAGE"


def test_reference_and_fingerprint_rejection(publish):
    incomplete,_ = publish(incomplete=True)
    with pytest.raises(ValueError,match="completed published"):
        validate_market_entries(incomplete)
    assert store.cancel(incomplete)
    identifier,_ = publish(bad_proof=True)
    with pytest.raises(ValueError,match="source decision and pinned policies"):
        validate_market_entries(identifier)
    with pytest.raises(ValueError,match="fingerprint mismatch"):
        validate_market_entries(identifier,tape=tape([]),tape_sha256="0"*64)
    with transaction() as db:
        assert db.execute("SELECT count(*) FROM offline_market_entry_results").fetchone()[0]==0


def test_unsupported_entry_and_cross_run_identity(publish):
    identifier,intent = publish(kind="LIMIT")
    assert intent["entry"]["kind"]=="LIMIT"
    response = validate(identifier,tape([quote(Q+1,Q+1,1)]))
    assert response["results"][0]["status"]=="UNSUPPORTED_ENTRY_TYPE"
    assert response["simulated_fills"]==response["simulated_orders"]==0
    with pytest.raises(ValueError,match="does not belong"):
        validate_market_entries(identifier,intent_id="intent-from-another-run")
