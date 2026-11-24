from collections.abc import Iterator
from contextlib import ExitStack, closing
from datetime import UTC, datetime
from heapq import merge
from itertools import groupby
import re

from .db import rows
from .storage import iter_complete_partition, read_partition, read_partition_tail


def recent_archive_range(provider: str, symbol: str, timeframe: str, step: int, *, resolver=None) -> tuple[int, int]:
    query = resolver.rows if resolver else rows
    columns = "rowid AS _snapshot_rowid, *" if resolver else "first_time, last_time, feed_id, price_basis"
    partitions = query(f"""SELECT {columns} FROM datasets
        WHERE provider=? AND symbol=? AND timeframe=? AND kind='candles' AND (provider<>'DUKASCOPY' OR price_basis='BID')
        ORDER BY last_time DESC, created_at DESC LIMIT 50""", (provider, symbol, timeframe))
    if not partitions:
        raise FileNotFoundError("No saved candle partitions for this symbol and timeframe")
    latest = partitions[0]
    compatible = [part for part in partitions if (part["feed_id"], part["price_basis"]) ==
                  (latest["feed_id"], latest["price_basis"])]
    if resolver:
        for part in compatible:
            resolver.resolve_mapping(part)
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
                start, end = recent_archive_range(provider, symbol, timeframe, step, resolver=resolver)
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


def tick_source_manifests(partitions):
    ids = [part["id"] for part in partitions]
    if not 1 <= len(ids) <= 50:
        raise ValueError("Select 1 to 50 tick partitions")
    placeholders = ",".join("?" for _ in ids)
    matches = rows(f"""SELECT json_extract(manifest,'$.tick_dataset.id') AS id,
        json_extract(manifest,'$.tick_dataset.sha256') AS sha256, source_sha256,
        feed_id, symbol, source_start, source_end FROM historical_imports
        WHERE json_extract(manifest,'$.tick_dataset.id') IN ({placeholders})
        ORDER BY source_start,id""", ids)
    catalog = {part["id"]: part for part in partitions}
    result = {}
    for match in matches:
        key = match["id"]
        if key in result or any(match[k] != catalog[key][k] for k in ("sha256", "feed_id", "symbol")):
            raise ValueError("Ambiguous or inconsistent tick import provenance")
        result[key] = match
    return result


TICK_FIELDS = ("time", "time_ms", "source_order", "bid_units", "ask_units", "scale")


def ordered_partition(part, columns, bounds_ms):
    # The legacy writer sorts by whole second. Sort the bounded equal-second
    # groups by the finer recorded clock and ordinal without sorting the archive.
    previous = -1
    with closing(iter_complete_partition(part, max_rows=TICK_REPLAY_LIMIT, batch_size=1024, columns=columns)) as records:
        for second, group in groupby(records, key=lambda row: row["time"]):
            if type(second) is not int or second <= previous:
                raise ValueError("Invalid tick partition time order")
            previous = second
            batch = []
            for quote in group:
                ms = quote.get("time_ms")
                if type(ms) is not int or ms // 1000 != second:
                    raise ValueError("Invalid tick timestamp or source order")
                if bounds_ms is not None and not bounds_ms[0] <= ms < bounds_ms[1]:
                    continue
                ordinal = quote.get("source_order")
                if type(ordinal) is not int or ordinal < 0:
                    raise ValueError("Invalid tick timestamp or source order")
                batch.append(quote)
            batch.sort(key=lambda row: (row["time_ms"], row["source_order"]))
            for quote in batch:
                yield quote["time_ms"], quote["source_order"], part["id"], quote


def tick_origin(quote, file_id, manifests):
    fingerprint = quote.get("source_sha256")
    registered = manifests.get(file_id)
    if registered:
        if fingerprint is not None and fingerprint != registered["source_sha256"]:
            raise ValueError("Tick row contradicts source manifest")
        fingerprint = registered["source_sha256"]
    if fingerprint is None:
        # A legacy unpinned ordinal is only comparable inside its original partition.
        return ("partition", file_id)
    if not isinstance(fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
        raise ValueError("Invalid tick source fingerprint")
    return ("source", fingerprint)


class TickOrderError(ValueError):
    def __init__(self, message, time_ms, ordinal):
        super().__init__(message)
        self.priority = (time_ms, ordinal)


def read_tick_partitions(partitions: list[dict], *, source_manifests=None, execution_only=False, period=None, through_priority=None,
                         required_source=None) -> list[dict]:
    return list(iter_tick_partitions(partitions, source_manifests=source_manifests, execution_only=execution_only,
                                    period=period, through_priority=through_priority, required_source=required_source))


def iter_tick_partitions(partitions: list[dict], *, source_manifests=None, execution_only=False,
                         period=None, through_priority=None, required_source=None) -> Iterator[dict]:
    if len(partitions) > 50 or sum(part["rows_count"] for part in partitions) > TICK_REPLAY_LIMIT:
        raise ValueError("Selected tick partitions exceed the 500,000 quote replay limit or 50 files")
    if len({(p["provider"], p["feed_id"], p["symbol"], p["price_basis"]) for p in partitions}) != 1:
        raise ValueError("Incompatible tick partition source or basis")
    if source_manifests is None:
        source_manifests = tick_source_manifests(partitions)
    scale = None
    with ExitStack() as handles:
        # Multi-file ties need the entire source row for exact duplicate verification.
        columns = [*TICK_FIELDS, "source_sha256"] if execution_only and len(partitions) == 1 else None
        bounds_ms = None if period is None else (period[0] * 1000, period[1] * 1000)
        if through_priority is not None:
            if bounds_ms is None:
                raise ValueError("A terminal priority requires a pinned period")
            bounds_ms = (bounds_ms[0], min(bounds_ms[1], through_priority[0] + 1))
        streams = [handles.enter_context(closing(ordered_partition(part, columns, bounds_ms))) for part in partitions]
        merged = merge(*streams, key=lambda item: item[:3])
        for ms, at_ms in groupby(merged, key=lambda item: item[0]):
            origin = None
            for ordinal, tied in groupby(at_ms, key=lambda item: item[1]):
                if through_priority is not None and origin is not None and (ms, ordinal) > through_priority:
                    # Later ordinals are comparable only within the winning source.
                    if any(tick_origin(q, file_id, source_manifests) != origin
                           for _, _, file_id, q in tied):
                        raise TickOrderError(f"Ambiguous tick source order at {ms}", *through_priority)
                    continue
                previous_file = None
                winner = None
                for _, _, file_id, quote in tied:
                    current_origin = tick_origin(quote, file_id, source_manifests)
                    if required_source is not None:
                        if current_origin != ("source", required_source):
                            raise ValueError("Recorded quote lacks the pinned BI5 source identity")
                        # An import manifest and a shard row can attest the same original.
                        quote = quote | {"source_sha256": required_source}
                    if origin is not None and current_origin != origin:
                        raise TickOrderError(f"Ambiguous tick source order at {ms}", ms, ordinal)
                    origin = current_origin
                    if previous_file == file_id:
                        raise TickOrderError("Tick source order is ambiguous within a partition", ms, ordinal)
                    if winner is not None and quote != winner:
                        raise TickOrderError("Conflicting tick quotes in overlapping partitions", ms, ordinal)
                    previous_file, winner = file_id, quote
                if scale is None:
                    scale = winner["scale"]
                if type(scale) is not int or scale <= 0 or winner["scale"] != scale:
                    raise ValueError("Inconsistent tick price scale")
                yield {k: winner[k] for k in TICK_FIELDS} if execution_only else winner
