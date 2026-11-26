"""Inspect one UTC H1 in an immutable saved M5 chart session."""
from fastapi import HTTPException

from .db import transaction
from .indicator_coverage import MarketSchedule, aggregate_ohlc, inspect_window
from .indicators import Bar
from .storage import iter_complete_partition

STEP = 300
HOUR = 3600


def verified_children(lane, visible):
    """Identify the contributing files, even if the selected range spans overlapping partitions."""
    by_time = {bar.time: (bar.model_dump(), available) for bar, available in visible}
    origins = {stamp: [] for stamp in by_time}
    expected = dict(provider=lane["source"].removesuffix("_SAVED"), kind="candles", symbol=lane["symbol"],
                    timeframe="M5", feed_id=lane["feed_id"], price_basis=lane["price_basis"])
    pins = lane.get("partitions") or []
    if not 1 <= len(pins) <= 50:
        raise HTTPException(409, "Replay requires immutable candle partition references")
    with transaction() as db:
        db.execute("BEGIN")
        for pin in sorted(pins, key=lambda p: p["id"]):
            row = db.execute("SELECT * FROM datasets WHERE id=?", (pin["id"],)).fetchone()
            if not row or row["sha256"] != pin["sha256"] or any(row[k] != value for k, value in expected.items()):
                raise HTTPException(409, f"Replay archive pin conflicts with catalog: {pin['id']}")
            try:
                for archived in iter_complete_partition(dict(row), max_rows=2000):
                    stamp = archived["time"]
                    if stamp not in by_time:
                        continue
                    bar, available = by_time[stamp]
                    if (any(archived[k] != value for k, value in bar.items())
                            or archived.get("available_at", stamp + STEP) != available):
                        raise ValueError(f"M5 candle at {stamp} differs from captured replay")
                    if origins[stamp] and origins[stamp][-1] == pin:
                        raise ValueError(f"Ambiguous M5 source row at {stamp}")
                    origins[stamp].append(pin)
            except (ValueError, OSError, RuntimeError) as exc:
                raise HTTPException(409, f"Replay archive {pin['id']}: {exc}") from exc
    children = []
    for stamp, (bar, available) in by_time.items():
        parts = origins[stamp]
        if not parts:
            raise HTTPException(409, f"No pinned archive contains replay M5 candle at {stamp}")
        children.append({**bar, "available_at": available, "partitions": parts,
                         "source_id": f"{parts[0]['id']}:{stamp}"})
    return children


def inspect_hour(session, view, hour_start, cutoff):
    lane = session["market"] if view == "primary" else session["views"].get(view)
    if not lane or lane.get("error"):
        raise HTTPException(422, "Selected replay lane unavailable")
    if (lane["timeframe"] != "M5" or lane["source"] != "DUKASCOPY_SAVED"
            or lane["symbol"] not in ("EURUSD", "GBPUSD") or not lane.get("coverage")):
        raise HTTPException(422, "H1 inspection requires a saved Dukascopy EURUSD or GBPUSD M5 archive range")
    coverage = lane["coverage"]
    bars = lane["candles"]
    left = bars[0]["time"] if bars and coverage["truncated"] else coverage["start"]
    horizon = max(coverage["end"], bars[-1]["available_at"]) if bars else coverage["end"]
    end = hour_start + HOUR
    if (hour_start % HOUR or not left <= cutoff <= horizon or hour_start > cutoff
            or end <= left or hour_start >= coverage["end"]):
        raise HTTPException(422, "Requested UTC hour or cutoff outside the captured replay window")
    schedule = MarketSchedule(kind="forex_weekly")
    receipt = dict(session_id=session["id"], view=view, symbol=lane["symbol"], source=lane["source"],
                   feed_id=lane["feed_id"], price_basis=lane["price_basis"], source_timeframe="M5", timeframe="H1",
                   start=hour_start, end=end, cutoff=cutoff, calendar=schedule.model_dump(),
                   availability_policy="PINNED_M5_AVAILABLE_AT_OR_MODELED_NOMINAL_CLOSE",
                   available_at=None, ohlc=None, children=[], missing_slots=[])
    # Only completed children enter the calculation. Missing and not-yet-delivered children
    # are indistinguishable at this cutoff, even after a later checkpoint has been inspected.
    visible = []
    for row in bars:
        if hour_start <= row["time"] < end:
            available = row["available_at"]
            if type(available) is not int or available < row["time"] + STEP:
                raise HTTPException(409, "Invalid M5 candle availability in replay")
            if available <= cutoff:
                visible.append((Bar.model_validate(row), available))
    report = inspect_window(hour_start, end, STEP, visible, schedule, cutoff)
    status = report["status"]
    if status == "MARKET_CLOSED":
        return dict(receipt, status="CLOSED", reason=status)
    if status == "SCHEDULE_MISMATCH":
        return dict(receipt, status="UNRESOLVED", reason=status)
    if cutoff < end:
        return dict(receipt, status="FORMING", reason="PERIOD_OPEN")
    if status != "COMPLETE":
        present = {bar.time for bar, _ in visible}
        missing = [t for t in range(hour_start, end, STEP) if t not in present]
        return dict(receipt, status="UNRESOLVED", reason=status, missing_slots=missing)
    if len(visible) != HOUR // STEP:
        return dict(receipt, status="UNRESOLVED", reason="PARTIAL_MARKET_HOUR")
    children = verified_children(lane, visible)
    return dict(receipt, status="COMPLETE", reason=None, available_at=report["available_at"],
                ohlc=aggregate_ohlc([bar for bar, _ in visible]), children=children)
