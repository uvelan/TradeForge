"""Compare Phase 1 and Phase 2 quote paths against identical temporary Parquet inputs."""
import csv
import gc
import math
import platform
import statistics
import subprocess
import sys
import tempfile
import tracemalloc
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from time import perf_counter
from unittest.mock import patch

import pyarrow.parquet as pq

from research_backend.app import db, saved_history, storage

BASELINE = "565143014c1e482ad4e652b5cdceada64b2ce27a"


def baseline_module():
    filename = "research_backend/app/saved_history.py"
    source = subprocess.check_output(["git", "show", f"{BASELINE}:{filename}"])
    namespace = {"__name__": "research_backend.app.phase1", "__package__": "research_backend.app"}
    exec(compile(source, "phase1_saved_history.py", "exec"), namespace)
    return namespace


def rss_mib():
    """Current process working set, including native Arrow pools, where available."""
    if sys.platform != "win32":
        return "NOT_VERIFIED"
    import ctypes
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD)] + [
            (name, ctypes.c_size_t) for name in
            ("peak", "working", "peak_paged", "paged", "peak_nonpaged", "nonpaged", "page", "peak_page")]
    data = Counters()
    data.cb = ctypes.sizeof(data)
    kernel = ctypes.WinDLL("kernel32")
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    get_memory = ctypes.WinDLL("psapi").GetProcessMemoryInfo
    get_memory.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    if get_memory(kernel.GetCurrentProcess(), ctypes.byref(data), data.cb):
        return round(data.working / 1048576, 2)
    return "NOT_VERIFIED"


def measure(operation, prepare):
    times = []
    for _ in range(5):
        prepare()
        start = perf_counter()
        operation()
        times.append((perf_counter() - start) * 1000)
    decoded, verified = [], []
    real_parquet, real_digest = pq.ParquetFile, storage.check_partition_digest

    class MeteredParquet(real_parquet):
        def read_row_groups(self, groups, *args, **kwargs):
            decoded.append(sum(self.metadata.row_group(group).num_rows for group in groups))
            return super().read_row_groups(groups, *args, **kwargs)

    def digest(file, expected):
        verified.append(1)
        return real_digest(file, expected)

    prepare()
    gc.collect()
    with patch.object(pq, "ParquetFile", MeteredParquet), patch.object(storage, "check_partition_digest", digest):
        tracemalloc.start()
        operation()
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
    return (round(statistics.median(times), 3), round(sorted(times)[math.ceil(0.95 * len(times)) - 1], 3),
            round(peak / 1048576, 3), rss_mib(), sum(decoded), sum(verified))


def create_sources(total, shards):
    files = []
    for index in range(shards):
        rows = [
            dict(time=ms//1000, time_ms=ms, source_order=ms-total*index//shards,
                 bid_units=110_000+ms%101, ask_units=110_003+ms%101, scale=100_000)
            for ms in range(total*index//shards, total*(index+1)//shards)
        ]
        item = storage.write_partition("DUKASCOPY", f"quote_{total}_{shards}", "EURUSD",
                                       "ticks", None, "BID_ASK_UNITS", rows)
        files.append(db.one("SELECT * FROM datasets WHERE id=?", (item["id"],)))
    return files


def main():
    print(f"# {platform.platform()}, Python {platform.python_version()}; five runs per case, warm OS file cache")
    print("# Cold = application cache cleared per sample; concurrent uses two simultaneous cold pages")
    print("# RSS = post-sample process working set; is cumulative and is not a per-operation peak")
    output = csv.writer(sys.stdout, lineterminator="\n")
    output.writerow(("ticks", "files", "bytes", "implementation", "scenario", "median_ms", "p95_ms",
                     "python_peak_mib", "process_rss_mib", "decoded_rows", "files_verified"))
    old = baseline_module()
    with tempfile.TemporaryDirectory(prefix="tradeforge-phase2-") as temp, ThreadPoolExecutor(max_workers=2) as pool:
        db.DATABASE = Path(temp) / "benchmark.sqlite"
        storage.MARKET_DIR = Path(temp) / "market"
        db.initialize()
        for total in (10_000, 100_000, 500_000):
            for shards in (1, 3):
                files = create_sources(total, shards)
                size = sum(storage.partition_path(part).stat().st_size for part in files)
                boundary = total // 3 if shards > 1 else total // 2
                low, high = boundary - 250, boundary + 249
                lower = (low, low if shards == 1 else 0)
                upper = (high, high if shards == 1 else 0)
                for name, read, cache in (
                    ("phase1", old["saved_quote_path"], old["_QUOTE_PATH_CACHE"]),
                    ("phase2", lambda f, s, e: saved_history.saved_quote_path(f, s, e, window_verified=True),
                     saved_history._QUOTE_PATH_CACHE),
                ):
                    def page(offset):
                        subset = read(files, lower, upper)[offset:offset+250]
                        assert [quote["time_ms"] for quote in subset] == list(range(low+offset, low+offset+250))

                    def warm():
                        cache.clear()
                        page(0)

                    for scenario, op, prepare in (
                        ("cold", lambda: page(0), cache.clear),
                        ("warm", lambda: page(0), warm),
                        ("sequential", lambda: (page(0), page(250)), cache.clear),
                        ("concurrent", lambda: list(pool.map(page, (0, 250))), cache.clear),
                    ):
                        output.writerow((total, shards, size, name, scenario, *measure(op, prepare)))
                        sys.stdout.flush()


if __name__ == "__main__":
    main()
