"""Run a Strategy Lab historical request or completed run against local recorded quotes."""
import argparse
from datetime import datetime
import json
from pathlib import Path
from time import monotonic, sleep

import httpx

API = "/api/historical-strategy-runs/v1"


def stamp(text):
    date = datetime.fromisoformat(text.replace("Z","+00:00"))
    if date.utcoffset() is None:
        raise argparse.ArgumentTypeError("Include the UTC offset")
    return int(date.timestamp()*1000)


def execute(client, *, run_id, recipe, start, end, cutoff_ms, resume=None, partitions=()):
    def body(response):
        if not response.is_success:
            raise ValueError(f"{response.status_code} {response.text}")
        return response.json()
    if recipe is not None:
        run_id = body(client.post(API,json=recipe))["run_id"]
    deadline = monotonic()+210
    while True:
        detail = body(client.get(f"{API}/{run_id}"))
        state = detail["worker"]["state"]
        if state=="COMPLETED":
            break
        if state not in ("QUEUED","RUNNING") or monotonic()>=deadline:
            raise ValueError(f"{run_id}: {detail['worker']}")
        sleep(0.5)
    return body(client.post(f"{API}/{run_id}/recorded-replay",json=dict(start=start,end=end,
                            cutoff_ms=cutoff_ms,resume=resume,partitions=list(partitions))))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--recipe",type=Path,help="Pinned historical request JSON (strategy version and research interval)")
    group.add_argument("--run",help="Completed or queued Strategy Lab historical run ID")
    p.add_argument("--start",type=stamp,required=True,help="Inclusive recorded window start")
    p.add_argument("--end",type=stamp,required=True,help="Exclusive recorded window end")
    p.add_argument("--through",type=stamp,help="Commit a checkpoint through this instant, inclusive")
    p.add_argument("--resume",help="Session ID from an earlier checkpoint")
    p.add_argument("--port",type=int,default=8000)
    args = p.parse_args()
    if args.start%1000 or args.end%1000:
        p.error("Use whole seconds for window bounds")
    with httpx.Client(base_url=f"http://127.0.0.1:{args.port}/research",timeout=180,trust_env=False) as client:
        result = execute(client,run_id=args.run,recipe=json.loads(args.recipe.read_text()) if args.recipe else None,
                         start=args.start//1000,end=args.end//1000,cutoff_ms=args.through if args.through is not None else args.end,
                         resume=args.resume)
    print(json.dumps(result,indent=2))


if __name__=="__main__":
    main()
