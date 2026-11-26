"""Check CCI(20) and Stochastic(14, 3) via the archived-candle chart API."""

import argparse
from contextlib import closing
import csv
from datetime import UTC, datetime
from fractions import Fraction
from hashlib import sha256
from io import StringIO
import json
import os
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs/implementation/evidence"
ARCHIVE_PIN = EVIDENCE / "TF-I10A1-dukascopy-nov13/pin.json"
CSV_PIN = EVIDENCE / "TF-I08E/numerical-checks.json"
DAY = "2026-11-13"
START = int(datetime.fromisoformat(f"{DAY}T13:00:00+00:00").timestamp())
END = int(datetime.fromisoformat(f"{DAY}T15:00:00+00:00").timestamp())
STEP = 300
SYMBOLS = ("EURUSD", "GBPUSD")
INDICATORS = [{"id": "cci", "period": 20}, {"id": "stochastic", "period": 14}]
FIRST = {"CCI": 20, "%K": 14, "%D": 16}
ABS_TOLERANCE = 1e-10
PRICE_FIELDS = ("open", "high", "low", "close")
CHART_ENDPOINT = "/api/indicators/evaluate"
TIMING_POLICY = "LEGACY_RESEARCH"


def utc(stamp):
    return datetime.fromtimestamp(stamp, UTC).isoformat(timespec="seconds")


def read_source(storage, symbol):
    path = storage / "tf-i08e-source" / f"{symbol}_{DAY}_M5_BID.csv"
    original = path.read_bytes()
    digest = sha256(original).hexdigest()
    assert digest == json.loads(CSV_PIN.read_text(encoding="utf-8"))[symbol]["provider_m5_sha256"], path
    candles = []
    for row in csv.DictReader(StringIO(original.decode("utf-8-sig"))):
        stamp = int(datetime.fromisoformat(row["Etc/UTC"]).timestamp())
        if START <= stamp < END:
            candles.append({
                "time": stamp,
                **{field: Fraction(row[field.title()]) for field in PRICE_FIELDS},
            })
    assert [bar["time"] for bar in candles] == list(range(START, END, STEP)), path
    return candles, digest


def exact_reference(bars):
    """Use exact rationals from the provider decimals throughout the calculation."""
    typical = [(bar["high"] + bar["low"] + bar["close"]) / 3 for bar in bars]
    cci = {}
    for index in range(19, len(bars)):
        window = typical[index - 19:index + 1]
        mean = sum(window) / 20
        deviation = sum(abs(price - mean) for price in window) / 20
        cci[bars[index]["time"]] = (
            (typical[index] - mean) / (Fraction(3, 200) * deviation) if deviation else Fraction(0)
        )

    k = {}
    for index in range(13, len(bars)):
        window = bars[index - 13:index + 1]
        high = max(bar["high"] for bar in window)
        low = min(bar["low"] for bar in window)
        k[bars[index]["time"]] = (
            100 * (bars[index]["close"] - low) / (high - low) if high > low else Fraction(50)
        )
    valid_k = list(k.items())
    d = {stamp: (ka + kb + kc) / 3 for (_, ka), (_, kb), (stamp, kc) in
         zip(valid_k, valid_k[1:], valid_k[2:])}
    return {"CCI": cci, "%K": k, "%D": d}


def checked_json(response):
    assert response.status_code == 200, (response.request.url, response.status_code, response.text)
    return response.json()


def load_chart(client, storage, symbol, source):
    selections = json.loads(ARCHIVE_PIN.read_text(encoding="utf-8"))["run"]["selection"]["series"]
    selection, = (series for series in selections.values()
                  if (series["symbol"], series["timeframe"]) == (symbol, "M5"))
    pin, = selection["partitions"]
    archive = (storage / "research").resolve()
    with closing(sqlite3.connect((archive / "tradeforge.sqlite3").as_uri() + "?mode=ro", uri=True)) as db:
        relative, = db.execute("SELECT relative_path FROM datasets WHERE id=? AND sha256=?",
                               (pin["id"], pin["sha256"])).fetchone()
    path = (archive / "market" / relative).resolve()
    path.relative_to(archive / "market")
    payload = path.read_bytes()
    assert sha256(payload).hexdigest() == pin["sha256"], path
    receipt = checked_json(client.post("/api/market/import/reviewed", files={"file": (path.name, payload)}))
    part, = receipt["partitions"]
    assert receipt["source_sha256"] == part["sha256"] == pin["sha256"]
    assert part["id"] == pin["id"] == selection["dataset_id"]
    assert part["symbol"] == symbol

    identity = {field: selection[field] for field in
                ("source", "symbol", "timeframe", "feed_id", "dataset_id", "price_basis", "partitions")}
    chart = checked_json(client.get("/api/market/candles", params={
        "source": identity["source"], "symbol": symbol, "timeframe": "M5", "start": START, "end": END,
    }))
    assert chart["synthetic"] is False
    assert all(chart[field] == identity[field] for field in ("source", "feed_id", "price_basis", "partitions"))
    assert chart["coverage"] == {"start": START, "end": END, "gaps": [], "duplicate_bars": 0, "truncated": False}
    candles = chart["candles"]
    assert [
        {"time": bar["time"], **{field: Fraction(str(bar[field])) for field in PRICE_FIELDS}}
        for bar in candles
    ] == source, f"{symbol}: chart history differs from the pinned provider OHLC"
    return candles, identity


def evaluate_cutoff(client, identity, cutoff, candles, count):
    data = checked_json(client.post(CHART_ENDPOINT, json={
        **identity, "cutoff": cutoff, "candles": candles, "indicators": INDICATORS,
        "frozen": True, "load_reference": True, "timing_policy": TIMING_POLICY,
    }))
    assert data["cutoff"] == cutoff
    assert data["input"]["candles"] == count, (identity["symbol"], utc(cutoff), data["input"])
    assert all(data["input"][field] == value for field, value in identity.items()), data["input"]
    series = {}
    assert len(data["results"]) == len(INDICATORS)
    for result, spec in zip(data["results"], INDICATORS):
        assert all(result[field] == value for field, value in spec.items()), result
        assert result["render"] == "pane"
        assert result["availability"] == "confirmed_input_close"
        series.update(result["series"])
    assert series.keys() == FIRST.keys(), series.keys()
    return series, data["input"]["sha256"]


def compare_points(symbol, label, cutoff, points, reference):
    expected = [(stamp, value) for stamp, value in reference.items() if stamp + STEP <= cutoff]
    assert len(points) == len(expected), (symbol, label, utc(cutoff), len(points), len(expected))
    errors = []
    for point, (stamp, value) in zip(points, expected):
        assert (point["time"], point["available_at"], point["status"]) == (stamp, stamp + STEP, "confirmed"), point
        error = abs(point["value"] - float(value))
        assert error <= ABS_TOLERANCE, (symbol, label, utc(cutoff), point, float(value), error)
        errors.append(error)
    return max(errors, default=0)


def value_at(point, reference):
    return {
        "bar_open_utc": utc(point["time"]),
        "available_utc": utc(point["available_at"]),
        "value": point["value"],
        "reference_value": float(reference[point["time"]]),
    }


def verify_symbol(client, source_storage, symbol):
    source, csv_digest = read_source(source_storage, symbol)
    reference = exact_reference(source)
    candles, identity = load_chart(client, source_storage, symbol, source)

    previous_series = {label: [] for label in FIRST}
    max_errors = dict.fromkeys(FIRST, 0)
    boundaries = {}
    for count in range(len(candles) + 1):
        cutoff = START + count * STEP
        prefix = candles[:count]
        if count:
            before, _ = evaluate_cutoff(client, identity, cutoff - 1, prefix, count - 1)
            assert before == previous_series, (symbol, utc(cutoff - 1), "premature publication")

        series, digest = evaluate_cutoff(client, identity, cutoff, prefix, count)
        if count < len(candles):
            assert evaluate_cutoff(client, identity, cutoff, candles, count) == (series, digest), (
                symbol, utc(cutoff), "future input changed the fixed-cutoff result",
            )
        for label, previous_points in previous_series.items():
            points = series[label]
            error = compare_points(symbol, label, cutoff, points, reference[label])
            max_errors[label] = max(max_errors[label], error)
            assert points[:len(previous_points)] == previous_points, (symbol, label, utc(cutoff), "prior point changed")
            assert len(points) == max(0, count - FIRST[label] + 1), (symbol, label, utc(cutoff))
            if count == FIRST[label]:
                boundaries[label] = {
                    "before_utc": utc(cutoff - 1),
                    "before_count": len(previous_points),
                    "at_utc": utc(cutoff),
                    "at_count": len(points),
                }
        previous_series = series

    summaries = {}
    for label, points in previous_series.items():
        summaries[label] = {
            "count": len(points),
            "first": value_at(points[0], reference[label]),
            "first_availability_boundary": boundaries[label],
            "final": value_at(points[-1], reference[label]),
            "last_point_before_final_cutoff": value_at(points[-2], reference[label]),
            "max_abs_error": max_errors[label],
        }
    return {
        **identity,
        "provider_csv_sha256": csv_digest,
        "candle_count": len(candles),
        "closed_prefixes_checked": len(candles) + 1,
        "append_invariance_checks": len(candles),
        "fixed_cutoff_future_input_checks": len(candles),
        "one_second_before_close_checks": len(candles),
        "series": summaries,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-storage", type=Path, default=ROOT / "storage",
                        help="Read-only storage root containing research/ and tf-i08e-source/")
    args = parser.parse_args()

    from fastapi.testclient import TestClient

    scratch_parent = ROOT / "storage"
    scratch_parent.mkdir(exist_ok=True)
    with TemporaryDirectory(prefix="t10-numerical-", dir=scratch_parent) as workspace:
        os.environ["TRADEFORGE_STORAGE"] = workspace
        os.environ["TRADEFORGE_DATA_DIR"] = str(Path(workspace) / "research")
        from research_backend.app.main import app

        with TestClient(app) as client:
            result = {symbol: verify_symbol(client, args.source_storage, symbol) for symbol in SYMBOLS}

    print(json.dumps({
        "status": "PASS",
        "chart_backend_endpoint": CHART_ENDPOINT,
        "timing_policy": TIMING_POLICY,
        "availability_basis": "nominal M5 close",
        "start_inclusive_utc": utc(START),
        "end_exclusive_utc": utc(END),
        "one_second_before_final_cutoff_utc": utc(END - 1),
        "absolute_tolerance": ABS_TOLERANCE,
        "reference": "exact rational arithmetic on provider CSV decimals, rounded to binary float for comparison",
        "instruments": result,
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
