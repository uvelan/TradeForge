# API and state contracts

Existing /research/api market, trade, journal and backtest routes remain. Signal reads return latest lifecycle pages or exact recorded revisions. Evidence keeps its verified feeds and archive checksums and linked trade versions can be read by event key.

- /drawings uses a validated source:symbol:timeframe scope and a version-checked scene update. Each immutable version stores ordered UTC anchors and decimal price strings. A stale writer gets HTTP 409.
- /replay/sessions captures closed panes on the server and uses a content hash for immutable snapshots. Bounded checkpoints update a separate SQLite row. Captured signal replay is scoped by exact event key and status.
- /backtest/runs accepts the existing strategy parameters and makes a hash-addressed record of the candle input, rule/cost settings, result and file lineage. Quote-path reads re-verify the original tick partition and bound events by both timestamp and source order, including matching quotes at the same millisecond.
- /chart-snapshots validates a bounded PNG supplied by the chart and preserves source context. User-chart capture authority is distinct from the recorded SMT candles. Plan creation validates snapshot instrument/timeframe and AI export preserves the original PNG.
- Plan revisions can require the previously loaded version and no-trade adds an auditable finalization event. Journal context stores tags, exact plan and signal links, chart capture IDs and supersession. It is append-only and does not mutate old entries.
- /workspace/search handles bounded search pages and opens exact or available resource records. No browser search result grants execution authority.

Data completeness is assessed independently of checksum verification. Live execution stays disabled.
