"""Compare one existing chart API on verified prices in clean copies of main and PR storage."""
import argparse
from datetime import date
from hashlib import sha256
import json
from math import ceil
import os
from pathlib import Path
from statistics import median
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import perf_counter

ROOT = Path(__file__).resolve().parents[3]


def worker(directory):
    sys.path.insert(0, str(directory))
    from fastapi.testclient import TestClient
    from research_backend.app.db import initialize
    from research_backend.app.main import app
    # Import the same fixture verifier in both processes, after binding the app under test.
    sys.path.append(str(ROOT))
    from scripts.u01_comparison_archive import reference
    initialize()
    expected = reference(date(2026, 11, 13), Path(os.environ["TRADEFORGE_DATA_DIR"]))
    timings = []
    digests = {}
    # Isolate request/response cost from background jobs and server startup.
    client = TestClient(app)
    for sample in range(31):
        for symbol, lane in expected.items():
            start = lane["window"][0]["time"]
            begin = perf_counter()
            response = client.get("/api/market/candles", params=dict(symbol=symbol, timeframe="M5",
                source="DUKASCOPY_SAVED", start=start, end=start+7200, limit=24))
            elapsed = (perf_counter()-begin)*1000
            response.raise_for_status()
            result = response.json()
            assert result["source"]=="DUKASCOPY_SAVED" and result["price_basis"]=="BID" and not result["synthetic"]
            rows = [{key: bar[key] for key in ("time","open","high","low","close")} for bar in result["candles"]]
            assert rows==lane["window"] and len(rows)==24
            digest = sha256(json.dumps(rows,sort_keys=True).encode()).hexdigest()
            assert digests.setdefault(symbol,digest)==digest
            if sample:
                timings.append(elapsed)
    return dict(sha=subprocess.check_output(["git","rev-parse","HEAD"],cwd=directory,text=True).strip(),
                n=len(timings),median_ms=round(median(timings),3),p95_ms=round(sorted(timings)[ceil(.95*len(timings))-1],3),
                ohlc_sha256=digests, samples_ms=[round(t,3) for t in timings])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--main",type=Path)
    parser.add_argument("--worker",type=Path)
    parser.add_argument("--output",type=Path)
    args=parser.parse_args()
    if args.worker:
        print(json.dumps(worker(args.worker)))
        return
    if not args.main or not args.output:
        parser.error("--main and --output are required")
    result={}
    with TemporaryDirectory() as temp:
        for label,tree in (("main",args.main.resolve()),("candidate",ROOT)):
            storage=Path(temp)/label
            env={**os.environ,"TRADEFORGE_STORAGE":str(storage),"TRADEFORGE_DATA_DIR":str(storage/"research"),
                 "TRADEFORGE_COMPARISON_SOURCE_DIR":str(ROOT/"storage"/"research")}
            args_worker=[sys.executable,str(Path(__file__).resolve()),"--worker",str(tree)]
            result[label]=json.loads(subprocess.check_output(args_worker,cwd=tree,env=env,text=True))
    assert result["main"]["ohlc_sha256"]==result["candidate"]["ohlc_sha256"]
    args.output.write_text(json.dumps(result,indent=2)+"\n",encoding="utf8")


if __name__=="__main__":
    main()
