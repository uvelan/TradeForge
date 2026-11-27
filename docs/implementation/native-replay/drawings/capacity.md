# Native replay drawing capacity on Windows

The candidate rendered and persisted 250 drawings across four 5,000-candle panes. It met the 33 ms pan and zoom frame p95 target at 1, 25 and 250 drawings. Four native writes attempting 250 drawings each hit the existing 100-item per-book limit, so the 1,000-drawing case has no render or timing result.

## Workload

Both runs used Windows build 26200, an Intel i7-9700K with 16 GiB RAM, Chromium 159.0.8154.6, a 1536 × 900 CSS-pixel viewport and DPR 1. The Windows host was reserved for these runs after the other browser jobs finished. The baseline application was PR #41 merge commit `7153f40f22d7f50529ef7c019921aa003a7ae6e7`. Its harness checkout was `7ccac1db5b7cce2f7755bce73b1b1ce1f30d8fe3`. The candidate application was `270455f8495b24e14442fcd3b4b27a6a9743ebf5`, measured at `ca135ccbaa7dc7930577b9a31d03c22735ec6ded` with the same test-only timing patch.

The controlled fixture used 72 BI5 uploads through the production import endpoint. EURUSD M5, GBPUSD M1, USDJPY M5 and XAUUSD M1 each supplied 5,000 bars. Playwright compared every plotted candle's UTC open time and OHLC with the independent BI5 reference on each reopen. Both runs used the same frozen replay reference and distinct copies of its store. The fixture SHA-256 was `0a102e550c674cdd6d8446f3857d7c5555846f66a30544a90840c9842ab8f352`. The browser used the application APIs and actual input gestures.

The harness seeded visible horizontal lines through the drawings API. Each line had a distinct stroke color so a painted line could be traced to the persisted ID. Canvas counts include a stroke only when it appeared inside the pane's chart area. Each trial selected the intended drawing by clicking its line, moved the drawing vertically, verified the preview stroke before release, and checked the returned revision against a separate GET. It then left the workspace, reopened the saved replay, and compared the actual stroke row with the chart's projection of the exact saved price. The remaining drawing IDs and anchors in the edited book were compared on every save.

## Results

Pane order is primary, 2, 3, 4. Each row includes three warmups followed by seven measured trials. Timings are milliseconds, shown as median / p95. Gesture frame p95 uses the pooled frame intervals for that row. Other percentiles use seven trial durations with nearest-rank quantiles.

| Application | Requested | Persisted by pane | Painted by pane | Gesture pane | Frame intervals | Pan/zoom frame | Click to selection | Drag to visible preview | Release to save | Reopen |
|---|---:|---|---|---|---:|---|---|---|---|---|
| Baseline | 1 | 1 / 0 / 0 / 0 | 1 / 0 / 0 / 0 | Primary | 111 | 16.70 / 16.70 | 155.80 / 172.50 | 0.80 / 1.20 | 74.40 / 952.70 | 1,274.63 / 1,495.21 |
| Candidate | 1 | 1 / 0 / 0 / 0 | 1 / 0 / 0 / 0 | Primary | 113 | 16.70 / 16.80 | 166.80 / 179.10 | 1.40 / 1.80 | 90.60 / 948.00 | 1,392.48 / 1,521.47 |
| Baseline | 25 | 25 / 0 / 0 / 0 | 25 / 0 / 0 / 0 | Primary | 118 | 16.70 / 16.80 | 152.10 / 168.80 | 1.10 / 2.40 | 427.50 / 1,051.10 | 1,275.28 / 1,465.59 |
| Candidate | 25 | 25 / 0 / 0 / 0 | 25 / 0 / 0 / 0 | Primary | 112 | 16.70 / 16.70 | 151.40 / 161.70 | 0.90 / 1.00 | 69.40 / 803.90 | 1,350.65 / 1,474.82 |
| Baseline | 250 | 63 / 63 / 62 / 62 | 63 / 0 / 0 / 0 | Primary | 123 | 16.70 / 16.80 | 166.60 / 211.40 | 1.30 / 2.00 | 96.00 / 607.30 | 1,406.04 / 1,910.10 |
| Candidate | 250 | 63 / 63 / 62 / 62 | 63 / 63 / 62 / 62 | View 2, GBPUSD M1 | 117 | 16.70 / 16.70 | 445.40 / 465.20 | 1.20 / 1.70 | 77.00 / 1,187.70 | 1,426.39 / 1,577.10 |

The 1- and 25-drawing rows compare equivalent render loads and use the same interaction pane. A relative 250-drawing latency delta is inapplicable because the baseline painted only the primary book and the candidate handled its gesture on view 2. At 250 drawings, the candidate companion selection took 465.20 ms p95 and the preview stroke took 1.70 ms p95. The 33 ms target applies to gesture frame intervals. Each 1,000-drawing probe preserved the existing books and returned four HTTP 422 responses with an item limit of 100. [Baseline trials](capacity-baseline.json) · [Candidate trials](capacity-candidate.json)

### Timing boundaries

- **Pan and zoom** use animation-frame intervals from immediately before a 45 px, 12-step drag through two frames after a wheel zoom. The logical range moved on the drag and narrowed on zoom.
- **Selection** starts at browser pointer release and ends on the first browser animation frame where the toolbox selector contains the intended drawing ID. The observer is armed before the click.
- **Drag preview** starts at the pointer move during an active drag and ends when the application completes the gold canvas stroke at the shifted row.
- **Save** starts at pointer release and ends at the browser Resource Timing response end for the versioned drawing POST. The request returned HTTP 200 and GET recovered the identical revision and item list.
- **Reopen** starts immediately before selecting the saved replay. It includes the native GET, full candle and paint verification, and two animation frames. The subsequent exact saved-price projection check is a separate assertion.

Both clocks capture their endpoints inside the page. Playwright retrieves the stored timestamps after the gestures. The selection measurement includes animation-frame scheduling. The preview measurement ends at the canvas stroke API call, before any compositor presentation.

### Resources

Each run verified zero outstanding app-tracked charts, subscriptions, observers, listeners, timers and animation frames after all 30 trial unmounts. Each also did three teardown operations between sizes, leaving 264 chart instances created and 264 disposed across 33 total teardown operations. Neither receipt contains an uncaught page error. Chromium renderer heap after explicit garbage collection was 17.13–18.70 MiB for the baseline and 17.16–18.57 MiB for the candidate. Those measurements describe the unmounted heap in this workload.

## Independent review

Reviewed candidate `270455f` against PR #41, following pointer and keyboard input through the active pane, candle and price checks, persisted version updates, canvas drawing and chart teardown. The review assumes one local Market Terminal with four loaded replay panes and accounts for repeated panes sharing a market book. The optimistic revision check and shared per-book write lock prevent competing writes from silently overwriting a newer revision. A pane switch cancels its unfinished gesture. A committed edit remains attached to the originating market.

The toolbox is the user-authored market book. The cutoff determines which drawings reach the canvas and which candle evidence the inspector exposes. A frozen companion can have a loaded book before its first eligible candle. At that point the toolbox can arm placement while no drawable plot exists. Placement waits for an eligible candle and never writes into a different book. The 100-item native API ceiling remains per book.

**Verdict** Ship. The timed run exercised the native primary and companion paths and their teardown. The separate genuine-data and keyboard acceptance suites cover fresh-process recovery and a broader set of tools.

## Reproduce

Prepare a disposable native store with the [PR #41 BI5 validation commands](../README.md#local-commands). Clone the prepared store with the API stopped. Run the benchmark from the baseline and candidate code against separate copies of that store and use the same fixture JSON. The environment variable TRADEFORGE_DRAWING_VARIANT identifies the application and the pane-count expectations.

~~~powershell
$env:TRADEFORGE_NATIVE_FIXTURE=(Resolve-Path storage/drawing-capacity/fixture.json).Path
$env:TRADEFORGE_NATIVE_STORAGE=(Resolve-Path storage/drawing-capacity/native).Path
$env:TRADEFORGE_E2E_API_PORT='8823'
$env:TRADEFORGE_E2E_UI_PORT='4823'
$env:TRADEFORGE_DRAWING_VARIANT='baseline'
npm --prefix frontend run e2e -- --config playwright.drawing-capacity.config.ts --retries=0 --output ../storage/drawing-capacity/baseline
~~~

The final baseline measured 09:25:23–09:27:00 UTC and the final candidate measured 09:27:23–09:29:17 UTC on November 27, 2026. The preceding complete development runs measured 09:17:48–09:19:24 UTC and 09:20:17–09:22:08 UTC, using Playwright polling to detect selection and the preview stroke. The older interaction readings are upper-bound observations. [Development baseline](capacity-dev-baseline.json) · [Development candidate](capacity-dev-candidate.json)

Two earlier timed baseline attempts recorded partial trials before harness fixes. The first consumed the browser's Resource Timing entry buffer. The second compared absolute plot rows across different autoscale state. The final method resets the buffer before each measured write and checks the reopened plot row against its own saved-price projection.
