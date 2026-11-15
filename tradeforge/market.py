from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from importlib import import_module
from math import sin
from threading import Lock
from typing import Any, Callable, Protocol, TypeVar

MT5_LOCK = Lock()

class Timeframe(StrEnum):
    M1 = "M1"
    M3 = "M3"
    M5 = "M5"
    M15 = "M15"
    M30 = "M30"
    H1 = "H1"
    H4 = "H4"
    D1 = "D1"

    @property
    def seconds(self) -> int:
        return dict(M1=60, M3=180, M5=300, M15=900, M30=1800, H1=3600, H4=14400, D1=86400)[self]


@dataclass(frozen=True)
class Candle:
    time: int
    available_at: int
    open: int
    high: int
    low: int
    close: int


class CandleSource(Protocol):
    provider: str
    feed: str
    price_basis: str
    price_scale: int

    def candles(self, symbol: str, timeframe: Timeframe, start: int, end: int) -> list[Candle]: ...


class Simulator:
    provider = "simulator"
    feed = "deterministic-eurusd-v1"
    price_basis = "synthetic_bid"
    price_scale = 100_000

    def candles(self, symbol: str, timeframe: Timeframe, start: int, end: int) -> list[Candle]:
        if symbol != "EURUSD":
            raise ValueError("Simulator supports EURUSD only")
        step = timeframe.seconds

        def price(index: int) -> int:
            return 108_450 + round(72 * sin(index / 17) + 28 * sin(index / 3.7) + 55 * sin(index / 39))

        rows = []
        for timestamp in range(start, end, step):
            index = timestamp // step
            opening, closing = price(index), price(index + 1)
            rows.append(Candle(timestamp, timestamp + step, opening,
                               max(opening, closing) + 3 + index % 11,
                               min(opening, closing) - 3 - index % 7, closing))
        return rows


Result = TypeVar("Result")


class MT5Unavailable(RuntimeError):
    pass


class MT5Connection:
    def __init__(self, terminal_path: str | None = None, load_module: Callable[[], Any] | None = None):
        self.terminal_path = terminal_path
        self.load_module = load_module or (lambda: import_module("MetaTrader5"))
        self.lock = MT5_LOCK

    def read(self, operation: Callable[[Any, Any], Result]) -> Result:
        try:
            mt5 = self.load_module()
        except ImportError as error:
            raise MT5Unavailable("MT5 Python integration unavailable") from error
        with self.lock:
            args = (self.terminal_path,) if self.terminal_path else ()
            if not mt5.initialize(*args, timeout=1500):
                raise MT5Unavailable("MT5 initialization failed")
            try:
                terminal = mt5.terminal_info()
                if not terminal or not terminal.connected:
                    raise MT5Unavailable("MT5 terminal disconnected")
                return operation(mt5, terminal)
            finally:
                mt5.shutdown()

    def connected(self) -> bool:
        try:
            return self.read(lambda _module, _terminal: True)
        except MT5Unavailable:
            return False

    def historical_candles(self, symbol: str, timeframe: Timeframe, start: int, end: int):
        def fetch(mt5, _terminal):
            account = mt5.account_info()
            instrument = mt5.symbol_info(symbol)
            if account is None or not account.server or instrument is None:
                raise MT5Unavailable("MT5 account or instrument unavailable")
            if instrument.chart_mode != 0:
                raise ValueError("Only MT5 instruments with bid-based bars are supported")
            scale = 10 ** instrument.digits
            rates = mt5.copy_rates_range(
                symbol, getattr(mt5, f"TIMEFRAME_{timeframe}"),
                datetime.fromtimestamp(start, UTC), datetime.fromtimestamp(end - 1, UTC),
            )
            if rates is None:
                raise MT5Unavailable("MT5 candle history unavailable")

            def units(value) -> int:
                scaled = Decimal(str(value)) * scale
                if not scaled.is_finite() or scaled != scaled.to_integral_value():
                    raise ValueError("MT5 candle exceeds instrument precision")
                return int(scaled)

            candles = []
            for row in rates:
                timestamp = int(row["time"])
                candles.append(Candle(timestamp, timestamp + timeframe.seconds,
                                      *(units(row[name]) for name in ("open", "high", "low", "close"))))
            return account.server, scale, candles

        return self.read(fetch)


def utc_now() -> datetime:
    return datetime.now(UTC)
