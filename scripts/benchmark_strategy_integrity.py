"""Exercise the evaluator on a separate, densely overlapping synthetic archive."""
import json
import os
from tempfile import TemporaryDirectory

from verify_real_strategy_evaluation import demo, measure

START = 1577959200
END = START + 79_200
SAMPLES = 30


def make_archive():
    from research_backend.app.storage import write_partition

    records = []
    for symbol in ("EURUSD", "GBPUSD"):
        for frame, step in (("M1", 60), ("M5", 300)):
            pattern = ([18,20,26,20,18,20,30,20,18,20,20,20] if symbol=="EURUSD" else
                       [32,34,42,34,32,34,39,34,32,34,34,34])
            bars = []
            for index in range((END-START)//step):
                if frame=="M5":
                    template = [(9,10,8,9),(11,13,10,12),(11,12,9,10),(12,15,11,14),
                                (13,14,11.25,13),(13,14,10.5,13),(13,14,9,11),(9,11,8,9),
                                (99,100,98,99),(100,103,100,102),(110,111,110,111),
                                (112,114,112,113),(113,114,106,113)]
                    o,h,l,c = template[index%len(template)]
                else:
                    c = pattern[index%len(pattern)]
                    if index//len(pattern)%2:
                        c = 70-c
                    o,h,l = c,c+1,c-1
                bars.append({"time":START+index*step,"open":o,"high":h,"low":l,"close":c,"closed":True,"volume":0})
            row = write_partition("DUKASCOPY","dense-integrity",symbol,"candles",frame,"BID",bars)
            records.append({"source":"DUKASCOPY_SAVED","feed_id":"dense-integrity","symbol":symbol,
                            "timeframe":frame,"price_basis":"BID","calendar":{"kind":"continuous"},
                            "partitions":[{"id":row["id"],"sha256":row["sha256"]}]})
    return records


def benchmark():
    from fastapi.testclient import TestClient
    from research_backend.app import strategy_evaluation_sources as sources
    from research_backend.app.db import initialize
    from research_backend.app.main import app
    from research_backend.app.strategy_evaluation_routes import calculate, prepare
    from research_backend.app.strategy_definitions import StrategyDefinitionV2, validate_definition
    from research_backend.app.strategy_evidence import fingerprint

    initialize()
    selection = {"id":"DENSE_INTEGRITY_FIXTURE","series":make_archive(),"start":START,"end":END}
    selection["fingerprint"] = fingerprint(selection)
    sources.selection_catalog = lambda: [selection]
    data = demo("bearish",0,0)
    data["instruments"]["calendar"] = {"kind":"continuous"}
    data["indicators"].extend([
        {"id":"base-structure","indicator":"STRUCTURE","algorithm_version":2,"role":"PRIMARY","timeframe":"M5",
         "settings":{"left":1,"right":1}},
        {"id":"fvg","indicator":"FVG","algorithm_version":1,"role":"PRIMARY","timeframe":"M5","settings":{}},
        {"id":"liquidity","indicator":"LIQUIDITY","algorithm_version":1,"role":"PRIMARY","timeframe":"M5",
         "settings":{"min_separation_bars":1},"producers":["base-structure"]},
        {"id":"ob","indicator":"ORDER_BLOCK","algorithm_version":1,"role":"PRIMARY","timeframe":"M5",
         "settings":{},"producers":["base-structure"]},
    ])
    leaf = data["trigger"]["children"][0]
    def rule(identifier, observation, expected, operator="EQ"):
        return {**leaf,"id":identifier,"dependency":"base-structure","observation":observation,
                "operator":operator,"expected":expected,"alias":None,"same_family_as":None,
                "evidence":{"state":"HISTORICAL","purpose":"HISTORICAL_SIMULATION","max_age_seconds":3600},
                "selection":"EARLIEST_ELIGIBLE"}

    data["attempt"]["crt_reference_alias"] = None
    data["temporal"] = []
    data["expiry"] = {"unit":"SECONDS","count":3600}
    data["context"]["children"] = [rule("ctx-pivot","STRUCTURE_PIVOT","HIGH")]
    data["setup"].update(operator="ALL",children=[rule("setup-pivot","STRUCTURE_PIVOT","HIGH")])
    data["invalidation"]["children"] = [rule("never","STRUCTURE_BREAK_PRICE",1000,"GT")]
    data["trigger"]["children"] = [rule("trigger-test","STRUCTURE_BREAK_PRICE",1000,"GT")]
    leaf = data["trigger"]["children"][0]
    for index,amount in enumerate((21,21,21,20)):
        data["trigger"]["children"].append({"type":"GROUP","operator":"ALL","id":f"group-{index}",
            "children":[{**leaf,"id":f"condition-{index}-{i}"} for i in range(amount)]})
    result = validate_definition(data)
    assert result["valid"],result["errors"]
    with TestClient(app) as client:
        strategy = client.post("/api/strategies/v1",json=data)
        assert strategy.status_code==201,strategy.text
        record = strategy.json()
        request = {"strategy_id":record["strategy_id"],"strategy_version_id":record["version_id"],
                   "configuration_fingerprint":record["configuration_fingerprint"],"evaluator_version":4,
                   "selection_id":selection["id"],"selection_fingerprint":selection["fingerprint"],
                   "purpose":"HISTORICAL_SIMULATION","warmup_start":START,"evaluation_start":START+7200,"cutoff":END}
        pinned = prepare(sources.EvaluationInput.model_validate(request))
        definition = StrategyDefinitionV2.model_validate(pinned["definition"])

        def produce():
            proof = sources.collect(definition,pinned["selection"],END,START+7200)
            counts = {f"{key}:{kind}":len(lane[0]) for key,view in proof.views.items()
                      for kind,lane in proof.events[view.snapshot].items()}
            fvgs = proof.events[proof.views["fvg"].snapshot]["fvg"][1]
            overlaps = sum(bool(item.get("overlaps")) for item in fvgs if item["state"]=="ACTIVE")
            assert overlaps > 0 and counts["smt:smt"] > 10
            return {"resource_counts":{**proof.metrics,"publications_by_producer_category":counts,
                                       "overlapping_fvg_formations":overlaps,
                                       "timeline_boundaries":len(proof.timeline)}}

        measurements = {
            "canonical_producers":measure(produce,SAMPLES),
            "full_evaluation":measure(lambda:calculate(pinned,END),SAMPLES),
        }
        oversized = {**selection,"end":START+400_200}
        oversized["fingerprint"]=fingerprint(oversized)
        sources.selection_catalog = lambda: [oversized]

        def reject():
            response = client.post("/api/strategy-evaluations/v1",
                json={**request,"selection_fingerprint":oversized["fingerprint"]})
            assert response.status_code==422 and response.json()["detail"]["code"]=="RESOURCE_LIMIT",response.text
            return response.json()

        measurements["reject_oversized_selection"] = measure(reject,SAMPLES)
    return measurements


if __name__=="__main__":
    with TemporaryDirectory(prefix="tradeforge-integrity-dense-") as directory:
        os.environ["TRADEFORGE_STORAGE"] = directory
        os.environ["TRADEFORGE_DATA_DIR"] = os.path.join(directory,"research")
        print(json.dumps(benchmark(),indent=2))
