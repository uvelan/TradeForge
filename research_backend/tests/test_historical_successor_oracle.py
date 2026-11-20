"""Independent successor schedule and exact archive-prefix attestations."""
from copy import deepcopy

import pytest

from research_backend.app import historical_strategy_engine as engine
from research_backend.app import storage
from research_backend.app.strategy_evaluation_sources import collect
from research_backend.app.strategy_evaluator import EvaluationMachine, TERMINAL
from test_historical_strategy_runs import execute, prepared
from test_strategy_evaluator import BASE


def expected_opportunities(run,model):
    """Replay each attempt from scratch on separately collected M5 prefixes."""
    identity = run["identity"]
    begin,end = identity["evaluation_start"],identity["cutoff"]
    # This canonical fixture uses zero delay, no in-window age or expiry
    # deadlines, and eligible new context only on M5 closes.
    closes = list(range(begin,end+1,300))
    pending = set(closes)
    history = []
    last_terminal = begin-1
    used = set()
    while pending:
        cutoff = min(pending)
        pending.remove(cutoff)
        prefix = collect(model,run["selection"],cutoff,begin)
        machine = EvaluationMachine(model,prefix,identity,last_terminal+1,end,
                                    rearm_rule=run["rearm_rule"] if used else None,
                                    rearm_after=last_terminal if used else None,spent_families=used)
        for stamp in [point for point in history if point>last_terminal]+[cutoff]:
            machine.at(stamp)
            machine.expire_during_uncertainty(stamp)
        history.append(cutoff)
        if machine.setup_at==cutoff and cutoff<end:
            pending.add(cutoff+1)
        if machine.state in TERMINAL and machine.attempt:
            decision = machine.decision(cutoff,0)
            family = next(row["family"] for row in decision["attempt_bindings"] if row["rule"]==run["rearm_rule"])
            assert family not in used
            yield cutoff,family,decision
            used.add(family)
            last_terminal = cutoff
        elif cutoff==end:
            yield cutoff,None,machine.decision(cutoff,0)


@pytest.mark.parametrize("mirrored",[False,True])
def test_successor_ledger_matches_independent_asof_decisions(prepared,mirrored):
    _,run,model,full = prepared(mirrored)
    expected = list(expected_opportunities(run,model))
    assert [entry[2]["state"] for entry in expected][:2]==["INVALIDATED","TRIGGER_QUALIFIED"]
    assert len({entry[1] for entry in expected if entry[1]})>=3
    combined,snapshots = execute(run,model,full,3)
    for chunk_size in (1,4,32):
        assert execute(run,model,full,chunk_size)[0]==combined
    assert len(combined)==len(expected)
    for item,(cutoff,family,decision) in zip(combined,expected):
        actual = item["trace"]
        assert (actual["cutoff"],actual["state"],actual["attempt_id"])==(cutoff,decision["state"],decision["attempt_id"])
        if family:
            assert family in {binding["family"] for binding in actual["attempt_bindings"]}
        assert actual["rules"]==decision["rules"]
        assert actual["activation_proofs"]==decision["activation_proofs"]
        assert actual["supporting_events"]==decision["supporting_events"]
        assert bool(item["intent"])==bool(decision["entry_intent"])
        if item["intent"]:
            for key,value in decision["entry_intent"].items():
                assert item["intent"][key]==value
    for prior in snapshots[:-1]:
        resumed = []
        engine.run_chunks(run,full,model,lambda snap,frontier,batch,*_:resumed.extend(batch),saved=prior,chunk_size=5)
        assert resumed==[row for row in combined if row["trace"]["cutoff"]>prior["frontier"]]


def test_exact_prefix_survives_noncontributing_partition(prepared):
    _,run,model,_ = prepared(split=12)
    cutoff = BASE+3600
    full = run["selection"]
    pinned = full["series"]["PRIMARY:M5"]["partitions"]
    assert len(pinned)==2
    reference = deepcopy(full)
    reference["series"]["PRIMARY:M5"]["partitions"] = pinned[:1]
    prefix = engine.asof_selection(full,cutoff)
    assert prefix==reference
    a = collect(model,reference,cutoff,run["identity"]["evaluation_start"])
    b = collect(model,prefix,cutoff,run["identity"]["evaluation_start"])
    assert a.events==b.events and a.views["fvg"].calculation_id==b.views["fvg"].calculation_id
    assert engine.make_machine(run,a,model).run(cutoff)==engine.make_machine(run,b,model).run(cutoff)
    after = collect(model,engine.asof_selection(full,cutoff+300),cutoff+300,run["identity"]["evaluation_start"])
    assert after.views["fvg"].calculation_id!=b.views["fvg"].calculation_id
    for field,value in (("feed_id","other-feed"),("delay_seconds",1)):
        changed = deepcopy(reference)
        changed["series"]["PRIMARY:M5"][field] = value
        if field=="feed_id":
            with pytest.raises(ValueError,match="scope"):
                collect(model,changed,cutoff,run["identity"]["evaluation_start"])
        else:
            modified = collect(model,changed,cutoff,run["identity"]["evaluation_start"])
            assert modified.views["fvg"].calculation_id!=b.views["fvg"].calculation_id
            assert modified.coverage["PRIMARY:M5"].observed!=b.coverage["PRIMARY:M5"].observed
    alternate_definition = model.model_copy(update={"indicators":[model.indicators[0].model_copy(
        update={"settings":{**model.indicators[0].settings,"mitigation":"wick"}})]})
    alternate = collect(alternate_definition,reference,cutoff,run["identity"]["evaluation_start"])
    assert alternate.views["fvg"].calculation_id!=b.views["fvg"].calculation_id
    from research_backend.app.db import one
    path = storage.MARKET_DIR / one("SELECT relative_path FROM datasets WHERE id=?",(pinned[0]["id"],))["relative_path"]
    with path.open("ab") as stream:
        stream.write(b"changed")
    with pytest.raises(ValueError,match="failed verification"):
        collect(model,prefix,cutoff,run["identity"]["evaluation_start"])


def test_rearm_rejects_same_family_and_equal_cutoff(client, monkeypatch):
    from research_backend.app.historical_strategy_inputs import HistoricalInput, prepare_historical
    from research_backend.app.strategy_definitions import StrategyDefinitionV2
    from test_strategy_definitions import group
    from test_strategy_evaluator import request_body
    from test_strategy_evaluator_closure import fvg_strategy
    from test_strategy_evaluator_integrity import condition, with_fvg_interaction

    archive = with_fvg_interaction(monkeypatch)
    definition = fvg_strategy()
    definition["trigger"] = group("trigger",condition("unbound","FVG_STATE","fvg","PARTIALLY_MITIGATED"))
    request = request_body(client,archive,definition,BASE+3600)
    request.update(rearm="NEW_CONTEXT_FAMILY",max_attempts=3,context_alias="bound_gap",evaluation_start=BASE+1800)
    run = prepare_historical(HistoricalInput.model_validate(request))
    model = StrategyDefinitionV2.model_validate(run["definition"])
    formation, revision = BASE+3000, BASE+3300
    evidence = collect(model,run["selection"],revision,request["evaluation_start"])
    def select(definition, after, at, spent=()):
        machine = engine.make_machine(run,evidence,definition,after,spent)
        rule = machine.graph.rules[run["rearm_rule"]]
        return machine.select(rule,"context",at,after+1)

    assert select(model,formation,formation).proof is None
    later = select(model,formation-1,formation)
    assert later.outcome.value=="SATISFIED"
    family = later.proof.family
    # Probe the engine's spent-family defence with a positive current revision
    # predicate that is deliberately excluded from the public rearm contract.
    rule = model.context.children[0].model_copy(update={"expected":"PARTIALLY_MITIGATED"})
    revised = model.model_copy(update={"context":model.context.model_copy(update={"children":[rule]})})
    unspent = select(revised,formation,revision)
    assert unspent.outcome.value=="SATISFIED"
    assert unspent.proof.family==family and unspent.proof.reference.available_at==revision
    spent = select(revised,formation,revision,[family])
    print("shared selector FVG control", "family",family,"candidate",unspent.proof.reference.event_id,
          "available",revision,"terminal",formation,"cutoff",revision,
          "unspent",unspent.outcome.value,"spent",spent.outcome.value,"spent_has_proof",spent.proof is not None)
    assert spent.proof is None, "Spent membership must exclude the same otherwise eligible post-terminal revision"
