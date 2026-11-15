from datetime import UTC, datetime

from .db import rows
from .storage import read_partition


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
