"""Adversarial definition, registry boundary, and immutable API contracts."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from itertools import product
import json
import sqlite3

import pytest

from research_backend.app.causal_timing import TemporalEvidence, strategy_eligible
from research_backend.app.db import transaction
from research_backend.app.strategy_catalog import OBSERVATIONS
from research_backend.app.strategy_definitions import (
    ExactEvidenceReference, Outcome, causal_order, guarded_outcome, logical_outcome, validate_definition,
)
from research_backend.app.strategy_evidence import EvidenceRegistry
from research_backend.app.strategy_routes import template


ROUTE = "/api/strategies/v1"
SAT, NO, UNKNOWN = Outcome.SATISFIED, Outcome.NOT_SATISFIED, Outcome.UNKNOWN


def variant(path, value):
    result = template("bearish")
    owner = result
    pieces = path.split(".")
    for key in pieces[:-1]:
        owner = owner[int(key)] if isinstance(owner,list) else owner[key]
    key = int(pieces[-1]) if isinstance(owner,list) else pieces[-1]
    owner[key] = value
    return result


def group(name,*children,operator="ALL",**settings):
    return dict(id=name,type="GROUP",operator=operator,children=list(children),**settings)


def ready_definition():
    data = template("bullish")
    sample = data["context"]["children"][0]
    data["trigger"] = group("trigger",{**sample,"id":"trigger_event"})
    data["invalidation"] = group("invalidation",{**sample,"id":"invalid_event"})
    bound = {"unit":"SECONDS","count":300}
    data.update(status="READY_FOR_EVALUATION",expiry=bound,
                risk={"account_risk_pct":0.5,"max_simultaneous_exposure_pct":1,"max_session_loss_pct":2,
                      "min_reward_risk":2,"instrument_limits":[{"role":role,"max_risk_percent":1}
                                                               for role in ("PRIMARY","COMPARISON")]},
                execution={"entry_intent":"MARKET","price_basis":"BID","spread_bps":1,"slippage_bps":0,
                           "commission_bps":0,"expiry":bound,"intrabar":"REJECT_AMBIGUOUS"},
                management={"initial_stop":{"kind":"FIXED_PIPS","distance":10},
                            "target":{"kind":"REWARD_MULTIPLE","multiple":2}})
    return data


@pytest.mark.parametrize("direction,expected",[("bearish","HIGH_SWEPT_RECLAIMED"),("bullish","LOW_SWEPT_RECLAIMED")])
def test_mirrored_template_resolves_pinned_scope_and_preserves_pending_settings(client,direction,expected):
    catalog = client.get(ROUTE+"/capabilities").json()
    assert catalog["indicators"]["SMT"]["algorithm_version"]==2
    assert catalog["indicators"]["FVG"]["identity_policy_version"]==2
    assert catalog["observations"].keys()==OBSERVATIONS.keys()
    response = client.get(ROUTE+"/templates")
    loaded = next(item for item in response.json() if item["id"]==direction)
    value = loaded["definition"]
    assert value["setup"]["children"][0]["expected"]==expected
    assert value["setup"]["children"][1]["expected"]==direction
    assert value["indicators"][0]["settings"]["reference_frame"]=="H1"
    assert value["indicators"][1]["settings"]["relationship"]=="POSITIVE"
    assert {"trigger","invalidation","expiry","management.target"} <= set(loaded["unresolved"])
    check = client.post(ROUTE+"/validate",json=value).json()
    assert check["valid"] and not check["restrictions"]["evaluation_available"]
    assert not check["restrictions"]["recorded_operational"]


@pytest.mark.parametrize("path,value,code",[
    ("schema_version",2,"SCHEMA_LITERAL_ERROR"),
    ("schema_version",True,"UNSUPPORTED_SCHEMA_VERSION"),
    ("indicators.0.indicator","MACD","SCHEMA_LITERAL_ERROR"),
    ("indicators.0.algorithm_version",0,"UNSUPPORTED_ALGORITHM"),
    ("indicators.0.timeframe","M1","UNSUPPORTED_TIMEFRAME"),
    ("indicators.0.settings",{"reference_frame":"H4"},"UNSUPPORTED_INDICATOR_PARAMETER"),
    ("indicators.1.settings",{"relationship":"INVERSE"},"INVALID_INDICATOR_PARAMETER"),
    ("indicators.1.settings",{"confirmation_bars":7,"expiry_bars":6},"INVALID_INDICATOR_PARAMETER"),
    ("indicators.1.settings",{"period":"3"},"INVALID_INDICATOR_PARAMETER"),
    ("instruments.primary.source","DEMO","SCHEMA_LITERAL_ERROR"),
    ("instruments.primary.price_basis","LAST_TRADE","UNSUPPORTED_BASIS"),
    ("instruments.comparison.price_basis","ASK","INCOMPATIBLE_PAIR"),
    ("instruments.comparison.symbol","EURUSD","INCOMPATIBLE_PAIR"),
    ("instruments.calendar",{"kind":"continuous","extra":1},"SCHEMA_EXTRA_FORBIDDEN"),
    ("instruments.calendar",{"kind":"unknown"},"INVALID_SCOPE"),
    ("instruments.primary.modeled_delay_seconds",90000,"SCHEMA_LESS_THAN_EQUAL"),
    ("context.children.0.observation","SESSION_MEMBERSHIP","UNSUPPORTED_OBSERVATION"),
    ("context.children.0.operator","EVAL","SCHEMA_LITERAL_ERROR"),
    ("context.children.0.operator","LT","UNSUPPORTED_OPERATOR"),
    ("context.children.0.expected","__import__('os')","INVALID_EXPECTED_VALUE"),
    ("context.children.0.evidence.max_age_seconds",0,"SCHEMA_GREATER_THAN_EQUAL"),
    ("context.children.0.evidence.max_age_seconds",86401,"SCHEMA_LESS_THAN_EQUAL"),
    ("context.children.0.evidence.purpose","STRICT_RECORDED","PURPOSE_MISMATCH"),
    ("context.children",[],"SCHEMA_TOO_SHORT"),
    ("setup.children.0.evidence.state","CURRENT","UNSUPPORTED_EVIDENCE_STATE"),
    ("setup.children.0.dependency","missing","MISSING_DEPENDENCY"),
    ("availability_modes",["LEGACY_RESEARCH"],"SCHEMA_LITERAL_ERROR"),
    ("status","READY_FOR_EVALUATION","UNRESOLVED_REQUIRED"),
    ("name","  ","INVALID_SCOPE"),
    ("risk",{"account_risk_pct":2.1},"SCHEMA_LESS_THAN_EQUAL"),
    ("risk",{"account_risk_pct":-1},"SCHEMA_GREATER_THAN"),
    ("expiry",{"unit":"ELIGIBLE_BARS","count":501,"timeframe":"M5"},"SCHEMA_VALUE_ERROR"),
    ("expiry",{"unit":"ELIGIBLE_BARS","count":1,"timeframe":"M1"},"UNBOUND_EXPIRY_TIMEFRAME"),
    ("execution",{"price_basis":"ASK"},"INCOMPATIBLE_PRICE_BASIS"),
    ("execution",{"expr":"lambda price: true"},"SCHEMA_EXTRA_FORBIDDEN"),
])
def test_reject_bad_roles_algorithms_rules_and_policies(client,path,value,code):
    result = client.post(ROUTE+"/validate",json=variant(path,value)).json()
    assert not result["valid"]
    error = next(item for item in result["errors"] if item["code"]==code)
    assert {"path","condition_id","expected","actual","code"}==set(error)


def test_nonfinite_duplicate_key_and_stream_limits(client):
    for raw in (b'{"rate":NaN}',b'{"rate":Infinity}',b'{"rate":1e9999}',b'{"name":"one","name":"two"}'):
        result = client.post(ROUTE+"/validate",content=raw)
        assert result.status_code==422
        assert result.json()["detail"]["errors"][0]["code"] in {"INVALID_JSON","INVALID_NUMBER"}
    for raw in (br'{"name":"\ud800"}', br'{"\udc00":true}'):
        result = client.post(ROUTE+"/validate", content=raw)
        assert result.status_code == 422
        assert result.json()["detail"]["errors"][0]["code"] == "INVALID_JSON"
    unicode_definition = {**template("bullish"), "name": "Market study " + chr(0x1F4C8)}
    assert client.post(ROUTE+"/validate", json=unicode_definition).json()["valid"]
    data = b'{"x":"' + b"o"*65536+b'"}'
    assert client.post(ROUTE+"/validate",content=data).status_code==413
    nested = '{"x":'+"["*40+"0"+"]"*40+"}"
    assert client.post(ROUTE+"/validate",content=nested).json()["detail"]["errors"][0]["code"]=="PAYLOAD_COMPLEXITY"


def test_unsupported_atr_and_missing_exact_producers(client):
    data = template("bullish")
    data["indicators"].extend([
        {"id":"zone","indicator":"FVG","algorithm_version":1,"role":"PRIMARY","timeframe":"M5","settings":{"atr_multiple":1}},
        {"id":"blocks","indicator":"ORDER_BLOCK","algorithm_version":1,"role":"PRIMARY","timeframe":"M5","producers":["crt"]},
        {"id":"pools","indicator":"LIQUIDITY","algorithm_version":1,"role":"PRIMARY","timeframe":"M5"},
    ])
    result = client.post(ROUTE+"/validate",json=data).json()
    assert [(e["path"],e["code"]) for e in result["errors"]]==[
        ("indicators.2.settings","UNSUPPORTED_INDICATOR_PARAMETER"),
        ("indicators.3.producers","INVALID_PRODUCER_BINDING"),("indicators.4.producers","INVALID_PRODUCER_BINDING")]
    data["indicators"].append({"id":"structure","indicator":"STRUCTURE","algorithm_version":2,"role":"PRIMARY","timeframe":"M5"})
    data["indicators"][2]["settings"]={"atr_multiple":0}
    for item in data["indicators"][3:5]:
        item["producers"]=["structure"]
    assert client.post(ROUTE+"/validate",json=data).json()["valid"]


def test_bound_depth_count_temporal_cycles_and_not_requirements():
    data = template("bearish")
    root = data["context"]["children"][0]
    for depth in range(6):
        root = group("level"+str(depth),root)
    data["context"]=root
    assert any(e["code"]=="RULE_COMPLEXITY" for e in validate_definition(data)["errors"])
    data["context"]=group("top",*(group("lane"+str(section),
        *({**data["setup"]["children"][0],"id":f"row-{section}-{index}"} for index in range(22))) for section in range(5)))
    assert any(e["code"]=="RULE_COMPLEXITY" for e in validate_definition(data)["errors"])
    assert any(e["code"]=="CYCLIC_RULE_GRAPH" for e in validate_definition(
        variant("temporal",[{"first":"divergence","then":"reclaim","relation":"AFTER"}]))["errors"])
    assert any(e["code"]=="INVALID_TEMPORAL_LINK" for e in validate_definition(
        variant("temporal",[{"first":"reclaim","then":"divergence","relation":"WITHIN_AFTER"}]))["errors"])
    assert any(e["code"]=="INVALID_SEQUENCE" for e in validate_definition(
        variant("setup.children",[root]))["errors"])
    assert any(e["code"]=="INVALID_NEGATION" for e in validate_definition(
        variant("context.operator","NOT"))["errors"])


@pytest.mark.parametrize("a,b",list(product((SAT,NO,UNKNOWN),repeat=2)))
def test_all_any_truth_table(a,b):
    table = {
        (SAT,SAT):(SAT,SAT),(SAT,NO):(NO,SAT),(SAT,UNKNOWN):(UNKNOWN,SAT),
        (NO,SAT):(NO,SAT),(NO,NO):(NO,NO),(NO,UNKNOWN):(NO,UNKNOWN),
        (UNKNOWN,SAT):(UNKNOWN,SAT),(UNKNOWN,NO):(NO,UNKNOWN),(UNKNOWN,UNKNOWN):(UNKNOWN,UNKNOWN),
    }
    assert (logical_outcome("ALL",(a,b)),logical_outcome("ANY",(a,b)))==table[a,b]


def test_unknown_empty_negative_smt_boundary_and_temporal_order():
    assert logical_outcome("NOT",[UNKNOWN])==UNKNOWN
    assert logical_outcome("NOT",[NO])==SAT
    assert logical_outcome("ALL",[])==logical_outcome("ANY",[])==UNKNOWN
    assert guarded_outcome(UNKNOWN,logical_outcome("ANY",[SAT,UNKNOWN]))==UNKNOWN
    assert causal_order(10,10,20)==NO
    assert causal_order(10,20,19)==UNKNOWN
    assert causal_order(None,20,30)==UNKNOWN
    assert causal_order(10,20,20,elapsed_limit=10)==SAT
    assert causal_order(10,20,20,elapsed_limit=9)==NO
    assert causal_order(20,10,20)==NO


def test_exact_reference_required_and_existing_registry_rejects_unknown_or_wrong_purpose():
    with pytest.raises(ValueError):
        ExactEvidenceReference.model_validate({"version":1,"event_id":"raw-only"})
    registry = EvidenceRegistry(purpose="HISTORICAL_SIMULATION")
    source = dict(source="DUKASCOPY_SAVED",symbol="EURUSD",timeframe="M5",feed_id="fixture",price_basis="BID")
    view = registry.calculation("structure","config",source,"history",125)
    timing = TemporalEvidence(100,110,112,0,125,True,"VERIFIED_ARCHIVE_SIMULATED_AVAILABILITY").contract(reason="ELIGIBLE")
    timing["strategy_eligibility"]="SIMULATION_ONLY_ELIGIBLE"
    event = {"id":"pivot","type":"PIVOT","status":"confirmed","algorithm_version":2,
             "available_at":112,"confirmation_time":110,"timing":timing}
    view.publish(event,"structure",source,"config",predicate="FORMATION")
    assert strategy_eligible(event,125,registry=view,purpose="HISTORICAL_SIMULATION")
    assert not strategy_eligible(event,125,registry=view,purpose="STRICT_RECORDED")
    assert not strategy_eligible(event,125,registry=view,current=True,purpose="HISTORICAL_SIMULATION")
    assert not strategy_eligible(event,111,registry=view,purpose="HISTORICAL_SIMULATION")
    assert not strategy_eligible(event,125,registry=EvidenceRegistry(purpose="HISTORICAL_SIMULATION"),
                                 purpose="HISTORICAL_SIMULATION")
    unknown = {"id":"other","timing":{"strategy_eligibility":"UNVERIFIED_AVAILABILITY"}}
    assert not strategy_eligible(unknown,125,registry=view,purpose="HISTORICAL_SIMULATION")


def test_persistence_conflict_immutable_duplicate_archive_and_metadata(client,plan):
    initial_plan = client.post("/api/trades",json=plan).json()
    created = client.post(ROUTE,json=ready_definition())
    assert created.status_code==201,created.text
    original = created.json()
    url = ROUTE+"/"+original["strategy_id"]
    definition = original["definition"]
    assert original["version"]==1 and original["status"]=="READY_FOR_EVALUATION"
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(client.post,url+"/revisions",json={
            "expected_version":1,"definition":{**definition,"name":name}}) for name in ("Service A","Service B")]
        attempts = sorted((future.result() for future in futures),key=lambda response:response.status_code)
    assert [item.status_code for item in attempts]==[201,409]
    winner = attempts[0].json()
    assert winner["version"]==2 and winner["version_id"]!=original["version_id"]
    assert winner["configuration_fingerprint"]==original["configuration_fingerprint"]
    assert attempts[1].json()["detail"]["current_version"]==2
    assert client.get(url+"?version=1").json()==original
    history = client.get(url+"/history").json()
    assert [row["version"] for row in history]==[2,1]
    compare = client.get(url+"/compare?from_version=1&to_version=2").json()
    assert compare["same_configuration"] and [change["path"] for change in compare["changes"]]==["name"]
    clone = client.post(url+"/duplicate",json={"expected_version":2}).json()
    assert clone["strategy_id"]!=original["strategy_id"] and clone["version"]==1 and clone["status"]=="DRAFT"
    archived = client.post(url+"/archive",json={"expected_version":2}).json()
    assert archived["version"]==3 and archived["status"]=="ARCHIVED"
    assert client.post(url+"/revisions",json={"expected_version":3,"definition":definition}).status_code==409
    assert client.get(url+"?version=1").json()==original
    assert len(client.get(ROUTE).json())==2
    assert client.get("/api/trades/"+initial_plan["id"]).json()["plan"]==initial_plan["plan"]
    with transaction() as db, pytest.raises(sqlite3.IntegrityError,match="immutable"):
        db.execute("UPDATE strategy_versions SET status='DRAFT' WHERE strategy_id=? AND version=1",(original["strategy_id"],))


def test_semantic_fingerprint_deterministic_preserves_ordered_sequence():
    data = template("bearish")
    original = validate_definition(data)
    same = validate_definition(json.loads(json.dumps(data,sort_keys=True)))
    assert original["configuration_fingerprint"]==same["configuration_fingerprint"]
    assert validate_definition(original["definition"])["configuration_fingerprint"]==original["configuration_fingerprint"]
    changed = deepcopy(data)
    changed["temporal"]=[]
    reversed_order = deepcopy(changed)
    reversed_order["setup"]["children"].reverse()
    assert validate_definition(changed)["configuration_fingerprint"]!=validate_definition(reversed_order)["configuration_fingerprint"]


def test_recorded_definition_stays_operationally_blocked():
    data = template("bullish")
    data["availability_modes"] = ["STRICT_RECORDED"]
    denied = validate_definition(data)
    assert any(e["code"]=="RECORDED_SCOPE" for e in denied["errors"])
    for role in ("primary","comparison"):
        data["instruments"][role]["source"]="MT5_SAVED"
    for phase in ("context","setup"):
        for row in data[phase]["children"]:
            row["evidence"]["purpose"]="STRICT_RECORDED"
    confirmed = validate_definition(data)
    assert confirmed["valid"] and not confirmed["restrictions"]["recorded_operational"]
