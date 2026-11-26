"""Verified producer, replay and worker fencing regressions."""
import json
from copy import deepcopy
from itertools import count
from uuid import UUID

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
    def create(mirrored=False,rearm="NEW_CONTEXT_FAMILY",extra_bars=0,split=None):
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
        if split:
            for begin,segment in ((0,bars[:split]),(split,bars[split:])):
                frozen_candles(records,"EURUSD","M5",BASE+begin*300,segment,mirrored)
            records[0]["partitions"].extend(records.pop()["partitions"])
        else:
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
    assert all(row["trace"]["historical_attempt_state"] for row in large if row["trace"]["attempt_id"])
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
    broken = {**prior,"payload":prior["payload"].replace('"version":2','"version":1')}
    with pytest.raises(ValueError,match="corrupt or incompatible"):
        engine.restore(run,evidence,broken)


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


@pytest.mark.parametrize("reason",["deadline","memory","cancel"])
def test_supervisor_terminates_uncooperative_process(prepared, monkeypatch, reason):
    import asyncio
    import subprocess
    import sys
    from contextvars import ContextVar
    from research_backend.app import historical_strategy_worker as worker

    _,run,_,_ = prepared()
    identifier = store.enqueue(run)
    own_supervisor = ContextVar("own_supervisor",default=False)
    monkeypatch.setattr(worker,"claim",lambda owner:store.claim(owner) if own_supervisor.get() else None)
    monkeypatch.setattr(worker,"HARD_SECONDS",0.01 if reason=="deadline" else 60)
    if reason=="memory":
        monkeypatch.setattr(worker,"working_set",lambda pid:worker.MEMORY_BYTES+1)

    launch = subprocess.Popen
    children = []

    def launch_uncooperative(_command,**options):
        process = launch([sys.executable,"-c","import time; time.sleep(60)"],**options)
        children.append(process)
        if reason=="cancel":
            assert store.cancel(identifier)
        return process

    monkeypatch.setattr(worker.subprocess,"Popen",launch_uncooperative)

    async def observe():
        own_supervisor.set(True)
        stop = asyncio.Event()
        supervisor = asyncio.create_task(worker.supervise(stop))
        expected = "CANCELLED" if reason=="cancel" else "RESOURCE_LIMIT_EXCEEDED"
        try:
            async with asyncio.timeout(5):
                while store.status(identifier)["state"]!=expected:
                    await asyncio.sleep(.05)
        finally:
            stop.set()
            await supervisor

    asyncio.run(observe())
    assert len(children)==1 and children[0].poll() is not None
    assert store.latest_checkpoint(identifier) is None
    outcome = store.status(identifier)
    assert outcome["frontier"] is None
    diagnostic = {"deadline":"External process deadline","memory":"Sampled working-set limit",
                  "cancel":"Worker lease or cancellation"}[reason]
    assert outcome["diagnostic"]==diagnostic


def test_supervisor_claim_recovers_after_sqlite_busy(prepared, monkeypatch):
    import asyncio
    from contextlib import closing
    from contextvars import ContextVar
    import sqlite3
    from threading import Event

    from research_backend.app.db import DATABASE
    from research_backend.app import historical_strategy_worker as worker

    _, run, _, _ = prepared()
    identifier = store.enqueue(run)
    own_supervisor = ContextVar("own_supervisor", default=False)
    hit_lock = Event()
    child_started = Event()

    def claim(owner):
        if not own_supervisor.get():
            return None
        if not hit_lock.is_set():
            with closing(sqlite3.connect(DATABASE, timeout=0)) as contender:
                try:
                    contender.execute("PRAGMA synchronous=FULL")
                except sqlite3.OperationalError as error:
                    assert error.sqlite_errorcode == sqlite3.SQLITE_BUSY
                    hit_lock.set()
                    raise
        return store.claim(owner)

    def launch(command, **options):
        assert command[-2] == identifier
        child_started.set()
        raise OSError("Child launch deliberately disabled for this test")

    monkeypatch.setattr(worker, "claim", claim)
    monkeypatch.setattr(worker.subprocess, "Popen", launch)

    async def check_recovery():
        own_supervisor.set(True)
        stop = asyncio.Event()
        with closing(sqlite3.connect(DATABASE)) as holder:
            holder.execute("PRAGMA locking_mode=EXCLUSIVE")
            holder.execute("BEGIN IMMEDIATE")
            supervisor = asyncio.create_task(worker.supervise(stop))
            assert await asyncio.to_thread(hit_lock.wait, 2)
        try:
            async with asyncio.timeout(5):
                while not supervisor.done() and not child_started.is_set():
                    await asyncio.sleep(.02)
        finally:
            stop.set()
            await supervisor
        assert child_started.is_set()
        assert store.status(identifier)["state"] == "FAILED"

    asyncio.run(check_recovery())


@pytest.mark.parametrize("legacy",[False,True])
def test_opportunity_accounting_fvg_and_legacy_api(client,prepared,legacy):
    _,run,model,evidence = prepared()
    if legacy:
        run.pop("opportunity_accounting_version")
    identifier = store.enqueue(run)
    assert store.claim("test-worker")==identifier
    engine.run_chunks(run,evidence,model,lambda *args:store.publish(identifier,"test-worker",*args),chunk_size=2)
    response = client.get(f"{API}/{identifier}").json()
    items = client.get(f"{API}/{identifier}/opportunities").json()
    attempts = client.get(f"{API}/{identifier}/attempts").json()
    assert response["results_published"] and len(attempts)>=3
    if legacy:
        assert response["opportunity_accounting"]=={"status":"NOT_COLLECTED"}
        assert items==[]
    else:
        assert response["opportunity_accounting"]["status"]=="COLLECTED"
        assert response["opportunity_accounting"]["total"]==len(items)
        assert len({row["family"] for row in items})==len(items)
        assert {row["attempt_id"] for row in items if row["disposition"]=="BOUND"}=={a["id"] for a in attempts}
        assert all(row["first"]["evidence_reference"]["category"]=="fvg" for row in items)
        print("FVG rows",[(row["cutoff"]-BASE,row["disposition"],row["reason"]) for row in items])


def test_measured_zero_opportunities(client,prepared):
    request,*_ = prepared()
    run = prepare_historical(HistoricalInput.model_validate({**request,"cutoff":BASE+2100}))
    model = StrategyDefinitionV2.model_validate(run["definition"])
    evidence = verified_evidence(model,run)
    identifier = store.enqueue(run)
    assert store.claim("test-worker")==identifier
    engine.run_chunks(run,evidence,model,lambda *args:store.publish(identifier,"test-worker",*args))
    summary = client.get(f"{API}/{identifier}").json()["opportunity_accounting"]
    assert summary["status"]=="COLLECTED" and summary["total"]==0
    assert summary["by_disposition"]=={"BOUND":0,"SUPPRESSED":0,"UNRESOLVED":0}
    assert client.get(f"{API}/{identifier}/opportunities").json()==[]


def test_warmup_candidates_follow_existing_selection_without_extra_attempts(prepared):
    request,*_ = prepared()
    start = BASE+5100
    run = prepare_historical(HistoricalInput.model_validate({**request,"evaluation_start":start}))
    model = StrategyDefinitionV2.model_validate(run["definition"])
    prefix = collect(model,run["selection"],start,start)
    reference = engine.make_machine(run,prefix,model)
    reference.run(start)
    winner = reference.bindings[run["rearm_rule"]].proof
    items = []
    engine.account_cutoff(run,reference,start,None,{"families":{},"unresolved_inspections":0},items,lambda:None)
    bound, = [row for row in items if row["disposition"]=="BOUND"]
    competing = [row for row in items if row["reason"]=="SELECTION_POLICY"]
    assert competing
    assert {row["family"] for row in items}=={bound["family"],*(row["family"] for row in competing)}
    assert bound["family"]==winner.family
    assert any(row["evidence_reference"]["available_at"]<start for row in items)
    assert all(row["cutoff"]==start for row in items)
    assert all(row["attempt_id"] is None for row in competing)


@pytest.fixture
def overflow_context(client,monkeypatch):
    # Reuse deterministic strategy identities when replaying the reviewed baseline.
    identifiers = count(1)
    monkeypatch.setattr("research_backend.app.strategy_routes.uuid4",lambda:UUID(int=next(identifiers)))
    monkeypatch.setattr("research_backend.app.historical_strategy_worker.claim",lambda _:None)
    lanes = []
    bars = [(99,100,97,99)]*8 + [
        (100,108,100,108),(106,110,105,109),(109,111,109,110),(110,112,110,111),
    ]
    frozen_candles(lanes,"EURUSD","M5",BASE,bars)
    archive = selection(monkeypatch,lanes,len(bars)*300)
    monkeypatch.setattr("research_backend.app.historical_strategy_inputs.selection_catalog",lambda:[archive])

    def build(begin,end):
        definition = fvg_strategy()
        definition["attempt"]["candidate_limit"] = 1
        definition["context"]["children"].append(condition("false-sibling","FVG_STATE","fvg","FULLY_MITIGATED"))
        request = request_body(client,archive,definition,BASE+end)
        request.update(evaluation_start=BASE+begin,rearm="NEW_CONTEXT_FAMILY",
                       max_attempts=2,context_alias="bound_gap")
        admitted = client.post(API+"/validate",json=request)
        assert admitted.status_code==200,admitted.text
        queued = client.post(API,json=request)
        assert queued.status_code==202,queued.text
        run = prepare_historical(HistoricalInput.model_validate(request))
        assert queued.json()["run_id"]==run["run_id"]
        model = StrategyDefinitionV2.model_validate(run["definition"])
        return run,model,verified_evidence(model,run)
    return build


@pytest.mark.parametrize("offset,expected_outcome,expected_reason,families,unresolved",[
    (2700,"NOT_SATISFIED","NO_MATCH_COMPLETE_COVERAGE",0,0),
    (3300,"UNKNOWN","CANDIDATE_LIMIT_EXCEEDED",2,1),
])
def test_false_context_sibling_does_not_mask_opportunity_uncertainty(
    client,overflow_context,offset,expected_outcome,expected_reason,families,unresolved
):
    run,model,evidence = overflow_context(offset,offset)
    cutoff = BASE+offset
    view = evidence.views["fvg"]
    assert all(lane.at(cutoff)["status"]=="COMPLETE" for lane in evidence.coverage.values())
    active = {view.records[row["id"]].family for row in evidence.events[view.snapshot].get("fvg",((),()))[1]
              if row["state"]=="ACTIVE" and view.eligible(row,cutoff,current=True,purpose=view.purpose)}
    assert len(active)==families
    assert engine.successor_schedule(run,evidence,model)=={cutoff}
    identifier = run["run_id"]
    assert store.claim("worker")==identifier
    engine.run_chunks(run,evidence,model,lambda *args:store.publish(identifier,"worker",*args))
    detail = client.get(f"{API}/{identifier}").json()
    decision = client.get(f"{API}/{identifier}/decisions/{cutoff}").json()
    rules = {item["id"]:item for item in decision["rules"]}
    assert (rules["gap"]["outcome"],rules["gap"]["reason"])==(expected_outcome,expected_reason)
    assert rules["false-sibling"]["outcome"]=="NOT_SATISFIED"
    assert decision["phase_results"]["context"]=="NOT_SATISFIED"
    assert decision["attempt_id"] is None and decision["entry_intent"] is None
    assert detail["qualified_intents"]==0 and detail["attempt_counts"]=={}
    summary = detail["opportunity_accounting"]
    assert detail["results_published"] and summary["status"]=="COLLECTED" and summary["total"]==0
    assert summary["by_disposition"]=={"BOUND":0,"SUPPRESSED":0,"UNRESOLVED":0}
    assert client.get(f"{API}/{identifier}/opportunities").json()==[]
    print("single inspection",offset,"eligible families",len(active),"gap",expected_outcome,expected_reason,
          "context",decision["phase_results"]["context"],"decision",decision["decision_id"],"summary",summary)
    assert summary["unresolved_inspections"]==unresolved
    assert summary["unresolved_by_reason"]==({expected_reason:unresolved} if unresolved else {})


def test_masked_overflow_recovery_preserves_counts_and_established_family(client,overflow_context):
    run,model,evidence = overflow_context(3000,3600)
    identifier = run["run_id"]
    uninterrupted,snapshots = execute(run,model,evidence,93)
    original = {key:value for key,value in run.items() if key!="opportunity_accounting_version"}
    assert execute(original,model,evidence,1)[0]==uninterrupted
    expected_progress = json.loads(snapshots[-1]["payload"])["opportunity_accounting"]
    assert expected_progress["unresolved_inspections"]==2
    assert expected_progress["unresolved_by_reason"]=={"CANDIDATE_LIMIT_EXCEEDED":2}
    assert len(expected_progress["families"])==1

    assert store.claim("worker")==identifier
    def interrupt(snapshot,frontier,entries,completed,metrics):
        store.publish(identifier,"worker",snapshot,frontier,entries,completed,metrics)
        if frontier==BASE+3300:
            store.publish(identifier,"worker",snapshot,frontier,entries,completed,metrics)
            raise RuntimeError("interrupt after overflow checkpoint")

    with pytest.raises(RuntimeError,match="after overflow checkpoint"):
        engine.run_chunks(run,evidence,model,interrupt,chunk_size=1)
    store.finish(identifier,"worker","FAILED","interrupted")
    saved = store.latest_checkpoint(identifier)
    assert saved["frontier"]==BASE+3300
    progress = json.loads(saved["payload"])["opportunity_accounting"]
    assert progress["unresolved_inspections"]==1
    assert progress["unresolved_by_reason"]=={"CANDIDATE_LIMIT_EXCEEDED":1}
    assert progress["families"]==expected_progress["families"]
    assert client.get(f"{API}/{identifier}").json()["opportunity_accounting"]=={"status":"NOT_PUBLISHED"}
    assert client.get(f"{API}/{identifier}/checkpoint").status_code==409
    assert client.get(f"{API}/{identifier}/opportunities").json()==[]
    assert client.post(f"{API}/{identifier}/resume").status_code==200
    assert store.claim("resumed")==identifier
    engine.run_chunks(run,evidence,model,lambda *args:store.publish(identifier,"resumed",*args),
                      saved=saved,chunk_size=7)
    detail = client.get(f"{API}/{identifier}").json()
    summary = detail["opportunity_accounting"]
    assert summary["unresolved_inspections"]==expected_progress["unresolved_inspections"]
    assert summary["unresolved_by_reason"]==expected_progress["unresolved_by_reason"]
    assert summary["by_disposition"]=={"BOUND":0,"SUPPRESSED":1,"UNRESOLVED":0}
    row, = client.get(f"{API}/{identifier}/opportunities").json()
    assert row["family"]==next(iter(progress["families"]))
    assert row["first"]["evidence_reference"]["available_at"]==BASE+3000
    assert row["reason"]=="CONTEXT_NOT_SATISFIED"
    final, = uninterrupted
    decision = client.get(f"{API}/{identifier}/decisions/{BASE+3600}").json()
    assert decision=={"decision_id":final["id"],**final["trace"]}
    assert client.get(f"{API}/{identifier}/intents").json()==[]
    print("overflow replay", "committed",progress["unresolved_by_reason"],"resumed",summary,
          "established family",row["family"],"decision",decision["decision_id"])
