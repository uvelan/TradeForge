# Historical CRT and SMT confluence

## Profile pinned before inspecting results

`CRT_SMT_M5_V1` uses EURUSD for the H1 CRT reference and EURUSD/GBPUSD for positive-correlation exact M5 SMT. Both lanes require saved Dukascopy BID M5 candles with verified catalog pins. Pane order is a presentation choice. The genuine acceptance window is 13 November 2026, 12:00–16:00 UTC, with no earlier input; the comparison uses modeled M5 close unless the capture supplies availability.

At each cut, select the visible CRT family with the latest H1 reference start and the SMT family with the latest first candidate availability. Break inter-family ties with the lexicographically smallest family ID. Within the selected family use the last canonical revision visible at the cutoff, including equal-time transitions. A newer family displaces the older even if it is pending. Display each producer's own family ID, revision, time and evidence.

A reclaimed low requires bullish SMT; a reclaimed high requires bearish SMT. Expansion retains the original sweep direction. CRT expiry, invalidation, ambiguous chronology or reaching the opposite boundary ends eligibility. SMT must be currently confirmed under period 2, zero alignment bars, zero tolerance, one confirmation bar and 64 expiry bars. A candidate, an ended confirmation, an unresolved open slot, or a future peer arrival cannot establish agreement. A confirmed opposite direction is affirmative disagreement. Lack of evidence must remain visible. Decisions are research observations with no execution authority.
