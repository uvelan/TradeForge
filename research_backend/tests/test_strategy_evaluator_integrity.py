"""Causal evaluator regressions over checksum-verified frozen producer inputs."""
import json

import pytest

from research_backend.app import strategy_evaluation_routes as routes
from research_backend.app import strategy_evaluation_sources as sources
from research_backend.app.strategy_definitions import StrategyDefinitionV2
from research_backend.app.strategy_evidence import fingerprint
from test_smt_causal import LEFT, RIGHT
from test_strategy_definitions import group
from test_strategy_evaluator import API, BASE, approved_fixture, frozen_candles, ready_entry, request_body


def selection(monkeypatch, series, end):
    result = {"id": "INTEGRITY_FIXTURE", "series": series, "start": BASE, "end": BASE+end}
    result["fingerprint"] = fingerprint(result)
    monkeypatch.setattr(sources, "selection_catalog", lambda: [result])
    monkeypatch.setattr(routes, "selection_catalog", lambda: [result])
    return result


def condition(id_, observation, dependency, expected, *, current=False, alias=None, bound=None, op="EQ"):
    return {"type":"RULE", "id":id_, "observation":observation, "dependency":dependency,
            "expected":expected, "operator":op, "alias":alias,
            "evidence":{"purpose":"HISTORICAL_SIMULATION", "state":"CURRENT" if current else "HISTORICAL",
                        "max_age_seconds":7200},
            "selection":"BOUND_FAMILY" if bound else "EARLIEST_ELIGIBLE", "same_family_as":bound}


def base_definition(pair=False):
    result = ready_entry(pair=pair)
    result["indicators"] = []
    result["attempt"]["crt_reference_alias"] = None
    result["temporal"] = []
    return result


def fvg_spec():
    return {"id":"fvg", "indicator":"FVG", "algorithm_version":1, "role":"PRIMARY", "timeframe":"M5",
            "settings":{"mitigation":"close"}}


def with_fvg_interaction(monkeypatch):
    series = []
    bars = [(99,100,97,99)]*8 + [(100,108,100,108),(106,110,105,109),(105,106,103,104),(104,105,103,104)]
    frozen_candles(series,"EURUSD","M5",BASE,bars)
    return selection(monkeypatch,series,3600)


def evaluate_case(client, archive, definition, *, start, cutoff):
    body = request_body(client, archive, definition, BASE+cutoff)
    body.update(evaluation_start=BASE+start)
    response = client.post(API,json=body)
    assert response.status_code == 201,response.text
    run = response.json()
    return run, sources.collect(StrategyDefinitionV2.model_validate(routes.load_run(run["run_id"])["definition"]),
                                run["selection"],BASE+cutoff,BASE+start)


@pytest.mark.parametrize("phase", ["positive-context","negative-context","invalidation","negative-trigger"])
def test_fvg_current_uncertainty(client, monkeypatch, phase):
    archive = with_fvg_interaction(monkeypatch)
    definition = base_definition()
    definition["instruments"]["primary"]["modeled_delay_seconds"] = 20
    definition["attempt"]["context_mode"] = "CONTINUOUS"
    definition["indicators"] = [fvg_spec()]
    active = condition("fvg-active","FVG_ACTIVE","fvg","ACTIVE",current=True)
    formed = condition("formed","FVG_STATE","fvg","ACTIVE")
    definition["context"] = group("context", condition("context-rule","FVG_STATE","fvg","ACTIVE"))
    definition["setup"] = group("setup", formed)
    definition["invalidation"] = group("invalidation", condition("invalidation-rule","FVG_STATE","fvg","FULLY_MITIGATED"))
    definition["trigger"] = group("trigger", condition("trigger-rule","FVG_STATE","fvg","PARTIALLY_MITIGATED"))
    if phase.endswith("context"):
        definition["context"] = group("context",active)
        if phase.startswith("negative"):
            definition["context"].update(operator="NOT",unknown_policy="PROPAGATE")
    elif phase == "invalidation":
        definition["invalidation"] = group("invalidation",{**active,"expected":"PARTIALLY_MITIGATED"})
    else:
        definition["trigger"] = group("trigger",active)
        definition["trigger"].update(operator="NOT",unknown_policy="PROPAGATE")
        definition["indicators"].append({"id":"structure", "indicator":"STRUCTURE", "algorithm_version":2,
                                         "role":"PRIMARY", "timeframe":"M5","settings":{"left":1,"right":1}})
        definition["invalidation"] = group("invalidation",condition("invalidation-rule","CHOCH_DIRECTION",
                                                                    "structure","BEARISH"))
    run, observed = evaluate_case(client,archive,definition,start=3300 if phase=="negative-context" else 1800,cutoff=3320)
    view = observed.views["fvg"]
    events = observed.events[view.snapshot]["fvg"][1]
    uncertain = [event for event in events if event["state"]=="UNCERTAIN"]
    assert len(uncertain)==1
    assert uncertain[0]["timing"]["eligibility_reason"]=="STRADDLING_OBSERVATION"
    decision = run["decision"]
    print(json.dumps({"fixture":phase,"trace":decision["phase_results"],"state":decision["state"],
                      "rules":[(rule["id"],rule["outcome"],rule["reason"]) for rule in decision["rules"]]}))
    assert next(rule for rule in decision["rules"] if rule["id"]=="fvg-active")["outcome"]=="UNKNOWN"


def two_smt_families(monkeypatch):
    series = []
    for symbol, values in (
        ("EURUSD",[11]*20+LEFT+[11]*5+RIGHT+[24]*32),
        ("GBPUSD",[24]*20+RIGHT+[32,24,24,24,24]+LEFT+[11]*32),
    ):
        frozen_candles(series,symbol,"M1",BASE,[(v,v+1,v-1,v) for v in values])
    bars = [(99,100,97,99)]*8 + [(100,108,100,108),(106,110,105,109)]+[(109,110,107,109)]*2
    frozen_candles(series,"EURUSD","M5",BASE,bars)
    return selection(monkeypatch,series,3600)


def continuous_pair():
    definition = base_definition(pair=True)
    definition["attempt"]["context_mode"] = "CONTINUOUS"
    definition["indicators"] = [ready_entry()["indicators"][1], fvg_spec()]
    definition["context"] = group("context",condition("context-smt","SMT_CONFIRMED","smt","bearish",
                                                        current=True,alias="pair_context"))
    definition["setup"] = group("setup",condition("role","SMT_LEADER","smt","primary",bound="pair_context"))
    definition["invalidation"] = group("invalidation",condition("failed","FVG_STATE","fvg","FULLY_MITIGATED"))
    definition["trigger"] = group("trigger",condition("later-trigger","FVG_STATE","fvg","ACTIVE"))
    return definition


def test_continuous_context_preserves_family(client, monkeypatch):
    archive = two_smt_families(monkeypatch)
    run, observed = evaluate_case(client,archive,continuous_pair(),start=1500,cutoff=3590)
    view = observed.views["smt"]
    events = observed.events[view.snapshot]["smt"][1]
    print(json.dumps([(event["available_at"]-BASE,event["state"],event["leader"],event["direction"],
                       event["market_event_id"]) for event in events]))
    decision = run["decision"]
    print(json.dumps({"state":decision["state"],"rules":decision["rules"],"transitions":decision["transitions"]}))
    rules = {rule["id"]:rule for rule in decision["rules"]}
    bound, = decision["attempt_bindings"]
    assert bound["family"]==rules["role"]["family"]
    assert bound["activated_at"]==BASE+1800
    assert bound["validity"]=="DEFINITIVELY_INACTIVE"
    assert decision["state"]=="INVALIDATED"
    assert decision["entry_intent"] is None

def semantic_prefix(payload):
    """Canonical identity fields remain exact, apart from snapshot coordinates."""
    chosen = {key:payload[key] for key in (
        "state","reason","attempt_id","terminal","context_activated_at","setup_activated_at","trigger_activated_at",
        "invalidation","expiry","rules","phase_results","temporal","attempt_bindings","activation_proofs",
        "temporal_predecessors","entry_intent","coverage")}

    def walk(value):
        if isinstance(value, dict):
            return {key:walk(item) for key,item in value.items() if key!="calculation_snapshot"}
        if isinstance(value, list):
            return [walk(item) for item in value]
        return value

    return walk(chosen)


@pytest.mark.parametrize("delay", [0, 15, 30])
def test_independent_smt_prefix_equivalence(client, monkeypatch, delay):
    from research_backend.app.strategy_evaluator import EvaluationMachine

    archive = two_smt_families(monkeypatch)
    strategy = continuous_pair()
    strategy["instruments"]["comparison"]["modeled_delay_seconds"] = delay
    run, horizon = evaluate_case(client,archive,strategy,start=1500,cutoff=3590)
    pinned = routes.load_run(run["run_id"])
    definition = StrategyDefinitionV2.model_validate(pinned["definition"])
    identity = pinned["identity"]
    for offset in (1699,1740,1800,1815,1875,1979,1980,1995,2070,2580,2820,3000,3590):
        cutoff = BASE+offset
        reference = routes.calculate(pinned,cutoff)
        compared = EvaluationMachine(definition,horizon,identity,BASE+1500,BASE+3600).run(cutoff)
        assert semantic_prefix(compared)==semantic_prefix(reference),offset


@pytest.mark.parametrize("version", [1,2,3,4,5])
def test_existing_run_create_has_exact_cutoff_and_preserves_legacy(client, approved_fixture,version):
    from research_backend.app.db import transaction, utc_now
    from test_strategy_evaluator import A

    body = request_body(client,approved_fixture,ready_entry(pair=False),A+4200)
    run = client.post(API,json=body).json()
    assert client.post(API,json=body).json()==run
    early = client.post(API,json={**body,"cutoff":A+3900})
    assert early.status_code==409
    assert early.json()["detail"]=={
        "code":"EXACT_CUTOFF_RECONSTRUCTION_REQUIRED",
        "reason":"This run already exists. Reconstruct the requested cutoff or advance the latest checkpoint.",
        "run_id":run["run_id"],"requested_cutoff":A+3900,"latest_cutoff":A+4200,
    }
    pinned = routes.load_run(run["run_id"])
    legacy = {**pinned,"run_id":"legacy-run","identity":{**pinned["identity"],"evaluator_version":version}}
    original = {**run["decision"],"decision_id":"1"*64}
    with transaction() as db:
        db.execute("INSERT INTO strategy_evaluations VALUES(?,?,?,?)",
                   ("legacy-run",body["strategy_version_id"],routes.canonical(legacy),utc_now()))
        routes.store_decision(db,"legacy-run",original,None)
    assert client.get(API+"/legacy-run").json()["decision"]==original
    for operation in ("advance","reconstruct"):
        fields = {"expected_decision_id":"1"*64} if operation=="advance" else {}
        response = client.post(API+"/legacy-run/"+operation,json={"cutoff":A+4800,**fields})
        assert response.status_code==409
        assert response.json()["detail"]["code"]=="LEGACY_EVALUATION_READ_ONLY"


def test_expiry_during_uncertain_invalidation(client, monkeypatch):
    archive = with_fvg_interaction(monkeypatch)
    definition = base_definition()
    definition["expiry"] = {"unit":"SECONDS","count":300}
    definition["instruments"]["primary"]["modeled_delay_seconds"] = 20
    definition["indicators"] = [fvg_spec()]
    definition["context"] = group("context",condition("ctx","FVG_STATE","fvg","ACTIVE"))
    definition["setup"] = group("setup",condition("formed","FVG_STATE","fvg","ACTIVE"))
    definition["invalidation"] = group("invalidation",condition("invalid","FVG_ACTIVE","fvg","PARTIALLY_MITIGATED",current=True))
    definition["trigger"] = group("trigger",condition("trg","FVG_STATE","fvg","PARTIALLY_MITIGATED"))
    run, _ = evaluate_case(client,archive,definition,start=1800,cutoff=3500)
    decision = run["decision"]
    assert decision["context_activated_at"]==BASE+3020
    assert decision["state"]=="EXPIRED"
    assert decision["transitions"][-1]==[BASE+3321,"EXPIRED","SETUP_EXPIRY_DURING_UNCERTAINTY"]
    assert decision["expiry"]=="SATISFIED"
    assert decision["entry_intent"] is None


def test_cooperative_deadline_rejects_at_bound():
    from research_backend.app.analysis_evidence import ResourceLimit
    from research_backend.app.strategy_evaluation_budget import deadline

    times = iter([0,19.99,20])
    check = deadline(clock=lambda:next(times))
    check()
    with pytest.raises(ResourceLimit,match="cooperative deadline"):
        check()

STRUCTURE = {"id":"structure","indicator":"STRUCTURE","algorithm_version":2,"role":"PRIMARY","timeframe":"M5",
             "settings":{"left":1,"right":1,"price_source":"close"}}
ORDER_BLOCK = {"id":"ob","indicator":"ORDER_BLOCK","algorithm_version":1,"role":"PRIMARY","timeframe":"M5",
               "settings":{},"producers":["structure"]}
LIQUIDITY = {"id":"pool","indicator":"LIQUIDITY","algorithm_version":1,"role":"PRIMARY","timeframe":"M5",
             "settings":{"min_separation_bars":1},"producers":["structure"]}


def prices(values):
    return [(value,)*4 for value in values]


OBSERVATION_CASES = (
    ("STRUCTURE_PIVOT","structure","HIGH","structure",900,False),
    ("STRUCTURE_BREAK","structure","BOS","structure",1200,False),
    ("BOS_DIRECTION","structure","BULLISH","structure",1200,False),
    ("CHOCH_DIRECTION","structure","BEARISH","structure",3000,False),
    ("STRUCTURE_BREAK_PRICE","structure",15,"structure",2400,False),
    ("LIQUIDITY_POOL","pool","SWEPT","pool",1800,False),
    ("LIQUIDITY_ACTIVE_POOL","pool","ACTIVE","pool",900,True),
    ("LIQUIDITY_SWEEP","pool","SWEPT","pool",1800,False),
    ("FVG_ACTIVE","fvg","ACTIVE","fvg",300,True),
    ("FVG_STATE","fvg","ACTIVE","fvg",300,False),
    ("ORDER_BLOCK_ACTIVE","ob","ACTIVE","ob",1200,True),
    ("ORDER_BLOCK_STATE","ob","ACTIVE","ob",1200,False),
)


def observation_fixture(monkeypatch, case):
    from test_market_structure import UP
    from test_order_blocks import BARS

    seeds = {
        "structure": prices(UP), "pool":prices([10,12,10,12,10])+[(11,13,11,11)],
        "fvg":[(99,100,97,99),(100,108,100,108),(106,110,105,109)],
        "ob":BARS,
    }
    # The left segment establishes the independent context/setup pivot.
    # A non-crossing delayed start separates it from the requested trigger.
    bars = prices([10,10,10,12,10])+seeds[case]
    records = []
    frozen_candles(records,"EURUSD","M5",BASE,bars)
    return selection(monkeypatch,records,len(bars)*300)


@pytest.mark.parametrize("observation,dependency,expected,case,offset,current",OBSERVATION_CASES)
def test_advertised_trigger_inventory(client,monkeypatch,observation,dependency,expected,case,offset,current):
    archive = observation_fixture(monkeypatch,case)
    strategy = base_definition()
    items = {"ob":ORDER_BLOCK,"pool":LIQUIDITY,"fvg":fvg_spec()}
    strategy["indicators"] = [STRUCTURE]+([items[dependency]] if dependency in items else [])
    strategy["context"] = group("context",condition("ctx","STRUCTURE_PIVOT","structure","HIGH"))
    strategy["setup"] = group("setup",condition("setup-pivot","STRUCTURE_PIVOT","structure","HIGH"))
    strategy["invalidation"] = group("invalidation",condition("never","STRUCTURE_BREAK_PRICE","structure",1000,op="GT"))
    strategy["trigger"] = group("trigger",condition("signal",observation,dependency,expected,current=current))
    run, horizon = evaluate_case(client,archive,strategy,start=1500,cutoff=archive["end"]-BASE)
    decision = run["decision"]
    proof = next(rule for rule in decision["rules"] if rule["id"]=="signal")
    assert decision["state"]=="TRIGGER_QUALIFIED",proof
    assert proof["evidence_reference"]["calculation_id"]==next(
        item["calculation_id"] for item in decision["producers"] if item["dependency"]==dependency)
    assert proof["evidence_reference"]["source"][:3]==["DUKASCOPY_SAVED","EURUSD","M5"]
    assert proof["available_at"]==decision["trigger_activated_at"]==BASE+1500+offset
    from research_backend.app.strategy_evaluator import EvaluationMachine

    pinned = routes.load_run(run["run_id"])
    compiled = StrategyDefinitionV2.model_validate(pinned["definition"])
    for cutoff in (proof["selected_at"]-1,proof["selected_at"]):
        prefix = routes.calculate(pinned,cutoff)
        later = EvaluationMachine(compiled,horizon,pinned["identity"],BASE+1500,archive["end"]).run(cutoff)
        assert semantic_prefix(prefix)==semantic_prefix(later)
        assert (prefix["entry_intent"] is not None)==(cutoff==proof["selected_at"])

@pytest.mark.parametrize("observation,expected,offset,current,bound", [
    ("CRT_REFERENCE","ACTIVE",3600,False,None),
    ("CRT_MIDPOINT",104.75,3600,True,None),
    ("CRT_RECLAIM","HIGH_SWEPT_RECLAIMED",1200,False,"crt_reference"),
    ("CRT_STATE","EXPANSION_OBSERVED",4800,False,"crt_reference"),
])
def test_crt_trigger_inventory(client,approved_fixture,observation,expected,offset,current,bound):
    from test_strategy_evaluator import A
    from research_backend.app.strategy_evaluator import EvaluationMachine

    definition = ready_entry(pair=False)
    if bound is None:
        definition["attempt"].update(crt_reference_alias=None,context_mode="CONTINUOUS")
        definition["invalidation"] = group("invalidation",condition("done","CRT_STATE","crt","OPPOSITE_BOUNDARY_REACHED"))
    if observation!="CRT_STATE":
        definition["setup"] = group("setup",condition("ready","CRT_REFERENCE","crt","ACTIVE",bound=bound))
    definition["trigger"] = group("trigger",condition("signal",observation,"crt",expected,current=current,bound=bound))
    begin = A + (300 if observation=="CRT_STATE" else 0)
    run, horizon = evaluate_case(client,approved_fixture,definition,start=begin-BASE,cutoff=A-BASE+4800)
    decision = run["decision"]
    assert decision["state"]=="TRIGGER_QUALIFIED",decision["phase_results"]
    proof = next(rule for rule in decision["rules"] if rule["id"]=="signal")
    assert proof["available_at"]==decision["trigger_activated_at"]==A+offset
    pinned = routes.load_run(run["run_id"])
    model = StrategyDefinitionV2.model_validate(pinned["definition"])
    for cutoff in (A+offset-1,A+offset):
        reference = routes.calculate(pinned,cutoff)
        later = EvaluationMachine(model,horizon,pinned["identity"],begin,approved_fixture["end"]).run(cutoff)
        assert semantic_prefix(reference)==semantic_prefix(later)
        assert (reference["entry_intent"] is not None)==(cutoff==A+offset)


@pytest.mark.parametrize("role,required", [("SMT_LEADER","primary"),("SMT_LAGGARD","comparison")])
def test_canonical_smt_role_projection(client,monkeypatch,role,required):
    archive = two_smt_families(monkeypatch)
    definition = continuous_pair()
    definition["setup"]["children"][0].update(observation=role,expected=required)
    run, _ = evaluate_case(client,archive,definition,start=1500,cutoff=1810)
    decision = run["decision"]
    context, selected = (next(rule for rule in decision["rules"] if rule["id"]==name)
                         for name in ("context-smt","role"))
    assert selected["outcome"]=="SATISFIED"
    assert selected["family"]==context["family"]
    assert selected["evidence_reference"]==context["evidence_reference"]
    assert selected["available_at"]==BASE+1800
    opposite = "comparison" if required=="primary" else "primary"
    definition["setup"]["children"][0]["expected"] = opposite
    run, _ = evaluate_case(client,archive,definition,start=1500,cutoff=1810)
    assert run["decision"]["setup_activated_at"] is None

def test_smt_confirmed_as_later_trigger(client,monkeypatch):
    archive = two_smt_families(monkeypatch)
    strategy = continuous_pair()
    structure = {**STRUCTURE,"timeframe":"M1"}
    strategy["indicators"] = [strategy["indicators"][0], structure]
    strategy["context"] = group("context",condition("ctx","STRUCTURE_PIVOT","structure","HIGH"))
    strategy["setup"] = group("setup",condition("ready","STRUCTURE_PIVOT","structure","HIGH"))
    strategy["invalidation"] = group("invalidation",condition("never","STRUCTURE_BREAK_PRICE","structure",1000,op="GT"))
    strategy["trigger"] = group("trigger",condition("signal","SMT_CONFIRMED","smt","bearish"))
    run, _ = evaluate_case(client,archive,strategy,start=1440,cutoff=1810)
    decision = run["decision"]
    assert decision["trigger_activated_at"]==BASE+1800,decision["phase_results"]
    proof = next(item for item in decision["rules"] if item["id"]=="signal")
    assert proof["available_at"]==BASE+1800
    assert set(proof["evidence_reference"]["participants"])=={"primary","comparison"}
    assert routes.calculate(routes.load_run(run["run_id"]),BASE+1799)["entry_intent"] is None


@pytest.mark.parametrize("case,delay,terminal,classification", [
    ("fvg",0,"FULLY_MITIGATED","DEFINITIVELY_INACTIVE"),
    ("ob",20,"UNCERTAIN","UNKNOWN"),
    ("ob",0,"INVALIDATED","DEFINITIVELY_INACTIVE"),
    ("pool",20,"UNCERTAIN","UNKNOWN"),
])
def test_canonical_family_revision_classification(client,monkeypatch,case,delay,terminal,classification):
    from test_order_blocks import BARS
    from research_backend.app.arrival_policy import SIMULATION_MODE

    pattern = {
        "fvg":[(99,100,97,99),(100,108,100,108),(106,110,105,109),(105,107,99,102)],
        "ob":(BARS[:3]+[(10,12,9,10),BARS[3],(13,14,11,13)] if delay else BARS+[(13,14,8,8)]),
        "pool":prices([10,12,10,12,10])+[(11,13,11,11)],
    }
    bars = prices([10,10,10,12,10])+pattern[case]+prices([11,11])
    records = []
    frozen_candles(records,"EURUSD","M5",BASE,bars)
    archive = selection(monkeypatch,records,len(bars)*300)
    strategy = base_definition()
    fvg = {**fvg_spec(),"settings":{"mitigation":"wick"}}
    spec = {"fvg":fvg,"ob":ORDER_BLOCK,"pool":LIQUIDITY}[case]
    strategy["indicators"] = [STRUCTURE,spec]
    strategy["instruments"]["primary"]["modeled_delay_seconds"] = delay
    for phase in ("context","setup","trigger","invalidation"):
        strategy[phase] = group(phase,condition(phase+"-pivot","STRUCTURE_PIVOT","structure","HIGH"))
    run, observed = evaluate_case(client,archive,strategy,start=1800 if delay else 1500,cutoff=archive["end"]-BASE)
    view = observed.views[case]
    category = {"fvg":"fvg","ob":"order_block","pool":"pool"}[case]
    events = observed.events[view.snapshot][category][1]
    revision = next(item for item in events if item["state"]==terminal)
    identity = view.records[revision["id"]].family
    activation = next(item for item in events if item["state"]=="ACTIVE" and view.records[item["id"]].family==identity)
    ref = activation["evidence_reference"]
    assert view.current_state(ref,activation["available_at"],purpose=SIMULATION_MODE).kind=="ACTIVE_ELIGIBLE"
    state = view.current_state(ref,revision["available_at"],purpose=SIMULATION_MODE)
    assert state.kind==classification and state.revision==revision["id"]
    assert view.current_state(ref,archive["end"]+1,purpose=SIMULATION_MODE).kind=="OUTSIDE_EVALUATED_HORIZON"


@pytest.mark.parametrize("stage",["slots","publications","timeline","scans","serialization"])
def test_evaluator_resource_limits_fail_closed(client,approved_fixture,monkeypatch,stage):
    from research_backend.app import strategy_evaluator
    from test_strategy_evaluator import A

    module, attribute, value = {
        "slots":(sources,"MAX_SELECTION_SLOTS",1),
        "publications":(sources,"MAX_PUBLICATIONS",1),
        "timeline":(sources,"MAX_TIMELINE",1),
        "scans":(strategy_evaluator,"MAX_SCANS",1),
        "serialization":(routes,"MAX_SERIALIZED_DECISION",1),
    }[stage]
    monkeypatch.setattr(module,attribute,value)
    body = request_body(client,approved_fixture,ready_entry(pair=False),A+4800)
    reply = client.post(API,json=body)
    assert reply.status_code==422,reply.text
    assert reply.json()["detail"]["code"]=="RESOURCE_LIMIT"
    assert client.get(API+"/runs",params={"strategy_version_id":body["strategy_version_id"]}).json()==[]

def test_tentative_historical_setup_cannot_migrate_to_new_context(client,monkeypatch):
    archive = two_smt_families(monkeypatch)
    definition = continuous_pair()
    definition["context"]["children"][0]["selection"] = "LATEST_ELIGIBLE"
    definition["setup"]["children"][0]["alias"] = "tentative_original_role"
    definition["setup"]["children"].append(condition("wait-for-fvg","FVG_STATE","fvg","ACTIVE"))
    early, _ = evaluate_case(client,archive,definition,start=1500,cutoff=1810)
    role = next(rule for rule in early["decision"]["rules"] if rule["id"]=="role")
    assert role["outcome"]=="SATISFIED" and early["decision"]["setup_activated_at"] is None
    assert {binding["alias"] for binding in early["decision"]["attempt_bindings"]}=={"pair_context"}
    pinned = routes.load_run(early["run_id"])
    final = routes.calculate(pinned,BASE+3590)
    assert final["state"]=="INVALIDATED" and final["setup_activated_at"] is None
    assert final["attempt_bindings"][0]["family"]==role["family"]
    assert final["entry_intent"] is None


def test_saved_run_and_append_limits_are_atomic(client,approved_fixture,monkeypatch):
    from test_strategy_evaluator import A

    body = request_body(client,approved_fixture,ready_entry(pair=False),A+4200)
    run = client.post(API,json=body).json()
    monkeypatch.setattr(routes,"MAX_DECISIONS",1)
    result = client.post(API+"/"+run["run_id"]+"/advance",json={
        "cutoff":A+4800,"expected_decision_id":run["decision"]["decision_id"]})
    assert result.status_code==422 and result.json()["detail"]["code"]=="RESOURCE_LIMIT"
    assert len(client.get(API+"/"+run["run_id"]+"/decisions").json())==1
    monkeypatch.setattr(routes,"MAX_RUNS",1)
    result = client.post(API,json={**body,"evaluation_start":A+600})
    assert result.status_code==422 and result.json()["detail"]["code"]=="RESOURCE_LIMIT"


def test_concurrent_evaluation_admission_limit(client,approved_fixture):
    from test_strategy_evaluator import A

    body = request_body(client,approved_fixture,ready_entry(pair=False),A+4800)
    assert routes.LANES.acquire(blocking=False)
    assert routes.LANES.acquire(blocking=False)
    try:
        assert client.post(API,json=body).status_code==429
    finally:
        routes.LANES.release()
        routes.LANES.release()
    assert client.post(API,json=body).status_code==201

def test_order_block_unknown_cannot_satisfy_negation(client,monkeypatch):
    from test_order_blocks import BARS

    bars = prices([10,10,10,12,10])+BARS[:3]+[(10,12,9,10),BARS[3],(13,14,11,13)]+prices([11,11])
    records = []
    frozen_candles(records,"EURUSD","M5",BASE,bars)
    archive = selection(monkeypatch,records,len(bars)*300)
    definition = base_definition()
    definition["indicators"] = [STRUCTURE,ORDER_BLOCK]
    definition["instruments"]["primary"]["modeled_delay_seconds"] = 20
    active = condition("ob-active","ORDER_BLOCK_ACTIVE","ob","ACTIVE",current=True)
    definition["context"] = group("context",active)
    definition["context"].update(operator="NOT",unknown_policy="PROPAGATE")
    for phase in ("setup","trigger","invalidation"):
        definition[phase] = group(phase,condition(phase+"-pivot","STRUCTURE_PIVOT","structure","HIGH"))
    run, horizon = evaluate_case(client,archive,definition,start=3300,cutoff=3320)
    view = horizon.views["ob"]
    assert any(row["state"]=="UNCERTAIN" and row["available_at"]==BASE+3320
               for row in horizon.events[view.snapshot]["order_block"][1])
    decision = run["decision"]
    print({key:decision[key] for key in ("state","context_activated_at","phase_results","rules")})
    assert decision["context_activated_at"] is None
    assert decision["phase_results"]["context"]=="UNKNOWN"


def test_age_boundary_is_observed_without_producer_publication(client,monkeypatch):
    archive = two_smt_families(monkeypatch)
    definition = continuous_pair()
    definition["context"]["children"][0]["evidence"]["max_age_seconds"] = 75
    run, horizon = evaluate_case(client,archive,definition,start=1500,cutoff=1890)
    assert not any(BASE+1876 in stamps for lanes in horizon.events.values() for stamps,_ in lanes.values())
    assert run["decision"]["transitions"][-1] == [BASE+1876,"INSUFFICIENT_EVIDENCE","CONTINUOUS_CONTEXT_FAILED"]
    assert next(rule for rule in run["decision"]["rules"] if rule["id"]=="context-smt")["reason"]=="BOUND_EVIDENCE_STALE"
