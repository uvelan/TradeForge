"""Summarize the raw Playwright capacity receipts in a completed output directory."""
import json
import math
import statistics
import sys
from pathlib import Path


def summarize(receipt):
    rows = []
    for panes in (1, 2, 4):
        trials = [s for s in receipt["samples"] if s["panes"] == panes and not s["warmup"]]
        if len(trials) != 7:
            raise ValueError(f"Expected seven measured trials for {panes} panes")
        intervals = sorted(ms for trial in trials for ms in trial["frames"])
        heap = [trial["unmountedHeapBytes"] for trial in trials]
        rows.append({
            "size": receipt["size"], "panes": panes, "trials": len(trials),
            "frames": len(intervals), "median_ms": statistics.median(intervals),
            "p95_ms": intervals[math.ceil(.95 * len(intervals)) - 1],
            "unmounted_heap_first_bytes": heap[0], "unmounted_heap_last_bytes": heap[-1],
            "unmounted_heap_max_bytes": max(heap),
        })
    return rows


if __name__ == "__main__":
    receipts = [json.loads(path.read_bytes()) for path in Path(sys.argv[1]).rglob("samples.json")]
    if sorted(r["size"] for r in receipts) != [600, 5000]:
        raise SystemExit("Expected one complete receipt at each workload size")
    print(json.dumps([row for receipt in sorted(receipts, key=lambda r: r["size"])
                      for row in summarize(receipt)], indent=2))
