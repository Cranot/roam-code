"""Lockstep discipline for schema.py and USER_VERSION.

Schema-hash discipline. Hashes both ``src/roam/db/schema.py`` AND the
FTS5 virtual-table column definition (which lives in connection.py as
``_FTS5_SCHEMA_COLUMNS``).

Widened in W97 after W94 found that FTS5 changes silently slip through
the schema.py-only hash. Now any change to either surface forces a
coordinated USER_VERSION bump or this test fails.

If the combined hash drifts (i.e. either the schema file or the FTS5
column tuple has been edited), the test requires ``USER_VERSION`` in
``connection.py`` to also have drifted from the snapshotted value
below. The two MUST move together: editing the schema without bumping
the contract version is a silent corruption hazard for downstream
consumers (manifest writer, bundle import, ``roam doctor`` drift
checks).

Workflow when this test fails:

1. Edit ``src/roam/db/schema.py`` (e.g. add a column) and/or
   ``_FTS5_SCHEMA_COLUMNS`` in ``src/roam/db/connection.py``.
2. Edit ``src/roam/db/connection.py`` and bump ``USER_VERSION``.
3. Recompute the schema hash and update ``_SNAPSHOT_SCHEMA_HASH``
   and ``_SNAPSHOT_USER_VERSION`` below. Both updates land in the
   same commit.

Companion to ``tests/test_db_user_version.py`` which enforces the
``USER_VERSION > 1`` floor and the migration-ledger invariants.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from roam.db.connection import _FTS5_SCHEMA_COLUMNS, USER_VERSION

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCHEMA_PATH = _REPO_ROOT / "src" / "roam" / "db" / "schema.py"

# Snapshot captured at commit time. Update both values together when
# schema.py or _FTS5_SCHEMA_COLUMNS legitimately changes. See module
# docstring for workflow.
_SNAPSHOT_SCHEMA_HASH = "6bde165795461cb1"
_SNAPSHOT_USER_VERSION = 18


def _current_schema_hash() -> str:
    """Compute the combined schema hash.

    Inputs:

    * Full contents of ``src/roam/db/schema.py``
    * The FTS5 virtual-table column tuple (``_FTS5_SCHEMA_COLUMNS``),
      which lives in ``connection.py`` as a separate module constant
      but is part of the on-disk schema contract.

    The FTS5 tuple is serialised as ``FTS5:col1,col2,...`` and
    appended (after a newline) to the schema-file bytes before
    hashing. The ``FTS5:`` prefix anchors the contribution so a
    trailing comment in schema.py can't accidentally mimic it.
    """
    schema_bytes = _SCHEMA_PATH.read_bytes()
    fts5_signature = "FTS5:" + ",".join(_FTS5_SCHEMA_COLUMNS)
    combined = schema_bytes + b"\n" + fts5_signature.encode("utf-8")
    return hashlib.sha256(combined).hexdigest()[:16]


def test_schema_file_exists():
    """Sanity check: the path we're hashing actually resolves."""
    assert _SCHEMA_PATH.is_file(), (
        f"Expected schema file at {_SCHEMA_PATH}, but it does not exist. "
        "Has the schema been moved? Update _SCHEMA_PATH in this test."
    )


def test_user_version_bumps_when_schema_changes():
    """If the schema surface drifts from the snapshot, USER_VERSION must drift too.

    This is the lockstep discipline. Either:

    * schema.py AND _FTS5_SCHEMA_COLUMNS are unchanged from snapshot
      → USER_VERSION may stay at the snapshot;
    * either surface changed → USER_VERSION MUST differ from the snapshot.

    A snapshot mismatch with a still-snapshot USER_VERSION is the
    failure mode: someone edited the schema (schema.py or the FTS5
    column tuple) and forgot the version bump.
    """
    current_hash = _current_schema_hash()
    if current_hash == _SNAPSHOT_SCHEMA_HASH:
        # Schema is unchanged from snapshot. Nothing further to enforce.
        return

    assert USER_VERSION != _SNAPSHOT_USER_VERSION, (
        f"schema hash changed ({_SNAPSHOT_SCHEMA_HASH} -> {current_hash}) "
        f"but USER_VERSION is still {USER_VERSION}. The hash covers both "
        f"src/roam/db/schema.py and _FTS5_SCHEMA_COLUMNS in connection.py. "
        f"Bump USER_VERSION in src/roam/db/connection.py and update the "
        f"snapshot values (_SNAPSHOT_SCHEMA_HASH, _SNAPSHOT_USER_VERSION) "
        f"in this test."
    )
