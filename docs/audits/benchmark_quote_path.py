"""Measure quote-path retrieval against the specified Git baseline using temp Parquet."""
import gc
import platform
import statistics
import subprocess
import tempfile
import tracemalloc
from pathlib import Path
from time import perf_counter

from research_backend.app import db, saved_history, storage

BASELINE = "0dcc2156becf58e6f4f571435bf635dc8c7a58f2"


def baseline_reader():
    source = subprocess.check_output(["git", "show", f"{BASELINE}:research_backend/app/saved_history.py"])
    namespace = {"__name__": "research_backend.app.saved_history_baseline", "__package__": "research_backend.app"}
    exec(compile(source, "baseline_saved_history.py", "exec"), namespace)
    return namespace["read_tick_partitions"]


def measure(label, operation, expected, repetitions=5):
    timings = []
    for _ in range(repetitions):
        start = perf_counter()
        result = operation()
        timings.append((perf_counter() - start) * 1000)
        assert len(result) == expected
    gc.collect()
    tracemalloc.start()
    assert len(operation()) == expected
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    print(f"{label}: median={statistics.median(timings):.3f} ms; Python peak={peak / 1048576:.3f} MiB")


def main():
    old_read = baseline_reader()
    with tempfile.TemporaryDirectory(prefix="tradeforge-quote-benchmark-") as temp:
        db.DATABASE = Path(temp) / "benchmark.sqlite"
        storage.MARKET_DIR = Path(temp) / "market"
        db.initialize()
        partitions = []
        for shard in range(3):
            rows = []
            for index in range(40_000):
                ms = shard * 40_000 + index
                bid = 110_000 + (ms % 101)
                rows.append(dict(time=ms//1000, time_ms=ms, source_order=index,
                                 bid_units=bid, ask_units=bid+3, scale=100_000))
            record = storage.write_partition("DUKASCOPY", "benchmark", "EURUSD", "ticks", None,
                                             "BID_ASK_UNITS", rows)
            partitions.append(db.one("SELECT * FROM datasets WHERE id=?", (record["id"],)))
        size = sum(storage.partition_path(part).stat().st_size for part in partitions)
        lower, upper = (39_900, 0), (40_399, 0)

        def before():
            # Different local ordinal keys but identical time-range coverage in this fixture.
            return [q for q in old_read(partitions) if lower[0] <= q["time_ms"] <= upper[0]][:250]

        def cold():
            saved_history._QUOTE_PATH_CACHE.clear()
            return saved_history.saved_quote_path(partitions, lower, upper)[:250]

        def warm():
            return saved_history.saved_quote_path(partitions, lower, upper)[:250]

        print(f"{platform.platform()} / Python {platform.python_version()}")
        print(f"120000 quotes, 3 Parquet partitions, {size} bytes on disk; 500 path quotes, 250/page")
        print("Five timed runs per case, warm OS file cache, Python allocation peak measured separately")
        assert [q["time_ms"] for q in before()] == [q["time_ms"] for q in cold()]
        measure("Baseline full decode", before, 250)
        measure("New cold full decode", cold, 250)
        measure("New warm cache + full source SHA-256", warm, 250)

        overlapping = [dict(time=1, time_ms=1000, source_order=order, bid_units=100,
                            ask_units=101, scale=100) for order in (0, 1)]
        sources = []
        for quote in overlapping:
            record = storage.write_partition("DUKASCOPY", "benchmark", "GBPUSD", "ticks", None,
                                             "BID_ASK_UNITS", [quote])
            sources.append(db.one("SELECT * FROM datasets WHERE id=?", (record["id"],)))
        print(f"Baseline legacy overlap produced {len(old_read(sources))} executable events")
        try:
            saved_history.read_tick_partitions(sources)
        except ValueError as error:
            print(f"Hardened legacy overlap: {error}")
        else:
            raise AssertionError("Ambiguous legacy data was accepted")


if __name__ == "__main__":
    main()
