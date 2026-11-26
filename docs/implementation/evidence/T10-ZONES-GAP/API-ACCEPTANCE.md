# Replay zone continuity API acceptance

The verifier creates isolated SQLite and Parquet fixtures. It calculates each file’s SHA-256, reads its rows with PyArrow, and derives the source ID from the partition ID and observed UTC open. It then creates a saved replay and inspects the public selected-zone receipt in wick and close mode on both panes.

| Case | Independent fixture and expected result | Comparison |
| --- | --- | --- |
| Missing open candle | EURUSD FVG forms at the 13:05 open and is available at 13:10 UTC. The 13:15 candle stays quiet, 13:20 is absent, and 13:25 resumes. GBPUSD order block follows the confirmed 13:30 BOS. The 13:35 candle stays quiet, 13:40 is absent, and 13:45 resumes. | [Ordinary missing opens](api-missing.json) |
| Contiguous | Restore the omitted quiet candle in each pane. The existing family remains active at the successor’s availability. | [Contiguous bars](api-contiguous.json) |
| Scheduled closure | Friday 21:55 UTC and Sunday 22:00 UTC bracket a complete scheduled forex closure. Both families remain active. | [Scheduled weekend](api-closure.json) |
| Closure and missing open candles | Friday 21:50 UTC and Sunday 22:05 UTC bracket the scheduled closure and one missing market-open candle on each side. The arrays contain the two separate open intervals. The two witnesses come from distinct Parquet files with different SHA-256 digests in each pane. | [Split-partition weekend gap](api-closure-missing.json) |
| Delayed availability | Each resumed candle has an archive-supplied arrival 17 seconds after its nominal close. The invalidation and its witnesses first appear at that arrival. | [Recorded arrival](api-delayed.json) |
| Unverifiable closure | A Saturday archive observation falls outside the declared schedule. The existing invalidation reason is retained and the receipt reports the two observations with unknown missing-open intervals. | [Out-of-schedule observation](api-unverifiable.json) |

In the ordinary gap fixtures, the selected revision remains active at 13:29:59 and 13:49:59 UTC respectively. At 13:30 and 13:50 it becomes revision 2, invalidated for missing open candles. The baseline contains the resumed candle as the revision observation. The fixed receipt identifies the preceding and resumed candles as gap witnesses on that same revision. Their available-at times and partition pins come from the archived rows.

The comparison retains the family and revision IDs and hashes every previously exposed selected-receipt field, role, and the full zone summary page at the terminal cutoff. The fixed digest removes only the additive continuity object and the two witness roles. The check also revisits the earlier cutoff after reading the terminal receipt and requires an exact match with the first read.

Run from a checkout containing the verifier. Use the repository’s pinned Python dependencies and set the paths to the reviewed baseline and fixed backend checkouts.

```powershell
$baselineRepo = 'K:/Projects/TradeForge-zone-gap-baseline'
$fixedRepo = (Get-Location).Path
$baseReceipt = Join-Path $env:TEMP 'zone-gaps-baseline.json'
$comparison = Join-Path $env:TEMP 'zone-gaps-comparison.json'

python -m scripts.verify_t10_zone_gaps --case missing --repo $baselineRepo --output $baseReceipt
python -m scripts.verify_t10_zone_gaps --case missing --repo $fixedRepo --phase fixed --baseline $baseReceipt --output $comparison
```

The case names are `missing`, `contiguous`, `closure`, `closure-missing`, `delayed`, and `unverifiable`. Use `--seed-only --storage <empty-directory> --output <manifest.json>` to create the ordinary archive for a browser run. Point `TRADEFORGE_STORAGE` at that same directory to reuse the pins across backend restarts.
