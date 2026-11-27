"""Independent source and policy oracle for the November 2025 EURUSD replay."""

from datetime import UTC, datetime
from fnmatch import fnmatch
from hashlib import sha256
from itertools import groupby
from io import BytesIO
import lzma
import math
import os
import struct
from typing import NamedTuple
from zoneinfo import ZoneInfo


START = int(datetime(2025, 11, 1, tzinfo=UTC).timestamp())
END = int(datetime(2025, 12, 1, tzinfo=UTC).timestamp())
SLOT = 600
GAP_MS = 60_000
CONTROL = {
    13: "2f47bcb14e0a2c4dcd6d65eb819b85f1b5168b656d1f5f67c3dcd2240569f014",
    14: "9d40aa3147d93e6daa2218933877adb9471dc02ff08b19fc0d272f1eb781688f",
}
NEW_YORK = ZoneInfo("America/New_York")


class Quote(NamedTuple):
    time_ms: int
    ordinal: int
    bid_units: int
    ask_units: int
    source_sha256: str

    def proof(self):
        return self._asdict()


def open_hour(stamp):
    local = datetime.fromtimestamp(stamp, NEW_YORK)
    if local.weekday() == 5:
        return False
    if local.weekday() == 6:
        return local.hour >= 17
    return local.weekday() != 4 or local.hour < 17


def decode_bi5(path, stamp):
    payload = path.read_bytes()
    digest = sha256(payload).hexdigest()
    decoder = lzma.LZMADecompressor(memlimit=64 * 1024 * 1024)
    decoded = decoder.decompress(payload, max_length=10_000_001)
    if not decoder.eof or decoder.unused_data or len(decoded) > 10_000_000:
        raise ValueError(f"Invalid BI5 stream in {path.name} at {stamp}")
    output = []
    for ordinal, (offset, ask, bid, av, bv) in enumerate(struct.iter_unpack(">IIIff", decoded)):
        if offset >= 3_600_000 or not 0 < bid <= ask or any(
            not math.isfinite(value) or value < 0 for value in (av, bv)
        ):
            raise ValueError(("Invalid BI5 row", stamp, ordinal))
        output.append(Quote(stamp * 1000 + offset, ordinal, bid, ask, digest))
    if not output:
        raise ValueError(("Empty BI5", stamp))
    return sorted(output)


def control_files(folder):
    paths = {}
    for hour, digest in CONTROL.items():
        name = f"{hour}h_ticks.bi5"
        path = folder / "2025-11-13" / name
        if not path.exists():
            path = folder / name
        if sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError(f"Control SHA-256 mismatch at hour {hour}")
        paths[int(datetime(2025, 11, 13, hour, tzinfo=UTC).timestamp())] = path
    return paths


class BI5Source:
    def __init__(self, controls, archive=None):
        self.files = control_files(controls)
        self.archive_file_count = 0
        if archive is None:
            return

        # glob() suppresses directory scan errors, which can look like an empty archive.
        with os.scandir(archive) as entries:
            folders = sorted(archive / entry.name for entry in entries
                             if fnmatch(entry.name, "2025-11-??") and entry.is_dir())
        for folder in folders:
            paths = sorted(path for path in folder.iterdir() if fnmatch(path.name, "[0-2][0-9]h_ticks.bi5"))
            for path in paths:
                day = datetime.fromisoformat(folder.name).replace(tzinfo=UTC)
                stamp = int(day.replace(hour=int(path.name[:2])).timestamp())
                if stamp in self.files and path.read_bytes() != self.files[stamp].read_bytes():
                    raise ValueError(f"Conflicting input files at {stamp}")
                self.files[stamp] = path
                self.archive_file_count += 1

    def hours(self):
        for stamp, path in sorted(self.files.items()):
            quotes = decode_bi5(path, stamp)
            yield stamp, quotes


def decode_mt5_day(path, stamp, expected_sha):
    import numpy as np

    payload = path.read_bytes()
    digest = sha256(payload).hexdigest()
    if digest != expected_sha:
        raise ValueError(f"MT5 daily file SHA-256 mismatch for {path.stem}: expected {expected_sha}, got {digest}")
    raw = np.load(BytesIO(payload), allow_pickle=False)
    required = {"time", "time_msc", "bid", "ask", "flags"}
    if raw.ndim != 1 or not required.issubset(raw.dtype.names or ()):
        raise ValueError(f"Invalid MT5 array shape or schema at {stamp}")
    seconds = raw["time"]
    millis = raw["time_msc"]
    if len(raw) and (millis[0] < stamp * 1000 or millis[-1] >= (stamp + 86400) * 1000):
        raise ValueError(("MT5 file contains observations outside its declared UTC day", stamp))
    if np.any(millis[1:] < millis[:-1]) or not np.all(millis // 1000 == seconds):
        raise ValueError(("MT5 time order or seconds/milliseconds conflict", stamp))
    bid, ask = raw["bid"] * 100_000, raw["ask"] * 100_000
    rounded_bid, rounded_ask = np.rint(bid), np.rint(ask)
    if (np.any(~np.isfinite(bid)) or np.any(~np.isfinite(ask)) or
            np.any(np.abs(bid - rounded_bid) > 1e-6) or np.any(np.abs(ask - rounded_ask) > 1e-6) or
            np.any(rounded_bid <= 0) or np.any(rounded_bid > rounded_ask)):
        raise ValueError(("MT5 row lacks valid five-decimal BID/ASK", stamp))
    return digest, millis, rounded_bid.astype(np.int64), rounded_ask.astype(np.int64)


def quote_digest(quotes):
    digest = sha256()
    for time_ms, ordinal, bid, ask, _ in quotes:
        digest.update(struct.pack(">QIII", time_ms, ordinal, bid, ask))
    return digest.hexdigest()


def intervals_by_slot(status, rows, previous_tail, left):
    """Put coverage gaps at the first slot whose evidence establishes them."""
    right = left + SLOT * 1000
    if status == "SCHEDULED_CLOSURE":
        pairs = [(previous_tail + 1, left)] if previous_tail is not None else []
        return [list(pair) for pair in pairs if pair[0] < pair[1]], None
    if not rows:
        first_missing = left if previous_tail is None else previous_tail + 1
        return [[first_missing, right]], None
    observed = [q.time_ms for q in rows]
    gaps = []
    if previous_tail is None:
        if observed[0] > left:
            gaps.append([left, observed[0]])
    elif observed[0] - previous_tail > GAP_MS:
        gaps.append([previous_tail + 1, observed[0]])
    gaps.extend([before + 1, after] for before, after in zip(observed, observed[1:])
                if after - before > GAP_MS)
    tail = observed[-1]
    if right - (tail + 1) > GAP_MS or right == END * 1000:
        if tail + 1 < right:
            gaps.append([tail + 1, right])
        tail = None
    return gaps, tail


class Strategy:
    """Apply daily qualification to observed segments and track one execution chain."""

    def __init__(self):
        self.events = []
        self.spent = set()
        self.anchor = None
        self.decision = None
        self.fill = None
        self.last = None
        self.frozen = False

    def discontinuity(self, reason, **timing):
        self.anchor = None
        if self.decision is not None and not self.frozen:
            self.events.append(dict(kind="UNRESOLVED", quote=self.last.proof(), reason=reason,
                                    remaining=int(self.fill is not None), **timing))
            self.frozen = True

    def observe(self, quote):
        self.last = quote
        if self.frozen:
            return
        day = quote.time_ms // 86_400_000
        if self.decision is not None:
            if self.fill is None and quote.time_ms > self.decision.time_ms:
                self.fill = quote
                self.events.append(dict(kind="ENTRY", quote=quote.proof(), side="ASK",
                                        price_units=quote.ask_units, remaining=1))
            if self.fill is not None:
                diff = quote.bid_units - self.fill.ask_units
                if diff <= -100 or diff >= 200:
                    self.events.append(dict(kind="STOP" if diff <= -100 else "TARGET",
                                            quote=quote.proof(), side="BID", price_units=quote.bid_units,
                                            remaining=0))
                    self.decision = self.fill = None
            return
        if self.anchor is None or self.anchor.time_ms // 86_400_000 != day:
            self.anchor = quote
        elif day not in self.spent and self.anchor.bid_units + 10 <= quote.bid_units:
            self.spent.add(day)
            self.decision = quote
            self.events.append(dict(kind="QUALIFIED", quote=quote.proof(), anchor=self.anchor.proof()))

    def apply_slot(self, start, status, quotes, gaps):
        remaining = iter(gaps)
        gap = next(remaining, None)
        for quote in quotes:
            while gap is not None and gap[1] <= quote.time_ms:
                self.discontinuity("OPEN_SESSION_QUOTE_GAP", missing_from_ms=gap[0])
                gap = next(remaining, None)
            self.observe(quote)
        while gap is not None:
            self.discontinuity("OPEN_SESSION_QUOTE_GAP", missing_from_ms=gap[0])
            gap = next(remaining, None)
        if status == "SCHEDULED_CLOSURE":
            self.discontinuity("SCHEDULED_MARKET_CLOSURE", closure_at_ms=start * 1000)


def expected(source):
    hours = iter(source.hours())
    current = next(hours, None)
    prior = None
    strategy = Strategy()
    coverage = []
    inventory = []
    quote_count = 0
    closed_observations = []
    hashes = {}
    for stamp in range(START, END, 3600):
        hour_rows = []
        if current is not None and current[0] == stamp:
            hour_rows = current[1]
            current = next(hours, None)
        is_open = open_hour(stamp)
        if hour_rows and not is_open:
            closed_observations.append(dict(start=stamp, count=len(hour_rows),
                                            first=hour_rows[0].proof(), last=hour_rows[-1].proof()))
        status = "REGISTERED" if hour_rows and is_open else "MISSING_OPEN" if is_open else "SCHEDULED_CLOSURE"
        inventory.append(dict(start=stamp, status=status,
                              source_sha256=hour_rows[0].source_sha256 if status == "REGISTERED" else None))
        groups = {start: list(rows) for start, rows in groupby(
            hour_rows if is_open else [], lambda q: q.time_ms // (SLOT * 1000) * SLOT
        )}
        for start in range(stamp, stamp + 3600, SLOT):
            rows = groups.get(start, [])
            qstatus = "OBSERVED" if rows else "SOURCE_WITHOUT_QUOTES" if hour_rows and is_open else status
            gaps, prior = intervals_by_slot(qstatus, rows, prior, start * 1000)
            strategy.apply_slot(start, qstatus, rows, gaps)
            coverage.append(dict(start=start, status=qstatus, count=len(rows),
                                 first_ms=rows[0].time_ms if rows else None,
                                 last_ms=rows[-1].time_ms if rows else None, gaps=gaps))
            quote_count += len(rows)
            if rows:
                hashes[start] = quote_digest(rows)
    if current is not None:
        raise ValueError("Observations outside the requested month")
    return dict(inventory=inventory, coverage=coverage, events=strategy.events,
                closed_observations=closed_observations, quote_count=quote_count,
                slot_quote_hashes=hashes)


def verify_control(result):
    actual = [
        (ev["kind"], ev["quote"]["ordinal"], ev["quote"]["time_ms"], ev.get("side"), ev.get("price_units"))
        for ev in result["events"]
    ]
    base = int(datetime(2025, 11, 13, 13, tzinfo=UTC).timestamp()) * 1000
    expected_events = [
        ("QUALIFIED", 5, base + 666, None, None),
        ("ENTRY", 6, base + 771, "ASK", 116243),
        ("STOP", 1564, base + 1_898_103, "BID", 116143),
    ]
    if result["quote_count"] != 8636 or actual != expected_events:
        raise ValueError(("Pinned two-hour control differs", result["quote_count"], actual))
