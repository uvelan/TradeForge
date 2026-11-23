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
from research_backend.app import offline_crt_smt_replay as offline
from research_backend.app import historical_strategy_store as store
from research_backend.app import strategy_evaluation_sources as sources
from research_backend.app.analysis_evidence import AnalysisCoordinator, ResourceLimit
from research_backend.app.historical_strategy_inputs import HistoricalInput, prepare_historical, verified_evidence
from research_backend.app.historical_strategy_worker import working_set
from research_backend.app.offline_crt_smt_replay import OFFLINE_CUTOFFS, ReplayArchive, run_offline
from research_backend.app.strategy_evaluator import CATALOG, EvaluationMachine, TERMINAL
from research_backend.app.strategy_evidence import fingerprint
from test_bounded_crt_smt_successors import CONFIRMATION_BAR, START, canonical_successors, successor_request
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


def optimized(run,horizon,model,check,observe,*,archive=None):
    archive = ReplayArchive() if archive is None else archive
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


REVISION_OPEN = BASE+CONFIRMATION_BAR*300
REVISION_ARRIVAL = REVISION_OPEN+300+20


def market_proof(value):
    """Keep market-event identity and timing, separating only pinned reference coordinates."""
    if isinstance(value,dict):
        if {"event_id","calculation_snapshot","calculation_id","source","category"}<=value.keys():
            pinned = {"source","participants","calculation_snapshot","calculation_id"}
            return {key:item for key,item in value.items() if key not in pinned}
        return {key:market_proof(item) for key,item in value.items()}
    if isinstance(value,(list,tuple)):
        return [market_proof(item) for item in value]
    return value


def strategy_projection(decision):
    omitted = {"attempt_id","producers","resource_counts","supporting_events","bound_crt","entry_intent"}
    projected = {key:market_proof(item) for key,item in decision.items() if key not in omitted}
    projected["has_attempt"] = decision["attempt_id"] is not None
    projected["bound_crt"] = market_proof({key:item for key,item in decision["bound_crt"].items()
                                          if key!="attempt_id"}) if decision["bound_crt"] else None
    projected["supporting_events"] = sorted((market_proof(row) for row in decision["supporting_events"].values()),
                                           key=lambda row:row["id"])
    intent = decision["entry_intent"]
    projected["entry_intent"] = ({key:item for key,item in intent.items() if key not in {"id","attempt_id"}}
                                 if intent else None)
    return projected


def observe_revision_session(run,model,mode,high,monkeypatch):
    peer = run["selection"]["series"]["COMPARISON:M5"]["partitions"][0]
    archive = ReplayArchive() if mode=="cached" else None
    horizon = verified_evidence(model,run)
    real_collect = sources.collect
    real_history = sources.load_reference
    inspections,material,cache_trace = {},{},{}
    spent = first_rows = None
    started = perf_counter()
    def check():
        if perf_counter()-started>150:
            raise ResourceLimit("In-window revision evaluation exceeded 150 seconds")

    def inspect_prefix(definition,selection,cutoff,start,**options):
        nonlocal first_rows
        observed,changed = [],set()
        @wraps(real_history)
        def tap(request,begin,end,step,**kwargs):
            result = real_history(request,begin,end,step,**kwargs)
            bars = [(bar.model_dump(mode="json"),available) for bar,available in result["rows"]]
            observed.append((request.symbol,request.timeframe,begin,end,step,bars))
            if request.symbol=="GBPUSD":
                changed.update((bar["high"],available) for bar,available in bars if bar["time"]==REVISION_OPEN)
            return result
        with monkeypatch.context() as patch:
            patch.setattr(sources,"load_reference",tap)
            prefix = real_collect(definition,selection,cutoff,start,**options)
        events = {dependency:{category:rows for category,(_,rows) in prefix.events[view.snapshot].items()}
                  for dependency,view in prefix.views.items()}
        inspections[cutoff] = {"candles":fingerprint(observed),"market_events":fingerprint(market_proof(events)),
                               "raw_events":fingerprint(events)}
        if archive is not None:
            rows, = [data for key,data in archive.archive.partitions.items() if key[0]==peer["id"]]
            if first_rows is None:
                first_rows = rows
                cache_trace["first_decode_cutoff"] = cutoff-BASE
                pinned, = [row for row in rows if row["time"]==REVISION_OPEN]
                assert pinned["high"]==high
                cache_trace["decoded_high"] = pinned["high"]
            assert rows is first_rows, "The peer partition must reuse the same cached rows"
            if cutoff in (REVISION_ARRIVAL-1,REVISION_ARRIVAL):
                cache_trace[str(cutoff-BASE)] = {
                    "same_decoded_object":rows is first_rows,
                    "verified_decodes":archive.archive.counts["partition_decodes"],
                    "reuses":archive.archive.counts["partition_reuses"],
                }
        if cutoff<REVISION_ARRIVAL:
            assert not changed, ("future candle visible",cutoff-BASE)
        if cutoff in (REVISION_ARRIVAL-1,REVISION_ARRIVAL):
            latest = [row for row in events["smt"]["smt"] if row["direction"]=="bearish"
                      and row["available_at"]<=cutoff][-1]
            material[str(cutoff-BASE)] = {"visible_candle":sorted(changed),
                                          "revision":latest}
        if cutoff==REVISION_ARRIVAL:
            assert changed=={(high,REVISION_ARRIVAL)}, "Arrived candle was stale or missing"
        return prefix

    def collect_inspection(definition,selection,cutoff,start,**options):
        before = REVISION_ARRIVAL-1
        if cutoff==REVISION_ARRIVAL:
            assert spent is not None
            early = inspect_prefix(definition,selection,before,start,**options)
            machine = engine.make_machine(run,early,definition,BASE+7200,[spent])
            observe(before,machine.run(before))
        return inspect_prefix(definition,selection,cutoff,start,**options)

    def observe(cutoff,decision):
        nonlocal spent
        if cutoff==BASE+7200:
            spent = decision["bound_crt"]["evidence"]["family"]
        inspections[cutoff].update(strategy=fingerprint(strategy_projection(decision)),
                                   raw_trace=fingerprint(decision),state=decision["state"],reason=decision["reason"])
        if cutoff in (REVISION_ARRIVAL-1,REVISION_ARRIVAL):
            material[str(cutoff-BASE)]["trace"] = decision

    with monkeypatch.context() as patch:
        patch.setattr(sources,"collect",collect_inspection)
        patch.setattr(offline,"collect",collect_inspection)
        result = (fresh(run,horizon,model,check,observe) if archive is None else
                  optimized(run,horizon,model,check,observe,archive=archive))
    if archive is not None:
        assert cache_trace["first_decode_cutoff"]<REVISION_ARRIVAL-BASE-1
        assert cache_trace["14119"]["verified_decodes"]==cache_trace["14120"]["verified_decodes"]==2
        assert cache_trace["14120"]["reuses"]>cache_trace["14119"]["reuses"]
    return inspections,material,result,cache_trace


def test_in_window_confirmation_revision(canonical_successors,monkeypatch):
    """Freeze the expectation before evaluating any path: the revised high invalidates at arrival."""
    identities = count(1)
    monkeypatch.setattr("research_backend.app.strategy_routes.uuid4",lambda:UUID(int=next(identities)))
    snapshots,results = {},{}
    for name,high in (("original",25),("revised",31)):
        request,model,_ = canonical_successors(extra=249,confirmation_high=high)
        if name=="original":
            strategy = {key:request[key] for key in ("strategy_id","strategy_version_id","configuration_fingerprint")}
        else:
            request.update(strategy)
        run = prepare_historical(HistoricalInput.model_validate({**successor_request(request),"max_attempts":32}))
        assert run["identity"]["cutoff"]==END
        snapshots[name] = {key:run[key] for key in ("run_id","identity","selection")}
        paths = {mode:observe_revision_session(run,model,mode,high,monkeypatch) for mode in ("fresh","cached")}
        # Include complete terminal records, all exact references, per-cutoff
        # trace digests and the opportunity accounting checkpoint projection.
        assert paths["fresh"][:3]==paths["cached"][:3]
        for mode,(_,material,_,_) in paths.items():
            candidate,at = material["14119"],material["14120"]
            assert candidate["revision"]["state"]=="SMT_CANDIDATE"
            assert candidate["trace"]["entry_intent"] is None
            assert at["revision"]["market_event_id"]==candidate["revision"]["market_event_id"]
            assert at["revision"]["available_at"]==REVISION_ARRIVAL
            assert at["revision"]["state"]==("SMT_CONFIRMED" if name=="original" else "SMT_INVALIDATED")
            assert (at["trace"]["entry_intent"] is not None)==(name=="original")
            print(name,mode,"+14119",candidate["revision"]["state"],candidate["trace"]["state"],"intent",False,
                  "+14120",at["revision"]["state"],at["trace"]["state"],"intent",bool(at["trace"]["entry_intent"]))
        results[name] = paths

    original,revised = (results[name]["fresh"][0] for name in ("original","revised"))
    before = lambda rows: {key for key in rows if key<REVISION_ARRIVAL}
    assert before(original)==before(revised)
    semantic_fields = ("candles","market_events","strategy")
    for cutoff in sorted(before(original)):
        assert {key:original[cutoff][key] for key in semantic_fields}=={
            key:revised[cutoff][key] for key in semantic_fields},("early semantic divergence",cutoff-BASE)
    assert any(original[cutoff]["raw_events"]!=revised[cutoff]["raw_events"] for cutoff in before(original))
    assert snapshots["original"]["run_id"]!=snapshots["revised"]["run_id"]
    first_difference = min(cutoff for cutoff in original.keys() & revised.keys()
                           if any(original[cutoff][key]!=revised[cutoff][key] for key in semantic_fields))
    assert first_difference==REVISION_ARRIVAL
    print("matched earlier inspections",len(before(original)),"first semantic divergence",first_difference-BASE)
    output = Path(os.environ["TRADEFORGE_BENCH_OUTPUT"])
    evidence = {"snapshots":snapshots,"first_semantic_divergence":first_difference,"earlier_inspections":len(before(original)),
                "paths":{f"{name}/{mode}":{"material":material,"inspections":inspections,"cache":cache,
                         "result_digest":fingerprint(result)}
                         for name,paths in results.items() for mode,(inspections,material,result,cache) in paths.items()}}
    (output/"in-window-revision.json").write_text(json.dumps(evidence,indent=2)+"\n",encoding="utf-8")

    # Supply the original four-way test's independently verified artifacts to
    # the existing full-session recovery and publication-fence test.
    for mode,filename in (("fresh","fresh"),("cached","optimized")):
        inspections,_,(decisions,opportunities,accounting),_ = results["original"][mode]
        trace = []
        for cutoff,inspection in sorted(inspections.items()):
            if cutoff!=REVISION_ARRIVAL-1:
                trace.append([cutoff,inspection["raw_trace"],inspection["state"],inspection["reason"]])
        payload = {key:snapshots["original"][key] for key in ("identity","selection")} | {
            "trace":trace,"decisions":decisions,"opportunities":opportunities,"accounting":accounting}
        (output/f"{filename}.json").write_text(json.dumps(payload)+"\n",encoding="utf-8")
