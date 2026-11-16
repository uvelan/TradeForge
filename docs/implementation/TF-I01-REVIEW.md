# TF-I01 review evidence

TF-D02 merged in PR #2 at `9ec151e401a74352c78e77d441ede1dc0ff56f0d`. That is the TF-I01 base. The implementation and test commit is `1a7e1de3909245b4348a20917a526ad36a360bf2`. The evidence follows in a separate commit.

## Architecture and contracts

- `research_backend/app/indicator_engine.py` defines the typed version-1 catalog and the batch evaluator. `indicator_routes.py` exposes the catalog and `POST /api/indicators/evaluate`. The original `/calculate` response stays compatible. The batch accepts up to eight unique indicator IDs and 2,000 ordered, nonoverlapping candles. It returns the algorithm version, UTC evaluation cutoff, calculation timestamp, available input count and SHA-256 fingerprint, declared feed and dataset/partition lineage, warm-up and quality warnings, and point status and availability.
- The prefix contains only closed candles available at or before the requested cutoff. A bar is available no earlier than the later of its nominal close and the supplied availability timestamp. An output that uses earlier delayed evidence cannot become available ahead of that evidence. Numerical windows continue over observed bars and report gaps. Range confirmation requires complete interval coverage.
- The fingerprint describes input content. Browser submissions have `CLIENT_SUPPLIED_UNVERIFIED` authority even when they carry declared partition IDs or checksums. This evaluator does not authenticate archive lineage or mint trading alerts.
- `frontend/src/indicators.tsx` persists enabled indicators and settings. It aborts obsolete requests. `App.tsx` supplies the current symbol/feed and closed replay prefix. `components.tsx` owns only rendering and reconciles line series, removes disabled panes, and leaves drawing input with its existing controller. `workspace.css` styles the library controls. `requirements.in` and `requirements.lock` pin the timezone database needed on Windows.
- The same Python evaluator can accept a frozen historical input and cutoff. The built-in strategy laboratory and AI export contracts remain separate. Existing chart SMT and the existing workspace session panel retain their behavior.

## Indicators and mathematical fixtures

| Indicator | Version-1 rule |
| --- | --- |
| EMA | Seed from the SMA of the first N closes and update with alpha 2/(N+1). With period 2 and closes 10, 12, 11, 14, 10 the outputs are 11, 11, 13, 11. |
| RSI | Wilder average on gains and losses. Requires N+1 closes. Flat gives 50, all gains give 100 and all losses give 0. The same period-2 fixture gives 66.6667, 88.8889, 32. |
| ATR | The first true range is high minus low. Later true ranges use the maximum of high minus low and the two distances from the last observed close. Wilder averaging of true ranges 2, 3, 2, 4, 5 gives 2.5, 2.25, 3.125, 4.0625 for period 2. ATR is in instrument price units. |
| Previous day/week | Use the wick extremes of the immediately preceding fully covered UTC day or Monday-based UTC week. Each displayed value carries the UTC reference window. |
| Asia/London/New York | Default to Tokyo 09:00–18:00, London 08:00–17:00, and New York 08:00–17:00 local time. A session can use a custom IANA timezone and an overnight window. Developing values are provisional. A completed value requires all bars in the window and a reached close. Ambiguous or nonexistent local boundary times are rejected for that window. |

The old SMA, WMA, Bollinger Bands, Donchian Channels, MACD, confirmed swings, ROC and normalized ATR stay available.

## Verification

| Check | Result |
| --- | --- |
| Backend | 90 passed, including 13 new tests |
| Vitest | 8 passed |
| TypeScript and Vite | Production build passed |
| Chromium | 39 passed, including all 35 existing scenarios |

The tests use independent formula values, constant markets, gaps, duplicate/overlapping bars, daily and weekly boundaries, and expected UTC conversions on either side of spring and autumn DST. The higher-timeframe fixture verifies an H1 bar arriving after its nominal close. Chromium checks persistent configuration, synchronized panes, repeated removal/re-add, chart scope switches, a historical cutoff, session range results, and TF-D02 drawing creation and editing with EMA active.

Local commands were `..venvScriptspython.exe -m pytest -q` from the repository root, then `npm test`, `npm run build`, and `npm run e2e -- --reporter=line` in `frontend`. The browser used isolated ports 4198 and 8098 and a temporary database. Hosted checks and exact head are available through the pull request.

[Backend log](evidence/TF-I01/backend.txt) · [Vitest log](evidence/TF-I01/vitest.txt) · [Build log](evidence/TF-I01/build.txt) · [Chromium log](evidence/TF-I01/playwright.txt)

[EMA, RSI and ATR](evidence/TF-I01/numerical-panes.png) · [Session overlay](evidence/TF-I01/session-overlay.png) · [Drawing with EMA](evidence/TF-I01/drawing-with-ema.png)

## Performance

The timing fixture evaluates 2,000 synthetic M5 candles and eight definitions on Windows with Python 3.13.14. It warms the evaluator once and records 30 calls using `perf_counter`. Input construction, HTTP, serialization and rendering are outside the measurement. The browser tests assert stable pane counts across repeated toggles. They do not measure retained heap.

[Evaluator timing](evidence/TF-I01/evaluator-timing.txt)

## Limits and rollback

- Strict coverage can suppress daily or weekly levels on archives with holidays or scheduled closures. The engine has no venue-specific closure calendars. A fine timeframe can also require more bars than the chart or API includes. Session windows on coarse or nonaligned bars can remain provisional.
- Traditional values continue across observed gaps with an explicit warning. A gap can represent a closure or missing feed data. The evaluator does not infer which cause applies.
- The terminal's primary series is the indicator input. There is no multi-series or cross-timeframe aggregation in this phase. A historical caller can evaluate a separate timeframe at the same cutoff.
- Broker connectivity and physical devices were outside the test environment. The historical-feed contract was exercised with synthetic fixtures. There is no new live execution or indicator-to-backtest strategy wiring.
- To roll back, revert the implementation commit and its evidence commit before release. Existing schema and storage need no migration. The prior primary indicator preference remains compatible.
