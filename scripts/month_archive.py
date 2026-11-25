"""Import local Dukascopy UTC-hour files or fetch missing open hours into the local catalog."""
import argparse
from datetime import UTC,datetime
from hashlib import sha256
from pathlib import Path
import json
import subprocess

from research_backend.app.db import initialize,transaction
from research_backend.app.historical_import import import_bi5
from research_backend.app.month_replay import inventory
from scripts.benchmark_t06 import PINS


def run(month, directory, fetch):
    initialize()
    with transaction() as db:
        plan=inventory(db,month)
    for hour in plan["hours"]:
        if hour["status"]=="SCHEDULED_CLOSURE":
            continue
        day=datetime.fromtimestamp(hour["start"],UTC)
        known=day.date().isoformat()=="2025-11-13" and day.hour in PINS
        name=f"{day.hour:02}h_ticks.bi5"
        path=directory/day.strftime("%Y-%m-%d")/name
        if known and not path.exists():
            path=directory/name
        if path.is_file():
            raw=path.read_bytes()
        elif hour["pin"] or not fetch:
            continue
        else:
            url=f"https://datafeed.dukascopy.com/datafeed/EURUSD/{day.year}/{day.month-1:02}/{day.day:02}/{name}"
            raw=subprocess.run(["curl","--proto","=https","--fail","--silent","--show-error",
                                "--max-time","30","--max-filesize","8000000",url],capture_output=False,
                               stdout=subprocess.PIPE,check=True).stdout
        if known and sha256(raw).hexdigest()!=PINS[day.hour]:
            raise ValueError(f"Approved control hash mismatch at {day}")
        import_bi5(raw,"EURUSD",day,day.hour)
    with transaction() as db:
        print(json.dumps(inventory(db,month),indent=2))


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("month")
    p.add_argument("--source",type=Path,default=Path("storage/t06-input"))
    p.add_argument("--fetch",action="store_true",help="Retrieve missing provider hours sequentially. Run again to resume.")
    args=p.parse_args()
    run(args.month,args.source,args.fetch)
