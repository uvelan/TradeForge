"""Run identical warm serial workloads from the reviewed or corrected checkout."""
from datetime import UTC, datetime
import json
from pathlib import Path
import platform
import runpy
from statistics import median, quantiles
from tempfile import TemporaryDirectory
from time import perf_counter
import tracemalloc

from fastapi.testclient import TestClient
from research_backend.app import config, db, storage
from research_backend.app.indicator_history import RECORDED_ARRIVAL
from research_backend.app.main import app
from research_backend.app.smt_causal import StrictSmtRequest, evaluate_strict_smt

rss_peak = runpy.run_path(str(Path(__file__).resolve().parents[1] / "TF-I08C" / "benchmark.py"))["rss_peak"]
STEP = 300
START = int(datetime(2025, 1, 6, tzinfo=UTC).timestamp())
URL = "/api/indicators/smt/strict"


def build_request(count, spacing, *, delayed=False, missing=False, expiry=12):
    end = START + count * STEP
    body = {"period": 2, "alignment_bars": 1, "expiry_bars": expiry, "cutoff": end, "limit": 8}
    for index, (side, symbol) in enumerate([("primary", "EURUSD"), ("comparison", "GBPUSD")]):
        rows = []
        for slot in range(count):
            if missing and index == 1 and slot == count-5:
                continue
            peak = (14, 16) if index == 0 else (29, 27)
            price = peak[(slot//spacing) % 2] if slot % spacing == 3 else 10+12*index
            stamp = START + slot*STEP
            delay = (96-slot)*STEP + ((slot*179)%241) if delayed and slot < 96 and slot % 3 == index else 0
            rows.append({"time": stamp, "open": price, "close": price, "high": price+1, "low": price-1,
                         "closed": True, "available_at": stamp+STEP+delay, "availability_policy": RECORDED_ARRIVAL})
        manifest = []
        for offset in range(0, len(rows), 2000):
            part = storage.write_partition("DUKASCOPY", "integrity-bench", symbol, "candles", "M5", "BID",
                                           rows[offset:offset+2000])
            manifest.append({"id": part["id"], "sha256": part["sha256"]})
        body[side] = {"source": "DUKASCOPY_SAVED", "symbol": symbol, "feed_id": "integrity-bench",
                      "timeframe": "M5", "price_basis": "BID", "calendar": {"kind": "continuous"},
                      "partitions": manifest, "start": START, "end": end}
    return body


def measured(callback):
    since = perf_counter()
    value = callback()
    return (perf_counter()-since)*1000, value


def stats(times):
    return [round(median(times), 3), round(quantiles(times, n=100, method="inclusive")[94], 3)]


def run(client, label, n, width, **scenario):
    body = build_request(n, width, **scenario)
    request = StrictSmtRequest.model_validate(body)

    def api(payload):
        response = client.post(URL, json=payload)
        if not response.is_success:
            raise RuntimeError(response.text)
        return response

    calc, combined, frontiers = [], [], []
    for sample in range(33):
        elapsed, result = measured(lambda: evaluate_strict_smt(request))
        api_elapsed, response = measured(lambda: api(body))
        if sample >= 3:
            calc.append(elapsed)
            combined.append(api_elapsed)
            if "matching_frontier_ms" in result["resources"]:
                frontiers.append(result["resources"]["matching_frontier_ms"])

    rewind, earlier = measured(lambda: api({**body, "cutoff": body["cutoff"]-30*STEP}))
    advance, restored = measured(lambda: api(body))
    assert restored.json()["snapshot"] == result["snapshot"] != earlier.json()["snapshot"]
    tracemalloc.start()
    evaluate_strict_smt(request)
    _, allocation = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return {"scenario": label, "candles_per_feed": n, "samples": len(calc), "calculation_ms": stats(calc),
            "asgi_ms": stats(combined), "matching_frontier_ms": stats(frontiers) if frontiers else None,
            "python_peak_bytes": allocation, "working_set_high_water_kib": rss_peak(),
            "rewind_ms": round(rewind, 3), "advance_ms": round(advance, 3), "response_bytes": len(response.content),
            "historical_families": result["delivery"]["total"], "current_families": len(result["current"]),
            "resources": result["resources"]}


def main():
    print(platform.platform(), platform.python_version(), flush=True)
    with TemporaryDirectory() as scratch:
        path = Path(scratch)
        db.DATABASE = config.DATABASE = path/"benchmark.sqlite3"
        storage.MARKET_DIR = path/"market"
        with TestClient(app) as client:
            for label, n, width, scenario in [
                ("dense", 2000, 6, {}), ("sparse", 10000, 100, {}), ("dense", 10000, 6, {}),
                ("delayed-prefix", 2000, 14, {"delayed": True}),
                ("missing-laggard-many-families", 2000, 6, {"missing": True, "expiry": 128}),
            ]:
                print(json.dumps(run(client, label, n, width, **scenario), sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
