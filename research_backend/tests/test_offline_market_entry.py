"""Exercise execution against ordinary published historical ledgers."""
import json
from sqlite3 import IntegrityError

import pytest

from research_backend.app import historical_strategy_engine as engine
from research_backend.app import historical_strategy_store as store
from research_backend.app import offline_market_entry
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


def test_first_eligible_quote_is_decisive(publish,monkeypatch):
    from itertools import count
    from uuid import UUID

    ids = count(1)
    monkeypatch.setattr("research_backend.app.strategy_routes.uuid4",lambda:UUID(int=next(ids)))
    identifier,intent = publish()
    assert (intent["direction"],intent["qualified_at"],intent["status"])==(
        "SHORT",Q//1000,"AWAITING_EXECUTION_VALIDATION")
    before = source_records(identifier)
    first = quote(Q+1,Q+2,4)
    first_conflict = quote(Q+1,Q+2,4,110301,110315)
    later = [quote(Q+3,Q+3,9),quote(Q+3,Q+3,9,111001,111002)]
    later_observation = [quote(Q+2,Q+2,4),quote(Q+2,Q+2,4,111001,111002)]
    later_sequence = [quote(Q+1,Q+2,5),quote(Q+1,Q+2,5,111001,111002)]
    prequalification = [quote(Q,Q+1,4),quote(Q,Q+1,4,111001,111002)]
    after_expiry = [quote(EXPIRY,EXPIRY+1,4),quote(EXPIRY,EXPIRY+1,4,111001,111002)]
    complete = [(Q+1,EXPIRY)]
    only_through_fill = [(Q+1,Q+2)]
    gap = [(Q+2,EXPIRY)]
    expected_fill = "SIMULATED_FILLED"
    scenarios = [
        ("clean winning prefix",[first],only_through_fill,expected_fill,1),
        ("later delivery conflict",[first,*later],only_through_fill,expected_fill,1),
        ("same delivery later observation conflict",[first,*later_observation],complete,expected_fill,1),
        ("same clocks later sequence conflict",[first,*later_sequence],complete,expected_fill,1),
        ("conflict at winning priority",[first,first_conflict],complete,"AMBIGUOUS_QUOTE_ORDER",0),
        ("identical duplicate at winning priority",[first,first.copy()],complete,expected_fill,1),
        ("late prequalification conflict",[first,*prequalification],complete,expected_fill,1),
        ("only ineligible conflicts",[*prequalification,*after_expiry],complete,"VERIFIED_EXPIRED",0),
        ("ineligible conflicts with incomplete expiry coverage",after_expiry,only_through_fill,
         "INSUFFICIENT_QUOTE_COVERAGE",0),
        ("gap before ambiguous winning priority",[first,first_conflict],gap,"INSUFFICIENT_QUOTE_COVERAGE",0),
    ]
    discrepancies,records = [],{}
    winning_fills = {}
    for name,quotes,coverage,status,fills in scenarios:
        for order,rows in (("forward",quotes),("reverse",list(reversed(quotes)))):
            data = tape(rows,coverage)
            digest = fingerprint(data)
            response = validate(identifier,data,intent["id"])
            result, = response["results"]
            assert result["source"]==intent
            assert result["identity"]["execution_data_fingerprint"]==digest
            assert response==validate(identifier,data,intent["id"])
            fill = result["fill"]
            if fill is not None:
                assert (fill["quote"],fill["time_ms"],fill["side"],fill["price"])==(
                    first | {"scale":100000},Q+2,"BID","1.10301")
                winning_fills[digest] = fill["id"]
            records[digest] = result["id"]
            actual = result["status"],response["simulated_fills"]
            expected = status,fills
            print(json.dumps({"case":name,"input_order":order,"tape_sha256":digest,"intent":intent["id"],
                              "policy":result["identity"]["policy"]["version"],"result_id":result["id"],
                              "expected_status":status,"expected_fills":fills,"status":actual[0],"fills":actual[1],
                              "fill":fill},sort_keys=True))
            if actual!=expected:
                discrepancies.append((name,order,expected,actual))
    assert source_records(identifier)==before
    assert len(set(records.values()))==len(records)
    with transaction() as db:
        assert db.execute("SELECT count(*) FROM offline_market_entry_results").fetchone()[0]==len(records)
    assert not discrepancies,discrepancies
    assert len(set(winning_fills.values()))==len(winning_fills)>1


def source_records(identifier):
    tables = ("historical_strategy_runs","historical_strategy_attempts",
              "historical_strategy_decisions","historical_strategy_intents")
    with transaction() as db:
        return tuple(tuple(tuple(row) for row in db.execute(
            f"SELECT * FROM {table} WHERE {'id' if table=='historical_strategy_runs' else 'run_id'}=? ORDER BY id",
            (identifier,))) for table in tables)


def test_corrected_policy_preserves_existing_immutable_result(publish,monkeypatch):
    identifier,intent = publish()
    data = tape([quote(Q+1,Q+1,4)])
    source_before = source_records(identifier)
    assert offline_market_entry.POLICY["version"]==2
    with monkeypatch.context() as previous:
        # The clean singleton produces exactly the same payload in the V1
        # selector. Seed that payload under its original policy identity.
        previous.setitem(offline_market_entry.POLICY,"version",1)
        original, = validate(identifier,data,intent["id"])["results"]
    with transaction() as db:
        original_bytes = db.execute("SELECT CAST(payload AS BLOB) FROM offline_market_entry_results WHERE id=?",
                                    (original["id"],)).fetchone()[0]

    corrected, = validate(identifier,data,intent["id"])["results"]
    repeated, = validate(identifier,data,intent["id"])["results"]
    assert corrected==repeated
    assert original["id"]!=corrected["id"]
    assert original["fill"]["id"]!=corrected["fill"]["id"]
    assert original["identity"] | {"policy":corrected["identity"]["policy"]}==corrected["identity"]
    assert original["fill"] | {"id":corrected["fill"]["id"]}==corrected["fill"]
    with transaction() as db:
        rows = dict(db.execute("SELECT id,CAST(payload AS BLOB) FROM offline_market_entry_results"))
        assert set(rows)=={original["id"],corrected["id"]}
        assert rows[original["id"]]==original_bytes
    assert source_records(identifier)==source_before
    print(json.dumps({"case":"immutable policy coexistence","tape_sha256":fingerprint(data),
                      "v1_result":original["id"],"v1_fill":original["fill"]["id"],
                      "v2_result":corrected["id"],"v2_fill":corrected["fill"]["id"],
                      "unchanged_original_bytes":True,"unchanged_source_bytes":True},sort_keys=True))
