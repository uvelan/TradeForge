"""Frozen producer cases for attempt activation and maintained predicates."""
import pytest

from test_strategy_definitions import group
from test_strategy_evaluator import BASE
from test_strategy_evaluator_integrity import (
    base_definition, condition, evaluate_case, fvg_spec, with_fvg_interaction,
)


def fvg_strategy(*, current=True):
    definition = base_definition()
    definition["indicators"] = [fvg_spec()]
    definition["context"] = group("context", condition("gap", "FVG_ACTIVE" if current else "FVG_STATE",
                                                       "fvg", "ACTIVE", current=current, alias="bound_gap"))
    definition["setup"] = group("setup", condition("gap-established", "FVG_STATE", "fvg", "ACTIVE"))
    definition["invalidation"] = group("invalidation", condition("invalid", "FVG_STATE", "fvg", "FULLY_MITIGATED"))
    definition["trigger"] = group("trigger", condition("gap-mitigated", "FVG_STATE", "fvg", "PARTIALLY_MITIGATED",
                                                       bound="bound_gap"))
    return definition


def test_bound_current_fvg_does_not_keep_active_predicate_after_mitigation(client, monkeypatch):
    archive = with_fvg_interaction(monkeypatch)
    run, evidence = evaluate_case(client, archive, fvg_strategy(), start=1800, cutoff=3300)
    observed = evidence.events[evidence.views["fvg"].snapshot]["fvg"][1]
    root = next(item for item in observed if item["state"] == "ACTIVE")
    latest = next(item for item in observed if item["fvg_id"] == root["fvg_id"] and item["state"] == "PARTIALLY_MITIGATED")
    assert (root["available_at"], root["lower"], root["upper"]) == (BASE+3000, 100, 105)
    assert (latest["available_at"], latest["observed_price"]) == (BASE+3300, 104)
    view = evidence.views["fvg"]
    assert view.current_state(root["evidence_reference"], BASE+3300, purpose="HISTORICAL_SIMULATION").kind == "ACTIVE_ELIGIBLE"
    decision = run["decision"]
    rule = next(item for item in decision["rules"] if item["id"] == "gap")
    print({"activation":root["id"], "latest":latest["id"], "latest_state":latest["state"],
           "rule_outcome":rule["outcome"], "rule_reference":rule["evidence_reference"],
           "result":decision["state"], "intent":bool(decision["entry_intent"])})
    assert rule["outcome"] == "NOT_SATISFIED"
    assert decision["entry_intent"] is None

def rule_result(decision, identifier):
    return next(item for item in decision["rules"] if item["id"] == identifier)


def compare_cutoffs(run, horizon, offsets, end, begin):
    from research_backend.app import strategy_evaluation_routes as routes
    from research_backend.app.strategy_definitions import StrategyDefinitionV2
    from research_backend.app.strategy_evaluator import EvaluationMachine
    from test_strategy_evaluator_integrity import semantic_prefix

    pinned = routes.load_run(run["run_id"])
    definition = StrategyDefinitionV2.model_validate(pinned["definition"])
    for offset in offsets:
        cutoff = BASE+offset
        expected = routes.calculate(pinned,cutoff)
        compared = EvaluationMachine(definition,horizon,pinned["identity"],BASE+begin,end).run(cutoff)
        assert semantic_prefix(compared) == semantic_prefix(expected),offset
        yield expected


@pytest.mark.parametrize("current", [True,False])
def test_fvg_activation_proof_and_latest_predicate_are_distinct(client,monkeypatch,current):
    archive = with_fvg_interaction(monkeypatch)
    run,horizon = evaluate_case(client,archive,fvg_strategy(current=current),start=1800,cutoff=3600)
    before,after = compare_cutoffs(run,horizon,(3299,3300),archive["end"],1800)
    start,latest = (rule_result(decision,"gap") for decision in (before,after))
    bound, = after["attempt_bindings"]
    assert start["outcome"]=="SATISFIED"
    assert {key:value for key,value in bound["reference"].items() if key!="calculation_snapshot"}=={
        key:value for key,value in start["evidence_reference"].items() if key!="calculation_snapshot"}
    assert bound["family"]==latest["family"] and bound["activated_at"]==BASE+3000
    assert next(item for item in after["activation_proofs"] if item["id"]=="gap")["evidence_reference"]==bound["reference"]
    if current:
        assert (latest["outcome"],latest["available_at"],latest["verified_at"])==("NOT_SATISFIED",BASE+3300,BASE+3300)
        assert bound["validity"]=="ACTIVE_ELIGIBLE"
        assert latest["evidence_reference"]!=bound["reference"]
        assert after["entry_intent"] is None
    else:
        assert latest["evidence_reference"]==bound["reference"]
        assert latest["verified_at"] is None
        assert after["entry_intent"]["qualified_at"]==BASE+3300


def test_actual_binding_time_can_follow_individual_selection(client,monkeypatch):
    archive = with_fvg_interaction(monkeypatch)
    definition = fvg_strategy(current=False)
    definition["context"]["children"].append(condition("gap-evolved","FVG_STATE","fvg","PARTIALLY_MITIGATED",
                                                       bound="bound_gap"))
    # Same-phase aliases are only valid positive predecessors. Use the same
    # producer but let the independent condition wait for mitigation.
    definition["context"]["children"][-1].update(selection="EARLIEST_ELIGIBLE",same_family_as=None)
    run,_ = evaluate_case(client,archive,definition,start=1800,cutoff=3300)
    decision = run["decision"]
    bound, = decision["attempt_bindings"]
    assert bound["selected_at"]==BASE+3000
    assert bound["activated_at"]==decision["context_activated_at"]==BASE+3300


@pytest.mark.parametrize("expected,activation,change", [("ACTIVE",2700,3000),("TOUCHED",3000,3300)])
def test_maintained_order_block_reads_exact_lifecycle_revision(client,monkeypatch,expected,activation,change):
    from test_order_blocks import BARS
    from test_strategy_evaluator import frozen_candles
    from test_strategy_evaluator_integrity import ORDER_BLOCK, STRUCTURE, prices, selection

    bars = prices([10,10,10,12,10])+BARS+[(13,14,12,13),(13,14,11.25,13),(13,14,10.5,13)]
    records = []
    frozen_candles(records,"EURUSD","M5",BASE,bars)
    archive = selection(monkeypatch,records,len(bars)*300)
    definition = base_definition()
    definition["indicators"] = [STRUCTURE,ORDER_BLOCK]
    definition["context"] = group("context",condition("block","ORDER_BLOCK_ACTIVE","ob",expected,current=True,alias="selected_ob"))
    definition["setup"] = group("setup",condition("initial","ORDER_BLOCK_STATE","ob",expected,bound="selected_ob"))
    definition["trigger"] = group("trigger",condition("penetration","ORDER_BLOCK_STATE","ob","PARTIALLY_MITIGATED",bound="selected_ob"))
    definition["invalidation"] = group("invalidation",condition("never","STRUCTURE_BREAK_PRICE","structure",1000,op="GT"))
    run,horizon = evaluate_case(client,archive,definition,start=1500,cutoff=3600)
    before,after = compare_cutoffs(run,horizon,(change-1,change),archive["end"],1500)
    assert before["context_activated_at"]==BASE+activation
    signal = rule_result(after,"block")
    original, = after["attempt_bindings"]
    assert original["family"]==signal["family"]
    assert original["validity"]=="ACTIVE_ELIGIBLE"
    assert signal["outcome"]=="NOT_SATISFIED"
    assert signal["available_at"]==BASE+change
    event = after["supporting_events"][signal["evidence_reference"]["calculation_snapshot"]+":"+
                                       signal["evidence_reference"]["event_id"]]
    assert event["state"]==("TOUCHED" if expected=="ACTIVE" else "PARTIALLY_MITIGATED")
    assert after["entry_intent"] is None

def test_continuous_non_alias_does_not_replace_latched_temporal_predecessor(client,monkeypatch):
    from test_smt_causal import LEFT, RIGHT
    from test_strategy_evaluator import frozen_candles
    from test_strategy_evaluator_integrity import continuous_pair, selection

    records = []
    for symbol,values in (
        ("EURUSD",[11]*19+LEFT+[11]*5+RIGHT+[24]*33),
        ("GBPUSD",[24]*19+RIGHT+[32,24,24,24,24]+LEFT+[11]*33),
    ):
        frozen_candles(records,symbol,"M1",BASE,[(v,v+1,v-1,v) for v in values])
    bars = [(99,100,97,99)]*4+[(100,108,100,108),(106,110,105,109),
            (107,110,106,108),(107,110,105,108),(105,110,104,108),
            (114,116,113,115),(114,116,113,115),(114,116,113,115)]
    frozen_candles(records,"EURUSD","M5",BASE,bars)
    archive = selection(monkeypatch,records,3600)
    definition = continuous_pair()
    definition["context"]["children"][0].update(alias=None,selection="LATEST_ELIGIBLE")
    definition["setup"] = group("setup",condition("setup-gap","FVG_STATE","fvg","ACTIVE"))
    definition["temporal"] = [
        {"first":"context-smt","then":"setup-gap","relation":"AFTER"},
        {"first":"context-smt","then":"later-trigger","relation":"WITHIN_AFTER",
         "within":{"unit":"SECONDS","count":1000}},
    ]
    run,horizon = evaluate_case(client,archive,definition,start=1500,cutoff=3300)
    earlier,later = compare_cutoffs(run,horizon,(1800,3000),archive["end"],1500)
    assert earlier["setup_activated_at"]==later["setup_activated_at"]==BASE+1800
    assert rule_result(later,"context-smt")["available_at"]>BASE+2400
    anchor, = later["temporal_predecessors"]
    assert anchor["reference"]["event_id"]==rule_result(earlier,"context-smt")["evidence_reference"]["event_id"]
    assert anchor["reference"]["available_at"]==BASE+1740
    assert later["entry_intent"] is None
    assert rule_result(later,"later-trigger")["reason"]=="CAUSAL_WINDOW_NOT_MET"


@pytest.mark.parametrize("mode", ["partial","terminal","uncertain"])
def test_bound_family_progression_policy(client,monkeypatch,mode):
    from test_strategy_evaluator import frozen_candles
    from test_strategy_evaluator_integrity import selection

    bars = [(99,100,97,99)]*8+[(100,108,100,108),(106,110,105,109),
            (105,108,98,99) if mode=="terminal" else (105,106,103,104),(104,105,102,103)]
    records = []
    frozen_candles(records,"EURUSD","M5",BASE,bars)
    archive = selection(monkeypatch,records,3600)
    definition = fvg_strategy()
    if mode=="partial":
        definition["context"]["children"][0]["expected"]="PARTIALLY_MITIGATED"
        definition["setup"]["children"][0]["expected"]="PARTIALLY_MITIGATED"
    if mode=="uncertain":
        definition["instruments"]["primary"]["modeled_delay_seconds"] = 20
    cutoff = 3600 if mode=="partial" else 3400
    run,horizon = evaluate_case(client,archive,definition,start=1800,cutoff=cutoff)
    decision, = compare_cutoffs(run,horizon,(cutoff,),archive["end"],1800)
    outcome = rule_result(decision,"gap")
    binding, = decision["attempt_bindings"]
    if mode=="partial":
        assert outcome["outcome"]=="SATISFIED"
        assert (binding["activated_at"],outcome["available_at"])==(BASE+3300,BASE+3600)
        assert outcome["evidence_reference"]!=binding["reference"]
        assert decision["entry_intent"]["qualified_at"]==BASE+3600
    elif mode=="terminal":
        assert binding["validity"]=="DEFINITIVELY_INACTIVE"
        assert outcome["outcome"]=="NOT_SATISFIED"
        assert decision["entry_intent"] is None
    else:
        assert binding["validity"]=="UNKNOWN"
        assert outcome["outcome"]=="UNKNOWN"
        assert outcome["reason"]=="STRADDLING_OBSERVATION"
        assert decision["entry_intent"] is None
