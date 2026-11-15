import { useEffect, useState } from "react";
import type { Candle, Timeframe } from "./types";

const intervals: Record<Timeframe, string> = {
  M1: "1m", M3: "3m", M5: "5m", M15: "15m", M30: "30m", H1: "1h", H4: "4h", D1: "1d",
};

type LiveFeed = { frame: Timeframe; status: string; candles: Candle[] };

export function useBinanceCandles(enabled: boolean, frame: Timeframe, onReconnect: () => void) {
  const [feed, setFeed] = useState<LiveFeed>();
  useEffect(() => {
    if (!enabled) {
      setFeed(undefined);
      return;
    }
    let active = true;
    let connectedOnce = false;
    let delay = 1000;
    let retry: number | undefined;
    let socket: WebSocket;
    setFeed({ frame, status: "Connecting", candles: [] });
    const connect = () => {
      socket = new WebSocket(`wss://data-stream.binance.vision/ws/btcusdt@kline_${intervals[frame]}`);
      socket.onopen = () => {
        if (!active) return;
        if (connectedOnce) onReconnect();
        connectedOnce = true;
        delay = 1000;
        setFeed(current => ({ frame, status: "Connected", candles: current?.candles ?? [] }));
      };
      socket.onmessage = event => {
        if (!active) return;
        try {
          const message = JSON.parse(event.data);
          const k = message.k;
          if (message.e !== "kline" || k?.s !== "BTCUSDT" || k.i !== intervals[frame]) return;
          const candle = {
            time: Number(k.t) / 1000, open: Number(k.o), high: Number(k.h), low: Number(k.l), close: Number(k.c),
          };
          if (!Object.values(candle).every(value => Number.isFinite(value) && value > 0) ||
              !Number.isInteger(candle.time) || candle.low > Math.min(candle.open, candle.close) ||
              candle.high < Math.max(candle.open, candle.close)) return;
          setFeed(current => {
            const rows = current?.candles ?? [];
            const last = rows.at(-1);
            if (last && candle.time < last.time) return current;
            return {
              frame, status: "Live",
              candles: (last?.time === candle.time ? [...rows.slice(0, -1), candle] : [...rows, candle]).slice(-600),
            };
          });
        } catch {
          // Ignore malformed upstream messages and wait for the next candle update.
        }
      };
      socket.onclose = () => {
        if (!active) return;
        setFeed(current => ({ frame, status: "Disconnected · retrying", candles: current?.candles ?? [] }));
        retry = window.setTimeout(connect, delay);
        delay = Math.min(delay * 2, 30_000);
      };
    };
    connect();
    return () => {
      active = false;
      window.clearTimeout(retry);
      socket.close();
    };
  }, [enabled, frame, onReconnect]);
  return enabled && feed?.frame === frame ? feed : undefined;
}
