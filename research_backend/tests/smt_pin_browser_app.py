"""Serve recorded SMT families from two checksum-pinned comparison partitions."""
import json
import os

from fastapi.testclient import TestClient

from research_backend.app import indicator_routes as routes
from research_backend.app.main import app as research_app
from scripts.u01_comparison_archive import PIN
from scripts.verify_archive_smt_watch import stamp

from .comparison_browser_app import app


def seed_watches():
    pins = json.loads(PIN.read_text(encoding="utf-8"))["run"]["selection"]["series"]
    primary = pins["PRIMARY:M5"]["partitions"][0]
    comparison = pins["COMPARISON:M5"]["partitions"][0]
    research_app.state.smt_monitor = {}
    watches = [
        ("12:05", "13:40", 2, 1),  # A is already available before B.
        ("12:15", "13:50", 3, 1),  # B first becomes available at 13:25 UTC.
        ("12:15", "13:40", 2, 2),  # C supplies a second browse page.
    ]
    # Advance synchronously without starting the background monitor.
    client = TestClient(research_app)
    try:
        for start, end, period, confirmation_bars in watches:
            response = client.post("/api/indicators/smt/monitor/archive", json={
                "primary": primary, "comparison": comparison, "start": stamp(start), "end": stamp(end),
                "period": period, "confirmation_bars": confirmation_bars,
            })
            response.raise_for_status()
            for _ in range(8):
                routes.poll_monitor(research_app.state.smt_monitor)
                progress = client.get("/api/indicators/smt/monitor")
                progress.raise_for_status()
                status = progress.json()
                if status.get("error"):
                    raise RuntimeError(status["error"])
                if status["status"] == "COMPLETE":
                    break
            else:
                raise RuntimeError(f"Historical SMT fixture did not complete: {status}")
    finally:
        client.close()


if os.environ.get("TRADEFORGE_PAIR_REOPEN") != "1":
    seed_watches()
