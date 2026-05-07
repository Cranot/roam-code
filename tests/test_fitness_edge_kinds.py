"""Tests for the edge_kinds filter on dependency rules.

Exercises _check_dependency_rule() directly using a lightweight
in-memory sqlite3 connection so no filesystem project or real index
is required.
"""

from __future__ import annotations

import sqlite3

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_conn() -> sqlite3.Connection:
    """Return an in-memory sqlite3 connection with the minimal schema used by
    _check_dependency_rule().  Uses row_factory=sqlite3.Row to match the real
    roam DB so r["kind"] etc. work identically."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    conn.executescript("""
        CREATE TABLE files (id INTEGER PRIMARY KEY, path TEXT);
        CREATE TABLE symbols (
            id INTEGER PRIMARY KEY,
            file_id INTEGER,
            name TEXT,
            kind TEXT,
            line_start INTEGER
        );
        CREATE TABLE edges (
            source_id INTEGER,
            target_id INTEGER,
            kind TEXT,
            line INTEGER
        );

        -- Two files: src/v2/Widget.tsx  and  src/components/Legacy.tsx
        INSERT INTO files VALUES (1, 'src/v2/Widget.tsx'), (2, 'src/components/Legacy.tsx');

        -- Symbols
        INSERT INTO symbols VALUES
            (10, 1, 'Widget',   'class',    1),
            (20, 2, 'Legacy',   'class',    1),
            (30, 1, 'settings', 'variable', 5),
            (40, 2, 'Settings', 'class',    5);

        -- Two edges between the same pair of files:
        --   - a real import edge  (Widget -> Legacy)
        --   - a call edge         (settings -> Settings) — name-match FP scenario
        INSERT INTO edges VALUES
            (10, 20, 'import', 3),
            (30, 40, 'call',   6);
    """)
    return conn


def _rule(name: str, from_pat: str, to_pat: str, edge_kinds=None) -> dict:
    r: dict = {
        "name": name,
        "type": "dependency",
        "from": from_pat,
        "to": to_pat,
        "allow": False,
    }
    if edge_kinds is not None:
        r["edge_kinds"] = edge_kinds
    return r


@pytest.fixture()
def conn():
    return _make_conn()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEdgeKindsFilter:
    def test_no_filter_sees_both_edges(self, conn):
        """Without edge_kinds, all edge kinds (import + call) are matched."""
        from roam.commands.cmd_fitness import _check_dependency_rule

        violations = _check_dependency_rule(
            _rule("all-edges", "src/v2/**", "src/components/**"),
            conn,
        )
        assert len(violations) == 2

    def test_import_filter_sees_only_import_edge(self, conn):
        """edge_kinds: [import] skips the call edge — only the true import fires."""
        from roam.commands.cmd_fitness import _check_dependency_rule

        violations = _check_dependency_rule(
            _rule("import-only", "src/v2/**", "src/components/**", edge_kinds=["import"]),
            conn,
        )
        assert len(violations) == 1
        assert violations[0]["edge_kind"] == "import"

    def test_call_filter_sees_only_call_edge(self, conn):
        """edge_kinds: [call] skips the import edge."""
        from roam.commands.cmd_fitness import _check_dependency_rule

        violations = _check_dependency_rule(
            _rule("call-only", "src/v2/**", "src/components/**", edge_kinds=["call"]),
            conn,
        )
        assert len(violations) == 1
        assert violations[0]["edge_kind"] == "call"

    def test_unknown_kind_produces_no_violations(self, conn):
        """Filtering by a kind that doesn't exist returns an empty list."""
        from roam.commands.cmd_fitness import _check_dependency_rule

        violations = _check_dependency_rule(
            _rule("noop", "src/v2/**", "src/components/**", edge_kinds=["type"]),
            conn,
        )
        assert violations == []

    def test_scalar_string_is_coerced_to_list(self, conn):
        """edge_kinds: import (bare string, no brackets in YAML) still works."""
        from roam.commands.cmd_fitness import _check_dependency_rule

        violations = _check_dependency_rule(
            _rule("scalar-str", "src/v2/**", "src/components/**", edge_kinds="import"),
            conn,
        )
        assert len(violations) == 1
        assert violations[0]["edge_kind"] == "import"

    def test_multi_kind_filter(self, conn):
        """edge_kinds: [import, call] passes both edges through."""
        from roam.commands.cmd_fitness import _check_dependency_rule

        violations = _check_dependency_rule(
            _rule("multi", "src/v2/**", "src/components/**", edge_kinds=["import", "call"]),
            conn,
        )
        assert len(violations) == 2

    def test_no_violations_when_globs_dont_match(self, conn):
        """edge_kinds filter does not affect glob matching: non-matching globs still pass."""
        from roam.commands.cmd_fitness import _check_dependency_rule

        violations = _check_dependency_rule(
            _rule("no-match", "src/legacy/**", "src/components/**", edge_kinds=["import"]),
            conn,
        )
        assert violations == []
