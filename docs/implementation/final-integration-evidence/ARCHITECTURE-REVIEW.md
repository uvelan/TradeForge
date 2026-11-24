# Architecture review

- The verified baseline is `c3e4ae28fe152530e2b4fc3c4c0eec826737fbf0`. The local Windows terminal, FastAPI APIs, SQLite ledgers, and immutable Parquet storage remain the system of record for research.
- Offline management is a consumer of verified persisted entries and checksum-pinned synthetic quotes. New policies have distinct immutable result identities. Fixed and break-even payloads are not rewritten. The partial path records a normalized fraction and a separate residual terminal close; it does not calculate broker quantity or cash P&L.
- The existing public historical cutoff bound and recorded readiness remain unchanged. No trading API, live connection, order submission, or broker credential is added.
- Recovery verification uses isolated synthetic SQLite/Parquet roots and online SQLite backups; it proves a known fixture can be restored, not that production backups span arbitrary concurrent writers. Catalog duplicate verification belongs within the existing serialized write boundary and must leave corrupt and conflicting artifacts intact for diagnosis.
- The baseline already supports bounded multi-partition tick replay and server monitor polling. Pending improvements include streaming scale/calendar acceptance and durable fenced catch-up. Canonical writer cutover requires independent parity and explicit sign-off. No live or optional-provider assumptions are part of this integration.

Dependency arcs and acceptance criteria: [BACKLOG.json](BACKLOG.json). Source: [roadmap](../../research/08_IMPLEMENTATION_ROADMAP.md), [migration plan](../../research/07_MIGRATION_PLAN.md), and [decision log](../../research/09_DECISION_LOG.md).
