"""Effects must be replaced even when the last effectful body becomes pure."""

import sqlite3
from contextlib import closing

import networkx as nx
import pytest

from roam.analysis.effects import LOGGING, compute_and_store_effects
from roam.db.schema import SCHEMA_SQL


@pytest.mark.parametrize("incremental", [False, True])
@pytest.mark.parametrize("with_graph", [False, True])
def test_last_effect_removed(tmp_path, monkeypatch, incremental, with_graph):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.chdir(tmp_path)
    worker = tmp_path / "worker.py"
    worker.write_text("def worker():\n    logger.info('working')\n", encoding="utf-8")
    (tmp_path / "caller.py").write_text("def caller():\n    return worker()\n", encoding="utf-8")
    graph = nx.DiGraph([(2, 1)]) if with_graph else None

    with closing(sqlite3.connect(":memory:")) as conn:
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA_SQL)
        conn.executemany(
            "INSERT INTO files (id, path, language) VALUES (?, ?, 'python')",
            [(1, "worker.py"), (2, "caller.py")],
        )
        conn.executemany(
            "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end) VALUES (?, ?, ?, 'function', 1, 2)",
            [(1, 1, "worker"), (2, 2, "caller")],
        )

        # Supply the supported source cache without optional ASTs so this test
        # never needs to acquire parser grammars from the network.
        source_cache = {name: ((tmp_path / name).read_bytes(), None) for name in ("worker.py", "caller.py")}
        compute_and_store_effects(conn, tmp_path, graph, source_cache=source_cache)
        expected = {(1, LOGGING, "direct")}
        if with_graph:
            expected.add((2, LOGGING, "transitive"))
        assert {
            tuple(row) for row in conn.execute("SELECT symbol_id, effect_type, source FROM symbol_effects")
        } == expected

        worker.write_text("def worker():\n    return 42\n", encoding="utf-8")
        source_cache["worker.py"] = (worker.read_bytes(), None)
        compute_and_store_effects(
            conn,
            tmp_path,
            graph,
            source_cache=source_cache,
            changed_paths={"worker.py"} if incremental else None,
        )
        assert conn.execute("SELECT * FROM symbol_effects").fetchall() == []
