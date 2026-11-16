r"""Run from the repo root via Get-Content benchmark.py -Raw | .venv\Scripts\python.exe -."""
from datetime import UTC, datetime
from math import ceil
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory
from time import perf_counter
import tracemalloc

from research_backend.app import db, storage
from research_backend.app.indicator_engine import Evaluation, evaluate
from research_backend.app.indicator_history import load_reference

start = int(datetime(2026, 11, 2, tzinfo=UTC).timestamp())
end = start + 7 * 86400
step = 60


def bar(when, high=12, low=9):
    return dict(time=when, open=10, high=high, low=low, close=10)


def describe(call):
    call()
    elapsed = []
    for _ in range(25):
        timestamp = perf_counter()
        outcome = call()
        elapsed.append((perf_counter() - timestamp) * 1000)
    tracemalloc.start()
    call()
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    p95 = sorted(elapsed)[ceil(.95*len(elapsed))-1]
    return f"p50={median(elapsed):.2f}ms p95={p95:.2f}ms Python_peak={peak/1024:.1f}KiB", outcome


with TemporaryDirectory() as root:
    db.DATABASE = Path(root) / "test.sqlite"
    storage.MARKET_DIR = Path(root) / "market"
    db.initialize()
    print("Windows local temp SQLite/Parquet, warm filesystem, 25 repetitions")
    print("One separate tracemalloc sample per operation; RSS and native Arrow memory not measured")
    for mode in ("forex_weekly", "continuous"):
        offsets = range(10080) if mode == "continuous" else (*range(118*60), *range(166*60, 168*60))
        bars = [bar(start+index*step, 14 if index==166*60 else 12,
                    8 if index==118*60-1 else 9) for index in offsets]
        for offset in range(0, len(bars), 1440):
            storage.write_partition("MT5", mode, "EURUSD","candles","M1","BID",bars[offset:offset+1440])
        request = Evaluation(source="MT5_SAVED", symbol="EURUSD", timeframe="M1",
            cutoff=end+120, price_basis="BID", feed_id=mode, load_reference=True,
            candles=[bar(end),bar(end+step)], indicators=[dict(id="previous_week",schedule=dict(kind=mode))])
        loaded, context = describe(lambda: load_reference(request,start,end,step))
        calculated, output = describe(lambda: evaluate(request, lambda *_: context))
        full, output = describe(lambda: evaluate(request, load_reference))
        coverage = output["results"][0]["coverage"]
        print(f"{mode}: {coverage['retrieval']}; {coverage['status']}; "
              f"high={coverage['candidate_high']} low={coverage['candidate_low']} available_at={coverage['available_at']}")
        print(f"  source read {loaded} | calculation only {calculated} | combined {full}")
        if mode == "forex_weekly":
            missing = request.model_copy(update={"load_reference":False,"candles":[
                *[row for row,_ in context["rows"][:10]],*[row for row,_ in context["rows"][11:]],
                *request.candles]})
            missing_result = evaluate(missing)["results"][0]
            # The API case stays bounded. This in-process fixture checks the same calculation.
            print(f"  one missing open candle: {missing_result['coverage']['status']}; "
                  f"observed={missing_result['coverage']['observed_candles']}; "
                  f"confirmed_points={len(missing_result['series']['High'])}")
