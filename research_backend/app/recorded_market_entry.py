"""Resolve a bounded archival BI5 execution window against its original import."""
from contextlib import closing
from dataclasses import dataclass
from typing import Annotated

from pydantic import Field, StrictInt, model_validator

from .historical_import import FEED
from .indicator_coverage import MarketSchedule, open_intervals
from .saved_history import TickOrderError, iter_tick_partitions, read_tick_partitions, tick_source_manifests
from .storage import POINT_SCALES
from .strategy_definitions import Contract

Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
MAX_GAP_MS = 60_000
RECORDED_RULES = {
    "id": "OFFLINE_RECORDED_MARKET_ENTRY", "version": 1,
    "availability": "BI5_OBSERVATION_PLUS_MODELED_ZERO_DELAY",
    "coverage": "VERIFIED_IMPORT_AND_BRACKETING_OBSERVATIONS_V1",
    "max_quote_gap_ms": MAX_GAP_MS, "calendar": "DUKASCOPY_FX_NY17_V1",
}


class PartitionPin(Contract):
    id: Digest
    sha256: Digest


class RecordedWindow(Contract):
    source: PartitionPin
    source_sha256: Digest
    partitions: list[PartitionPin] = Field(min_length=1, max_length=50)
    start: StrictInt = Field(ge=0)
    end: StrictInt = Field(gt=0)

    @model_validator(mode="after")
    def bounded(self):
        if self.end <= self.start or self.end - self.start > 86400:
            raise ValueError("Select a positive recorded window of at most one day")
        if len({pin.id for pin in self.partitions}) != len(self.partitions):
            raise ValueError("Duplicate recorded partition ID")
        return self


@dataclass
class ExecutionWindow:
    symbol: str
    scale: int
    quotes: list
    complete_delivery_intervals: list


def execution_quote(row, scale):
    from .offline_market_entry import Quote

    if row["scale"] != scale:
        raise ValueError("Recorded quote point scale differs from the instrument")
    return Quote(**{key: row[key] for key in ("time_ms", "source_order", "bid_units", "ask_units")},
                 available_at_ms=row["time_ms"])


def fixed_conflict_rows(error, original, source_sha256, touched, buy, units):
    """Allow price disagreement at a possible touch only with the source observation present."""
    pinned = original | {"source_sha256": source_sha256}
    if pinned not in error.variants:
        raise ValueError("Conflicting selection lacks the pinned BI5 observation") from error
    for variant in error.variants:
        if variant | {"bid_units": original["bid_units"], "ask_units": original["ask_units"]} != pinned:
            raise ValueError("Conflicting selection changes BI5 source metadata") from error
        execution_quote(variant, original["scale"])
    if units is None or not any(touched(buy, r["bid_units"], r["ask_units"], units)
                                for r in error.variants):
        raise error
    return error.variants


def recorded_partition(db, pin, symbol):
    row = db.execute("SELECT * FROM datasets WHERE id=?", (pin.id,)).fetchone()
    if row is None or row["sha256"] != pin.sha256:
        raise ValueError("Recorded partition pin mismatch")
    if tuple(row[k] for k in ("provider", "feed_id", "symbol", "kind", "price_basis")) != (
            "DUKASCOPY", FEED, symbol, "ticks", "BID_ASK_UNITS"):
        raise ValueError("Incompatible recorded quote source")
    return dict(row)


def recorded_reference(db, spec, symbol):
    source = recorded_partition(db, spec.source, symbol)
    origins = tick_source_manifests([source])
    origin = origins.get(source["id"])
    if not origin or origin["source_sha256"] != spec.source_sha256 or not (
            origin["source_start"] <= spec.start < spec.end <= origin["source_end"]):
        raise ValueError("Recorded window does not match a completed BI5 source import")
    return source, read_tick_partitions([source], source_manifests=origins, period=(spec.start, spec.end))


def resolve_window(db, spec, intent):
    from .offline_market_entry import DeliveryInterval, fixed_price_units, limit_touched, stop_touched

    symbol = intent["symbol"]
    start, end = spec.start, spec.end
    begin = intent["qualified_at"] * 1000
    deadline = begin + intent["order_expiry"]["count"] * 1000
    if not start * 1000 <= begin < end * 1000:
        raise ValueError("Qualification outside pinned recorded window")

    source, reference = recorded_reference(db, spec, symbol)
    parts = [recorded_partition(db, pin, symbol) for pin in sorted(spec.partitions, key=lambda p: p.id)]
    prior = next((row for row in reversed(reference) if row["time_ms"] <= begin), None)
    following = next((row for row in reference if row["time_ms"] > begin), None)
    kind = intent["entry"]["kind"]
    fixed = kind in {"LIMIT", "STOP"}
    touched = stop_touched if kind == "STOP" else limit_touched
    buy = intent["direction"] == "LONG"
    if fixed:
        price = intent["entry"]["price"]
        if not price or price["kind"] != "FIXED_ABSOLUTE":
            raise ValueError(f"Recorded {kind} requires a fixed absolute price")
        units = fixed_price_units(price, POINT_SCALES[symbol])
        # Keep the first post-expiry witness when no executable side touched.
        following = next((r for r in reference if r["time_ms"] > begin and
                          (r["time_ms"] > deadline or
                           (units is not None and touched(buy, r["bid_units"], r["ask_units"], units)))), None)
    boundary = None if following is None else (following["time_ms"], following["source_order"])
    # Even expiry needs a verified observation bracketing the quiet interval.
    # Include that observation when proving the layout contains the same prefix.
    expected = [row for row in reference if boundary is None or
                (row["time_ms"], row["source_order"]) <= boundary]
    ambiguous = False
    decision_rows = expected
    if len(parts) != 1 or parts[0]["id"] != source["id"]:
        shard_origins = tick_source_manifests(parts)
        if any(m["source_sha256"] != spec.source_sha256 for m in shard_origins.values()):
            raise ValueError("Partition registered to a different BI5 source")
        selected = iter_tick_partitions(parts, source_manifests=shard_origins, period=(start, end),
                                       through_priority=boundary, required_source=spec.source_sha256, inspect_ties=fixed)
        matched = 0
        missing_or_extra = "Recorded partition selection omits or adds source observations"
        with closing(selected):
            try:
                for row in selected:
                    if matched == len(expected):
                        raise ValueError(missing_or_extra)
                    original = expected[matched]
                    if (row["time_ms"], row["source_order"]) != (original["time_ms"], original["source_order"]):
                        raise ValueError(missing_or_extra)
                    if row != original | {"source_sha256": spec.source_sha256}:
                        raise ValueError("Recorded partition selection disagrees with verified BI5 source")
                    matched += 1
            except TickOrderError as exc:
                if fixed:
                    if matched >= len(expected) or not begin < exc.priority[0] <= deadline:
                        raise
                    original = expected[matched]
                    if exc.priority != (original["time_ms"], original["source_order"]):
                        raise ValueError(missing_or_extra) from exc
                    variants = fixed_conflict_rows(exc, original, spec.source_sha256, touched, buy, units)
                    decision_rows = expected[:matched] + variants
                elif boundary is None or exc.priority != boundary or following["time_ms"] > deadline:
                    raise
                # The iterator may raise at the winner before yielding its row.
                elif matched < len(expected) - 1:
                    raise ValueError(missing_or_extra) from exc
                else:
                    ambiguous = True
            else:
                if matched != len(expected):
                    raise ValueError(missing_or_extra)
    intervals = []
    quotes = []
    rows = ([r for r in decision_rows if r["time_ms"] > begin] if fixed
            else [following] if following else [])
    previous = prior
    frontier = begin
    for row in rows:
        quotes.append(execution_quote(row, POINT_SCALES[symbol]))
        if previous is not None and row["time_ms"] - previous["time_ms"] <= MAX_GAP_MS:
            frontier = row["time_ms"]
            previous = row
        else:
            previous = None
    if frontier > begin:
        left, right = prior["time_ms"] // 1000, frontier // 1000 + 1
        if open_intervals(left, right, MarketSchedule(kind="forex_weekly")) == [(left, right)]:
            intervals.append(DeliveryInterval(start_ms=begin+1, end_ms=frontier))
    evidence = spec.model_dump(mode="json")
    evidence["partitions"].sort(key=lambda pin: pin["id"])
    evidence.update(feed_id=FEED, symbol=symbol, price_basis="BID_ASK_UNITS",
                    prior_observation_ms=prior["time_ms"] if prior else None,
                    following_observation_ms=following["time_ms"] if following else None)
    if fixed:
        evidence["following_observation_ms"] = rows[0]["time_ms"] if rows else None
        evidence["last_inspected_priority"] = [rows[-1]["time_ms"], rows[-1]["source_order"]] if rows else None
    return ExecutionWindow(symbol, POINT_SCALES[symbol], quotes, intervals), evidence, ambiguous


def resolve_exit_window(db, spec, entry, cutoff_ms):
    """Use the pinned original import, not the entry selection's truncated shards."""
    from .offline_market_entry import DeliveryInterval, quote_priority

    symbol = entry["source"]["symbol"]
    fill = entry["fill"]
    start, scale = fill["time_ms"], POINT_SCALES[symbol]
    if not spec.start * 1000 <= start <= cutoff_ms < spec.end * 1000:
        raise ValueError("Exit cutoff and entry fill must belong to the pinned recorded window")
    _, reference = recorded_reference(db, spec, symbol)
    priority = (start, start, fill["quote"]["source_order"])
    quotes = [execution_quote(row, scale) for row in reference
              if (start, fill["quote"]["source_order"]) <= (row["time_ms"], row["source_order"])
              and row["time_ms"] <= cutoff_ms]
    if (not quotes or quote_priority(quotes[0]) != priority
            or quotes[0].model_dump(mode="json") | {"scale": scale} != fill["quote"]):
        raise ValueError("Entry fill does not match an original BI5 observation")

    # Do not use observations after cutoff, bridge a long absence, or cross a
    # calendar closure with an open position. Preserve earlier certain exits.
    observed = start
    second = start // 1000
    schedule = open_intervals(second, cutoff_ms // 1000 + 1, MarketSchedule(kind="forex_weekly"))
    intervals = []
    if schedule and schedule[0][0] == second:
        market_end = schedule[0][1] * 1000
        for quote in quotes[1:]:
            if quote.time_ms >= market_end or quote.time_ms - observed > MAX_GAP_MS:
                break
            observed = quote.time_ms
        intervals.append(DeliveryInterval(start_ms=start, end_ms=observed))
    evidence = {
        "source": spec.source.model_dump(mode="json"),
        "source_sha256": spec.source_sha256, "start": spec.start, "end": spec.end,
        "feed_id": FEED, "symbol": symbol, "price_basis": "BID_ASK_UNITS",
    }
    return ExecutionWindow(symbol, scale, quotes, intervals), evidence
