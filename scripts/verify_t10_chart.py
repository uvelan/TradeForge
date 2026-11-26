"""Verify a genuine chart journey across an API and Chromium restart on a disposable DB."""
import argparse
from pathlib import Path
import json
import os
import shutil
import subprocess
import tempfile
from time import perf_counter


def write_indicator_reference(source_storage: Path, workspace: Path):
    from scripts.t10_numerical_acceptance import (
        ARCHIVE_PIN, ABS_TOLERANCE, DAY, END, START, STEP, SYMBOLS, exact_reference, read_source,
    )

    pinned = json.loads(ARCHIVE_PIN.read_text(encoding="utf-8"))["run"]["selection"]["series"]
    lanes = {}
    for symbol in SYMBOLS:
        selection, = (lane for lane in pinned.values()
                      if lane["symbol"] == symbol and lane["timeframe"] == "M5")
        bars, digest = read_source(source_storage, symbol)
        lanes[symbol] = {
            "market": {field: selection[field] for field in
                       ("symbol", "timeframe", "source", "feed_id", "price_basis", "partitions")},
            "providerCsvSha256": digest,
            "bars": [{field: float(value) if field != "time" else value for field, value in bar.items()}
                     for bar in bars],
            "series": {label: [{"time": stamp, "value": float(value)} for stamp, value in points.items()]
                       for label, points in exact_reference(bars).items()},
        }
    (workspace / "t10-indicators-reference.json").write_text(json.dumps({
        "day": DAY, "start": START, "end": END, "step": STEP,
        "absoluteTolerance": ABS_TOLERANCE, "lanes": lanes,
    }), encoding="utf-8")


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="Directory for receipts and screenshots")
    journey = parser.add_mutually_exclusive_group()
    journey.add_argument("--h1-replay", action="store_true",
                         help="Verify hourly evidence in the saved M5 replay")
    journey.add_argument("--indicators-checkpoint", action="store_true",
                        help="Verify the final dual-chart oscillator checkpoint")
    journey.add_argument("--structure-liquidity", action="store_true",
                         help="Verify selected structure and liquidity events in the saved M5 replay")
    parser.add_argument("--structure-reference", type=Path,
                        help="Optional override for the independent structure and liquidity reference")
    parser.add_argument("--source-storage", type=Path,
                        help="Read-only input storage containing research/ and tf-i08e-source/")
    options = parser.parse_args()
    output = options.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    npm = shutil.which("npm.cmd") or shutil.which("npm")
    if not npm:
        parser.error("npm is required")
    with tempfile.TemporaryDirectory(prefix="tradeforge-e2e-") as storage:
        env = {**os.environ, "TRADEFORGE_PAIR_STORAGE": storage,
               "TRADEFORGE_T10_CHART": "1", "TRADEFORGE_T10_CHART_OUTPUT": str(output),
               "TRADEFORGE_COMPARISON_DAY": "2026-11-13"}
        env.setdefault("TRADEFORGE_E2E_API_PORT", "8283")
        env.setdefault("TRADEFORGE_E2E_UI_PORT", "4283")
        config = "playwright.comparison.config.ts"
        source_storage = (options.source_storage or root / "storage").resolve()
        if options.source_storage or options.indicators_checkpoint or options.h1_replay or options.structure_liquidity:
            env["TRADEFORGE_COMPARISON_SOURCE_DIR"] = str(source_storage / "research")
        if options.indicators_checkpoint:
            write_indicator_reference(source_storage, Path(storage))
            config = "playwright.t10-indicators.config.ts"
        elif options.h1_replay:
            config = "playwright.t10-h1.config.ts"
        elif options.structure_liquidity:
            reference = options.structure_reference or (
                root / "docs/implementation/evidence/T10-STRUCTURE-LIQUIDITY/oracle.json")
            env["TRADEFORGE_T10_STRUCTURE_REFERENCE"] = str(reference.resolve(strict=True))
            config = "playwright.t10-structure-liquidity.config.ts"
        for reopen in ("0", "1"):
            start = perf_counter()
            subprocess.run([npm, "run", "e2e", "--", "--config", config, "--retries=0"],
                           cwd=root / "frontend", check=True, env={**env, "TRADEFORGE_PAIR_REOPEN": reopen})
            print(f"{'Restart' if reopen == '1' else 'Initial journey'} passed in {perf_counter()-start:.2f}s",
                  flush=True)


if __name__ == "__main__":
    main()
