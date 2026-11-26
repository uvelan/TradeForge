"""Audit a pinned November 2025 MT5 capture before using its clock for EURUSD replay."""
import argparse
from collections import Counter
from datetime import UTC, datetime
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import re

import numpy as np

from research_backend.app.indicator_coverage import MarketSchedule, open_intervals
from research_backend.app.recorded_market_entry import MAX_GAP_MS


START = int(datetime(2025, 11, 1, tzinfo=UTC).timestamp())
END = int(datetime(2025, 12, 1, tzinfo=UTC).timestamp())
CALENDAR = MarketSchedule(kind="forex_weekly")


def verified_bytes(path, expected, size_limit):
    with path.open("rb") as stream:
        raw = stream.read(size_limit + 1)
    if len(raw) > size_limit or sha256(raw).hexdigest() != expected:
        raise ValueError(f"Size limit or SHA-256 mismatch: {path.name}")
    return raw


def day_ticks(directory, claim, start):
    day = datetime.fromtimestamp(start, UTC).date().isoformat()
    if (claim["day"], claim["start"], claim["end"]) != (day, start, start + 86400):
        raise ValueError("Expected one ordered half-open daily query per calendar day")
    if "sha256" not in claim:
        if "rows" in claim or claim["attempts"][-1]["code"] == 1:
            raise ValueError(f"Inconsistent failed query: {day}")
        return None
    if claim["attempts"][-1]["code"] != 1:
        raise ValueError(f"Unsuccessful source query: {day}")
    raw = verified_bytes(directory / f"{day}.npy", claim["sha256"], 64 * 1024 * 1024)
    ticks = np.load(BytesIO(raw), allow_pickle=False)
    fields = {"time": "<i8", "time_msc": "<i8", "bid": "<f8", "ask": "<f8"}
    if (ticks.ndim != 1 or len(ticks) != claim["rows"] or
            any(ticks.dtype.fields is None or key not in ticks.dtype.fields or
                ticks.dtype[key] != dtype for key, dtype in fields.items())):
        raise ValueError(f"Wrong MT5 tick schema or count: {day}")
    ms, bid, ask = ticks["time_msc"], ticks["bid"], ticks["ask"]
    if (np.any(ms < start * 1000) or np.any(ms >= (start + 86400) * 1000) or
            np.any(ms[1:] < ms[:-1]) or np.any(ticks["time"] != ms // 1000) or
            not np.isfinite(bid).all() or not np.isfinite(ask).all() or
            np.any(bid <= 0) or np.any(ask < bid)):
        raise ValueError(f"Invalid recorded EURUSD quote or timestamp: {day}")
    return ms


def audit(path, expected_sha):
    capture = json.loads(verified_bytes(path, expected_sha, 1024 * 1024))
    if (capture["symbol"] != "EURUSD" or capture["source"] != "MT5_COPY_TICKS_ALL" or
            capture["digits"] != 5 or not re.fullmatch(r"[0-9a-f]{24}", capture["server_hash"]) or
            len(capture["days"]) != 30):
        raise ValueError("Expected a server-pinned November 2025 EURUSD MT5 capture")
    sessions = open_intervals(START, END, CALENDAR)
    counts = Counter()
    hours = []
    gaps = []
    # Compare successive observations within each scheduled open session, including day boundaries.
    previous = None
    for index, claim in enumerate(capture["days"]):
        start = START + index * 86400
        ms = day_ticks(path.parent, claim, start)
        for t in range(start, start + 86400, 3600):
            left, right = t * 1000, (t + 3600) * 1000
            session = next(((a, b) for a, b in sessions if a <= t < b), None)
            begin, stop = (0, 0) if ms is None else ms.searchsorted([left, right])
            n = int(stop - begin)
            if n:
                times = ms[begin:stop]
                first, last = int(times[0]), int(times[-1])
            else:
                first = last = None
            if session:
                if previous is None:
                    previous = session[0] * 1000 - 1
                if n:
                    if first - previous > MAX_GAP_MS:
                        gaps.append([previous + 1, first])
                    for i in np.flatnonzero(np.diff(times) > MAX_GAP_MS):
                        gaps.append([int(times[i]) + 1, int(times[i + 1])])
                    previous = last
                if t + 3600 == session[1]:
                    if right - previous - 1 > MAX_GAP_MS:
                        gaps.append([previous + 1, right])
                    previous = None
                status = "OBSERVED_OPEN" if n else "MISSING_OPEN"
            else:
                status = "CLOSED_WITH_OBSERVATIONS" if n else "SCHEDULED_CLOSURE"
            if ms is None:
                status = "OPEN_QUERY_FAILED" if session else "CLOSED_QUERY_FAILED"
            counts[status] += 1
            hours.append(dict(start=t, end=t+3600, status=status, rows=n, first_ms=first, last_ms=last,
                              first_ordinal=int(begin) if n else None, day_sha256=claim.get("sha256")))
    reasons = []
    if counts["CLOSED_WITH_OBSERVATIONS"]:
        reasons.append("RECORDED_CLOCK_CONTRADICTS_REPLAY_CALENDAR")
    if counts["OPEN_QUERY_FAILED"] or counts["CLOSED_QUERY_FAILED"]:
        reasons.append("INCOMPLETE_TERMINAL_QUERIES")
    if counts["MISSING_OPEN"] or gaps:
        reasons.append("OPEN_SESSION_COVERAGE_INCOMPLETE")
    return dict(symbol="EURUSD", source=capture["source"], server_hash=capture["server_hash"],
                capture_sha256=expected_sha, calendar=CALENDAR.model_dump(), max_gap_ms=MAX_GAP_MS,
                status="REJECTED" if reasons else "ELIGIBLE_FOR_INDEPENDENT_REPLAY",
                reasons=reasons, clock="UNSHIFTED_MT5_TIME_MSC_RENDERED_UTC",
                delivery="NOT_RECORDED", counts=dict(counts), rows=sum(h["rows"] for h in hours),
                closed_rows=sum(h["rows"] for h in hours if h["status"]=="CLOSED_WITH_OBSERVATIONS"),
                open_gaps_ms=gaps, hours=hours)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = audit(args.capture, args.sha256)
    args.output.write_text(json.dumps(result, separators=(",", ":")) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("status", "reasons", "rows", "closed_rows", "counts")}))
