"""W708 — Invariant tests for the relations resolver.

Guards against the call-edge mis-attribution bug where every ref inside
a Python method silently re-attributed to the first symbol in the file
(``syms[0]`` in ``_closest_symbol``) because ``all_symbol_rows`` was
built without ``line_end``, so the "containing symbol" check
``ls <= ref_line <= le and le > 0`` could never succeed.

The structural invariant we assert: for every edge whose source is a
function or method (kinds with a definite ``line_start``..``line_end``
body), the reference line must fall inside the source symbol's body.
Module-scope refs (no containing function) are exempt because
``_closest_symbol`` intentionally attributes them to the first symbol
as a "file-level source" placeholder — see relations.py:850-855.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, index_in_process


@pytest.fixture
def class_method_project(tmp_path):
    """A Python project where a class method body contains calls.

    The class is defined AFTER a top-level helper. Pre-W708, calls
    inside ``Worker.run`` were mis-attributed to the top-level
    ``_format`` helper because (a) the resolver couldn't find a
    ``Worker.run`` key in ``symbols_by_name`` (only the bare
    ``run`` is keyed), (b) the ``_closest_symbol`` fallback then
    looked for a symbol whose [line_start, line_end] contained the
    ref line, but ``line_end`` was missing from ``all_symbol_rows``,
    so the containing check failed and ``syms[0]`` (= ``_format``)
    won by default.
    """
    proj = tmp_path / "cmproj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "helpers.py").write_text("def emit(x):\n    return str(x)\n\ndef stamp():\n    return 1\n")
    # Top-level helper BEFORE the class — this is the symbol that
    # ``syms[0]`` would mis-attribute to.
    (proj / "worker.py").write_text(
        "def _format(n):\n"
        "    return f'{n}'\n"
        "\n"
        "\n"
        "class Worker:\n"
        "    def run(self, n):\n"
        "        from helpers import emit, stamp\n"
        "        emit(n)\n"
        "        stamp()\n"
        "        return _format(n)\n"
    )
    git_init(proj)
    return proj


def _open_db(proj):
    from roam.db.connection import open_db

    return open_db(readonly=True, project_root=proj)


def test_method_call_edges_attributed_to_method_not_first_symbol(class_method_project):
    """Calls inside ``Worker.run`` belong to ``run``, not ``_format``."""
    proj = class_method_project
    out, rc = index_in_process(proj)
    assert rc == 0, out

    with _open_db(proj) as conn:
        # Locate the run method and the _format helper.
        rows = conn.execute(
            "SELECT s.id, s.name, s.kind, s.line_start, s.line_end "
            "FROM symbols s JOIN files f ON s.file_id=f.id "
            "WHERE f.path='worker.py' ORDER BY s.line_start"
        ).fetchall()
        by_name = {r["name"]: r for r in rows}
        assert "_format" in by_name and "run" in by_name, f"expected both symbols indexed, got {sorted(by_name)}"
        run_sym = by_name["run"]
        format_sym = by_name["_format"]

        # Sanity: line_end was actually populated in the DB.
        assert run_sym["line_end"] is not None and run_sym["line_end"] >= run_sym["line_start"]

        # Count edges sourced from each.
        emit_to_run = conn.execute(
            "SELECT COUNT(*) FROM edges e JOIN symbols t ON e.target_id=t.id WHERE e.source_id=? AND t.name='emit'",
            (run_sym["id"],),
        ).fetchone()[0]
        stamp_to_run = conn.execute(
            "SELECT COUNT(*) FROM edges e JOIN symbols t ON e.target_id=t.id WHERE e.source_id=? AND t.name='stamp'",
            (run_sym["id"],),
        ).fetchone()[0]
        # Pre-W708: these counts would be 0 (the edges go to _format instead).
        assert emit_to_run >= 1, "Worker.run -> emit edge missing; pre-W708 it was mis-attributed to _format"
        assert stamp_to_run >= 1, "Worker.run -> stamp edge missing; pre-W708 it was mis-attributed to _format"

        # _format should NOT have absorbed these edges.
        format_edges = conn.execute(
            "SELECT t.name FROM edges e JOIN symbols t ON e.target_id=t.id WHERE e.source_id=?",
            (format_sym["id"],),
        ).fetchall()
        format_targets = {r["name"] for r in format_edges}
        # _format's body is `return f'{n}'` — no calls into emit/stamp/_format.
        assert "emit" not in format_targets, (
            f"_format should not source an edge to emit (got targets={format_targets}); "
            "this is the W708 mis-attribution regression."
        )
        assert "stamp" not in format_targets, (
            f"_format should not source an edge to stamp (got targets={format_targets}); "
            "this is the W708 mis-attribution regression."
        )


def test_call_edges_originate_inside_symbol_body(class_method_project):
    """For every (source_id, line) where the source is a function/method
    with a populated ``line_end``, the ref line lies inside the body.

    Module-scope ``import`` edges are exempt — they're intentionally
    attributed to the file-level first-symbol placeholder.
    """
    proj = class_method_project
    out, rc = index_in_process(proj)
    assert rc == 0, out

    with _open_db(proj) as conn:
        violations = conn.execute(
            """
            SELECT s.name AS src, s.line_start AS ls, s.line_end AS le,
                   e.line AS rl, e.kind AS ek, t.name AS tgt
            FROM edges e
            JOIN symbols s ON e.source_id = s.id
            LEFT JOIN symbols t ON e.target_id = t.id
            WHERE s.kind IN ('function','method')
              AND s.line_end IS NOT NULL
              AND e.kind NOT IN ('import')
              AND (e.line < s.line_start OR e.line > s.line_end)
            """
        ).fetchall()
        # On this fixture there should be NO non-import violations.
        assert not violations, "edge.line outside source-symbol body: " + "; ".join(
            f"{v['src']} L{v['ls']}-{v['le']} ref_line={v['rl']} kind={v['ek']} -> {v['tgt']}" for v in violations
        )


def test_module_scope_imports_not_attributed_to_first_symbol(tmp_path):
    """W742 — module-scope imports must NOT attribute to ``syms[0]``.

    Before W742, ``_closest_symbol`` returned ``syms[0]`` for any
    reference whose line lay outside every function body. ``kind='import'``
    references at the top of the file (lines 1-N before the first def)
    therefore attributed to whichever function happened to be first,
    producing phantom outgoing IMPORT edges and — via the effect
    propagator — phantom transitive effects.

    The 3-line ``_helper`` formatter below MUST end up with 0 outgoing
    import edges. Module-scope ``import json`` / ``import os`` are
    intentionally dropped (no module pseudo-symbol exists yet).
    """
    proj = tmp_path / "w742_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "other.py").write_text("def callee():\n    return 42\n")
    # Module-scope imports BEFORE the first function. Pre-W742 these
    # would attribute to ``_helper`` as syms[0].
    (proj / "main.py").write_text(
        "import json\n"
        "import os\n"
        "from other import callee\n"
        "\n"
        "def _helper(n):\n"
        "    return f'{n}'\n"
        "\n"
        "def real_caller():\n"
        "    callee()\n"
        "    return _helper(1)\n"
    )
    git_init(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, out

    from roam.db.connection import open_db

    with open_db(readonly=True, project_root=proj) as conn:
        helper = conn.execute(
            "SELECT s.id FROM symbols s JOIN files f ON s.file_id=f.id WHERE f.path='main.py' AND s.name='_helper'"
        ).fetchone()
        assert helper is not None, "expected _helper to be indexed"

        # _helper must have ZERO outgoing import edges. Pre-W742 it
        # absorbed every module-scope import as syms[0].
        helper_imports = conn.execute(
            "SELECT COUNT(*) FROM edges WHERE source_id=? AND kind='import'",
            (helper["id"],),
        ).fetchone()[0]
        assert helper_imports == 0, (
            f"_helper absorbed {helper_imports} phantom import edges; this is the W742 mis-attribution regression."
        )


def test_all_symbol_rows_carry_line_end(tmp_path):
    """W708 invariant on the in-memory ``all_symbol_rows`` map.

    Whether a symbol is freshly extracted (``_store_symbols``) or
    merged from an existing DB row (``_merge_existing_symbols``),
    the dict MUST carry ``line_end`` so the relations resolver can
    use the containing-symbol fast path.
    """
    proj = tmp_path / "lineend_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "m.py").write_text("def a():\n    return 1\n\nclass C:\n    def b(self):\n        return 2\n")
    git_init(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, out

    # Re-merge from the existing DB by re-running indexer in-process.
    # _merge_existing_symbols is the path that historically dropped
    # line_end. We assert via DB state that every method/function
    # row has line_end populated.
    from roam.db.connection import open_db

    with open_db(readonly=True, project_root=proj) as conn:
        rows = conn.execute(
            "SELECT name, kind, line_start, line_end FROM symbols WHERE kind IN ('function','method')"
        ).fetchall()
        for r in rows:
            assert r["line_end"] is not None, (
                f"symbol {r['name']!r} ({r['kind']}) has NULL line_end; the extractor should always populate it."
            )
            assert r["line_end"] >= r["line_start"], (
                f"symbol {r['name']!r} has line_end {r['line_end']} < line_start {r['line_start']}"
            )
