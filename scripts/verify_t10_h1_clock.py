"""Independent acceptance for a delayed primary close and an entirely absent H1 interval."""
from argparse import ArgumentParser, SUPPRESS
import csv
from datetime import datetime
from fractions import Fraction
from hashlib import sha256
from io import StringIO
import json
import os
from pathlib import Path
import shutil
import subprocess
from sys import executable
from tempfile import TemporaryDirectory, gettempdir
from time import perf_counter

from scripts.t10_numerical_acceptance import CSV_PIN, DAY, PRICE_FIELDS, ROOT, START, STEP, SYMBOLS


HOUR = 3600
SHORT_END = START + HOUR
LONG_END = START + 3 * HOUR
DELAY = 20
FEED = "t10-h1-clock-control"
ENDPOINT = "/api/replay/sessions"


def dump(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def seed(source, directory):
    from research_backend.app.db import initialize
    from research_backend.app.storage import write_partition

    initialize()
    provider_pins = json.loads(CSV_PIN.read_text(encoding="utf-8"))
    result = {}
    for symbol in SYMBOLS:
        original = (source / "tf-i08e-source" / f"{symbol}_{DAY}_M5_BID.csv").read_bytes()
        digest = sha256(original).hexdigest()
        assert digest == provider_pins[symbol]["provider_m5_sha256"], symbol
        prices = {}
        for row in csv.DictReader(StringIO(original.decode("utf-8-sig"))):
            time = int(datetime.fromisoformat(row["Etc/UTC"]).timestamp())
            if START <= time < LONG_END:
                assert time not in prices, (symbol, time)
                prices[time] = {field: Fraction(row[field.title()]) for field in PRICE_FIELDS}
        assert sorted(prices) == list(range(START, LONG_END, STEP)), symbol
        rows = []
        for time, price in sorted(prices.items()):
            if symbol == "GBPUSD" and SHORT_END <= time < SHORT_END + HOUR:
                continue
            delay = DELAY if symbol == "EURUSD" and time == SHORT_END - STEP else 0
            rows.append({"time": time, "available_at": time + STEP + delay,
                         **{field: float(price[field]) for field in PRICE_FIELDS}})
        partition = write_partition("DUKASCOPY", FEED, symbol, "candles", "M5", "BID", rows)
        pin = {field: partition[field] for field in ("id", "sha256")}
        hours = {}
        for start in range(START, LONG_END, HOUR):
            children = [{**row, "partitions": [pin], "source_id": f"{pin['id']}:{row['time']}"}
                        for row in rows if start <= row["time"] < start + HOUR]
            if not children:
                hours[start] = {"status": "UNRESOLVED", "reason": "REFERENCE_ABSENT",
                                "ohlc": None, "available_at": None, "children": [],
                                "missing_slots": list(range(start, start + HOUR, STEP))}
                continue
            assert len(children) == HOUR // STEP
            decimals = [prices[row["time"]] for row in children]
            hours[start] = {
                "status": "COMPLETE", "reason": None, "missing_slots": [], "children": children,
                "available_at": max(row["available_at"] for row in children),
                "ohlc": {"open": float(decimals[0]["open"]),
                         "high": float(max(row["high"] for row in decimals)),
                         "low": float(min(row["low"] for row in decimals)),
                         "close": float(decimals[-1]["close"])},
            }
        result[symbol] = {"provider_csv_sha256": digest, "pin": pin, "hours": hours}
    reference = {"day": DAY, "feed_id": FEED, "start": START, "short_end": SHORT_END,
                 "long_end": LONG_END, "delay": DELAY, "symbols": result}
    dump(directory / "reference.json", reference)
    return json.loads((directory / "reference.json").read_text(encoding="utf-8"))


def verify_api(reference, output):
    from fastapi.testclient import TestClient
    from research_backend.app.main import app

    probes = []
    with TestClient(app, base_url="http://127.0.0.1") as client:
        def create(end):
            response = client.post(ENDPOINT, json={
                "source": "DUKASCOPY_SAVED", "symbol": "EURUSD", "timeframe": "M5",
                "start": START, "end": end, "views": [{"view": "2", "symbol": "GBPUSD", "timeframe": "M5"}],
            })
            assert response.status_code == 201, (response.status_code, response.text)
            snapshot = response.json()
            assert snapshot["market"]["coverage"]["end"] == snapshot["views"]["2"]["coverage"]["end"] == end
            return snapshot["id"]

        def probe(session, view, start, cutoff, expected_receipt, label):
            symbol = "EURUSD" if view == "primary" else "GBPUSD"
            expected = {
                "session_id": session, "view": view, "symbol": symbol, "source": "DUKASCOPY_SAVED",
                "feed_id": FEED, "price_basis": "BID", "source_timeframe": "M5", "timeframe": "H1",
                "start": start, "end": start + HOUR, "cutoff": cutoff, **expected_receipt,
            }
            response = client.get(f"{ENDPOINT}/{session}/higher-timeframe", params={
                "view": view, "hour_start": start, "cutoff": cutoff,
            })
            body = response.json()
            observed = {key: body.get(key) for key in expected} if response.status_code == 200 else body
            probes.append({"label": label, "http": response.status_code, "observed": observed,
                           "expected": expected, "passed": response.status_code == 200 and observed == expected})

        eur = reference["symbols"]["EURUSD"]["hours"]
        gbp = reference["symbols"]["GBPUSD"]["hours"]
        short = create(SHORT_END)
        probe(short, "primary", START, SHORT_END + DELAY - 1, {
            "status": "UNRESOLVED", "reason": "MISSING_OPEN_CANDLES", "available_at": None,
            "ohlc": None, "children": [], "missing_slots": [SHORT_END - STEP],
        }, "primary just before delayed child")
        probe(short, "2", START, SHORT_END, gbp[str(START)], "peer at nominal cutoff")
        cutoff = SHORT_END + DELAY
        probe(short, "primary", START, cutoff, eur[str(START)], "primary at shared cutoff")
        probe(short, "2", START, cutoff, gbp[str(START)], "peer at shared cutoff")
        long = create(LONG_END)
        probe(long, "2", SHORT_END, SHORT_END + HOUR, gbp[str(SHORT_END)], "fully missing open hour at close")
        probe(long, "2", LONG_END - HOUR, LONG_END, gbp[str(LONG_END - HOUR)], "later peer hour")
        probe(long, "2", SHORT_END, LONG_END, gbp[str(SHORT_END)], "fully missing open hour after later candles")

    passed = all(probe["passed"] for probe in probes)
    dump(output / "api.json", {"passed": passed, "probes": probes})
    for result in probes:
        print(f"{'PASS' if result['passed'] else 'FAIL'} HTTP {result['http']} {result['label']}", flush=True)
    return passed


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--source-storage", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--part", choices=("api", "browser"), required=True)
    parser.add_argument("--isolated", action="store_true", help=SUPPRESS)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if args.isolated:
        directory = Path(os.environ["TRADEFORGE_STORAGE"]).resolve()
        assert directory.parent == Path(gettempdir()).resolve() and directory.name.startswith("tradeforge-e2e-")
        assert directory / "research" == Path(os.environ["TRADEFORGE_DATA_DIR"]).resolve()
        assert not directory.joinpath("research").exists()
        assert not directory.is_relative_to(args.source_storage.resolve())
        reference = seed(args.source_storage.resolve(), directory)
        shutil.copyfile(directory / "reference.json", output / "reference.json")
        return 0 if args.part != "api" or verify_api(reference, output) else 1

    npm = shutil.which("npm.cmd") or shutil.which("npm")
    if args.part == "browser" and not npm:
        parser.error("npm is required")
    with TemporaryDirectory(prefix="tradeforge-e2e-") as path:
        env = {
            **os.environ, "TRADEFORGE_STORAGE": path, "TRADEFORGE_DATA_DIR": str(Path(path) / "research"),
            "TRADEFORGE_H1_CLOCK_STORAGE": path, "TRADEFORGE_H1_CLOCK_OUTPUT": str(output),
        }
        started = perf_counter()
        result = subprocess.run([executable, "-m", "scripts.verify_t10_h1_clock", "--isolated",
                                 "--source-storage", str(args.source_storage.resolve()),
                                 "--output", str(output), "--part", args.part], env=env)
        if args.part == "api" or result.returncode:
            print(f"API and fixture setup took {perf_counter() - started:.2f}s", flush=True)
            return result.returncode
        env.setdefault("TRADEFORGE_E2E_PYTHON", executable)
        env.setdefault("TRADEFORGE_E2E_API_PORT", "8633")
        env.setdefault("TRADEFORGE_E2E_UI_PORT", "4633")
        phases = {}
        for phase in ("initial", "restart"):
            started = perf_counter()
            run = subprocess.run([npm, "run", "e2e", "--", "--config", "playwright.t10-h1-clock.config.ts",
                                  "--retries=0", "--grep", phase], cwd=ROOT / "frontend",
                                 env=env)
            phases[phase] = {"exit_code": run.returncode, "elapsed_seconds": round(perf_counter() - started, 2)}
            print(f"{phase}: {phases[phase]}", flush=True)
        dump(output / "browser-phases.json", phases)
        return int(any(phase["exit_code"] for phase in phases.values()))


if __name__ == "__main__":
    raise SystemExit(main())
