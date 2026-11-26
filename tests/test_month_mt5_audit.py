"""Controlled raw epochs exercise admission; no terminal or market prices required."""
from hashlib import sha256
import json
from datetime import UTC, datetime

import numpy as np
import pytest

from scripts.month_mt5_audit import START, audit, on_point_grid


def capture(tmp_path):
    data = {"source": "MT5_COPY_TICKS_ALL", "digits": 5, "symbol": "EURUSD",
            "server_hash": "1" * 24, "days": []}
    for i in range(30):
        start = START + i * 86400
        day = datetime.fromtimestamp(start, UTC).date().isoformat()
        source = tmp_path / f"{day}.npy"
        # Friday at 21:59:59.999 is open; 22:00:00.000 is the first closed instant.
        ms = [(start + 22 * 3600) * 1000 + d for d in (-1, 0)] if day == "2025-11-07" else []
        ticks = np.array([(t // 1000, t, 1.1, 1.1001) for t in ms],
                         dtype=[("time", "<i8"), ("time_msc", "<i8"), ("bid", "<f8"), ("ask", "<f8")])
        np.save(source, ticks)
        data["days"].append(dict(day=day, start=start, end=start+86400, attempts=[dict(code=1)],
                                 rows=len(ticks), sha256=sha256(source.read_bytes()).hexdigest()))
    path = tmp_path / "capture.json"
    path.write_text(json.dumps(data))
    return path, sha256(path.read_bytes()).hexdigest()


def test_raw_clock_and_empty_sunday_fail_closed(tmp_path):
    path, pin = capture(tmp_path)
    result = audit(path, pin)
    assert result["status"] == "REJECTED"
    assert result["reasons"] == ["RECORDED_CLOCK_CONTRADICTS_REPLAY_CALENDAR", "OPEN_SESSION_COVERAGE_INCOMPLETE"]
    assert result["counts"] == dict(OBSERVED_OPEN=1, CLOSED_WITH_OBSERVATIONS=1, MISSING_OPEN=481, SCHEDULED_CLOSURE=237)
    assert result["rows"] == 2 and result["closed_rows"] == 1
    conflict, = (h for h in result["hours"] if h["status"] == "CLOSED_WITH_OBSERVATIONS")
    assert conflict["start"] == 1762552800 and conflict["first_ordinal"] == 1
    assert result["open_gaps_ms"][0] == [1762120800000, 1762552799999]


def test_changed_daily_bytes_or_manifest_pin_rejected(tmp_path):
    path, pin = capture(tmp_path)
    with pytest.raises(ValueError, match="SHA-256"):
        audit(path, "0" * 64)
    (tmp_path / "2025-11-07.npy").write_bytes(b"replacement")
    with pytest.raises(ValueError, match="2025-11-07"):
        audit(path, pin)


def test_provider_double_encoding_vs_fractional_point():
    assert on_point_grid(np.array([1.1529099999999999, 1.1534200000000001]))
    assert not on_point_grid(np.array([1.152915]))
