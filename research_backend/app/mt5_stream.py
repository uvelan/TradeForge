"""Shared server-side MT5 live stream hub and Server-Sent Events serializer.

Cadence, caching, and lock handling:
- Backend-to-MT5 poll cadence is fixed at ``MT5_STREAM_CADENCE_SECONDS`` (1.0s)
  when at least one subscriber is active, eliminating repeated multi-tab browser
  polling loops.
- Adapter calls go through ``market.mt5_rates`` / ``market.mt5_quote`` (serialized
  by ``tradeforge.market.MT5_LOCK``) and are cached for ``MT5_CACHE_TTL_SECONDS``
  (0.75s) keyed by ``(symbol, timeframe, limit, include_latest)`` and quote symbol.
  Multiple browser tabs or chart panes share those cached snapshots and wake up on
  revision or new-bar diffs.
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from .market import FRAME_MIN, mt5_quote, mt5_rates

MT5_STREAM_CADENCE_SECONDS = 1.0
MT5_CACHE_TTL_SECONDS = 0.75
MT5_KEEPALIVE_TICKS = 15


def _candle_signature(candles: list[dict[str, Any]], provenance: dict[str, Any]) -> tuple[Any, ...]:
    tail = candles[-1] if candles else None
    tail_key = (
        None
        if tail is None
        else (
            tail.get("time"),
            tail.get("open"),
            tail.get("high"),
            tail.get("low"),
            tail.get("close"),
            tail.get("volume"),
        )
    )
    return (
        len(candles),
        candles[0].get("time") if candles else None,
        tail_key,
        provenance.get("feed_id"),
        provenance.get("price_basis"),
    )


def format_sse(event: str, payload: dict[str, Any], event_id: int | None = None, retry_ms: int | None = None) -> str:
    lines: list[str] = []
    if retry_ms is not None:
        lines.append(f"retry: {retry_ms}")
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event}")
    lines.append(f"data: {json.dumps(payload, separators=(',', ':'))}")
    return "\n".join(lines) + "\n\n"


@dataclass
class _CachedCandles:
    updated_at: float
    payload: dict[str, Any]
    signature: tuple[Any, ...]


@dataclass
class _CachedQuote:
    updated_at: float
    bid: float | None
    error: str | None = None


@dataclass(eq=False)
class _Subscriber:
    symbol: str
    timeframe: str
    limit: int
    include_latest: bool
    quote_symbols: tuple[str, ...]
    queue: asyncio.Queue[str | None] = field(default_factory=lambda: asyncio.Queue(maxsize=64))
    last_candle_sig: tuple[Any, ...] | None = None
    last_quotes: dict[str, float | None] | None = None
    last_error: str | None = None
    idle_ticks: int = 0


class Mt5StreamHub:
    """Coalesces concurrent MT5 SSE subscribers onto shared cached snapshots."""

    def __init__(
        self,
        *,
        candle_reader: Callable[..., list[dict[str, Any]]] | None = None,
        quote_reader: Callable[[str], dict[str, Any]] | None = None,
        cadence_seconds: float = MT5_STREAM_CADENCE_SECONDS,
        cache_ttl_seconds: float = MT5_CACHE_TTL_SECONDS,
    ) -> None:
        self._candle_reader = candle_reader or mt5_rates
        self._quote_reader = quote_reader or mt5_quote
        self.cadence_seconds = cadence_seconds
        self.cache_ttl_seconds = cache_ttl_seconds
        self._subscribers: set[_Subscriber] = set()
        self._candle_cache: dict[tuple[str, str, int, bool], _CachedCandles] = {}
        self._quote_cache: dict[str, _CachedQuote] = {}
        self._inflight_candles: dict[tuple[str, str, int, bool], asyncio.Task[dict[str, Any]]] = {}
        self._inflight_quotes: dict[str, asyncio.Task[float | None]] = {}
        self._worker: asyncio.Task[None] | None = None
        self._seq = 0

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    @property
    def worker_running(self) -> bool:
        return self._worker is not None and not self._worker.done()

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    async def _read_candles(self, symbol: str, timeframe: str, limit: int, include_latest: bool) -> dict[str, Any]:
        key = (symbol, timeframe, limit, include_latest)
        cached = self._candle_cache.get(key)
        now = time.monotonic()
        if cached is not None and (now - cached.updated_at) < self.cache_ttl_seconds:
            return cached.payload

        inflight = self._inflight_candles.get(key)
        if inflight is None:
            async def _fetch() -> dict[str, Any]:
                provenance: dict[str, Any] = {}
                candles = await asyncio.to_thread(
                    self._candle_reader,
                    symbol,
                    timeframe,
                    limit,
                    include_latest=include_latest,
                    provenance=provenance,
                )
                payload = {
                    "source": "MT5",
                    "synthetic": False,
                    "price_basis": provenance.get("price_basis", "BROKER_BARS_UNSPECIFIED"),
                    "candles": candles,
                    "latest_unconfirmed": include_latest and bool(candles),
                    **provenance,
                }
                self._candle_cache[key] = _CachedCandles(
                    updated_at=time.monotonic(),
                    payload=payload,
                    signature=_candle_signature(candles, provenance),
                )
                return payload

            inflight = asyncio.create_task(_fetch())
            self._inflight_candles[key] = inflight
        try:
            return await inflight
        finally:
            if self._inflight_candles.get(key) is inflight and inflight.done():
                self._inflight_candles.pop(key, None)

    async def _read_quote(self, symbol: str) -> float | None:
        cached = self._quote_cache.get(symbol)
        now = time.monotonic()
        if cached is not None and (now - cached.updated_at) < self.cache_ttl_seconds:
            return cached.bid

        inflight = self._inflight_quotes.get(symbol)
        if inflight is None:
            async def _fetch() -> float | None:
                try:
                    result = await asyncio.to_thread(self._quote_reader, symbol)
                    bid = float(result["bid"]) if result and result.get("bid") is not None else None
                    self._quote_cache[symbol] = _CachedQuote(updated_at=time.monotonic(), bid=bid)
                    return bid
                except (RuntimeError, ValueError):
                    self._quote_cache[symbol] = _CachedQuote(updated_at=time.monotonic(), bid=None, error="unavailable")
                    return None

            inflight = asyncio.create_task(_fetch())
            self._inflight_quotes[symbol] = inflight
        try:
            return await inflight
        finally:
            if self._inflight_quotes.get(symbol) is inflight and inflight.done():
                self._inflight_quotes.pop(symbol, None)

    async def _read_quotes(self, symbols: tuple[str, ...]) -> dict[str, float | None]:
        out: dict[str, float | None] = {}
        for sym in symbols:
            out[sym] = await self._read_quote(sym)
        return out

    def _enqueue(self, sub: _Subscriber, chunk: str) -> None:
        if sub.queue.full():
            try:
                sub.queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        sub.queue.put_nowait(chunk)

    async def _tick(self) -> None:
        subs = list(self._subscribers)
        if not subs:
            return

        candle_keys = {(s.symbol, s.timeframe, s.limit, s.include_latest) for s in subs}
        quote_syms = sorted({sym for s in subs for sym in s.quote_symbols})

        candle_results: dict[tuple[str, str, int, bool], tuple[dict[str, Any] | None, str | None]] = {}
        for key in candle_keys:
            try:
                payload = await self._read_candles(*key)
                candle_results[key] = (payload, None)
            except (RuntimeError, ValueError) as exc:
                candle_results[key] = (None, str(exc))

        quotes_all = await self._read_quotes(tuple(quote_syms)) if quote_syms else {}

        for sub in subs:
            if sub not in self._subscribers:
                continue
            key = (sub.symbol, sub.timeframe, sub.limit, sub.include_latest)
            payload, err = candle_results[key]
            emitted = False

            if err is not None:
                if sub.last_error != err:
                    sub.last_error = err
                    seq = self._next_seq()
                    self._enqueue(sub, format_sse("mt5_error", {"sequence": seq, "error": err, "recoverable": True}, event_id=seq))
                    emitted = True
            elif payload is not None:
                sub.last_error = None
                sig = _candle_signature(payload["candles"], payload)
                if sig != sub.last_candle_sig:
                    sub.last_candle_sig = sig
                    seq = self._next_seq()
                    self._enqueue(sub, format_sse("candles", {"sequence": seq, "candles": payload}, event_id=seq))
                    emitted = True

            if sub.quote_symbols:
                sub_quotes = {sym: quotes_all.get(sym) for sym in sub.quote_symbols}
                if sub_quotes != sub.last_quotes:
                    sub.last_quotes = sub_quotes
                    seq = self._next_seq()
                    self._enqueue(sub, format_sse("quote", {"sequence": seq, "quotes": sub_quotes}, event_id=seq))
                    emitted = True

            if emitted:
                sub.idle_ticks = 0
            else:
                sub.idle_ticks += 1
                if sub.idle_ticks >= MT5_KEEPALIVE_TICKS:
                    sub.idle_ticks = 0
                    self._enqueue(sub, ": keepalive\n\n")

    async def _run(self) -> None:
        try:
            while self._subscribers:
                await asyncio.sleep(self.cadence_seconds)
                if not self._subscribers:
                    break
                await self._tick()
        except asyncio.CancelledError:
            pass
        finally:
            self._worker = None

    def _ensure_worker(self) -> None:
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run())

    def _unsubscribe(self, sub: _Subscriber) -> None:
        self._subscribers.discard(sub)
        if not self._subscribers:
            if self._worker is not None and not self._worker.done():
                self._worker.cancel()
            self._worker = None

    async def open_stream(
        self,
        *,
        symbol: str,
        timeframe: str,
        limit: int = 10,
        include_latest: bool = True,
        quote_symbols: tuple[str, ...] = (),
        is_disconnected: Callable[[], Awaitable[bool]] | None = None,
    ):
        """Validate initial live snapshot, then return an async SSE chunk generator."""
        if timeframe not in FRAME_MIN or not symbol.strip() or len(symbol) > 64:
            raise ValueError("Unsupported instrument or timeframe")

        initial_candles = await self._read_candles(symbol, timeframe, limit, include_latest)
        initial_quotes = await self._read_quotes(quote_symbols) if quote_symbols else {}
        sig = _candle_signature(initial_candles["candles"], initial_candles)

        sub = _Subscriber(
            symbol=symbol,
            timeframe=timeframe,
            limit=limit,
            include_latest=include_latest,
            quote_symbols=quote_symbols,
            last_candle_sig=sig,
            last_quotes=dict(initial_quotes) if quote_symbols else None,
        )
        self._subscribers.add(sub)
        self._ensure_worker()

        seq = self._next_seq()
        initial_chunk = format_sse(
            "snapshot",
            {
                "sequence": seq,
                "cadence_seconds": self.cadence_seconds,
                "candles": initial_candles,
                "quotes": initial_quotes,
            },
            event_id=seq,
            retry_ms=int(max(500, self.cadence_seconds * 2000)),
        )

        async def _chunks():
            try:
                yield initial_chunk
                while True:
                    if is_disconnected is not None and await is_disconnected():
                        break
                    try:
                        chunk = await asyncio.wait_for(sub.queue.get(), timeout=max(0.25, self.cadence_seconds))
                    except asyncio.TimeoutError:
                        continue
                    if chunk is None:
                        break
                    yield chunk
            finally:
                self._unsubscribe(sub)

        return _chunks()


mt5_stream_hub = Mt5StreamHub()
