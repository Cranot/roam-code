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

from pathlib import Path

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


def test_migration_ops_count_matches_source():
    """Pin the count of migration operations in ``ensure_schema``.

    Counted: ``_safe_alter(`` calls + ``CREATE INDEX IF NOT EXISTS`` +
    ``DROP INDEX IF EXISTS`` + ``_ensure_tfidf_cascade(`` +
    ``_ensure_fts5_table(``.

    When this count drifts, the contributor must:
      - Update ``MIGRATION_OPS_COUNT`` in ``connection.py`` to the new count
      - Bump ``USER_VERSION`` (so consumers see the new schema generation)
      - Confirm the new migration is idempotent (re-runs are safe)

    Why this matters: the manifest writer mirrors ``USER_VERSION`` into
    every ``index_manifest`` row. If schema changes ship without a bump,
    drift detection in ``roam doctor`` silently misses real changes.
    """
    src = (Path(__file__).resolve().parents[1] / "src" / "roam" / "db" / "connection.py").read_text(encoding="utf-8")
    lines = src.split("\n")

    # Locate the ensure_schema body — bounded by the next top-level ``def``.
    start = None
    end = len(lines)
    for i, ln in enumerate(lines):
        if ln.startswith("def ensure_schema"):
            start = i
        elif start is not None and ln.startswith("def "):
            end = i
            break
    assert start is not None, "could not locate ensure_schema function"

    body = "\n".join(lines[start:end])
    ops = (
        body.count("_safe_alter(")
        + body.count("CREATE INDEX IF NOT EXISTS")
        + body.count("DROP INDEX IF EXISTS")
        + body.count("_ensure_tfidf_cascade(")
        + body.count("_ensure_fts5_table(")
    )

    assert ops == MIGRATION_OPS_COUNT, (
        f"ensure_schema body has {ops} migration ops, but "
        f"MIGRATION_OPS_COUNT = {MIGRATION_OPS_COUNT}. "
        f"\n\nIf you intentionally added or removed a migration:\n"
        f"  1. Update MIGRATION_OPS_COUNT in src/roam/db/connection.py to {ops}\n"
        f"  2. Bump USER_VERSION so consumers see the new schema generation\n"
        f"  3. Confirm the migration is idempotent (re-runs are safe)\n"
    )
