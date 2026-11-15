# Implementation plan

TradeForge starts from an empty Windows workspace. Use a loopback FastAPI process, a React client, SQLite for metadata, and Parquet for historical series.

1. Establish the startup scripts, migration, health API, and strict TypeScript shell. Verify a fresh database and a repeat startup.
2. Add isolated feed adapters, canonical candle validation, and a persistent dataset catalog. Verify repeatable simulated EURUSD ingestion and restart persistence.
3. Render saved candles in an interactive terminal with explicit feed identity and Asia/Kolkata display.
4. Add immutable plans and deterministic risk assessment, then research, replay, manual AI review, broker reconciliation, and learning in that order.

Full workflow acceptance depends on planning, research, review, and learning, so validate the data-to-chart slice independently before running the full workflow. A direct Dukascopy downloader requires verified provider access and format documentation. Keep synthetic fixtures under a simulator identity. Broker observations and their time of availability are separate from candle opening times. Review export and replay must filter by availability.
