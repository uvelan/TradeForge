# Management ordering and legacy result coexistence

`legacy-unsupported.json` contains the three exact canonical payloads read from
`offline_first_exit_results` produced by the ordinary service at
`b0a7c47a74a1d5f710415b63b145953e4ba13dd0` (#26). For each case, the capture
uses the `position` fixture from `test_offline_first_exit.py`, freezes
`strategy_routes.uuid4` using `UUID(int=next(count(uuid_seed)))` with a persistent
counter starting at the listed seed, publishes a LONG STOP intent with the listed
management overrides, and validates the listed tape and cutoff. No payload was
obtained by calling the new policy implementation.

The test regenerates the exact matching entry IDs, seeds the old payloads in a
temporary archive, and verifies the new versioned result coexists with a
byte-identical old row. It also covers second same-millisecond trail updates,
terminal hit/miss ties after a verified reduction, and a duplicate deadline
quote preceding a later price stop, for both directions and two input orders.

```text
python -m pytest -q research_backend/tests/test_offline_management_ordering.py
```
