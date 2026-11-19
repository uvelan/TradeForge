"""Final binding restrictions using actual canonical CRT and strict SMT publications."""
import json
from pathlib import Path

import pytest

from research_backend.app import smt_causal
from research_backend.app.crt import CRTDelivery, CRTRules, evaluate_crt
from research_backend.app.db import transaction
from research_backend.app.strategy_binding import activate_crt_attempt, crt_family_validity, select_candidate
from research_backend.app.strategy_definitions import Outcome, causal_order, validate_definition
from research_backend.app.strategy_evidence import EvidenceRegistry, fingerprint
from research_backend.app.strategy_routes import store_version
from test_smt_causal import LEFT, RIGHT, archive, bars, event, query
from test_strategy_definitions import ROUTE, ready_definition_v2
from test_strategy_integrity import crt_fixture, select
from test_strict_causal_timing import ARCHIVE, OPEN, bar

A = 11 * 3600
VERSION_ID = "SV-" + "a" * 32


def crt_rule(data):
    rule = data["trigger"]["children"][0]
    rule.update(dependency="crt",observation="CRT_STATE",operator="EQ",expected="EXPANSION_OBSERVED",
                selection="LATEST_ELIGIBLE",alias=None,same_family_as=None)
    return rule


@pytest.mark.parametrize("phase,observation,expected,state", [
    ("context","CRT_MIDPOINT", 1.2,"CURRENT"),
    ("setup","CRT_STATE","EXPANSION_OBSERVED","HISTORICAL"),
    ("trigger","CRT_STATE","EXPANSION_OBSERVED","HISTORICAL"),
    ("invalidation","CRT_STATE","OPPOSITE_BOUNDARY_REACHED","HISTORICAL"),
])
def test_crt_projection_requires_tracked_family(phase,observation,expected,state):
    data=ready_definition_v2()
    rule=crt_rule(data)
    rule.update(id="projection",observation=observation,expected=expected)
    rule["evidence"]={**rule["evidence"],"state":state}
    if phase=="trigger":
        data[phase]["children"]=[rule]
    else:
        data=ready_definition_v2()
        data[phase]["children"].append(rule)
    check=validate_definition(data)
    assert not check["valid"]
    error=next(e for e in check["errors"] if e["code"] in {
        "CRT_LIFECYCLE_REQUIRES_REFERENCE","CRT_INVALIDATION_REQUIRES_REFERENCE"})
    assert error["path"]=="conditions.projection.same_family_as"
    rule.update(selection="BOUND_FAMILY",same_family_as="crt_reference")
    check=validate_definition(data)
    assert check["valid"],check["errors"]


def test_reviewed_invalid_ready_revision_is_read_only_evidence(client):
    original=ready_definition_v2()
    rule=crt_rule(original)
    baseline=json.loads((Path(__file__).resolve().parents[2] /
        "docs/implementation/evidence/TF-I09A-closure/baseline.json").read_text(encoding="utf-8-sig"))["crt_trigger"]
    assert baseline["valid"] and baseline["trigger"]==rule
    strategy="ST-"+"b"*32
    with transaction() as db:
        saved=store_version(db,strategy,1,{"definition":original,"configuration_fingerprint":baseline["fingerprint"]})
    url=ROUTE+"/"+strategy
    opened=client.get(url).json()
    assert opened["definition"]==saved["definition"] and opened["configuration_fingerprint"]==baseline["fingerprint"]
    assert opened["binding_revision"]["errors"][0]["code"]=="CRT_LIFECYCLE_REQUIRES_REFERENCE"
    mutation={"expected_version":1,"definition":original}
    assert client.post(url+"/revisions",json=mutation).status_code==422
    rule.update(selection="BOUND_FAMILY",same_family_as="crt_reference")
    assert client.post(url+"/revisions",json=mutation).status_code==201
    assert client.get(url+"?version=1").json()==opened


def test_compatible_definition_keeps_reviewed_fingerprint():
    evidence=Path(__file__).resolve().parents[2] / "docs/implementation/evidence/TF-I09A-integrity/api-example.json"
    example=json.loads(evidence.read_text(encoding="utf-8-sig"))
    checked=validate_definition(example["body"])
    assert checked["valid"]
    assert checked["configuration_fingerprint"]==example["response"]["configuration_fingerprint"]
    assert checked["restrictions"]["binding_policy_version"]==2


@pytest.mark.parametrize("observation",["SMT_LEADER","SMT_LAGGARD"])
def test_smt_roles_require_confirmed_direction_alias(observation):
    data=ready_definition_v2()
    role=data["trigger"]["children"][0]
    role.update(observation=observation,expected="primary")
    assert any(error["code"]=="SMT_ROLE_REQUIRES_DIVERGENCE" for error in validate_definition(data)["errors"])
    role.update(selection="BOUND_FAMILY",same_family_as="smt_confirmation")
    assert validate_definition(data)["valid"]
    data["setup"]["children"][1].update(observation="SMT_LEADER",expected="comparison")
    assert any(error["code"]=="SMT_ROLE_REQUIRES_DIVERGENCE" for error in validate_definition(data)["errors"])


@pytest.mark.parametrize("first,later,relation,valid",[
    ("reference","trigger_event","AFTER",True),
    ("trigger_event","reference","BEFORE",True),
    ("trigger_event","reference","AFTER",False),
    ("reference","trigger_event","BEFORE",False),
    ("divergence","reference","AFTER",False),
    ("trigger_event","invalid_event","AFTER",False),
    ("reference","invalid_event","AFTER",True),
])
def test_temporal_phase_precedence(first,later,relation,valid):
    data=ready_definition_v2()
    data["temporal"].append({"first":first,"then":later,"relation":relation})
    result=validate_definition(data)
    assert result["valid"]==valid
    if not valid:
        assert any(e["code"]=="UNSUPPORTED_PHASE_ORDER" for e in result["errors"])
    assert causal_order(100,100,100)==Outcome.NOT_SATISFIED


def activation(registry,producer,reference,revision,cutoff):
    return activate_crt_attempt(registry,reference,revision,snapshot=producer.snapshot,cutoff=cutoff,
                                age=7200,coverage_known=True,attempt_id=fingerprint(["closure",cutoff]),
                                strategy_version_id=VERSION_ID,alias="crt_reference")


def two_reclaimed_ranges():
    rows=[bar(t,(103,105,100,103)) for t in range(A-3600,A,300)]
    first_hour=[(104,105,103.5,104.5)]*12
    first_hour[3]=(104.5,106,103.5,104.5)
    second_hour=[
        (104,105,102.75,104.5),   # 12:05 B reclaims its low.
        (104.75,105,104,104.8),   # 12:10 B expands while A has no expansion.
        (104,104.5,103.5,104),
        (103.5,104,102,103.5),   # 12:20 A expands.
        (104,104.5,103.5,104),   # 12:25 A expires while B stays usable.
    ]
    rows += [bar(A+i*300, values) for i,values in enumerate(first_hour+second_hour)]
    registry=EvidenceRegistry()
    cutoff=rows[-1][1]
    producer=registry.calculation("crt","closure-v1",ARCHIVE,"two-reclaimed-ranges",cutoff)
    result=evaluate_crt(rows,300,cutoff,"H1",CRTRules(market_schedule=OPEN,max_age=17),CRTDelivery(),
                        ARCHIVE,registry=producer)
    left,right=result["crt_formations"]
    return registry,producer,result,left,right


def test_peer_expansion_cannot_qualify_selected_range(client):
    registry,producer,result,left,right=two_reclaimed_ranges()
    start=A+1200
    samples=(A+4200-start)//60
    primary=bars(LEFT+[11]*(samples-len(LEFT)),start=start)
    comparison=bars(RIGHT+[24]*(samples-len(RIGHT)),start=start)
    smt=event(query(client,archive(primary,comparison,start=start,end=A+4200)))
    assert smt["confirmed_at"]==A+1800 and smt["historically_eligible"]
    reclaim_a,reclaim_b=result["crt_sweeps"]
    assert reclaim_a["range_id"]==left["range_id"] and reclaim_b["range_id"]==right["range_id"]
    assert causal_order(reclaim_a["available_at"],smt["confirmed_at"],A+1800)==Outcome.SATISFIED

    _,reference=select(registry,producer,[left],A+300)
    _,attempt=activation(registry,producer,reference,left,A+300)
    assert attempt.activated_at==A+300 and reference.reference.available_at==A
    expansions=[e for e in result["crt_revisions"] if e["state"]=="EXPANSION_OBSERVED"]
    peer,own=expansions[:2]
    assert (peer["range_id"],peer["available_at"])==(right["range_id"],A+4200)
    assert (own["range_id"],own["available_at"])==(left["range_id"],A+4800)
    assert crt_family_validity(registry,attempt.evidence,reclaim_a,snapshot=producer.snapshot,
                               cutoff=A+4200,coverage_known=True)==Outcome.SATISFIED
    assert select(registry,producer,[peer],A+4200,"BOUND_FAMILY",bound=attempt.evidence)==(Outcome.UNKNOWN,None)
    qualified=select(registry,producer,[peer,own],A+4800,"BOUND_FAMILY",bound=attempt.evidence)
    assert qualified[0]==Outcome.SATISFIED and qualified[1].reference.event_id==own["id"]

    expired=next(e for e in result["crt_revisions"] if e["state"]=="EXPIRED" and e["range_id"]==left["range_id"])
    peer_current=next(e for e in result["current_crt"]["items"] if e["range_id"]==right["range_id"])
    assert producer.current_at(peer_current["id"],producer.cutoff)
    assert activation(registry,producer,reference,expired,producer.cutoff)==(Outcome.NOT_SATISFIED,None)
    assert activation(registry,producer,reference,peer_current,producer.cutoff)==(Outcome.UNKNOWN,None)
    assert activation(registry,producer,reference,left,A+300)==(Outcome.SATISFIED,attempt)


@pytest.mark.parametrize("terminal,state",[
    ((5,(104,108,103,107)),"INVALIDATED"),
    ((6,(103,104,100,101)),"OPPOSITE_BOUNDARY_REACHED"),
])
def test_new_attempt_cannot_revive_terminal_reference(terminal,state):
    registry,producer,result,reference,peer=crt_fixture(terminal=terminal)
    _,bound=select(registry,producer,[reference],A+300)
    active=activation(registry,producer,bound,reference,A+300)
    assert active[0]==Outcome.SATISFIED
    later=next(e for e in result["crt_revisions"] if e["state"]==state and e["range_id"]==reference["range_id"])
    assert activation(registry,producer,bound,later,producer.cutoff)==(Outcome.NOT_SATISFIED,None)
    assert activation(registry,producer,bound,reference,producer.cutoff)==(Outcome.UNKNOWN,None)
    assert producer.current_at(peer["id"],producer.cutoff)
    assert activation(registry,producer,bound,reference,A+300)==active


def test_strict_smt_cannot_mix_direction_and_leader_families(monkeypatch):
    views=[]
    class RecordingCoordinator(smt_causal.AnalysisCoordinator):
        def evidence_for(self, engine, *args, **kwargs):
            result=super().evidence_for(engine,*args,**kwargs)
            if engine=="smt":
                views.append(result)
            return result
    monkeypatch.setattr(smt_causal,"AnalysisCoordinator",RecordingCoordinator)
    primary=LEFT+[11]*5+RIGHT
    comparison=RIGHT+[24]*5+LEFT
    payload=archive(bars(primary),bars(comparison),end=len(primary)*60)
    result=smt_causal.evaluate_strict_smt(smt_causal.StrictSmtRequest.model_validate(payload))
    producer,=views
    confirmations=[rev for item in result["events"] for rev in item["revisions"] if rev["state"]=="SMT_CONFIRMED"]
    bearish=next(row for row in confirmations if row["direction"]=="bearish" and row["leader"]=="primary")
    matching=next(row for row in confirmations if row["leader"]=="comparison" and row["available_at"]>bearish["available_at"])
    assert bearish["market_event_id"]!=matching["market_event_id"]
    _,selected=select(producer.root,producer,[bearish],producer.cutoff)
    assert select(producer.root,producer,[matching],producer.cutoff,"BOUND_FAMILY",bound=selected)==(Outcome.UNKNOWN,None)
    chosen=select(producer.root,producer,[bearish],producer.cutoff,"BOUND_FAMILY",bound=selected)
    assert chosen==(Outcome.SATISFIED,selected)
