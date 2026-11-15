# Decisions

- Use pinned package versions resolved on Windows and retain npm and Python lockfiles.
- Use a separate synthetic feed identity and independent deterministic fixtures by timeframe.
- Store fixed-point candle prices in Parquet and convert to display numbers only in the chart.
- Include availability time in the candle contract before adding replay or review exports.
- Require a broker calendar policy before publishing MT5 candles into the dense simulator catalog.
