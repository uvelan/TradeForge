"""Reproduce via PowerShell from the repository root. See TF-I02-REVIEW.md."""
from datetime import UTC, datetime
from math import ceil
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory
from time import perf_counter
from tracemalloc import get_traced_memory, start as trace_start, stop as trace_stop

from research_backend.app import db, storage
from research_backend.app.indicator_engine import Evaluation, evaluate
from research_backend.app.indicator_history import load_reference

START = int(datetime(2026, 11, 2, tzinfo=UTC).timestamp())
PATTERN = [10, 12, 10, 13, 11, 14, 12, 15, 13, 10, 11]


def bars(count, shape):
    result = []
    for index in range(count):
        stamp = START+60*index*(2 if shape == "sparse" else 1)
        price = 10 if shape == "flat" else PATTERN[index % len(PATTERN)]
        result.append({"time": stamp, "open": price, "high": price, "low": price, "close":price})
    return result


def measure(label, call):
    outcome = call()
    samples = []
    for _ in range(20):
        clock = perf_counter()
        call()
        samples.append((perf_counter()-clock)*1000)
    trace_start()
    call()
    _, peak = get_traced_memory()
    trace_stop()
    observed = outcome["results"][0]
    print(f"{label}: p50={median(samples):.2f} ms, p95={sorted(samples)[ceil(.95*len(samples))-1]:.2f} ms, "
          f"Python peak={peak/1024:.1f} KiB, {len(observed['pivots'])} pivots, {len(observed['breaks'])} breaks")
    return observed


print("Windows, Python 3.13, M1, 20 warm samples; peak Python allocation sampled separately; RSS not measured")
rules = {"left":1, "right":1, "price_source":"close"}
for shape in ("many", "flat", "sparse"):
    candles = bars(2000, shape)
    request = Evaluation(source="DEMO",symbol="EURUSD",timeframe="M1",
        cutoff=candles[-1]["time"]+60,candles=candles,indicators=[dict(id="market_structure",structure=rules)])
    measure(f"2,000 caller bars, {shape}", lambda: evaluate(request))

with TemporaryDirectory() as root:
    db.DATABASE = Path(root)/"bench.sqlite"
    storage.MARKET_DIR = Path(root)/"market"
    db.initialize()
    candles = bars(10_000, "many")
    manifest = []
    for offset in range(0,len(candles),2000):
        item = storage.write_partition("MT5","fixture","EURUSD","candles","M1","BID",candles[offset:offset+2000])
        manifest.append({"id":item["id"],"sha256":item["sha256"]})
    request = Evaluation(source="MT5_SAVED",symbol="EURUSD",timeframe="M1",
        cutoff=START+600_000, candles=[], feed_id="fixture",price_basis="BID", frozen=True,
        partitions=manifest,indicators=[dict(id="market_structure",
            structure={**rules,"history_bars":10_000,"history_start":START})])
    output = measure("10,000 archived bars, many, including SHA-256 and Parquet decode",
                     lambda: evaluate(request,load_reference))
    print(output["provenance"]["retrieval"])
