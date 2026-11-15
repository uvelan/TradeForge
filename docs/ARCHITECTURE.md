# Architecture

One FastAPI application owns persistence and access to market providers. The React terminal uses Vite's same-origin API proxy over loopback. Trusted Host and Origin checks restrict supported local entry points.

SQLAlchemy manages SQLite transactions and Alembic upgrades the schema on startup. PyArrow publishes Zstandard-compressed candle files and Polars scans them. The catalog serializes publication under the SQLite writer lock. A crash between the atomic file replacement and database commit may leave an orphan file that a retry can replace. Automated reconciliation and an external backup workflow remain pending.

The initial simulator runs synchronous bounded requests. Durable jobs and isolated workers precede bulk ingestion. The MT5 adapter serializes local terminal access per application instance. The deployment uses one backend process.
