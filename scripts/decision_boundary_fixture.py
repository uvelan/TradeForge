"""Controlled M5 partitions for the Windows-local journal round trip."""
from datetime import UTC, datetime
import json
import sys

from research_backend.app import db, storage


def prepare():
    fixtures = []
    for day, count, scenario in (("2020-01-06", 3, "delayed"), ("2020-02-03", 3, "source-error"),
                                 ("2020-03-02", 5000, "long")):
        start = int(datetime.fromisoformat(day).replace(tzinfo=UTC).timestamp())
        pins = {}
        for symbol, price in (("EURUSD", 110000), ("GBPUSD", 130000)):
            if scenario == "source-error" and symbol == "GBPUSD":
                continue
            rows = []
            for i in range(count):
                t = start + i * 300
                units = price + i
                row = dict(time=t, open=units / 100000, high=(units+2) / 100000,
                           low=(units-1) / 100000, close=(units+1) / 100000)
                if scenario == "delayed" and symbol == "GBPUSD" and i == 0:
                    row["available_at"] = t + 600
                rows.append(row)
            part = storage.write_partition("DUKASCOPY", "controlled-decision", symbol,
                                           "candles", "M5", "BID", rows)
            pins[symbol] = {key:part[key] for key in ("id","sha256")}
        fixtures.append(dict(scenario=scenario, start=start, count=count, pins=pins))
    return fixtures


if __name__ == "__main__":
    if sys.argv[1:] == ["counts"]:
        print(json.dumps({table:db.one(f"SELECT count(*) AS n FROM {table}")["n"]
                          for table in ("journal_entries", "chart_snapshots", "trades", "plan_paper_events")}))
    elif not sys.argv[1:]:
        print(json.dumps(prepare()))
    else:
        raise SystemExit("Expected no argument or counts")
