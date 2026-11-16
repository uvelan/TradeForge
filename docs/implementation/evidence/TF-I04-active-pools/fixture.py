import json
from research_backend.app.indicator_engine import Evaluation,evaluate
start=1793577600
bars=[dict(time=start+i*60,open=10,high=20 if i in (1,3) else 11+i/100 if i>=5 and i%2 else 10,
           low=5 if i in (1,3) else 10,close=10) for i in range(180)]
opts=dict(id="liquidity_sweeps",structure={"left":1,"right":1},liquidity={"equality_bps":0})
def run(limit):
    spec={**opts,"liquidity_delivery":{"pool_limit":limit}}
    return evaluate(Evaluation(source="DEMO",symbol="EURUSD",timeframe="M1",cutoff=start+len(bars)*60,
        candles=bars,indicators=[spec]))["results"][0]
full,latest=run(500),run(60)
view=latest.get("current_pools")
print(json.dumps({"current_page":None if view is None else {**view,"items":[row for row in view["items"] if row["state"]=="ACTIVE"]}, "revision_count":latest["liquidity_page"]["pools"]["total"],
  "activation_revisions":[p for p in full["pools"] if p["state"]=="ACTIVE"],
  "target_pools_on_latest_page":[p["pool_id"] for p in latest["pools"] if p["upper"]==20 or p["lower"]==5]},indent=2))
