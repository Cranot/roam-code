"""Per-command read/write lock-mode registry for the v12.2 daemon scaffold.

Under the daemon (``roam daemon start``), every command takes a lock at
the boundary of the daemon's coordinator before its DB connection opens.
Modes:

* ``read``       — shared lock, N concurrent readers OK
* ``write``      — single writer, blocks new readers from starting
* ``exclusive``  — drains all readers then blocks; for migrations / init

The registry is the source of truth. ``DEFAULT_MODE = "exclusive"`` keeps
us safe — unknown commands escalate rather than silently corrupt.

Initial seed covers 5 pilot commands. Migration is incremental;
the audit env var ``ROAM_DAEMON_AUDIT=1`` checks per-command DB
``readonly=`` flags match the registered mode.

Companion: ``src/roam/runtime/lockmgr.py`` (RWLock + drain semantics).
"""

from __future__ import annotations

from typing import Literal

LockMode = Literal["read", "write", "exclusive"]
DEFAULT_MODE: LockMode = "exclusive"


# Phase 1 pilots — chosen to exercise every mode + the read/write split.
LOCK_MODE: dict[str, LockMode] = {
    # Pilot reads
    "preflight": "read",
    "retrieve": "read",
    "critique": "read",
    "context": "read",
    "uses": "read",
    # Pilot writes
    "index": "exclusive",  # full re-index — drain everything
    "init": "exclusive",  # schema bootstrap
    "clones": "write",  # writes clone_pairs / clone_clusters
    "ingest-trace": "write",  # writes runtime_stats
    "annotate": "write",  # writes annotations
}


def lookup_mode(command_name: str) -> LockMode:
    """Return the registered lock mode for *command_name* (or default)."""
    return LOCK_MODE.get(command_name, DEFAULT_MODE)
