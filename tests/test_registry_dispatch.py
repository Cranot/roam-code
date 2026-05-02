"""Tests for the registry-dispatch edge synthesiser.

Validates the (module_path_str, fn_name_str) shape used by
roam.cli._COMMANDS — the most common Python registry-of-functions
pattern that the runtime resolves via importlib.
"""

from __future__ import annotations

import sqlite3

from roam.index.registry_dispatch import resolve_registry_dispatch


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE files (
            id INTEGER PRIMARY KEY,
            path TEXT NOT NULL,
            language TEXT,
            file_role TEXT DEFAULT 'source'
        );
        CREATE TABLE symbols (
            id INTEGER PRIMARY KEY,
            file_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            qualified_name TEXT,
            kind TEXT NOT NULL
        );
        CREATE TABLE edges (
            id INTEGER PRIMARY KEY,
            source_id INTEGER NOT NULL,
            target_id INTEGER NOT NULL,
            kind TEXT NOT NULL
        );
        """
    )
    return conn


def _add_file(conn, file_id, path, role="source"):
    conn.execute(
        "INSERT INTO files (id, path, language, file_role) VALUES (?, ?, 'python', ?)",
        (file_id, path, role),
    )


def _add_symbol(conn, sym_id, file_id, name, qualified_name=None, kind="function"):
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, qualified_name, kind) VALUES (?, ?, ?, ?, ?)",
        (sym_id, file_id, name, qualified_name or name, kind),
    )


class TestRegistryDispatch:
    def test_no_files_returns_zero(self, tmp_path, monkeypatch):
        conn = _make_conn()
        monkeypatch.chdir(tmp_path)
        assert resolve_registry_dispatch(conn) == 0

    def test_dispatch_dict_creates_edges(self, tmp_path, monkeypatch):
        # Stage a target file with a function ``run`` and a registry
        # file with a _COMMANDS dict referencing it.
        target = tmp_path / "src" / "myproj" / "commands" / "cmd_run.py"
        target.parent.mkdir(parents=True)
        target.write_text("def run():\n    pass\n")

        cli = tmp_path / "src" / "myproj" / "cli.py"
        cli.write_text('_COMMANDS = {\n    "run": ("myproj.commands.cmd_run", "run"),\n}\n')

        conn = _make_conn()
        _add_file(conn, 1, str(cli))
        _add_file(conn, 2, str(target))
        _add_symbol(conn, 10, 1, "_COMMANDS", "_COMMANDS", kind="variable")
        # The target symbol's file_path needs to map to "myproj.commands.cmd_run"
        # via the by_module_dotted lookup. The lookup builds the module
        # name from the file path with src/ stripped; using full absolute
        # path won't match. So we need to pass the absolute path as
        # files.path (which the production code does too, since paths
        # are stored relative).
        # For this test, we need files.path to be relative to the
        # project root for the dotted lookup to work. Since the helper
        # uses by_qualified as a fallback when by_module_dotted misses,
        # add a qualified_name match too.
        _add_symbol(conn, 20, 2, "run", qualified_name="run")

        monkeypatch.chdir(tmp_path)
        # Update files.path to relative form so by_module_dotted resolves
        conn.execute("UPDATE files SET path = ? WHERE id = ?", ("src/myproj/cli.py", 1))
        conn.execute("UPDATE files SET path = ? WHERE id = ?", ("src/myproj/commands/cmd_run.py", 2))

        # Re-stage the cli file at the relative path resolve_registry_dispatch
        # opens via `open(path)`. Our chdir is set, so the relative path works.
        n = resolve_registry_dispatch(conn, package_prefix="myproj.")
        assert n == 1
        edges = conn.execute("SELECT source_id, target_id FROM edges WHERE kind = 'dispatch'").fetchall()
        assert (edges[0]["source_id"], edges[0]["target_id"]) == (10, 20)

    def test_only_string_tuples_match(self, tmp_path, monkeypatch):
        cli = tmp_path / "cli.py"
        cli.write_text(
            "_THINGS = {\n"
            '    "a": ("myproj.x", "a"),\n'
            '    "b": "not a tuple",\n'
            '    "c": (1, 2),\n'
            '    "d": ("notmyproj.y", "d"),  # wrong prefix\n'
            "}\n"
        )
        conn = _make_conn()
        _add_file(conn, 1, "cli.py")
        _add_symbol(conn, 10, 1, "_THINGS", "_THINGS", kind="variable")
        _add_symbol(conn, 20, 1, "a", qualified_name="a")
        monkeypatch.chdir(tmp_path)
        n = resolve_registry_dispatch(conn, package_prefix="myproj.")
        assert n == 1

    def test_idempotent_reindex(self, tmp_path, monkeypatch):
        cli = tmp_path / "cli.py"
        cli.write_text('_C = {\n    "a": ("myproj.x", "a"),\n}\n')
        conn = _make_conn()
        _add_file(conn, 1, "cli.py")
        _add_symbol(conn, 10, 1, "_C", "_C", kind="variable")
        _add_symbol(conn, 20, 1, "a", qualified_name="a")
        monkeypatch.chdir(tmp_path)
        assert resolve_registry_dispatch(conn, package_prefix="myproj.") == 1
        # Second run should drop and re-derive — count stays at 1.
        assert resolve_registry_dispatch(conn, package_prefix="myproj.") == 1
        total = conn.execute("SELECT COUNT(*) FROM edges WHERE kind='dispatch'").fetchone()[0]
        assert total == 1
