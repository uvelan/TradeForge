"""Bounded, verified historical candle windows for opt-in replay capture."""
from contextlib import contextmanager
from datetime import UTC, datetime
from io import SEEK_END

from . import db, storage
from .archive_provenance import require_market_partition
from .indicator_coverage import replay_availability

MAX_WINDOW = 5_000
MAX_FILES = 6_000  # Includes room for one partition per candle and overlapping shards.
MAX_ROWS = 100_000
MAX_BYTES = 128 * 1024 * 1024  # Separate compressed and uncompressed budgets per pane.


class WindowTooLarge(ValueError):
    pass


@contextmanager
def replay_catalog():
    """Keep the catalog revision fixed while capturing all requested panes."""
    from tradeforge.shadow_reads import manifest_path, open_resolver

    manifest = manifest_path()
    if manifest is not None:
        with open_resolver(manifest, "research", db.DATABASE, storage.MARKET_DIR) as resolver:
            yield resolver.source, resolver
    else:
        with db.transaction() as connection:
            connection.execute("BEGIN")
            yield connection, None


def utc(timestamp):
    return datetime.fromtimestamp(timestamp, UTC).isoformat()


def stamp(iso):
    return int(datetime.fromisoformat(iso).timestamp())


def saved_replay_window(catalog, provider, symbol, timeframe, start, end, step, limit, cutoff):
    import pyarrow.parquet as pq

    connection, resolver = catalog
    explicit = start is not None
    end = end if explicit else cutoff // step * step
    columns = "rowid AS _snapshot_rowid, *" if resolver else "*"
    params = [provider, symbol, timeframe, utc(end)]
    lower = ""
    if explicit:
        lower = "AND last_time >= ?"
        params.append(utc(start))
    # The first unopened partition's maximum timestamp certifies when the recent tail is complete.
    cursor = connection.execute(f"""SELECT {columns} FROM datasets WHERE kind='candles'
        AND provider=? AND symbol=? AND timeframe=? AND (provider<>'DUKASCOPY' OR price_basis='BID')
        AND first_time < ? {lower} ORDER BY last_time DESC, first_time DESC, id LIMIT ?""",
        (*params, MAX_FILES + 1))
    records, origins, visible, pins = {}, {}, set(), []
    encoded_bytes = parquet_bytes = decoded_bytes = row_count = duplicates = 0
    tail_start = first = last = identity = None
    truncated = False
    for row in cursor:
        part = dict(row)
        part_first, part_last = stamp(part["first_time"]), stamp(part["last_time"])
        if tail_start is not None and part_last < tail_start:
            truncated = True
            break
        if len(pins) == MAX_FILES:
            raise ValueError("Replay partition count exceeds the read budget")
        feed = (part["feed_id"], part["price_basis"])
        if identity is not None and feed != identity:
            raise ValueError("Replay contains incompatible feeds or price bases")
        identity = feed
        expected = part["rows_count"]
        row_count += expected
        if expected <= 0 or row_count > MAX_ROWS:
            raise ValueError("Replay partition rows exceed the read budget")
        require_market_partition(part["id"])
        remaining = MAX_BYTES - encoded_bytes
        snapshot = (resolver.artifact(part, max_bytes=remaining) if resolver else
                    storage.verified_partition_file(part, max_bytes=remaining))
        with snapshot as file:
            encoded_bytes += file.seek(0, SEEK_END)
            file.seek(0)
            parquet = pq.ParquetFile(file, thrift_string_size_limit=MAX_BYTES, thrift_container_size_limit=100_000)
            if parquet.metadata.num_rows != expected:
                raise ValueError("Replay partition row count differs from its manifest")
            parquet_bytes += sum(parquet.metadata.row_group(i).total_byte_size for i in range(parquet.num_row_groups))
            if parquet_bytes > MAX_BYTES:
                raise ValueError("Decoded replay partitions exceed the read budget")
            previous = None
            for batch in parquet.iter_batches(batch_size=1024):
                decoded_bytes += batch.nbytes
                if decoded_bytes > MAX_BYTES:
                    raise ValueError("Decoded replay partitions exceed the read budget")
                for bar in batch.to_pylist():
                    timestamp = bar["time"]
                    if (type(timestamp) is not int or timestamp % step or timestamp > part_last
                            or (previous is None and timestamp != part_first)
                            or (previous is not None and timestamp < previous)):
                        raise ValueError("Replay partition contradicts its ordered UTC interval")
                    previous = timestamp
                    if timestamp >= end or (explicit and timestamp < start):
                        continue
                    if timestamp in records:
                        if bar != records[timestamp]:
                            raise ValueError("Conflicting candles in overlapping replay partitions")
                        if origins[timestamp][-1] != len(pins):
                            origins[timestamp].append(len(pins))
                        duplicates += 1
                        continue
                    records[timestamp] = bar
                    origins[timestamp] = [len(pins)]
                    available = replay_availability(bar, step)
                    if type(available) is not int or available < timestamp + step:
                        raise ValueError("Replay availability precedes candle close or has an invalid timestamp")
                    if available <= cutoff and bar.get("closed", True):
                        visible.add(timestamp)
                        if explicit and len(visible) > limit:
                            raise WindowTooLarge(f"Choose a replay range of at most {limit} closed candles per pane")
            if previous != part_last:
                raise ValueError("Replay partition final candle contradicts its registered interval")
        pins.append({"id": part["id"], "sha256": part["sha256"]})
        first = part_first if first is None else min(first, part_first)
        last = part_last if last is None else max(last, part_last)
        if not explicit and len(visible) >= limit:
            tail_start = sorted(visible)[-limit]

    if not pins:
        raise FileNotFoundError("No saved candle partitions for this replay window")
    ordered = sorted(visible)
    if not explicit:
        truncated |= len(ordered) > limit
        ordered = ordered[-limit:]
        start = tail_start if tail_start is not None else first
        end = min(end, last + step)
    gaps = []
    cursor_time = start
    for timestamp in [*ordered, end]:
        if timestamp > cursor_time:
            gaps.append({"start": cursor_time, "end": timestamp})
        cursor_time = timestamp + step
    candles = [{**records[t], "available_at": replay_availability(records[t], step),
                "availability_basis": "ARCHIVE_SUPPLIED" if records[t].get("available_at") is not None else "MODELED_NOMINAL_CLOSE",
                "source_partition_indexes": origins[t]} for t in ordered]
    # Clock positions follow arrival, independently of the UTC-open plot order.
    return {"replay_cutoffs": sorted(bar["available_at"] for bar in candles), "price_basis": identity[1], "feed_id": identity[0], "partitions": pins, "candles": candles,
            "coverage": {"start": start, "end": end, "gaps": gaps, "duplicate_bars": duplicates,
                         "truncated": truncated}}
