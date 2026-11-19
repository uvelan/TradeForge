"""Exercise the real read-only capture adapter with a disposable evidence ledger."""
import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import MetaTrader5 as mt5

from research_backend.app import db, storage
from research_backend.app.arrival_capture import CaptureClock, CaptureConfig, Worker, claim, observe, release
from research_backend.app.market import mt5_rates


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("terminal")
    args = parser.parse_args()
    if not mt5.initialize(args.terminal, timeout=5000):
        print(json.dumps({"connection": "failed", "code": mt5.last_error()[0]}))
        return
    try:
        with TemporaryDirectory(prefix="tradeforge-arrival-gate-") as directory:
            db.DATABASE = Path(directory) / "gate.sqlite3"
            storage.MARKET_DIR = Path(directory) / "market"
            db.initialize()
            scopes = {}
            for timeframe in ("M1", "M5"):
                config = CaptureConfig(timeframe=timeframe)
                clock = CaptureClock()
                worker = Worker(config, *claim(config, clock), clock)
                try:
                    for symbol in config.symbols:
                        try:
                            result = observe(worker, mt5_rates, symbol)
                            scopes[f"{symbol} {timeframe}"] = result["state"]
                        except ValueError as error:
                            scopes[f"{symbol} {timeframe}"] = str(error)
                        except RuntimeError:
                            scopes[f"{symbol} {timeframe}"] = "MT5_READ_UNAVAILABLE"
                finally:
                    release(tuple(worker.sessions.values()))
            print(json.dumps({"connection": "initialized", "scopes": scopes,
                "observations": db.one("SELECT COUNT(*) AS n FROM arrival_observations")["n"],
                "witnesses": db.one("SELECT COUNT(*) AS n FROM arrival_publications")["n"],
                "leases_after_release": db.one("SELECT COUNT(*) AS n FROM arrival_leases")["n"]}, indent=2))
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
