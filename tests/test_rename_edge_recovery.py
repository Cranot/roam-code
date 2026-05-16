"""Regression tests for the rename-no-recovery silent-edge-loss bug.

ROADMAP S2. The incremental indexer used to gate affected-neighbor
re-extraction on ``if not force and modified and changed_file_ids``
(indexer.py around line 1618). The ``and modified`` clause caused the
entire recovery path to be skipped on a *pure* rename, where
``get_changed_files`` returns ``added=[new], removed=[old], modified=[]``.

Failure mode: the old symbol rows are CASCADE-deleted with the old file
row, which deletes every edge that targeted them. The new symbol rows
are created from the new file, but their callers (in unchanged files)
are never re-extracted, so no new edges are written. ``roam impact
<renamed_symbol>`` then silently under-reports callers.

The fix drops the ``and modified`` clause. These tests lock the
invariant: a pure rename preserves all cross-file edges without any
caller-side modification.

Existing test ``tests/test_index.py::test_rename_preserves_xfile_edges``
already covers the case where lib.py is *also* modified, which made
``modified`` non-empty and sidestepped the bug. The tests here exercise
the strictly-pure-rename path that the existing test cannot reach.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_commit, git_init, index_in_process


def _open_db_for(proj):
    from roam.db.connection import open_db

    return open_db(readonly=True, project_root=proj)


def _edge_count(proj) -> int:
    with _open_db_for(proj) as conn:
        return conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]


def _edges_into(proj, symbol_name: str) -> list[tuple[str, str]]:
    """Return (source_path, target_name) for edges into a given symbol name."""
    with _open_db_for(proj) as conn:
        rows = conn.execute(
            """
            SELECT
              src.path AS source_path,
              s_tgt.name AS target_name
            FROM edges e
            JOIN symbols s_tgt ON e.target_id = s_tgt.id
            JOIN symbols s_src ON e.source_id = s_src.id
            JOIN files src ON s_src.file_id = src.id
            WHERE s_tgt.name = ?
            """,
            (symbol_name,),
        ).fetchall()
        return [(r["source_path"], r["target_name"]) for r in rows]


@pytest.fixture
def caller_callee_project(tmp_path):
    """Two-file project: ``a.py`` defines ``foo``; ``b.py`` calls it."""
    proj = tmp_path / "rename_recovery_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "a.py").write_text("def foo():\n    return 1\n")
    (proj / "b.py").write_text("from a import foo\n\ndef caller():\n    return foo()\n")
    git_init(proj)
    return proj


def test_pure_rename_target_only_preserves_callers(caller_callee_project):
    """Rename ONLY the file containing the target symbol; do not touch caller.

    Before the fix: incremental classified b.py as unchanged, so its
    references were never re-extracted. The new c:foo symbol existed
    in the DB but had zero incoming edges from b:caller. ``roam impact
    foo`` reported zero callers despite b.py literally containing
    ``foo()``.

    The trick to exercise the bug: rename a.py -> c.py while keeping
    ``from a import foo`` in b.py (it won't resolve at runtime, but
    indexing only needs the call-site to point at *any* symbol named
    ``foo``). The pure-disk-rename keeps ``modified=[]``.
    """
    proj = caller_callee_project

    out1, rc1 = index_in_process(proj)
    assert rc1 == 0, f"initial index failed: {out1}"

    # Pre-rename: there must be an edge from b.py into a symbol named foo.
    edges_into_foo_before = _edges_into(proj, "foo")
    assert any(src == "b.py" for src, _ in edges_into_foo_before), (
        f"Initial index produced no b.py -> foo edge; fixture is broken. edges_into_foo_before={edges_into_foo_before}"
    )

    # Pure rename: a.py -> c.py on disk. b.py is untouched.
    # The `from a import foo` line in b.py is now broken at runtime, but
    # the call-site `foo()` still produces a reference the indexer can
    # match against the new c:foo symbol.
    time.sleep(0.1)  # ensure mtime differs for stored file rows
    src_text = (proj / "a.py").read_text()
    (proj / "a.py").unlink()
    (proj / "c.py").write_text(src_text)
    git_commit(proj, "pure rename a.py -> c.py")

    # Incremental reindex (NOT --force). With the fix, this must run
    # affected-neighbor recovery and re-extract b.py's references.
    out2, rc2 = index_in_process(proj)
    assert rc2 == 0, f"incremental rename index failed: {out2}"

    edges_into_foo_after = _edges_into(proj, "foo")
    assert any(src == "b.py" for src, _ in edges_into_foo_after), (
        "After a pure rename (a.py -> c.py, b.py untouched), no edge from "
        "b.py to a symbol named foo survived in the index. This is the "
        "rename-no-recovery bug: affected-neighbor re-extraction was gated "
        "on `modified` being non-empty, which a pure rename does not "
        "satisfy. edges_into_foo_after={!r}".format(edges_into_foo_after)
    )


def test_incremental_pure_rename_matches_force_edge_count(caller_callee_project):
    """Control: incremental pure-rename edge count equals --force edge count.

    The single most actionable invariant. If the affected-neighbor path
    short-circuits, the incremental edge count will be strictly less
    than the --force count over the same final tree.
    """
    proj = caller_callee_project

    out1, rc1 = index_in_process(proj)
    assert rc1 == 0, f"initial index failed: {out1}"

    # Pure rename a.py -> c.py; b.py untouched.
    time.sleep(0.1)
    src_text = (proj / "a.py").read_text()
    (proj / "a.py").unlink()
    (proj / "c.py").write_text(src_text)
    git_commit(proj, "pure rename a.py -> c.py")

    out2, rc2 = index_in_process(proj)
    assert rc2 == 0, f"incremental rename index failed: {out2}"
    edges_incremental = _edge_count(proj)

    # Force reindex of the same final state — ground truth.
    out3, rc3 = index_in_process(proj, "--force")
    assert rc3 == 0, f"force reindex failed: {out3}"
    edges_force = _edge_count(proj)

    assert edges_incremental == edges_force, (
        f"Incremental pure-rename produced {edges_incremental} edges, "
        f"--force produced {edges_force} over the identical file tree. "
        f"Affected-neighbor recovery is being skipped — the indexer's "
        f"rename-recovery gate must run whenever changed_file_ids is "
        f"non-empty, not only when modified is non-empty."
    )


def test_pure_rename_invokes_neighbor_recovery_branch(caller_callee_project, monkeypatch):
    """The neighbor re-extraction branch must actually fire on pure rename.

    Spies the ``_re_extract_affected`` method itself, which is the branch
    that previously had the buggy ``and modified`` gate. The earlier
    ``_find_affected_neighbor_files`` call happens regardless and so is
    not a reliable signal that the gate fired.
    """
    from roam.index.indexer import Indexer

    proj = caller_callee_project

    out1, rc1 = index_in_process(proj)
    assert rc1 == 0, f"initial index failed: {out1}"

    # Pure rename.
    time.sleep(0.1)
    src_text = (proj / "a.py").read_text()
    (proj / "a.py").unlink()
    (proj / "c.py").write_text(src_text)
    git_commit(proj, "pure rename a.py -> c.py")

    original = Indexer._re_extract_affected
    calls: list[int] = []

    def spy(self, conn, affected_file_ids, get_extractor, all_references, verbose):
        calls.append(len(affected_file_ids))
        return original(self, conn, affected_file_ids, get_extractor, all_references, verbose)

    monkeypatch.setattr(Indexer, "_re_extract_affected", spy)

    out2, rc2 = index_in_process(proj)
    assert rc2 == 0, f"incremental rename index failed: {out2}"

    assert calls, (
        "_re_extract_affected was NEVER called during incremental indexing "
        "after a pure rename (modified=[], removed=[old], added=[new]). "
        "This is the rename-no-recovery bug — the gate at indexer.py around "
        "line 1618 used `if not force and modified and affected_file_ids`, "
        "and modified=[] short-circuited the recovery path."
    )
    assert any(n > 0 for n in calls), (
        f"_re_extract_affected was called but always with an empty "
        f"affected_file_ids set: {calls}. The neighbor-detection logic is "
        f"not finding b.py as an affected caller of the renamed symbol."
    )
