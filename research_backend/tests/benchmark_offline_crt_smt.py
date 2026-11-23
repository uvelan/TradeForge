"""Explicitly invoked full-session benchmark; ordinary CI does not run long research jobs."""
import json
import os
from collections import Counter
from functools import wraps
from itertools import count
from pathlib import Path
from time import perf_counter
from uuid import UUID

import pytest

from research_backend.app import historical_strategy_engine as engine
from research_backend.app import historical_strategy_store as store
from research_backend.app import strategy_evaluation_sources as sources
from research_backend.app.analysis_evidence import AnalysisCoordinator, ResourceLimit
from research_backend.app.historical_strategy_inputs import HistoricalInput, prepare_historical, verified_evidence
from research_backend.app.historical_strategy_worker import working_set
from research_backend.app.offline_crt_smt_replay import OFFLINE_CUTOFFS, ReplayArchive, run_offline
from research_backend.app.strategy_evaluator import CATALOG, EvaluationMachine, TERMINAL
from research_backend.app.strategy_evidence import fingerprint
from test_bounded_crt_smt_successors import START, canonical_successors, successor_request
from test_historical_strategy_runs import API
from test_strategy_evaluator import BASE

END = BASE+90_000


def points(evidence, model, first, end):
    """Independent M5 arrival grid, publication availability and predicate age edges."""
    stamps = {START,end}
    for binding in (model.instruments.primary,model.instruments.comparison):
        stamps.update(stamp+binding.modeled_delay_seconds for stamp in range(BASE+300,end+1,300))
    for lanes in evidence.events.values():
        for times,_ in lanes.values():
            stamps.update(times)
    for view in evidence.views.values():
        for windows in view.smt_uncertainty.values():
            stamps.update(stamp for item in windows for stamp in (item.start,item.recovered_at) if stamp is not None)
    def leaves(group):
        for child in group.children:
            if child.type=="RULE":
                yield child
            else:
                yield from leaves(child)
    for phase in ("context","setup","trigger","invalidation"):
        for rule in leaves(getattr(model,phase)):
            view = evidence.views[rule.dependency]
            times,_ = evidence.events[view.snapshot].get(CATALOG[rule.observation]["category"],((),()))
            stamps.update(stamp+rule.evidence.max_age_seconds+1 for stamp in times)
    return {stamp for stamp in stamps if first<=stamp<=end}


def timers(machine, frontier, end):
    """Independently derive release and seconds-expiry without scheduler helpers."""
    values = set()
    if machine.setup_at is not None:
        values.add(machine.setup_at+1)
    origin = (machine.context_at if machine.definition.attempt.expiry_origin=="CONTEXT_ACTIVATION"
              else machine.setup_at)
    if origin is not None:
        assert machine.definition.expiry.unit=="SECONDS"
        values.add(origin+machine.definition.expiry.count+1)
    return {stamp for stamp in values if frontier<stamp<=end}


def fresh(run,horizon,model,check,observe):
    end = run["identity"]["cutoff"]
    pending = points(horizon,model,START,end)
    dynamic = set()
    terminal = None
    spent = set()
    observations,entries = [],[]
    accounting = {"version":1,"families":{},"unresolved_inspections":0}
    inspected = 0
    while pending or dynamic:
        check()
        inspected += 1
        if inspected>OFFLINE_CUTOFFS:
            raise ResourceLimit("Independent reference exceeds its offline cutoff ceiling")
        frontier = min(pending|dynamic)
        pending.discard(frontier)
        # Both pinned single partitions contribute at every inspected cutoff.
        prefix = sources.collect(model,run["selection"],frontier,START,check_budget=check)
        begin = START if terminal is None else terminal+1
        machine = EvaluationMachine(model,prefix,run["identity"],begin,end,check_budget=check,
                                    rearm_rule=None if terminal is None else run["rearm_rule"],
                                    rearm_after=terminal,spent_families=spent)
        dues = points(prefix,model,begin,frontier)
        known = set(dues)
        while dues:
            stamp = min(dues)
            dues.remove(stamp)
            machine.step(stamp)
            if machine.state in TERMINAL:
                break
            scheduled = timers(machine,stamp,frontier)-known
            known |= scheduled
            dues |= scheduled
        observe(frontier,machine.decision(frontier,len(known)))
        engine.account_cutoff(run,machine,frontier,terminal,accounting,observations,check)
        if machine.state in TERMINAL and machine.attempt:
            entries.append(engine.ledger_entry(run,machine,frontier))
            spent.add(machine.bindings[run["rearm_rule"]].proof.family)
            terminal,dynamic = frontier,set()
        else:
            dynamic = timers(machine,frontier,end)
        if not (pending or dynamic) and (not entries or entries[-1]["trace"]["cutoff"]!=frontier):
            entries.append(engine.ledger_entry(run,machine,frontier))
    return entries,observations,accounting


def optimized(run,horizon,model,check,observe):
    archive = ReplayArchive()
    entries,observations = [],[]
    final = {}
    def publish(snapshot,frontier,batch,done,metrics):
        entries.extend(batch)
        observations.extend(snapshot[2])
        if done:
            final.update(json.loads(snapshot[0])["opportunity_accounting"])
    run_offline(run,horizon,model,publish,archive=archive,chunk_size=17,check=check,observe=observe)
    final.pop("batch_digest")
    return entries,observations,final


@pytest.fixture
def session_run(client,canonical_successors,monkeypatch):
    identities = count(1)
    monkeypatch.setattr("research_backend.app.strategy_routes.uuid4",lambda:UUID(int=next(identities)))
    request,model,_ = canonical_successors(extra=249)
    assert request["cutoff"]==END
    request = {**successor_request(request),"max_attempts":32}
    run = prepare_historical(HistoricalInput.model_validate(request))
    return run,model


def measured(path,run,model,monkeypatch):
    calls,times = Counter(),Counter()
    rows = []
    started = perf_counter()
    cutoff_seconds = 300 if path=="fresh" else 150
    def check():
        if perf_counter()-started>cutoff_seconds:
            raise ResourceLimit(f"{path} exhausted its {cutoff_seconds}-second benchmark deadline")
    def timed(name,method):
        @wraps(method)
        def invoke(*args,**kwargs):
            before = perf_counter()
            calls[name] += 1
            try:
                return method(*args,**kwargs)
            finally:
                times[name] += perf_counter()-before
        return invoke
    def observe(cutoff,decision):
        rows.append([cutoff,fingerprint(decision),decision["state"],decision["reason"]])
    with monkeypatch.context() as patch:
        patch.setattr(sources,"read_partition",timed("verified_archive_decodes",sources.read_partition))
        patch.setattr(sources,"load_reference",timed("history_load",sources.load_reference))
        patch.setattr(sources,"evaluate",timed("crt",sources.evaluate))
        patch.setattr(sources,"evaluate_strict_smt",timed("smt",sources.evaluate_strict_smt))
        patch.setattr(EvaluationMachine,"step",timed("evaluator_step",EvaluationMachine.step))
        horizon = verified_evidence(model,run,check)
        result = (fresh if path=="fresh" else optimized)(run,horizon,model,check,observe)
    elapsed = perf_counter()-started
    peak = working_set(os.getpid())
    return {"elapsed_seconds":round(elapsed,4),"peak_process_bytes":peak,"counts":dict(calls),
            "seconds":{key:round(value,4) for key,value in times.items()},"cutoffs":len(rows),
            "attempts":sum(bool(item["trace"]["attempt_id"]) for item in result[0])},rows,result


def test_measure_session(session_run,monkeypatch):
    path = os.environ["TRADEFORGE_BENCH_PATH"]
    output = Path(os.environ["TRADEFORGE_BENCH_OUTPUT"])
    run,model = session_run
    cold,trace,result = measured(path,run,model,monkeypatch)
    warm,again,repeated = measured(path,run,model,monkeypatch)
    assert (trace,result)==(again,repeated)
    assert 160<cold["cutoffs"]<=OFFLINE_CUTOFFS
    decisions,opportunities,accounting = result
    assert [(item["trace"]["cutoff"]-BASE,item["trace"]["state"]) for item in decisions[:3]]==[
        (7200,"EXPIRED"),(14120,"TRIGGER_QUALIFIED"),(15000,"INVALIDATED")]
    assert sum(bool(item["intent"]) for item in decisions)==1
    assert any(item["cutoff"]==BASE+7200 and item["reason"]=="TERMINAL_BOUNDARY" for item in opportunities)
    payload = {"selection":run["selection"],"identity":run["identity"],"cold":cold,"warm":warm,
               "trace":trace,"decisions":decisions,"opportunities":opportunities,"accounting":accounting}
    (output/f"{path}.json").write_text(json.dumps(payload,indent=2)+"\n",encoding="utf-8")
    print(path, "cold", cold, "warm", warm)


def first_difference(left,right):
    for key in ("identity","selection","trace","decisions","opportunities","accounting"):
        if left[key]==right[key]:
            continue
        if key=="trace":
            for a,b in zip(left[key],right[key]):
                if a!=b:
                    return key,a,b
            return key,len(left[key]),len(right[key])
        return key,fingerprint(left[key]),fingerprint(right[key])
    return None


def test_session_recovery_and_equivalence(client,session_run):
    output = Path(os.environ["TRADEFORGE_BENCH_OUTPUT"])
    reference,comparison = (json.loads((output/f"{path}.json").read_text(encoding="utf-8"))
                            for path in ("fresh","optimized"))
    divergence = first_difference(reference,comparison)
    assert divergence is None,divergence
    assert reference["identity"]==session_run[0]["identity"]
    print("exact full-session comparison",len(reference["trace"]),"cutoffs",
          "ledger digest",fingerprint(reference["decisions"]),
          "opportunity digest",fingerprint(reference["opportunities"]))
    run,model = session_run
    request = {key:run["identity"][key] for key in HistoricalInput.model_fields}
    for route in ("","/validate"):
        rejected = client.post(API+route,json=request)
        assert rejected.status_code==422 and "160 as-of cutoffs" in rejected.text
    horizon = verified_evidence(model,run)
    identifier = store.enqueue(run)  # Isolated fixture exercises the real publication fence.
    observed = []
    def observe(cutoff,decision):
        observed.append([cutoff,fingerprint(decision),decision["state"],decision["reason"]])

    for owner,offset in (("boundary",7200),("before-peer",14100)):
        assert store.claim(owner)==identifier
        def interrupt(snapshot,frontier,batch,done,metrics):
            store.publish(identifier,owner,snapshot,frontier,batch,done,metrics)
            if frontier==BASE+offset:
                store.publish(identifier,owner,snapshot,frontier,batch,done,metrics)
                raise RuntimeError("committed checkpoint deliberately interrupted")
        with pytest.raises(RuntimeError,match="deliberately interrupted"):
            run_offline(run,horizon,model,interrupt,saved=store.latest_checkpoint(identifier),
                        chunk_size=1,observe=observe)
        assert store.latest_checkpoint(identifier)["frontier"]==BASE+offset
        store.finish(identifier,owner,"FAILED","deliberate offline interruption")
        assert client.get(f"{API}/{identifier}/opportunities").json()==[]
        assert client.get(f"{API}/{identifier}/decisions").json()==[]
        assert client.get(f"{API}/{identifier}/intents").json()==[]
        assert client.get(f"{API}/{identifier}/checkpoint").status_code==409
        assert client.post(f"{API}/{identifier}/resume").status_code==200

    assert store.claim("recovered")==identifier
    next_heartbeat = 0
    def check():
        nonlocal next_heartbeat
        now = perf_counter()
        if now>=next_heartbeat:
            assert store.heartbeat(identifier,"recovered")
            next_heartbeat = now+2
    run_offline(run,horizon,model,lambda *args:store.publish(identifier,"recovered",*args),
                saved=store.latest_checkpoint(identifier),chunk_size=23,observe=observe,check=check)
    assert observed==reference["trace"]
    rows = client.get(f"{API}/{identifier}/opportunities",params={"limit":100}).json()
    assert len(rows)==len(reference["opportunities"])
    assert [{key:value for key,value in row.items() if key!="first"} for row in rows]==reference["opportunities"]
    summaries = client.get(f"{API}/{identifier}/decisions",params={"limit":100}).json()
    assert [row["id"] for row in summaries]==[entry["id"] for entry in reference["decisions"]]
    for item in reference["decisions"]:
        saved = client.get(f"{API}/{identifier}/decisions/{item['trace']['cutoff']}").json()
        assert saved=={"decision_id":item["id"],**item["trace"]}
    assert client.get(f"{API}/{identifier}/intents").json()==[
        item["intent"] for item in reference["decisions"] if item["intent"]]
    assert client.get(f"{API}/{identifier}").json()["opportunity_accounting"]["unresolved_by_reason"]==(
        reference["accounting"].get("unresolved_by_reason",{}))
    print("recovery after +7200 and +14100",len(observed),"exact cutoffs",len(rows),"families")

    # A revised later candle in a noncontributing partition must leave each
    # earlier pinned selection intact. Revising a contributing partition would
    # intentionally create new archive and proof identities.
    from copy import deepcopy
    from research_backend.app.storage import write_partition
    variants = []
    for close in (101,102):
        source = deepcopy(run["selection"])
        primary = source["series"]["PRIMARY:M5"]
        part = write_partition("DUKASCOPY",primary["feed_id"],"EURUSD","candles","M5","BID",
                               [{"time":END,"open":100,"high":103,"low":99,"close":close,"volume":0,"closed":True}])
        primary["partitions"].append({key:part[key] for key in ("id","sha256")})
        variants.append(source)
    assert variants[0]!=variants[1]
    for cutoff,*_ in observed:
        assert engine.asof_selection(variants[0],cutoff)==engine.asof_selection(variants[1],cutoff)==run["selection"]

    b = next(item for item in reference["opportunities"] if item["reason"]=="TERMINAL_BOUNDARY")
    first = reference["decisions"][0]["trace"]["bound_crt"]["evidence"]["family"]
    for offset,state in ((14119,"SMT_CANDIDATE"),(14120,"SMT_CONFIRMED")):
        cutoff = BASE+offset
        decisions = []
        for variant,archive in zip(variants,(None,ReplayArchive())):
            prefix = sources.collect(model,engine.asof_selection(variant,cutoff),cutoff,START,replay_archive=archive)
            machine = engine.make_machine(run,prefix,model,BASE+7200,[first])
            decision = machine.run(cutoff)
            view = prefix.views["smt"]
            bearish = [row for row in prefix.events[view.snapshot]["smt"][1] if row["direction"]=="bearish"]
            latest = bearish[-1]
            assert latest["state"]==state
            assert bool(decision["entry_intent"])==(offset==14120)
            decisions.append((decision,latest["evidence_reference"],latest["market_event_id"]))
        assert decisions[0]==decisions[1]
        print("delayed peer",offset,state,"family",decisions[0][2],"proof",decisions[0][1],
              "intent",bool(decisions[0][0]["entry_intent"]))
    assert b["cutoff"]==b["evidence_reference"]["available_at"]==BASE+7200
