"""Controlled M5 outside bars create two exact-pair SMT families on one clock tick."""
import pytest

from research_backend.tests.test_replay_hour import START, URL, publish, session


@pytest.mark.parametrize("eur_center", [100, 101])
def test_simultaneous_opposing_smt_families_use_identity_tie_break(client, eur_center):
    step = 300
    end = START + 14 * step
    feeds = {}
    for side, symbol, center, widths in (
        ("primary", "EURUSD", eur_center, {2: 4, 6: 6}),
        ("comparison", "GBPUSD", 200, {2: 8, 6: 5}),
    ):
        # Two successive outside bars are each both a high pivot and a low pivot.
        # EUR's range expands while GBP's contracts, yielding bearish and bullish SMT.
        rows = []
        for i in range(14):
            width = widths.get(i, 1)
            rows.append({"time": START + i*step, "open": center, "high": center+width,
                         "low": center-width, "close": center})
        part = publish(rows, symbol)
        feeds[side] = dict(symbol=symbol, source="DUKASCOPY_SAVED", feed_id="controlled-replay-test",
                           price_basis="BID", timeframe="M5", partitions=[{k:part[k] for k in ("id","sha256")}],
                           calendar={"kind":"forex_weekly"}, start=START, end=end)
    sessions = [session(client, symbol=symbol, end=end, views=[{"view":"comparison","symbol":other,"timeframe":"M5"}])
                for symbol, other in (("EURUSD","GBPUSD"), ("GBPUSD","EURUSD"))]
    for cutoff, state in ((START+9*step-1, None), (START+9*step, "SMT_CANDIDATE"),
                          (START+10*step, "SMT_CONFIRMED")):
        independent = client.post("/api/indicators/smt/strict", json={
            **feeds, "period":2, "alignment_bars":0, "tolerance_bps":0, "confirmation_bars":1,
            "expiry_bars":64, "availability_mode":"HISTORICAL_SIMULATION", "cutoff":cutoff,
        })
        assert independent.status_code == 200, independent.text
        families = independent.json()["events"]
        if state:
            assert len(families) == 2
            assert {f["direction"] for f in families} == {"bullish", "bearish"}
            assert {f["candidate_at"] for f in families} == {START+9*step}
            winner = min(families, key=lambda f:f["id"])
        else:
            assert not families
        for key in sessions:
            response = client.get(f"{URL}/{key}/confluence", params={"cutoff":cutoff})
            assert response.status_code == 200, response.text
            selected = response.json()["components"]["smt"]
            if state:
                assert selected["family_id"] == winner["id"]
                assert selected["revision_id"] == winner["revisions"][-1]["id"]
                assert selected["state"] == state
            else:
                assert selected["family_id"] is None
