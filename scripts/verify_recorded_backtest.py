"""Windows-local acceptance on the approved November 2025 BI5 inputs; no network or terminal."""
import argparse
from datetime import UTC,datetime
import json
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

from scripts.recorded_backtest import execute
from scripts.verify_real_strategy_evaluation import peak_rss_bytes

ROOT = Path(__file__).resolve().parents[1]


def main(inputs):
    with TemporaryDirectory(prefix="tradeforge-recorded-run-") as work:
        os.environ.update(TRADEFORGE_STORAGE=work,TRADEFORGE_DATA_DIR=str(Path(work)/"research"))
        from fastapi.testclient import TestClient
        from research_backend.app.main import app
        start = int(datetime(2025,11,13,13,tzinfo=UTC).timestamp())
        with TestClient(app,base_url="http://localhost") as client:
            parts = []
            for hour in (13,14):
                path = inputs/f"{hour}h_ticks.bi5"
                response = client.post("/api/market/import/reviewed",files={"file":(path.name,path.read_bytes())})
                response.raise_for_status()
                parts.extend(response.json()["partitions"])
            ids = [p["id"] for p in parts if p.get("timeframe")=="M5" and p.get("price_basis")=="BID"]
            definition = json.loads((ROOT/"docs/implementation/plan-integration-evidence/REPLAY-FVG-DEFINITION.json").read_text())
            saved = client.post("/api/strategies/v1",json=definition)
            if saved.status_code!=201:
                raise ValueError(saved.text)
            version = saved.json()
            recipe = dict(evaluator_version=6,purpose="HISTORICAL_SIMULATION",selection_id="WORKSPACE",
                selection_fingerprint="0"*64, strategy_id=version["strategy_id"],strategy_version_id=version["version_id"],
                configuration_fingerprint=version["configuration_fingerprint"],partitions=ids,warmup_start=start,
                evaluation_start=start+3600,cutoff=start+7200)
            # Count Python opens of registered Parquet files in the API and spawned worker.
            # SQLite file pages and OS cache hits are not physical disk-read measurements.
            probe = Path(work)/"probe"
            probe.mkdir()
            (probe/"sitecustomize.py").write_text("""import atexit,json,os,sys
from pathlib import Path
counter=[0]
def count(event,args):
    if event=="open" and isinstance(args[0],str) and args[0].endswith(".parquet") and isinstance(args[1],str) and "r" in args[1]:
        counter[0]+=1
sys.addaudithook(count)
@atexit.register
def report():
    from scripts.verify_real_strategy_evaluation import peak_rss_bytes
    Path(os.environ["TRADEFORGE_BENCH_PROBE"],str(os.getpid())+".json").write_text(json.dumps([counter[0],peak_rss_bytes()]))
""",encoding="utf-8")
            os.environ["TRADEFORGE_BENCH_PROBE"]=str(probe)
            os.environ["PYTHONPATH"]=os.pathsep.join([str(probe),str(ROOT),os.environ.get("PYTHONPATH","")])
            opened=[]
            def count(event,args):
                if (event=="open" and isinstance(args[0],str) and args[0].endswith(".parquet")
                        and isinstance(args[1],str) and "r" in args[1]):
                    opened.append(args[0])
            sys.addaudithook(count)
            begin = perf_counter()
            result = execute(client,run_id=None,recipe=recipe,start=start,end=start+7200,cutoff_ms=(start+7200)*1000)
            elapsed = perf_counter()-begin
            from time import sleep
            for _ in range(50):
                if list(probe.glob("*.json")):
                    break
                sleep(.05)
            workers=[json.loads(p.read_text()) for p in probe.glob("*.json")]
            print(json.dumps(dict(api_parquet_opens=len(opened),worker_open_count_and_peak_bytes=workers,seconds=elapsed,process_peak_bytes=peak_rss_bytes(),recipe=recipe,
                             decisions=result["decisions"],positions=result["positions"],binding=result["binding"],
                             unverified_intervals_ms=result["unverified_intervals_ms"],intervals=result["intervals"],observed_quotes=result["observed_quotes"],
                             parquet_reads=result["parquet_reads"],bi5_reads=result["bi5_reads"]),indent=2))


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source",type=Path,default=ROOT/"storage/t06-input")
    main(p.parse_args().source)
