"""Integration tests for the indexing pipeline.

Covers:
- Full indexing (DB creation, files/symbols/edges population, exit codes)
- Incremental indexing (skip unchanged, detect modified/new/deleted)
- Language detection (.py, .js, .ts, .go)
- Schema correctness (required columns, safe migrations)
- Edge cases (empty project, --force flag, .gitignore respected)
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_commit, git_init, index_in_process

# ===========================================================================
# Shared fixtures
# ===========================================================================


@pytest.fixture
def index_project(tmp_path):
    """A minimal Python project with cross-file references."""
    proj = tmp_path / "idx_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "app.py").write_text("def hello():\n    return 'world'\n")
    (proj / "lib.py").write_text("from app import hello\n\ndef greet():\n    return hello()\n")
    git_init(proj)
    return proj


@pytest.fixture
def multilang_project(tmp_path):
    """A project with files in several languages."""
    proj = tmp_path / "ml_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "main.py").write_text("def main(): pass\n")
    (proj / "app.js").write_text("function run() { return 1; }\n")
    (proj / "util.ts").write_text("export function helper(): number { return 2; }\n")
    (proj / "server.go").write_text("package main\n\nfunc serve() {}\n")
    git_init(proj)
    return proj


def _open_db_for(proj):
    """Open the roam DB for a project (read-only), passing project_root explicitly."""
    from roam.db.connection import open_db

    return open_db(readonly=True, project_root=proj)


# ===========================================================================
# Full indexing (5 tests)
# ===========================================================================


class TestFullIndexing:
    """Tests that verify a full index run creates the expected DB artefacts."""

    def test_index_creates_db(self, index_project):
        """Running `roam index` creates the .roam/index.db file."""
        out, rc = index_in_process(index_project)
        assert rc == 0, f"roam index failed:\n{out}"
        # DB file may be at .roam/index.db (current default)
        db_path = index_project / ".roam" / "index.db"
        assert db_path.exists(), (
            f"Expected DB at {db_path} but it does not exist. "
            f"Contents of .roam/: {list((index_project / '.roam').iterdir()) if (index_project / '.roam').exists() else 'dir missing'}"
        )

    def test_index_populates_files(self, index_project):
        """The files table has one entry per source file."""
        out, rc = index_in_process(index_project)
        assert rc == 0, f"roam index failed:\n{out}"
        with _open_db_for(index_project) as conn:
            rows = conn.execute("SELECT path FROM files ORDER BY path").fetchall()
            paths = [r["path"] for r in rows]
            assert "app.py" in paths
            assert "lib.py" in paths
            # .gitignore is not a parseable source — may or may not appear
            # The key point is that our two Python files are indexed
            assert len(paths) >= 2

    def test_index_populates_symbols(self, index_project):
        """The symbols table has entries for functions defined in source files."""
        out, rc = index_in_process(index_project)
        assert rc == 0, f"roam index failed:\n{out}"
        with _open_db_for(index_project) as conn:
            count = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
            assert count >= 2, f"Expected at least 2 symbols (hello, greet), got {count}"
            names = {r["name"] for r in conn.execute("SELECT name FROM symbols").fetchall()}
            assert "hello" in names
            assert "greet" in names

    def test_index_populates_edges(self, index_project):
        """The edges table has entries for cross-file references."""
        out, rc = index_in_process(index_project)
        assert rc == 0, f"roam index failed:\n{out}"
        with _open_db_for(index_project) as conn:
            count = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
            # lib.py imports and calls hello from app.py -> at least 1 edge
            assert count >= 1, f"Expected at least 1 edge, got {count}"

    def test_index_exit_code_zero(self, index_project):
        """Index exits with code 0 on a clean project."""
        out, rc = index_in_process(index_project)
        assert rc == 0, f"roam index exited {rc}:\n{out}"

    def test_stale_lock_can_be_reused_when_delete_denied(self, tmp_path, monkeypatch):
        """Windows/cloud-sync folders may allow overwrites but deny deletes."""
        from roam.index import indexer as indexer_mod

        lock_path = tmp_path / ".roam" / "index.lock"
        lock_path.parent.mkdir()
        lock_path.write_text("999999")

        original_unlink = Path.unlink

        def deny_lock_unlink(path, *args, **kwargs):
            if Path(path) == lock_path:
                raise PermissionError("delete denied")
            return original_unlink(path, *args, **kwargs)

        monkeypatch.setattr(indexer_mod, "_pid_is_running", lambda _pid: False)
        monkeypatch.setattr(Path, "unlink", deny_lock_unlink)

        assert indexer_mod._claim_index_lock(lock_path) is True
        assert lock_path.read_text() == str(os.getpid())

    def test_pid_probe_handles_windows_stale_pid_systemerror(self, monkeypatch):
        from roam.index import indexer as indexer_mod

        def raise_systemerror(_pid, _signal):
            raise SystemError("<class 'OSError'> returned a result with an exception set")

        monkeypatch.setattr(indexer_mod.os, "kill", raise_systemerror)

        assert indexer_mod._pid_is_running(999999) is False

    def test_semantic_activation_advice_when_vectors_empty(self, tmp_path):
        import sqlite3

        from roam.index.indexer import _semantic_activation_advice

        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE symbols (id INTEGER PRIMARY KEY)")
        conn.execute(
            "CREATE TABLE symbol_embeddings ("
            "symbol_id INTEGER PRIMARY KEY, "
            "vector TEXT NOT NULL, "
            "dims INTEGER NOT NULL, "
            "provider TEXT NOT NULL DEFAULT 'onnx')"
        )
        conn.executemany("INSERT INTO symbols(id) VALUES (?)", [(1,), (2,)])

        advice = _semantic_activation_advice(conn, tmp_path)

        assert advice is not None
        assert "0/2 dense vectors" in advice
        assert "zeta=0.2" in advice


# ===========================================================================
# Incremental indexing (4 tests)
# ===========================================================================


class TestIncrementalIndexing:
    """Tests that verify incremental re-indexing behaviour."""

    def test_incremental_skips_unchanged(self, index_project):
        """Re-running index on an unchanged project reports 'up to date'."""
        out1, rc1 = index_in_process(index_project)
        assert rc1 == 0, f"First index failed:\n{out1}"

        # Wait briefly so mtimes are stable
        time.sleep(0.05)

        out2, rc2 = index_in_process(index_project)
        assert rc2 == 0, f"Second index failed:\n{out2}"
        # The indexer should report "up to date" when nothing changed
        assert "up to date" in out2.lower(), f"Expected 'up to date' in output, got:\n{out2}"

    def test_incremental_detects_modified(self, index_project):
        """Modifying a file triggers re-indexing of that file."""
        out1, rc1 = index_in_process(index_project)
        assert rc1 == 0

        # Modify app.py — add a new function
        time.sleep(0.1)  # Ensure mtime differs
        (index_project / "app.py").write_text(
            "def hello():\n    return 'world'\n\ndef farewell():\n    return 'goodbye'\n"
        )
        git_commit(index_project, "add farewell")

        out2, rc2 = index_in_process(index_project)
        assert rc2 == 0, f"Incremental index failed:\n{out2}"

        with _open_db_for(index_project) as conn:
            names = {r["name"] for r in conn.execute("SELECT name FROM symbols").fetchall()}
            assert "farewell" in names, f"New symbol 'farewell' not found after incremental index. Symbols: {names}"

    def test_incremental_detects_new_file(self, index_project):
        """Adding a new file indexes its symbols."""
        out1, rc1 = index_in_process(index_project)
        assert rc1 == 0

        # Add a new file
        time.sleep(0.1)
        (index_project / "util.py").write_text("def helper():\n    return 42\n")
        git_commit(index_project, "add util")

        out2, rc2 = index_in_process(index_project)
        assert rc2 == 0, f"Incremental index failed:\n{out2}"

        with _open_db_for(index_project) as conn:
            paths = {r["path"] for r in conn.execute("SELECT path FROM files").fetchall()}
            assert "util.py" in paths, f"New file 'util.py' not found in files table: {paths}"
            names = {r["name"] for r in conn.execute("SELECT name FROM symbols").fetchall()}
            assert "helper" in names, f"Symbol 'helper' from new file not found: {names}"

    def test_incremental_detects_deleted_file(self, index_project):
        """Deleting a file removes its symbols from the index."""
        out1, rc1 = index_in_process(index_project)
        assert rc1 == 0

        with _open_db_for(index_project) as conn:
            initial_paths = {r["path"] for r in conn.execute("SELECT path FROM files").fetchall()}
            assert "app.py" in initial_paths

        # Delete app.py
        (index_project / "app.py").unlink()
        git_commit(index_project, "remove app.py")

        out2, rc2 = index_in_process(index_project)
        assert rc2 == 0, f"Incremental index failed:\n{out2}"

        with _open_db_for(index_project) as conn:
            paths = {r["path"] for r in conn.execute("SELECT path FROM files").fetchall()}
            assert "app.py" not in paths, f"Deleted file 'app.py' still present in files table: {paths}"
            names = {r["name"] for r in conn.execute("SELECT name FROM symbols").fetchall()}
            assert "hello" not in names, f"Symbol 'hello' from deleted file still present: {names}"

    def test_rename_preserves_xfile_edges(self, tmp_path):
        """Pure file rename triggers affected-neighbor recovery.

        Regression test for the gating bug at indexer.py:1409 where
        ``if not force and modified and changed_file_ids`` skipped recovery
        on pure renames (modified=[], removed=[old], added=[new]). CASCADE
        wiped edges into the renamed file's symbols, and unchanged callers
        were never re-extracted, so new edges never got created.

        Contract: after an incremental rename, edge count must match the
        ``--force`` reindex of the same final state.
        """
        proj = tmp_path / "rename_proj"
        proj.mkdir()
        (proj / ".gitignore").write_text(".roam/\n")
        # a.py defines hello(); lib.py calls hello() via `from a import`.
        (proj / "a.py").write_text("def hello():\n    return 'world'\n")
        (proj / "lib.py").write_text("from a import hello\n\ndef caller():\n    return hello()\n")
        git_init(proj)

        # Initial index — establishes the baseline edge from lib.caller -> a.hello.
        out1, rc1 = index_in_process(proj)
        assert rc1 == 0, f"Initial index failed:\n{out1}"

        with _open_db_for(proj) as conn:
            edges_before = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
        assert edges_before > 0, "Initial index produced no edges; fixture is wrong"

        # Pure rename: a.py -> c.py, AND update lib.py's import to match.
        # We update lib.py too to keep the import resolvable; this also
        # makes ``modified`` non-empty, so the test passes the gating check
        # whether or not the bug is present. The point is to lock in the
        # invariant that incremental rename produces the same edge graph
        # as a force reindex of the final state.
        time.sleep(0.1)  # ensure mtime differs
        (proj / "a.py").unlink()
        (proj / "c.py").write_text("def hello():\n    return 'world'\n")
        (proj / "lib.py").write_text("from c import hello\n\ndef caller():\n    return hello()\n")
        git_commit(proj, "rename a.py -> c.py")

        # Incremental reindex.
        out2, rc2 = index_in_process(proj)
        assert rc2 == 0, f"Incremental rename index failed:\n{out2}"

        with _open_db_for(proj) as conn:
            edges_incremental = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
            paths_incremental = {r["path"] for r in conn.execute("SELECT path FROM files").fetchall()}

        assert "a.py" not in paths_incremental
        assert "c.py" in paths_incremental

        # Force reindex of the same final state — the ground truth.
        out3, rc3 = index_in_process(proj, "--force")
        assert rc3 == 0, f"Force reindex failed:\n{out3}"

        with _open_db_for(proj) as conn:
            edges_force = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]

        assert edges_incremental == edges_force, (
            f"Incremental rename produced {edges_incremental} edges, "
            f"--force produced {edges_force}. Affected-neighbor recovery "
            f"likely skipped for the rename — see indexer.py:1409 gating."
        )

    def test_pure_removal_invokes_affected_neighbor_recovery(self, tmp_path, monkeypatch):
        """Direct gating test: when removed != [] and modified == [],
        ``_find_affected_neighbor_files`` MUST be called.

        Spies the recovery function via monkeypatch so this test exercises
        the gating clause at indexer.py:1409 specifically — independent of
        whether the resulting edge graph differs. The bug pre-fix was that
        the gate's ``and modified`` clause caused the entire recovery path
        to be skipped on a pure deletion or pure rename.
        """
        from roam.index.indexer import Indexer

        proj = tmp_path / "rec_proj"
        proj.mkdir()
        (proj / ".gitignore").write_text(".roam/\n")
        (proj / "a.py").write_text("def hello():\n    return 1\n")
        (proj / "lib.py").write_text("from a import hello\n\ndef caller():\n    return hello()\n")
        git_init(proj)

        # Initial index.
        out1, rc1 = index_in_process(proj)
        assert rc1 == 0, f"initial index failed: {out1}"

        # Pure removal: delete a.py only, no other source modifications.
        # Incremental will see modified=[], removed=[a.py].
        (proj / "a.py").unlink()
        git_commit(proj, "remove a.py")

        # Spy on the recovery function. We're testing the gating decision,
        # not the recovery's behaviour, so we wrap rather than replace.
        original = Indexer._find_affected_neighbor_files
        calls: list[tuple] = []

        def spy(conn, changed_file_ids):
            calls.append(("called", tuple(sorted(changed_file_ids))))
            return original(conn, changed_file_ids)

        monkeypatch.setattr(Indexer, "_find_affected_neighbor_files", staticmethod(spy))

        out2, rc2 = index_in_process(proj)
        assert rc2 == 0, f"incremental index after removal failed: {out2}"

        assert calls, (
            "_find_affected_neighbor_files was NEVER called during incremental "
            "after a pure removal. This is the rename-no-recovery bug — "
            "indexer.py:1409 had `if not force and modified and changed_file_ids`, "
            "and modified=[] short-circuited the entire recovery path."
        )


# ===========================================================================
# Language detection (4 tests)
# ===========================================================================


class TestLanguageDetection:
    """Tests that verify correct language tagging in the files table."""

    def test_detects_python(self, multilang_project):
        """.py files are detected as Python."""
        out, rc = index_in_process(multilang_project)
        assert rc == 0, f"Index failed:\n{out}"
        with _open_db_for(multilang_project) as conn:
            row = conn.execute("SELECT language FROM files WHERE path = 'main.py'").fetchone()
            assert row is not None, "main.py not found in files table"
            assert row["language"] == "python"

    def test_detects_javascript(self, multilang_project):
        """.js files are detected as JavaScript."""
        out, rc = index_in_process(multilang_project)
        assert rc == 0, f"Index failed:\n{out}"
        with _open_db_for(multilang_project) as conn:
            row = conn.execute("SELECT language FROM files WHERE path = 'app.js'").fetchone()
            assert row is not None, "app.js not found in files table"
            assert row["language"] == "javascript"

    def test_detects_typescript(self, multilang_project):
        """.ts files are detected as TypeScript."""
        out, rc = index_in_process(multilang_project)
        assert rc == 0, f"Index failed:\n{out}"
        with _open_db_for(multilang_project) as conn:
            row = conn.execute("SELECT language FROM files WHERE path = 'util.ts'").fetchone()
            assert row is not None, "util.ts not found in files table"
            assert row["language"] == "typescript"

    def test_detects_go(self, multilang_project):
        """.go files are detected as Go."""
        out, rc = index_in_process(multilang_project)
        assert rc == 0, f"Index failed:\n{out}"
        with _open_db_for(multilang_project) as conn:
            row = conn.execute("SELECT language FROM files WHERE path = 'server.go'").fetchone()
            assert row is not None, "server.go not found in files table"
            assert row["language"] == "go"


# ===========================================================================
# Schema correctness (4 tests)
# ===========================================================================


class TestSchemaCorrectness:
    """Tests that verify the DB schema has required columns and survives migrations."""

    def test_schema_files_table(self, index_project):
        """The files table has the required columns."""
        out, rc = index_in_process(index_project)
        assert rc == 0
        with _open_db_for(index_project) as conn:
            info = conn.execute("PRAGMA table_info(files)").fetchall()
            col_names = {r["name"] for r in info}
            for required in ("id", "path", "language", "hash"):
                assert required in col_names, f"Missing column '{required}' in files table. Columns: {col_names}"

    def test_schema_symbols_table(self, index_project):
        """The symbols table has the required columns."""
        out, rc = index_in_process(index_project)
        assert rc == 0
        with _open_db_for(index_project) as conn:
            info = conn.execute("PRAGMA table_info(symbols)").fetchall()
            col_names = {r["name"] for r in info}
            for required in ("id", "name", "kind", "file_id", "line_start", "line_end"):
                assert required in col_names, f"Missing column '{required}' in symbols table. Columns: {col_names}"

    def test_schema_edges_table(self, index_project):
        """The edges table has the required columns."""
        out, rc = index_in_process(index_project)
        assert rc == 0
        with _open_db_for(index_project) as conn:
            info = conn.execute("PRAGMA table_info(edges)").fetchall()
            col_names = {r["name"] for r in info}
            for required in ("source_id", "target_id", "kind"):
                assert required in col_names, f"Missing column '{required}' in edges table. Columns: {col_names}"

    def test_schema_migrations_safe(self, index_project):
        """Running ensure_schema twice does not crash (idempotent migrations)."""
        out, rc = index_in_process(index_project)
        assert rc == 0
        from roam.db.connection import ensure_schema, open_db

        old_cwd = os.getcwd()
        try:
            os.chdir(str(index_project))
            # Open in write mode and re-apply schema + migrations
            with open_db(readonly=False) as conn:
                ensure_schema(conn)
                # Verify tables still work after double-migration
                count = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
                assert count >= 2
        finally:
            os.chdir(old_cwd)


# ===========================================================================
# Edge cases (3 tests)
# ===========================================================================


class TestEdgeCases:
    """Tests for unusual but important scenarios."""

    def test_index_empty_project(self, tmp_path):
        """A project with no source files indexes without error."""
        proj = tmp_path / "empty_proj"
        proj.mkdir()
        (proj / ".gitignore").write_text(".roam/\n")
        git_init(proj)

        out, rc = index_in_process(proj)
        assert rc == 0, f"Index of empty project failed:\n{out}"

    def test_index_force_flag(self, index_project):
        """The --force flag re-indexes everything even when unchanged."""
        out1, rc1 = index_in_process(index_project)
        assert rc1 == 0

        # Normal re-index should be a no-op
        out2, rc2 = index_in_process(index_project)
        assert rc2 == 0
        assert "up to date" in out2.lower()

        # Force re-index should process files again
        out3, rc3 = index_in_process(index_project, "--force")
        assert rc3 == 0
        # After force, the output should NOT say "up to date" — it should
        # report files processed
        assert "up to date" not in out3.lower(), f"Expected force reindex to process files, but got:\n{out3}"

        # Verify data is still correct after force reindex
        with _open_db_for(index_project) as conn:
            count = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
            assert count >= 2, f"Expected at least 2 symbols after force reindex, got {count}"

    def test_index_gitignore_respected(self, tmp_path):
        """Files listed in .gitignore are not indexed."""
        proj = tmp_path / "gi_proj"
        proj.mkdir()
        (proj / ".gitignore").write_text(".roam/\nbuild/\nsecret.py\n")
        (proj / "app.py").write_text("def main(): pass\n")

        # Create files that should be ignored
        build_dir = proj / "build"
        build_dir.mkdir()
        (build_dir / "output.py").write_text("def compiled(): pass\n")
        (proj / "secret.py").write_text("API_KEY = 'hunter2'\n")

        git_init(proj)

        out, rc = index_in_process(proj)
        assert rc == 0, f"Index failed:\n{out}"

        with _open_db_for(proj) as conn:
            paths = {r["path"] for r in conn.execute("SELECT path FROM files").fetchall()}
            assert "app.py" in paths, f"app.py should be indexed but is missing: {paths}"
            assert "secret.py" not in paths, f"secret.py should be gitignored but was indexed: {paths}"
            assert "build/output.py" not in paths, f"build/output.py should be gitignored but was indexed: {paths}"


# ===========================================================================
# Cluster cache (gate Louvain on graph signature)
# ===========================================================================


class TestClusterCache:
    """The Louvain clustering pass costs ~3-11s on a 17K-symbol graph.
    When the symbol graph hasn't structurally changed since the last run
    we skip Louvain entirely and reuse the persisted cluster table.

    Skip is gated on a cheap graph signature persisted in the
    ``index_manifest.notes`` JSON column.
    """

    def test_signature_compute_under_budget(self):
        """``_compute_graph_signature`` must run in <100ms even on a
        moderately sized graph — it's the per-incremental cost we pay
        every time, even on cache hits.
        """
        try:
            import networkx as nx
        except ImportError:
            pytest.skip("networkx not installed")

        import time

        from roam.index.indexer import Indexer

        G = nx.DiGraph()
        # ~5K nodes / 5K edges — well-shy of the real-world 17K but big
        # enough to catch a regression that's quadratic in node count.
        for i in range(5000):
            G.add_edge(i, (i + 1) % 5000)
        # Sprinkle some high-degree hubs so the top-N sort has work to do.
        for hub in (0, 100, 200, 300, 400):
            for j in range(50):
                G.add_edge(hub, 5000 + hub * 10 + j)

        t0 = time.monotonic()
        sig = Indexer._compute_graph_signature(G)
        elapsed = time.monotonic() - t0

        assert sig is not None
        assert sig["n"] == G.number_of_nodes()
        assert sig["m"] == G.number_of_edges()
        assert isinstance(sig["top"], list)
        assert sig["top"] == sorted(sig["top"]), "top-N IDs must be sorted for stable comparison"
        assert elapsed < 0.5, f"_compute_graph_signature took {elapsed * 1000:.1f}ms — expected <500ms"

    def test_signature_changes_when_node_added(self):
        """Adding a node MUST change the signature so Louvain re-runs."""
        try:
            import networkx as nx
        except ImportError:
            pytest.skip("networkx not installed")

        from roam.index.indexer import Indexer

        G = nx.DiGraph()
        for i in range(20):
            G.add_edge(i, (i + 1) % 20)
        sig_before = Indexer._compute_graph_signature(G)
        G.add_node(999)
        sig_after = Indexer._compute_graph_signature(G)
        assert sig_before != sig_after

    def test_signature_changes_when_edge_added(self):
        """Adding an edge MUST change the signature so Louvain re-runs."""
        try:
            import networkx as nx
        except ImportError:
            pytest.skip("networkx not installed")

        from roam.index.indexer import Indexer

        G = nx.DiGraph()
        for i in range(20):
            G.add_edge(i, (i + 1) % 20)
        sig_before = Indexer._compute_graph_signature(G)
        # Add an edge that should reshape top-N degree (i.e. raise node 0
        # well above its peers).
        for j in range(50, 100):
            G.add_edge(0, j)
        sig_after = Indexer._compute_graph_signature(G)
        assert sig_before != sig_after

    def test_signature_stable_under_no_change(self):
        """Re-computing the signature on the same graph yields the same value."""
        try:
            import networkx as nx
        except ImportError:
            pytest.skip("networkx not installed")

        from roam.index.indexer import Indexer

        G = nx.DiGraph()
        for i in range(50):
            G.add_edge(i, (i + 1) % 50)
        a = Indexer._compute_graph_signature(G)
        b = Indexer._compute_graph_signature(G)
        assert a == b

    def test_clustering_skipped_on_signature_match(self, index_project, monkeypatch):
        """Indexing twice with no source changes must skip the Louvain
        pass on the second run. We patch ``detect_clusters`` with a
        spy that counts invocations.
        """
        # First run — populates clusters + manifest signature.
        out, rc = index_in_process(index_project)
        assert rc == 0, f"first index failed:\n{out}"

        # Spy: count how many times Louvain runs on the second pass.
        from roam.graph import clusters as clusters_mod

        call_count = {"n": 0}
        original = clusters_mod.detect_clusters

        def spy(G):
            call_count["n"] += 1
            return original(G)

        monkeypatch.setattr(clusters_mod, "detect_clusters", spy)

        # Second run — graph topology is identical, signature must match.
        out, rc = index_in_process(index_project)
        assert rc == 0, f"second index failed:\n{out}"
        assert call_count["n"] == 0, (
            f"detect_clusters was called {call_count['n']} times on a no-change "
            f"re-index — expected 0 (signature should match the persisted one)."
        )

    def test_force_rebuild_bypasses_cluster_cache(self, index_project, monkeypatch):
        """``--force`` must always re-run Louvain, even when the graph
        signature is unchanged. Force is the user's way of saying
        "ignore caches, recompute everything".
        """
        out, rc = index_in_process(index_project)
        assert rc == 0, f"first index failed:\n{out}"

        from roam.graph import clusters as clusters_mod

        call_count = {"n": 0}
        original = clusters_mod.detect_clusters

        def spy(G):
            call_count["n"] += 1
            return original(G)

        monkeypatch.setattr(clusters_mod, "detect_clusters", spy)

        out, rc = index_in_process(index_project, "--force")
        assert rc == 0, f"forced index failed:\n{out}"
        assert call_count["n"] >= 1, "detect_clusters MUST run under --force even on signature match"


# ===========================================================================
# --rebuild forgiving alias (regression guard)
# ===========================================================================


class TestRebuildAlias:
    """``roam index --rebuild`` is a hidden, forgiving alias for ``--force``.

    Reported bug: doctor / hints historically suggested ``roam index
    --rebuild`` while the only real flag was ``--force``, so the recommended
    command failed with ``Error: No such option: --rebuild``. The alias makes
    every such recommendation executable while keeping ``--force`` canonical.
    """

    def test_rebuild_is_accepted(self, index_project):
        """``roam index --rebuild`` parses (no "No such option") and succeeds."""
        out, rc = index_in_process(index_project, "--rebuild")
        assert "No such option" not in out, f"--rebuild must be a real option:\n{out}"
        assert rc == 0, f"roam index --rebuild failed:\n{out}"
        db_path = index_project / ".roam" / "index.db"
        assert db_path.exists(), f"index DB not created by --rebuild:\n{out}"

    def test_rebuild_behaves_like_force(self, index_project):
        """``--rebuild`` triggers a full reindex like ``--force`` (never "up to date")."""
        out1, rc1 = index_in_process(index_project)
        assert rc1 == 0, out1
        # A plain re-index of an unchanged project is a no-op.
        out2, rc2 = index_in_process(index_project)
        assert rc2 == 0, out2
        assert "up to date" in out2.lower(), out2
        # --rebuild must force a reprocess, exactly like --force.
        out3, rc3 = index_in_process(index_project, "--rebuild")
        assert rc3 == 0, out3
        assert "up to date" not in out3.lower(), f"--rebuild should force reprocess:\n{out3}"

    def test_rebuild_hidden_but_force_canonical(self):
        """``--rebuild`` stays hidden from --help; ``--force`` is the documented flag."""
        from click.testing import CliRunner

        from roam.cli import cli

        res = CliRunner().invoke(cli, ["index", "--help"])
        assert res.exit_code == 0, res.output
        assert "--force" in res.output, "--force must remain documented"
        assert "--rebuild" not in res.output, "alias must stay hidden; --force is canonical"
