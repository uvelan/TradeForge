"""Deterministic offline paper market-order ledger, not a broker adapter."""
from dataclasses import dataclass, field, replace
from enum import StrEnum

_MAX_ROWS = 1024
_MAX_UNIT = 10**12


class PaperSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


def _integer(value, *, positive=False):
    return type(value) is int and (1 if positive else 0) <= value <= _MAX_UNIT


def _label(value):
    return isinstance(value, str) and 0 < len(value) <= 128 and value.strip() == value


@dataclass(frozen=True, kw_only=True)
class PaperQuote:
    id: str
    symbol: str
    observed_at_ms: int
    bid_units: int
    ask_units: int


@dataclass(frozen=True, kw_only=True)
class PaperOrder:
    id: str
    symbol: str
    side: PaperSide
    quantity_units: int


@dataclass(frozen=True, kw_only=True)
class PaperEvent:
    order: PaperOrder
    quote: PaperQuote | None
    decision_at_ms: int
    status: str
    reason: str | None
    price_units: int | None
    cash_units: int
    position_units: int
    mode: str = field(default="PAPER", init=False)
    schema_version: int = field(default=1, init=False)

    @property
    def id(self):
        return f"paper-v1:{self.order.id}"


@dataclass(frozen=True, kw_only=True)
class PaperLedger:
    """One account/instrument; integer prices are per whole quantity unit.

    Only accept ledger instances reconstructed from your own authoritative
    event storage. This offline model is not a persistence or trust boundary.
    """
    symbol: str
    opening_cash_units: int
    max_quote_age_ms: int
    events: tuple[PaperEvent, ...] = ()
    mode: str = field(default="PAPER", init=False)

    def __post_init__(self):
        if (not _label(self.symbol) or not _integer(self.opening_cash_units)
                or not _integer(self.max_quote_age_ms)
                or not isinstance(self.events, tuple) or len(self.events) > _MAX_ROWS):
            raise ValueError("Invalid paper ledger")

    @property
    def cash_units(self):
        return self.events[-1].cash_units if self.events else self.opening_cash_units

    @property
    def position_units(self):
        return self.events[-1].position_units if self.events else 0

    def _validate_order(self, order: PaperOrder):
        if (not _label(order.id) or order.symbol != self.symbol or not isinstance(order.side, PaperSide)
                or not _integer(order.quantity_units, positive=True)):
            raise ValueError("Invalid paper order")

    def cancel(self, order: PaperOrder, *, decision_at_ms: int):
        """Cancel before processing; if already terminal, never undo the result."""
        self._validate_order(order)
        if type(decision_at_ms) is not int or not 0 <= decision_at_ms <= 32_503_680_000_000:
            raise ValueError("Invalid paper decision time")
        for prior in self.events:
            if prior.order.id == order.id:
                if prior.order != order:
                    raise ValueError("Paper order ID already binds different evidence")
                return self, prior
        self._validate_new(decision_at_ms)
        event = PaperEvent(order=order, quote=None, decision_at_ms=decision_at_ms,
                           status="PAPER_CANCELED", reason=None, price_units=None,
                           cash_units=self.cash_units, position_units=self.position_units)
        return replace(self, events=(*self.events, event)), event

    def _validate_new(self, decision_at_ms):
        if len(self.events) == _MAX_ROWS:
            raise ValueError("Paper event capacity reached")
        if self.events and decision_at_ms < self.events[-1].decision_at_ms:
            raise ValueError("Paper decision time is not monotonic")

    def apply(self, order: PaperOrder, quote: PaperQuote, *, decision_at_ms: int):
        """Return a new ledger and its terminal event; original ledger is unchanged."""
        self._validate_order(order)
        if (not _label(quote.id) or quote.symbol != self.symbol
                or not all(_integer(v, positive=True) for v in (quote.bid_units, quote.ask_units))
                or quote.bid_units > quote.ask_units
                or not all(type(t) is int and 0 <= t <= 32_503_680_000_000
                           for t in (quote.observed_at_ms, decision_at_ms))):
            raise ValueError("Invalid paper quote evidence")
        for prior in self.events:
            if prior.order.id == order.id:
                if (prior.order != order or (prior.status != "PAPER_CANCELED"
                        and (prior.quote, prior.decision_at_ms) != (quote, decision_at_ms))):
                    raise ValueError("Paper order ID already binds different evidence")
                return self, prior
        self._validate_new(decision_at_ms)

        buying = order.side is PaperSide.BUY
        price = quote.ask_units if buying else quote.bid_units
        value = price * order.quantity_units
        cash, position = self.cash_units, self.position_units
        reason = None
        if not 0 <= decision_at_ms - quote.observed_at_ms <= self.max_quote_age_ms:
            reason = "STALE_OR_FUTURE_QUOTE"
        elif buying and value > cash:
            reason = "INSUFFICIENT_PAPER_CASH"
        elif not buying and order.quantity_units > position:
            reason = "INSUFFICIENT_PAPER_POSITION"
        else:
            direction = 1 if buying else -1
            cash -= direction * value
            position += direction * order.quantity_units
        event = PaperEvent(order=order, quote=quote, decision_at_ms=decision_at_ms,
                           status="PAPER_REJECTED" if reason else "PAPER_FILLED",
                           reason=reason, price_units=None if reason else price,
                           cash_units=cash, position_units=position)
        return replace(self, events=(*self.events, event)), event
