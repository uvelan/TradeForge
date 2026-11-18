"""Reproduce the TF-I08C serial, checksum-verified warm archive benchmark."""
from datetime import UTC, datetime
import json
from pathlib import Path
import platform
from statistics import median, quantiles
from tempfile import TemporaryDirectory
from time import perf_counter
import tracemalloc

from research_backend.app import config, db, storage
from research_backend.app.indicator_engine import EvidenceBar
from research_backend.app.indicator_history import RECORDED_ARRIVAL
from research_backend.app.main import app
from research_backend.app.smt import detect_smt
from research_backend.app.smt_causal import StrictSmtRequest, evaluate_strict_smt


SAMPLES = 30
START = int(datetime(2025, 1, 6, tzinfo=UTC).timestamp())
STEP = 300


def rss_peak():
    if platform.system() != "Windows":
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    import ctypes
    from ctypes import wintypes

    class ProcessMemory(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD),
                    ("peak", ctypes.c_size_t), ("current", ctypes.c_size_t),
                    ("other", ctypes.c_size_t*6)]

    memory = ProcessMemory()
    memory.cb = ctypes.sizeof(memory)
    kernel = ctypes.WinDLL("kernel32")
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    query = ctypes.WinDLL("psapi").GetProcessMemoryInfo
    query.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessMemory), wintypes.DWORD]
    return memory.peak // 1024 if query(kernel.GetCurrentProcess(), ctypes.byref(memory), memory.cb) else None


def measure(callback):
    started = perf_counter()
    result = callback()
    return (perf_counter()-started)*1_000, result


def stats(values):
    return {"p50_ms": round(median(values), 3),
            "p95_ms": round(quantiles(values, n=100, method="inclusive")[94], 3)}


def feed(index, count, spacing, delayed, missing):
    rows = []
    for n in range(count):
        if missing and index == 1 and n == count//2:
            continue
        peak = (14, 16) if index == 0 else (29, 27)
        price = peak[(n//spacing) % 2] if n % spacing == 3 else 10+12*index
        time = START+n*STEP
        delay = (n*179 % 241) if delayed and n % 3 == index else 0
        rows.append({"time": time, "open": price, "close": price, "high": price+1, "low": price-1, "closed": True,
                     "available_at": time+STEP+delay, "availability_policy": RECORDED_ARRIVAL})
    return rows


def inputs(count, spacing, delayed, missing):
    body = {"period": 2, "alignment_bars": 1, "expiry_bars": 12, "cutoff": START+count*STEP+300, "limit": 8}
    raw = []
    for index, (side, symbol) in enumerate([("primary", "EURUSD"), ("comparison", "GBPUSD")]):
        rows = feed(index, count, spacing, delayed, missing)
        raw.append([EvidenceBar.model_validate(row) for row in rows])
        parts = []
        for offset in range(0, len(rows), 2000):
            part = storage.write_partition("DUKASCOPY", "benchmark", symbol, "candles", "M5", "BID",
                                           rows[offset:offset+2000])
            parts.append({"id": part["id"], "sha256": part["sha256"]})
        body[side] = {"source": "DUKASCOPY_SAVED", "symbol": symbol, "timeframe": "M5", "feed_id": "benchmark",
                      "price_basis": "BID", "calendar": {"kind": "continuous"}, "partitions": parts,
                      "start": START, "end": START+count*STEP}
    return body, raw


def run_case(client, label, count, spacing, delayed=False, missing=False):
    body, raw = inputs(count, spacing, delayed, missing)
    endpoint = "/api/indicators/smt/strict"
    request = StrictSmtRequest.model_validate(body)
    other_indicators = {
        "timing_policy": "STRICT_CAUSAL", "source": "DUKASCOPY_SAVED", "symbol": "EURUSD",
        "timeframe": "M5", "feed_id": "benchmark", "price_basis": "BID", "cutoff": body["cutoff"],
        "candles": [bar.model_dump() for bar in raw[0][-2_000:]],
        "indicators": [{"id": "market_structure"}, {"id": "fair_value_gap"}, {"id": "order_blocks"},
                       {"id": "candle_range_theory"}],
    }

    def legacy():
        return detect_smt(*raw, 2, STEP, alignment_bars=1)

    def strict():
        return evaluate_strict_smt(request)

    def http(payload):
        response = client.post(endpoint, json=payload)
        response.raise_for_status()
        return response

    def mixed():
        result = http(body)
        companion = client.post("/api/indicators/evaluate", json=other_indicators)
        if not companion.is_success:
            raise RuntimeError(companion.text)
        return result, companion

    for _ in range(3):
        legacy()
        strict()
        http(body)
        if label == "sparse":
            mixed()

    samples = {lane: [] for lane in ("legacy_detector", "strict_archive_calculation", "json_encoding", "strict_asgi")}
    if label == "sparse":
        samples["strict_asgi_and_companion_indicators"] = []
    for _ in range(SAMPLES):
        delay, _ = measure(legacy)
        samples["legacy_detector"].append(delay)
        delay, projection = measure(strict)
        samples["strict_archive_calculation"].append(delay)
        delay, _ = measure(lambda: json.dumps(projection, separators=(",", ":"), allow_nan=False))
        samples["json_encoding"].append(delay)
        delay, response = measure(lambda: http(body))
        samples["strict_asgi"].append(delay)
        if label == "sparse":
            delay, (_, companion) = measure(mixed)
            samples["strict_asgi_and_companion_indicators"].append(delay)

    rewind, previous = measure(lambda: http({**body, "cutoff": START+(count-30)*STEP}))
    advance, again = measure(lambda: http(body))
    assert again.json()["snapshot"] == response.json()["snapshot"]
    assert previous.json()["snapshot"] != response.json()["snapshot"]
    tracemalloc.start()
    strict()
    _, allocation = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return {"scenario": label, "per_feed_candles": count, "samples": SAMPLES,
            **{lane: stats(values) for lane, values in samples.items()},
            "replay_rewind_ms": round(rewind, 3), "replay_readvance_ms": round(advance, 3),
            "response_bytes": len(response.content), "python_allocation_peak_bytes": allocation,
            "process_rss_high_water_kib": rss_peak(), "event_count": projection["delivery"]["total"],
            "resources": projection["resources"],
            "companion_executions": companion.json()["diagnostics"]["executions"] if label == "sparse" else None}


def main():
    from fastapi.testclient import TestClient
    print(platform.platform(), platform.python_version(), flush=True)
    with TemporaryDirectory() as scratch:
        path = Path(scratch)
        db.DATABASE = config.DATABASE = path/"benchmark.sqlite3"
        storage.MARKET_DIR = path/"market"
        with TestClient(app) as client:
            for label, count, spacing, delayed, missing in [
                ("dense", 2000, 6, False, False), ("sparse", 10000, 100, False, False),
                ("dense", 10000, 6, False, False), ("delayed and missing", 2000, 14, True, True),
            ]:
                print(json.dumps(run_case(client, label, count, spacing, delayed, missing), sort_keys=True),
                      flush=True)


if __name__ == "__main__":
    main()
