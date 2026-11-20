"""A canonical, bounded three-opportunity CRT/SMT fixture and reference."""
from time import perf_counter

import pytest

from research_backend.app.strategy_evaluation_sources import collect, freeze_selection, EvaluationInput
from research_backend.app.strategy_evaluator import EvaluationMachine
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

    def build(delay=LATE,missing=False,extra=0):
        lanes = []
        primary,comparison = fixture_bars()
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
    for cutoff in (13520,14119,14120):
        decision,evidence = reference_prefix(model,pin,BASE+cutoff,request,BASE+7200)
        bearish = [(row["state"],row["available_at"]-BASE) for row in events(evidence,"smt","smt")
                   if row.get("direction")=="bearish"]
        print(cutoff,decision["state"],bearish)
        assert bool(decision["entry_intent"])==(cutoff==14120)
        assert any(state=="SMT_CONFIRMED" and since==14120 for state,since in bearish)==(cutoff==14120)
    for missing in (False,True):
        delay = LATE if missing else 0
        request,model,pin = canonical_successors(delay,missing)
        cutoff = 14120 if missing else 13800
        decision,_ = reference_prefix(model,pin,BASE+cutoff,request,BASE+7200)
        print("variant",missing,decision["state"],decision["reason"])
        assert bool(decision["entry_intent"]) is not missing
        if missing:
            assert decision["state"]=="INSUFFICIENT_EVIDENCE"

def test_exact_cutoff_and_spent_rearm_rejections(canonical_successors):
    request,model,pin = canonical_successors()
    at_terminal,evidence = reference_prefix(model,pin,BASE+7200,request)
    roots = {row["reference_start"]:row for row in events(evidence,"crt","crt") if row["state"]=="ACTIVE"}
    spent_a,b = roots[BASE],roots[BASE+3600]
    assert (at_terminal["bound_crt"]["evidence"]["family"],at_terminal["state"])==(spent_a["range_id"],"EXPIRED")
    assert b["available_at"]==BASE+7200 and b["range_id"]!=spent_a["range_id"]
    inspection,_ = reference_prefix(model,pin,BASE+7201,request,BASE+7200)
    assert inspection["attempt_id"] is None
    inspection,prefix = reference_prefix(model,pin,BASE+7500,request,BASE+7200)
    revision = next(row for row in events(prefix,"crt","crt") if row["range_id"]==spent_a["range_id"]
                    and row["available_at"]==BASE+7500)
    assert revision["state"]=="HIGH_SWEPT_RECLAIMED"
    assert inspection["attempt_id"] is None and inspection["entry_intent"] is None
    print("rejected",b["range_id"],7200,"AT_TERMINAL_CUTOFF",
          revision["range_id"],7500,revision["state"],"SPENT_FAMILY")


def successor_request(request):
    return {**request,"rearm":"NEW_CONTEXT_FAMILY","context_alias":"crt_reference","max_attempts":4}


def exact_smt(evidence):
    return [row for row in events(evidence,"smt","smt") if row.get("direction")=="bearish"]


def test_scheduler_equivalence_and_completion_gate(client,canonical_successors,monkeypatch):
    from research_backend.app import historical_strategy_engine as engine
    from research_backend.app import historical_strategy_store as store
    from research_backend.app.historical_strategy_inputs import HistoricalInput, prepare_historical, verified_evidence
    from test_historical_strategy_runs import API, execute

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
    result,checkpoints = execute(run,model,evidence,7)
    elapsed = perf_counter()-clock
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
    for chunk in (1,17,93):
        assert execute(run,model,evidence,chunk)[0]==result
    assert store.claim("first")==identifier

    def interrupt(snapshot,frontier,batch,completed,metrics):
        store.publish(identifier,"first",snapshot,frontier,batch,completed,metrics)
        if frontier==BASE+14100:
            raise RuntimeError("interrupted before confirming peer candle")

    with pytest.raises(RuntimeError,match="before confirming peer"):
        engine.run_chunks(run,evidence,model,interrupt,chunk_size=1)
    store.finish(identifier,"first","FAILED","test interruption")
    assert store.latest_checkpoint(identifier)["frontier"]==BASE+14100
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
    assert saved["supporting_events"]==seen[BASE+14120][0]["supporting_events"]


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
