"""Qualified module-attribute uses must reach dead/coverage consumers."""

from __future__ import annotations


def test_module_qualified_call_keeps_export_alive(project_factory, monkeypatch):
    proj = project_factory(
        {
            "helpers.py": "def render_invoice(value):\n    return str(value)\n",
            "consumer.py": ("import helpers\ndef render(value):\n    return helpers.render_invoice(value)\n"),
        }
    )
    monkeypatch.chdir(proj)
    from roam.commands.cmd_dead import _analyze_dead
    from roam.db.connection import open_db

    with open_db(readonly=True, project_root=proj) as conn:
        high, low, *_rest = _analyze_dead(conn)
    assert "render_invoice" not in {row["name"] for row in high + low}


def test_unreferenced_export_remains_dead(project_factory, monkeypatch):
    proj = project_factory({"helpers.py": "def unused_export(value):\n    return str(value)\n"})
    monkeypatch.chdir(proj)
    from roam.commands.cmd_dead import _analyze_dead
    from roam.db.connection import open_db

    with open_db(readonly=True, project_root=proj) as conn:
        high, low, *_rest = _analyze_dead(conn)
    assert "unused_export" in {row["name"] for row in high + low}


def test_direct_private_module_call_in_assert_counts_as_coverage(project_factory, monkeypatch):
    proj = project_factory(
        {
            "helpers.py": "def _normalise(value):\n    return value.strip()\n",
            "test_helpers.py": ("import helpers\ndef test_normalise():\n    assert helpers._normalise(' x ') == 'x'\n"),
        }
    )
    monkeypatch.chdir(proj)
    from roam.commands.cmd_path_coverage import _build_tested_set
    from roam.db.connection import open_db

    with open_db(readonly=True, project_root=proj) as conn:
        symbol_id = conn.execute("SELECT id FROM symbols WHERE name = '_normalise'").fetchone()[0]
        tested = _build_tested_set(conn)
    assert symbol_id in tested


def test_uncalled_private_function_remains_uncovered(project_factory, monkeypatch):
    proj = project_factory(
        {
            "helpers.py": "def _normalise(value):\n    return value.strip()\n",
            "test_helpers.py": "def test_placeholder():\n    assert True\n",
        }
    )
    monkeypatch.chdir(proj)
    from roam.commands.cmd_path_coverage import _build_tested_set
    from roam.db.connection import open_db

    with open_db(readonly=True, project_root=proj) as conn:
        symbol_id = conn.execute("SELECT id FROM symbols WHERE name = '_normalise'").fetchone()[0]
        tested = _build_tested_set(conn)
    assert symbol_id not in tested
