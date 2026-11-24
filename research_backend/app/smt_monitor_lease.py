"""Short process-independent lease for the background SMT monitor."""
from dataclasses import dataclass
from time import time_ns
from uuid import uuid4

from .db import transaction

LEASE_NS = 30_000_000_000


@dataclass(frozen=True)
class MonitorLease:
    generation: int
    owner: str


def claim(generation: int) -> MonitorLease | None:
    lease = MonitorLease(generation, uuid4().hex)
    with transaction() as conn:
        conn.execute("BEGIN IMMEDIATE")
        now = time_ns()
        result = conn.execute(
            """INSERT INTO smt_monitor_lease (id, generation, owner, expires_ns)
               SELECT id, enabled_at, ?, ? FROM smt_monitor
               WHERE id=1 AND enabled_at=?
               ON CONFLICT(id) DO UPDATE SET generation=excluded.generation,
                   owner=excluded.owner, expires_ns=excluded.expires_ns
               WHERE smt_monitor_lease.expires_ns<=?
                  OR smt_monitor_lease.generation!=excluded.generation""",
            (lease.owner, now+LEASE_NS, generation, now),
        )
        return lease if result.rowcount else None


def owns(conn, lease: MonitorLease) -> bool:
    """Call under the same write lock that protects the monitored results."""
    return conn.execute(
        """SELECT 1 FROM smt_monitor_lease AS lease JOIN smt_monitor AS watch ON watch.id=lease.id
           WHERE lease.id=1 AND lease.generation=? AND lease.generation=watch.enabled_at
             AND lease.owner=? AND lease.expires_ns>?""",
        (lease.generation, lease.owner, time_ns()),
    ).fetchone() is not None


def finish(lease: MonitorLease) -> bool:
    with transaction() as conn:
        conn.execute("BEGIN IMMEDIATE")
        current = owns(conn, lease)
        conn.execute(
            "DELETE FROM smt_monitor_lease WHERE id=1 AND generation=? AND owner=?",
            (lease.generation, lease.owner),
        )
        return current
