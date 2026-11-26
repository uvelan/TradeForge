"""Two synthetic monitor pollers share one isolated SQLite watch."""
from datetime import UTC, datetime
import json
import subprocess
import sys

import pytest

from research_backend.app import db, indicator_routes as routes, smt_monitor_lease as leases
from test_smt_authority import LEFT, RIGHT, RULES, candles


@pytest.mark.parametrize("case", ["busy", "expired", "takeover", "disabled", "reconfigured"])
def test_monitor_fences_stale_capture(tmp_path, monkeypatch, case):
    close = 1_700_000_040
    lease_clock = [(close-30)*1_000_000_000]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls.fromtimestamp(close, tz)

    monkeypatch.setattr(db, "DATABASE", tmp_path/"watch.sqlite")
    db.initialize()
    monkeypatch.setattr(routes, "datetime", Clock)
    monkeypatch.setattr(routes, "time_ns", lambda: lease_clock[0])
    monkeypatch.setattr(leases, "time_ns", lambda: lease_clock[0])

    watch = routes.LiveSmtWatch(**RULES, primary_symbol="EURUSD", comparison_symbol="GBPUSD")
    routes.configure_monitor(watch)
    stale_state, successor_state = {}, {}
    fetching_successor = False
    observed = []

    def fetch(symbol, timeframe, limit, *, include_latest, provenance):
        nonlocal fetching_successor
        source = "successor" if fetching_successor else "original"
        observed.append((source, symbol))
        if len(observed) == 1:
            if case in {"expired", "takeover"}:
                lease_clock[0] += leases.LEASE_NS
            if case in {"busy", "takeover"}:
                fetching_successor = True
                routes.poll_monitor(successor_state)
                fetching_successor = False
            if case == "disabled":
                routes.configure_monitor(watch.model_copy(update={"enabled": False}))
            if case == "reconfigured":
                lease_clock[0] += 1
                routes.configure_monitor(watch.model_copy(update={"alignment_bars": 1}))
                assert routes.monitor_configuration()["alignment_bars"] == 1

        provenance.update(feed_id=f"fixture:{source}", price_basis="BID")
        assert include_latest
        return candles(LEFT if symbol == "EURUSD" else RIGHT, close-600)

    monkeypatch.setattr(routes, "mt5_rates", fetch)
    routes.poll_monitor(stale_state)

    history = db.rows("SELECT payload FROM smt_event_history")
    alerts = db.rows("SELECT payload FROM smt_alerts")
    assert db.rows("SELECT * FROM smt_monitor_lease") == []
    progress = db.rows("SELECT payload FROM smt_watch_checkpoint")
    if case in {"busy", "takeover"}:
        assert len(progress)==1 and json.loads(progress[0]["payload"])["verified_through"]==close
        winner = "original" if case == "busy" else "successor"
        assert len(history) == 2 and len(alerts) == 1
        assert all(json.loads(row["payload"])["provenance"]["primary"]["feed_id"] == f"fixture:{winner}"
                   for row in history + alerts)
        assert [source for source, _ in observed].count("successor") == (2 if case == "takeover" else 0)
        assert bool(stale_state) == (case == "busy")
        assert bool(successor_state) == (case == "takeover")
    else:
        assert not progress and not history and not alerts and not stale_state and not successor_state

def test_lease_survives_worker_exit_and_can_be_reclaimed(tmp_path, monkeypatch):
    database = tmp_path/"watch.sqlite"
    monkeypatch.setattr(db, "DATABASE", database)
    db.initialize()
    clock = [1_700_000_000_000_000_000]
    monkeypatch.setattr(routes, "time_ns", lambda: clock[0])
    monkeypatch.setattr(leases, "time_ns", lambda: clock[0])
    routes.configure_monitor(routes.LiveSmtWatch(
        **RULES, primary_symbol="EURUSD", comparison_symbol="GBPUSD"
    ))

    subprocess.run([sys.executable, "-c", """
from pathlib import Path
import os
import sys
from research_backend.app import db, smt_monitor_lease as leases
db.DATABASE = Path(sys.argv[1])
generation = int(sys.argv[2])
leases.time_ns = lambda: generation
assert leases.claim(generation)
os._exit(0)
""", str(database), str(clock[0])], check=True, timeout=15)
    previous = db.one("SELECT generation, owner, expires_ns FROM smt_monitor_lease")
    assert previous["generation"] == clock[0]
    assert leases.claim(clock[0]) is None
    clock[0] = previous["expires_ns"] - 1
    assert leases.claim(previous["generation"]) is None
    clock[0] += 1
    current = leases.claim(previous["generation"])
    assert current is not None and current.owner != previous["owner"]
    assert not leases.finish(leases.MonitorLease(previous["generation"], previous["owner"]))
    assert db.one("SELECT owner FROM smt_monitor_lease")["owner"] == current.owner
    assert leases.finish(current)
    assert db.rows("SELECT * FROM smt_monitor_lease") == []
