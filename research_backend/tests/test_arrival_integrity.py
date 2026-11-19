"""Adversarial publication and constituent-lineage checks."""
from contextlib import contextmanager
from threading import Event, Thread

import pytest

from research_backend.app import arrival_capture as capture
from research_backend.app.db import rows, transaction
from test_arrival_capture import Clock, candle, capture_scope, observe, provider, worker
from research_backend.app.indicator_history import load_reference
from research_backend.app.smt_causal import ArchiveRequest


OPEN = 11 * 3600
CLOSE = OPEN + 300


def delay_receipt(monkeypatch, clock):
    """Hold a real SQLite write lock at the receipt transaction boundary."""
    phase = 0

    @contextmanager
    def delayed():
        nonlocal phase
        phase += 1
        if phase != 2:
            with transaction() as db:
                yield db
            if phase == 1:
                clock.now = (CLOSE + 2) * 1000
            return

        locked, release = Event(), Event()

        def hold():
            with transaction() as competing:
                competing.execute("BEGIN IMMEDIATE")
                locked.set()
                assert release.wait(5)

        thread = Thread(target=hold)
        thread.start()
        try:
            assert locked.wait(5)

            class Connection:
                def execute(self, *args):
                    if args[0] == "BEGIN IMMEDIATE":
                        clock.now = (CLOSE + 8) * 1000
                        release.set()
                    return db.execute(*args)

                def executemany(self, sql, values):
                    if "INSERT OR IGNORE INTO arrival_receipts" in sql:
                        clock.now = (CLOSE + 9) * 1000
                        release.set()
                    return db.executemany(sql, values)

            with transaction() as db:
                yield Connection()
        finally:
            release.set()
            thread.join(5)

    monkeypatch.setattr(capture, "transaction", delayed)


@pytest.mark.parametrize("aggregate", [False, True], ids=["native", "aggregate"])
def test_receipt_cannot_be_backdated(client, monkeypatch, aggregate):
    from arrival_fixtures import trusted_archive

    clock = Clock((CLOSE + 1) * 1000)
    if aggregate:
        source = trusted_archive([candle(t) for t in range(OPEN, CLOSE, 60)],
            provider="MT5", feed="minute", symbol="EURUSD", timeframe="M1",
            start=OPEN, end=CLOSE, cutoff=CLOSE)
        delay_receipt(monkeypatch, clock)
        output = capture.aggregate_m1(source, clock)
        scope = {key: output[key] for key in capture.SCOPE_FIELDS}
        calendar = capture.MarketSchedule.model_validate(output["calendar"])
    else:
        current = worker(Clock(OPEN * 1000), timeframe="M5")
        with transaction() as db:
            for offset in (240, 180, 120, 60):
                capture.renew(db, current.sessions["EURUSD"], (CLOSE-offset)*1000)
        current.clock = clock
        def read(symbol, frame, limit, **kwargs):
            kwargs["provenance"].update(feed_id="mt5:fixture", price_basis="BID",
                                        timestamp_policy="UTC_EPOCH_VERIFIED_V1")
            return [candle(OPEN)]
        delay_receipt(monkeypatch, clock)
        result = observe(current, read, "EURUSD")
        scope = {**capture_scope(result), "timeframe": "M5"}
        calendar = current.config.calendar
    with pytest.raises(ValueError, match="No bounded recorded evidence"):
        capture.seal(scope, OPEN, CLOSE, CLOSE + 5, calendar)
    final = capture.seal(scope, OPEN, CLOSE, CLOSE + 10, calendar)
    request = ArchiveRequest(source="MT5_SAVED", symbol="EURUSD", timeframe="M5",
        feed_id=scope["feed_id"], price_basis="BID", calendar=calendar, start=OPEN, end=CLOSE,
        partitions=final["partitions"], arrival_manifest=final["id"], cutoff=CLOSE+5, availability_mode=capture.RECORDED_MODE)
    assert not load_reference(request, OPEN, CLOSE, 300)["rows"]
    result = load_reference(request.model_copy(update={"cutoff":CLOSE+9}), OPEN, CLOSE, 300)["rows"]
    assert [(bar.close, available) for bar, available in result] == [(12,CLOSE+9)]


def test_older_manifest_cannot_replace_corrected_aggregate(client):
    current = worker(Clock(OPEN * 1000))
    with transaction() as db:
        for offset in (240, 180, 120, 60):
            capture.renew(db, current.sessions["EURUSD"], (CLOSE-offset)*1000)
    current.clock.now = (CLOSE + 1) * 1000
    batch = [candle(t) for t in range(OPEN, CLOSE, 60)]
    scope = capture_scope(observe(current, provider(batch), "EURUSD"))
    a = capture.seal(scope, OPEN, CLOSE, CLOSE+2, current.config.calendar)
    first = capture.aggregate_m1(a, Clock((CLOSE+3)*1000))
    current.clock.now = (CLOSE+4)*1000
    observe(current, provider([candle(OPEN, 20)]), "EURUSD")
    b = capture.seal(scope, OPEN, CLOSE, CLOSE+5, current.config.calendar)
    second = capture.aggregate_m1(b, Clock((CLOSE+6)*1000))
    try:
        capture.aggregate_m1(a, Clock((CLOSE+7)*1000))
    except ValueError as error:
        assert str(error) == "STALE_OR_INCOMPARABLE_AGGREGATE_SOURCE"
    assert first["id"] != second["id"]
    request = ArchiveRequest(source="MT5_SAVED", symbol="EURUSD", timeframe="M5",
        feed_id=first["feed_id"], price_basis="BID", calendar=first["calendar"], start=OPEN, end=CLOSE,
        partitions=first["partitions"], arrival_manifest=first["id"], cutoff=first["cutoff"], availability_mode=capture.RECORDED_MODE)
    assert load_reference(request, OPEN, CLOSE, 300)["rows"][0][0].high == 13
    latest = request.model_copy(update={"partitions": [
        item.model_copy(update=entry) for item, entry in zip(request.partitions, second["partitions"], strict=True)],
        "arrival_manifest":second["id"], "cutoff":second["cutoff"]})
    assert load_reference(latest, OPEN, CLOSE, 300)["rows"][0][0].high == 21
    assert load_reference(latest.model_copy(update={"cutoff":CLOSE+5}), OPEN, CLOSE, 300)["rows"][0][0].high == 13
    assert len(rows("SELECT sequence FROM arrival_observations WHERE timeframe='M5'")) == 2




def test_expired_worker_cannot_write_after_provider_returns(client):
    current = worker()
    current.clock.now = 61_000
    replacement = None

    def takeover(*args, **kwargs):
        nonlocal replacement
        current.clock.now = 95_000
        replacement = worker(Clock(95_000))
        return provider([candle(0)])(*args, **kwargs)

    with pytest.raises(ValueError, match="CAPTURE_LEASE_LOST"):
        observe(current, takeover, "EURUSD")
    assert rows("SELECT sequence FROM arrival_observations") == []
    assert rows("SELECT session_id FROM arrival_leases") == [
        {"session_id": replacement.sessions["EURUSD"]}]


def test_expiry_between_receipt_and_witness_requires_late_recovery(client, monkeypatch):
    current = worker()
    current.clock.now = 61_000
    phase = 0

    @contextmanager
    def takeover_after_receipt():
        nonlocal phase
        phase += 1
        with transaction() as db:
            yield db
        if phase == 2:
            current.clock.now = 160_000
            monkeypatch.setattr(capture, "transaction", transaction)
            worker(Clock(160_000))

    monkeypatch.setattr(capture, "transaction", takeover_after_receipt)
    with pytest.raises(ValueError, match="CAPTURE_LEASE_LOST"):
        observe(current, provider([candle(0)]), "EURUSD")
    assert rows("SELECT persisted_ms FROM arrival_receipts") == [{"persisted_ms": 61_000}]
    assert not rows("SELECT sequence FROM arrival_publications")
    sessions = rows("SELECT session_id FROM arrival_leases")
    replacement = capture.Worker(current.config, {"EURUSD": sessions[0]["session_id"]}, 160_000, Clock(161_125))
    status = observe(replacement, provider([candle(0)]), "EURUSD")
    scope = capture_scope(status)
    with pytest.raises(ValueError, match="No bounded recorded evidence"):
        capture.seal(scope, 0, 60, 161, current.config.calendar)
    sealed = capture.seal(scope, 0, 60, 162, current.config.calendar)
    assert set(sealed["sessions"]) == {current.sessions["EURUSD"], replacement.sessions["EURUSD"]}
    assert rows("SELECT receipt_committed_by_ms FROM arrival_publications") == [
        {"receipt_committed_by_ms": 161_125}]


def test_stop_and_unknown_broker_clock_never_write(client):
    current = worker()
    current.clock.now = 65_000

    def unknown(*args, **kwargs):
        result = provider([candle(0)])(*args, **kwargs)
        kwargs["provenance"].pop("timestamp_policy")
        return result

    with pytest.raises(ValueError, match="UNVERIFIED_TIMESTAMP_DOMAIN"):
        observe(current, unknown, "EURUSD")

    def stopped(*args, **kwargs):
        current.stop.set()
        return provider([candle(0)])(*args, **kwargs)

    with pytest.raises(ValueError, match="CAPTURE_STOPPED"):
        observe(current, stopped, "EURUSD")
    assert not rows("SELECT sequence FROM arrival_observations")


def test_legacy_manifest_remains_accessible_without_strict_authority(client):
    from research_backend.app.indicator_history import load_reference
    from test_arrival_capture import frozen

    current = worker()
    payload = {**capture_scope({"feed_id": "legacy", "price_basis": "BID"}),
        "availability_policy": "SERVER_RECORDED_ARRIVAL_V1", "start": 0, "end": 60,
         "cutoff": 100, "calendar": current.config.calendar.model_dump(),
        "partitions": [{"id": "legacy", "sha256": "a"*64}]}
    identifier = capture.fingerprint(payload)
    with transaction() as db:
        db.execute("INSERT INTO arrival_manifests VALUES(?,?)", (identifier, capture.canonical(payload)))
    response = client.get("/api/market/arrival/manifests/" + identifier)
    assert response.json() == {"id": identifier, **payload}
    request = frozen(payload, response.json(), 100)
    result = load_reference(request, 0, 60, 60)
    assert result["rows"] == []
    assert result["evidence"]["reason"] == "LEGACY_RECEIPT_HAS_NO_DURABLE_PUBLICATION_PROOF"


def test_aggregate_pending_restart_and_unchanged_ohlc_revision(client, monkeypatch):
    from arrival_fixtures import trusted_archive
    from research_backend.app import storage
    from research_backend.app.db import one

    batch = [candle(t, 20 if t == 0 else 12, available_at=t+61) for t in range(0, 300, 60)]
    args = dict(provider="MT5", feed="minute", symbol="EURUSD", timeframe="M1", start=0, end=300)
    original = trusted_archive(batch, cutoff=305, **args)
    real_publish = capture.publish

    def crash(*args, **kwargs):
        raise OSError("interrupted before receipt")

    monkeypatch.setattr(capture, "publish", crash)
    with pytest.raises(OSError, match="interrupted"):
        capture.aggregate_m1(original, Clock(310_000))
    monkeypatch.setattr(capture, "publish", real_publish)
    first = capture.aggregate_m1(original, Clock(315_000))
    assert first["cutoff"] == 315
    assert first == capture.aggregate_m1(original, Clock(316_000))

    corrected = trusted_archive([candle(120, 12, available_at=320)], cutoff=322, **args)
    second = capture.aggregate_m1(corrected, Clock(324_000))
    dataset = one("SELECT * FROM datasets WHERE id=?", (second["partitions"][0]["id"],))
    before, after = storage.read_partition(dataset)
    assert before["aggregate_candle_id"] == after["aggregate_candle_id"]
    assert all(before[key] == after[key] for key in ("open", "high", "low", "close"))
    assert after["previous_sequence"] == before["sequence"]
    assert after["source_manifest_id"] == corrected["id"]
    assert after["source_cutoff"] == 322
    assert before["constituents"][2]["available_at"] == 181
    assert after["constituents"][2]["available_at"] == 320
    assert before["constituents"][2]["sequence"] < after["constituents"][2]["sequence"]


def test_aggregate_competing_requests_cannot_publish_superseded_draft(client, monkeypatch):
    from arrival_fixtures import trusted_archive

    args = dict(provider="MT5", feed="minute", symbol="EURUSD", timeframe="M1", start=0, end=600)
    old = trusted_archive([candle(t) for t in range(0,600,60)], cutoff=600, **args)
    new = trusted_archive([candle(0,20,available_at=601)], cutoff=602, **{**args, "end":300})
    original = capture.publish
    result = []

    def concurrent(*args, **kwargs):
        monkeypatch.setattr(capture, "publish", original)
        result.append(capture.aggregate_m1(new, Clock(605_000)))
        return original(*args, **kwargs)

    monkeypatch.setattr(capture, "publish", concurrent)
    with pytest.raises(ValueError, match="STALE_OR_INCOMPARABLE_AGGREGATE_SOURCE"):
        capture.aggregate_m1(old, Clock(603_000))
    assert result[0]["data_quality"] == {"observations":1,"corrections":1}
    assert rows("""SELECT o.candle_open FROM arrival_observations o
        JOIN arrival_publications p USING(sequence) WHERE o.timeframe='M5'""") == [{"candle_open":0}]


def test_receipt_transaction_rollback_and_retry_use_new_boundary(client, monkeypatch):
    current = worker()
    current.clock.now = 61_000
    phase = 0

    @contextmanager
    def interrupted():
        nonlocal phase
        phase += 1
        with transaction() as db:
            yield db
            if phase == 2:
                raise OSError("receipt commit interrupted")

    monkeypatch.setattr(capture, "transaction", interrupted)
    with pytest.raises(OSError, match="receipt commit interrupted"):
        observe(current, provider([candle(0)]), "EURUSD")
    assert len(rows("SELECT sequence FROM arrival_observations")) == 1
    assert rows("SELECT * FROM arrival_receipts") == []
    assert rows("SELECT * FROM arrival_publications") == []
    monkeypatch.setattr(capture, "transaction", transaction)
    current.clock.now = 78_001
    status = observe(current, provider([candle(0)]), "EURUSD")
    assert status["last_persisted_ms"] == 78_001


def test_clock_step_back_during_transaction_never_issues_proof(client, monkeypatch):
    current = worker()
    current.clock.now = 61_000
    phase = 0

    @contextmanager
    def stepped():
        nonlocal phase
        phase += 1
        with transaction() as db:
            yield db
        if phase == 2:
            current.clock.now = 60_001

    monkeypatch.setattr(capture, "transaction", stepped)
    with pytest.raises(ValueError, match="CLOCK_DISCONTINUITY"):
        observe(current, provider([candle(0)]), "EURUSD")
    assert len(rows("SELECT * FROM arrival_receipts")) == 1
    assert rows("SELECT * FROM arrival_publications") == []


def test_expired_during_writer_lock_wait_never_renews(client, monkeypatch):
    current = worker()
    current.clock.now = 61_000
    real_transaction = capture.transaction

    @contextmanager
    def delayed():
        with real_transaction() as db:
            class Connection:
                def execute(self, sql, *args):
                    if sql == "BEGIN IMMEDIATE":
                        # Captured observation time is valid, but cannot renew after the wait.
                        current.clock.now = 100_000
                    return db.execute(sql, *args)
            yield Connection()

    monkeypatch.setattr(capture, "transaction", delayed)
    with pytest.raises(ValueError, match="CAPTURE_LEASE_LOST"):
        observe(current, provider([candle(0)]), "EURUSD")
    assert rows("SELECT expires_ms FROM arrival_leases") == [{"expires_ms":90_001}]
    assert rows("SELECT sequence FROM arrival_observations") == []


def test_aggregate_rejects_incomparable_source_vectors(client):
    from arrival_fixtures import trusted_archive

    scope = dict(provider="MT5", feed="minute", symbol="EURUSD", timeframe="M1", start=0, end=300)
    trusted_archive([candle(t) for t in range(0,300,60)], cutoff=301, **scope)
    left = trusted_archive([candle(0,20,available_at=350)], cutoff=355, **scope)
    capture.aggregate_m1(left, Clock(360_000))
    # Deliberately construct a snapshot with one older and one newer constituent.
    right = trusted_archive([candle(60,18,available_at=340)], cutoff=345, **scope)
    with pytest.raises(ValueError, match="STALE_OR_INCOMPARABLE_AGGREGATE_SOURCE"):
        capture.aggregate_m1(right, Clock(361_000))
    assert len(rows("SELECT sequence FROM arrival_observations WHERE timeframe='M5'")) == 1


def test_receipt_rounds_up_fractional_millisecond_at_cutoff():
    clock = capture.CaptureClock(wall=lambda: 10_000_000_001, monotonic=lambda: 0)
    assert clock.sample()[0] == 10_000
    assert clock.sample_receipt() == 10_001
    assert capture.to_seconds(clock.sample_receipt()) == 11
