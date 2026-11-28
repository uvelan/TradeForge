"""Resolve the plotted OHLC values against the immutable archive pins supplied by a chart."""
from hashlib import sha256
import json
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field, FiniteFloat

from .indicator_engine import Partition
from .market import FRAME_MIN
from .replay_archive import MAX_FILES, MAX_ROWS, MAX_WINDOW
from .storage import iter_complete_partition, read_partition


class ChartBar(BaseModel):
    time: int
    open: FiniteFloat
    high: FiniteFloat
    low: FiniteFloat
    close: FiniteFloat


class ChartLane(BaseModel):
    role: Literal["primary", "comparison"]
    symbol: str = Field(min_length=1, max_length=64)
    timeframe: str = Field(max_length=4)
    feed_id: str = Field(min_length=1, max_length=200)
    price_basis: str = Field(min_length=1, max_length=64)
    partitions: list[Partition] = Field(min_length=1, max_length=50)
    bars: list[ChartBar] = Field(min_length=1, max_length=2000)


class FrozenChartLane(ChartLane):
    """Only constructed from a checksum-verified native replay, never from an upload."""
    partitions: list[Partition] = Field(min_length=1, max_length=MAX_FILES)
    bars: list[ChartBar] = Field(min_length=1, max_length=MAX_WINDOW)


def verified_lanes(db, request, signal=None):
    primary = request.lanes[0]
    peer = request.lanes[1] if len(request.lanes)==2 else None
    if signal is not None and peer is None:
        raise HTTPException(422, "Linked SMT lane attestations require both instruments")
    if (request.source not in {"MT5_SAVED", "DUKASCOPY_SAVED"} or primary.role != "primary"
            or primary.symbol != request.symbol or (peer and (peer.role != "comparison" or peer.symbol == primary.symbol))
            or any(lane.timeframe != request.timeframe for lane in request.lanes)):
        raise HTTPException(422, "Snapshot lanes differ from chart context")
    result = []
    for lane in request.lanes:
        times = [bar.time for bar in lane.bars]
        wanted = set(times)
        ids = [part.id for part in lane.partitions]
        if times != sorted(wanted) or len(set(ids)) != len(ids):
            raise HTTPException(422, "Snapshot requires ordered unique bars and distinct pins")
        if signal is not None:
            origin = signal["provenance"][lane.role]
            pins = origin.get("partitions") or [origin]
            expected_pins = [(pin.get("id") or pin.get("dataset_id"), pin.get("sha256")) for pin in pins]
            if (origin.get("symbol") != lane.symbol or origin.get("feed_id") != lane.feed_id
                    or origin.get("price_basis") != lane.price_basis or origin.get("source") != request.source
                    or expected_pins != [(pin.id, pin.sha256) for pin in lane.partitions]):
                raise HTTPException(409, f"{lane.role} archive differs from linked signal")
        observed = {}
        latest_availability = None
        for part in lane.partitions:
            row = db.execute("SELECT * FROM datasets WHERE id=?", (part.id,)).fetchone()
            expected = dict(provider=request.source.removesuffix("_SAVED"), kind="candles", symbol=lane.symbol,
                            timeframe=lane.timeframe, feed_id=lane.feed_id, price_basis=lane.price_basis, sha256=part.sha256)
            if row is None or any(row[key] != value for key, value in expected.items()):
                raise HTTPException(409, f"{lane.role} archive pin conflicts with catalog: {part.id}")
            try:
                # Native sessions already bounded all contributing partitions at capture.
                # An early decision can fall entirely before the ordinary chart reader's tail.
                rows = (iter_complete_partition(dict(row), max_rows=MAX_ROWS)
                        if request.decision_replay else read_partition(dict(row)))
                for bar in rows:
                    t = bar["time"]
                    if t not in wanted:
                        continue
                    available = bar.get("available_at")
                    if available is None:
                        available = t + FRAME_MIN[lane.timeframe] * 60
                    latest_availability = available if latest_availability is None else max(latest_availability, available)
                    if request.cutoff is not None and available > request.cutoff:
                        raise HTTPException(409, "Plotted archive bar unavailable at cutoff")
                    values = {key:bar[key] for key in ChartBar.model_fields}
                    if t in observed and observed[t] != values:
                        raise HTTPException(409, "Conflicting plotted archive bars")
                    observed[t] = values
            except (OSError, ValueError, RuntimeError) as exc:
                raise HTTPException(409, f"{lane.role} partition {part.id}: {exc}") from exc
        plotted = [bar.model_dump() for bar in lane.bars]
        if any(observed.get(bar["time"]) != bar for bar in plotted):
            raise HTTPException(409, f"{lane.role} plotted OHLC differs from pinned archive")
        result.append({**lane.model_dump(exclude={"bars"}), "bar_times":times, "last_bar":plotted[-1],
                       "ohlc_sha256":sha256(json.dumps(plotted,sort_keys=True,separators=(",",":")).encode()).hexdigest()})
        if signal is not None:
            result[-1]["latest_bar_available_at"] = latest_availability
    return result
