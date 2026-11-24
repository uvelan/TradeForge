from dataclasses import FrozenInstanceError, replace
from decimal import Decimal, localcontext

import pytest

from tradeforge.broker_contracts import (
    AccountMode, AccountSnapshot, BrokerCapabilities, BrokerEvent, BrokerOrderAck, DisabledBroker,
    Fill, InstrumentSpecification, OrderIntent, OrderStatus, RiskEvidence, RiskLimits, Side, evaluate,
)

D = Decimal


class FakeReadOnlyBroker(DisabledBroker):
    """Fixed offline observations, not an adapter that can submit or cancel orders."""

    def capabilities(self):
        return BrokerCapabilities(id="fixture-capabilities", reads_account=True, reads_instrument=True, reads_events=True)

    def account(self):
        return AccountSnapshot(id="fixture-account", mode=AccountMode.DEMO, currency="USD",
                               as_of_ms=1000, connected=True, exposure=D("10"),
                               realized_daily_loss=D("5"), active_orders=1)

    def instrument(self, symbol):
        if symbol != "EURUSD":
            return None
        return InstrumentSpecification(id="fixture-spec", symbol=symbol, tick_size=D("0.00001"),
                                       quantity_step=D("0.01"), minimum_quantity=D("0.01"),
                                       maximum_quantity=D("1.00"), as_of_ms=1000)

    def ack(self):
        return BrokerOrderAck(id="fixture-ack", intent_id="fixture-intent", order_id="fixture-order",
                              status=OrderStatus.ACKNOWLEDGED)

    def events_after(self, sequence, *, limit):
        super().events_after(sequence, limit=limit)
        order = self.ack().order_id
        events = tuple(BrokerEvent(id=f"fixture-event-{i}", sequence=i, order_id=order, status=status,
                                   fill=None if qty is None else Fill(id=f"fixture-fill-{i}", order_id=order,
                                                                      quantity=D(qty), price=D("1.12345"),
                                                                      observed_at_ms=1000+i))
                       for i, status, qty in [(1, OrderStatus.ACKNOWLEDGED, None),
                                               (2, OrderStatus.PARTIALLY_FILLED, "0.01"),
                                               (3, OrderStatus.FILLED, "0.02")])
        return tuple(event for event in events if event.sequence > sequence)[:limit]


@pytest.fixture
def risk():
    broker = FakeReadOnlyBroker()
    return (
        OrderIntent(id="fixture-intent", symbol="EURUSD", side=Side.BUY, quantity=D(".03"),
                    reference_price=D("1.12345")),
        broker.account(), broker.instrument("EURUSD"),
        RiskEvidence(now_ms=1010, quote_ms=1000, bid=D("1.12344"), ask=D("1.12345"),
                     market_open=True, disabled=False, prospective_exposure=D("2")),
        RiskLimits(currency="USD", max_age_ms=10, max_spread=D(".00001"), max_deviation=D("0"),
                   max_exposure=D("12"), max_daily_loss=D("10"), max_active_orders=2),
    )


def test_disabled_adapter_has_no_broker_mutation_authority():
    adapter = DisabledBroker()
    assert adapter.capabilities() == BrokerCapabilities(id="disabled-v1")
    assert adapter.account() is adapter.instrument("EURUSD") is None
    assert adapter.events_after(0, limit=10) == ()
    assert not any(hasattr(adapter, method) for method in ("submit", "cancel", "replace", "connect"))
    with pytest.raises(ValueError):
        adapter.events_after(0, limit=101)


def test_fake_read_lifecycle_is_stable_across_replay_and_pages():
    adapter = FakeReadOnlyBroker()
    first = adapter.events_after(0, limit=2)
    second = adapter.events_after(first[-1].sequence, limit=2)
    assert first + second == FakeReadOnlyBroker().events_after(0, limit=3)
    assert [event.status for event in first+second] == [OrderStatus.ACKNOWLEDGED,
                                                       OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED]
    assert sum(event.fill.quantity for event in first+second if event.fill) == D(".03")
    assert all(event.order_id == adapter.ack().order_id and event.schema_version == 1 for event in first+second)
    assert len({event.id for event in first+second}) == 3
    assert not adapter.capabilities().order_routing
    with pytest.raises(FrozenInstanceError):
        adapter.capabilities().order_routing = True


def test_exact_boundary_checks_pass_without_granting_authority(risk):
    with localcontext() as context:
        context.prec = 2
        decision = evaluate(*risk, decision_id="decision-1")
    assert decision.passed_checks and not decision.order_routing and not decision.reasons
    assert decision.intent_id == risk[0].id and decision.schema_version == 1


@pytest.mark.parametrize(("index", "changes", "reason"), [
    (0, {"quantity": D(".031")}, "INVALID_INSTRUMENT_OR_QUANTITY"),
    (0, {"side": Side.SELL}, "DEVIATION_LIMIT"),
    (1, {"mode": AccountMode.LIVE}, "ACCOUNT_MODE_UNVERIFIED"),
    (1, {"currency": "EUR"}, "ACCOUNT_CURRENCY_MISMATCH"),
    (1, {"connected": False}, "DISCONNECTED"),
    (1, {"realized_daily_loss": D("10")}, "DAILY_LOSS_LIMIT"),
    (1, {"active_orders": 2}, "ACTIVE_ORDER_LIMIT"),
    (2, {"as_of_ms": 1011}, "STALE_OR_FUTURE_EVIDENCE"),
    (3, {"quote_ms": 999}, "STALE_OR_FUTURE_EVIDENCE"),
    (3, {"bid": D("NaN")}, "SPREAD_LIMIT"),
    (3, {"ask": D("1e999999999")}, "SPREAD_LIMIT"),
    (3, {"ask": D("1.12346")}, "SPREAD_LIMIT"),
    (3, {"prospective_exposure": D("2.01")}, "EXPOSURE_LIMIT"),
    (3, {"market_open": False}, "MARKET_NOT_OPEN"),
    (3, {"disabled": True}, "EMERGENCY_DISABLED"),
])
def test_rejections_are_fail_closed(risk, index, changes, reason):
    items = list(risk)
    items[index] = replace(items[index], **changes)
    decision = evaluate(*items, decision_id="decision-denied")
    assert not decision.passed_checks and not decision.order_routing and reason in decision.reasons


def test_emergency_and_stale_evidence_both_surface(risk):
    intent, account, spec, evidence, limits = risk
    decision = evaluate(intent, replace(account, connected=False), spec,
                        replace(evidence, disabled=True, quote_ms=999), limits, decision_id="interaction")
    assert decision.reasons[:2] == ("EMERGENCY_DISABLED", "DISCONNECTED")
    assert "STALE_OR_FUTURE_EVIDENCE" in decision.reasons
    assert not decision.passed_checks and not decision.order_routing
