"""Read-only MT5 time evidence. Raw epochs are rendered as UTC, never normalized."""
import argparse
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
import platform
import subprocess
from time import monotonic_ns, sleep


def utc(epoch):
    return datetime.fromtimestamp(epoch, UTC).isoformat()


def sync_status():
    if platform.system() != "Windows":
        return {"available": False}
    try:
        output = subprocess.run(["w32tm", "/query", "/status"], capture_output=True,
                                text=True, errors="replace", timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return {"available": False}
    fields = {}
    for line in output.stdout.splitlines():
        key, separator, value = line.partition(":")
        if separator and key.strip() in {"Leap Indicator", "Stratum"}:
            # Emit numeric synchronization indicators only.
            first = value.strip().split(maxsplit=1)
            if first and first[0].isdecimal():
                fields[key.strip()] = int(first[0])
    return {"available": output.returncode == 0, "exit_code": output.returncode, **fields}


def bars(rates):
    if rates is None:
        return None
    return [{"raw_time": value, "rendered_utc": utc(value)} for value in (int(row["time"]) for row in rates)]


def report(mt5, symbols, samples, terminal_path=None):
    if not mt5.initialize(*([terminal_path] if terminal_path else []), timeout=5000):
        return {"status": "INITIALIZATION_FAILED", "code": mt5.last_error()[0]}
    try:
        host = datetime.now(UTC)
        terminal = mt5.terminal_info()
        account = mt5.account_info()
        evidence = {"status": "READ_ONLY_DIAGNOSTIC", "python_package": mt5.__version__,
                    "terminal_version": mt5.version(), "terminal_build": terminal.build if terminal else None,
                    "terminal_connected": terminal.connected if terminal else False,
                    "server_hash": sha256(account.server.encode()).hexdigest()[:24] if account else None,
                    "host_utc": host.isoformat(), "host_local": host.astimezone().isoformat(),
                    "host_tzname": host.astimezone().tzname(), "clock_sync": sync_status(),
                    "symbols": {}}
        for symbol in symbols:
            info = mt5.symbol_info(symbol)
            item = {"chart_basis": {0: "BID", 1: "LAST_TRADE"}.get(info.chart_mode) if info else None,
                    "ticks": [], "bars": {}}
            evidence["symbols"][symbol] = item
            identities = set()
            for index in range(samples):
                started = monotonic_ns()
                tick = mt5.symbol_info_tick(symbol)
                received = datetime.now(UTC)
                elapsed = (monotonic_ns() - started) / 1_000_000
                if tick is None:
                    item["ticks"].append({"code": mt5.last_error()[0], "response_ms": elapsed})
                else:
                    identities.add((tick.time_msc, tick.bid, tick.ask, tick.last, tick.volume))
                    item["ticks"].append({"time": tick.time, "time_msc": tick.time_msc,
                        "rendered_utc": utc(tick.time_msc / 1000), "received_utc": received.isoformat(),
                        "apparent_age_seconds": round(received.timestamp() - tick.time_msc / 1000, 3),
                        "response_ms": round(elapsed, 3)})
                if index + 1 < samples:
                    sleep(1)
            item["distinct_tick_observations"] = len(identities)
            for frame in ("M1", "M5"):
                interval = getattr(mt5, "TIMEFRAME_" + frame)
                end = datetime.now(UTC)
                start = end - timedelta(minutes=15)
                tick_epoch = item["ticks"][-1].get("time")
                anchored = datetime.fromtimestamp(tick_epoch, UTC) if tick_epoch else None
                started = monotonic_ns()
                position = bars(mt5.copy_rates_from_pos(symbol, interval, 0, 3))
                from_host = bars(mt5.copy_rates_range(symbol, interval, start, end))
                from_tick = (bars(mt5.copy_rates_range(symbol, interval, anchored-timedelta(minutes=15), anchored))
                             if anchored else None)
                item["bars"][frame] = {"by_position": position, "latest_position_may_be_developing": True,
                    "host_query": [start.isoformat(), end.isoformat()], "by_host_utc_range": from_host,
                    "tick_epoch_query": [(anchored-timedelta(minutes=15)).isoformat(), anchored.isoformat()]
                    if anchored else None, "by_tick_epoch_range": from_tick,
                    "response_total_ms": round((monotonic_ns()-started)/1_000_000, 3)}
        return evidence
    finally:
        mt5.shutdown()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("symbols", nargs="*", default=["EURUSD", "GBPUSD"])
    parser.add_argument("--samples", type=int, choices=range(2, 16), default=5)
    parser.add_argument('--terminal', help='Optional installed terminal executable')
    args = parser.parse_args()
    import MetaTrader5 as mt5

    print(json.dumps(report(mt5, args.symbols, args.samples, args.terminal), indent=2))


if __name__ == "__main__":
    main()
