"""Exercise the accepted real archive in a disposable copy of its exact four partitions."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
from statistics import median, quantiles
from tempfile import TemporaryDirectory
from time import perf_counter
import tracemalloc

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT/"research_backend"/"app"/"strategy_sources.json"


def copy_sources(catalog, original, temp):
    requested = {part["id"]:part["sha256"] for item in catalog["series"] for part in item["partitions"]}
    with sqlite3.connect((original/"tradeforge.sqlite3").as_uri()+"?mode=ro",uri=True) as database:
        database.row_factory = sqlite3.Row
        datasets = [dict(database.execute("SELECT * FROM datasets WHERE id=?",(identifier,)).fetchone()) for identifier in requested]
    for dataset in datasets:
        path = (original/"market"/dataset["relative_path"]).resolve()
        if not path.is_relative_to((original/"market").resolve()):
            raise ValueError("Archive path outside workspace")
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream,"sha256").hexdigest()
        if digest != requested[dataset["id"]] or digest != dataset["sha256"]:
            raise ValueError("Frozen archive failed checksum")
        target = temp/"research"/"market"/dataset["relative_path"]
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(path,target)
    return datasets


def demo(direction, primary_delay, peer_delay):
    from research_backend.app.strategy_routes import template_v2
    strategy = template_v2(direction)
    strategy["name"] = f"TF-I09B research fixture {direction} {primary_delay}/{peer_delay}"
    strategy["status"] = "READY_FOR_EVALUATION"
    strategy["timeframes"]["trigger"] = "M1"
    strategy["indicators"][1]["timeframe"] = "M1"
    strategy["instruments"]["primary"]["modeled_delay_seconds"] = primary_delay
    strategy["instruments"]["comparison"]["modeled_delay_seconds"] = peer_delay
    strategy["trade_intent"].update(entry={"kind":"MARKET"},expiry={"unit":"SECONDS","count":300})
    strategy["expiry"] = {"unit":"SECONDS","count":7200}
    strategy["execution"] = {"price_basis":"BID","spread_bps":1,"slippage_bps":0,
                             "commission_bps":0,"intrabar":"REJECT_AMBIGUOUS"}
    strategy["risk"] = {"account_risk_pct":0.5,"max_simultaneous_exposure_pct":1,"max_session_loss_pct":2,
                        "min_reward_risk":2,"instrument_limits":[{"role":"PRIMARY","max_risk_percent":1}]}
    strategy["management"] = {"initial_stop":{"kind":"FIXED_PIPS","distance":10},
                              "target":{"kind":"REWARD_MULTIPLE","multiple":2}}
    rule = {**strategy["setup"]["children"][0],"alias":None,"observation":"CRT_STATE"}
    strategy["trigger"] = {"type":"GROUP","id":"trigger","operator":"ALL","children":[
        {**rule,"id":"expand","expected":"EXPANSION_OBSERVED"}]}
    strategy["invalidation"] = {"type":"GROUP","id":"invalidation","operator":"ALL","children":[
        {**rule,"id":"completed","expected":"OPPOSITE_BOUNDARY_REACHED"}]}
    return strategy


def independent_references(decision, datasets, archive, delay):
    import pyarrow.parquet as pq
    proof = decision.get("bound_crt")
    if not proof:
        return []
    ref = proof["evidence"]["reference"]
    observation = decision["supporting_events"][ref["calculation_snapshot"]+":"+ref["event_id"]]
    starts = range(observation["reference_start"],observation["reference_end"],300)
    records = {}
    for dataset in datasets:
        if dataset["symbol"]=="EURUSD" and dataset["timeframe"]=="M5":
            records.update((row["time"],row) for row in pq.read_table(archive/dataset["relative_path"]).to_pylist())
    members = [records[stamp] for stamp in starts]
    assert len(members)==12
    quote = [members[0]["open"],max(row["high"] for row in members),min(row["low"] for row in members),
             members[-1]["close"]]
    assert quote==observation["reference_ohlc"]
    assert ref["available_at"]==members[-1]["time"]+300+delay<=proof["activated_at"]
    assert proof["activated_at"]==decision["context_activated_at"]
    return [{"evidence_id":ref["event_id"],"checked_constituents":len(members),
             "complete_ohlc_match":True,"availability_match":True,
             "expected_available_at":members[-1]["time"]+300+delay,"actual_activation":proof["activated_at"]}]


def measure(call,samples):
    for _ in range(3):
        call()
    elapsed = []
    for _ in range(samples):
        start = perf_counter()
        result = call()
        elapsed.append((perf_counter()-start)*1000)
    tracemalloc.start()
    try:
        call()
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    output = result if isinstance(result,dict) else None
    return {"samples":samples,"p50_ms":round(median(elapsed),3),
            "p95_ms":round(quantiles(elapsed,n=100,method="inclusive")[94],3),
            "peak_python_bytes":peak,"response_bytes":len(json.dumps(output).encode()) if output else None,
            "resource_counts":output.get("resource_counts") if output else None}


def run(catalog,datasets,samples):
    from fastapi.testclient import TestClient
    from research_backend.app.config import MARKET_DIR
    from research_backend.app.db import initialize, transaction
    from research_backend.app.main import app
    from research_backend.app.strategy_definitions import StrategyDefinitionV2, validate_definition
    from research_backend.app.strategy_evaluation_routes import calculate, load_run
    from research_backend.app.strategy_evaluation_sources import collect
    from research_backend.app.strategy_evaluator import RuleGraph

    initialize()
    with transaction() as database:
        for item in datasets:
            database.execute("INSERT INTO datasets VALUES("+",".join("?" for _ in item)+")",tuple(item.values()))
    prefix="/api/strategy-evaluations/v1"
    cases=[]
    runs={}
    with TestClient(app) as client:
        selected=client.get(prefix+"/selections").json()["selections"][0]

        def store(definition, cutoff=None):
            record=client.post("/api/strategies/v1",json=definition)
            assert record.status_code==201,record.text
            item=record.json()
            request={"evaluator_version":1,"purpose":"HISTORICAL_SIMULATION","strategy_id":item["strategy_id"],
                     "strategy_version_id":item["version_id"],"configuration_fingerprint":item["configuration_fingerprint"],
                     "selection_id":selected["id"],"selection_fingerprint":selected["fingerprint"],
                     "warmup_start":catalog["start"],"evaluation_start":catalog["start"]+7200,"cutoff":cutoff if cutoff is not None else catalog["end"]}
            response = client.post(prefix,json=request)
            assert response.status_code==201,response.text
            return request,response.json()

        for direction in ("bullish","bearish"):
            for left,right in ((0,0),(2,2),(2,15),(15,30)):
                request,run=store(demo(direction,left,right))
                decision=run["decision"]
                replay=client.post(prefix+"/"+run["run_id"]+"/reconstruct",json={"cutoff":request["cutoff"]}).json()
                assert decision["decision_id"]==replay["decision_id"]
                check=independent_references(decision,datasets,MARKET_DIR,left)
                del decision["supporting_events"]  # No provider candle payload in committed evidence.
                cases.append({"fixture":request,"direction":direction,"delays":[left,right],
                              "independent_reference_checks":check,"same_cutoff_reconstruction":True,
                              "decision":decision})
                runs[(direction,left,right)] = load_run(run["run_id"])

        benchmarks = {}
        if samples:
            canonical=runs[("bearish",0,0)]
            benchmark_definition = StrategyDefinitionV2.model_validate(canonical["definition"])
            benchmarks["strategy_compilation"] = measure(lambda:RuleGraph(benchmark_definition),samples)
            benchmarks["full_session_crt_smt"] = measure(lambda:calculate(canonical,catalog["end"]),samples)
            benchmarks["reconstruction_early"] = measure(lambda:calculate(canonical,catalog["start"]+12600),samples)
            single=demo("bearish",0,0)
            single["indicators"].pop()
            single["timeframes"]["trigger"] = "M5"
            single["setup"]["children"].pop()
            single["setup"]["operator"]="ALL"
            single["temporal"]=[]
            single["instruments"]["comparison"]=None
            _,entered=store(single)
            single=load_run(entered["run_id"])
            benchmarks["one_instrument_one_frame"] = measure(lambda:calculate(single,catalog["end"]),samples)
            multi=demo("bearish",0,0)
            multi["indicators"].extend([
                {"id":"base-structure","indicator":"STRUCTURE","algorithm_version":2,"role":"PRIMARY","timeframe":"M5","settings":{}},
                {"id":"fvg","indicator":"FVG","algorithm_version":1,"role":"PRIMARY","timeframe":"M5","settings":{}},
                {"id":"liquidity","indicator":"LIQUIDITY","algorithm_version":1,"role":"PRIMARY","timeframe":"M5",
                 "settings":{},"producers":["base-structure"]},
                {"id":"ob","indicator":"ORDER_BLOCK","algorithm_version":1,"role":"PRIMARY","timeframe":"M5",
                 "settings":{},"producers":["base-structure"]},
            ])
            _,entered=store(multi)
            multi=load_run(entered["run_id"])
            benchmarks["six_producers"] = measure(lambda:calculate(multi,catalog["end"]),samples)
            compiled=demo("bearish",0,0)
            leaf=compiled["trigger"]["children"][0]
            for index,amount in enumerate((21,21,21,20)):
                compiled["trigger"]["children"].append({"type":"GROUP","operator":"ALL","id":f"group-{index}",
                    "children":[{**leaf,"id":f"leaf-{index}-{j}"} for j in range(amount)]})
            checked=validate_definition(compiled)
            assert checked["valid"],checked["errors"]
            graph=StrategyDefinitionV2.model_validate(checked["definition"])
            benchmarks["maximum_graph_compilation"]=measure(lambda:RuleGraph(graph),samples)
            fixed=StrategyDefinitionV2.model_validate(multi["definition"])
            benchmarks["producer_graph_resolution"]=measure(lambda:collect(fixed,multi["selection"],
                catalog["end"],multi["identity"]["evaluation_start"]).metrics,samples)

            starts = iter([store(demo("bearish",0,0),catalog["start"]+12600)[1] for _ in range(samples+4)])

            def persist_advancement():
                run = next(starts)
                result = client.post(prefix+"/"+run["run_id"]+"/advance",json={
                    "cutoff":catalog["end"],"expected_decision_id":run["decision"]["decision_id"],
                })
                assert result.status_code==200,result.text
                return result.json()

            benchmarks["persist_advancement"] = measure(persist_advancement,samples)
            compiled["trigger"]["children"][-1]["children"].append({**leaf,"id":"beyond-graph-limit"})

            def reject_oversized_graph():
                result = validate_definition(compiled)
                assert not result["valid"]
                return result

            benchmarks["reject_oversized_graph"] = measure(reject_oversized_graph,samples)
    return cases,benchmarks


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--samples",type=int,choices=(0,30),default=0)
    args=parser.parse_args()
    source_root=Path(os.environ.get("TRADEFORGE_STORAGE", ROOT/"storage"))
    original=Path(os.environ.get("TRADEFORGE_DATA_DIR",source_root/"research")).resolve()
    catalog=json.loads(CATALOG.read_text(encoding="utf-8"))
    with TemporaryDirectory(prefix="tradeforge-real-evaluation-") as space:
        temp=Path(space)
        datasets=copy_sources(catalog,original,temp)
        os.environ["TRADEFORGE_STORAGE"]=space
        os.environ["TRADEFORGE_DATA_DIR"]=str(temp/"research")
        cases,measurements=run(catalog,datasets,args.samples)
        print(json.dumps({"selection":catalog,"cases":cases,"benchmarks":measurements},indent=2))


if __name__=="__main__":
    main()
