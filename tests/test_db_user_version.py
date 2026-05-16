"""Discipline tests for the SQLite USER_VERSION contract.

The schema-version field on the DB (PRAGMA user_version) is mirrored into
``index_manifest.schema_version`` on every index run, so consumers
(manifest writer, bundle import, drift checks in ``roam doctor``) can
detect when an indexed DB was built under a different schema generation.

These tests catch two failure modes:

1. ``USER_VERSION`` left at 1 forever (the bug the audit flagged) — every
   index_manifest row claims schema_version=1 regardless of reality.
2. Schema migrations added without bumping ``USER_VERSION`` — manifest
   reports stale version, downstream drift detection silently misses
   real schema deltas.

The discipline: when you add or remove a migration op in ``ensure_schema``,
you must bump both ``USER_VERSION`` (semantic) and ``MIGRATION_OPS_COUNT``
(this file's pin). The test fails on either-side drift, forcing a
deliberate review.
"""

from __future__ import annotations

from roam.db.connection import MIGRATION_OPS_COUNT, USER_VERSION


def test_user_version_is_above_one():
    """USER_VERSION must reflect the migrations that have landed since v1.

    Static value of 1 is the bug — every column-add since the original
    schema still claims schema_version=1.
    """
    assert USER_VERSION > 1, (
        f"USER_VERSION is {USER_VERSION}; this is the original bug — "
        "static value, never bumped despite ~50 migration ops landing "
        "since the initial schema. Bump it to reflect the current era."
    )


def test_pragma_user_version_matches_constant_on_fresh_db(tmp_path):
    """A freshly-initialised DB carries PRAGMA user_version == USER_VERSION."""
    from roam.db.connection import open_db

    proj = tmp_path / "fresh_proj"
    proj.mkdir()

    with open_db(readonly=False, project_root=proj) as conn:
        row = conn.execute("PRAGMA user_version").fetchone()
        live = int(row[0]) if row else 0

    assert live == USER_VERSION, (
        f"PRAGMA user_version on fresh DB = {live}, but USER_VERSION = {USER_VERSION}. "
        f"ensure_schema() should bump the pragma to match the constant."
    )


def test_migration_ops_count_matches_ledger():
    """``MIGRATION_OPS_COUNT`` is derived from ``_MIGRATIONS``; verify
    that derivation hasn't been replaced with a hand-bumped constant.

    R9.A2 moved migrations into a numbered ledger (``_MIGRATIONS``)
    so adding/removing an entry auto-updates the count without manual
    bookkeeping. This test catches a regression where a contributor
    re-introduces a hand-pinned constant.
    """
    from roam.db.connection import _MIGRATIONS

    assert MIGRATION_OPS_COUNT == len(_MIGRATIONS), (
        f"MIGRATION_OPS_COUNT={MIGRATION_OPS_COUNT} but len(_MIGRATIONS)="
        f"{len(_MIGRATIONS)} — these MUST be the same expression "
        f"(`MIGRATION_OPS_COUNT = len(_MIGRATIONS)`)."
    )


def test_migration_seqs_are_unique_and_monotonic():
    """Sequence numbers in the ledger must be strictly increasing
    (so the ledger order = the apply order, no ambiguity).

    Catches: copy-paste mistakes (two entries with the same seq),
    out-of-order edits (renumbered some but not others), and
    accidentally-shuffled merge conflicts.
    """
    from roam.db.connection import _MIGRATIONS

    seqs = [seq for seq, _, _ in _MIGRATIONS]
    assert seqs == sorted(seqs), f"_MIGRATIONS seqs are not in increasing order: {seqs}"
    assert len(seqs) == len(set(seqs)), f"_MIGRATIONS contains duplicate seqs: {[s for s in seqs if seqs.count(s) > 1]}"


def test_migrations_are_idempotent_on_a_fresh_db(tmp_path):
    """Running ensure_schema twice on the same DB must be a no-op
    on the second call — every migration in the ledger is required
    to be idempotent. R9.A2 ledger discipline is what allows future
    partial-failure recovery (re-run from where we crashed) without
    side effects.
    """
    from roam.db.connection import open_db

    proj = tmp_path / "idempotent_proj"
    proj.mkdir()

    # First call — full schema build.
    with open_db(readonly=False, project_root=proj) as conn:
        v1 = int(conn.execute("PRAGMA user_version").fetchone()[0])

    # Second call — re-runs every migration. Should not raise.
    with open_db(readonly=False, project_root=proj) as conn:
        v2 = int(conn.execute("PRAGMA user_version").fetchone()[0])

    assert v1 == v2 == USER_VERSION, (
        f"user_version drifted across two ensure_schema runs: v1={v1}, v2={v2}, expected={USER_VERSION}"
    )
