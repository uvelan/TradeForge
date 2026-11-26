"""Versioned broker-neutral evidence contracts. No submission surface."""
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from fractions import Fraction
from typing import Protocol


@dataclass(frozen=True, kw_only=True)
class Evidence:
    id: str
    schema_version: int = field(default=1, init=False)

    def __post_init__(self):
        if not isinstance(self.id, str) or not self.id.strip():
            raise ValueError("Evidence requires a stable nonempty ID")


class AccountMode(StrEnum):
    DEMO = "DEMO"
    LIVE = "LIVE"
    UNKNOWN = "UNKNOWN"


class OrderStatus(StrEnum):
    ACKNOWLEDGED = "ACKNOWLEDGED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"


class Side(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass(frozen=True, kw_only=True)
class BrokerCapabilities(Evidence):
    reads_account: bool = False
    reads_instrument: bool = False
    reads_events: bool = False
    order_routing: bool = field(default=False, init=False)


@dataclass(frozen=True, kw_only=True)
class InstrumentSpecification(Evidence):
    symbol: str
    tick_size: Decimal
    quantity_step: Decimal
    minimum_quantity: Decimal
    maximum_quantity: Decimal
    as_of_ms: int


@dataclass(frozen=True, kw_only=True)
class OrderIntent(Evidence):
    symbol: str
    side: Side
    quantity: Decimal
    reference_price: Decimal


@dataclass(frozen=True, kw_only=True)
class AccountSnapshot(Evidence):
    mode: AccountMode
    currency: str
    as_of_ms: int
    connected: bool
    exposure: Decimal
    realized_daily_loss: Decimal
    active_orders: int


@dataclass(frozen=True, kw_only=True)
class BrokerOrderAck(Evidence):
    intent_id: str
    order_id: str
    status: OrderStatus


@dataclass(frozen=True, kw_only=True)
class Fill(Evidence):
    order_id: str
    quantity: Decimal
    price: Decimal
    observed_at_ms: int


@dataclass(frozen=True, kw_only=True)
class BrokerEvent(Evidence):
    sequence: int
    order_id: str
    status: OrderStatus
    fill: Fill | None = None


@dataclass(frozen=True, kw_only=True)
class PreTradeRiskDecision(Evidence):
    intent_id: str
    passed_checks: bool
    reasons: tuple[str, ...]
    order_routing: bool = field(default=False, init=False)


@dataclass(frozen=True, kw_only=True)
class RiskEvidence:
    now_ms: int
    quote_ms: int
    bid: Decimal
    ask: Decimal
    market_open: bool
    disabled: bool
    prospective_exposure: Decimal


@dataclass(frozen=True, kw_only=True)
class RiskLimits:
    currency: str
    max_age_ms: int
    max_spread: Decimal
    max_deviation: Decimal
    max_exposure: Decimal
    max_daily_loss: Decimal
    max_active_orders: int


def _decimal(value: Decimal, zero_allowed: bool = False) -> bool:
    if not isinstance(value, Decimal) or not value.is_finite():
        return False
    parts = value.as_tuple()
    # Bound exact Fraction conversion before allocating powers of ten.
    return (len(parts.digits) <= 38 and -38 <= parts.exponent <= 38
            and (value >= 0 if zero_allowed else value > 0))


def _time(value: int) -> bool:
    return type(value) is int and value >= 0


def evaluate(intent: OrderIntent, account: AccountSnapshot, spec: InstrumentSpecification,
             evidence: RiskEvidence, limits: RiskLimits, *, decision_id: str) -> PreTradeRiskDecision:
    """Pure deterministic pre-trade checks. A pass never grants order authority."""
    reasons = []
    def require(condition, reason):
        if not condition:
            reasons.append(reason)

    require(evidence.disabled is False, "EMERGENCY_DISABLED")
    require(account.connected is True, "DISCONNECTED")
    require(account.mode is AccountMode.DEMO, "ACCOUNT_MODE_UNVERIFIED")
    require(bool(limits.currency) and account.currency == limits.currency, "ACCOUNT_CURRENCY_MISMATCH")
    require(evidence.market_open is True, "MARKET_NOT_OPEN")
    timing = all(_time(v) for v in (limits.max_age_ms, evidence.now_ms, evidence.quote_ms, account.as_of_ms, spec.as_of_ms))
    require(timing and all(0 <= evidence.now_ms - v <= limits.max_age_ms
                           for v in (evidence.quote_ms, account.as_of_ms, spec.as_of_ms)), "STALE_OR_FUTURE_EVIDENCE")

    valid_grid = (bool(intent.symbol) and intent.symbol == spec.symbol and
                  all(_decimal(v) for v in (spec.tick_size, spec.quantity_step, spec.minimum_quantity,
                                             spec.maximum_quantity, intent.quantity, intent.reference_price))
                  and isinstance(intent.side, Side))
    if valid_grid:
        def aligned(value, step):
            return (Fraction(value) / Fraction(step)).denominator == 1
        valid_grid = (spec.minimum_quantity <= intent.quantity <= spec.maximum_quantity
                      and aligned(intent.quantity, spec.quantity_step) and aligned(intent.reference_price, spec.tick_size))
    require(valid_grid, "INVALID_INSTRUMENT_OR_QUANTITY")

    quote = all(_decimal(v) for v in (evidence.bid, evidence.ask)) and evidence.bid <= evidence.ask
    spread_ok = quote and _decimal(limits.max_spread, True)
    deviation_ok = quote and valid_grid and _decimal(limits.max_deviation, True)
    # Use exact arithmetic independent of the caller's Decimal context.
    require(spread_ok and Fraction(evidence.ask) - Fraction(evidence.bid) <= Fraction(limits.max_spread), "SPREAD_LIMIT")
    executable = evidence.ask if intent.side is Side.BUY else evidence.bid
    require(deviation_ok and abs(Fraction(executable) - Fraction(intent.reference_price)) <= Fraction(limits.max_deviation),
            "DEVIATION_LIMIT")
    exposure_ok = all(_decimal(v, True) for v in (account.exposure, evidence.prospective_exposure, limits.max_exposure))
    require(exposure_ok and Fraction(account.exposure) + Fraction(evidence.prospective_exposure) <= Fraction(limits.max_exposure),
            "EXPOSURE_LIMIT")
    require(_decimal(account.realized_daily_loss, True) and _decimal(limits.max_daily_loss) and
            account.realized_daily_loss < limits.max_daily_loss, "DAILY_LOSS_LIMIT")
    require(_time(account.active_orders) and _time(limits.max_active_orders) and
            account.active_orders < limits.max_active_orders, "ACTIVE_ORDER_LIMIT")
    return PreTradeRiskDecision(id=decision_id, intent_id=intent.id, passed_checks=not reasons, reasons=tuple(reasons))


class ReadOnlyBroker(Protocol):
    def capabilities(self) -> BrokerCapabilities: ...
    def account(self) -> AccountSnapshot | None: ...
    def instrument(self, symbol: str) -> InstrumentSpecification | None: ...
    def events_after(self, sequence: int, *, limit: int) -> tuple[BrokerEvent, ...]: ...


def _validate_page(sequence: int, limit: int):
    if not _time(sequence) or type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("Invalid event cursor or page limit")


class DisabledBroker:
    """Intentionally disconnected stub; never imports a broker SDK."""
    def capabilities(self) -> BrokerCapabilities:
        return BrokerCapabilities(id="disabled-v1")

    def account(self) -> None:
        return None

    def instrument(self, symbol: str) -> None:
        return None

    def events_after(self, sequence: int, *, limit: int) -> tuple[BrokerEvent, ...]:
        _validate_page(sequence, limit)
        return ()
