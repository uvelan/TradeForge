# Market data

Schema v1 stores time and availability as UTC timestamps and OHLC prices as int64 using the dataset's price scale. A requested range is half-open and aligns to timeframe boundaries. The simulator uses close-time availability and dense coverage. It permits up to 10,000 bars per bounded synchronous dataset. Reads verify the checksum and may filter by UTC availability.

MT5 imports M1–M30 bid candles under their broker server feed. Sparse ranges retain their original timestamps and are flagged whenever any requested slot is absent, including at either endpoint. Market closures and missing history remain indistinguishable until broker calendars are modeled. Availability uses the nominal close time for display. Historical research will need an explicit policy for delayed arrivals and broker-specific session boundaries. [MT5 range and UTC contract](https://www.mql5.com/en/docs/python_metatrader5/mt5copyratesrange_py)

Tick storage will preserve timestamps, bid and ask price units, provider flags, source order where known, and ingestion time. Keep providers and feeds separate.

The Dukascopy historical export interface is documented. Direct downloader implementation requires verified format, range limits, usage terms and real data checks. [Dukascopy Historical Data Export](https://www.dukascopy.com/swiss/english/marketwatch/historical/)
