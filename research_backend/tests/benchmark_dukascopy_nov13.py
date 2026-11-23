"""Opt-in November 13 validation against the user's local approved provider archive."""
from collections import Counter
from hashlib import file_digest
from itertools import count
import json
import os
from pathlib import Path
from time import perf_counter
from uuid import UUID

import pytest

from scripts.verify_real_strategy_evaluation import CATALOG, ROOT, copy_sources, independent_references
from benchmark_offline_crt_smt import measured, modeled_arrivals
from research_backend.app import historical_strategy_store as store, storage
from research_backend.app.analysis_evidence import ResourceLimit
from research_backend.app.db import one, transaction
from research_backend.app.historical_strategy_engine import successor_schedule
from research_backend.app.historical_strategy_inputs import HistoricalInput, verified_evidence
from research_backend.app.indicator_coverage import MarketSchedule, open_intervals
from research_backend.app.offline_crt_smt_replay import NOV13_FINGERPRINT, NOV13_WINDOW, prepare_nov13_offline, run_offline
from research_backend.app.strategy_definitions import StrategyDefinitionV2
from research_backend.app.strategy_evidence import fingerprint
from test_bounded_crt_smt_successors import bounded_definition
from test_historical_strategy_runs import API

START, BEGIN, END = NOV13_WINDOW
STEP = 300


def output_dir():
    return Path(os.environ["TRADEFORGE_BENCH_OUTPUT"])


def emit(name,payload):
    (output_dir()/f"{name}.json").write_text(json.dumps(payload,indent=2)+"\n",encoding="utf-8")


def read(name):
    return json.loads((output_dir()/f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture
def pinned(client,monkeypatch,tmp_path,pytestconfig):
    monkeypatch.setattr("research_backend.app.historical_strategy_worker.claim",lambda _:None)
    ids = count(1)
    monkeypatch.setattr("research_backend.app.strategy_routes.uuid4",lambda:UUID(int=next(ids)))
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    assert fingerprint(catalog)==NOV13_FINGERPRINT
    assert (catalog["start"],catalog["end"])==(START,END)
    lanes = [lane for lane in catalog["series"] if lane["timeframe"]=="M5"]
    assert {lane["symbol"] for lane in lanes}=={"EURUSD","GBPUSD"}
    configured = pytestconfig.tradeforge_previous_storage
    original_storage = Path(configured["TRADEFORGE_STORAGE"] or ROOT/"storage")
    original = Path(configured["TRADEFORGE_DATA_DIR"] or original_storage/"research").resolve()
    datasets = copy_sources({**catalog,"series":lanes},original,tmp_path)
    isolated = tmp_path/"research"/"market"
    monkeypatch.setattr(storage,"MARKET_DIR",isolated)
    with transaction() as db:
        for item in datasets:
            db.execute("INSERT INTO datasets VALUES("+",".join("?" for _ in item)+")",tuple(item.values()))
    definition = bounded_definition()
    definition["instruments"]["calendar"] = {"kind":"forex_weekly"}
    definition["name"] = "November 13 offline CRT and SMT successors"
    created = client.post("/api/strategies/v1",json=definition)
    assert created.status_code==201,created.text
    strategy = created.json()
    request = {"evaluator_version":6,"purpose":"HISTORICAL_SIMULATION",
        "strategy_id":strategy["strategy_id"],"strategy_version_id":strategy["version_id"],
        "configuration_fingerprint":strategy["configuration_fingerprint"],
        "selection_id":catalog["id"],"selection_fingerprint":NOV13_FINGERPRINT,
        "warmup_start":START,"evaluation_start":BEGIN,"cutoff":END,
        "rearm":"NEW_CONTEXT_FAMILY","context_alias":"crt_reference","max_attempts":32}
    run = prepare_nov13_offline(HistoricalInput.model_validate(request))
    model = StrategyDefinitionV2.model_validate(run["definition"])
    yield request,run,model,datasets
    for item in datasets:
        with (original/"market"/item["relative_path"]).open("rb") as stream:
            assert file_digest(stream,"sha256").hexdigest()==item["sha256"]


def inspect_archive(pinned):
    import pyarrow.parquet as pq

    _,run,_,datasets = pinned
    coverage,raw = {},{}
    by_symbol = {item["symbol"]:item for item in datasets}
    for key,feed in run["selection"]["series"].items():
        item = by_symbol[feed["symbol"]]
        assert (item["provider"],item["kind"],item["feed_id"],item["timeframe"],item["price_basis"])==(
            "DUKASCOPY","candles",feed["feed_id"],"M5","BID")
        path = storage.MARKET_DIR/item["relative_path"]
        with path.open("rb") as stream:
            assert file_digest(stream,"sha256").hexdigest()==feed["partitions"][0]["sha256"]==item["sha256"]
        rows = pq.read_table(path).to_pylist()
        assert len(rows)==item["rows_count"]
        intervals = open_intervals(START,END,MarketSchedule(**feed["calendar"]))
        assert intervals==[(START,END)]
        expected = [stamp for left,right in intervals for stamp in range(left,right,STEP)]
        source = {row["time"]:row for row in rows if START<=row["time"]<END}
        assert sorted(source)==expected and len(expected)==len(rows)==264
        assert all(row.get("closed",True) is True and row.get("complete_m1") is not False for row in source.values())
        delay = feed["delay_seconds"]
        eligible = [stamp for stamp in expected if stamp+STEP+delay<=END]
        warm = [stamp for stamp in expected if stamp+STEP+delay<=BEGIN]
        assert len(warm)==(24 if delay==0 else 23) and len(warm)>=12
        last = eligible[-1]
        assert last==END-STEP*(1 if delay==0 else 2)
        coverage[key] = {"calendar":feed["calendar"],"open_intervals":intervals,"slots":len(expected),
                         "warmup_observed":len(warm),"eligible_at_cutoff":len(eligible),
                         "last_eligible_open":last,"last_eligible_arrival":last+STEP+delay,
                         "last_archive_open":expected[-1],"last_archive_arrival":expected[-1]+STEP+delay,
                         "partition":feed["partitions"][0],"feed":feed["feed_id"]}
        raw[key] = source
    return coverage,raw


def test_pin_nov13(pinned):
    request,run,_,_ = pinned
    coverage,_ = inspect_archive(pinned)
    emit("pin",{"request":request,"run":run,"coverage":coverage})
    print("PIN",run["run_id"],json.dumps(coverage,sort_keys=True))


def test_measure_nov13(pinned,monkeypatch):
    _,run,model,_ = pinned
    # Reject any drift from the preflight written before either outcome run.
    assert run==read("pin")["run"]
    path = os.environ["TRADEFORGE_BENCH_PATH"]
    assert path in {"fresh","optimized"}
    fields = []
    def inspect(cutoff,trace):
        fields.append({key:fingerprint(value) for key,value in trace.items()})
    cold,trace,result = measured(path,run,model,monkeypatch,inspect=inspect)
    warm,again,repeated = measured(path,run,model,monkeypatch)
    assert (trace,result)==(again,repeated)
    assert 160<cold["cutoffs"] and trace[-1][0]==END
    decisions,opportunities,accounting = result
    emit(path,{"trace":trace,"fields":fields,"decisions":decisions,"opportunities":opportunities,
               "accounting":accounting,"cold":cold,"warm":warm})
    print(path,"cold",cold,"warm",warm)
    print(path,"terminal decisions",dict(Counter(item["trace"]["reason"] for item in decisions)),
          "intents",sum(bool(item["intent"]) for item in decisions))


def compare(fresh,cached):
    schedule = [[row[0] for row in data["trace"]] for data in (fresh,cached)]
    if schedule[0]!=schedule[1]:
        for i,(a,b) in enumerate(zip(*schedule)):
            if a!=b:
                pytest.fail(f"First differing ordered cutoff at index {i}: fresh {a}, cached {b}")
        pytest.fail(f"First differing cutoff after the shared prefix: {schedule[0][len(schedule[1]):][:1]} / "
                    f"{schedule[1][len(schedule[0]):][:1]}")
    for index,(a,b) in enumerate(zip(fresh["trace"],cached["trace"])):
        if a!=b:
            left,right = fresh["fields"][index],cached["fields"][index]
            pytest.fail(f"First differing trace at cutoff {a[0]}: {[key for key in left if left[key]!=right.get(key)]}")
    for key in ("decisions","opportunities"):
        left,right = fresh[key],cached[key]
        for a,b in zip(left,right):
            if a!=b:
                cutoff = a.get("cutoff") or a["trace"]["cutoff"]
                changed = next(field for field in a if a[field]!=b.get(field))
                pytest.fail(f"First differing {key} at cutoff {cutoff}: {changed}")
        if len(left)!=len(right):
            extra = (left[len(right):] or right[len(left):])[0]
            cutoff = extra.get("cutoff") or extra["trace"]["cutoff"]
            pytest.fail(f"First differing {key} at cutoff {cutoff}: extra row")
    assert fresh["accounting"]==cached["accounting"],f"Accounting summary diverged at cutoff {END}"


def inspect_smt(decisions,raw,run):
    """Independently check the accepted confirmation candles and publication clock."""
    verified = []
    for item in decisions:
        if item["intent"] is None:
            continue
        trace = item["trace"]
        rules = [row for row in trace["rules"] if row["id"]=="divergence" and row["evidence_reference"]]
        for rule in rules:
            ref = rule["evidence_reference"]
            event = trace["supporting_events"][ref["calculation_snapshot"]+":"+ref["event_id"]]
            assert event["state"]=="SMT_CONFIRMED"
            timing = event["timing"]
            prior = timing["dependency_references"]["smt:previous"]
            opened = list(range(timing["observation_start"],timing["observation_end"],STEP))
            arrivals = {key:[raw[key][stamp]["time"]+STEP+feed["delay_seconds"] for stamp in opened]
                        for key,feed in run["selection"]["series"].items()}
            greatest = max(prior["available_at"],*(ts for lane in arrivals.values() for ts in lane))
            assert greatest==ref["available_at"]<=trace["trigger_activated_at"]<=END
            verified.append({"event_id":ref["event_id"],"confirmation_opens":opened,"modeled_arrivals":arrivals,
                             "available_at":greatest,"qualification":trace["trigger_activated_at"]})
        assert rules,"Qualified intent must include a confirmed SMT proof"
    return verified


def test_nov13_equivalence_and_recovery(client,pinned):
    request,run,model,datasets = pinned
    assert run==read("pin")["run"]
    coverage,raw = inspect_archive(pinned)
    assert fingerprint(coverage)==fingerprint(read("pin")["coverage"])
    fresh,cached = read("fresh"),read("optimized")
    compare(fresh,cached)
    reference = fresh["decisions"]
    assert len(fresh["trace"])>160
    horizon = verified_evidence(model,run)
    for lane,verified in coverage.items():
        assert horizon.coverage[lane].at(END)["observed"]==verified["eligible_at_cutoff"]
        assert horizon.coverage[lane].observed[-1]==verified["last_eligible_arrival"]
    assert END+20 in modeled_arrivals(run["selection"])
    assert all(row[0]<=END for row in fresh["trace"])
    assert run["identity"]["method"]=="CRT_SMT_NOV13_OFFLINE_V1"

    first_bound = next(item["trace"] for item in reference if item["trace"]["bound_crt"])
    crt = independent_references(first_bound,datasets,storage.MARKET_DIR,0)
    assert crt
    smt = inspect_smt(reference,raw,run)
    nonqualifying = [item["trace"] for item in reference if not item["intent"]]
    zero_intent_reasons = dict(Counter(trace["reason"] for trace in nonqualifying))
    smt_at_terminal = dict(Counter(rule["reason"] for trace in nonqualifying for rule in trace["rules"]
                                   if rule["id"]=="divergence"))
    setup_activations = sum(trace["setup_activated_at"] is not None for trace in nonqualifying)
    # Production still rejects the forex-weekly scope, and its independent
    # cutoff guard also rejects the frozen run before any ledger work.
    for suffix in ("/validate",""):
        response = client.post(API+suffix,json=request)
        assert response.status_code==422 and "Use SINGLE_ATTEMPT" in response.text
    assert one("SELECT COUNT(*) AS total FROM historical_strategy_runs")["total"]==0
    with pytest.raises(ResourceLimit,match="160 as-of cutoffs"):
        successor_schedule(run,horizon,model)

    frontier = first_bound["cutoff"]
    assert frontier<END
    identifier = store.enqueue(run)
    observed = []
    def observe(cutoff,trace):
        observed.append([cutoff,fingerprint(trace),trace["state"],trace["reason"]])
    def heartbeat(owner):
        next_refresh = 0.0
        def check():
            nonlocal next_refresh
            now = perf_counter()
            if now>=next_refresh:
                assert store.heartbeat(identifier,owner)
                next_refresh = now+2
        return check
    assert store.claim("interrupted")==identifier
    def interrupt(snapshot,cutoff,entries,done,metrics):
        store.publish(identifier,"interrupted",snapshot,cutoff,entries,done,metrics)
        if cutoff==frontier:
            store.publish(identifier,"interrupted",snapshot,cutoff,entries,done,metrics)
            raise RuntimeError("deliberate material interruption")
    with pytest.raises(RuntimeError,match="deliberate material interruption"):
        run_offline(run,horizon,model,interrupt,chunk_size=1,check=heartbeat("interrupted"),observe=observe)
    saved = store.latest_checkpoint(identifier)
    assert saved["frontier"]==frontier
    store.finish(identifier,"interrupted","FAILED","offline validation interrupted")
    for ledger in ("decisions","intents","opportunities"):
        assert client.get(f"{API}/{identifier}/{ledger}").json()==[]
    assert client.get(f"{API}/{identifier}/checkpoint").status_code==409
    assert client.get(f"{API}/{identifier}").json()["opportunity_accounting"]=={"status":"NOT_PUBLISHED"}
    assert client.post(f"{API}/{identifier}/resume").status_code==200
    assert store.claim("resumed")==identifier
    run_offline(run,horizon,model,lambda *args:store.publish(identifier,"resumed",*args),
                saved=saved,chunk_size=23,check=heartbeat("resumed"),observe=observe)
    assert observed==fresh["trace"]
    decisions = client.get(f"{API}/{identifier}/decisions",params={"limit":100}).json()
    assert [row["id"] for row in decisions]==[item["id"] for item in reference]
    for item in reference:
        cutoff = item["trace"]["cutoff"]
        assert client.get(f"{API}/{identifier}/decisions/{cutoff}").json()=={"decision_id":item["id"],**item["trace"]}
    assert client.get(f"{API}/{identifier}/intents").json()==[item["intent"] for item in reference if item["intent"]]
    first,last = {},{}
    for row in fresh["opportunities"]:
        first.setdefault(row["id"],{key:row[key] for key in ("cutoff","disposition","reason","evidence_reference")})
        last[row["id"]] = row
    api_opportunities = client.get(f"{API}/{identifier}/opportunities",params={"limit":100}).json()
    assert api_opportunities==[{**row,"first":first[key]} for key,row in last.items()]
    summary = client.get(f"{API}/{identifier}").json()["opportunity_accounting"]
    assert summary["total"]==len(last)
    assert summary["unresolved_by_reason"]==fresh["accounting"].get("unresolved_by_reason",{})
    emit("verification",{"run_id":identifier,"ordered_cutoffs":len(observed),"first_divergence":None,
        "ledger_digest":fingerprint(reference),"opportunity_digest":fingerprint(fresh["opportunities"]),
        "independent_crt":crt,"independent_smt_confirmations":smt,"nonqualifying_terminal_reasons":zero_intent_reasons,
        "nonqualifying_setup_activations":setup_activations,"nonqualifying_terminal_smt_reasons":smt_at_terminal,
        "intents":sum(bool(item["intent"]) for item in reference),"opportunity_summary":summary,
        "recovery":{"interrupted_frontier":frontier,"retry_idempotent":True,
                    "resume_chunk_size":23,"exact_published_records":True,"partial_results_private":True},
        "public":{"validate":422,"enqueue":422,"runs_created":0,"cutoff_budget":160}})
    print("exact cutoff comparison",len(observed),"first divergence",None,
          "material interruption",frontier,"CRT checks",len(crt),"SMT checks",len(smt),
          "nonqualifying terminal reasons",zero_intent_reasons)


def test_nov13_market_entry_negative_control(client,pinned):
    from research_backend.app.offline_market_entry import validate_market_entries

    _,run,model,datasets = pinned
    evidence_dir = ROOT/"docs"/"implementation"/"evidence"/"TF-I10A1-dukascopy-nov13"
    approved_pin = json.loads((evidence_dir/"pin.json").read_text(encoding="utf-8"))
    approved_result = json.loads((evidence_dir/"verification.json").read_text(encoding="utf-8"))
    assert run==approved_pin["run"]
    coverage,_ = inspect_archive(pinned)
    assert fingerprint(coverage)==fingerprint(approved_pin["coverage"])
    identifier = store.enqueue(run)
    owner = "nov13-offline-market-entry"
    assert store.claim(owner)==identifier
    entries = []
    last_refresh = 0.0

    def renew():
        nonlocal last_refresh
        now = perf_counter()
        if now-last_refresh>=2:
            assert store.heartbeat(identifier,owner)
            last_refresh = now

    def commit(snapshot,cutoff,batch,completed,metrics):
        store.publish(identifier,owner,snapshot,cutoff,batch,completed,metrics)
        entries.extend(batch)

    run_offline(run,verified_evidence(model,run),model,commit,chunk_size=23,check=renew)
    assert fingerprint(entries)==approved_result["ledger_digest"]
    before = client.get(f"{API}/{identifier}/decisions",params={"limit":100}).json()
    assert len(before)==14
    assert client.get(f"{API}/{identifier}/intents").json()==[]
    expected = {"run_id":identifier,"historical_attempts":14,"qualified_intents":0,
                "simulated_orders":0,"simulated_fills":0,"results":[]}
    assert validate_market_entries(identifier)==expected
    assert validate_market_entries(identifier)==expected
    assert client.get(f"{API}/{identifier}/decisions",params={"limit":100}).json()==before
    assert client.get(f"{API}/{identifier}/intents").json()==[]
    assert one("SELECT COUNT(*) AS total FROM offline_market_entry_results")["total"]==0
    print(json.dumps({"execution":expected,"historical_ledger_sha256":fingerprint(entries),
                      "archive_catalog_sha256":NOV13_FINGERPRINT,
                      "partition_sha256":{item["symbol"]:item["sha256"] for item in datasets}},sort_keys=True))
