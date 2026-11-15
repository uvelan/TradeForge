# Trade identity policy

Campaigns group related decisions and episodes by explicit links. A decision owns plan versions. Broker orders and executions retain scoped broker identities and provenance. Fills are the accounting authority.

In netting mode, partition by account and symbol and open an episode when signed exposure leaves zero. A partial close or scale-in remains in the episode. Close at zero. A reversal splits the fill into closing and opening allocations with reconciled quantity and costs. The next opening starts a new episode regardless of elapsed time.

In hedging mode, track exposure by account and broker position identity. Preserve distinct opposing positions. Duplicate fills are idempotent and broker corrections are recorded explicitly, with affected projections rebuilt from the corrected event stream.
