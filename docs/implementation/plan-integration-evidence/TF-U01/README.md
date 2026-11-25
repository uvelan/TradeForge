# TF-U01 chart workspace acceptance

The starting PR head was `d09b5ce4c83f534d6bcbd2b1fc42b9caa86f73be`. Through the unmodified Market Terminal, the DEMO source, GBPUSD, M15 and a two-chart layout survived route navigation, reload and a 1500-to-390 px viewport change. Opening the drawing toolbox survived a viewport change and reset on remount. The watchlist, toolbar and tool rail had no size or collapse settings. Signal Explorer had an Evidence Inspector. The terminal showed data-trust evidence without a configurable inspector. The baseline UI observation and seven existing capture regressions passed in 38.5 s.

Panel settings now stores visibility and size through the existing browser preferences. Watchlist size controls width on desktop and height when stacked. The toolbar and inspector adjust height, and the tool rail adjusts width. Invalid values select the original automatic layout. Drawing tool, snap mode and disclosure use independent keys. Panel edits retain the mounted chart and server-backed drawing revision. The secondary panes store their own indicators.

## Chromium evidence

The real chart factory is instrumented only by the Playwright Vite configuration. The lifecycle test repeatedly enters Market Terminal, uses a chart, leaves for Trade Planner and returns. It rotates 1/2/4 panes, holds an obsolete candle response, changes instrument/timeframe/source and advances a frozen replay cutoff. The following settled counts were observed in a 100-cycle session.

| Layout | Chart instances | Chart subscriptions | Observed chart targets | Application listeners | Application timers | Animation frames |
|---|---:|---:|---:|---:|---:|---:|
| One | 1 | 3 | 9 | 14 | 1 | 0 |
| Two | 2 | 6 | 18 | 27 | 1 | 0 |
| Four | 4 | 12 | 36 | 53 | 1 | 0 |
| Away, every cycle | 0 | 0 | 0 | 0 | 0 | 0 |

Each return matched that layout's initial counts. The session created 688 charts and disposed 687, leaving the active solo chart. No uncaught browser exception was observed. A separate fault-injection test retains a click callback on purpose and requires exactly one outstanding subscription after unmount. These counts measure application registrations and chart-surface observations, not total browser heap.

The panel test compares the restored desktop watchlist controls with their pre-reload pixels (at most 1% differing by more than 16 in any RGB channel), reads the saved drawing through the API, checks the original chart node, and verifies a wheel gesture changes the narrow chart's logical range. Desktop and narrow screenshots are [1536 × 900](desktop-workspace.png) and [390 × 844](narrow-workspace.png). CI uploads fresh screenshots and per-run JSON in **TF-U01-workspace-evidence**. Full-chart screenshots are review artifacts; the pixel gate applies to the controls region.

## Reproduction

From `frontend`, run:

```powershell
npm test
npm run build
$env:TRADEFORGE_E2E_API_PORT='8537'
$env:TRADEFORGE_E2E_UI_PORT='4537'
npm run e2e -- chart-workspace --retries=0
npm run e2e -- --retries=0
```

The full browser command includes capture, drawings, indicators, chart links, and the workspace test. The first focused attempt found missing pointer-enter cleanup and an imprecise select locator. Later development runs corrected an instrument display assertion and a drawing-tool accessible name. The visual assertion was narrowed to the stable controls region and the narrow input assertion measures zoom directly. No automatic retries are configured. To roll back, revert the workspace commit on the integration branch. Additional browser preference keys are safe to leave in place. No database migration is involved.
