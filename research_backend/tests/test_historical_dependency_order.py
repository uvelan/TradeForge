"""Regression for current verification across one phase's causal edges."""
from copy import deepcopy

from test_strategy_definitions import group
from test_strategy_evaluator import BASE, frozen_candles
from test_strategy_evaluator_integrity import (
    base_definition, condition, evaluate_case, fvg_spec, selection,
)


def test_current_dependencies_follow_graph_order(client, monkeypatch):
    records = []
    bars = [(99,100,97,99)]*8+[(100,108,100,108),(106,110,105,109),
            (105,108,102,104),(114,116,112,115),(114,117,101,114),
            (114,117,112,114),(114,117,112,114)]
    frozen_candles(records,"EURUSD","M5",BASE,bars)
    archive = selection(monkeypatch,records,4500)
    definition = base_definition()
    definition["indicators"] = [fvg_spec(),{**fvg_spec(),"id":"wick","settings":{"mitigation":"wick"}}]
    early = condition("early","FVG_ACTIVE","wick","PARTIALLY_MITIGATED",current=True,alias="early_gap")
    later = condition("later","FVG_ACTIVE","fvg","ACTIVE",current=True,alias="later_gap")
    later["selection"] = "LATEST_ELIGIBLE"
    definition["context"] = group("context",early,later)
    definition["setup"] = group("setup",condition("setup-gap","FVG_STATE","fvg","ACTIVE"))
    definition["invalidation"] = group("invalidation",condition("no-loss","FVG_STATE","fvg","FULLY_MITIGATED"))
    definition["trigger"] = group("trigger",condition("later-mitigation","FVG_STATE","fvg","PARTIALLY_MITIGATED"))
    definition["temporal"] = [{"first":"early","then":"later","relation":"AFTER"}]
    reversed_definition = deepcopy(definition)
    reversed_definition["context"]["children"].reverse()
    outcomes = []
    for item in (definition,reversed_definition):
        run,_ = evaluate_case(client,archive,item,start=1800,cutoff=3900)
        decision = run["decision"]
        outcomes.append((decision["state"],decision["temporal"],
            [(r["id"],r["outcome"],r["available_at"]) for r in decision["rules"] if r["id"] in {"early","later"}]))
        assert decision["context_activated_at"]==BASE+3600
    print(outcomes)
    assert outcomes[0]==outcomes[1]
    assert outcomes[0][0]=="INVALIDATED"
