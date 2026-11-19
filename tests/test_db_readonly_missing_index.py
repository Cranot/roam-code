"""Read-only database opens must never create a missing index."""

import sqlite3

import pytest

from roam.db.connection import get_connection, open_db


def test_readonly_missing_index_does_not_create_database(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("ROAM_DB_DIR", raising=False)
    store = tmp_path / ".roam"
    store.mkdir()
    db_path = store / "index.db"
    error = None

    try:
        with open_db(readonly=True, project_root=tmp_path):
            pass
    except Exception as exc:
        # Error types may vary; the invariant is that a failed read writes nothing.
        error = exc

    assert not db_path.exists(), "A read-only open created the missing index.db"
    assert list(store.iterdir()) == [], "A failed read left database artifacts"
    assert error is not None, "Opening a missing index must fail"


def test_existing_database_remains_readonly(tmp_path):
    db_path = tmp_path / "index.db"
    writer = get_connection(db_path, readonly=False)
    try:
        writer.execute("CREATE TABLE sample (value INTEGER)")
        writer.execute("INSERT INTO sample VALUES (42)")
        writer.commit()
    finally:
        writer.close()

    reader = get_connection(db_path, readonly=True)
    try:
        assert [row[0] for row in reader.execute("SELECT value FROM sample")] == [42]
        with pytest.raises(sqlite3.OperationalError):
            reader.execute("INSERT INTO sample VALUES (99)")
    finally:
        reader.close()
