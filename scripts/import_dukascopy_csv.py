"""Import one original UTC M1 Dukascopy web export into the configured catalog."""
import argparse
from datetime import date
import json
from pathlib import Path

from research_backend.app.db import initialize
from research_backend.app.historical_csv import CSV_MAX_BYTES, import_csv


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("symbol",choices=("EURUSD","GBPUSD"))
    parser.add_argument("day",type=date.fromisoformat)
    parser.add_argument("file",type=Path)
    parser.add_argument("--side",choices=("BID","ASK"),default="BID")
    parser.add_argument("--original-name",help="The exact name shown by the provider when it supplied the file")
    args = parser.parse_args()
    with args.file.open("rb") as handle:
        payload = handle.read(CSV_MAX_BYTES+1)
    initialize()
    print(json.dumps(import_csv(payload,args.symbol,args.day,args.original_name or args.file.name,side=args.side),indent=2))


if __name__ == "__main__":
    main()
