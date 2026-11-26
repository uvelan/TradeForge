"""Independent UTC M5 BID reference for the two approved T06 BI5 hours."""
import argparse
from collections import defaultdict
import hashlib
import json
import lzma
from pathlib import Path
import struct
import subprocess
from time import sleep

from scripts.benchmark_t06 import DAY, PINS


def verify(raw, pin):
    if hashlib.sha256(raw).hexdigest() != pin:
        raise ValueError("Recorded workspace BI5 checksum mismatch")


def download_hour(url):
    # Use the runner's native HTTPS stack. Keep retries finite and leave integrity
    # errors to the caller; a different archive can never satisfy a transport retry.
    for attempt in range(3):
        response = subprocess.run(
            ["curl", "--silent", "--show-error", "--fail", "--proto", "=https",
             "--connect-timeout", "15", "--max-time", "45", "--max-filesize", "100000",
             "--retry", "1", "--retry-max-time", "60", url],
            stdout=subprocess.PIPE, check=False,
        )
        if response.returncode == 0:
            return response.stdout
        if attempt == 2 or response.returncode not in (7, 18, 28, 35, 52, 55, 56):
            response.check_returncode()
        sleep(10 * (attempt + 1))


def reference(directory, download=False):
    bars = []
    counts = []
    for hour, pin in PINS.items():
        path = directory / f"{hour}h_ticks.bi5"
        if download:
            url = f"https://datafeed.dukascopy.com/datafeed/EURUSD/2025/10/13/{path.name}"
            raw = download_hour(url)
            verify(raw, pin)
            directory.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        raw = path.read_bytes()
        verify(raw, pin)
        start = int(DAY.replace(hour=hour).timestamp())
        buckets = defaultdict(list)
        for ordinal, (ms, ask, bid, _, _) in enumerate(struct.iter_unpack(">IIIff", lzma.decompress(raw))):
            if not 0 <= ms < 3600000 or not 0 < bid <= ask:
                raise ValueError("Invalid quote in pinned source")
            buckets[start + ms // 300000 * 300].append((ms, ordinal, bid))
        counts.append(sum(map(len, buckets.values())))
        for stamp, observations in sorted(buckets.items()):
            prices = [bid for _, _, bid in sorted(observations)]
            bars.append(dict(time=stamp, open=prices[0], high=max(prices), low=min(prices), close=prices[-1]))
    if counts != [3171, 5465] or len(bars) != 24:
        raise ValueError("Unexpected recorded fixture volume")
    return dict(counts=counts, bars=bars)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--fetch", action="store_true", help="Fetch only the two pinned public Dukascopy hours")
    args = parser.parse_args()
    print(json.dumps(reference(args.source, args.fetch)))
