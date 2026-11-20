"""Adversarial API publication and SQLite fencing regressions."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event, current_thread, main_thread

import pytest

from research_backend.app import db
from research_backend.app import historical_strategy_engine as engine
from research_backend.app import historical_strategy_store as store
from research_backend.app.analysis_evidence import ResourceLimit
from research_backend.app.historical_strategy_inputs import HistoricalInput, prepare_historical, verified_evidence
from research_backend.app.strategy_definitions import StrategyDefinitionV2
from test_historical_strategy_runs import API, prepared
from test_strategy_definitions import group
from test_strategy_evaluator import BASE, ready_entry, request_body, seed_fixture
from test_strategy_evaluator_closure import fvg_strategy
from test_strategy_evaluator_integrity import condition


def assert_unpublished(client,run_id,entry,state):
    base = f"{API}/{run_id}"
    result = client.get(base).json()
    assert result["worker"]["state"]==state
    assert result["worker"]["frontier"]==entry["trace"]["cutoff"]
    assert result["results_published"] is False
    assert result["attempt_counts"]=={} and result["qualified_intents"]==0
    for name in ("decisions","attempts","intents"):
        assert client.get(f"{base}/{name}").json()==[]
    ref = entry["intent"]["evidence_references"][0]
    decision = f"{base}/decisions/{entry['trace']['cutoff']}"
    evidence = f"{decision}/evidence/{ref['calculation_snapshot']}/{ref['event_id']}"
    for endpoint in (base+"/checkpoint",decision,evidence,evidence+"/chart"):
        assert client.get(endpoint).status_code==409


@pytest.mark.parametrize("outcome",["FAILED","CANCELLED","LOST","RESOURCE_LIMIT_EXCEEDED"])
def test_staged_ledgers_hidden_until_atomic_completion(client,prepared,monkeypatch,outcome):
    _,run,model,evidence = prepared()
    clock = [100]
    monkeypatch.setattr(store,"time",lambda:clock[0])
    identifier = store.enqueue(run)
    assert store.claim("original")==identifier
    staged = []

    def stop_after_intent(snapshot,frontier,batch,completed,metrics):
        store.publish(identifier,"original",snapshot,frontier,batch,completed,metrics)
        if any(item["intent"] for item in batch):
            staged.extend(item for item in batch if item["intent"])
            raise RuntimeError("worker interrupted after commit")

    with pytest.raises(RuntimeError,match="after commit"):
        engine.run_chunks(run,evidence,model,stop_after_intent,chunk_size=1)
    assert_unpublished(client,identifier,staged[0],"RUNNING")
    if outcome=="CANCELLED":
        assert client.post(f"{API}/{identifier}/cancel").json()["worker"]["state"]=="CANCELLING"
    if outcome=="LOST":
        clock[0] += 12
        assert store.claim("reaper") is None
    else:
        store.finish(identifier,"original",outcome,"test interruption")
    expected = "FAILED" if outcome=="LOST" else outcome
    assert_unpublished(client,identifier,staged[0],expected)
    if outcome=="RESOURCE_LIMIT_EXCEEDED":
        return
    assert client.post(f"{API}/{identifier}/resume").status_code==200
    assert store.claim("resumed")==identifier
    saved = store.latest_checkpoint(identifier)
    engine.run_chunks(run,evidence,model,lambda *args:store.publish(identifier,"resumed",*args),saved=saved)
    result = client.get(f"{API}/{identifier}").json()
    assert result["results_published"] is True and result["worker"]["state"]=="COMPLETED"
    published = client.get(f"{API}/{identifier}/intents").json()
    assert sum(item["id"]==staged[0]["intent"]["id"] for item in published)==1
    assert result["qualified_intents"]==len(published)
    cutoff = staged[0]["trace"]["cutoff"]
    ref = staged[0]["intent"]["evidence_references"][0]
    path = f"{API}/{identifier}/decisions/{cutoff}"
    assert client.get(path).json()["entry_intent"]==staged[0]["intent"]
    assert client.get(f"{path}/evidence/{ref['calculation_snapshot']}/{ref['event_id']}/chart").status_code==200


def test_attempt_budget_hides_qualifying_first_attempt(client,prepared):
    request,_,_,_ = prepared()
    definition = fvg_strategy(current=False)
    definition["trigger"] = group("trigger",condition("later","FVG_STATE","fvg","ACTIVE"))
    from research_backend.app.strategy_evaluation_sources import selection_catalog
    request = request_body(client, selection_catalog()[0], definition,request["cutoff"]) | {
        "rearm":"NEW_CONTEXT_FAMILY","context_alias":"bound_gap","max_attempts":1,"evaluation_start":BASE+1800}
    run = prepare_historical(HistoricalInput.model_validate(request))
    model = StrategyDefinitionV2.model_validate(run["definition"])
    evidence = verified_evidence(model,run)
    identifier = client.post(API,json=request).json()["run_id"]
    assert store.claim("worker")==identifier
    staged = []
    def publish(snapshot,frontier,batch,completed,metrics):
        store.publish(identifier,"worker",snapshot,frontier,batch,completed,metrics)
        staged.extend(batch)
    with pytest.raises(ResourceLimit,match="attempt budget"):
        engine.run_chunks(run,evidence,model,publish)
    assert len(staged)==1 and staged[0]["intent"]
    # Simulate an interruption after staging the ceiling-bound attempt but
    # before persisting the terminal worker outcome.
    store.finish(identifier,"worker","FAILED","Interrupted before reporting attempt budget")
    assert_unpublished(client,identifier,staged[0],"FAILED")
    assert client.post(f"{API}/{identifier}/resume").status_code==200
    assert store.claim("replacement")==identifier
    saved = store.latest_checkpoint(identifier)
    with pytest.raises(ResourceLimit,match="attempt budget"):
        engine.run_chunks(run,evidence,model,lambda *args:pytest.fail("Attempt budget was reset on resume"),saved=saved)
    store.finish(identifier,"replacement","RESOURCE_LIMIT_EXCEEDED","Attempt budget")
    assert_unpublished(client,identifier,staged[0],"RESOURCE_LIMIT_EXCEEDED")


@pytest.mark.parametrize("operation",["heartbeat","publish","finish"])
def test_expiry_after_waiting_for_writer_lock(client,prepared,monkeypatch,operation):
    _,run,_,_ = prepared()
    identifier = store.enqueue(run)
    clock = [100]
    monkeypatch.setattr(store,"time",lambda:clock[0])
    assert store.claim("original")==identifier
    clock[0]=111
    waiting = Event()
    original = db.connect
    def observed_connection():
        connection = original()
        if current_thread()!=main_thread():
            connection.set_trace_callback(lambda statement:waiting.set() if statement=="BEGIN IMMEDIATE" else None)
        return connection
    monkeypatch.setattr(db,"connect",observed_connection)
    operations = {
        "heartbeat":lambda:store.heartbeat(identifier,"original"),
        "publish":lambda:store.publish(identifier,"original",("{}", "stale"),BASE,[],False,{}),
        "finish":lambda:store.finish(identifier,"original","CANCELLED","stale acknowledgement"),
    }
    with ThreadPoolExecutor(max_workers=1) as pool:
        with db.transaction() as lock:
            lock.execute("BEGIN IMMEDIATE")
            attempted = pool.submit(operations[operation])
            assert waiting.wait(5)
            clock[0]=112
        if operation=="publish":
            with pytest.raises(store.LeaseLost):
                attempted.result(timeout=5)
        else:
            assert not attempted.result(timeout=5)
    expired = store.status(identifier)
    assert (expired["lease_until"],expired["state"],expired["frontier"])==(112,"RUNNING",None)
    assert store.claim("cleanup") is None
    assert store.status(identifier)["state"]=="FAILED"
    assert store.resume(identifier)
    assert store.claim("replacement")==identifier
    current = store.status(identifier)
    assert not store.heartbeat(identifier,"original")
    with pytest.raises(store.LeaseLost):
        store.publish(identifier,"original",("{}", "stale"),BASE,[],False,{})
    store.finish(identifier,"original","CANCELLED","stale")
    assert store.status(identifier)==current


def test_admission_rejects_missing_or_nonmandatory_rearm_without_queue(client,prepared,monkeypatch):
    request,_,model,_ = prepared()
    for alias in (None,"","absent","optional"):
        candidate = dict(request, context_alias=alias)
        if alias is None:
            assert client.post(API+"/validate",json={k:v for k,v in candidate.items() if k!="context_alias"}).status_code==422
            assert client.post(API,json={k:v for k,v in candidate.items() if k!="context_alias"}).status_code==422
        assert client.post(API+"/validate",json=candidate).status_code==422
        assert client.post(API,json=candidate).status_code==422
    # The saved-strategy validator forbids aliases inside ANY already.
    # Exercise historical admission in isolation with the optional rule in place.
    rule = model.context.children[0].model_copy(update={"alias":"optional"})
    alternative = rule.model_copy(update={"id":"alternative","alias":None})
    context = model.context.model_copy(update={"operator":"ANY","children":[rule,alternative]})
    with monkeypatch.context() as scope:
        scope.setattr("research_backend.app.historical_strategy_inputs.load_definition",
                      lambda _:model.model_copy(update={"context":context}))
        for path in (API,API+"/validate"):
            response = client.post(path,json={**request,"context_alias":"optional"})
            assert response.status_code==422 and "mandatory positive" in response.text

    archive = seed_fixture()
    monkeypatch.setattr("research_backend.app.strategy_evaluation_sources.selection_catalog",lambda:[archive])
    mixed = request_body(client,archive,ready_entry(),archive["end"])
    mixed.update(rearm="NEW_CONTEXT_FAMILY",context_alias="crt_reference",max_attempts=3)
    for path in (API,API+"/validate"):
        response = client.post(path,json=mixed)
        assert response.status_code==422 and "Use SINGLE_ATTEMPT" in response.text
    for table in ("historical_strategy_runs","historical_worker_state"):
        assert db.one(f"SELECT COUNT(*) AS total FROM {table}")["total"]==0


def test_legacy_incomplete_ledger_is_hidden(client,prepared):
    _,run,model,evidence = prepared()
    legacy = {**run,"identity":{**run["identity"],"historical_version":1}}
    identifier = store.enqueue(legacy)
    assert store.claim("old-worker")==identifier
    staged = []
    def publish(snapshot,frontier,batch,done,metrics):
        store.publish(identifier,"old-worker",snapshot,frontier,batch,done,metrics)
        if any(entry["intent"] for entry in batch):
            staged.extend(entry for entry in batch if entry["intent"])
            raise RuntimeError("old worker interrupted")
    with pytest.raises(RuntimeError,match="old worker"):
        engine.run_chunks(run,evidence,model,publish,chunk_size=1)
    store.finish(identifier,"old-worker","FAILED","saved by older evaluator")
    assert_unpublished(client,identifier,staged[0],"FAILED")
    assert client.post(f"{API}/{identifier}/resume").status_code==409
