"""Tests for the world-model restore-loss detector."""

from __future__ import annotations


def _classify(proj, symbol):
    from roam.db.connection import open_db
    from roam.world_model.restore_loss import classify_restore_loss

    with open_db(readonly=True) as conn:
        return classify_restore_loss(conn, symbol_name=symbol)


def test_restore_loss_flags_missing_reinsert(project_factory, monkeypatch):
    """Unconditional deletes of t1/t2/t3 with reinserts of t1/t2 -> flag t3."""
    proj = project_factory(
        {
            "src/restore.py": (
                "def restore_subset(conn, rows1, rows2):\n"
                '    conn.execute("DELETE FROM t1")\n'
                '    conn.execute("DELETE FROM t2")\n'
                '    conn.execute("DELETE FROM t3")\n'
                '    conn.execute("INSERT INTO t1 VALUES (?)", rows1)\n'
                '    conn.execute("INSERT INTO t2 VALUES (?)", rows2)\n'
            ),
        }
    )
    monkeypatch.chdir(proj)

    findings = _classify(proj, "restore_subset")

    assert findings, "Expected a restore-loss finding"
    finding = findings[0]
    assert finding.kind == "silent_data_loss"
    assert finding.lost_tables == ["t3"], f"Expected only t3 lost, got {finding.lost_tables}"
    assert finding.deleted_tables == ["t1", "t2", "t3"]
    assert finding.inserted_tables == ["t1", "t2"]


def test_restore_loss_silent_when_sets_match(project_factory, monkeypatch):
    """Deleting and re-inserting the same tables is clean."""
    proj = project_factory(
        {
            "src/restore.py": (
                "def restore_all(conn, rows1, rows2, rows3):\n"
                '    conn.execute("DELETE FROM t1")\n'
                '    conn.execute("DELETE FROM t2")\n'
                '    conn.execute("DELETE FROM t3")\n'
                '    conn.execute("INSERT INTO t1 VALUES (?)", rows1)\n'
                '    conn.execute("INSERT INTO t2 VALUES (?)", rows2)\n'
                '    conn.execute("INSERT INTO t3 VALUES (?)", rows3)\n'
            ),
        }
    )
    monkeypatch.chdir(proj)

    findings = _classify(proj, "restore_all")

    assert findings == [], f"Expected no finding, got {findings}"


def test_restore_loss_delete_only_is_not_a_finding(project_factory, monkeypatch):
    """A delete-only function is a normal delete, not a restore-loss shape."""
    proj = project_factory(
        {
            "src/cleanup.py": (
                'def drop_tables(conn):\n    conn.execute("DELETE FROM t1")\n    conn.execute("DELETE FROM t2")\n'
            ),
        }
    )
    monkeypatch.chdir(proj)

    findings = _classify(proj, "drop_tables")

    assert findings == [], f"Expected no finding for delete-only function, got {findings}"


def test_restore_loss_conditional_delete_is_not_a_finding(project_factory, monkeypatch):
    """Conditional deletes are ignored because they are not unconditional wipes."""
    proj = project_factory(
        {
            "src/cleanup.py": (
                "def prune(conn, cutoff):\n"
                '    conn.execute("DELETE FROM t1 WHERE updated_at < ?", (cutoff,))\n'
                '    conn.execute("INSERT INTO t1 VALUES (?)", (cutoff,))\n'
            ),
        }
    )
    monkeypatch.chdir(proj)

    findings = _classify(proj, "prune")

    assert findings == [], f"Expected no finding for conditional delete, got {findings}"


def test_restore_loss_detects_explicit_delete_order_loop(project_factory, monkeypatch):
    """A literal DELETE_ORDER list consumed in a loop should still flag missing tables."""
    proj = project_factory(
        {
            "src/restore.py": (
                "def restore_from_backup(conn, rows1, rows2):\n"
                "    DELETE_ORDER = ['t1', 't2', 't3']\n"
                "    for table in DELETE_ORDER:\n"
                '        conn.execute(f"DELETE FROM {table}")\n'
                '    conn.execute("INSERT INTO t1 VALUES (?)", rows1)\n'
                '    conn.execute("INSERT INTO t2 VALUES (?)", rows2)\n'
            ),
        }
    )
    monkeypatch.chdir(proj)

    findings = _classify(proj, "restore_from_backup")

    assert findings, "Expected a restore-loss finding from delete-order loop"
    assert findings[0].lost_tables == ["t3"], f"Expected only t3 lost, got {findings[0].lost_tables}"
