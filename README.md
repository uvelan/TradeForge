# TradeForge

Local Windows terminal for forex research. The main dashboard includes chart replay, trade planning, illustrative risk estimates, manual AI review packages, a learning journal, and local Dukascopy BI5 import. The Data Library links to the fixed-point catalog for persisted synthetic EURUSD candles and bounded MT5 date-range imports.

## Start on Windows

From the repository root, using 64-bit Python 3.13 and Node 24:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
npm --prefix frontend ci
.\start-api.ps1
```

In another PowerShell window run `.\start-terminal.ps1`. Open <http://127.0.0.1:4180> for the research dashboard. To generate and persist a fixture, select **Data Library → Open fixed-point catalog → Generate EURUSD simulation**. Data lives under `storage` by default, with research data under `storage/research`. Set `TRADEFORGE_STORAGE` before starting the API to change that base. `TRADEFORGE_MT5_TERMINAL` selects an installed terminal for the fixed-point catalog.

The MT5 status probe uses the current terminal configuration. Synthetic candles are fixed fixtures and independently generated for each timeframe. Use them to exercise the application workflow.

Select **Binance Spot** in the Market Terminal to chart BTC/USDT. The app requests completed history through the public REST API and subscribes to live chart updates in the browser. The source label reports the connection state. This feed is for chart viewing and requires no API key.

To import broker history, select M1, M5, M15, or M30, enter the exact symbol shown in MT5, choose a UTC date range with an exclusive end, and select **Import MT5 candles**. M1 accepts up to six full UTC days per request. A coverage notice appears when the broker returns fewer candles than the requested interval contains.

## Verify

```powershell
.\.venv\Scripts\python.exe -m pytest -q
npm --prefix frontend run build
npm --prefix frontend test
Set-Location frontend
npx playwright install chromium
npm run e2e
```

The browser suite starts isolated servers. Free ports 8000 and 4180 before running it. See the [implementation status](docs/IMPLEMENTATION_STATUS.md) for coverage and outstanding work.
