# Confluence correction API and archive verification

The original writer ran at `7cdacaf4c10bdd1073128c0a4d10b2e34be7c7f6`. The corrected API ran at `db80a2ec866c7af57d3c2e9f60d263d28ddcb38e`. Each phase started a fresh FastAPI process. The original writer created real SQLite receipts through POST. Both revisions opened the same fixture database and immutable Parquet files. The verifier matched each original SQLite payload BLOB to the bytes read before restart, checked its SHA-256 against the original receipt ID, and compared the full HTTP response canonically with the stored payload. The database retained each exact verdict and producer reference.

## Pinned price evidence

The genuine November 13, 2026 fixture imported the original Dukascopy M5 BID Parquet files via the public reviewed import. Their SHA-256 digests matched the catalog pins. Independently decoded OHLC matched the provider CSV and all rows in the selected window had nominal arrival. The public results matched the independent price oracle at 13:25:00, 13:30:00, 14:10:00 and 14:10:17 UTC. The 13:25:00 receipt aligned bearish. The later probes found `NOT_ALIGNED` and had empty V2 recovery witness lists.

| Genuine source | Dataset ID | Parquet SHA-256 |
|---|---|---|
| EURUSD | `ca01e9ac45346d7c9fdfdf4263085831a07735bc16158901c9a14187f77aaf4e` | `fa2af192aa86dc41bb20df51dffe95c29bc48cdf2fe38b643f699985c6b0e09d` |
| GBPUSD | `1cc6acc1b358b3b043438badc69f72413827a8cd5dad379de8e41dd9aaaecfd0` | `aed73f09d78bb3df29d3678e0ace1f00949a673a920ed22dcd6d23cb7a173d02` |

Two controlled Parquet captures on the same date used a 14:05 EURUSD candle with OHLC `16 / 16.5 / 15.5 / 16`. It closed nominally at 14:10:00 UTC and arrived at 14:10:17 UTC. The second capture delivered the quiet 14:05 GBPUSD candle at that same instant. Independent Parquet decoding matched the fixture rows and their source hashes. Both captures had a bearish alignment at 14:05 with an already confirmed SMT and an active reclaimed CRT high.

| Probe in UTC | Verdict in both builds | Coverage/recovery in the corrected build |
|---|---|---|
| 14:05:00 and 14:09:59 | `ALIGNED`, first available at 14:05:00 | Empty recovery proof |
| 14:10:00 and 14:10:16 | `INCOMPLETE`, `MISSING_OPEN_CANDLES` | Missing interval [14:05:00, 14:10:00) on each delayed lane. Empty recovery proof |
| 14:10:17 and 14:10:18 | `ALIGNED`, first available at 14:10:17 | One EURUSD witness in the quiet-EURUSD case. EURUSD and GBPUSD witnesses in symbol order in the simultaneous case |

CRT and SMT family IDs, revision IDs and publication clocks stayed the same throughout both controls. The original receipt advanced its first availability to 14:10:17 without including a causal proof of the quiet EURUSD row. Each corrected witness records the symbol, row open, nominal close, arrival, availability basis, source ID and checksum-pinned partitions. The verifier compared the full witness against the independently decoded row and its pin.

## Reopen and discovery

On the corrected process, the list returned two original receipts per capture from SQLite and excluded the later one when queried at the earlier cutoff. After saving each corrected recovery receipt and restarting again, one-entry keyset pagination returned both versions at 14:10:17 and the earlier pin exactly once. Both versions opened by their own original ID.

| Capture | Cutoff in UTC | Rule version | Receipt ID and raw payload SHA-256 | Payload bytes |
|---|---|---|---|---:|
| genuine | 13:25:00 | 1 | `fc5627b440c2fff1f9f90d86738b6a5250506e06eeb1acb35841fcd735b1fe89` | 43,498 |
| genuine | 14:10:00 | 1 | `7ab118f42e53b2762ce81fd140ff060b5aac1154d7e77209659558cccc152c03` | 38,655 |
| quiet-eurusd | 14:05:00 | 1 | `8ab3eec522deaa4c1177bafc235dc780e25cde6424c4469b8c8daa3bfcfcf86e` | 32,386 |
| quiet-eurusd | 14:10:17 | 1 | `2d78e30551c1b6637ee5e21a76ca1fb5e0a2cb62c5935d2ccb379d0ea42217a5` | 33,045 |
| quiet-eurusd | 14:10:17 | 2 | `46e68697d76948dcf05a619daa645511c204fe4a35ce6fb1fd7365f8c2ae37bd` | 33,455 |
| both | 14:05:00 | 1 | `a0c0ce59d94ce2b4880dfc57baca70ab413f02b5220f6c97b5ec48e0bf60cd13` | 32,386 |
| both | 14:10:17 | 1 | `1b21c1ffa4e8c13c588aa8a3408a34b6d06711d9e35760c729e97bc90cd359c9` | 33,045 |
| both | 14:10:17 | 2 | `f2eadc24ef16d4684d20a9807e6ef7a1a1b2d586717103beb707a4fc579b14aa` | 33,841 |

## Application work and latency

The benchmark used the same genuine EURUSD/GBPUSD session, two stored receipts, and the same exact 13:25 receipt on both builds. Cold samples sent the first measured request in a fresh process and excluded process startup. Warm samples used an already-running process. The Windows host was running sibling oracle and focused checks, and the OS file cache was uncontrolled. Timings describe this run and do not establish a latency effect from the change.

| Operation | Samples per build | Original median / p95 ms | Corrected median / p95 ms | SELECTs per trial, original / corrected | Parquet opens / decoded rows / verified bytes per trial |
|---|---:|---:|---:|---:|---:|
| Cold saved-receipt list | 4 | N/A, HTTP 404 | 52.381 / 59.419 | N/A / 3 | 0 / 0 / 0 on corrected |
| Warm saved-receipt list | 30 | N/A, HTTP 404 | 9.491 / 57.382 | N/A / 3 | 0 / 0 / 0 on corrected |
| Warm replay-session list | 30 | 4.416 / 5.233 | 4.349 / 4.928 | 1 / 1 | 0 / 0 / 0 |
| Cold exact receipt reopen | 4 | 355.011 / 409.402 | 342.373 / 348.183 | 5 / 5 | 2 / 528 / 20,818 |
| Warm exact receipt reopen | 30 | 19.284 / 21.437 | 21.095 / 26.103 | 5–7 / 5 | 2 / 528 / 20,818 |
| Cold session checkpoint and exact receipt reopen | 4 | 359.071 / 377.642 | 360.648 / 365.195 | 7 / 7 | 2 / 528 / 20,818 |
| Warm session checkpoint and exact receipt reopen | 30 | 30.306 / 60.197 | 31.391 / 35.447 | 7–9 / 7–9 | 2 / 528 / 20,818 |

## Reproduce

Run the verifier from the corrected checkout with the genuine files available in `storage`. Each fixture uses disposable storage. The JSON report includes every sampled receipt summary, both source pins, the original payload hashes and all per-request work counters.

```powershell
git worktree add --detach ../TradeForge-confluence-original 7cdacaf4c10bdd1073128c0a4d10b2e34be7c7f6
.venv/Scripts/python.exe -m scripts.verify_t10_confluence_correction --baseline ../TradeForge-confluence-original --candidate . --source-storage storage --cold-samples 4 --warm-samples 30 --output storage/confluence-correction-api.json
```
