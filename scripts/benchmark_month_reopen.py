"""Time inspection of an already completed monthly ledger on two local revisions."""
import argparse
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


def request_worker(seed):
    sys.path.insert(0, str(Path.cwd()))
    from fastapi.testclient import TestClient
    from research_backend.app.main import app

    route = "/api/month-replay/2025-11"
    with TestClient(app, base_url="http://localhost") as api:
        response = api.get(route)
        response.raise_for_status()
        session = response.json()["session"]
        if seed:
            index = 0
            while True:
                step = api.post(f"{route}/{session}/advance", params={"index": index})
                step.raise_for_status()
                value = step.json()
                if value["completed"]:
                    break
                index = value["next_index"]
        times = []
        for i in range(31):
            start = perf_counter()
            response = api.get(route, params={"session": session})
            elapsed = (perf_counter() - start) * 1000
            response.raise_for_status()
            data = response.json()
            assert data["checkpoint"]["completed"] and data["checkpoint"]["quote_count"] == 8636
            if i:
                times.append(elapsed)
        proof = sha256(json.dumps(data["checkpoint"], sort_keys=True).encode()).hexdigest()
        return dict(samples=len(times), median_ms=median(times), p95_ms=sorted(times)[ceil(.95 * len(times))-1],
                    session=session, checkpoint_digest=proof, gap_count=data.get("open_gap_intervals"))


def compare(baseline, candidate, source):
    with TemporaryDirectory() as temp:
        env = os.environ | {"TRADEFORGE_STORAGE": temp, "TRADEFORGE_DATA_DIR": str(Path(temp) / "research")}
        call = [sys.executable, str(Path(__file__).resolve())]
        # The sessions below issue only GETs after the common seed. Worktrees must share a compatible schema.
        subprocess.run([sys.executable, "-m", "scripts.month_archive", "2025-11", "--source", str(source.resolve())],
                       cwd=baseline, env=env, stdout=subprocess.DEVNULL, check=True)
        subprocess.run(call + ["--mode", "seed"], cwd=baseline, env=env, stdout=subprocess.DEVNULL, check=True)
        result = {}
        for label, directory in (("base", baseline), ("candidate", candidate)):
            sample = json.loads(subprocess.check_output(call + ["--mode", "reopen"], cwd=directory, env=env))
            sample["revision"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=directory, text=True).strip()
            result[label] = sample
        for key in ("session", "checkpoint_digest"):
            assert result["base"][key] == result["candidate"][key]
        return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--candidate", type=Path, default=Path.cwd())
    parser.add_argument("--source", type=Path, default=Path("storage/t06-input"))
    parser.add_argument("--mode", choices=("seed", "reopen"), help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not args.mode and not args.baseline:
        parser.error("--baseline is required")
    report = request_worker(args.mode == "seed") if args.mode else compare(args.baseline, args.candidate, args.source)
    print(json.dumps(report, indent=2))
