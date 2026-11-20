"""Canonical negated CURRENT regression through the normal evaluation API."""
import json

import pytest

from research_backend.app.strategy_evaluation_routes import load_run
from test_strategy_definitions import group
from test_strategy_evaluator import BASE
from test_strategy_evaluator_closure import fvg_strategy
from test_strategy_evaluator_integrity import condition, evaluate_case, with_fvg_interaction


def negated_definition(phase, *, reverse=False, prohibited="PARTIALLY_MITIGATED"):
    definition = fvg_strategy()
    definition["context"] = group("context",condition("formed","FVG_STATE","fvg","ACTIVE",alias="bound_gap"))
    definition["setup"] = group("setup",condition("established","FVG_STATE","fvg","ACTIVE"))
    negative = group("negation",condition("prohibited","FVG_ACTIVE","fvg",prohibited,current=True),operator="NOT",unknown_policy="PROPAGATE")
    definition[phase]["children"].append(negative)
    if reverse:
        definition[phase]["children"].reverse()
    definition["trigger"] = group("trigger",condition("mitigated","FVG_STATE","fvg","PARTIALLY_MITIGATED"))
    return definition


@pytest.mark.parametrize("phase",["context","setup"])
@pytest.mark.parametrize("reverse",[False,True])
def test_negated_current_blocks_trigger_on_partial_mitigation(client,monkeypatch,phase,reverse):
    archive = with_fvg_interaction(monkeypatch)
    definition = negated_definition(phase,reverse=reverse)
    run,evidence = evaluate_case(client,archive,definition,start=1800,cutoff=3300)
    decision = run["decision"]
    view = evidence.views["fvg"]
    _,events = evidence.events[view.snapshot]["fvg"]
    revisions = [{"state":row["state"],"family":row["fvg_id"],"reference":row["evidence_reference"]}
                 for row in events if row["available_at"] in {BASE+3000,BASE+3300}]
    leaf = next(row for row in decision["rules"] if row["id"]=="prohibited")
    print("NEGATED_CURRENT "+json.dumps({"phase":phase,"reversed":reverse,
        "admitted_definition":load_run(run["run_id"])["definition"],
        "cutoff":decision["cutoff"],"revisions":revisions,
        "activation":decision.get("activation_outcomes"),
        "rule":leaf,"groups":decision["phase_results"],"decision":decision["state"],
        "intents":int(decision["entry_intent"] is not None)},sort_keys=True))
    assert decision["context_activated_at"]==decision["setup_activated_at"]==BASE+3000
    assert decision["state"]=="INVALIDATED" and decision["entry_intent"] is None
    assert decision["phase_results"][phase]=="NOT_SATISFIED"
    assert leaf["outcome"]=="SATISFIED" and leaf["available_at"]==BASE+3300
    assert leaf["evidence_reference"]==revisions[-1]["reference"]


@pytest.mark.parametrize("phase",["context","setup"])
def test_negated_current_absence_allows_independent_formation_trigger(client,monkeypatch,phase):
    from test_strategy_evaluator import frozen_candles
    from test_strategy_evaluator_integrity import selection

    rows = []
    bars = [(99,100,97,99)]*8+[(100,108,100,108),(106,110,105,109),(112,114,112,113)]
    frozen_candles(rows,"EURUSD","M5",BASE,bars)
    archive = selection(monkeypatch,rows,3300)
    definition = negated_definition(phase)
    definition["trigger"] = group("trigger",condition("new-gap","FVG_STATE","fvg","ACTIVE"))
    run,_ = evaluate_case(client,archive,definition,start=1800,cutoff=3300)
    decision = run["decision"]
    assert decision["state"]=="TRIGGER_QUALIFIED" and decision["entry_intent"]
    assert decision["phase_results"][phase]=="SATISFIED"
    leaf = next(row for row in decision["rules"] if row["id"]=="prohibited")
    assert (leaf["outcome"],leaf["reason"],leaf["verified_at"])==(
        "NOT_SATISFIED","NO_MATCH_COMPLETE_COVERAGE",BASE+3300)
    initial, = decision["activation_outcomes"]
    assert initial["outcome"]=="NOT_SATISFIED" and initial["phase_activated_at"]==BASE+3000


@pytest.mark.parametrize("phase",["context","setup"])
def test_negated_current_preserves_unknown_for_straddling_observation(client,monkeypatch,phase):
    archive = with_fvg_interaction(monkeypatch)
    definition = negated_definition(phase,reverse=True)
    definition["instruments"]["primary"]["modeled_delay_seconds"] = 20
    run,evidence = evaluate_case(client,archive,definition,start=1800,cutoff=3320)
    decision = run["decision"]
    view = evidence.views["fvg"]
    assert any(row["state"]=="UNCERTAIN" and row["available_at"]==BASE+3320
               for row in evidence.events[view.snapshot]["fvg"][1])
    assert decision["state"]=="INSUFFICIENT_EVIDENCE" and decision["entry_intent"] is None
    assert decision["phase_results"][phase]=="UNKNOWN"
    leaf = next(row for row in decision["rules"] if row["id"]=="prohibited")
    assert (leaf["outcome"],leaf["reason"],leaf["verified_at"])==(
        "UNKNOWN","STRADDLING_OBSERVATION",BASE+3320)
    initial, = decision["activation_outcomes"]
    assert initial["outcome"]=="NOT_SATISFIED" and initial["phase_activated_at"]==BASE+3020


@pytest.mark.parametrize("phase",["context","setup"])
def test_historical_negation_remains_latched(client,monkeypatch,phase):
    archive = with_fvg_interaction(monkeypatch)
    definition = negated_definition(phase)
    negative = next(row for row in definition[phase]["children"] if row["id"]=="negation")
    negative["children"] = [condition("prohibited","FVG_STATE","fvg","PARTIALLY_MITIGATED")]
    run,_ = evaluate_case(client,archive,definition,start=1800,cutoff=3300)
    assert run["decision"]["state"]=="TRIGGER_QUALIFIED"


@pytest.mark.parametrize("phase",["context","setup"])
def test_negated_current_historical_single_and_resumed_successor_agree(client,monkeypatch,phase):
    from research_backend.app import historical_strategy_engine as engine
    from research_backend.app import historical_strategy_store as store
    from research_backend.app.historical_strategy_inputs import HistoricalInput, prepare_historical, verified_evidence
    from research_backend.app.strategy_definitions import StrategyDefinitionV2
    from test_historical_strategy_runs import API, execute
    from test_strategy_evaluator import frozen_candles, request_body
    from test_strategy_evaluator_integrity import selection

    monkeypatch.setattr("research_backend.app.historical_strategy_worker.claim",lambda _:None)
    rows = []
    frozen_candles(rows,"EURUSD","M5",BASE,[(99,100,97,99)]*8)
    frozen_candles(rows,"EURUSD","M5",BASE+2400,[(100,108,100,108),(106,110,105,109),
                                               (105,106,103,104),(104,105,103,104)])
    rows[0]["partitions"].extend(rows.pop()["partitions"])
    archive = selection(monkeypatch,rows,3600)
    definition = negated_definition(phase)
    normal,_ = evaluate_case(client,archive,definition,start=1800,cutoff=3300)
    start_request = request_body(client,archive,definition,BASE+3300)
    start_request["evaluation_start"] = BASE+1800

    def prepare(request):
        queued = client.post(API,json=request)
        assert queued.status_code==202,queued.text
        run = prepare_historical(HistoricalInput.model_validate(request))
        assert queued.json()["run_id"]==run["run_id"]
        model = StrategyDefinitionV2.model_validate(run["definition"])
        return run,model,verified_evidence(model,run)

    single,single_model,single_evidence = prepare(start_request)
    assert store.claim("single")==single["run_id"]
    engine.run_chunks(single,single_evidence,single_model,
                      lambda *args:store.publish(single["run_id"],"single",*args))
    resumed,model,evidence = prepare({**start_request,"cutoff":BASE+3600,"rearm":"NEW_CONTEXT_FAMILY",
                                      "context_alias":"bound_gap","max_attempts":3})
    identifier = resumed["run_id"]
    expected,_ = execute(resumed,model,evidence,3)
    assert store.claim("first")==identifier
    def interrupt(snapshot,frontier,batch,completed,metrics):
        store.publish(identifier,"first",snapshot,frontier,batch,completed,metrics)
        if frontier==BASE+3000:
            raise RuntimeError("interrupted before contradiction")
    with pytest.raises(RuntimeError,match="before contradiction"):
        engine.run_chunks(resumed,evidence,model,interrupt,chunk_size=1)
    store.finish(identifier,"first","FAILED","test interruption")
    assert client.get(f"{API}/{identifier}/decisions").json()==[]
    assert client.post(f"{API}/{identifier}/resume").status_code==200
    assert store.claim("resumed")==identifier
    engine.run_chunks(resumed,evidence,model,lambda *args:store.publish(identifier,"resumed",*args),
                      saved=store.latest_checkpoint(identifier),chunk_size=4)
    expected_decision = next(row for row in expected if row["trace"]["cutoff"]==BASE+3300)
    single_trace = client.get(f"{API}/{single['run_id']}/decisions/{BASE+3300}").json()
    resumed_trace = client.get(f"{API}/{identifier}/decisions/{BASE+3300}").json()
    assert resumed_trace["decision_id"]==expected_decision["id"]
    assert resumed_trace["supporting_events"]==expected_decision["trace"]["supporting_events"]
    assert client.get(f"{API}/{identifier}/intents").json()==[]
    assert client.get(f"{API}/{single['run_id']}/intents").json()==[]
    for trace in (single_trace,resumed_trace):
        assert trace["state"]==normal["decision"]["state"]=="INVALIDATED"
        assert trace["entry_intent"] is None
        for field in ("rules","activation_outcomes","phase_results","supporting_events"):
            assert trace[field]==normal["decision"][field]


@pytest.mark.parametrize("phase",["context","setup"])
def test_missing_due_candle_makes_latched_negation_unknown(client,monkeypatch,phase):
    from test_strategy_evaluator import frozen_candles
    from test_strategy_evaluator_integrity import selection

    rows = []
    bars = [(99,100,97,99)]*8+[(100,108,100,108),(106,110,105,109)]
    frozen_candles(rows,"EURUSD","M5",BASE,bars)
    archive = selection(monkeypatch,rows,3300)
    run,_ = evaluate_case(client,archive,negated_definition(phase),start=1800,cutoff=3300)
    decision = run["decision"]
    assert decision["state"]=="INSUFFICIENT_EVIDENCE" and decision["entry_intent"] is None
    assert decision["phase_results"][phase]=="UNKNOWN"
    leaf = next(row for row in decision["rules"] if row["id"]=="prohibited")
    assert (leaf["outcome"],leaf["reason"],leaf["verified_at"])==(
        "UNKNOWN","REQUIRED_SOURCE_COVERAGE_UNKNOWN",BASE+3300)
    assert decision["activation_outcomes"][0]["outcome"]=="NOT_SATISFIED"
