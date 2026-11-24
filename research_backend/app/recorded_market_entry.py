"""Resolve a bounded archival BI5 execution window against its original import."""
from dataclasses import dataclass
from typing import Annotated

from pydantic import Field, StrictInt, model_validator

from .historical_import import FEED
from .indicator_coverage import MarketSchedule, open_intervals
from .saved_history import TickOrderError, read_tick_partitions, tick_source_manifests
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


def resolve_window(db, spec, intent):
    from .offline_market_entry import DeliveryInterval, Quote

    symbol = intent["symbol"]
    start, end = spec.start, spec.end
    begin = intent["qualified_at"] * 1000
    deadline = begin + intent["order_expiry"]["count"] * 1000
    if not start * 1000 <= begin < end * 1000:
        raise ValueError("Qualification outside pinned recorded window")

    def lookup(pin):
        row = db.execute("SELECT * FROM datasets WHERE id=?", (pin.id,)).fetchone()
        if row is None or row["sha256"] != pin.sha256:
            raise ValueError("Recorded partition pin mismatch")
        if tuple(row[k] for k in ("provider", "feed_id", "symbol", "kind", "price_basis")) != (
                "DUKASCOPY", FEED, symbol, "ticks", "BID_ASK_UNITS"):
            raise ValueError("Incompatible recorded quote source")
        return dict(row)

    source = lookup(spec.source)
    parts = [lookup(pin) for pin in sorted(spec.partitions, key=lambda p: p.id)]
    origins = tick_source_manifests([source])
    origin = origins.get(source["id"])
    if not origin or origin["source_sha256"] != spec.source_sha256 or not (
            origin["source_start"] <= start < end <= origin["source_end"]):
        raise ValueError("Recorded window does not match a completed BI5 source import")
    reference = read_tick_partitions([source], source_manifests=origins, period=(start, end))
    prior = next((row for row in reversed(reference) if row["time_ms"] <= begin), None)
    following = next((row for row in reference if row["time_ms"] > begin), None)
    boundary = None if following is None else (following["time_ms"], following["source_order"])
    # Even expiry needs a verified observation bracketing the quiet interval.
    # Include that observation when proving the layout contains the same prefix.
    expected = [row for row in reference if boundary is None or
                (row["time_ms"], row["source_order"]) <= boundary]
    ambiguous = False
    if len(parts) != 1 or parts[0]["id"] != source["id"]:
        shard_origins = tick_source_manifests(parts)
        if any(m["source_sha256"] != spec.source_sha256 for m in shard_origins.values()):
            raise ValueError("Partition registered to a different BI5 source")
        try:
            selected = read_tick_partitions(parts, source_manifests=shard_origins, period=(start, end),
                                            through_priority=boundary, required_source=spec.source_sha256)
        except TickOrderError as exc:
            if boundary is None or exc.priority != boundary or following["time_ms"] > deadline:
                raise
            ambiguous = True
        else:
            if len(selected) != len(expected):
                raise ValueError("Recorded partition selection omits or adds source observations")
            for actual, original in zip(selected, expected, strict=True):
                if actual != original and actual != original | {"source_sha256": spec.source_sha256}:
                    raise ValueError("Recorded partition selection disagrees with verified BI5 source")
    intervals = []
    quotes = []
    if following:
        if following["scale"] != POINT_SCALES[symbol]:
            raise ValueError("Recorded quote point scale differs from the instrument")
        quotes.append(Quote(**{k: following[k] for k in
                              ("time_ms", "source_order", "bid_units", "ask_units")},
                            available_at_ms=following["time_ms"]))
        if prior and following["time_ms"] - prior["time_ms"] <= MAX_GAP_MS:
            left, right = prior["time_ms"] // 1000, following["time_ms"] // 1000 + 1
            if open_intervals(left, right, MarketSchedule(kind="forex_weekly")) == [(left, right)]:
                intervals.append(DeliveryInterval(start_ms=begin+1, end_ms=following["time_ms"]))
    evidence = spec.model_dump(mode="json")
    evidence["partitions"].sort(key=lambda pin: pin["id"])
    evidence.update(feed_id=FEED, symbol=symbol, price_basis="BID_ASK_UNITS",
                    prior_observation_ms=prior["time_ms"] if prior else None,
                    following_observation_ms=following["time_ms"] if following else None)
    return ExecutionWindow(symbol, POINT_SCALES[symbol], quotes, intervals), evidence, ambiguous
