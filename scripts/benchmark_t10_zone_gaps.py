"""Time the same four terminal inspections on disposable pinned M5 archives."""
import argparse
import json
from pathlib import Path
import runpy
import subprocess
from tempfile import TemporaryDirectory

from scripts.benchmark_t10_structure_liquidity import install_counters, sample, summary
from scripts.verify_t10_zone_gaps import configure, get_json, replay, write_fixtures


ROOT = Path(__file__).resolve().parents[1]


def measure(directory, repo):
    configure(directory, repo)
    manifest = write_fixtures("missing")
    from fastapi.testclient import TestClient
    from research_backend.app.main import app

    timings = {}
    with TestClient(app) as client:
        session = replay(client, manifest)
        endpoint = f"/api/replay/sessions/{session}/zones"
        counts = install_counters()
        for symbol, view in (("EURUSD", "primary"), ("GBPUSD", "2")):
            lane = manifest["symbols"][symbol]
            for mode in ("wick", "close"):
                params = {"view": view, "mitigation": mode, "cutoff": lane["T"]}
                zones = get_json(client.get(endpoint, params=params))["zones"]
                family, = [z for z in zones if z["type"] == lane["type"] and z["formation_time"] == lane["formation"]]
                params["zone_id"] = family["zone_id"]

                def inspect():
                    response = client.get(endpoint, params=params)
                    result = get_json(response)["selected"]
                    assert result["reason"] == "MISSING_OPEN_CANDLES" and result["id"] == family["id"]
                    counts["response_bytes"] = len(response.content)

                inspect()
                timings[f"{symbol}_{mode}"] = {"family": family["zone_id"], "revision": family["id"],
                                                **summary([sample(inspect, counts)[0] for _ in range(30)])}
    peak = runpy.run_path(str(ROOT / "docs/implementation/evidence/TF-I08C/benchmark.py"))["rss_peak"]
    return {"revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip(),
            "session": session, "peak_process_kib": peak(), "timings": timings}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repo", type=Path, default=ROOT, help="Application checkout under measurement")
    args = parser.parse_args()
    with TemporaryDirectory(prefix="tradeforge-gap-timing-") as temp:
        result = measure(Path(temp), args.repo.resolve())
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
