"""Independent UTC M5 BID reference for the two approved T06 BI5 hours."""
import argparse
from collections import defaultdict
import hashlib
import json
import lzma
from pathlib import Path
import struct
from time import sleep
from urllib.error import HTTPError
from urllib.request import urlopen

from scripts.benchmark_t06 import DAY, PINS


def verify(raw, pin):
    if hashlib.sha256(raw).hexdigest() != pin:
        raise ValueError("Recorded workspace BI5 checksum mismatch")


def download_hour(url):
    for attempt in range(3):
        try:
            with urlopen(url, timeout=45) as response:
                return response.read(100_000)
        except HTTPError as exc:
            if exc.code not in (429, 502, 503, 504) or attempt == 2:
                raise
            retry = exc.headers.get("Retry-After")
            if retry and (not retry.isdigit() or int(retry) > 60):
                raise
            sleep(max(15 * (attempt + 1), int(retry or 0)))


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
