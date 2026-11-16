"""Coverage of UTC reference periods under explicitly declared market hours."""
from datetime import UTC, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, model_validator

DAY = 86400
WEEK = DAY * 7


class MarketSchedule(BaseModel):
    kind: Literal["continuous", "forex_weekly", "unknown"]
    timezone: str = Field(default="America/New_York", max_length=64)
    sunday_open: str = Field(default="17:00", pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")
    friday_close: str = Field(default="17:00", pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")

    @model_validator(mode="after")
    def validate_timezone(self):
        if self.kind == "forex_weekly":
            try:
                ZoneInfo(self.timezone)
            except (ValueError, ZoneInfoNotFoundError) as error:
                raise ValueError("Unknown IANA market timezone") from error
        return self


def period_start(timestamp: int, length: int) -> int:
    offset = 3 * DAY if length == WEEK else 0  # Unix epoch was Thursday.
    return (timestamp + offset) // length * length - offset


def open_intervals(start: int, end: int, schedule: MarketSchedule):
    if schedule.kind == "continuous":
        return [(start, end)]
    if schedule.kind == "unknown":
        return None
    zone = ZoneInfo(schedule.timezone)
    day = datetime.fromtimestamp(start, zone).date() - timedelta(days=7)
    sunday = day - timedelta(days=(day.weekday() + 1) % 7)
    intervals = []
    while datetime.combine(sunday, time(), zone).timestamp() < end:
        bounds = []
        for date, clock in ((sunday, schedule.sunday_open),
                            (sunday + timedelta(days=5), schedule.friday_close)):
            local = datetime.combine(date, time.fromisoformat(clock))
            candidates = {int(local.replace(tzinfo=zone, fold=fold).timestamp()) for fold in (0, 1)}
            valid = {stamp for stamp in candidates
                     if datetime.fromtimestamp(stamp, zone).replace(tzinfo=None) == local}
            if len(valid) != 1:
                return None
            bounds.append(valid.pop())
        left, right = max(start, bounds[0]), min(end, bounds[1])
        if left < right:
            intervals.append((left, right))
        sunday += timedelta(days=7)
    return intervals


def inspect_window(start, end, step, observations, schedule, cutoff):
    intervals = open_intervals(start, end, schedule)
    expected = None
    status = "UNKNOWN_SCHEDULE"
    if intervals is not None:
        if any(left % step or right % step for left, right in intervals):
            status = "UNALIGNED_MARKET_BOUNDARY"
        else:
            expected = sum((right-left)//step for left, right in intervals)
            if any(bar.time % step or not any(left <= bar.time < right for left, right in intervals)
                   for bar, _ in observations):
                status = "SCHEDULE_MISMATCH"
            elif not expected:
                status = "MARKET_CLOSED"
            elif not observations:
                status = "REFERENCE_ABSENT"
            elif len(observations) != expected:
                status = "MISSING_OPEN_CANDLES"
            elif end > cutoff:
                status = "PERIOD_OPEN"
            else:
                status = "COMPLETE"
    available = max([end, *(instant for _, instant in observations)]) if status == "COMPLETE" else None
    return {"status": status, "reference_start": start, "reference_end": end,
            "expected_candles": expected,
            "observed_candles": len(observations), "available_at": available,
            "scheduled_closed_seconds": None if intervals is None else end-start-sum(b-a for a,b in intervals),
            "candidate_high": max((bar.high for bar, _ in observations), default=None),
            "candidate_low": min((bar.low for bar, _ in observations), default=None)}


def previous_levels(rows, step, length, schedule, cutoff, context=None):
    grouped = {}
    for bar, available in rows:
        grouped.setdefault(period_start(bar.time, length), []).append((bar, available))
    current = period_start(cutoff, length)
    coverage = {}
    series = {"High": [], "Low": []}
    for end in sorted({current, *grouped}):
        start = end - length
        historical = context is not None and end == current
        observed = context["rows"] if historical else grouped.get(start, [])
        report = inspect_window(start, end, step, observed, schedule, cutoff)
        if historical:
            report.update(context["evidence"])
        coverage[end] = report
        if report["status"] != "COMPLETE":
            continue
        for bar, available in grouped.get(end, []):
            if bar.time + step < report["available_at"]:
                continue
            for label, value in (("High", report["candidate_high"]), ("Low", report["candidate_low"])):
                series[label].append({"time": bar.time, "value": value, "available_at": available,
                                      "status": "confirmed", "reference_start": start,
                                      "reference_end": end})
    latest = coverage[current]
    latest["confirmation_status"] = ("confirmed" if latest["status"] == "COMPLETE" else
                                     "provisional" if latest["candidate_high"] is not None else "unavailable")
    warnings = [] if latest["status"] == "COMPLETE" else [latest["status"]]
    if latest["status"] != "COMPLETE" and not series["High"]:
        warnings.append("PREVIOUS_PERIOD_UNAVAILABLE")
    return series, warnings, latest
