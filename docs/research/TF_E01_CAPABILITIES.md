# TF-E01: broker-neutral evidence contracts

This is an offline, unwired contract lane. It does not add a terminal adapter, routes,
SDK imports, credentials, order submission, cancellation, or replacement. The disabled
adapter is disconnected and reports no account, instrument, or events. The deterministic
fake lives in the test suite; it reveals an already-recorded acknowledgement, partial
fill, and final fill, with stable IDs across independent instances and bounded cursor pages.

The immutable Python contracts use schema version 1 and caller-supplied opaque stable
IDs for individual evidence records. Order status and account mode are enumerated.
The pre-trade evaluator performs deterministic demo-mode, currency, instrument and
exact price/quantity-grid checks, plus freshness, market hours, spread, deviation,
exposure, loss, active-order, disconnect, and emergency-disable checks. A pass is evidence
only: both capabilities and decisions expose a routing field fixed to false. Evidence
and policy are injected; this code neither obtains market hours/currency data nor attests
the caller's values or arbitrates them across processes. It never promotes recorded
observations or synthetic fixtures to broker execution authority.

Run through the ordinary CI pytest discovery:

```text
python -m pytest -q tests/test_broker_contracts.py
```

A future externally sourced adapter needs a separate transport, broker evidence policy,
validated account snapshot and instrument contracts, and explicit authorization before
any routing capability can be considered.
