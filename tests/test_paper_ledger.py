from dataclasses import FrozenInstanceError, replace

import pytest

from tradeforge.paper_ledger import PaperLedger, PaperOrder, PaperQuote, PaperSide


@pytest.fixture
def paper():
    ledger = PaperLedger(symbol="EURUSD", opening_cash_units=500, max_quote_age_ms=2)
    order = PaperOrder(id="order-1", symbol=ledger.symbol, side=PaperSide.BUY, quantity_units=2)
    quote = PaperQuote(id="quote-1", symbol=ledger.symbol, observed_at_ms=1000, bid_units=100, ask_units=101)
    return ledger, order, quote


def test_exact_bid_ask_cash_positions_and_immutable_prefix(paper):
    empty, order, quote = paper
    bought, buy = empty.apply(order, quote, decision_at_ms=1002)
    closed, sell = bought.apply(replace(order, id="order-2", side=PaperSide.SELL), quote, decision_at_ms=1002)
    assert (buy.status, buy.price_units, bought.position_units, bought.cash_units) == ("PAPER_FILLED", 101, 2, 298)
    assert (sell.status, sell.price_units, closed.position_units, closed.cash_units) == ("PAPER_FILLED", 100, 0, 498)
    assert all(event.mode == "PAPER" and event.schema_version == 1 for event in closed.events)
    assert closed.events[:1] == bought.events == (buy,) and empty.events == ()
    with pytest.raises(FrozenInstanceError):
        buy.price_units = 1


def test_retries_do_not_mutate_balances_or_accept_different_evidence(paper):
    ledger, order, quote = paper
    first, event = ledger.apply(order, quote, decision_at_ms=1000)
    later, _ = first.apply(replace(order, id="second"), quote, decision_at_ms=1001)
    assert later.apply(order, quote, decision_at_ms=1000) == (later, event)
    assert len(later.events) == 2
    with pytest.raises(ValueError, match="binds different evidence"):
        later.apply(order, replace(quote, ask_units=102), decision_at_ms=1000)
    with pytest.raises(ValueError, match="not monotonic"):
        later.apply(replace(order, id="out-of-order"), quote, decision_at_ms=1000)


@pytest.mark.parametrize(("decision", "side", "quantity", "reason"), [
    (999, PaperSide.BUY, 1, "STALE_OR_FUTURE_QUOTE"),
    (1003, PaperSide.BUY, 1, "STALE_OR_FUTURE_QUOTE"),
    (1000, PaperSide.BUY, 5, "INSUFFICIENT_PAPER_CASH"),
    (1000, PaperSide.SELL, 1, "INSUFFICIENT_PAPER_POSITION"),
])
def test_business_rejection_is_immutable_and_preserves_state(paper, decision, side, quantity, reason):
    ledger, order, quote = paper
    order = replace(order, side=side, quantity_units=quantity)
    updated, event = ledger.apply(order, quote, decision_at_ms=decision)
    assert (event.status, event.reason, event.price_units) == ("PAPER_REJECTED", reason, None)
    assert (updated.cash_units, updated.position_units) == (500, 0)
    assert updated.apply(order, quote, decision_at_ms=decision) == (updated, event)


def test_wrong_instrument_crossed_quote_and_non_integer_quantity_fail_without_events(paper):
    ledger, order, quote = paper
    with pytest.raises(ValueError, match="quote"):
        ledger.apply(order, replace(quote, symbol="GBPUSD"), decision_at_ms=1000)
    with pytest.raises(ValueError, match="quote"):
        ledger.apply(order, replace(quote, bid_units=102), decision_at_ms=1000)
    with pytest.raises(ValueError, match="order"):
        ledger.apply(replace(order, quantity_units=True), quote, decision_at_ms=1000)
    assert not ledger.events


def test_sqlite_restart_and_first_terminal_event_wins(paper, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from tradeforge.paper_repository import PaperRepository

    ledger, order, quote = paper
    path = tmp_path / "paper.sqlite3"
    start = Barrier(2)

    def action(cancel):
        repo = PaperRepository(path, ledger)
        start.wait(timeout=3)
        if cancel:
            return repo.cancel(order, decision_at_ms=1000)
        return repo.apply(order, quote, decision_at_ms=1000)

    with ThreadPoolExecutor(2) as pool:
        cancel, submit = tuple(pool.map(action, (True, False)))
    assert cancel == submit
    repo = PaperRepository(path, ledger)
    restored = repo.read()
    assert restored.events == (submit,)
    if submit.status == "PAPER_FILLED":
        assert (restored.cash_units, restored.position_units) == (298, 2)
    else:
        assert submit.status == "PAPER_CANCELED"
        assert (restored.cash_units, restored.position_units) == (500, 0)
    assert repo.apply(order, quote, decision_at_ms=1000) == submit
    with pytest.raises(ValueError, match="opening policy"):
        PaperRepository(path, replace(ledger, opening_cash_units=999)).read()


@pytest.mark.parametrize("cancel_first", [True, False])
def test_forced_terminal_orderings_survive_a_fresh_repository(paper, tmp_path, cancel_first):
    from tradeforge.paper_repository import PaperRepository

    ledger, order, quote = paper
    path = tmp_path / "paper.sqlite3"
    def submit():
        return PaperRepository(path, ledger).apply(order, quote, decision_at_ms=1000)
    def cancel():
        return PaperRepository(path, ledger).cancel(order, decision_at_ms=1000)
    first, late = (cancel, submit) if cancel_first else (submit, cancel)
    result = first()
    assert result.status == ("PAPER_CANCELED" if cancel_first else "PAPER_FILLED")
    assert late() == result
    assert PaperRepository(path, ledger).read().events == (result,)


def test_repository_rejects_foreign_files_and_guards_events(paper, tmp_path):
    import sqlite3
    from contextlib import closing
    from tradeforge.paper_repository import PaperRepository

    ledger, order, quote = paper
    path = tmp_path / "existing.sqlite3"
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("CREATE TABLE unrelated (value INTEGER)")
    with pytest.raises(ValueError, match="Not a supported"):
        PaperRepository(path, ledger).read()

    dedicated = tmp_path / "paper.sqlite3"
    repo = PaperRepository(dedicated, ledger)
    event = repo.apply(order, quote, decision_at_ms=1000)
    with closing(sqlite3.connect(dedicated)) as db, db:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            db.execute("DELETE FROM paper_events")
    assert repo.read().events == (event,)
