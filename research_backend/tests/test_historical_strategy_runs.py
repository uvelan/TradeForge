"""Verified producer, replay and worker fencing regressions."""
import json
from copy import deepcopy

import pytest

from research_backend.app import historical_strategy_engine as engine
from research_backend.app import historical_strategy_store as store
from research_backend.app.historical_strategy_inputs import HistoricalInput, prepare_historical, verified_evidence
from research_backend.app.strategy_definitions import StrategyDefinitionV2
from research_backend.app.strategy_evaluation_sources import collect
from test_strategy_definitions import group
from test_strategy_evaluator import BASE, frozen_candles, request_body
from test_strategy_evaluator_closure import fvg_strategy
from test_strategy_evaluator_integrity import condition, selection

API = "/api/historical-strategy-runs/v1"


@pytest.fixture
def prepared(client,monkeypatch):
    monkeypatch.setattr("research_backend.app.historical_strategy_worker.claim",lambda owner:None)
    # Intentionally lead with a mitigated/invalidated range, followed by
    # separately formed gaps that can qualify and rearm.
    def create(mirrored=False,rearm="NEW_CONTEXT_FAMILY",extra_bars=0):
        records = []
        bars = [(99,100,97,99)]*8+[
            (100,108,100,108),(106,110,105,109),(105,108,102,104),
            (103,105,101,103),(105,112,104,110),(111,115,110,113),
            (113,119,113,118),(119,124,120,123),(123,126,120,125),
            (124,126,121,125),(124,126,121,125),
        ]
        # Keep all body prices inside their corresponding wick extremes.
        bars[15] = (121,124,120,123)
        bars.extend([(124,126,121,125)]*extra_bars)
        frozen_candles(records,"EURUSD","M5",BASE,bars,mirrored)
        archive = selection(monkeypatch,records,len(bars)*300)
        monkeypatch.setattr("research_backend.app.historical_strategy_inputs.selection_catalog",lambda:[archive])
        definition = fvg_strategy()
        definition["trigger"] = group("trigger",condition("later-gap","FVG_STATE","fvg","ACTIVE"))
        definition["trade_intent"]["direction"] = "LONG" if mirrored else "SHORT"
        request = request_body(client,archive,definition,archive["end"])
        request.update(evaluation_start=BASE+1800,rearm=rearm,
                       max_attempts=6 if rearm=="NEW_CONTEXT_FAMILY" else 1,
                       context_alias="bound_gap" if rearm=="NEW_CONTEXT_FAMILY" else None)
        run = prepare_historical(HistoricalInput.model_validate(request))
        model = StrategyDefinitionV2.model_validate(run["definition"])
        evidence = verified_evidence(model,run)
        return request,run,model,evidence
    return create


def execute(run,model,evidence,chunk_size):
    emitted, snapshots = [], []

    def publish(snapshot,frontier,entries,completed,metrics):
        snapshots.append({"payload":snapshot[0],"digest":snapshot[1],"frontier":frontier})
        emitted.extend(deepcopy(entries))
    engine.run_chunks(run,evidence,model,publish,chunk_size=chunk_size)
    return emitted,snapshots


@pytest.mark.parametrize("mirrored",[False,True])
def test_bounded_fvg_prefix_rearm_and_chunk_equivalence(prepared,mirrored):
    _,run,model,evidence = prepared(mirrored)
    large,_ = execute(run,model,evidence,100)
    small,checkpoints = execute(run,model,evidence,2)
    assert engine.normalize_prefix(large)==engine.normalize_prefix(small)
    assert [row["trace"]["state"] for row in large][:2]==["INVALIDATED","TRIGGER_QUALIFIED"]
    assert len({row["trace"]["attempt_id"] for row in large})==len(large)
    assert large[1]["intent"]["direction"]==("LONG" if mirrored else "SHORT")
    assert len({row["intent"]["id"] for row in large if row["intent"]})==sum(bool(row["intent"]) for row in large)
    assert len({row["trace"]["attempt_bindings"][0]["family"] for row in large if row["trace"]["attempt_bindings"]})>=3
    for cutoff in sorted(engine.make_machine(run,evidence,model).boundaries(run["identity"]["cutoff"])):
        earlier = collect(model,run["selection"],cutoff,run["identity"]["evaluation_start"])
        assert engine.publication_prefix(earlier,cutoff)==engine.publication_prefix(evidence,cutoff)
        reference = engine.make_machine(run,earlier,model).run(cutoff)
        frozen = engine.make_machine(run,evidence,model).run(cutoff)
        for trace in (reference,frozen):
            trace.pop("resource_counts")
            trace.pop("producers")
            trace["supporting_events"] = {row["evidence_reference"]["event_id"]:row for row in trace["supporting_events"].values()}
        assert engine.normalize_prefix(reference)==engine.normalize_prefix(frozen)
    prior = checkpoints[2]
    suffix = []
    engine.run_chunks(run,evidence,model,lambda args,frontier,entries,*_:suffix.extend(deepcopy(entries)),
                      saved=prior,chunk_size=3)
    assert suffix==[row for row in large if row["trace"]["cutoff"]>prior["frontier"]]
    broken = {**prior,"payload":prior["payload"].replace('"version":1','"version":2')}
    with pytest.raises(ValueError,match="corrupt or incompatible"):
        engine.restore(run,evidence,model,broken)


def test_fenced_atomic_publication_and_resume(prepared):
    _,run,model,evidence = prepared()
    identifier = store.enqueue(run)
    assert store.enqueue(run)==identifier
    assert store.claim("first")==identifier
    assert store.claim("second") is None
    seen = []

    def stop_after_commit(snapshot,frontier,batch,completed,metrics):
        store.publish(identifier,"first",snapshot,frontier,batch,completed,metrics)
        # Identical delivery after commit is idempotent.
        store.publish(identifier,"first",snapshot,frontier,batch,completed,metrics)
        if batch:
            seen.append(frontier)
            raise RuntimeError("interrupted after commit")

    with pytest.raises(RuntimeError,match="interrupted"):
        engine.run_chunks(run,evidence,model,stop_after_commit,chunk_size=1)
    assert store.cancel(identifier)
    old_checkpoint = store.latest_checkpoint(identifier)
    with pytest.raises(store.LeaseLost):
        store.publish(identifier,"first",(old_checkpoint["payload"],old_checkpoint["digest"]),seen[0],[],False,{})
    store.finish(identifier,"first","FAILED","interrupted")
    assert store.status(identifier)["state"]=="CANCELLED"
    assert store.resume(identifier)
    assert store.claim("second")==identifier
    engine.run_chunks(run,evidence,model,lambda *args:store.publish(identifier,"second",*args),
                      saved=old_checkpoint,chunk_size=4)
    assert store.status(identifier)["state"]=="COMPLETED"
    from research_backend.app.db import rows
    intents = rows("SELECT payload FROM historical_strategy_intents WHERE run_id=?",(identifier,))
    assert intents and all(json.loads(row["payload"])["status"]=="AWAITING_EXECUTION_VALIDATION" for row in intents)


def test_reference_single_retains_first_attempt(prepared):
    _,run,model,evidence = prepared(rearm="SINGLE_ATTEMPT")
    entries,_ = execute(run,model,evidence,2)
    reference = engine.make_machine(run,evidence,model).run(run["identity"]["cutoff"])
    assert len(entries)==1 and entries[0]["trace"]["state"]==reference["state"]=="INVALIDATED"
    assert entries[0]["trace"]["attempt_id"]==reference["attempt_id"]


def test_rejected_checkpoint_batch_leaves_no_partial_decision(prepared):
    from research_backend.app.db import one

    _,run,model,evidence = prepared()
    identifier = store.enqueue(run)
    assert store.claim("test-worker")==identifier

    def reject_batch(snapshot,frontier,entries,completed,metrics):
        if not entries:
            return
        bad = deepcopy(entries[0])
        bad["trace"]["cutoff"] = frontier+1
        store.publish(identifier,"test-worker",snapshot,frontier,[entries[0],bad],completed,metrics)

    with pytest.raises(ValueError,match="outside its committed checkpoint interval"):
        engine.run_chunks(run,evidence,model,reject_batch,chunk_size=1)
    assert store.latest_checkpoint(identifier) is None
    assert store.status(identifier)["frontier"] is None
    assert one("SELECT COUNT(*) AS count FROM historical_strategy_decisions WHERE run_id=?",
               (identifier,))["count"]==0


def test_rearm_requires_declared_positive_family_and_admitted_archives(prepared):
    request,run,_,_ = prepared()
    with pytest.raises(ValueError,match="mandatory positive"):
        prepare_historical(HistoricalInput.model_validate({**request,"context_alias":"unknown-alias"}))
    with pytest.raises(ValueError,match="no admitted historical provenance"):
        prepare_historical(HistoricalInput.model_validate({**request,"selection_id":"WORKSPACE",
                                                           "selection_fingerprint":"0"*64,"partitions":["a"*64]}))
    partitions = [part["id"] for lane in run["selection"]["series"].values() for part in lane["partitions"]]
    pinned = prepare_historical(HistoricalInput.model_validate({**request,"selection_id":"WORKSPACE",
                                                               "selection_fingerprint":"0"*64,"partitions":partitions}))
    assert pinned["selection"]["id"]=="WORKSPACE"
    assert len(pinned["selection"]["catalog_fingerprint"])==64
