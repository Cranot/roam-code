"""Proves N+1 batching fixes by counting actual SQL queries.

Two functions in the codebase were flagged by ``roam math`` as N+1
patterns when roam was run against itself:

* ``critique.checks.find_changed_symbols`` — was running 2 queries per
  changed file (one to resolve file_id, one to fetch candidate symbols).
* ``commands.cmd_n1.analyze_n1`` — was running multiple per-model
  queries inside the model-iteration loop.

After the fix, both should run a constant (or near-constant) number of
queries regardless of input size.  These tests pin that property by
intercepting ``conn.execute`` and counting calls.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init  # noqa: E402  fixture utilities

# ---------------------------------------------------------------------------
# Counting wrapper
# ---------------------------------------------------------------------------


class CountingConn:
    """Thin wrapper that proxies to a real sqlite3 connection and counts
    how many times ``execute`` is invoked.  Other attribute access falls
    through to the wrapped connection so existing call sites keep working
    (``conn.row_factory``, ``conn.close()``, etc.).
    """

    def __init__(self, real_conn: sqlite3.Connection) -> None:
        self._conn = real_conn
        self.execute_calls = 0
        self.queries: list[str] = []

    def execute(self, sql, *args, **kwargs):
        self.execute_calls += 1
        # Keep a short tag for debugging; full SQL would balloon test logs.
        normalised = " ".join(sql.split())[:80]
        self.queries.append(normalised)
        return self._conn.execute(sql, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._conn, name)


# ---------------------------------------------------------------------------
# Fixture: a small indexed repo with several files + symbols
# ---------------------------------------------------------------------------


@pytest.fixture
def small_indexed_repo(tmp_path):
    """Create a tiny git repo with 10 source files, index it, return the
    DB path and a list of changed-region paths the test can use."""
    proj = tmp_path / "n1_fixture"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    src = proj / "src"
    src.mkdir()

    paths: list[str] = []
    for i in range(10):
        rel = f"src/mod_{i}.py"
        body = (
            f"def fn_a_{i}():\n    return {i}\n\n\n"
            f"def fn_b_{i}(x):\n    return x + {i}\n\n\n"
            f"def fn_c_{i}(x, y):\n    return x * y + {i}\n"
        )
        (proj / rel).write_text(body, encoding="utf-8")
        paths.append(rel.replace(os.sep, "/"))

    git_init(proj)

    # Index via the CLI runner so we use the real indexing path.
    from click.testing import CliRunner

    from roam.cli import cli

    runner = CliRunner()
    cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        result = runner.invoke(cli, ["index"], catch_exceptions=False)
        assert result.exit_code == 0, f"index failed: {result.output}"
    finally:
        os.chdir(cwd)

    db_path = proj / ".roam" / "index.db"
    assert db_path.exists(), f"index DB missing at {db_path}"
    return db_path, paths


# ---------------------------------------------------------------------------
# Test 1 — find_changed_symbols
# ---------------------------------------------------------------------------


def _make_regions(paths, hunks_per_file=1):
    from roam.critique.checks import ChangedRegion

    regions = []
    for p in paths:
        hunks = tuple((1 + i * 2, 2) for i in range(hunks_per_file))
        regions.append(
            ChangedRegion(
                file_path=p,
                hunks=hunks,
                additions=hunks_per_file * 2,
                deletions=0,
            )
        )
    return regions


@pytest.mark.parametrize("n_files", [1, 5, 10])
def test_find_changed_symbols_constant_query_count(small_indexed_repo, n_files):
    """Query count should be ~constant in n_files, not linear."""
    db_path, paths = small_indexed_repo
    from roam.critique.checks import find_changed_symbols

    real = sqlite3.connect(str(db_path))
    real.row_factory = sqlite3.Row
    cc = CountingConn(real)
    try:
        regions = _make_regions(paths[:n_files])
        result = find_changed_symbols(cc, regions)
    finally:
        real.close()

    # Every region had a hunk that overlaps multiple symbols, so we
    # should get back at least one match per region.
    assert len(result) >= n_files, f"expected at least {n_files} matches, got {len(result)}\nresult: {result}"

    # The fix runs:
    #   * 1 batched IN for path → file_id  (or up to 1 follow-up suffix
    #     fallback per unresolved path; usually 0 in this fixture)
    #   * 1 batched IN for symbols by file_id
    # plus an unavoidable handful of bookkeeping queries from the
    # ``batched_in`` helper. Floor of 2 per call regardless of n_files.
    # Cap at 6 — well under the 2*n_files the old code would emit.
    assert cc.execute_calls <= 6, (
        f"find_changed_symbols issued {cc.execute_calls} queries for "
        f"{n_files} regions — expected <= 6 (constant in n).\n"
        f"queries: {cc.queries}"
    )


def test_find_changed_symbols_query_count_not_linear(small_indexed_repo):
    """Bigger regression: 1 region vs 10 regions should NOT see 10x query growth."""
    db_path, paths = small_indexed_repo
    from roam.critique.checks import find_changed_symbols

    def count_for(n):
        real = sqlite3.connect(str(db_path))
        real.row_factory = sqlite3.Row
        cc = CountingConn(real)
        try:
            find_changed_symbols(cc, _make_regions(paths[:n]))
        finally:
            real.close()
        return cc.execute_calls

    one = count_for(1)
    ten = count_for(10)
    # Old code: 2 queries per region → 2 vs 20.
    # New code: ~2 queries total → 2 vs 2.
    # Allow some slack (suffix fallback or batched_in chunking) but reject
    # any solution that's still linear in n.
    assert ten <= one + 2, (
        f"query count grew from {one} to {ten} as regions went 1→10 — expected near-constant. New code should batch."
    )


def test_find_changed_symbols_empty_regions_no_queries():
    """No regions → no DB work."""
    from roam.critique.checks import find_changed_symbols

    real = sqlite3.connect(":memory:")
    real.row_factory = sqlite3.Row
    cc = CountingConn(real)
    try:
        result = find_changed_symbols(cc, [])
    finally:
        real.close()
    assert result == []
    assert cc.execute_calls == 0


# ---------------------------------------------------------------------------
# Test 2 — analyze_n1 model-method bulk fetch
# ---------------------------------------------------------------------------


def _seed_synthetic_models(conn, n_models):
    """Insert N synthetic class symbols + 3 methods each with parent_id link.

    Bypasses the framework-detection path (we patch ``_find_model_classes``
    to surface these directly), so the test exercises just the model-
    method bulk fetch we batched.
    """
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS files (
            id INTEGER PRIMARY KEY,
            path TEXT,
            language TEXT,
            file_role TEXT
        );
        CREATE TABLE IF NOT EXISTS symbols (
            id INTEGER PRIMARY KEY,
            file_id INTEGER,
            name TEXT,
            qualified_name TEXT,
            kind TEXT,
            line_start INTEGER,
            line_end INTEGER,
            parent_id INTEGER,
            signature TEXT,
            default_value TEXT
        );
        CREATE TABLE IF NOT EXISTS edges (
            id INTEGER PRIMARY KEY,
            source_id INTEGER,
            target_id INTEGER,
            kind TEXT
        );
        """
    )
    conn.execute("INSERT INTO files (id, path, language, file_role) VALUES (1, 'app/Models/M.php', 'php', 'source')")
    models = {}
    for i in range(n_models):
        class_id = 100 + i
        conn.execute(
            "INSERT INTO symbols (id, file_id, name, qualified_name, kind, line_start, line_end) "
            "VALUES (?, 1, ?, ?, 'class', ?, ?)",
            (class_id, f"Model{i}", f"App\\Models\\Model{i}", i * 100, i * 100 + 50),
        )
        # Three methods per model, parent_id-linked.
        for j in range(3):
            conn.execute(
                "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end, parent_id) "
                "VALUES (?, 1, ?, 'method', ?, ?, ?)",
                (class_id * 10 + j, f"method_{j}", i * 100 + j * 5, i * 100 + j * 5 + 3, class_id),
            )
        models[class_id] = {
            "id": class_id,
            "name": f"Model{i}",
            "qualified_name": f"App\\Models\\Model{i}",
            "kind": "class",
            "line_start": i * 100,
            "line_end": i * 100 + 50,
            "file_path": "app/Models/M.php",
            "file_id": 1,
        }
    conn.commit()
    return models


@pytest.mark.parametrize("n_models", [1, 5, 20])
def test_analyze_n1_model_methods_constant_query_count(monkeypatch, n_models):
    """The model-methods lookup should run as a single bulk fetch
    regardless of how many models we pass through.

    Patches ``_find_model_classes`` to return ``n_models`` synthetic
    classes and the side helpers to no-op, isolating the loop body
    we're measuring.
    """
    from roam.commands import cmd_n1

    real = sqlite3.connect(":memory:")
    real.row_factory = sqlite3.Row
    models = _seed_synthetic_models(real, n_models)

    # Stub the helpers — we're measuring the model-loop overhead, not
    # the per-accessor I/O tracing (which is a separate fix candidate).
    monkeypatch.setattr(cmd_n1, "_detect_framework", lambda _conn: "laravel")
    monkeypatch.setattr(cmd_n1, "_find_model_classes", lambda _conn: models)
    monkeypatch.setattr(cmd_n1, "_is_test_path", lambda _p: False)
    # Returning empty appended-properties short-circuits each model's
    # remaining work — the relevant query is already done by then.
    monkeypatch.setattr(cmd_n1, "_find_appends_properties", lambda *a, **k: [])

    cc = CountingConn(real)
    try:
        findings, framework = cmd_n1.analyze_n1(cc)
    finally:
        real.close()

    # No findings expected (we stubbed appends to empty), but the
    # function must have run all the way through every model.
    assert framework == "laravel"
    assert findings == []

    # The pre-loop bulk fetches issue a fixed number of batched_in
    # queries regardless of n_models:
    #   * 1 — _bulk_fetch_methods_with_locations
    #   * 1 — _bulk_fetch_methods_by_file (W86 follow-up; covers PHP
    #         gap-models whose methods lack parent_id linkage)
    #   * 1-2 — _bulk_fetch_appends_symbols (parent_id pass + optional
    #           file-range fallback when models lack parent_id-linked
    #           appends; this fixture's synthetic models have none)
    #   * 1 — _bulk_fetch_incoming_refs
    #   * 1 — _build_controller_cache (files-where-Controller scan)
    # Cap at 9 to absorb batched_in chunking under future expansion.
    # Old code: 1 per model for several helpers (linear in n_models).
    assert cc.execute_calls <= 9, (
        f"analyze_n1 ran {cc.execute_calls} queries for {n_models} models — "
        f"expected <= 9 (constant). Old code: linear in n_models.\n"
        f"queries: {cc.queries}"
    )


def test_analyze_n1_appends_collection_edges_constant_query_count(monkeypatch):
    """The follow-up bulk fetches (_bulk_fetch_appends_symbols,
    _bulk_fetch_incoming_refs, _bulk_fetch_accessor_edge_traces) must
    each issue a constant number of queries regardless of n_models.

    Compares 1-model run against 50-model run; total query count
    should not scale.
    """
    from roam.commands import cmd_n1

    def _run(n_models):
        real = sqlite3.connect(":memory:")
        real.row_factory = sqlite3.Row
        models = _seed_synthetic_models(real, n_models)
        monkeypatch.setattr(cmd_n1, "_detect_framework", lambda _conn: "laravel")
        monkeypatch.setattr(cmd_n1, "_find_model_classes", lambda _conn: models)
        monkeypatch.setattr(cmd_n1, "_is_test_path", lambda _p: False)
        monkeypatch.setattr(cmd_n1, "_find_appends_properties", lambda *a, **k: [])
        cc = CountingConn(real)
        try:
            cmd_n1.analyze_n1(cc)
        finally:
            real.close()
        return cc.execute_calls

    one = _run(1)
    fifty = _run(50)
    # Old code: ~5 per model = 5 vs 250.
    # New code: constant ~4-6.
    # Allow 3 queries of slack (batched_in chunking) but reject any
    # solution that scales with n_models.
    assert fifty <= one + 3, (
        f"query count grew from {one} to {fifty} as n_models went 1→50 — "
        f"expected near-constant. Bulk fetches should batch."
    )


def _seed_php_models_no_parent_id(conn, n_models, file_id_per_model=True):
    """Insert N synthetic class symbols + 3 methods each, **without**
    parent_id linkage (mimics the PHP parser path where the candidate-
    filter fallback at cmd_n1.py:1217 used to fire per model).

    Each class lives in its own file (the gap-model worst case: every
    model in a separate file_id, so the bulk-by-file fetch must batch
    them all into one query).
    """
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS files (
            id INTEGER PRIMARY KEY,
            path TEXT,
            language TEXT,
            file_role TEXT
        );
        CREATE TABLE IF NOT EXISTS symbols (
            id INTEGER PRIMARY KEY,
            file_id INTEGER,
            name TEXT,
            qualified_name TEXT,
            kind TEXT,
            line_start INTEGER,
            line_end INTEGER,
            parent_id INTEGER,
            signature TEXT,
            default_value TEXT
        );
        CREATE TABLE IF NOT EXISTS edges (
            id INTEGER PRIMARY KEY,
            source_id INTEGER,
            target_id INTEGER,
            kind TEXT
        );
        """
    )
    models = {}
    for i in range(n_models):
        file_id = (i + 1) if file_id_per_model else 1
        path = f"app/Models/Model{i}.php"
        # Insert the file row only once per unique file_id.
        existing = conn.execute("SELECT 1 FROM files WHERE id = ?", (file_id,)).fetchone()
        if not existing:
            conn.execute(
                "INSERT INTO files (id, path, language, file_role) VALUES (?, ?, 'php', 'source')",
                (file_id, path),
            )
        class_id = 1000 + i
        line_start = 1 if file_id_per_model else (i * 100)
        line_end = line_start + 50
        conn.execute(
            "INSERT INTO symbols (id, file_id, name, qualified_name, kind, line_start, line_end) "
            "VALUES (?, ?, ?, ?, 'class', ?, ?)",
            (class_id, file_id, f"Model{i}", f"App\\Models\\Model{i}", line_start, line_end),
        )
        # Methods WITHOUT parent_id — the file-range fallback path.
        for j in range(3):
            conn.execute(
                "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end, parent_id) "
                "VALUES (?, ?, ?, 'method', ?, ?, NULL)",
                (class_id * 10 + j, file_id, f"method_{j}", line_start + j * 5 + 1, line_start + j * 5 + 4),
            )
        models[class_id] = {
            "id": class_id,
            "name": f"Model{i}",
            "qualified_name": f"App\\Models\\Model{i}",
            "kind": "class",
            "line_start": line_start,
            "line_end": line_end,
            "file_path": path,
            "file_id": file_id,
        }
    conn.commit()
    return models


def test_bulk_fetch_methods_by_file_empty_input():
    """Helper must tolerate empty + all-None file_ids without querying."""
    from roam.commands import cmd_n1

    real = sqlite3.connect(":memory:")
    real.row_factory = sqlite3.Row
    real.executescript("CREATE TABLE symbols (id INTEGER, file_id INTEGER, name TEXT, kind TEXT, line_start INTEGER);")
    cc = CountingConn(real)
    try:
        assert cmd_n1._bulk_fetch_methods_by_file(cc, []) == {}
        assert cmd_n1._bulk_fetch_methods_by_file(cc, [None, None]) == {}
    finally:
        real.close()
    assert cc.execute_calls == 0


def test_candidate_filter_fallback_uses_bulk_fetch(monkeypatch):
    """When ``methods_by_model`` is empty for every model (PHP parser
    path: no parent_id), ``analyze_n1`` must satisfy the fallback via a
    single bulk-by-file fetch — NOT one SELECT per gap-model.

    Regression for W86 follow-up (B3.5).
    """
    from roam.commands import cmd_n1

    def _run(n_models):
        real = sqlite3.connect(":memory:")
        real.row_factory = sqlite3.Row
        models = _seed_php_models_no_parent_id(real, n_models)
        monkeypatch.setattr(cmd_n1, "_detect_framework", lambda _conn: "laravel")
        monkeypatch.setattr(cmd_n1, "_find_model_classes", lambda _conn: models)
        monkeypatch.setattr(cmd_n1, "_is_test_path", lambda _p: False)
        monkeypatch.setattr(cmd_n1, "_find_appends_properties", lambda *a, **k: [])
        cc = CountingConn(real)
        try:
            cmd_n1.analyze_n1(cc)
        finally:
            real.close()
        return cc.execute_calls, cc.queries

    one_count, _ = _run(1)
    fifty_count, fifty_queries = _run(50)

    # The fallback file-range SELECT must NOT appear once per model.
    # Old code emitted 1-2 of these per gap-model (one for file_id
    # resolution if missing, plus the BETWEEN scan). After the fix the
    # bulk-by-file fetch happens once, in-memory filter takes over.
    fallback_selects = [q for q in fifty_queries if "kind = 'method'" in q and "line_start >= ?" in q]
    assert len(fallback_selects) == 0, (
        f"Per-model file-range fallback SELECT fired {len(fallback_selects)} "
        f"times for 50 models — must be 0 (bulk-by-file owns this path now).\n"
        f"queries: {fifty_queries}"
    )

    # Constant-query-count assertion (matching the existing
    # appends/collection-edges test's slack budget).
    assert fifty_count <= one_count + 3, (
        f"analyze_n1 query count grew from {one_count} to {fifty_count} "
        f"as n_models went 1→50 with no parent_id linkage — expected "
        f"near-constant. The bulk-by-file fetch should absorb the gap.\n"
        f"queries (n=50): {fifty_queries}"
    )


# ---------------------------------------------------------------------------
# Test 3 — _find_colocated_tests bulk-fetch
# ---------------------------------------------------------------------------


def _seed_files_table(conn, n_files):
    """Seed an in-memory files table with a mix of source + test files
    spread across n_files / 5 directories. Returns the source file paths."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS files (
            id INTEGER PRIMARY KEY,
            path TEXT,
            language TEXT,
            file_role TEXT
        );
        """
    )
    src_paths: list[str] = []
    file_id = 1
    dirs_per_5 = max(1, n_files // 5)
    for i in range(dirs_per_5):
        d = f"src/pkg{i}"
        # Two source files + one test file per directory.
        for fname in (f"mod_a_{i}.py", f"mod_b_{i}.py"):
            p = f"{d}/{fname}"
            conn.execute(
                "INSERT INTO files(id, path, language, file_role) VALUES (?, ?, 'python', 'source')",
                (file_id, p),
            )
            src_paths.append(p)
            file_id += 1
        # Colocated test file in the same directory.
        test_path = f"{d}/test_mod_{i}.py"
        conn.execute(
            "INSERT INTO files(id, path, language, file_role) VALUES (?, ?, 'python', 'test')",
            (file_id, test_path),
        )
        file_id += 1
    conn.commit()
    return src_paths


@pytest.mark.parametrize("n_files", [2, 10, 30])
def test_find_colocated_tests_constant_query_count(n_files):
    """Query count should be ~constant in n_files, not linear.

    Pre-fix: per-dir LIKE query (M dirs) + per-file language lookup (N
    files) + per-candidate path existence check (N×C). Post-fix: a
    single ``SELECT path, language FROM files`` followed by in-memory
    set/dict lookups.
    """
    real = sqlite3.connect(":memory:")
    real.row_factory = sqlite3.Row
    src_paths = _seed_files_table(real, n_files)
    cc = CountingConn(real)
    try:
        from roam.commands.cmd_affected_tests import _find_colocated_tests

        result = _find_colocated_tests(cc, src_paths)
    finally:
        real.close()

    # Each fixture directory has a colocated test_mod_i.py — every input
    # source file's directory should yield at least one colocated test.
    assert result, (
        f"expected at least one colocated test for {len(src_paths)} source "
        f"files, got nothing. Either fixture is wrong or behaviour regressed."
    )

    # Single bulk fetch — anything more is a regression. Allow up to 2
    # queries to absorb potential future one-shot lookups (batched_in
    # chunking, etc.); the old code would emit O(N) queries.
    assert cc.execute_calls <= 2, (
        f"_find_colocated_tests ran {cc.execute_calls} queries for "
        f"{len(src_paths)} source files — expected <= 2 (constant in n).\n"
        f"queries: {cc.queries}"
    )


# ---------------------------------------------------------------------------
# Test 4 — _print_mega_detail bulk-fetch
# ---------------------------------------------------------------------------


def _seed_clusters_table(conn, n_clusters):
    """Seed clusters + symbols + files + graph_metrics so
    ``_print_mega_detail`` has something to iterate.
    """
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS files (id INTEGER PRIMARY KEY, path TEXT);
        CREATE TABLE IF NOT EXISTS symbols (
            id INTEGER PRIMARY KEY,
            file_id INTEGER,
            name TEXT,
            kind TEXT
        );
        CREATE TABLE IF NOT EXISTS clusters (
            symbol_id INTEGER,
            cluster_id INTEGER,
            cluster_label TEXT
        );
        CREATE TABLE IF NOT EXISTS graph_metrics (
            symbol_id INTEGER PRIMARY KEY,
            pagerank REAL
        );
        """
    )
    conn.execute("INSERT INTO files(id, path) VALUES (1, 'src/a.py')")
    sid = 1
    for cid in range(n_clusters):
        # 4 symbols per cluster
        for j in range(4):
            conn.execute(
                "INSERT INTO symbols(id, file_id, name, kind) VALUES (?, 1, ?, 'function')",
                (sid, f"fn_{cid}_{j}"),
            )
            conn.execute(
                "INSERT INTO clusters(symbol_id, cluster_id, cluster_label) VALUES (?, ?, ?)",
                (sid, cid, f"cluster_{cid}"),
            )
            conn.execute("INSERT INTO graph_metrics(symbol_id, pagerank) VALUES (?, ?)", (sid, 0.1))
            sid += 1
    conn.commit()


@pytest.mark.parametrize("n_clusters", [1, 5, 10])
def test_print_mega_detail_constant_query_count(n_clusters, capsys):
    """``_print_mega_detail`` must bulk-fetch all symbols for visible
    mega-clusters in one query, not one query per cluster.
    """
    real = sqlite3.connect(":memory:")
    real.row_factory = sqlite3.Row
    _seed_clusters_table(real, n_clusters)

    # Build the args _print_mega_detail expects.
    visible = [{"cluster_id": cid, "cluster_label": f"cluster_{cid}", "size": 4} for cid in range(n_clusters)]
    mega_ids = set(range(n_clusters))
    intra_count = {cid: 2 for cid in range(n_clusters)}
    total_count = {cid: 4 for cid in range(n_clusters)}
    edges: list = []

    cc = CountingConn(real)
    try:
        from roam.commands.cmd_clusters import _print_mega_detail

        _print_mega_detail(cc, visible, mega_ids, 4 * n_clusters, intra_count, total_count, 50.0, edges)
    finally:
        real.close()

    # Pre-fix: 1 query per visible mega cluster (linear).
    # Post-fix: 1 batched query total, regardless of cluster count.
    # Allow up to 2 to absorb batched_in's potential chunk handling.
    assert cc.execute_calls <= 2, (
        f"_print_mega_detail ran {cc.execute_calls} queries for "
        f"{n_clusters} mega clusters — expected <= 2 (constant).\n"
        f"queries: {cc.queries}"
    )


# ---------------------------------------------------------------------------
# Test 5 — _against_mode bulk-fetch
# ---------------------------------------------------------------------------


def _seed_cochange_table(conn, n_files):
    """Seed git_cochange + file_stats + git_commits + files for the
    coupling-against-mode test. n_files ≥ 2 — every adjacent pair of
    files gets one co-change row.
    """
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS files (id INTEGER PRIMARY KEY, path TEXT);
        CREATE TABLE IF NOT EXISTS file_stats (
            file_id INTEGER PRIMARY KEY,
            commit_count INTEGER
        );
        CREATE TABLE IF NOT EXISTS git_commits (
            sha TEXT PRIMARY KEY,
            author TEXT,
            ts INTEGER
        );
        CREATE TABLE IF NOT EXISTS git_cochange (
            file_id_a INTEGER,
            file_id_b INTEGER,
            cochange_count INTEGER
        );
        """
    )
    for i in range(1, n_files + 1):
        conn.execute("INSERT INTO files(id, path) VALUES (?, ?)", (i, f"src/mod_{i}.py"))
        conn.execute("INSERT INTO file_stats(file_id, commit_count) VALUES (?, 10)", (i,))
    # Pairwise co-change between consecutive files (1-2, 2-3, ...).
    for i in range(1, n_files):
        conn.execute(
            "INSERT INTO git_cochange(file_id_a, file_id_b, cochange_count) VALUES (?, ?, 5)",
            (i, i + 1),
        )
    conn.execute("INSERT INTO git_commits(sha, author, ts) VALUES ('abc', 'me', 0)")
    conn.commit()


@pytest.mark.parametrize("n_files", [2, 5, 15])
def test_against_mode_constant_query_count(n_files):
    """``_against_mode`` previously issued one ``SELECT FROM git_cochange``
    per file_map entry. Bulk-fetch must collapse that to ~1 query.
    """
    real = sqlite3.connect(":memory:")
    real.row_factory = sqlite3.Row
    _seed_cochange_table(real, n_files)

    file_map = {f"src/mod_{i}.py": i for i in range(1, n_files + 1)}
    change_fids = list(range(1, n_files + 1))

    cc = CountingConn(real)
    try:
        from roam.commands.cmd_coupling import _against_mode

        missing, included = _against_mode(cc, change_fids, file_map, min_strength=0.0, min_cochanges=1)
    finally:
        real.close()

    # _against_mode does:
    # 1. SELECT id, path FROM files (bookkeeping)
    # 2. SELECT file_id, commit_count FROM file_stats
    # 3. SELECT COUNT(*) FROM git_commits
    # 4. ONE batched SELECT FROM git_cochange (post-fix)
    #
    # That's 4 queries regardless of n_files. Old code added one
    # SELECT FROM git_cochange per fid in file_map → 4 + n_files.
    # Allow up to 5 to absorb batched_in chunking.
    assert cc.execute_calls <= 5, (
        f"_against_mode ran {cc.execute_calls} queries for {n_files} "
        f"files — expected <= 5 (constant). Old code: 4 + n_files.\n"
        f"queries: {cc.queries}"
    )


# ---------------------------------------------------------------------------
# Test 6 — cmd_context: get_blast_radius / get_affected_tests_bfs / get_coupling
# ---------------------------------------------------------------------------


def _seed_context_fixture(conn, n_chain=200):
    """Seed a long caller chain so the BFS helpers have real work to do.

    The pre-fix code issued one ``SELECT source_id ... WHERE target_id =
    ?`` per BFS pop — on a 200-edge chain that meant ~200 queries. The
    bulk-load fix issues exactly ONE query for the full reverse
    adjacency, regardless of chain length.

    Symbol layout (chain of length n_chain):
        sym_0 -> sym_1 -> sym_2 -> ... -> sym_{n-1}
    Plus a couple of co-change rows so ``get_coupling`` has partners.
    """
    conn.executescript(
        """
        CREATE TABLE files (
            id INTEGER PRIMARY KEY,
            path TEXT,
            language TEXT,
            file_role TEXT
        );
        CREATE TABLE symbols (
            id INTEGER PRIMARY KEY,
            file_id INTEGER,
            name TEXT,
            qualified_name TEXT,
            kind TEXT,
            line_start INTEGER,
            line_end INTEGER,
            parent_id INTEGER,
            signature TEXT,
            docstring TEXT,
            is_exported INTEGER DEFAULT 1,
            is_async INTEGER DEFAULT 0,
            decorators TEXT
        );
        CREATE TABLE edges (
            id INTEGER PRIMARY KEY,
            source_id INTEGER,
            target_id INTEGER,
            kind TEXT,
            line INTEGER
        );
        CREATE TABLE file_stats (
            file_id INTEGER PRIMARY KEY,
            commit_count INTEGER,
            total_churn INTEGER,
            distinct_authors INTEGER,
            cochange_entropy REAL
        );
        CREATE TABLE git_cochange (
            file_id_a INTEGER,
            file_id_b INTEGER,
            cochange_count INTEGER
        );
        CREATE TABLE symbol_metrics (
            symbol_id INTEGER PRIMARY KEY,
            cognitive_complexity INTEGER,
            nesting_depth INTEGER,
            param_count INTEGER,
            line_count INTEGER,
            return_count INTEGER,
            bool_op_count INTEGER,
            callback_depth INTEGER
        );
        CREATE TABLE graph_metrics (
            symbol_id INTEGER PRIMARY KEY,
            pagerank REAL,
            in_degree INTEGER,
            out_degree INTEGER,
            betweenness REAL
        );
        """
    )
    # n_chain files, each with one function. file_id == sym_id == i+1.
    for i in range(n_chain):
        conn.execute(
            "INSERT INTO files(id, path, language, file_role) VALUES (?, ?, 'python', 'source')",
            (i + 1, f"src/m_{i}.py"),
        )
        conn.execute(
            "INSERT INTO symbols(id, file_id, name, qualified_name, kind, line_start, line_end) "
            "VALUES (?, ?, ?, ?, 'function', 1, 5)",
            (
                i + 1,
                i + 1,
                f"sym_{i}",
                f"sym_{i}",
            ),
        )
        conn.execute(
            "INSERT INTO file_stats(file_id, commit_count) VALUES (?, ?)",
            (i + 1, 10 + (i % 5)),
        )

    # Caller chain: sym_0 (id=1) is called by sym_1, sym_1 by sym_2, ...
    # So get_blast_radius / get_affected_tests_bfs starting at sym_0
    # walks reverse edges through the entire chain.
    for i in range(n_chain - 1):
        conn.execute(
            "INSERT INTO edges(source_id, target_id, kind, line) VALUES (?, ?, 'call', 3)",
            (
                i + 2,
                i + 1,
            ),
        )

    # A couple of co-change rows so get_coupling has work.
    for i in range(min(5, n_chain - 1)):
        conn.execute(
            "INSERT INTO git_cochange(file_id_a, file_id_b, cochange_count) VALUES (1, ?, ?)",
            (i + 2, 5 - i),
        )
    conn.commit()


@pytest.mark.parametrize("n_chain", [10, 100, 200])
def test_get_blast_radius_constant_query_count(n_chain):
    """``get_blast_radius`` must bulk-load the reverse adjacency once
    instead of querying per BFS pop.

    Pre-fix: 1 ``SELECT source_id ... WHERE target_id = ?`` per visited
    node → linear in chain depth.
    Post-fix: 1 bulk ``SELECT source_id, target_id FROM edges`` + 1
    batched_in for dependent file paths → constant.
    """
    real = sqlite3.connect(":memory:")
    real.row_factory = sqlite3.Row
    _seed_context_fixture(real, n_chain=n_chain)

    cc = CountingConn(real)
    try:
        from roam.commands.context_helpers import get_blast_radius

        result = get_blast_radius(cc, sym_id=1)
    finally:
        real.close()

    # Sanity: every other symbol in the chain transitively depends on sym_0.
    assert result["dependent_symbols"] == n_chain - 1, (
        f"expected {n_chain - 1} dependents, got {result['dependent_symbols']}"
    )
    # Constant query budget: 1 bulk reverse-adj fetch + 1 batched_in.
    # Cap at 4 to absorb batched_in chunking. Old code: ~n_chain queries.
    assert cc.execute_calls <= 4, (
        f"get_blast_radius ran {cc.execute_calls} queries for chain of "
        f"{n_chain} — expected <= 4 (constant). Old code: linear in chain.\n"
        f"queries: {cc.queries}"
    )


@pytest.mark.parametrize("n_chain", [10, 100, 200])
def test_get_affected_tests_bfs_constant_query_count(n_chain):
    """``get_affected_tests_bfs`` must bulk-load reverse adjacency once
    rather than issue a query per visited node.

    Pre-fix: 1 ``SELECT source_id, name ... WHERE target_id = ?`` per
    pop. Post-fix: 1 bulk reverse-adj load + 1 batched_in for caller
    metadata.
    """
    real = sqlite3.connect(":memory:")
    real.row_factory = sqlite3.Row
    _seed_context_fixture(real, n_chain=n_chain)

    cc = CountingConn(real)
    try:
        from roam.commands.context_helpers import get_affected_tests_bfs

        # Use a deep enough max_hops that the BFS would walk the full
        # chain in the pre-fix code path.
        result = get_affected_tests_bfs(cc, sym_id=1, max_hops=n_chain + 5)
    finally:
        real.close()

    # No symbols are in test files in this fixture, so result is empty —
    # but the BFS must still have walked everyone for that to be true.
    assert result == []
    # Constant: 1 reverse-adj bulk fetch + 1 batched_in for symbol meta.
    # Cap at 4 to absorb batched_in chunking.
    assert cc.execute_calls <= 4, (
        f"get_affected_tests_bfs ran {cc.execute_calls} queries for chain "
        f"of {n_chain} — expected <= 4 (constant). Old code: linear.\n"
        f"queries: {cc.queries}"
    )


@pytest.mark.parametrize("n_partners", [1, 5, 10])
def test_get_coupling_constant_query_count(n_partners):
    """``get_coupling`` must batch the per-partner ``commit_count``
    lookup, not run one query per partner.

    Pre-fix: 1 partners query + 1 ``SELECT commit_count`` per partner
    inside the loop → 2 + n_partners.
    Post-fix: 1 partners query + 1 batched_in across all partners → 4
    queries total regardless of n_partners.
    """
    real = sqlite3.connect(":memory:")
    real.row_factory = sqlite3.Row
    # n_partners + 1 files so partner edges have somewhere to point.
    _seed_context_fixture(real, n_chain=n_partners + 1)

    cc = CountingConn(real)
    try:
        from roam.commands.context_helpers import get_coupling

        partners = get_coupling(cc, "src/m_0.py", limit=n_partners)
    finally:
        real.close()

    assert partners, "expected at least one coupling partner"
    # 4 queries: file_id resolve, file_stats fetch, partner select,
    # batched_in for partner stats. Allow up to 5 for chunking.
    assert cc.execute_calls <= 5, (
        f"get_coupling ran {cc.execute_calls} queries for {n_partners} "
        f"partners — expected <= 5 (constant). Old code: 2 + n_partners.\n"
        f"queries: {cc.queries}"
    )


def test_get_blast_radius_query_count_not_linear():
    """Bigger regression: 10-chain vs 200-chain should NOT see 20x query
    growth. Pin the bulk-load behaviour."""
    from roam.commands.context_helpers import get_blast_radius

    def count_for(n):
        real = sqlite3.connect(":memory:")
        real.row_factory = sqlite3.Row
        _seed_context_fixture(real, n_chain=n)
        cc = CountingConn(real)
        try:
            get_blast_radius(cc, sym_id=1)
        finally:
            real.close()
        return cc.execute_calls

    ten = count_for(10)
    two_hundred = count_for(200)
    assert two_hundred <= ten + 1, (
        f"query count grew from {ten} to {two_hundred} as chain went 10→200 — "
        f"expected near-constant. Bulk-load reverse adjacency."
    )


def test_cmd_context_total_under_50_queries():
    """End-to-end: invoking the helpers cmd_context calls in single-symbol
    mode must finish under the <50 query ceiling other hot commands hold.

    Pre-fix: ~1986 queries for a moderate-fan symbol (the BFS helpers
    queried per-pop and the coupling helper queried per-partner).
    Post-fix: ~30-40 queries.
    """
    real = sqlite3.connect(":memory:")
    real.row_factory = sqlite3.Row
    # Simulate a chain of 150 callers — exercises both BFS helpers in a
    # way that the old code would have blown out to 300+ queries on its
    # own.
    _seed_context_fixture(real, n_chain=150)

    sym = real.execute("SELECT * FROM symbols WHERE id = 1").fetchone()
    sym_dict = dict(sym)
    sym_dict["file_path"] = "src/m_0.py"

    cc = CountingConn(real)
    try:
        from roam.commands.context_helpers import (
            gather_annotations,
            get_affected_tests_bfs,
            get_blast_radius,
            get_coupling,
            get_file_churn,
            get_file_context,
            get_graph_metrics,
            get_similar_symbols,
            get_symbol_metrics,
        )

        # Mirror the calls _gather_single makes in cmd_context.py — minus
        # gather_symbol_context (which depends on graph_metrics &
        # file_edges that aren't seeded here) and a couple of helpers
        # that need extra schema tables. The two BFS helpers + coupling
        # are the dominant offenders we measured.
        gather_annotations(cc, sym=sym_dict)
        get_symbol_metrics(cc, sym_dict["id"])
        get_graph_metrics(cc, sym_dict["id"])
        get_file_churn(cc, sym_dict["file_path"])
        get_coupling(cc, sym_dict["file_path"], limit=10)
        get_affected_tests_bfs(cc, sym_dict["id"], max_hops=200)
        get_blast_radius(cc, sym_dict["id"])
        get_similar_symbols(cc, sym_dict, limit=10)
        get_file_context(cc, sym_dict["file_id"], sym_dict["id"])
    finally:
        real.close()

    assert cc.execute_calls < 50, (
        f"cmd_context helpers ran {cc.execute_calls} queries — expected "
        f"<50. Pre-fix baseline was ~1986 on the live roam DB.\n"
        f"queries (first 20): {cc.queries[:20]}"
    )


# ---------------------------------------------------------------------------
# Test 7 — cmd_attest._collect_risk test-coverage loop
# ---------------------------------------------------------------------------


def _seed_attest_coverage_fixture(conn, n_source_files):
    """Seed files + file_edges so the test-coverage loop in
    ``_collect_risk`` has work. For every source file we insert one
    test-file file_edge so the source ends up "covered" — this lets us
    assert behaviour preservation alongside query count.
    """
    conn.executescript(
        """
        CREATE TABLE files (
            id INTEGER PRIMARY KEY,
            path TEXT,
            language TEXT,
            file_role TEXT
        );
        CREATE TABLE file_edges (
            source_file_id INTEGER,
            target_file_id INTEGER,
            symbol_count INTEGER
        );
        """
    )
    file_map: dict = {}
    fid = 1
    for i in range(n_source_files):
        src_path = f"src/m_{i}.py"
        conn.execute(
            "INSERT INTO files(id, path, language, file_role) VALUES (?, ?, 'python', 'source')",
            (fid, src_path),
        )
        src_fid = fid
        file_map[src_path] = src_fid
        fid += 1
        # Colocated test file that imports the source.
        test_path = f"src/test_m_{i}.py"
        conn.execute(
            "INSERT INTO files(id, path, language, file_role) VALUES (?, ?, 'python', 'test')",
            (fid, test_path),
        )
        conn.execute(
            "INSERT INTO file_edges(source_file_id, target_file_id, symbol_count) VALUES (?, ?, 1)",
            (fid, src_fid),  # test_path imports src_path → file_edge src->tgt
        )
        fid += 1
    conn.commit()
    return file_map


def _run_attest_coverage(conn, file_map):
    """Re-implement the test-coverage block from ``_collect_risk`` in
    isolation so the test doesn't have to spin up the rest of the risk
    pipeline (networkx, file_stats, etc.). Mirrors the post-fix code in
    cmd_attest.py exactly.
    """
    from roam.commands.changed_files import is_test_file

    try:
        from roam.commands.changed_files import is_low_risk_file
    except ImportError:
        is_low_risk_file = lambda p: False  # noqa: E731

    from roam.db.connection import batched_in

    source_files = [p for p in file_map if not is_test_file(p) and not is_low_risk_file(p)]
    covered_files = 0
    if source_files:
        source_fids = [file_map[p] for p in source_files]
        rows = batched_in(
            conn,
            "SELECT fe.target_file_id, f.path FROM file_edges fe "
            "JOIN files f ON fe.source_file_id = f.id "
            "WHERE fe.target_file_id IN ({ph})",
            source_fids,
        )
        incoming_by_fid: dict = {}
        for r in rows:
            incoming_by_fid.setdefault(r["target_file_id"], []).append(r["path"])
        for path in source_files:
            fid = file_map[path]
            if any(is_test_file(p) for p in incoming_by_fid.get(fid, ())):
                covered_files += 1
    return covered_files, len(source_files)


@pytest.mark.parametrize("n_files", [1, 5, 50])
def test_attest_coverage_constant_query_count(n_files):
    """The test-coverage block in ``_collect_risk`` must bulk-fetch
    incoming file_edges in a single batched query. Pre-fix: 1 SELECT per
    source file (linear). Post-fix: 1 batched_in regardless of n_files.
    """
    real = sqlite3.connect(":memory:")
    real.row_factory = sqlite3.Row
    file_map = _seed_attest_coverage_fixture(real, n_files)

    cc = CountingConn(real)
    try:
        covered, total = _run_attest_coverage(cc, file_map)
    finally:
        real.close()

    # Behaviour preservation: every fixture source has a colocated test file.
    assert total == n_files, f"expected {n_files} source files, got {total}"
    assert covered == n_files, f"expected all {n_files} sources covered, got {covered}"

    # Constant query budget: 1 batched_in. Allow up to 2 to absorb
    # potential chunking under future growth. Old code: n_files queries.
    assert cc.execute_calls <= 2, (
        f"_collect_risk test-coverage loop ran {cc.execute_calls} queries for "
        f"{n_files} source files — expected <= 2 (constant in n).\n"
        f"queries: {cc.queries}"
    )


def test_attest_coverage_query_count_not_linear():
    """1-file vs 50-file should not see 50x query growth."""

    def count_for(n):
        real = sqlite3.connect(":memory:")
        real.row_factory = sqlite3.Row
        file_map = _seed_attest_coverage_fixture(real, n)
        cc = CountingConn(real)
        try:
            _run_attest_coverage(cc, file_map)
        finally:
            real.close()
        return cc.execute_calls

    one = count_for(1)
    fifty = count_for(50)
    assert fifty <= one + 1, (
        f"query count grew from {one} to {fifty} as n_files went 1→50 — "
        f"expected near-constant. The coverage loop must batch."
    )


# ---------------------------------------------------------------------------
# Test 8 — cmd_module: _module_deps + _collect_sym_ids
# ---------------------------------------------------------------------------


def _seed_module_fixture(conn, n_files, with_external=True):
    """Seed files + file_edges + symbols so ``_module_deps`` and
    ``_collect_sym_ids`` have work for a directory of n_files.

    Every file imports an *external* shared utility file (so
    imports_external should pick up exactly one entry post-bulk-fetch),
    and every file is imported by an *external* test consumer (so
    imported_by_external picks up one entry). Each module file gets 2
    symbols.
    """
    conn.executescript(
        """
        CREATE TABLE files (
            id INTEGER PRIMARY KEY,
            path TEXT,
            language TEXT,
            file_role TEXT
        );
        CREATE TABLE symbols (
            id INTEGER PRIMARY KEY,
            file_id INTEGER,
            name TEXT,
            qualified_name TEXT,
            kind TEXT,
            line_start INTEGER,
            line_end INTEGER,
            is_exported INTEGER DEFAULT 1
        );
        CREATE TABLE file_edges (
            source_file_id INTEGER,
            target_file_id INTEGER,
            symbol_count INTEGER
        );
        """
    )
    files = []
    sid = 1
    fid = 1
    # External "util" file (target of imports_external) and external
    # "consumer" file (source of imported_by_external).
    if with_external:
        conn.execute(
            "INSERT INTO files(id, path, language, file_role) VALUES (?, 'src/ext/util.py', 'python', 'source')",
            (1000,),
        )
        conn.execute(
            "INSERT INTO files(id, path, language, file_role) VALUES (?, 'src/ext/consumer.py', 'python', 'source')",
            (1001,),
        )
    for i in range(n_files):
        p = f"mymod/m_{i}.py"
        conn.execute(
            "INSERT INTO files(id, path, language, file_role) VALUES (?, ?, 'python', 'source')",
            (fid, p),
        )
        files.append({"id": fid, "path": p})
        # 2 symbols per file
        for j in range(2):
            conn.execute(
                "INSERT INTO symbols(id, file_id, name, qualified_name, kind, line_start, line_end) "
                "VALUES (?, ?, ?, ?, 'function', ?, ?)",
                (sid, fid, f"fn_{i}_{j}", f"mymod.m_{i}.fn_{j}", j * 10, j * 10 + 5),
            )
            sid += 1
        if with_external:
            # m_i imports util once
            conn.execute(
                "INSERT INTO file_edges(source_file_id, target_file_id, symbol_count) VALUES (?, 1000, 2)",
                (fid,),
            )
            # consumer imports m_i once
            conn.execute(
                "INSERT INTO file_edges(source_file_id, target_file_id, symbol_count) VALUES (1001, ?, 1)",
                (fid,),
            )
        fid += 1
    conn.commit()
    return files


@pytest.mark.parametrize("n_files", [1, 5, 50])
def test_module_deps_constant_query_count(n_files):
    """``_module_deps`` must bulk-fetch imports + imported_by in two
    batched queries regardless of n_files. Pre-fix: 2 SELECT per file
    (linear). Post-fix: 2 batched_in total.
    """
    real = sqlite3.connect(":memory:")
    real.row_factory = sqlite3.Row
    files = _seed_module_fixture(real, n_files)

    cc = CountingConn(real)
    try:
        from roam.commands.cmd_module import _module_deps

        file_ids = [f["id"] for f in files]
        imports_external, imported_by_external = _module_deps(cc, file_ids)
    finally:
        real.close()

    # Behaviour preservation: each module file imports util.py (one
    # external import target with summed symbol_count = 2 * n_files), and
    # consumer.py imports each module file (one external importer with
    # summed symbol_count = 1 * n_files).
    assert "src/ext/util.py" in imports_external, f"expected util.py in external imports, got {list(imports_external)}"
    assert imports_external["src/ext/util.py"] == 2 * n_files, (
        f"expected sum 2*{n_files} for util.py, got {imports_external['src/ext/util.py']}"
    )
    assert "src/ext/consumer.py" in imported_by_external
    assert imported_by_external["src/ext/consumer.py"] == 1 * n_files

    # Constant query budget: 2 batched_in. Allow up to 4 to absorb
    # chunking. Old code: 2*n_files.
    assert cc.execute_calls <= 4, (
        f"_module_deps ran {cc.execute_calls} queries for {n_files} files — "
        f"expected <= 4 (constant in n). Old code: 2*n_files.\n"
        f"queries: {cc.queries}"
    )


@pytest.mark.parametrize("n_files", [1, 5, 50])
def test_collect_sym_ids_constant_query_count(n_files):
    """``_collect_sym_ids`` must bulk-fetch all symbol IDs in one
    batched query. Pre-fix: 1 SELECT per file. Post-fix: 1 batched_in.
    """
    real = sqlite3.connect(":memory:")
    real.row_factory = sqlite3.Row
    files = _seed_module_fixture(real, n_files, with_external=False)

    cc = CountingConn(real)
    try:
        from roam.commands.cmd_module import _collect_sym_ids

        sym_ids = _collect_sym_ids(cc, files)
    finally:
        real.close()

    # Behaviour preservation: 2 symbols per file.
    assert len(sym_ids) == 2 * n_files, f"expected {2 * n_files} symbol ids, got {len(sym_ids)}"
    # Constant: 1 batched_in. Allow up to 2.
    assert cc.execute_calls <= 2, (
        f"_collect_sym_ids ran {cc.execute_calls} queries for {n_files} files — "
        f"expected <= 2 (constant in n). Old code: n_files queries.\n"
        f"queries: {cc.queries}"
    )


def test_module_deps_query_count_not_linear():
    """1-file vs 50-file: query count must not scale linearly."""

    def count_for(n):
        real = sqlite3.connect(":memory:")
        real.row_factory = sqlite3.Row
        files = _seed_module_fixture(real, n)
        cc = CountingConn(real)
        try:
            from roam.commands.cmd_module import _collect_sym_ids, _module_deps

            file_ids = [f["id"] for f in files]
            _module_deps(cc, file_ids)
            _collect_sym_ids(cc, files)
        finally:
            real.close()
        return cc.execute_calls

    one = count_for(1)
    fifty = count_for(50)
    # Old code: 3*n_files (2 in _module_deps + 1 in _collect_sym_ids).
    # New code: 3 batched queries total.
    assert fifty <= one + 1, (
        f"query count grew from {one} to {fifty} as n_files went 1→50 — "
        f"expected near-constant. Both helpers must batch."
    )


# ---------------------------------------------------------------------------
# Test 9 — cmd_dead._predict_extinction BFS
# ---------------------------------------------------------------------------


def _seed_extinction_fixture(conn, n_chain):
    """Seed a linear caller chain so ``_predict_extinction`` walks every
    node when the leaf is "deleted":

        sym_0 (target) <- sym_1 <- sym_2 <- ... <- sym_{n-1}

    Each sym_i (i ≥ 1) calls ONLY sym_{i-1}, so removing sym_0 cascades
    all the way to sym_{n-1}. This exercises both the BFS reverse-edge
    walk AND the per-orphan info lookup.
    """
    conn.executescript(
        """
        CREATE TABLE files (
            id INTEGER PRIMARY KEY,
            path TEXT,
            language TEXT,
            file_role TEXT
        );
        CREATE TABLE symbols (
            id INTEGER PRIMARY KEY,
            file_id INTEGER,
            name TEXT,
            qualified_name TEXT,
            kind TEXT,
            line_start INTEGER,
            line_end INTEGER,
            parent_id INTEGER,
            signature TEXT,
            docstring TEXT,
            is_exported INTEGER DEFAULT 1
        );
        CREATE TABLE edges (
            id INTEGER PRIMARY KEY,
            source_id INTEGER,
            target_id INTEGER,
            kind TEXT,
            line INTEGER
        );
        """
    )
    for i in range(n_chain):
        conn.execute(
            "INSERT INTO files(id, path, language, file_role) VALUES (?, ?, 'python', 'source')",
            (i + 1, f"src/m_{i}.py"),
        )
        conn.execute(
            "INSERT INTO symbols(id, file_id, name, qualified_name, kind, line_start, line_end) "
            "VALUES (?, ?, ?, ?, 'function', 1, 5)",
            (i + 1, i + 1, f"sym_{i}", f"sym_{i}"),
        )
    # i+1 calls i (so target_id=i, source_id=i+1)
    for i in range(n_chain - 1):
        conn.execute(
            "INSERT INTO edges(source_id, target_id, kind, line) VALUES (?, ?, 'call', 3)",
            (i + 2, i + 1),
        )
    conn.commit()


@pytest.mark.parametrize("n_chain", [10, 50, 200])
def test_predict_extinction_constant_query_count(n_chain, monkeypatch):
    """``_predict_extinction`` must bulk-load the full edge adjacency in
    one query (not query-per-BFS-pop). Pre-fix: 1 SELECT per pop +
    1 batched_count per visited caller + 1 SELECT per orphan info →
    O(n_chain) queries. Post-fix: 2 queries total (1 edges scan + 1
    batched_in for cascade info).
    """
    real = sqlite3.connect(":memory:")
    real.row_factory = sqlite3.Row
    _seed_extinction_fixture(real, n_chain)

    # Stub out find_symbol so we don't need the heavy resolve module's
    # full schema (graph_metrics/file_edges/etc. would otherwise be
    # required for the FTS / fuzzy fallbacks).
    target_sym = dict(real.execute("SELECT * FROM symbols WHERE id = 1").fetchone())
    from roam.commands import resolve as resolve_mod

    monkeypatch.setattr(resolve_mod, "find_symbol", lambda _conn, _name: target_sym)

    cc = CountingConn(real)
    try:
        from roam.commands.cmd_dead import _predict_extinction

        sym, cascade = _predict_extinction(cc, "sym_0")
    finally:
        real.close()

    # Behaviour preservation: removing sym_0 orphans the entire chain
    # except sym_0 itself (which is the deletion target, not in cascade).
    assert sym is not None
    assert sym["id"] == 1
    assert len(cascade) == n_chain - 1, (
        f"expected cascade of {n_chain - 1} (whole chain minus target), got {len(cascade)}"
    )
    # Cascade items should each have name/kind/location/reason.
    for item in cascade:
        assert "name" in item and "location" in item and item["reason"] == "only callees removed"

    # Constant query budget: 1 edges scan + 1 batched_in for info. Allow
    # up to 4 to absorb find_symbol-side bookkeeping under future
    # refactoring. Old code: linear in n_chain (one SELECT per pop +
    # batched_count per caller + info SELECT per orphan).
    assert cc.execute_calls <= 4, (
        f"_predict_extinction ran {cc.execute_calls} queries for chain "
        f"of {n_chain} — expected <= 4 (constant). Old code: linear in chain.\n"
        f"queries (first 10): {cc.queries[:10]}"
    )


def test_predict_extinction_query_count_not_linear(monkeypatch):
    """10-chain vs 200-chain: query count must NOT scale 20x."""

    def count_for(n):
        real = sqlite3.connect(":memory:")
        real.row_factory = sqlite3.Row
        _seed_extinction_fixture(real, n)
        target_sym = dict(real.execute("SELECT * FROM symbols WHERE id = 1").fetchone())
        from roam.commands import resolve as resolve_mod

        monkeypatch.setattr(resolve_mod, "find_symbol", lambda _conn, _name: target_sym)

        cc = CountingConn(real)
        try:
            from roam.commands.cmd_dead import _predict_extinction

            _predict_extinction(cc, "sym_0")
        finally:
            real.close()
        return cc.execute_calls

    ten = count_for(10)
    two_hundred = count_for(200)
    assert two_hundred <= ten + 1, (
        f"query count grew from {ten} to {two_hundred} as chain went 10→200 — "
        f"expected near-constant. Bulk-load adjacency once."
    )


# ---------------------------------------------------------------------------
# Regression sentinel — ROADMAP B2 controller-file read cache
#
# ``_find_eager_loads`` used to re-read every Laravel controller file once
# per (model, controller) pair. ``analyze_n1`` now builds the cache once
# via ``_build_controller_cache`` and threads ``controller_cache=`` into
# every per-model call. These tests pin that invariant so a future
# refactor can't silently regress to per-call ``read_text``.
# ---------------------------------------------------------------------------


def test_find_eager_loads_with_cache_does_not_read_disk(monkeypatch):
    """When ``controller_cache`` is provided, ``_find_eager_loads`` must
    NOT call ``Path.read_text`` for any controller file — the cache is
    the authoritative source.

    Old (pre-B2) code re-read every controller from disk per model.
    """
    from pathlib import Path as RealPath

    from roam.commands import cmd_n1

    real = sqlite3.connect(":memory:")
    real.row_factory = sqlite3.Row
    # Empty schema — _find_eager_loads only needs ``symbols``/``files`` to
    # answer the $with + resources.php queries (both will return empty,
    # which is fine for this test). The controller-loop path is the one
    # we're pinning, and it consumes ``controller_cache`` directly.
    real.executescript(
        """
        CREATE TABLE IF NOT EXISTS files (id INTEGER PRIMARY KEY, path TEXT);
        CREATE TABLE IF NOT EXISTS symbols (
            id INTEGER PRIMARY KEY, file_id INTEGER, name TEXT,
            kind TEXT, default_value TEXT, line_start INTEGER, line_end INTEGER
        );
        """
    )

    # Counter wired into Path.read_text. Any disk read from inside
    # _find_eager_loads will bump this — the assertion below says zero.
    read_calls: list[str] = []
    real_read_text = RealPath.read_text

    def counting_read_text(self, *args, **kwargs):
        read_calls.append(str(self))
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(RealPath, "read_text", counting_read_text)
    # Force find_project_root → None so any "fall through to disk" path
    # short-circuits cleanly without touching the filesystem.
    monkeypatch.setattr(
        "roam.db.connection.find_project_root",
        lambda *_a, **_k: None,
    )

    cache = {
        "app/Http/Controllers/PostController.php": ("<?php\nPost::with(['comments', 'author'])->get();\n"),
        "app/Http/Controllers/CommentController.php": ("<?php\nComment::with(['post'])->get();\n"),
    }

    try:
        rels = cmd_n1._find_eager_loads(real, "Post", controller_cache=cache)
    finally:
        real.close()

    assert read_calls == [], (
        f"_find_eager_loads called Path.read_text {len(read_calls)} times "
        f"despite being passed a pre-built controller_cache — caching "
        f"regression. paths: {read_calls}"
    )
    assert rels == {"comments", "author"}


@pytest.fixture
def laravel_controller_dir(tmp_path):
    """Minimal Laravel layout: one controller, one model class hint.
    Used by the cache-invariant regression test below."""
    proj = tmp_path / "lara"
    proj.mkdir()
    (proj / ".git").mkdir()  # make find_project_root happy
    ctrl_dir = proj / "app" / "Http" / "Controllers"
    ctrl_dir.mkdir(parents=True)
    (ctrl_dir / "PostController.php").write_text(
        "<?php\nPost::with(['comments']);\nUser::with(['profile']);\n",
        encoding="utf-8",
    )
    (ctrl_dir / "OrderController.php").write_text(
        "<?php\nOrder::with(['items']);\nPost::with(['author']);\n",
        encoding="utf-8",
    )
    return proj


def test_build_controller_cache_reads_each_file_once(monkeypatch, laravel_controller_dir):
    """``_build_controller_cache`` is the read-once choke point. Every
    file path discovered by the SQL scan should be read EXACTLY once,
    regardless of how many models will later consult the cache.
    """
    from pathlib import Path as RealPath

    from roam.commands import cmd_n1

    monkeypatch.chdir(laravel_controller_dir)

    real = sqlite3.connect(":memory:")
    real.row_factory = sqlite3.Row
    real.executescript(
        """
        CREATE TABLE files (id INTEGER PRIMARY KEY, path TEXT);
        """
    )
    real.execute(
        "INSERT INTO files (id, path) VALUES "
        "(1, 'app/Http/Controllers/PostController.php'), "
        "(2, 'app/Http/Controllers/OrderController.php')"
    )

    read_calls: list[str] = []
    real_read_text = RealPath.read_text

    def counting_read_text(self, *args, **kwargs):
        read_calls.append(str(self).replace("\\", "/"))
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(RealPath, "read_text", counting_read_text)

    try:
        cache = cmd_n1._build_controller_cache(real)
    finally:
        real.close()

    # Each controller path read exactly once.
    controller_reads = [p for p in read_calls if "Controllers/" in p]
    assert len(controller_reads) == 2, (
        f"_build_controller_cache should read 2 controllers exactly "
        f"once each, got {len(controller_reads)} reads: {controller_reads}"
    )
    assert len(cache) == 2
    # Subsequent simulated per-model invocations consume the cache —
    # no additional disk reads should occur. Each call needs the minimal
    # ``symbols`` + ``files`` schema so the $with / resources.php queries
    # short-circuit cleanly (returning no rows is fine).
    pre_count = len(read_calls)
    conn2 = sqlite3.connect(":memory:")
    conn2.row_factory = sqlite3.Row
    conn2.executescript(
        """
        CREATE TABLE files (id INTEGER PRIMARY KEY, path TEXT);
        CREATE TABLE symbols (
            id INTEGER PRIMARY KEY, file_id INTEGER, name TEXT,
            kind TEXT, default_value TEXT, line_start INTEGER, line_end INTEGER
        );
        """
    )
    try:
        for model in ("Post", "Order", "User", "Comment", "Tag"):
            cmd_n1._find_eager_loads(conn2, model, controller_cache=cache)
    finally:
        conn2.close()
    assert len(read_calls) == pre_count, (
        f"per-model _find_eager_loads with shared cache did "
        f"{len(read_calls) - pre_count} extra reads — cache bypass regression."
    )


# ---------------------------------------------------------------------------
# Test — B3: bulk $with-symbol fetch + resource-config cache
# (`_bulk_fetch_with_symbols`, `_build_resource_config_cache`)
# ---------------------------------------------------------------------------


def _seed_models_with_with_symbols(conn, n_models, with_every=True):
    """Seed N models — one PHP file per model — each with a parent_id-linked
    ``$with`` property symbol carrying a default_value array literal.

    When ``with_every`` is False, only the first model gets a ``$with`` —
    the rest are bare (so we can verify the bulk path returns ``None`` for
    them rather than crashing).
    """
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS files (
            id INTEGER PRIMARY KEY,
            path TEXT,
            language TEXT,
            file_role TEXT
        );
        CREATE TABLE IF NOT EXISTS symbols (
            id INTEGER PRIMARY KEY,
            file_id INTEGER,
            name TEXT,
            qualified_name TEXT,
            kind TEXT,
            line_start INTEGER,
            line_end INTEGER,
            parent_id INTEGER,
            signature TEXT,
            default_value TEXT
        );
        CREATE TABLE IF NOT EXISTS edges (
            id INTEGER PRIMARY KEY,
            source_id INTEGER,
            target_id INTEGER,
            kind TEXT
        );
        """
    )
    models = {}
    for i in range(n_models):
        file_id = 1 + i
        class_id = 100 + i
        path = f"app/Models/Model{i}.php"
        conn.execute(
            "INSERT INTO files (id, path, language, file_role) VALUES (?, ?, 'php', 'source')",
            (file_id, path),
        )
        conn.execute(
            "INSERT INTO symbols (id, file_id, name, qualified_name, kind, line_start, line_end) "
            "VALUES (?, ?, ?, ?, 'class', ?, ?)",
            (class_id, file_id, f"Model{i}", f"App\\Models\\Model{i}", 1, 50),
        )
        if with_every or i == 0:
            conn.execute(
                "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end, default_value) "
                "VALUES (?, ?, '$with', 'property', ?, ?, ?)",
                (class_id * 10, file_id, 5, 5, f"['rel_{i}_a', 'rel_{i}_b']"),
            )
        # Methods on the model — required so analyze_n1's candidate
        # filter doesn't fall back to its per-model file-range SELECT
        # (a separate N+1 site, outside the B3 fix surface).
        for j in range(2):
            conn.execute(
                "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end, parent_id) "
                "VALUES (?, ?, ?, 'method', ?, ?, ?)",
                (class_id * 10 + 1 + j, file_id, f"method_{j}", 10 + j * 5, 12 + j * 5, class_id),
            )
        models[class_id] = {
            "id": class_id,
            "name": f"Model{i}",
            "qualified_name": f"App\\Models\\Model{i}",
            "kind": "class",
            "line_start": 1,
            "line_end": 50,
            "file_path": path,
            "file_id": file_id,
        }
    conn.commit()
    return models


@pytest.mark.parametrize("n_models", [1, 10, 50])
def test_bulk_fetch_with_symbols_single_query(n_models):
    """``_bulk_fetch_with_symbols`` issues exactly ONE SELECT regardless
    of how many models are passed in.

    Old code: 1 SELECT per model inside ``_find_eager_loads`` —
    100 models → 100 ``LIKE '%Model.php'`` queries. New code: 1
    bare-table scan keyed by file_id.
    """
    from roam.commands import cmd_n1

    real = sqlite3.connect(":memory:")
    real.row_factory = sqlite3.Row
    models = _seed_models_with_with_symbols(real, n_models)

    cc = CountingConn(real)
    try:
        result = cmd_n1._bulk_fetch_with_symbols(cc, models)
    finally:
        real.close()

    # Exactly one SELECT — the bulk scan over all $with property symbols.
    assert cc.execute_calls == 1, (
        f"_bulk_fetch_with_symbols ran {cc.execute_calls} queries for "
        f"{n_models} models — expected exactly 1. queries: {cc.queries}"
    )
    # Every model gets its $with row matched correctly.
    assert len(result) == n_models
    for mid, info in models.items():
        row = result[mid]
        assert row is not None, f"model {mid} ({info['name']}) missing $with row"
        assert row["default_value"] == f"['rel_{mid - 100}_a', 'rel_{mid - 100}_b']"
        assert row["file_path"] == info["file_path"]


def test_bulk_fetch_with_symbols_returns_none_for_missing():
    """Models without a ``$with`` property map to ``None`` in the index —
    the sentinel that says "we fetched and found nothing" so callers
    can distinguish from the ``_BULK_NOT_FETCHED`` legacy fallback path.
    """
    from roam.commands import cmd_n1

    real = sqlite3.connect(":memory:")
    real.row_factory = sqlite3.Row
    # 3 models, only model 0 has $with
    models = _seed_models_with_with_symbols(real, 3, with_every=False)

    try:
        result = cmd_n1._bulk_fetch_with_symbols(real, models)
    finally:
        real.close()

    # First model has a row; the other two are explicit None.
    model_0_id = 100
    model_1_id = 101
    model_2_id = 102
    assert result[model_0_id] is not None
    assert result[model_0_id]["default_value"] == "['rel_0_a', 'rel_0_b']"
    assert result[model_1_id] is None
    assert result[model_2_id] is None


def test_find_eager_loads_with_bulk_with_sym_skips_per_model_query():
    """When ``bulk_with_sym`` is provided, ``_find_eager_loads`` must NOT
    re-issue the ``LIKE '%Model.php'`` SELECT — it consumes the
    pre-fetched row directly.

    Counts ``execute`` calls and asserts step 1 issues zero
    ``$with``-shaped queries when the bulk path is taken.
    """
    from roam.commands import cmd_n1

    real = sqlite3.connect(":memory:")
    real.row_factory = sqlite3.Row
    real.executescript(
        """
        CREATE TABLE files (id INTEGER PRIMARY KEY, path TEXT);
        CREATE TABLE symbols (
            id INTEGER PRIMARY KEY, file_id INTEGER, name TEXT, kind TEXT,
            default_value TEXT, line_start INTEGER, line_end INTEGER
        );
        """
    )
    real.commit()

    pre_fetched_row = {
        "default_value": "['author', 'tags']",
        "line_start": 5,
        "line_end": 5,
        "file_path": "app/Models/Post.php",
    }

    cc = CountingConn(real)
    try:
        rels = cmd_n1._find_eager_loads(
            cc,
            "Post",
            controller_cache={},
            bulk_with_sym=pre_fetched_row,
            resource_config_contents=[],
            model_id=100,
        )
    finally:
        real.close()

    # No queries — every external read is mocked out by the bulk
    # parameters (with-sym, resource configs, controller cache).
    assert cc.execute_calls == 0, (
        f"_find_eager_loads issued {cc.execute_calls} queries despite having all 3 caches pre-populated: {cc.queries}"
    )
    # The relationships from the pre-fetched $with were parsed correctly.
    assert rels == {"author", "tags"}


def test_find_eager_loads_falls_back_when_bulk_with_sym_is_sentinel():
    """When called WITHOUT ``bulk_with_sym`` (the default sentinel),
    ``_find_eager_loads`` runs the original per-model
    ``LIKE '%Model.php'`` query.

    This pins the fallback path so ad-hoc callers (and the existing
    cache-disk regression test) keep working.
    """
    from roam.commands import cmd_n1

    real = sqlite3.connect(":memory:")
    real.row_factory = sqlite3.Row
    real.executescript(
        """
        CREATE TABLE files (id INTEGER PRIMARY KEY, path TEXT);
        CREATE TABLE symbols (
            id INTEGER PRIMARY KEY, file_id INTEGER, name TEXT, kind TEXT,
            default_value TEXT, line_start INTEGER, line_end INTEGER
        );
        """
    )
    real.execute("INSERT INTO files (id, path) VALUES (1, 'app/Models/Post.php')")
    real.execute(
        "INSERT INTO symbols (id, file_id, name, kind, default_value, line_start, line_end) "
        "VALUES (1, 1, '$with', 'property', \"['author']\", 5, 5)"
    )
    real.commit()

    cc = CountingConn(real)
    try:
        rels = cmd_n1._find_eager_loads(
            cc,
            "Post",
            controller_cache={},
            resource_config_contents=[],
            # bulk_with_sym deliberately omitted → sentinel → fallback.
        )
    finally:
        real.close()

    # Fallback path means at least the $with SELECT ran.
    assert cc.execute_calls >= 1, (
        "_find_eager_loads with no bulk_with_sym should run the legacy "
        "per-model SELECT (fallback path is intentionally preserved)"
    )
    # CountingConn truncates SQL at 80 chars — match on the leading
    # SELECT shape rather than the WHERE clause, which lives past the
    # truncation boundary. ("FROM symbols" itself gets cut to "FROM symbo".)
    has_with_query = any("default_value" in q and "line_start" in q and "FROM symbo" in q for q in cc.queries)
    assert has_with_query, f"fallback SELECT missing; queries seen: {cc.queries}"
    assert rels == {"author"}


@pytest.fixture
def laravel_resource_config_dir(tmp_path):
    """Minimal Laravel layout with two resource-config files using
    ``->eagerLoad([...])`` near model class references."""
    proj = tmp_path / "lara"
    proj.mkdir()
    (proj / ".git").mkdir()  # find_project_root anchor
    cfg_dir = proj / "config"
    cfg_dir.mkdir()
    (cfg_dir / "resources.php").write_text(
        "<?php\nreturn [\n  Post::class => (new Resource)->eagerLoad(['author', 'comments']),\n];",
        encoding="utf-8",
    )
    rdir = cfg_dir / "resources"
    rdir.mkdir()
    (rdir / "orders.php").write_text(
        "<?php\nreturn [\n  Order::class => (new Resource)->eagerLoad(['items', 'customer']),\n];",
        encoding="utf-8",
    )
    return proj


def test_build_resource_config_cache_reads_each_file_once(monkeypatch, laravel_resource_config_dir):
    """``_build_resource_config_cache`` reads every config file EXACTLY
    once. The returned content list is then reused across all per-model
    ``_find_eager_loads`` calls — replacing N × M reads with M.
    """
    from pathlib import Path as RealPath

    from roam.commands import cmd_n1

    monkeypatch.chdir(laravel_resource_config_dir)

    real = sqlite3.connect(":memory:")
    real.row_factory = sqlite3.Row
    real.executescript("CREATE TABLE files (id INTEGER PRIMARY KEY, path TEXT);")
    real.execute("INSERT INTO files (id, path) VALUES (1, 'config/resources.php'), (2, 'config/resources/orders.php')")
    real.commit()

    read_calls: list[str] = []
    real_read_text = RealPath.read_text

    def counting_read_text(self, *args, **kwargs):
        read_calls.append(str(self).replace("\\", "/"))
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(RealPath, "read_text", counting_read_text)

    try:
        contents = cmd_n1._build_resource_config_cache(real)
    finally:
        real.close()

    # Two config files = exactly two reads.
    config_reads = [p for p in read_calls if "config/" in p]
    assert len(config_reads) == 2, (
        f"_build_resource_config_cache should read 2 files exactly once "
        f"each, got {len(config_reads)} reads: {config_reads}"
    )
    assert len(contents) == 2

    # Now simulate the per-model loop: 5 models share the same cache.
    # No additional disk reads should happen.
    pre_count = len(read_calls)
    conn2 = sqlite3.connect(":memory:")
    conn2.row_factory = sqlite3.Row
    conn2.executescript(
        """
        CREATE TABLE files (id INTEGER PRIMARY KEY, path TEXT);
        CREATE TABLE symbols (
            id INTEGER PRIMARY KEY, file_id INTEGER, name TEXT, kind TEXT,
            default_value TEXT, line_start INTEGER, line_end INTEGER
        );
        """
    )
    try:
        for model in ("Post", "Order", "User", "Comment", "Tag"):
            cmd_n1._find_eager_loads(
                conn2,
                model,
                controller_cache={},
                resource_config_contents=contents,
                bulk_with_sym=None,
            )
    finally:
        conn2.close()

    assert len(read_calls) == pre_count, (
        f"per-model _find_eager_loads with shared resource_config_contents "
        f"did {len(read_calls) - pre_count} extra reads — cache bypass regression."
    )


def test_analyze_n1_eager_loads_constant_query_count(monkeypatch):
    """End-to-end: ``analyze_n1`` issues a constant number of queries
    inside ``_find_eager_loads`` regardless of how many models flow
    through. Compares 1-model vs 50-model runs.

    Pre-B3 (with controller-cache only): scaled by N because each
    model still ran 2 queries (``$with`` SELECT + config-file SELECT).
    Post-B3: both are pre-fetched once; per-model query count stays flat.
    """
    from roam.commands import cmd_n1

    def _run(n_models):
        real = sqlite3.connect(":memory:")
        real.row_factory = sqlite3.Row
        models = _seed_models_with_with_symbols(real, n_models)
        # Append an appended-property so _find_eager_loads is reached
        # for every model (not short-circuited by empty appended).
        monkeypatch.setattr(cmd_n1, "_detect_framework", lambda _conn: "laravel")
        monkeypatch.setattr(cmd_n1, "_find_model_classes", lambda _conn: models)
        monkeypatch.setattr(cmd_n1, "_is_test_path", lambda _p: False)
        # Surface a synthetic appended attr + a single accessor so the
        # candidate filter passes and step 3 (_find_eager_loads) runs
        # for every model.
        monkeypatch.setattr(cmd_n1, "_find_appends_properties", lambda *a, **k: ["full_name"])
        monkeypatch.setattr(
            cmd_n1,
            "_find_accessor_methods",
            lambda conn, mid, minfo, names, **kw: [
                (
                    {
                        "id": 9000 + mid,
                        "name": "getFullNameAttribute",
                        "qualified_name": f"M{mid}::getFullNameAttribute",
                        "file_path": minfo["file_path"],
                        "line_start": 10,
                        "line_end": 12,
                    },
                    "full_name",
                ),
            ],
        )
        # Avoid touching the disk for resource configs.
        monkeypatch.setattr(cmd_n1, "_build_resource_config_cache", lambda _conn: [])
        # Drop edge-trace work so we measure pre-loop fetches only.
        monkeypatch.setattr(cmd_n1, "_trace_accessor_io", lambda *a, **k: [])
        cc = CountingConn(real)
        try:
            cmd_n1.analyze_n1(cc)
        finally:
            real.close()
        return cc.execute_calls

    one = _run(1)
    fifty = _run(50)
    # Allow modest batched_in slack (chunk threshold) but reject any
    # solution that scales with n_models. Old code: 2 per model inside
    # _find_eager_loads → +98 queries. New code: should be flat.
    assert fifty <= one + 5, (
        f"analyze_n1 query count grew from {one} to {fifty} as n_models "
        f"went 1→50 — _find_eager_loads should be fully bulk-fetched."
    )
