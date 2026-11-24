"""Benchmark GET /api/backtest on two pinned Dukascopy EURUSD BI5 hours in isolation."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
from datetime import UTC, datetime
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import perf_counter
import tracemalloc

from scripts.verify_real_strategy_evaluation import peak_rss_bytes

DAY = datetime(2025, 11, 13, tzinfo=UTC)
PINS = {13: "2f47bcb14e0a2c4dcd6d65eb819b85f1b5168b656d1f5f67c3dcd2240569f014",
        14: "9d40aa3147d93e6daa2218933877adb9471dc02ff08b19fc0d272f1eb781688f"}


def measure():
    from fastapi.testclient import TestClient
    from research_backend.app.main import app
    from contextlib import closing
    with closing(TestClient(app, base_url="http://localhost")) as client:
        tracemalloc.start()
        start = perf_counter()
        response = client.get("/api/backtest", params={
            "source": "DUKASCOPY_SAVED", "tick_archive_range": True, "timeframe": "M1",
            "start": int(DAY.replace(hour=13).timestamp()), "end": int(DAY.replace(hour=15).timestamp())})
        elapsed = perf_counter() - start
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        response.raise_for_status()
        result = response.json()
        return dict(seconds=elapsed, python_peak_bytes=peak, process_peak_bytes=peak_rss_bytes(),
                    python=sys.version.split()[0], platform=platform.platform(), result=result)


def run(source):
    with TemporaryDirectory() as temp:
        os.environ["TRADEFORGE_STORAGE"] = temp
        os.environ["TRADEFORGE_DATA_DIR"] = str(Path(temp) / "research")
        os.environ.pop("TRADEFORGE_CANDLE_SHADOW_MANIFEST", None)
        from research_backend.app.db import initialize
        from research_backend.app.historical_import import import_bi5
        initialize()
        inputs = []
        for hour, pin in PINS.items():
            raw = (source / f"{hour}h_ticks.bi5").read_bytes()
            if hashlib.sha256(raw).hexdigest() != pin:
                raise ValueError(f"Wrong BI5 input at {hour}:00 UTC")
            part = import_bi5(raw, "EURUSD", DAY, hour)["tick_dataset"]
            inputs.append(dict(hour=hour, raw_sha256=pin, raw_bytes=len(raw), partition=part,
                               parquet_bytes=(Path(temp)/"research/market"/part["relative_path"]).stat().st_size))
        # Fresh process includes interpreter/API RSS but excludes import and ingestion peaks.
        output = subprocess.check_output([sys.executable, "-m", "scripts.benchmark_t06", "--measure"])
        return dict(inputs=inputs, measurement=json.loads(output))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    task = parser.add_mutually_exclusive_group(required=True)
    task.add_argument("--source", type=Path)
    task.add_argument("--measure", action="store_true")
    args = parser.parse_args()
    print(json.dumps(measure() if args.measure else run(args.source), indent=2))
