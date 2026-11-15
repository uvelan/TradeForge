from bisect import bisect_left, bisect_right
from collections import OrderedDict
from datetime import UTC, datetime
from itertools import combinations, groupby
from threading import Lock

from .db import rows
from .storage import partition_path, read_partition, verify_partition


def recent_archive_range(provider: str, symbol: str, timeframe: str, step: int) -> tuple[int, int]:
    partitions = rows("""SELECT first_time, last_time, feed_id, price_basis FROM datasets
        WHERE provider=? AND symbol=? AND timeframe=? AND kind='candles'
        ORDER BY last_time DESC, created_at DESC LIMIT 50""", (provider, symbol, timeframe))
    if not partitions:
        raise FileNotFoundError("No saved candle partitions for this symbol and timeframe")
    latest = partitions[0]
    compatible = [part for part in partitions if (part["feed_id"], part["price_basis"]) ==
                  (latest["feed_id"], latest["price_basis"])]
    start = min(int(datetime.fromisoformat(part["first_time"]).timestamp()) for part in compatible)
    end = int(datetime.fromisoformat(latest["last_time"]).timestamp()) + step
    return start, end


def saved_range(provider: str, symbol: str, timeframe: str, start: int, end: int, step: int, limit: int) -> dict:
    def iso(timestamp: int) -> str:
        return datetime.fromtimestamp(timestamp, UTC).isoformat()

    if not 0 <= start < end <= int(datetime.now(UTC).timestamp()) or start % step or end % step:
        raise ValueError("Use an aligned, completed UTC range")
    partitions = rows("""SELECT * FROM datasets
        WHERE provider=? AND symbol=? AND timeframe=? AND kind='candles' AND last_time>=? AND first_time<?
        ORDER BY first_time, id LIMIT 51""", (provider, symbol, timeframe, iso(start), iso(end)))
    if not partitions:
        raise FileNotFoundError("No saved candle partitions for this range")
    if len(partitions) > 50:
        raise ValueError("Range spans more than 50 partitions")
    if len({(item["feed_id"], item["price_basis"]) for item in partitions}) != 1:
        raise ValueError("Range contains incompatible feeds or price bases")
    candles = {}
    duplicates = 0
    for partition in partitions:
        if partition["rows_count"] > 2000:
            raise ValueError("Partition exceeds the bounded range reader")
        for bar in read_partition(partition):
            timestamp = bar["time"]
            if not start <= timestamp < end:
                continue
            if timestamp % step:
                raise ValueError("Saved candle falls outside the UTC timeframe grid")
            if timestamp in candles:
                if candles[timestamp] != bar:
                    raise ValueError("Conflicting candles in overlapping partitions")
                duplicates += 1
            else:
                candles[timestamp] = bar
    ordered = sorted(candles)
    gaps = []
    cursor = start
    for timestamp in [*ordered, end]:
        if timestamp > cursor:
            gaps.append({"start": cursor, "end": timestamp})
        cursor = timestamp + step
    return {"candles": [candles[timestamp] for timestamp in ordered[-limit:]],
            "price_basis": partitions[0]["price_basis"], "feed_id": partitions[0]["feed_id"],
            "partitions": [{"id": item["id"], "sha256": item["sha256"]} for item in partitions],
            "coverage": {"start": start, "end": end, "gaps": gaps, "duplicate_bars": duplicates,
                         "truncated": len(ordered) > limit}}


TICK_REPLAY_LIMIT = 500_000


def tick_partitions(provider: str, symbol: str, feed_id: str, start: int, end: int) -> list[dict]:
    first = datetime.fromtimestamp(start, UTC).isoformat()
    last = datetime.fromtimestamp(end, UTC).isoformat()
    partitions = rows("""SELECT * FROM datasets
        WHERE provider=? AND symbol=? AND feed_id=? AND kind='ticks'
        AND price_basis='BID_ASK_UNITS' AND last_time>=? AND first_time<?
        ORDER BY first_time,id LIMIT 51""", (provider, symbol, feed_id, first, last))
    if not partitions:
        raise ValueError("No compatible saved bid/ask ticks overlap the candle range")
    if len(partitions) > 50:
        raise ValueError("Range spans more than 50 tick partitions")
    return partitions


def read_tick_partitions(partitions: list[dict]) -> list[dict]:
    if sum(part["rows_count"] for part in partitions) > TICK_REPLAY_LIMIT:
        raise ValueError("Selected tick partitions exceed the 500,000 quote replay limit")
    if len({(part["provider"], part["feed_id"], part["symbol"], part["price_basis"])
            for part in partitions}) != 1:
        raise ValueError("Tick partitions must share the provider, feed, instrument and price basis")
    archives = []
    for part in sorted(partitions, key=lambda item: item["id"]):
        quotes = read_partition(part, max_rows=part["rows_count"] + 1)
        if not quotes or len(quotes) != part["rows_count"]:
            raise ValueError("Tick partition row count differs from its manifest")
        positions = [quote.get("source_order") for quote in quotes]
        if any(type(quote.get("time_ms")) is not int or quote["time_ms"] < 0
               or quote.get("time") != quote["time_ms"] // 1000 for quote in quotes):
            raise ValueError("Tick timestamp is inconsistent with its UTC archive timestamp")
        if any(type(position) is not int or position < 0 for position in positions) or len(set(positions)) != len(positions):
            raise ValueError("Tick source order is ambiguous within a partition")
        quotes.sort(key=lambda quote: (quote["time_ms"], quote["source_order"]))
        # Legacy rows retain reliable file-local order but cannot establish equivalence across files.
        origin = None
        if any("source_sha256" in quote for quote in quotes):
            origins = {(quote.get("source_sha256"), quote.get("source_window_start_ms"),
                        quote.get("source_window_end_ms")) for quote in quotes}
            if len(origins) != 1:
                raise ValueError("Inconsistent tick source provenance")
            checksum, start, end = next(iter(origins))
            if (not isinstance(checksum, str) or len(checksum) != 64
                    or any(char not in "0123456789abcdef" for char in checksum)
                    or type(start) is not int or type(end) is not int
                    or not start <= quotes[0]["time_ms"] <= quotes[-1]["time_ms"] < end):
                raise ValueError("Invalid tick source provenance")
            if any(b <= a for a, b in zip((q["source_order"] for q in quotes),
                                           (q["source_order"] for q in quotes[1:]))):
                raise ValueError("Source record positions disagree with UTC chronology")
            origin = (start, end - 1)
        if len(partitions) == 1:
            for quote in quotes:
                quote.update(source_partition_id=part["id"], original_source_order=quote["source_order"])
            return quotes
        groups = {time: list(group) for time, group in groupby(quotes, key=lambda quote: quote["time_ms"])}
        archives.append((part["id"], groups, list(groups), origin))

    for (_, left, left_times, left_origin), (_, right, right_times, right_origin) in combinations(archives, 2):
        left_start, left_end = left_origin or (left_times[0], left_times[-1])
        right_start, right_end = right_origin or (right_times[0], right_times[-1])
        start, end = max(left_start, right_start), min(left_end, right_end)
        if start > end:
            continue
        shared_left = left_times[bisect_left(left_times, start):bisect_right(left_times, end)]
        shared_right = right_times[bisect_left(right_times, start):bisect_right(right_times, end)]
        if not shared_left and not shared_right:
            continue
        if left_origin is None or right_origin is None:
            raise ValueError("Overlapping tick archives lack source provenance; reimport the original BI5 files")
        if shared_left != shared_right or any(
            tick_observations(left[time]) != tick_observations(right[time]) for time in shared_left
        ):
            raise ValueError("Conflicting tick quotes in overlapping partitions")

    merged = {}
    for partition_id, groups, _, _ in archives:
        for time, group in groups.items():
            if time in merged:
                continue
            for index, quote in enumerate(group):
                quote.update(source_partition_id=partition_id,
                             original_source_order=quote["source_order"], source_order=index)
            merged[time] = group
    return [quote for time in sorted(merged) for quote in merged[time]]


def tick_observations(group: list[dict]) -> list[tuple]:
    # Compare the entire ordered bucket inside a common declared BI5 window.
    # Equal-price repeats survive because the sequence and its multiplicity must agree.
    return [(quote["bid_units"], quote["ask_units"], quote["scale"],
             quote["ask_volume"], quote["bid_volume"]) for quote in group]


# Cache only short paths. Each hit rehashes every pinned source but skips decoding
# and merging Parquet. Long paths use the bounded replay reader without caching.
_QUOTE_PATH_CACHE: OrderedDict = OrderedDict()
_QUOTE_PATH_LOCK = Lock()
_QUOTE_PATH_MAX_ENTRIES = 4
_QUOTE_PATH_MAX_ROWS = 10_000


def saved_quote_path(datasets: list[dict], start: tuple[int, int], end: tuple[int, int]) -> list[dict]:
    key = (tuple((part["id"], part["sha256"], partition_path(part), part["rows_count"])
                 for part in sorted(datasets, key=lambda item: item["id"])), start, end)
    with _QUOTE_PATH_LOCK:
        cached = _QUOTE_PATH_CACHE.get(key)
        if cached is not None:
            _QUOTE_PATH_CACHE.move_to_end(key)
    if cached is not None:
        for dataset in datasets:
            verify_partition(dataset)
        return cached

    path = [quote for quote in read_tick_partitions(datasets)
            if start <= (quote["time_ms"], quote["source_order"]) <= end]
    if len(path) <= _QUOTE_PATH_MAX_ROWS:
        with _QUOTE_PATH_LOCK:
            _QUOTE_PATH_CACHE[key] = path
            _QUOTE_PATH_CACHE.move_to_end(key)
            if len(_QUOTE_PATH_CACHE) > _QUOTE_PATH_MAX_ENTRIES:
                _QUOTE_PATH_CACHE.popitem(last=False)
    return path
