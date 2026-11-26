"""Save read-only November 2025 EURUSD terminal tick responses to ignored local storage."""
import argparse
from datetime import UTC, datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
import time

import MetaTrader5 as mt5
import numpy as np


def publish_temp(tmp, dest, *, reuse):
    digest = hashlib.sha256(tmp.read_bytes()).hexdigest()
    try:
        os.link(tmp, dest)
    except FileExistsError:
        if not reuse or hashlib.sha256(dest.read_bytes()).hexdigest() != digest:
            raise FileExistsError(f"Existing {dest.name} is preserved. Choose a new capture directory.")
    finally:
        tmp.unlink(missing_ok=True)
    return digest


def capture(directory):
    directory.mkdir(exist_ok=True, parents=True)
    manifest = directory / "capture.json"
    if manifest.exists():
        raise FileExistsError("Completed capture.json is preserved. Choose a new capture directory.")
    records = []
    started = datetime.now(UTC).isoformat()
    if not mt5.initialize(timeout=15000):
        raise RuntimeError(f"Terminal initialization failed, code {mt5.last_error()[0]}")
    try:
        info = mt5.symbol_info("EURUSD")
        terminal = mt5.terminal_info()
        account = mt5.account_info()
        if not account or not account.server:
            raise ValueError("Terminal server identity unavailable")
        server_hash = hashlib.sha256(account.server.encode()).hexdigest()[:24]
        if not info or info.digits != 5:
            raise ValueError("Capture requires the EURUSD five-decimal terminal symbol")
        for day in range(1, 31):
            start = datetime(2025, 11, day, tzinfo=UTC)
            end = start + timedelta(days=1)
            dest = directory / (start.date().isoformat() + ".npy")
            attempts = []
            for attempt in range(2):
                rows = mt5.copy_ticks_range("EURUSD", start, end, mt5.COPY_TICKS_ALL)
                code = mt5.last_error()[0]
                attempts.append({"code": code, "response_rows": None if rows is None else len(rows)})
                if rows is not None and code == 1:
                    break
                if attempt == 0:
                    time.sleep(2)
            record = {"day": start.date().isoformat(), "start": int(start.timestamp()), "end": int(end.timestamp()), "attempts": attempts}
            if rows is not None and code == 1:
                epoch = rows["time_msc"]
                if np.any(epoch < int(start.timestamp())*1000) or np.any(epoch >= int(end.timestamp())*1000+1000):
                    raise ValueError(f"Unexpected terminal response bounds for {start.date()}")
                rows = rows[epoch < int(end.timestamp())*1000]
                if np.any(np.diff(rows["time_msc"]) < 0):
                    raise ValueError(f"Out-of-order terminal history at {start.date()}")
                with NamedTemporaryFile(dir=directory, delete=False) as file:
                    tmp = Path(file.name)
                    try:
                        np.save(file, rows, allow_pickle=False)
                    except BaseException:
                        file.close()
                        tmp.unlink(missing_ok=True)
                        raise
                digest = publish_temp(tmp, dest, reuse=True)
                record.update(rows=len(rows), sha256=digest)
            records.append(record)
            print(start.date(), record.get("rows"), attempts, flush=True)
        receipt = {"symbol": "EURUSD", "source": "MT5_COPY_TICKS_ALL", "digits": info.digits, "server_hash": server_hash,
                   "terminal_build": terminal.build, "connected": terminal.connected,
                   "python_package": mt5.__version__, "started_utc": started,
                   "finished_utc": datetime.now(UTC).isoformat(), "days": records}
        with NamedTemporaryFile(dir=directory, delete=False) as file:
            file.write((json.dumps(receipt, indent=2)+"\n").encode())
            tmp = Path(file.name)
        publish_temp(tmp, manifest, reuse=False)
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    capture(parser.parse_args().directory)
