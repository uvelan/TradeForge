# TF-D01 acceptance and rollback

Base: 0dcc2156becf58e6f4f571435bf635dc8c7a58f2, fetched origin/main. The two supplied assignment documents were present as untracked user files and are not part of the PR. [Baseline hosted CI](https://github.com/uvelan/TradeForge/actions/runs/43722618094)

## Reproduction and correction

Open Market Terminal, select DEMO, open Drawing toolbox, arm Trendline, then press, drag and release in the primary plot. Baseline saved no line at either tested resolution. Its sequential native click path rejects pointer movement above six CSS pixels and has no drag commit or live preview. Two separate clicks remained usable. Fixed placement owns pointerdown through matching pointerup, previews using the primary chart transform, and submits one version for a two-anchor drag. A click-click remains valid; touch taps share the pointer path. Risk/reward uses the existing entry, stop and target format.

| Viewport | Baseline screenshot | Fixed screenshot |
|---|---|---|
| 1366×768 | [Before](evidence/TF-D01/before-1366x768.png) | [After](evidence/TF-D01/after-1366x768.png) |
| 1920×1080 | [Before](evidence/TF-D01/before-1920x1080.png) | [After](evidence/TF-D01/after-1920x1080.png) |

Baseline Playwright traces and videos are retained locally under ignored storage/tf-d01-browser-before. Final Playwright writes traces for the drawing suite and the workflow uploads these as TF-D01-browser-evidence. Traces contain synthetic data and isolated test requests only.

## TF-000 restore

Read-only SQLite connections used the online backup API for the fixed-point catalog and research databases. Both restored copies passed PRAGMA integrity_check and reported user_version 0. Two Parquet files matched the manifest SHA-256 on source, backup and restore. Full manifest digest: 12c589936b2049cd1d6451a918acda0e12e02ef5c177ac5babe884b5c29fc7e6. Restore used a different private temporary directory. No real storage was overwritten; no SQLite or Parquet files are committed. Real browser profile preferences were not exported or reset. Browser tests use an isolated origin/database.

## Verification

On Windows, from the repository root:

    .\.venv\Scripts\python.exe -m pytest -q
    cd frontend
    npm run build
    npm test
    $env:TRADEFORGE_E2E_API_PORT='8098'
    $env:TRADEFORGE_E2E_UI_PORT='4198'
    npm run e2e

Baseline: 77 Python, 4 Vitest, production build and six pre-existing Chromium tests passed. Both added drag reproductions failed as intended. Final: 77 Python, 4 Vitest, production build and 13 Chromium tests passed. [Backend](evidence/TF-D01/backend.txt) · [Frontend](evidence/TF-D01/frontend.txt) · [Browser](evidence/TF-D01/browser.txt)

## Limits and rollback

Golden tools are horizontal, trendline, rectangle and risk/reward. Selecting a stroke and placing an anchor through the existing toolbox works. Direct dragging/resizing of a saved shape and drawing on secondary panes are outside this task. Touch ran in Chromium emulation; native mobile hardware and fullscreen interactions were not separately exercised. Old chart and drawing schemas, IDs, settings, API, and live-order posture are preserved.

Roll back by reverting the TF-D01 commit on its branch or closing the unmerged PR. If deployed later, redeploy the parent frontend and leave the SQLite, Parquet and browser preference stores in place. Drawings saved by both versions use the same schema. Use the existing isolated restore copy only for recovery after comparing its cutoff and obtaining authorization to replace data.
