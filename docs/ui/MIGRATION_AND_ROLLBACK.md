# Migration and rollback

SQLite initialization adds drawing scene versions, replay sessions, checkpoints, saved backtest runs, chart snapshots and journal context. These tables leave original trade, journal and market archive records readable. Published drawings, replay captures, runs and chart attachments are immutable. Checkpoints hold user progress separately; journal revisions append.

Do not remove SQLite sidecar data or new tables to roll back presentation. Back up the database and both storage roots consistently. Existing horizontal levels use the same legacy browser keys. Display presets remain browser-local and validated. Market-data format and Parquet paths remain unchanged.
