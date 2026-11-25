"""Verify both local chart partitions and read a direct Arrow OHLC reference."""
import argparse
from contextlib import closing
from datetime import UTC, date, datetime, time
from hashlib import sha256
import io
import json
from pathlib import Path
import sqlite3

import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
PIN = ROOT / "docs/implementation/evidence/TF-I10A1-dukascopy-nov13/pin.json"


def reference(day: date, destination: Path | None = None):
    selection = json.loads(PIN.read_text())["run"]["selection"]["series"]
    source = ROOT / "storage/research"
    start = int(datetime.combine(day, time(13), UTC).timestamp())
    result = {}
    with closing(sqlite3.connect((source / "tradeforge.sqlite3").as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        for lane, symbol in [("PRIMARY:M5", "EURUSD"), ("COMPARISON:M5", "GBPUSD")]:
            contract = selection[lane]
            part, = contract["partitions"]
            row = db.execute("SELECT * FROM datasets WHERE id=?", (part["id"],)).fetchone()
            fingerprint = dict(id=contract["dataset_id"], sha256=part["sha256"], symbol=symbol,
                               provider="DUKASCOPY", feed_id="dukascopy-web-m1-v1", kind="candles",
                               timeframe="M5", price_basis="BID")
            if contract["source"] != "DUKASCOPY_SAVED" or any(
                contract[k] != fingerprint[k] for k in ("symbol", "timeframe", "feed_id", "price_basis")
            ):
                raise ValueError(f"{lane} inconsistent selection in {PIN}")
            if row is None or any(row[k] != v for k, v in fingerprint.items()):
                raise ValueError(f"{lane} missing or mismatched catalog row for {part['id']}")
            path = (source / "market" / row["relative_path"]).resolve()
            relative = path.relative_to((source / "market").resolve())
            raw = path.read_bytes()
            if sha256(raw).hexdigest() != part["sha256"]:
                raise ValueError(f"{lane} checksum mismatch at {path}")
            bars = pq.read_table(io.BytesIO(raw), columns=["time", "open", "high", "low", "close"]).to_pylist()
            window = [bar for bar in bars if start <= bar["time"] < start + 7200]
            if [bar["time"] for bar in window] != list(range(start, start + 7200, 300)):
                raise ValueError(f"{symbol} {part['id']}: {len(window)} bars in {day} [13:00,15:00) UTC; "
                                 f"catalog covers {row['first_time']} to {row['last_time']}")
            result[symbol] = dict(partition=part, bars=bars, window=window)
            if destination is not None:
                # Called only on startup of the isolated Playwright server.
                from research_backend.app.db import DATABASE, transaction
                if DATABASE.parent.resolve() != destination.resolve() or destination.resolve() == source.resolve():
                    raise ValueError("Catalog and isolated output directory must match")
                target = destination / "market" / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(raw)
                with transaction() as out:
                    keys = row.keys()
                    out.execute(f"INSERT INTO datasets ({','.join(keys)}) VALUES ({','.join('?' for _ in keys)})", tuple(row))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("day", type=date.fromisoformat)
    print(json.dumps(reference(parser.parse_args().day)))
