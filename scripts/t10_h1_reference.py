"""Build the independent H1 replay oracle from checksummed provider CSVs."""
import argparse
import json
from pathlib import Path

from scripts.t10_numerical_acceptance import (
    ARCHIVE_PIN, DAY, END, PRICE_FIELDS, ROOT, START, STEP, SYMBOLS, read_source,
)

HOUR = 60 * 60
BOUNDARIES = (START + HOUR - 1, START + HOUR, END - 1, END)


def build_reference(source_storage: Path) -> dict:
    selections = json.loads(ARCHIVE_PIN.read_text(encoding="utf-8"))["run"]["selection"]["series"]
    lanes = {}
    for symbol in SYMBOLS:
        selection, = (entry for entry in selections.values() if entry["symbol"] == symbol and entry["timeframe"] == "M5")
        partition, = selection["partitions"]
        bars, digest = read_source(source_storage, symbol)
        hours = []
        for start in range(START, END, HOUR):
            group = [bar for bar in bars if start <= bar["time"] < start + HOUR]
            assert [bar["time"] for bar in group] == list(range(start, start + HOUR, STEP))
            hours.append({
                "start": start,
                "end": start + HOUR,
                "available_at": start + HOUR,
                "ohlc": {
                    "open": float(group[0]["open"]),
                    "high": float(max(bar["high"] for bar in group)),
                    "low": float(min(bar["low"] for bar in group)),
                    "close": float(group[-1]["close"]),
                },
                "children": [{
                    "open_time": bar["time"],
                    "available_at": bar["time"] + STEP,
                    "partition": partition,
                    "ohlc": {field: float(bar[field]) for field in PRICE_FIELDS},
                } for bar in group],
            })
        lanes[symbol] = {
            "market": {field: selection[field] for field in (
                "symbol", "timeframe", "source", "feed_id", "price_basis", "partitions",
            )},
            "provider_csv_sha256": digest,
            "hours": hours,
        }
    return {
        "date_utc": DAY,
        "interval_utc_seconds": [START, END],
        "availability_model": "Nominal M5 close with zero modeled delivery delay",
        "checks": list(BOUNDARIES),
        "symbols": lanes,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-storage", type=Path, default=ROOT / "storage")
    args = parser.parse_args()
    print(json.dumps(build_reference(args.source_storage), indent=2))


if __name__ == "__main__":
    main()
