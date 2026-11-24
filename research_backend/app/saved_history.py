from datetime import UTC, datetime

from .db import rows
from .storage import iter_complete_partition, read_partition, read_partition_tail


def recent_archive_range(provider: str, symbol: str, timeframe: str, step: int, *, query=rows) -> tuple[int, int]:
    partitions = query("""SELECT first_time, last_time, feed_id, price_basis FROM datasets
        WHERE provider=? AND symbol=? AND timeframe=? AND kind='candles' AND (provider<>'DUKASCOPY' OR price_basis='BID')
        ORDER BY last_time DESC, created_at DESC LIMIT 50""", (provider, symbol, timeframe))
    if not partitions:
        raise FileNotFoundError("No saved candle partitions for this symbol and timeframe")
    latest = partitions[0]
    compatible = [part for part in partitions if (part["feed_id"], part["price_basis"]) ==
                  (latest["feed_id"], latest["price_basis"])]
    start = min(int(datetime.fromisoformat(part["first_time"]).timestamp()) for part in compatible)
    end = int(datetime.fromisoformat(latest["last_time"]).timestamp()) + step
    return start, end


def saved_range(provider: str, symbol: str, timeframe: str, start: int, end: int, step: int, limit: int, *, resolver=None) -> dict:
    def iso(timestamp: int) -> str:
        return datetime.fromtimestamp(timestamp, UTC).isoformat()

    if not 0 <= start < end <= int(datetime.now(UTC).timestamp()) or start % step or end % step:
        raise ValueError("Use an aligned, completed UTC range")
    query = resolver.rows if resolver else rows
    columns = "rowid AS _snapshot_rowid, *" if resolver else "*"
    partitions = query(f"""SELECT {columns} FROM datasets
        WHERE provider=? AND symbol=? AND timeframe=? AND kind='candles' AND (provider<>'DUKASCOPY' OR price_basis='BID')
        AND last_time>=? AND first_time<?
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
        if resolver:
            with resolver.artifact(partition, max_rows=2000) as snapshot:
                values = read_partition_tail(snapshot, 2000)
        else:
            if partition["rows_count"] > 2000:
                raise ValueError("Partition exceeds the bounded range reader")
            values = read_partition(partition)
        for bar in values:
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


def chart_range(provider, symbol, timeframe, start, end, step, limit):
    from tradeforge.shadow_reads import manifest_path, open_resolver
    from . import db, storage

    path = manifest_path()
    if path is not None:
        with open_resolver(path, "research", db.DATABASE, storage.MARKET_DIR) as resolver:
            if start is None:
                start, end = recent_archive_range(provider, symbol, timeframe, step, query=resolver.rows)
            return saved_range(provider, symbol, timeframe, start, end, step, limit, resolver=resolver)
    if start is None:
        start, end = recent_archive_range(provider, symbol, timeframe, step)
    return saved_range(provider, symbol, timeframe, start, end, step, limit)


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
    quotes = {}
    for part in partitions:
        keys = set()
        for quote in iter_complete_partition(part, max_rows=TICK_REPLAY_LIMIT):
            key = (quote["time_ms"], quote["source_order"])
            if key in keys:
                raise ValueError("Tick source order is ambiguous within a partition")
            keys.add(key)
            if key in quotes and quotes[key] != quote:
                raise ValueError("Conflicting tick quotes in overlapping partitions")
            quotes[key] = quote
    return [quotes[key] for key in sorted(quotes)]
