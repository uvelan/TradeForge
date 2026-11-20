"""Canonical adversarial regressions for v5 maintained predicates."""
from copy import deepcopy

import pytest

from test_strategy_definitions import group
from test_strategy_evaluator import BASE, frozen_candles
from test_strategy_evaluator_closure import fvg_strategy
from test_strategy_evaluator_integrity import condition, evaluate_case, selection, with_fvg_interaction


@pytest.mark.parametrize("alias", [None, "named_gap"])
@pytest.mark.parametrize("phase", ["context", "setup"])
def test_current_family_is_maintained_with_or_without_alias(client, monkeypatch, alias, phase):
    archive = with_fvg_interaction(monkeypatch)
    definition = fvg_strategy()
    definition["trigger"] = group("trigger",condition("mitigated","FVG_STATE","fvg","PARTIALLY_MITIGATED"))
    definition["context"] = group("context",condition("formed","FVG_STATE","fvg","ACTIVE"))
    definition["setup"] = group("setup",condition("established","FVG_STATE","fvg","ACTIVE"))
    definition[phase] = group(phase,condition("must-stay-active","FVG_ACTIVE","fvg","ACTIVE",current=True,alias=alias))
    run,evidence = evaluate_case(client,archive,definition,start=1800,cutoff=3300)
    decision = run["decision"]
    proof = next(row for row in decision["activation_proofs"] if row["id"]=="must-stay-active")
    latest = next(row for row in decision["rules"] if row["id"]=="must-stay-active")
    view = evidence.views["fvg"]
    _,events = evidence.events[view.snapshot]["fvg"]
    revision = next(row for row in events if row["fvg_id"]==proof["family"] and row["available_at"]==BASE+3300)
    print({"phase":phase,"alias":alias,"decision":decision["state"],"activation":proof["evidence_reference"],
           "latest_event":revision["evidence_reference"],"latest_state":revision["state"],
           "verified_event":latest["evidence_reference"],"intents":int(decision["entry_intent"] is not None)})
    assert proof["available_at"]==BASE+3000
    assert latest["available_at"]==BASE+3300
    assert latest["family"]==proof["family"]
    assert latest["outcome"]=="NOT_SATISFIED"
    assert decision["state"]=="INVALIDATED" and decision["entry_intent"] is None


def test_maintained_any_accepts_alternative_successful_branch(client,monkeypatch):
    archive = with_fvg_interaction(monkeypatch)
    definition = fvg_strategy()
    definition["context"] = {**group("context",
        condition("current","FVG_ACTIVE","fvg","ACTIVE",current=True),
        condition("formation","FVG_STATE","fvg","ACTIVE")),"operator":"ANY"}
    definition["trigger"] = group("trigger",condition("mitigated","FVG_STATE","fvg","PARTIALLY_MITIGATED"))
    for children in (definition["context"]["children"],list(reversed(definition["context"]["children"]))):
        variant = deepcopy(definition)
        variant["context"]["children"] = children
        run,_ = evaluate_case(client,archive,variant,start=1800,cutoff=3300)
        decision = run["decision"]
        assert decision["state"]=="TRIGGER_QUALIFIED" and decision["entry_intent"]
        assert next(row for row in decision["rules"] if row["id"]=="current")["outcome"]=="NOT_SATISFIED"
