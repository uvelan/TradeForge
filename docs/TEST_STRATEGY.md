# Test strategy

Pytest exercises migrations via application startup, the simulation API, concurrent duplicate imports, rollback and retry after a publication error, restart persistence, checksum failures, cutoff filtering, candle validation, origin rejection, and the MT5 adapter using a deterministic terminal double. Vitest checks chart conversion. Playwright generates and reopens a dataset through the running UI and checks that its canvas renders.

Later phases add the required adversarial suites for ticks, market calendars, resume/recovery, execution identity, risk, backtesting, reviews and backup integrity. Live provider acceptance remains a separate check.
