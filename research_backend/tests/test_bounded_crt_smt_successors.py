"""A canonical, bounded three-opportunity CRT/SMT fixture and reference."""
from collections import Counter
import json
from time import perf_counter

import pytest

from research_backend.app.strategy_evaluation_sources import collect, freeze_selection, EvaluationInput
from research_backend.app.strategy_evaluator import CATALOG, EvaluationMachine, RuleGraph, predicate
from research_backend.app.strategy_evidence import fingerprint
from test_smt_causal import LEFT, RIGHT
from test_strategy_definitions import group
from test_strategy_evaluator import BASE, frozen_candles, ready_entry, request_body
from test_strategy_evaluator_integrity import selection

START = BASE+3600
END = BASE+15300
LATE = 20
# These boundaries are specified from the candle construction, not producer output.
ATTEMPTS = ((0,3600,7200,"EXPIRED"), (7200,10800,14120,"TRIGGER_QUALIFIED"),
            (10800,14400,15000,"INVALIDATED"))
CONFIRMATION_BAR = 46


def fixture_bars():
    primary = [(109,112,100,109)]*24+[(109,112,100,109)]*12
    primary[24] = (110,113,108,110)
    primary.extend((value+98,value+99,value+97,value+98) for value in LEFT)
    primary[42] = (112,115,112,112)
    primary[44] = (108,109,106,108)
    primary.extend([(113,116,108,113),(109,112,106,109),(109,112,107,109)])
    comparison = [(24,25,23,24)]*36
    comparison.extend((value,value+1,value-1,value) for value in RIGHT)
    comparison.extend([(24,25,23,24)]*3)
    return primary,comparison


def bounded_definition(delay=LATE):
    definition = ready_entry()
    definition["name"] = "Bounded bearish CRT and SMT successors"
    definition["instruments"]["comparison"]["modeled_delay_seconds"] = delay
    definition["timeframes"]["trigger"] = "M5"
    definition["indicators"][0]["settings"]["max_ranges"] = 4
    definition["indicators"][1]["timeframe"] = "M5"
    reclaim,divergence = definition["setup"]["children"]
    definition["setup"] = group("setup",reclaim)
    definition["trigger"]["children"].append(divergence)
    definition["expiry"] = {"unit":"SECONDS","count":3599}
    return definition


@pytest.fixture
def canonical_successors(client,monkeypatch):
    monkeypatch.setattr("research_backend.app.historical_strategy_worker.claim",lambda _:None)

    def build(delay=LATE,missing=False,extra=0,confirmation_high=None):
        lanes = []
        primary,comparison = fixture_bars()
        if confirmation_high is not None:
            open_,high,low,close = comparison[CONFIRMATION_BAR]
            assert high==25 and confirmation_high>=max(open_,close)
            comparison[CONFIRMATION_BAR] = (open_,confirmation_high,low,close)
        primary.extend([primary[-1]]*extra)
        comparison.extend([comparison[-1]]*extra)
        frozen_candles(lanes,"EURUSD","M5",BASE,primary)
        if missing:
            from research_backend.app.storage import write_partition
            feed = lanes[0]["feed_id"]
            rows = [dict(time=BASE+index*300,open=o,high=h,low=l,close=c,volume=0,closed=True)
                    for index,(o,h,l,c) in enumerate(comparison) if index!=CONFIRMATION_BAR]
            part = write_partition("DUKASCOPY",feed,"GBPUSD","candles","M5","BID",rows)
            lanes.append({**lanes[0],"symbol":"GBPUSD","partitions":[{key:part[key] for key in ("id","sha256")}]})
        else:
            frozen_candles(lanes,"GBPUSD","M5",BASE,comparison)
        end = END+300*extra
        archive = selection(monkeypatch,lanes,end-BASE)
        request = request_body(client,archive,bounded_definition(delay),end)
        request.update(evaluation_start=START)
        from research_backend.app.strategy_evaluation_routes import load_definition
        model = load_definition(EvaluationInput.model_validate(request))
        pinned = freeze_selection(EvaluationInput.model_validate(request),model)
        return request,model,pinned
    return build


def events(evidence,dependency,category):
    view = evidence.views[dependency]
    return evidence.events[view.snapshot].get(category,((),()))[1]


def reference_prefix(model,pinned,cutoff,identity,after=None):
    """Evaluate the fixture's fixed attempt window on a fresh exact prefix."""
    prefix = collect(model,pinned,cutoff,START)
    machine = EvaluationMachine(model,prefix,identity,START if after is None else after+1,END,
                                rearm_after=after)
    # Both schedules are derived directly from the documented nominal grid
    # and feed delays. No production cutoff enumeration is used.
    delay = model.instruments.comparison.modeled_delay_seconds
    due = {BASE+slot*300+arrival for slot in range(12,52) for arrival in (0,delay)}
    due.update({START,cutoff})
    lower = START if after is None else after+1
    previous = lower-1
    dynamic = set()
    while True:
        remaining = [stamp for stamp in due|dynamic if previous<stamp<=cutoff]
        if not remaining:
            break
        stamp = min(remaining)
        machine.step(stamp)
        previous = stamp
        if machine.state in {"EXPIRED","INVALIDATED","TRIGGER_QUALIFIED","NO_TRADE"}:
            break
        dynamic = set()
        if machine.setup_at is not None:
            dynamic.add(machine.setup_at+1)
        if machine.context_at is not None:
            dynamic.add(machine.context_at+3600)
    return machine.decision(cutoff,0),prefix


def test_independent_three_opportunities(canonical_successors):
    request,model,pin = canonical_successors()
    identity = {**request,"frozen_selection":fingerprint(pin)}
    before = None
    for start,activation,cutoff,state in ATTEMPTS:
        expected,evidence = reference_prefix(model,pin,BASE+cutoff,identity,before)
        print(cutoff,expected["state"],expected["context_activated_at"],expected["setup_activated_at"],expected["trigger_activated_at"],expected["reason"])
        assert expected["state"]==state
        assert expected["context_activated_at"]==BASE+activation
        assert bool(expected["entry_intent"])==(state=="TRIGGER_QUALIFIED")
        roots = {row["reference_start"]:row["range_id"] for row in events(evidence,"crt","crt") if row["state"]=="ACTIVE"}
        assert expected["bound_crt"]["evidence"]["family"]==roots[BASE+start]
        before = BASE+cutoff


def test_later_confirmation_and_missing_quote(canonical_successors):
    request,model,pin = canonical_successors()
    _,initial = reference_prefix(model,pin,BASE+13520,request,BASE+7200)
    candidate, = [row for row in exact_smt(initial) if row["state"]=="SMT_CANDIDATE"
                  and row["available_at"]==BASE+13520]
    family = initial.views["smt"].records[candidate["id"]].family
    assert family==candidate["market_event_id"]

    for offset,available,state,intent_count in (
        (14119,13520,"SMT_CANDIDATE",0), (14120,14120,"SMT_CONFIRMED",1),
    ):
        cutoff = BASE+offset
        decision,evidence = reference_prefix(model,pin,cutoff,request,BASE+7200)
        view = evidence.views["smt"]
        revision = max((row for row in exact_smt(evidence) if (record:=view.records[row["id"]]).family==family
                        and record.attested and record.available<=cutoff),key=lambda row:row["available_at"])
        record = view.records[revision["id"]]
        assert (revision["state"], record.available)==(state,BASE+available)
        # A published candidate is valid lifecycle lineage, even while the
        # current-state projection marks the pending comparison candle uncertain.
        projected_state,projection_reason = view.smt_state(family,cutoff)
        assert (projected_state,projection_reason)==(
            ("SMT_CONFIRMED",None) if intent_count else ("UNCERTAIN","MISSING_LAGGARD_OBSERVATION")
        )
        assert record.eligible==bool(intent_count)
        assert int(decision["entry_intent"] is not None)==intent_count
        if intent_count:
            assert decision["state"]=="TRIGGER_QUALIFIED"
            assert decision["context_activated_at"]==BASE+10800
            assert decision["trigger_activated_at"]==cutoff
            proof = revision["evidence_reference"]
            assert any(row["evidence_reference"]==proof for row in decision["supporting_events"].values())
            assert set(proof["participants"])=={"primary","comparison"}
            assert {key.split(":",1)[0] for key in revision["timing"]["dependency_references"]
                    if key.startswith(("primary:","comparison:"))}=={"primary","comparison"}
        else:
            assert revision["id"]==candidate["id"]
        print("SMT",offset,"family",family,"revision",revision["id"],state,"available",available,
              "strategy_eligible",record.eligible,"projection",projected_state,projection_reason,
              "decision",decision["state"],"intents",intent_count)
    for missing in (False,True):
        delay = LATE if missing else 0
        request,model,pin = canonical_successors(delay,missing)
        cutoff = 14120 if missing else 13800
        decision,_ = reference_prefix(model,pin,BASE+cutoff,request,BASE+7200)
        print("variant",missing,decision["state"],decision["reason"])
        assert bool(decision["entry_intent"]) is not missing
        if missing:
            assert decision["state"]=="INSUFFICIENT_EVIDENCE"

def test_exact_cutoff_root_and_nonmatching_reclaim(canonical_successors):
    request,model,pin = canonical_successors()
    terminal_cutoff = BASE+7200
    at_terminal,evidence = reference_prefix(model,pin,terminal_cutoff,request)
    roots = {row["reference_start"]:row for row in events(evidence,"crt","crt") if row["state"]=="ACTIVE"}
    a,b = roots[BASE],roots[BASE+3600]
    assert (at_terminal["bound_crt"]["evidence"]["family"],at_terminal["state"],at_terminal["cutoff"])==(
        a["range_id"],"EXPIRED",terminal_cutoff)
    assert b["available_at"]==at_terminal["cutoff"] and b["range_id"]!=a["range_id"]

    graph = RuleGraph(model)
    rule_id = graph.aliases["crt_reference"]
    rule = graph.rules[rule_id]

    def inspect(cutoff,prefix):
        # Use the actual rearm rule while keeping the spent set empty. This
        # isolates the terminal cutoff and the ACTIVE predicate in these checks.
        machine = EvaluationMachine(model,prefix,request,terminal_cutoff+1,END,
                                    rearm_after=terminal_cutoff,rearm_rule=rule_id)
        machine.step(cutoff)
        return machine.decision(cutoff,0)

    inspection_cutoff = terminal_cutoff+1
    first_prefix = collect(model,pin,inspection_cutoff,START)
    inspection = inspect(inspection_cutoff,first_prefix)
    assert inspection["attempt_id"] is None and inspection["bound_crt"] is None
    assert (rule.observation,rule.expected)==("CRT_REFERENCE","ACTIVE")
    assert predicate(rule,CATALOG[rule.observation],b) is True
    print("CRT terminal",7200,a["range_id"],at_terminal["state"],at_terminal["reason"],
          "B root",b["range_id"],b["id"],b["state"],b["available_at"]-BASE,
          "first successor",7201,inspection["state"],"bound",inspection["bound_crt"])

    cutoff = BASE+7500
    prefix = collect(model,pin,cutoff,START)
    inspection = inspect(cutoff,prefix)
    revisions = events(prefix,"crt","crt")
    revision, = [row for row in revisions if row["range_id"]==a["range_id"] and row["available_at"]==cutoff]
    view = prefix.views["crt"]
    record = view.records[revision["id"]]
    assert (record.family,record.state,record.available)==(a["range_id"],"HIGH_SWEPT_RECLAIMED",cutoff)
    assert not predicate(rule,CATALOG[rule.observation],revision)
    active_roots = Counter(row["range_id"] for row in revisions if row["state"]=="ACTIVE")
    assert active_roots[a["range_id"]]==1 and all(count==1 for count in active_roots.values())
    assert inspection["attempt_id"] is None and inspection["bound_crt"] is None and inspection["entry_intent"] is None
    print("CRT revision",7500,record.family,revision["id"],record.state,"available",record.available-BASE,
          "rearm",rule.observation,rule.expected,"predicate_match",False,"active_roots",active_roots[a["range_id"]],
          "spent_set_empty",True,"bound",inspection["bound_crt"])


def successor_request(request):
    return {**request,"rearm":"NEW_CONTEXT_FAMILY","context_alias":"crt_reference","max_attempts":4}


def exact_smt(evidence):
    return [row for row in events(evidence,"smt","smt") if row.get("direction")=="bearish"]


@pytest.mark.parametrize("interruption",[7200,14100])
def test_scheduler_equivalence_and_completion_gate(client,canonical_successors,monkeypatch,interruption):
    from research_backend.app import historical_strategy_engine as engine
    from research_backend.app import historical_strategy_store as store
    from research_backend.app.analysis_evidence import canonical
    from research_backend.app.db import one
    from research_backend.app.historical_strategy_inputs import HistoricalInput, prepare_historical, verified_evidence
    from test_historical_strategy_runs import API

    request,model,_ = canonical_successors()
    request = successor_request(request)
    validation = client.post(API+"/validate",json=request)
    assert validation.status_code==200,validation.text
    queued = client.post(API,json=request)
    assert queued.status_code==202,queued.text
    identifier = queued.json()["run_id"]
    run = prepare_historical(HistoricalInput.model_validate(request))
    assert run["run_id"]==identifier
    evidence = verified_evidence(model,run)
    seen = {}
    original = EvaluationMachine.decision

    def observe(machine,cutoff,boundaries):
        result = original(machine,cutoff,boundaries)
        if boundaries:
            seen[cutoff] = result,exact_smt(machine.evidence)
        return result

    monkeypatch.setattr(EvaluationMachine,"decision",observe)
    clock = perf_counter()
    def uninterrupted(calculation,chunk):
        entries,observations = [],[]
        def publish(snapshot,frontier,batch,completed,metrics):
            entries.extend(batch)
            observations.extend(snapshot[2] if len(snapshot)==3 else ())
        engine.run_chunks(calculation,evidence,model,publish,chunk_size=chunk)
        return entries,observations

    result,observations = uninterrupted(run,7)
    elapsed = perf_counter()-clock
    first_successor = min(stamp for stamp in seen if stamp>BASE+7200)
    first_trace = seen[first_successor][0]
    assert first_trace["attempt_id"] is None and first_trace["bound_crt"] is None
    print("first scheduled inspection",first_successor-BASE,first_trace["state"],"bound",first_trace["bound_crt"])
    primary_cutoffs = (3600,7200,7500,10800,12900,13500,13520,14100,14120,14400,14700,15000)
    fields = ("state","reason","bound_crt","crt_latest_reference","context_activated_at","setup_activated_at",
              "trigger_activated_at","phase_results","entry_intent","supporting_events","rules")
    for offset in primary_cutoffs:
        terminal = next((BASE+end for end in (15000,14120,7200) if end<offset),None)
        expected,prefix = reference_prefix(model,run["selection"],BASE+offset,run["identity"],terminal)
        actual,smt = seen[BASE+offset]
        assert {key:actual[key] for key in fields}=={key:expected[key] for key in fields},offset
        assert smt==exact_smt(prefix),offset
        crt = expected["bound_crt"]
        print("cutoff",offset,expected["state"],crt["evidence"]["family"] if crt else None,
              expected["reason"],expected["trigger_activated_at"],bool(expected["entry_intent"]))
    confirm = next(row for row in seen[BASE+14120][1] if row["state"]=="SMT_CONFIRMED")
    assert set(confirm["evidence_reference"]["participants"])=={"primary","comparison"}
    refs = confirm["timing"]["dependency_references"]
    assert any(ref.startswith("primary:") for ref in refs)
    assert any(ref.startswith("comparison:") for ref in refs)
    print("confirmation_exact_reference",confirm["evidence_reference"],"pivot_dependencies",refs)
    print("scheduler_cutoffs",len(seen),"elapsed_seconds",round(elapsed,3))
    assert [row["trace"]["state"] for row in result[:-1]]==["EXPIRED","TRIGGER_QUALIFIED","INVALIDATED"]
    assert sum(bool(item["intent"]) for item in result)==1
    # Build all four expected rows from the independently planned roots and
    # freshly collected exact prefixes, before inspecting the accounting output.
    expected = []
    for root,cutoff,disposition,reason in (
        (0,3600,"BOUND","ATTEMPT_BOUND"),
        (3600,7200,"SUPPRESSED","TERMINAL_BOUNDARY"),
        (7200,10800,"BOUND","ATTEMPT_BOUND"),
        (10800,14400,"BOUND","ATTEMPT_BOUND"),
    ):
        prefix = collect(model,run["selection"],BASE+cutoff,START)
        event, = [row for row in events(prefix,"crt","crt") if row["state"]=="ACTIVE"
                  and row["reference_start"]==BASE+root]
        assert event["available_at"]==BASE+cutoff
        expected.append((event["range_id"],BASE+cutoff,disposition,reason,event["evidence_reference"]))
    assert [(item["family"],item["cutoff"],item["disposition"],item["reason"],item["evidence_reference"])
            for item in observations]==expected
    assert len({item["id"] for item in observations})==4
    assert [item["attempt_id"] for item in observations if item["disposition"]=="BOUND"]==[
        entry["trace"]["attempt_id"] for entry in result if entry["trace"]["attempt_id"]]
    assert sum(item["reason"]=="TERMINAL_BOUNDARY" for item in observations)==1
    print("opportunity rows",[(item["family"],item["cutoff"]-BASE,item["disposition"],item["reason"])
                              for item in observations])

    if interruption==7200:
        print("B boundary proof",json.dumps(observations[1],sort_keys=True))
        for chunk in (1,17,93):
            assert uninterrupted(run,chunk)==(result,observations)
        legacy = {key:value for key,value in run.items() if key!="opportunity_accounting_version"}
        assert uninterrupted(legacy,7)==(result,[])
        # Repeat B's real accounting inspection deliberately. Its candidate
        # is still eligible for observation, and its family must remain unique.
        cutoff = BASE+7200
        prefix = collect(model,run["selection"],cutoff,START)
        machine = engine.make_machine(run,prefix,model)
        machine.run(cutoff)
        progress = {"families":{},"unresolved_inspections":0}
        repeated = []
        engine.account_cutoff(run,machine,cutoff,None,progress,repeated,lambda:None)
        engine.account_cutoff(run,machine,cutoff,None,progress,repeated,lambda:None)
        assert [item["family"] for item in repeated].count(expected[1][0])==1
    assert store.claim("first")==identifier

    def interrupt(snapshot,frontier,batch,completed,metrics):
        if interruption==7200 and frontier==BASE+7200:
            previous = store.latest_checkpoint(identifier)
            altered = [*snapshot[2],{**snapshot[2][0],"cutoff":frontier+1}]
            checkpoint = json.loads(snapshot[0])
            checkpoint["opportunity_accounting"]["batch_digest"] = fingerprint(altered)
            with pytest.raises(ValueError,match="Opportunity is outside"):
                store.publish(identifier,"first",(canonical(checkpoint),fingerprint(checkpoint),altered),
                              frontier,batch,completed,metrics)
            assert store.latest_checkpoint(identifier)==previous
            assert not one("SELECT id FROM historical_strategy_decisions WHERE run_id=? AND cutoff=?",
                           (identifier,frontier))
            assert not one("SELECT ordinal FROM historical_strategy_opportunities WHERE run_id=? AND cutoff=?",
                           (identifier,frontier))
        store.publish(identifier,"first",snapshot,frontier,batch,completed,metrics)
        if frontier==BASE+7200:
            store.publish(identifier,"first",snapshot,frontier,batch,completed,metrics)
            assert one("""SELECT COUNT(*) AS total FROM historical_strategy_opportunities
                          WHERE run_id=? AND reason='TERMINAL_BOUNDARY'""",(identifier,))["total"]==1
        if frontier==BASE+interruption:
            raise RuntimeError("interrupted after committing checkpoint")

    with pytest.raises(RuntimeError,match="after committing checkpoint"):
        engine.run_chunks(run,evidence,model,interrupt,chunk_size=1)
    store.finish(identifier,"first","FAILED","test interruption")
    assert store.latest_checkpoint(identifier)["frontier"]==BASE+interruption
    assert client.get(f"{API}/{identifier}/opportunities").json()==[]
    assert client.get(f"{API}/{identifier}").json()["opportunity_accounting"]=={"status":"NOT_PUBLISHED"}
    assert client.get(f"{API}/{identifier}/checkpoint").status_code==409
    assert client.get(f"{API}/{identifier}/decisions").json()==[]
    assert client.get(f"{API}/{identifier}/intents").json()==[]
    assert client.post(f"{API}/{identifier}/resume").status_code==200
    assert store.claim("resumed")==identifier
    engine.run_chunks(run,evidence,model,lambda *args:store.publish(identifier,"resumed",*args),
                      saved=store.latest_checkpoint(identifier),chunk_size=3)
    published = client.get(f"{API}/{identifier}/decisions").json()
    assert [item["id"] for item in published]==[row["id"] for row in result]
    intent, = client.get(f"{API}/{identifier}/intents").json()
    assert intent==next(item["intent"] for item in result if item["intent"])
    assert intent["status"]=="AWAITING_EXECUTION_VALIDATION"
    saved = client.get(f"{API}/{identifier}/decisions/{BASE+14120}").json()
    qualified = next(item["trace"] for item in result if item["intent"])
    assert saved["supporting_events"]==qualified["supporting_events"]
    page = client.get(f"{API}/{identifier}/opportunities",params={"limit":2}).json()
    tail = client.get(f"{API}/{identifier}/opportunities",params={"after":page[-1]["cursor"]}).json()
    assert len(page)==len(tail)==2
    assert page+tail==[{**item,"first":{key:item[key] for key in
        ("cutoff","disposition","reason","evidence_reference")}} for item in observations]
    summary = client.get(f"{API}/{identifier}").json()["opportunity_accounting"]
    assert summary["status"]=="COLLECTED" and summary["total"]==4
    assert summary["by_disposition"]=={"BOUND":3,"SUPPRESSED":1,"UNRESOLVED":0}
    assert summary["by_reason"]==[
        {"disposition":"BOUND","reason":"ATTEMPT_BOUND","total":3},
        {"disposition":"SUPPRESSED","reason":"TERMINAL_BOUNDARY","total":1},
    ]
    print("resumed after",interruption,"published",summary)


@pytest.mark.parametrize("case",["missing","zero-delay","too-long"])
def test_unsupported_or_incomplete_successors_rejected_before_queue(client,canonical_successors,case):
    from research_backend.app.db import one
    from test_historical_strategy_runs import API
    request,*_ = canonical_successors(delay=0 if case=="zero-delay" else LATE,missing=case=="missing",
                                      extra=50 if case=="too-long" else 0)
    expected = {"missing":"missing required candles","zero-delay":"Use SINGLE_ATTEMPT",
                "too-long":"exceeds 160 as-of cutoffs"}[case]
    for path in (API+"/validate",API):
        response = client.post(path,json=successor_request(request))
        print(case,response.status_code,response.json())
        assert response.status_code==422 and expected in response.text
    assert one("SELECT COUNT(*) AS total FROM historical_worker_state")["total"]==0


def test_offline_archive_cache_keeps_exact_prefix_and_public_limit(canonical_successors):
    from research_backend.app import historical_strategy_engine as engine
    from research_backend.app.historical_strategy_inputs import HistoricalInput, prepare_historical, verified_evidence
    from research_backend.app.offline_crt_smt_replay import ReplayArchive, run_offline
    from test_historical_strategy_runs import execute

    request,model,_ = canonical_successors()
    run = prepare_historical(HistoricalInput.model_validate(successor_request(request)))
    horizon = verified_evidence(model,run)
    original,checkpoints = execute(run,model,horizon,17)
    archive = ReplayArchive()
    actual,observed = [],[]
    def publish(snapshot,cutoff,entries,done,metrics):
        actual.extend(entries)
    run_offline(run,horizon,model,publish,archive=archive,chunk_size=17,
                observe=lambda cutoff,decision:observed.append(cutoff))
    assert actual==original
    assert len(observed)==json.loads(checkpoints[-1]["payload"])["processed"]
    assert archive.archive.counts["partition_decodes"]==2
    assert run["identity"]["budgets"]["oracle_cutoffs"]==160

    for offset,qualified in ((14119,False),(14120,True)):
        cutoff = BASE+offset
        selection = engine.asof_selection(run["selection"],cutoff)
        a = collect(model,selection,cutoff,START)
        b = collect(model,selection,cutoff,START,replay_archive=archive)
        assert a.events==b.events and a.coverage==b.coverage and a.metrics==b.metrics
        prior = original[0]["trace"]["bound_crt"]["evidence"]["family"]
        decisions = [engine.make_machine(run,prefix,model,BASE+7200,[prior]).run(cutoff) for prefix in (a,b)]
        assert decisions[0]==decisions[1]
        assert bool(decisions[0]["entry_intent"])==qualified

    from research_backend.app import storage
    from research_backend.app.db import one
    primary = run["selection"]["series"]["PRIMARY:M5"]["partitions"][0]
    stored = one("SELECT relative_path FROM datasets WHERE id=?",(primary["id"],))
    with (storage.MARKET_DIR/stored["relative_path"]).open("ab") as stream:
        stream.write(b"corrupted offline source")
    with pytest.raises(ValueError,match="failed verification"):
        collect(model,selection,cutoff,START,replay_archive=ReplayArchive())
