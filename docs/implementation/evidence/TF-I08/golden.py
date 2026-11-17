"""Approved single and combined TF-I07 results with timing excluded."""
import hashlib
import json
from pathlib import Path

from research_backend.app.indicator_engine import Evaluation,evaluate

ROOT=Path(__file__).parent
START=1793577600
STRUCTURE={"left":1,"right":1}
SPECS=[
    {"id":"ema","period":5},{"id":"rsi","period":5},{"id":"atr","period":5},
    {"id":"previous_day","schedule":{"kind":"continuous"}},
    {"id":"previous_week","schedule":{"kind":"continuous"}},
    {"id":"session_asia"},
    {"id":"market_structure","structure":STRUCTURE},
    {"id":"market_structure","structure":{**STRUCTURE,"algorithm_version":2}},
    {"id":"liquidity_sweeps","structure":STRUCTURE},
    {"id":"fair_value_gap"},
    {"id":"order_blocks","structure":STRUCTURE},
    {"id":"candle_range_theory"},
]


def run(specs):
    market=[(100,101,99,100),(101,104,100,103),(102,103,100,101),(103,106,102,105),
            (104,106,103,105),(105,107,104,104),(103,104,98,99),(100,102,99,101)]
    bars=[]
    for i in range(1800):
        o,h,l,c=market[i%len(market)]
        bars.append(dict(time=START+i*300,open=o,high=h,low=l,close=c))
    result=evaluate(Evaluation(source="DEMO",symbol="EURUSD",feed_id="demo-v1",price_basis="SIMULATED",
                     timeframe="M5",cutoff=START+1800*300,candles=bars,indicators=specs))
    return [{key:value for key,value in item.items() if key!="analysis"} for item in result["results"]]


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(",",":")).encode()).hexdigest()


def fingerprints():
    return {"singles":[digest(run([spec])[0]) for spec in SPECS],
            "combined_v1":[digest(item) for item in run(SPECS[6:7]+SPECS[8:])],
            "combined_v2":[digest(item) for item in run(SPECS[7:])]}
if __name__=="__main__":
    print(json.dumps(fingerprints(),indent=2))
