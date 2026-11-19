"""File suffix resolution must use whole path components for policy evidence."""

import sqlite3
from contextlib import closing

import pytest

from roam.policy.graph_clauses import check_imports_from


@pytest.mark.parametrize("target", ["db.py", "src/db.py", r"src\db.py"])
@pytest.mark.parametrize("db_imports_dependency", [True, False])
def test_import_evidence_belongs_to_requested_file(target, db_imports_dependency):
    # The misleading suffix is deliberately first in the table's scan order.
    # Both files have edges, so this tests resolution without symbol fallback.
    with closing(sqlite3.connect(":memory:")) as conn:
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE files (id INTEGER PRIMARY KEY, path TEXT NOT NULL);
            CREATE TABLE file_edges (source_file_id INTEGER, target_file_id INTEGER);
            INSERT INTO files VALUES
                (1, 'src/mydb.py'), (2, 'src/db.py'),
                (3, 'lib/dependency.py'), (4, 'lib/unrelated.py');
            """
        )
        intended, decoy = (3, 4) if db_imports_dependency else (4, 3)
        conn.executemany("INSERT INTO file_edges VALUES (?, ?)", [(1, decoy), (2, intended)])

        matches, evidence = check_imports_from(conn, "lib/dependency.py", target)

        assert matches is db_imports_dependency, evidence
        assert evidence["target_file"] == "src/db.py"


@pytest.mark.parametrize("name", ["my_db.py", "my%db.py"])
def test_suffix_names_are_literal(name):
    from roam.policy.graph_clauses import _resolve_file_id

    with closing(sqlite3.connect(":memory:")) as conn:
        conn.row_factory = sqlite3.Row
        conn.executescript("CREATE TABLE files(id INTEGER, path TEXT); INSERT INTO files VALUES (1, 'src/myxdb.py');")
        assert _resolve_file_id(conn, name) == (None, None)
        conn.execute("INSERT INTO files VALUES (2, ?)", ("src/" + name,))
        assert _resolve_file_id(conn, name) == (2, "src/" + name)
