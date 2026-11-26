"""Restore quiescent local TradeForge catalogs and market archives into a new profile."""
import argparse
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from scripts.t04_cutover_rehearsal import DATABASES, digest, reconcile, snapshot
from tradeforge.shadow_manifest import register


def restore(source, destination):
    hashes = snapshot(source, destination)
    # These are private SQLite backups. Normalize their headers before read-only
    # verification so a read cannot create a WAL sidecar during registration.
    for name in DATABASES:
        database = destination / name
        with closing(sqlite3.connect(database)) as copied:
            if copied.execute("PRAGMA journal_mode=DELETE").fetchone() != ("delete",):
                raise ValueError("Restored catalog could not enter offline verification mode")
        hashes[name] = digest(database)
    state = reconcile(destination)
    if state["dangling_receipts"]:
        raise ValueError(f"Restore contains dangling publication receipts: {state['dangling_receipts']}")
    manifest = destination / "registered-candles.sqlite"
    registration = register(destination, destination/"research/tradeforge.sqlite3",
                            destination/"research/market", manifest)
    report = {"hashes":hashes, "references":state, "registration":registration}
    (destination/"restore-report.json").write_text(json.dumps(report,sort_keys=True,indent=2),encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source",type=Path)
    parser.add_argument("destination",type=Path)
    args = parser.parse_args()
    print(json.dumps(restore(args.source,args.destination),indent=2))
