"""Compare the reviewed and candidate level identities with identical caller candles."""
import json
from pathlib import Path
import sys
from types import ModuleType

root=Path(__file__).resolve().parents[4]
sys.path[:0]=[str(root),str(root/"research_backend"/"tests")]
from test_level_evidence_integrity import archive, saved_case, case

approved=ModuleType("reviewed_tradeforge")
approved.__path__=[str(Path(sys.argv[1])/"research_backend")]
sys.modules[approved.__name__]=approved

from reviewed_tradeforge.app import indicator_engine as baseline
from research_backend.app import indicator_engine as current


def run(engine, data, history=None):
    def load(*args):
        result=history(*args)
        return {**result,"rows":[(engine.EvidenceBar.model_validate(bar.model_dump()),stamp)
                                 for bar,stamp in result["rows"]]}
    out=engine.evaluate(engine.Evaluation.model_validate(data),load if history else None)
    return {value["id"]:value for value in out["results"]}


for engine in (baseline,current):
    output=run(engine,case("session_asia",custom=True))
    analysis=output["session_asia"]["analysis"]
    ref=output["candle_range_theory"]["crt_sweeps"][0]["context"]["session_asia_high"]
    print(engine.__name__,"shared Tokyo",json.dumps({key:[analysis[key],ref[key]]
          for key in ("configuration_fingerprint","calculation_snapshot")}))
    print("same canonical observation",ref in analysis.get("level_observations",[]))
    for name,label,correction in (("previous_day","High",{"high":1.107}),
                                  ("previous_week","Low",{"low":1.099})):
        data=saved_case(name)
        for variant,edits in (("baseline evidence",None),("price correction",correction),
                              ("same-extremes correction",{"close":1.1035})):
            level=run(engine,data,archive(changed=edits))[name]
            analysis=level["analysis"]
            ref=next((item for item in analysis.get("level_observations",[]) if item["label"]==label),{})
            print(name,variant,json.dumps({
                "price":level["series"][label][-1]["value"],
                "config":analysis["configuration_fingerprint"],"snapshot":analysis["calculation_snapshot"],
                "observation":ref.get("observation_id"),"evidence":ref.get("evidence_fingerprint")}))
