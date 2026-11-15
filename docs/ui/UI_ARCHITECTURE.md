# UI architecture

TradeForge retains React, Vite, Lightweight Charts, FastAPI, SQLite, Parquet and Polars. The app shell coordinates page context. SignalExplorer, ReplayControls, DataTrust, Drawings, ChartLink, PlanHistory, SessionIntelligence, WorkspacePresets, CommandPalette and SimulationEvidence own chart and workflow features. Drawing SVG-style geometry is rendered through one Lightweight Charts pane primitive. The chart-link controller publishes from the chart receiving input.

SQLite captures the actual replay and backtest inputs, persists exact linked planning/journal evidence, and separates drawing versions and mutable replay checkpoints from market data. The drawing and workspace API modules implement bounded local records. Existing UI preferences and legacy levels keep their keys.

See [the feature matrix](FEATURE_IMPLEMENTATION_MATRIX.md) for further acceptance, especially broker calendars and sizing, cross-partition quotes, and hosted accessibility/performance hardening.
