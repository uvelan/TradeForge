# Chart workspace — design reference

Reference: https://candl.live/charts (CandL's chart-first terminal), with its user guide at https://candl.live/help.html.

TradeForge's implementation is original, not a replica of CandL. No CandL assets, trademarks, scripts, proprietary datasets, or CSS have been copied.

## Implemented interaction ideas

- Chart-focused layout with compact upper toolbar and right-side symbol watchlist.
- Dark chart surface, price/time axes, interactive pan/zoom and crosshair powered by the open-source Lightweight Charts library.
- Fast timeframe switching across M1/M5/M15/M30/H1/H4/D1.
- Two-chart layout with a second configurable timeframe.
- Horizontal price-level drawing via click, browser-local persistence, clear-all command.
- Chart PNG snapshot download.
- Historical bar replay, step and play/pause; hides candles beyond replay cutoff, including incomplete higher-timeframe bars in split mode.
- Responsive watchlist and chart controls, source provenance indicator, visible synthetic-data warning.

## Not implemented

- CandL's extensive indicators, drawing tools, Pine-like scripting, price alerts, layout templates, crosshair synchronization, annotation drag handles, or server-synced workspaces.
- No CandL market data or account integration.

## UI accessibility and correctness notes

- All controls are buttons/selects, rather than passive mockups.
- Replay is limited to locally loaded candles and is not a trade fill simulator.
- Chart price levels are stored in the current browser, not the SQLite event ledger. They are not part of AI export evidence yet.
- Live broker quotes require MetaTrader 5, with invalid/stale or unavailable responses visibly separated from demo data.
