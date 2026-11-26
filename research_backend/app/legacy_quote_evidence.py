"""Replay the archived V2 display order, never the V3 execution policy."""
from .storage import iter_complete_partition
from .saved_history import TICK_REPLAY_LIMIT


def read_v2_quote_evidence(partitions):
    """Use the original V2 global ordinal convention on checksum-pinned files.

    A successful read reconstructs the archived observation order. It provides
    no new cross-file source provenance, and must not validate a new execution.
    """
    if not 1 <= len(partitions) <= 50 or sum(part["rows_count"] for part in partitions) > TICK_REPLAY_LIMIT:
        raise ValueError("Selected tick partitions exceed the 500,000 quote replay limit or 50 files")
    quotes = {}
    for part in partitions:
        seen = set()
        for quote in iter_complete_partition(part, max_rows=TICK_REPLAY_LIMIT):
            ms, ordinal = quote["time_ms"], quote["source_order"]
            if type(ms) is not int or type(ordinal) is not int:
                raise ValueError("Invalid archived tick timestamp or ordinal")
            key = ms, ordinal
            if key in seen:
                raise ValueError("Tick source order is ambiguous within a partition")
            seen.add(key)
            if key in quotes and quotes[key] != quote:
                raise ValueError("Conflicting tick quotes in overlapping partitions")
            quotes[key] = quote
    return [quotes[key] for key in sorted(quotes)]
