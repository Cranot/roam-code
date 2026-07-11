"""Tests for `roam cycles` — the import/call cycle (SCC) command.

Sibling of `roam clusters` / `roam layers`; the focused view of the cycle
analysis `roam health` bundles.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from conftest import (  # noqa: E402
    index_in_process,
    invoke_cli,
    parse_json_output,
)


def test_cycles_finds_cross_file_cycle(cli_runner, tmp_path, monkeypatch):
    proj = tmp_path / "cyc"
    proj.mkdir()
    # Anchor project-root detection here so find_project_root can't walk up to a
    # polluted /tmp ancestor (lesson from the brief-test /tmp pollution dig).
    (proj / ".git").mkdir()
    (proj / "a.py").write_text("from b import foo\n\n\ndef bar():\n    return foo()\n")
    (proj / "b.py").write_text("from a import bar\n\n\ndef foo():\n    return bar()\n")
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, out

    result = invoke_cli(cli_runner, ["cycles"], cwd=proj, json_mode=True)
    assert result.exit_code == 0, result.output
    data = parse_json_output(result, command="cycles")
    assert data["command"] == "cycles"
    assert data["summary"]["cycle_count"] >= 1
    assert data["summary"]["actionable_count"] >= 1  # 2 distinct non-test files


# ---------------------------------------------------------------------------
# Shadow-artifact classification (mark_shadow_artifacts) — label-only.
# Unit tests build the index DB directly so the graph shape (phantom edges,
# non-exported bindings, import linkage) is exact and deterministic.
# ---------------------------------------------------------------------------


def _shadow_test_db(files, symbols, file_edges, edges):
    import sqlite3

    from roam.db.schema import SCHEMA_SQL

    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA_SQL)
    conn.executemany("INSERT INTO files (id, path) VALUES (?, ?)", files)
    conn.executemany(
        "INSERT INTO symbols (id, file_id, name, kind, is_exported) VALUES (?, ?, ?, ?, ?)",
        symbols,
    )
    conn.executemany(
        "INSERT INTO file_edges (source_file_id, target_file_id, kind) VALUES (?, ?, ?)",
        file_edges,
    )
    conn.executemany("INSERT INTO edges (source_id, target_id, kind) VALUES (?, ?, ?)", edges)
    conn.commit()
    return conn


def test_shadow_artifact_false_for_genuine_cycle_with_unrelated_name_collision():
    """REGRESSION: a corpus-wide name collision is NOT proof of shadowing.

    Genuine cross-file cycle ``a.helper <-> b.config`` whose closing edge
    targets a non-exported const ``config`` that merely name-collides with
    an UNRELATED exported symbol in a third module that neither cycle file
    imports. The prior attempt classified this as a shadow artifact and
    suppressed a genuine cycle — it must assert ``shadow_artifact is False``
    and report unchanged.
    """
    import networkx as nx

    from roam.graph.cycles import (
        find_cycles,
        format_cycles,
        mark_actionable_cycles,
        mark_shadow_artifacts,
    )

    conn = _shadow_test_db(
        files=[(1, "a.py"), (2, "b.py"), (3, "unrelated.py")],
        symbols=[
            (1, 1, "helper", "function", 1),
            (2, 2, "config", "constant", 0),  # non-exported closing-edge target
            (3, 3, "config", "constant", 1),  # unrelated exported name collision
        ],
        # a.py <-> b.py genuinely import each other; NOBODY imports unrelated.py
        file_edges=[(1, 2, "imports"), (2, 1, "imports")],
        edges=[(2, 1, "calls"), (1, 2, "references")],
    )
    G = nx.DiGraph()
    G.add_edges_from([(2, 1), (1, 2)])

    formatted = format_cycles(find_cycles(G), conn)
    mark_actionable_cycles(formatted)
    before = [(c["size"], c["files"], c["actionable"]) for c in formatted]

    mark_shadow_artifacts(formatted, G, conn)

    assert len(formatted) == 1
    assert formatted[0]["shadow_artifact"] is False
    assert "shadow_evidence" not in formatted[0]
    # HARD CONSTRAINT: label-only — the genuine cycle still reports unchanged.
    assert [(c["size"], c["files"], c["actionable"]) for c in formatted] == before
    conn.close()


def test_shadow_artifact_true_for_destructured_consumer_phantom():
    """POSITIVE: the destructured-consumer mislink shape IS labelled.

    Consumer does ``const { total } = useCart()`` — a non-exported local
    ``total``. The composable module (which the consumer imports) exports a
    genuine ``total``; the resolver mislinks a reference inside the
    composable's own file to the consumer's local binding, closing a
    phantom cycle. Must label ``shadow_artifact: True`` (never suppress).
    """
    import networkx as nx

    from roam.graph.cycles import (
        find_cycles,
        format_cycles,
        mark_actionable_cycles,
        mark_shadow_artifacts,
    )

    conn = _shadow_test_db(
        files=[(1, "src/composables/cart.js"), (2, "src/components/Consumer.vue")],
        symbols=[
            (1, 1, "useCart", "function", 1),
            (2, 1, "total", "constant", 1),  # genuine sibling export (destructured source)
            (3, 2, "total", "constant", 0),  # destructured local binding in consumer
        ],
        file_edges=[(2, 1, "imports")],  # consumer imports the composable module
        edges=[(3, 1, "calls"), (1, 3, "references")],  # (1, 3) is the phantom mislink
    )
    G = nx.DiGraph()
    G.add_edges_from([(3, 1), (1, 3)])

    formatted = format_cycles(find_cycles(G), conn)
    mark_actionable_cycles(formatted)
    mark_shadow_artifacts(formatted, G, conn)

    assert len(formatted) == 1
    assert formatted[0]["shadow_artifact"] is True
    evidence = formatted[0]["shadow_evidence"]
    assert evidence["shadowed_name"] == "total"
    assert evidence["genuine_sibling_file"] == "src/composables/cart.js"
    assert evidence["edge"] == [1, 3]
    # Label-only: the cycle is still present and still counted.
    assert formatted[0]["size"] == 2


def test_shadow_artifact_false_when_sibling_is_same_file_as_binding():
    """NEGATIVE: an exported same-name symbol in the binding's OWN file is
    not a destructure sibling — the genuine sibling must be cross-file."""
    import networkx as nx

    from roam.graph.cycles import (
        find_cycles,
        format_cycles,
        mark_actionable_cycles,
        mark_shadow_artifacts,
    )

    conn = _shadow_test_db(
        files=[(1, "a.py"), (2, "b.py")],
        symbols=[
            (1, 1, "helper", "function", 1),
            (2, 2, "limit", "constant", 0),  # non-exported closing-edge target
            (3, 2, "limit", "constant", 1),  # exported, but SAME file as the binding
        ],
        file_edges=[(1, 2, "imports"), (2, 1, "imports")],
        edges=[(2, 1, "calls"), (1, 2, "references")],
    )
    G = nx.DiGraph()
    G.add_edges_from([(2, 1), (1, 2)])

    formatted = format_cycles(find_cycles(G), conn)
    mark_actionable_cycles(formatted)
    mark_shadow_artifacts(formatted, G, conn)

    assert len(formatted) == 1
    assert formatted[0]["shadow_artifact"] is False
    conn.close()


def test_cycles_clean_repo_reports_none(cli_runner, tmp_path, monkeypatch):
    proj = tmp_path / "clean"
    proj.mkdir()
    (proj / ".git").mkdir()
    (proj / "a.py").write_text("def foo():\n    return 1\n")
    (proj / "b.py").write_text("def bar():\n    return 2\n")
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj, "--force")
    assert rc == 0, out

    result = invoke_cli(cli_runner, ["cycles"], cwd=proj, json_mode=True)
    assert result.exit_code == 0
    data = parse_json_output(result, command="cycles")
    assert data["summary"]["cycle_count"] == 0
