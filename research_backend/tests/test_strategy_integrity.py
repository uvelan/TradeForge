from copy import deepcopy

import pytest

from research_backend.app.crt import CRTDelivery, CRTRules, evaluate_crt
from research_backend.app.strategy_binding import AttemptBinding, crt_family_validity, expiry_status, select_candidate
from research_backend.app.strategy_definitions import Outcome, TimeBound, causal_order, validate_definition
from research_backend.app.strategy_evidence import EvidenceRegistry, fingerprint
from research_backend.app.strategy_routes import store_version
from research_backend.app.db import transaction
from test_smt_causal import LEFT, RIGHT, archive, bars, event, query
from test_strategy_definitions import ROUTE, group, ready_definition, ready_definition_v2
from test_strict_causal_timing import ARCHIVE, OPEN, bar, canonical_seed


@pytest.mark.parametrize("direction,side",[("bullish","LONG"),("bearish","SHORT")])
@pytest.mark.parametrize("entry",["MARKET","LIMIT","STOP"])
def test_explicit_intent_and_fixed_price(direction,side,entry):
    item=ready_definition_v2(direction)
    item["trade_intent"]["entry"]={"kind":entry}
    if entry!="MARKET":
        assert "trade_intent.entry.price" in validate_definition(item)["unresolved"]
        assert not validate_definition(item)["valid"]
        item["trade_intent"]["entry"]["price"]={"kind":"FIXED_ABSOLUTE","value":1.123,"basis":"BID"}
    reply=validate_definition(item)
    assert reply["valid"], reply["errors"]
    assert reply["definition"]["trade_intent"]["direction"]==side


@pytest.mark.parametrize("key,value,code",[
    ("direction",None,"UNRESOLVED_REQUIRED"),("role",None,"UNRESOLVED_REQUIRED"),
    ("role","PAIR","SCHEMA_LITERAL_ERROR"),("earliest",None,"UNRESOLVED_REQUIRED"),
    ("entry",{"kind":"MARKET","price":1.0},"SCHEMA_EXTRA_FORBIDDEN"),
    ("entry",{"kind":"LIMIT","price":{"kind":"EXPRESSION","value":1.2,"basis":"BID"}},"SCHEMA_LITERAL_ERROR"),
    ("entry",{"kind":"STOP","price":{"kind":"FIXED_ABSOLUTE","value":1.2,"basis":"ASK"}},"INCOMPATIBLE_PRICE_BASIS"),
])
def test_invalid_intent(key,value,code):
    data=ready_definition_v2()
    data["trade_intent"][key]=value
    assert code in {error["code"] for error in validate_definition(data)["errors"]}


@pytest.mark.parametrize("path,code", [
    ("instrument_limits","RISK_CAP_BELOW_INTENT"),
    ("max_simultaneous_exposure_pct","INVALID_EXPOSURE"),
    ("max_session_loss_pct","RISK_CAP_BELOW_INTENT"),
    ("break_even_at_r","MANAGEMENT_AFTER_TARGET"),
    ("partial_exit","MANAGEMENT_AFTER_TARGET"),
])
def test_risk_rejects_contradictions(path,code):
    data=ready_definition_v2()
    if path=="instrument_limits":
        data["risk"][path][0]["max_risk_percent"]=0.25
    elif path in data["risk"]:
        data["risk"][path]=0.25
    else:
        data["management"][path]=2 if path=="break_even_at_r" else {"at_r":3,"percent":50}
    assert code in {error["code"] for error in validate_definition(data)["errors"]}


def test_semantic_fingerprints_pin_intent_binding_selection_and_metadata():
    base=ready_definition_v2()
    def digest(item):
        reply=validate_definition(item)
        assert reply["valid"],reply["errors"]
        return reply["configuration_fingerprint"]
    baseline=digest(base)
    for section,key,value in [
        ("trade_intent","direction","SHORT"),("trade_intent","role","COMPARISON"),
        ("trade_intent","entry",{"kind":"STOP","price":{"kind":"FIXED_ABSOLUTE","value":1.2,"basis":"BID"}}),
        ("attempt","context_mode","CONTINUOUS"),("attempt","candidate_limit",11),
    ]:
        item=deepcopy(base)
        item[section][key]=value
        assert digest(item)!=baseline
    item=deepcopy(base)
    item["context"]["children"][0]["selection"]="LATEST_ELIGIBLE"
    assert digest(item)!=baseline
    item=deepcopy(base)
    item["context"]["children"][0]["alias"]="replacement_name"
    item["setup"]["children"][0]["same_family_as"]="replacement_name"
    item["attempt"]["crt_reference_alias"]="replacement_name"
    assert digest(item)!=baseline
    assert digest({**base,"name":"Another title"})==baseline


@pytest.mark.parametrize("mutate,code",[
    (lambda x:x["attempt"].update(candidate_limit=129),"SCHEMA_LESS_THAN_EQUAL"),
    (lambda x:x["setup"]["children"][0].update(same_family_as="smt_confirmation"),"INCOMPATIBLE_FAMILY_ALIAS"),
    (lambda x:x["temporal"].append({"first":"divergence","then":"reference","relation":"AFTER"}),"CYCLIC_RULE_GRAPH"),
    (lambda x:x["context"].update(operator="ANY"),"AMBIGUOUS_BINDING_BRANCH"),
    (lambda x:x["setup"]["children"][0].update(same_family_as="crt_reclaim"),"CYCLIC_RULE_GRAPH"),
    (lambda x:x["context"]["children"][0]["evidence"].update(state="CURRENT"),"INVALID_CRT_ACTIVATION"),
])
def test_ambiguous_binding_shapes(mutate,code):
    item=ready_definition_v2()
    mutate(item)
    assert code in {error["code"] for error in validate_definition(item)["errors"]}


@pytest.mark.parametrize("operator",["ANY","NOT"])
def test_temporal_links_cannot_read_discretionary_branches(operator):
    data=ready_definition_v2()
    data["trigger"]["operator"]=operator
    if operator=="NOT":
        data["trigger"]["unknown_policy"]="PROPAGATE"
    data["temporal"].append({"first":"divergence","then":"trigger_event","relation":"AFTER"})
    assert "INVALID_TEMPORAL_LINK" in {error["code"] for error in validate_definition(data)["errors"]}
    data["trigger"]["children"][0]["alias"]="cannot_bind"
    assert "AMBIGUOUS_BINDING_BRANCH" in {error["code"] for error in validate_definition(data)["errors"]}


def test_legacy_row_unchanged_on_revision_and_blocked_on_new_ready_write(client):
    old=ready_definition()
    original=validate_definition(old)
    assert original["restrictions"]["requires_schema_revision"]
    assert client.post(ROUTE,json=old).status_code==422
    assert client.post(ROUTE+"/validate",json=old).json()["errors"][0]["code"]=="LEGACY_REQUIRES_REVISION"
    with transaction() as db:
        stored=store_version(db,"ST-"+"a"*32,1,original)
    url=ROUTE+"/"+stored["strategy_id"]
    next_revision=client.post(url+"/revisions",json={"expected_version":1,"definition":ready_definition_v2()})
    assert next_revision.status_code==201,next_revision.text
    assert client.get(url+"?version=1").json()==stored
    assert stored["definition"]["schema_version"]==1
    assert "trade_intent" not in stored["definition"]
    assert client.post(url+"/revisions",json={"expected_version":2,"definition":{**old,"status":"DRAFT"}}).status_code==422


def crt_fixture(*, terminal=None):
    a=11*3600
    rows=[bar(t,(103,120,90,103)) for t in range(a-7200,a-3600,300)]
    rows += [bar(t,(103,105,100,103)) for t in range(a-3600,a,300)]
    values=[(103,104,103,103)]*8
    values[3]=(104,106,103,104)
    values[4]=(103,104,102,103)
    if terminal is not None:
        index,values_at_index=terminal
        values[index]=values_at_index
    rows += [bar(a+i*300,values_at_index) for i,values_at_index in enumerate(values)]
    cutoff=rows[-1][1]
    registry=EvidenceRegistry()
    producer=registry.calculation("crt","fixture-v1",ARCHIVE,"fixture",cutoff)
    result=evaluate_crt(rows,300,cutoff,"H1",CRTRules(market_schedule=OPEN),CRTDelivery(),ARCHIVE,registry=producer)
    peer,reference=result["crt_formations"]
    assert peer["range_id"]!=reference["range_id"]
    assert reference["available_at"]==a
    return registry,producer,result,reference,peer


def select(registry,producer,events,at,policy="EARLIEST_ELIGIBLE",**options):
    return select_candidate(registry,events,snapshot=producer.snapshot,cutoff=at,policy=policy,
                            **{"age":7200,"limit":32,"coverage_known":True,**options})


def test_two_range_attempt_cannot_rebind_when_reclaimed_completed_or_expired(client):
    a=11*3600
    registry,producer,result,reference,peer=crt_fixture(terminal=(6,(103,104,100,101)))
    reclaim,=result["crt_sweeps"]
    expansion,completion=result["crt_revisions"]
    assert reclaim["available_at"]==a+1200
    assert completion["state"]=="OPPOSITE_BOUNDARY_REACHED"
    ok,bound=select(registry,producer,[peer,reference],a,"LATEST_ELIGIBLE",current=True)
    assert ok==Outcome.SATISFIED and bound.family==reference["range_id"]
    attempt=AttemptBinding(attempt_id=fingerprint(["attempt",1]),strategy_version_id="SV-"+"a"*32,
                          alias="crt_reference",evidence=bound,activated_at=a)
    _,wrong=select(registry,producer,[peer],a)
    assert select(registry,producer,[reclaim],a+1200,"BOUND_FAMILY",bound=wrong)==(Outcome.UNKNOWN,None)
    assert select(registry,producer,[reclaim],a+1200,"BOUND_FAMILY",bound=bound)[0]==Outcome.SATISFIED
    assert not producer.current_at(reference["id"],a+1200)
    check=lambda revision,at:crt_family_validity(registry,bound,revision,snapshot=producer.snapshot,
                                                cutoff=at,coverage_known=True)
    assert check(reclaim,a+1200)==Outcome.SATISFIED
    assert check(peer,a+1200)==Outcome.UNKNOWN
    start=a+1200
    smt=event(query(client,archive(bars(LEFT,start=start),bars(RIGHT,start=start),start=start,end=start+720)))
    assert smt["confirmed_at"]==a+1800 and smt["direction"]=="bearish"
    assert causal_order(reclaim["available_at"],smt["confirmed_at"],a+1800)==Outcome.SATISFIED
    assert check(expansion,a+1800)==Outcome.SATISFIED
    assert check(completion,a+2100)==Outcome.NOT_SATISFIED
    assert check(peer,a+2100)==Outcome.UNKNOWN
    assert producer.current_at(peer["id"],a+2100)
    window=TimeBound(unit="SECONDS",count=1800)
    assert expiry_status(window,attempt.activated_at,a+1800,traded_role="PRIMARY")==Outcome.NOT_SATISFIED
    assert expiry_status(window,attempt.activated_at,a+2100,traded_role="PRIMARY")==Outcome.SATISFIED
    bar_window=TimeBound(unit="ELIGIBLE_BARS",count=6,timeframe="M5")
    assert expiry_status(bar_window,attempt.activated_at,a+2100,traded_role="PRIMARY")==Outcome.UNKNOWN
    assert expiry_status(bar_window,attempt.activated_at,a+2100,traded_role="PRIMARY",bar_count=7)==Outcome.SATISFIED
    assert attempt.evidence.family!=wrong.family
    next_attempt=AttemptBinding(attempt_id=fingerprint(["attempt",2]),strategy_version_id=attempt.strategy_version_id,
                               alias=attempt.alias,evidence=wrong,activated_at=a+2100)
    assert next_attempt.attempt_id!=attempt.attempt_id and attempt.activated_at==a


@pytest.mark.parametrize("terminal,state,outcome", [
    ((3,(103,106,99,103)),"AMBIGUOUS",Outcome.UNKNOWN),
    ((5,(104,108,103,107)),"INVALIDATED",Outcome.NOT_SATISFIED),
])
def test_bound_range_ambiguity_and_invalidation(terminal,state,outcome):
    registry,producer,result,reference,peer=crt_fixture(terminal=terminal)
    cutoff=producer.cutoff
    last=next(row for row in result["crt_sweeps"]+result["crt_revisions"] if row["state"]==state)
    _,bound=select(registry,producer,[reference],reference["available_at"])
    assert crt_family_validity(registry,bound,last,snapshot=producer.snapshot,cutoff=cutoff,
                               coverage_known=True)==outcome
    assert producer.current_at(peer["id"],cutoff)


def test_candidate_order_limits_exact_snapshots_and_coverage():
    registry=EvidenceRegistry()
    first=registry.calculation("structure","config",ARCHIVE,"first",3000)
    second=registry.calculation("structure","config",ARCHIVE,"second",3000)
    peers=[]
    for producer in (first,second):
        for name in ("z","a"):
            row=canonical_seed(name,600,kind="PIVOT")
            producer.publish(row,"structure",ARCHIVE,"a"*64,predicate="FORMATION")
            if producer is second:
                duplicate=row
            else:
                peers.append(row)
    assert select(registry,first,peers,600)[1].reference.event_id=="a"
    assert select(registry,first,reversed(peers),600,"LATEST_ELIGIBLE")[1].reference.event_id=="z"
    assert select(registry,first,peers,600,limit=1)==(Outcome.UNKNOWN,None)
    assert select(registry,first,peers,600,coverage_known=False)==(Outcome.UNKNOWN,None)
    assert select(registry,first,[duplicate],600)==(Outcome.UNKNOWN,None)
    assert select(registry,first,[{"id":"a"}],600)==(Outcome.UNKNOWN,None)
    assert select(registry,first,[{**peers[0],"type":"tampered"}],600)==(Outcome.UNKNOWN,None)
    assert select(registry,first,peers,700,age=99)==(Outcome.UNKNOWN,None)


def test_smt_canonical_role_fields_do_not_set_trade_direction(client):
    catalog=client.get(ROUTE+"/capabilities").json()["observations"]
    assert catalog["SMT_LEADER"]["field"]=="leader"
    assert catalog["SMT_LAGGARD"]["projection"]=="OPPOSITE_PAIR_ROLE"
    for left,right,leader in ((LEFT,RIGHT,"primary"),(RIGHT,LEFT,"comparison")):
        observed=event(query(client,archive(bars(left),bars(right))))["revisions"][-1]
        assert observed["leader"]==leader
        item=ready_definition_v2("bullish")
        item["trigger"]=group("trigger",{**item["trigger"]["children"][0],"observation":"SMT_LEADER","expected":leader})
        item["invalidation"]["children"][0].update(observation="SMT_LAGGARD",expected=leader)
        assert validate_definition(item)["valid"]
        assert item["trade_intent"]["direction"]=="LONG"


def test_traded_role_controls_risk_cap_and_missing_peer_rejects():
    data=ready_definition_v2()
    data["risk"]["instrument_limits"][1]["max_risk_percent"]=0.25
    assert validate_definition(data)["valid"]
    data["trade_intent"]["role"]="COMPARISON"
    assert "RISK_CAP_BELOW_INTENT" in {error["code"] for error in validate_definition(data)["errors"]}
    data["instruments"]["comparison"]=None
    assert "UNBOUND_TRADED_ROLE" in {error["code"] for error in validate_definition(data)["errors"]}


def test_partial_exit_requires_configured_gross_reward_floor():
    data=ready_definition_v2()
    data["management"]["partial_exit"]={"at_r":1,"percent":50}
    assert "PARTIAL_REWARD_BELOW_MINIMUM" in {error["code"] for error in validate_definition(data)["errors"]}
    data["management"]["target"]["multiple"]=3
    assert validate_definition(data)["valid"]


def test_crt_invalidation_on_selected_dependency_requires_same_family():
    data=ready_definition_v2()
    rule=data["invalidation"]["children"][0]
    rule.update(dependency="crt",observation="CRT_STATE",expected="OPPOSITE_BOUNDARY_REACHED")
    assert "CRT_INVALIDATION_REQUIRES_REFERENCE" in {error["code"] for error in validate_definition(data)["errors"]}
    rule.update(same_family_as="crt_reference",selection="BOUND_FAMILY")
    assert validate_definition(data)["valid"]
