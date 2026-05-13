"""Multi-Agent Lease System (R21 substrate).

A *lease* is one agent's claim on a set of files OR a graph partition,
stored as a JSON document under ``.roam/leases/<lease_id>.json``. The
lease is purely *advisory*: roam-code does NOT auto-block command
dispatch when an agent edits a file covered by another agent's lease.
The substrate exists so higher-level tooling (``roam orchestrate``,
``roam preflight``, ``roam pr-bundle``) can opt in to conflict checks.

Re-exports the public API from :mod:`roam.leases.store` so callers can
``from roam.leases import claim_lease, ...`` directly.
"""

from __future__ import annotations

from roam.leases.store import (
    LEASES_DIR_NAME,
    LEASES_SUBDIR,
    Lease,
    claim_lease,
    find_conflict,
    gc_expired_leases,
    leases_root,
    list_leases,
    read_lease,
    release_lease,
)

__all__ = [
    "LEASES_DIR_NAME",
    "LEASES_SUBDIR",
    "Lease",
    "claim_lease",
    "find_conflict",
    "gc_expired_leases",
    "leases_root",
    "list_leases",
    "read_lease",
    "release_lease",
]
