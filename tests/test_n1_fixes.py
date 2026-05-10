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
            signature TEXT
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

    # The bulk model-methods fetch is 1 query regardless of n_models.
    # Allow up to 3 queries total to absorb batched_in chunking and any
    # other one-shot lookups; the old code would emit n_models queries.
    assert cc.execute_calls <= 3, (
        f"analyze_n1 ran {cc.execute_calls} queries for {n_models} models — "
        f"expected <= 3 (constant). Old code: 1 per model.\n"
        f"queries: {cc.queries}"
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
