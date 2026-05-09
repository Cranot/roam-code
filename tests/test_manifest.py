"""Tests for the index manifest subsystem.

Covers:
- Schema migration creates ``index_manifest`` table.
- ``collect_manifest`` returns the expected shape with correct types.
- Round-trip: write a manifest then read it back via ``latest_manifest``;
  JSON columns come back decoded.
- ``manifest_diff`` flags drift fields and stays empty on identical input.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, index_in_process

from roam.db.connection import USER_VERSION, ensure_schema
from roam.index.manifest import (
    collect_manifest,
    latest_manifest,
    manifest_diff,
    write_manifest,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fresh_db(tmp_path: Path) -> sqlite3.Connection:
    """Return a brand-new SQLite connection with the full roam schema applied."""
    db_path = tmp_path / "manifest_test.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    return conn


@pytest.fixture
def manifest_project(tmp_path):
    """A small git-tracked project we can index end-to-end."""
    proj = tmp_path / "manifest_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "app.py").write_text("def hello():\n    return 'world'\n")
    git_init(proj)
    return proj


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def test_manifest_table_exists(tmp_path):
    """A fresh DB exposes ``index_manifest`` for query."""
    conn = _fresh_db(tmp_path)
    try:
        # Table is queryable
        rows = conn.execute("SELECT * FROM index_manifest").fetchall()
        assert rows == []

        # The expected columns are present
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(index_manifest)")}
        expected = {
            "id",
            "indexed_at",
            "roam_version",
            "schema_version",
            "parser_versions",
            "grammar_versions",
            "config_hash",
            "git_head",
            "git_dirty_hash",
            "enabled_extras",
            "index_profile",
            "notes",
        }
        missing = expected - cols
        assert not missing, f"index_manifest missing columns: {missing}"

        # Bumped user_version is mirrored into PRAGMA
        pragma = conn.execute("PRAGMA user_version").fetchone()[0]
        assert int(pragma) == int(USER_VERSION) >= 1

        # Index on indexed_at exists
        idx_rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='index_manifest'"
        ).fetchall()
        idx_names = {r[0] for r in idx_rows}
        assert "idx_index_manifest_at" in idx_names
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# collect_manifest
# ---------------------------------------------------------------------------


def test_collect_manifest_shape(tmp_path, manifest_project):
    """The dict from ``collect_manifest`` has the documented keys + types."""
    conn = _fresh_db(tmp_path)
    try:
        manifest = collect_manifest(manifest_project, profile="all", conn=conn)

        assert isinstance(manifest, dict)

        assert isinstance(manifest["indexed_at"], int)
        assert manifest["indexed_at"] > 0

        assert isinstance(manifest["roam_version"], str)
        assert manifest["roam_version"]

        assert isinstance(manifest["schema_version"], int)
        assert manifest["schema_version"] >= 1

        assert isinstance(manifest["parser_versions"], dict)
        # tree-sitter is a hard dep so it should always be present here
        assert "tree_sitter" in manifest["parser_versions"]

        # grammar_versions is currently None until we have a real source
        assert manifest["grammar_versions"] is None or isinstance(manifest["grammar_versions"], dict)

        assert isinstance(manifest["config_hash"], str)
        assert len(manifest["config_hash"]) == 64  # sha256 hex

        # git_head is set for a freshly committed project
        assert manifest["git_head"] is None or isinstance(manifest["git_head"], str)
        assert manifest["git_dirty_hash"] is None or isinstance(manifest["git_dirty_hash"], str)

        assert isinstance(manifest["enabled_extras"], list)
        assert all(isinstance(x, str) for x in manifest["enabled_extras"])

        assert manifest["index_profile"] == "all"
        assert manifest["notes"] is None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Round-trip: write_manifest + latest_manifest
# ---------------------------------------------------------------------------


def test_write_then_latest_roundtrip(tmp_path):
    """A written manifest is read back with JSON columns decoded to Python types."""
    conn = _fresh_db(tmp_path)
    try:
        synthetic = {
            "indexed_at": 1700000000,
            "roam_version": "12.99.0",
            "schema_version": int(USER_VERSION),
            "parser_versions": {"tree_sitter": "0.25.2", "tree_sitter_language_pack": "1.6.2"},
            "grammar_versions": None,
            "config_hash": "a" * 64,
            "git_head": "deadbeef" + "0" * 32,
            "git_dirty_hash": None,
            "enabled_extras": ["networkx", "fastmcp"],
            "index_profile": "all",
            "notes": "synthetic test row",
        }

        row_id = write_manifest(conn, synthetic)
        assert isinstance(row_id, int) and row_id > 0

        latest = latest_manifest(conn)
        assert latest is not None

        # JSON-decoded fields come back as native Python structures
        assert latest["parser_versions"] == synthetic["parser_versions"]
        assert latest["grammar_versions"] is None
        assert latest["enabled_extras"] == synthetic["enabled_extras"]

        # Other scalar fields preserved
        assert latest["roam_version"] == "12.99.0"
        assert latest["schema_version"] == int(USER_VERSION)
        assert latest["config_hash"] == "a" * 64
        assert latest["git_head"] == synthetic["git_head"]
        assert latest["git_dirty_hash"] is None
        assert latest["index_profile"] == "all"
        assert latest["notes"] == "synthetic test row"
        assert latest["id"] == row_id

        # Writing a second row returns the newest one
        synthetic2 = {**synthetic, "indexed_at": 1700001000, "roam_version": "12.99.1"}
        row_id2 = write_manifest(conn, synthetic2)
        latest2 = latest_manifest(conn)
        assert latest2 is not None
        assert latest2["id"] == row_id2
        assert latest2["roam_version"] == "12.99.1"
    finally:
        conn.close()


def test_latest_manifest_returns_none_when_empty(tmp_path):
    """Empty table → None, no exceptions."""
    conn = _fresh_db(tmp_path)
    try:
        assert latest_manifest(conn) is None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# manifest_diff
# ---------------------------------------------------------------------------


def test_manifest_diff_detects_version_change():
    """Drift in roam_version (or any tracked field) shows up in the diff."""
    prev = {
        "roam_version": "12.0.0",
        "schema_version": 1,
        "parser_versions": {"tree_sitter": "0.25.0"},
        "grammar_versions": None,
        "config_hash": "x" * 64,
        "git_head": "abc",
        "git_dirty_hash": None,
        "enabled_extras": ["networkx"],
        "index_profile": "all",
    }
    current = {
        **prev,
        "roam_version": "12.1.0",
        "parser_versions": {"tree_sitter": "0.25.2"},
    }
    diff = manifest_diff(prev, current)
    assert set(diff.keys()) == {"roam_version", "parser_versions"}
    assert diff["roam_version"] == ("12.0.0", "12.1.0")
    assert diff["parser_versions"] == ({"tree_sitter": "0.25.0"}, {"tree_sitter": "0.25.2"})


def test_manifest_diff_clean_no_change():
    """Identical manifests produce an empty diff."""
    snap = {
        "roam_version": "12.0.0",
        "schema_version": 1,
        "parser_versions": {"tree_sitter": "0.25.2"},
        "grammar_versions": None,
        "config_hash": "z" * 64,
        "git_head": None,
        "git_dirty_hash": None,
        "enabled_extras": [],
        "index_profile": "all",
    }
    assert manifest_diff(snap, snap) == {}
    # Differences in non-drift fields are ignored
    other = {**snap, "id": 99, "indexed_at": 1234567890, "notes": "ignored"}
    assert manifest_diff(snap, other) == {}


def test_manifest_diff_handles_missing_inputs():
    """Empty inputs short-circuit to {} rather than crashing."""
    assert manifest_diff({}, {"roam_version": "12.0.0"}) == {}
    assert manifest_diff({"roam_version": "12.0.0"}, {}) == {}


# ---------------------------------------------------------------------------
# End-to-end: indexer writes a manifest row
# ---------------------------------------------------------------------------


def test_indexer_writes_manifest_row(manifest_project):
    """A real `roam index` run leaves at least one ``index_manifest`` row."""
    out, rc = index_in_process(manifest_project)
    assert rc == 0, f"roam index failed:\n{out}"

    from roam.db.connection import open_db

    with open_db(readonly=True, project_root=manifest_project) as conn:
        rows = conn.execute("SELECT * FROM index_manifest").fetchall()
        assert len(rows) >= 1
        row = rows[-1]
        # roam_version is whatever's installed; just check it's non-empty.
        assert row["roam_version"]
        # Parser versions are JSON-encoded.
        parsers = json.loads(row["parser_versions"])
        assert "tree_sitter" in parsers
        assert int(row["schema_version"]) >= 1
