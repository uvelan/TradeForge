"""Real canonical producer integration with isolated, explicitly approved fixture data."""
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
import sqlite3

import pytest

from research_backend.app import strategy_evaluation_routes as routes
from research_backend.app import strategy_evaluation_sources as sources
from research_backend.app.archive_provenance import SYNTHETIC_PARTITIONS, require_market_partition
from research_backend.app.db import transaction
from research_backend.app.storage import write_partition
from research_backend.app.strategy_evidence import fingerprint
from test_smt_causal import LEFT, RIGHT
from test_strategy_definitions import ROUTE, group, ready_definition_v2

API = "/api/strategy-evaluations/v1"
BASE = int(datetime(2020,1,2,10,tzinfo=UTC).timestamp())
A = BASE+3600


def frozen_candles(fixture, symbol, frame, start, bars, mirrored=False):
    if mirrored:
        bars = [(250-o,250-l,250-h,250-c) for o,h,l,c in bars]
    step = 60 if frame=="M1" else 300
    candles = [dict(time=start+i*step,open=o,high=h,low=l,close=c,volume=0,closed=True)
               for i,(o,h,l,c) in enumerate(bars)]
    part = write_partition("DUKASCOPY","isolated-evaluator-fixture",symbol,"candles",frame,"BID",candles)
    fixture.append(dict(source="DUKASCOPY_SAVED",feed_id="isolated-evaluator-fixture",symbol=symbol,
                        timeframe=frame,price_basis="BID",calendar={"kind":"continuous"},
                        partitions=[{"id":part["id"],"sha256":part["sha256"]}]))


def seed_fixture(mirrored=False):
    records = []
    first_hour = [(104,105,103.5,104.5)]*12
    first_hour[3] = (104.5,106,103.5,104.5)
    second_hour = [
        (104,105,102.75,104.5),(104.75,105,104,104.8),(104,104.5,103.5,104),
        (103.5,104,102,103.5),(104,104.5,103.5,104),(104,104.5,103.5,104),
    ]
    frozen_candles(records,"EURUSD","M5",BASE,[(103,105,100,103)]*12+first_hour+second_hour,mirrored)
    frozen_candles(records,"GBPUSD","M5",BASE,[(113,115,111,113)]*30,mirrored)
    for symbol,sequence,normal in (("EURUSD",LEFT,11),("GBPUSD",RIGHT,24)):
        values = [normal]*80+sequence+[normal]*(150-80-len(sequence))
        frozen_candles(records,symbol,"M1",BASE,[(v,v+1,v-1,v) for v in values],mirrored)
    selection = {"id":"ISOLATED_CANONICAL_FIXTURE","series":records,"start":BASE,"end":BASE+9000}
    selection["fingerprint"] = fingerprint(selection)
    return selection


@pytest.fixture
def approved_fixture(client,monkeypatch):
    selection = seed_fixture()
    monkeypatch.setattr(sources,"selection_catalog",lambda:[selection])
    monkeypatch.setattr(routes,"selection_catalog",lambda:[selection])
    return selection


def ready_entry(kind="MARKET",pair=True,direction="bearish"):
    data = ready_definition_v2(direction)
    data["instruments"]["calendar"] = {"kind":"continuous"}
    data["expiry"] = {"unit":"SECONDS","count":9000}
    if pair:
        data["timeframes"]["trigger"]="M1"
        data["indicators"][1]["timeframe"]="M1"
        data["indicators"][1]["settings"].update(period=2,alignment_bars=0)
    else:
        data["instruments"]["comparison"]=None
        data["indicators"].pop()
        data["risk"]["instrument_limits"].pop()
        data["setup"]["children"].pop()
        data["setup"]["operator"]="ALL"
        data["temporal"]=[]
    reclaim = data["setup"]["children"][0]
    bound = {"dependency":"crt","observation":"CRT_STATE","selection":"BOUND_FAMILY",
             "same_family_as":"crt_reference","alias":None}
    data["trigger"] = group("trigger",{**reclaim,**bound,"id":"expand","expected":"EXPANSION_OBSERVED"})
    data["invalidation"] = group("invalidation",{**reclaim,**bound,"id":"completed","expected":"OPPOSITE_BOUNDARY_REACHED"})
    data["trade_intent"]["entry"] = {"kind":kind}
    if kind!="MARKET":
        data["trade_intent"]["entry"]["price"]={"kind":"FIXED_ABSOLUTE","value":103.25,"basis":"BID"}
    return data


def request_body(client, selection, data=None, cutoff=A+4200):
    definition = ready_entry() if data is None else data
    saved = client.post(ROUTE,json=definition)
    assert saved.status_code==201,saved.text
    version = saved.json()
    return {"evaluator_version":6,"purpose":"HISTORICAL_SIMULATION","strategy_id":version["strategy_id"],
            "strategy_version_id":version["version_id"],"configuration_fingerprint":version["configuration_fingerprint"],
            "selection_id":selection["id"],"selection_fingerprint":selection["fingerprint"],
            "warmup_start":BASE,"evaluation_start":A+300,"cutoff":cutoff}


def create(client,body):
    reply = client.post(API,json=body)
    assert reply.status_code==201,reply.text
    return reply.json()


def advance(client,run,cutoff):
    return client.post(f"{API}/{run['run_id']}/advance",json={
        "cutoff":cutoff,"expected_decision_id":run["decision"]["decision_id"]})


def test_two_range_smt_sequence_and_reconstruct(client,approved_fixture):
    request = request_body(client,approved_fixture)
    first = create(client,request)
    initial = first["decision"]
    assert initial["state"]=="WAITING_FOR_TRIGGER",initial["phase_results"]
    assert initial["context_activated_at"]==A+300
    assert initial["setup_activated_at"]==A+1800
    assert initial["entry_intent"] is None
    assert initial["bound_crt"]["evidence"]["reference"]["available_at"]==A
    assert initial["resource_counts"]["unique_producers"]==2
    assert initial["resource_counts"]["archive"]["partition_decodes"]==3
    advanced = advance(client,first,A+4800)
    assert advanced.status_code==200,advanced.text
    qualified = advanced.json()
    assert qualified["state"]=="TRIGGER_QUALIFIED"
    assert qualified["attempt_id"]==initial["attempt_id"]
    assert qualified["trigger_activated_at"]==A+4800
    intent = qualified["entry_intent"]
    assert (intent["symbol"],intent["direction"],intent["status"])==("EURUSD","SHORT","AWAITING_EXECUTION_VALIDATION")
    assert "price" not in intent["entry"]
    selected = next(r for r in qualified["rules"] if r["id"]=="expand")
    assert selected["family"]==qualified["bound_crt"]["evidence"]["family"]
    url = f"{API}/{first['run_id']}"
    exact = selected["evidence_reference"]
    result = client.get(f"{url}/decisions/{qualified['cutoff']}/evidence/{exact['calculation_snapshot']}/{exact['event_id']}")
    assert result.status_code==200 and result.json()["state"]=="EXPANSION_OBSERVED"
    chart=client.get(f"{url}/decisions/{qualified['cutoff']}/evidence/{exact['calculation_snapshot']}/{exact['event_id']}/chart")
    assert chart.status_code==200,chart.text
    assert chart.json()["feed_id"]=="isolated-evaluator-fixture"
    assert all(row["available_at"]<=qualified["cutoff"] for row in chart.json()["candles"])
    assert chart.json()["evidence_time"] in {row["time"] for row in chart.json()["candles"]}

    assert client.get(f"{url}/attempts/{qualified['attempt_id']}").json()==qualified
    for decision in (initial,qualified):
        rebuilt=client.post(url+"/reconstruct",json={"cutoff":decision["cutoff"]}).json()
        assert rebuilt["decision_id"]==decision["decision_id"]
        assert rebuilt["persisted"] is False
    assert advance(client,first,A+4800).json()==qualified
    assert len(client.get(url+"/decisions").json())==2
    with transaction() as db, pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE strategy_decisions SET created_at='rewritten' WHERE run_id=?",(first["run_id"],))


@pytest.mark.parametrize("kind",["LIMIT","STOP"])
def test_price_intent_remains_unexecuted(client,approved_fixture,kind):
    first = create(client,request_body(client,approved_fixture,ready_entry(kind,pair=False),A+4800))
    decision = first["decision"]
    assert decision["state"]=="TRIGGER_QUALIFIED"
    assert decision["entry_intent"]["entry"]=={"kind":kind,"price":{"kind":"FIXED_ABSOLUTE","value":103.25,"basis":"BID"}}
    assert decision["entry_intent"]["strictly_after"]==A+4800
    assert decision["entry_intent"]["status"]=="AWAITING_EXECUTION_VALIDATION"


def test_scope_rejections_and_quarantine(client,approved_fixture):
    body = request_body(client,approved_fixture)
    for mutation in ({"purpose":"STRICT_RECORDED"},{"selection_id":"arbitrary"},
                     {"selection_fingerprint":"f"*64},{"configuration_fingerprint":"f"*64},
                     {"cutoff":approved_fixture["end"]+1},{"warmup_start":body["evaluation_start"]},
                     {"definition":ready_entry()}):
        assert client.post(API,json={**body,**mutation}).status_code==422,mutation
    for dataset in SYNTHETIC_PARTITIONS:
        with pytest.raises(ValueError,match="QUARANTINED_SYNTHETIC_CAPTURE"):
            require_market_partition(dataset)
    assert client.get(API+"/selections").json()["recorded_readiness"]=="BLOCKED"


def test_concurrent_advances_and_revision_snapshot(client,approved_fixture):
    body = request_body(client,approved_fixture,ready_entry(pair=False))
    first = create(client,body)
    revised=client.post(ROUTE+"/"+body["strategy_id"]+"/revisions",
                        json={"expected_version":1,"definition":{**ready_entry("STOP",False),"name":"Changed"}})
    assert revised.status_code==201,revised.text
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses=list(pool.map(lambda cutoff:advance(client,first,cutoff),(A+4800,A+4860)))
    assert sorted(reply.status_code for reply in responses)==[200,409]
    accepted=next(reply.json() for reply in responses if reply.status_code==200)
    assert accepted["entry_intent"]["entry"]["kind"]=="MARKET"


def test_invalidation_precedes_trigger_and_expiry_boundary(client,approved_fixture):
    case = ready_entry(pair=False)
    case["invalidation"]["children"][0]["expected"]="EXPANSION_OBSERVED"
    result=create(client,request_body(client,approved_fixture,case,A+4800))["decision"]
    assert result["state"]=="INVALIDATED" and result["entry_intent"] is None
    for count,state in ((4500,"TRIGGER_QUALIFIED"),(4499,"EXPIRED")):
        case=ready_entry(pair=False)
        case["expiry"]["count"]=count
        result=create(client,request_body(client,approved_fixture,case,A+4800))["decision"]
        assert result["state"]==state


def test_test_session_uses_disposable_storage(pytestconfig):
    from research_backend.app.config import DATABASE
    from pathlib import Path

    assert DATABASE.is_relative_to(Path(pytestconfig.tradeforge_test_archive.name).resolve())

def test_candidate_overflow_fails_closed(client,approved_fixture):
    data=ready_entry(pair=False)
    data["attempt"]["candidate_limit"]=1
    data["context"]["children"][0]["evidence"]["max_age_seconds"]=7200
    body=request_body(client,approved_fixture,data,cutoff=A+4200)
    decision=create(client,{**body,"evaluation_start":A+3900})["decision"]
    assert decision["state"]=="INSUFFICIENT_EVIDENCE"
    assert any(rule["reason"]=="CANDIDATE_LIMIT_EXCEEDED" for rule in decision["rules"])
    assert not decision["entry_intent"]


def test_unknown_smt_invalidation_blocks_matching_expansion(client,approved_fixture):
    definition=ready_entry()
    invalid=definition["invalidation"]["children"][0]
    invalid.update(dependency="smt",observation="SMT_CONFIRMED",expected="bullish",
                   same_family_as=None,selection="EARLIEST_ELIGIBLE")
    decision=create(client,request_body(client,approved_fixture,definition,A+4800))["decision"]
    assert decision["state"]=="INSUFFICIENT_EVIDENCE"
    assert decision["invalidation"]=="UNKNOWN"
    assert decision["entry_intent"] is None


def test_source_corruption_rejected_before_analytical_publications(client,approved_fixture):
    from research_backend.app import storage
    from research_backend.app.db import one
    data=ready_entry(pair=False)
    body=request_body(client,approved_fixture,data)
    identifier=approved_fixture["series"][0]["partitions"][0]["id"]
    part=one("SELECT * FROM datasets WHERE id=?",(identifier,))
    with (storage.MARKET_DIR/part["relative_path"]).open("ab") as stream:
        stream.write(b"isolated test corruption")
    denied=client.post(API,json=body)
    assert denied.status_code==422
    assert "checksum" in denied.text.lower()


def test_draft_archived_and_historical_version_scope(client,approved_fixture):
    data=ready_entry(pair=False)
    draft=client.post(ROUTE,json={**data,"status":"DRAFT"}).json()
    request=request_body(client,approved_fixture,data)
    changed={**request,"strategy_id":draft["strategy_id"],"strategy_version_id":draft["version_id"],
             "configuration_fingerprint":draft["configuration_fingerprint"]}
    assert client.post(API,json=changed).status_code==422
    first=create(client,request)
    archived=client.post(ROUTE+"/"+request["strategy_id"]+"/archive",json={"expected_version":1})
    assert archived.status_code==201
    assert client.post(API,json=request).status_code==422
    assert client.get(API+"/"+first["run_id"]).json()["decision"]==first["decision"]


def test_independent_producer_configurations_and_shared_scope_reuse(client,approved_fixture):
    data=ready_entry(pair=False)
    original=data["indicators"][0]
    data["indicators"].extend([
        {**original,"id":"crt-copy"},
        {**original,"id":"crt-different","settings":{**original["settings"],"min_penetration":0.1}},
        {"id":"structure","indicator":"STRUCTURE","algorithm_version":2,"role":"PRIMARY","timeframe":"M5","settings":{}},
        {"id":"fvg","indicator":"FVG","algorithm_version":1,"role":"PRIMARY","timeframe":"M5","settings":{}},
        {"id":"liquidity","indicator":"LIQUIDITY","algorithm_version":1,"role":"PRIMARY","timeframe":"M5",
         "settings":{},"producers":["structure"]},
        {"id":"ob","indicator":"ORDER_BLOCK","algorithm_version":1,"role":"PRIMARY","timeframe":"M5",
         "settings":{},"producers":["structure"]},
    ])
    decision=create(client,request_body(client,approved_fixture,data,A+4800))["decision"]
    pins={row["dependency"]:row for row in decision["producers"]}
    assert pins["crt"]["snapshot"]==pins["crt-copy"]["snapshot"]
    assert pins["crt"]["snapshot"]!=pins["crt-different"]["snapshot"]
    assert decision["resource_counts"]["unique_producers"]==6
    assert decision["resource_counts"]["archive"]["partition_decodes"]==1

def replace_fixture_lane(selection,start,missing):
    from research_backend.app import storage
    from research_backend.app.db import one
    lane = selection["series"][0]
    existing = one("SELECT * FROM datasets WHERE id=?",(lane["partitions"][0]["id"],))
    rows = [row for row in storage.read_partition(existing) if row["time"]!=missing]
    assert rows[0]["time"]==start or missing==start
    part = write_partition("DUKASCOPY",lane["feed_id"],lane["symbol"],"candles","M5","BID",rows)
    lane["partitions"] = [{"id":part["id"],"sha256":part["sha256"]}]
    selection["fingerprint"] = fingerprint({key:value for key,value in selection.items() if key!="fingerprint"})


@pytest.mark.parametrize("missing,expected",[(BASE+300,422),(A+1800,201)])
def test_complete_coverage_vs_missing_direct_archive_slot(client,approved_fixture,missing,expected):
    replace_fixture_lane(approved_fixture,BASE,missing)
    body=request_body(client,approved_fixture,ready_entry(pair=False),A+4800)
    reply=client.post(API,json=body)
    assert reply.status_code==expected,reply.text
    if expected==201:
        decision=reply.json()["decision"]
        assert decision["state"]=="INSUFFICIENT_EVIDENCE"
        assert decision["reason"]=="REQUIRED_SOURCE_COVERAGE_UNKNOWN"
        assert decision["coverage"][0]["missing"]==1 and decision["entry_intent"] is None


def test_public_delivery_cannot_change_internal_crt_snapshot(client,approved_fixture):
    from research_backend.app.indicator_engine import IndicatorSpec, evaluate
    from research_backend.app.indicator_history import load_reference
    from research_backend.app.smt_causal import FrozenFeed
    definition=ready_entry(pair=False)
    stored=client.post(ROUTE,json=definition).json()
    model=sources.EvaluationInput.model_validate(
        request_body(client,approved_fixture,definition,A+4800))
    from research_backend.app.strategy_definitions import StrategyDefinitionV2
    typed=StrategyDefinitionV2.model_validate(stored["definition"])
    selection=sources.freeze_selection(model,typed)
    feed=FrozenFeed.model_validate(selection["series"]["PRIMARY:M5"])
    identities=[]
    for delivery in (None,{"formation_limit":1,"revision_limit":1,"sweep_limit":1},
                     {"formation_limit":0,"revision_limit":0,"sweep_limit":0,"current_limit":0}):
        spec=IndicatorSpec(id="candle_range_theory",crt={**typed.indicators[0].settings,
                                                       "market_schedule":{"kind":"continuous"}},
                           crt_delivery=delivery)
        request=sources.indicator_request(feed,model.cutoff,spec)
        snapshot=sources.StrategyCoordinator(request)
        evaluate(request,load_reference,coordinator=snapshot)
        view=snapshot.last_views["crt"]
        identities.append((view.snapshot,[row["id"] for row in snapshot.events[view.snapshot]]))
    assert len(identities[0][1])>=5 and identities[0]==identities[1]==identities[2]


def test_later_cutoff_references_the_same_terminal_intent(client,approved_fixture):
    first=create(client,request_body(client,approved_fixture,ready_entry(pair=False),A+4800))
    late=advance(client,first,A+5100)
    assert late.status_code==200
    assert late.json()["entry_intent"]==first["decision"]["entry_intent"]
    assert late.json()["trigger_activated_at"]==A+4800

def test_expired_earliest_crt_cannot_block_usable_new_reference(client,approved_fixture):
    definition = ready_entry(pair=False)
    definition["indicators"][0]["settings"]["max_age"] = 6
    request = request_body(client,approved_fixture,definition,cutoff=A+3900)
    request["evaluation_start"] = A+3900
    decision = create(client,request)["decision"]
    assert decision["context_activated_at"]==A+3900
    assert decision["bound_crt"]["evidence"]["reference"]["available_at"]==A+3600

def test_canonical_bullish_pair_qualifies_long_with_own_crt_family(client,monkeypatch):
    selection = seed_fixture(mirrored=True)
    monkeypatch.setattr(sources,"selection_catalog",lambda:[selection])
    initial = create(client,request_body(client,selection,ready_entry(direction="bullish")))
    assert initial["decision"]["state"]=="WAITING_FOR_TRIGGER"
    decision = advance(client,initial,A+4800).json()
    assert decision["state"]=="TRIGGER_QUALIFIED"
    assert decision["setup_activated_at"]==A+1800
    assert decision["trigger_activated_at"]==A+4800
    assert decision["entry_intent"]["direction"]=="LONG"
    evidence = decision["supporting_events"].values()
    assert any(row.get("state")=="SMT_CONFIRMED" and row["direction"]=="bullish" for row in evidence)
    assert any(row.get("state")=="EXPANSION_OBSERVED" and row["range_id"]==decision["bound_crt"]["evidence"]["family"]
               for row in evidence)
