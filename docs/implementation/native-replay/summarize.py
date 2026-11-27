"""Summarize four-pane PR #40 and native Playwright receipts from the same host."""
import json
import math
import statistics
import sys
from pathlib import Path


def distribution(values):
    ordered = sorted(values)
    return {"n": len(ordered), "median_ms": round(statistics.median(ordered), 3),
            "p95_ms": round(ordered[math.ceil(.95 * len(ordered)) - 1], 3)}


def summarize(path):
    receipt = json.loads(Path(path).read_bytes())
    samples = [row for row in receipt["samples"] if row.get("panes", 4) == 4 and not row["warmup"]]
    if len(samples) < 7:
        raise ValueError("Fewer than seven measured four-pane trials")
    report = {"trials": len(samples), "frames_ms": distribution(frame for row in samples for frame in row["frames"]),
              "matched_seven_frames_ms": distribution(frame for row in samples[:7] for frame in row["frames"]),
              "first_last_unmounted_heap_bytes": [samples[i]["unmountedHeapBytes"] for i in (0, -1)],
              "device": receipt["device"], "errors": receipt["errors"]}
    if "openMs" in samples[0]:
        report["verified_open_ms"] = distribution(row["openMs"] for row in samples)
        report["process_lifetime_peaks"] = receipt["memory"]
    return report


if __name__ == "__main__":
    print(json.dumps({name: summarize(path) for name, path in zip(
        ("intercepted_baseline", "native"), sys.argv[1:], strict=True)}, indent=2))
