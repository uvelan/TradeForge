# Domain model

A dataset version identifies one instrument, timeframe, price basis, provider and feed, coverage interval, schema, and immutable candle series. Each candle has a UTC opening time, availability time, and integer OHLC values.

A future Trade Decision records a hypothesis, including the explicit NO TRADE outcome. Its immutable plan versions carry Context, Observation, Evidence, Hypothesis, Setup, Trigger, Invalidation, Risk, Execution, Management, and Review. Execution events advance a separate lifecycle. AI rounds bind to an exact plan version and package. Journal entries are append-only.
