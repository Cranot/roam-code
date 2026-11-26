"""Dispatch sidecar behaviour through the CLI and through cached reach."""

from __future__ import annotations

import json
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from threading import Barrier, Lock

import pytest
from click.testing import CliRunner

from roam.commands import cmd_affected_tests as affected
from tests.conftest import index_in_process, invoke_cli

CLI = "src/roam/cli.py"
TEST = "TESTS/test_dispatch.py"
SOURCE = "from roam.cli import R\ndef test_reads_registry():\n    return R['alpha']\n"


def _write(root, path, content):
    file = root / path
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_bytes(content.encode("utf-8"))


@pytest.fixture
def indexed_project(tmp_path, monkeypatch):
    root = tmp_path / "project"
    db = tmp_path / "external-db"
    monkeypatch.setenv("ROAM_DB_DIR", str(db))
    monkeypatch.chdir(tmp_path)
    _write(root, "pyproject.toml", "[project]\nname = 'sample'\nversion = '0.1'\n")
    _write(root, "src/roam/__init__.py", "")
    _write(root, "src/roam/commands/__init__.py", "")
    _write(root, "src/roam/commands/cmd_alpha.py", "def alpha_cmd():\n    return 1\n")
    _write(root, CLI, "R = {'alpha': ('roam.commands.cmd_alpha', 'alpha_cmd')}\n")
    _write(root, TEST, SOURCE.replace("\n", "\r\n"))
    out, rc = index_in_process(root)
    assert rc == 0, out
    monkeypatch.chdir(root)
    return root, db / "index.db.dispatch.json"


def _run(command):
    result = invoke_cli(CliRunner(), [command, "alpha_cmd"], json_mode=True)
    assert result.exit_code == 0, result.output or repr(result.exception)
    return json.loads(result.output)


def _assert_reach(command, payload):
    if command == "preflight":
        assert payload["tests"]["cli_possible"] == 1
        assert TEST in payload["tests"]["test_files"]
    else:
        assert any(row["file"] == TEST and row["kind"] == "CLI_POSSIBLE" for row in payload["tests"])


@pytest.mark.parametrize("command", ["affected-tests", "preflight"])
@pytest.mark.parametrize(
    "contents",
    [b"[" * 10_000, b'["truncated",', b'{"foreign": []}', b"null", b"\xff", b'["other", {}]'],
    ids=["deep", "truncated", "foreign", "null", "encoding", "rows"],
)
def test_invalid_sidecar_discloses_fallback_and_refreshes(indexed_project, command, contents):
    _, sidecar = indexed_project
    sidecar.write_bytes(contents)

    result = _run(command)
    _assert_reach(command, result)
    assert result["dispatch_cache"]["state"] == "unusable"
    assert result["dispatch_cache"]["read_error"]
    assert _run(command)["dispatch_cache"]["state"] == "hit"


@pytest.mark.parametrize("command", ["affected-tests", "preflight"])
def test_json_distinguishes_cache_miss_hit_and_write_failure(indexed_project, monkeypatch, command):
    _, sidecar = indexed_project
    miss = _run(command)
    _assert_reach(command, miss)
    assert miss["dispatch_cache"]["state"] == "miss"
    assert miss["dispatch_cache_definition"]
    assert _run(command)["dispatch_cache"]["state"] == "hit"
    sidecar.write_bytes(b"{")
    replace = os.replace

    def deny(src, dst, *args, **kwargs):
        if os.path.normcase(dst) == os.path.normcase(sidecar):
            raise PermissionError("simulated read-only cache directory")
        return replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "replace", deny)
    failure = _run(command)
    _assert_reach(command, failure)
    assert failure["dispatch_cache"]["state"] == "write_failed"
    assert failure["dispatch_cache"]["read_error"]
    assert "PermissionError" in failure["dispatch_cache"]["write_error"]
    assert not list(sidecar.parent.glob(f".{sidecar.name}.*"))


@pytest.fixture
def dispatch_db(tmp_path):
    _write(tmp_path, CLI, "R = {}\nS = {}\n")
    _write(tmp_path, TEST, SOURCE)
    path = tmp_path / "index.db"
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.executescript(
            "CREATE TABLE files (id INTEGER, path TEXT, language TEXT);"
            "CREATE TABLE symbols (id INTEGER, name TEXT, file_id INTEGER);"
            "CREATE TABLE edges (source_id INTEGER, target_id INTEGER);"
        )
        conn.executemany("INSERT INTO files VALUES (?, ?, 'python')", [(1, CLI), (2, TEST)])
        conn.execute("INSERT INTO symbols VALUES (1, 'R', 1)")
    return path


def _query(db, *, registry=(CLI, "R", {"alpha"}), exclude=()):
    with closing(sqlite3.connect(db)) as conn:
        conn.row_factory = sqlite3.Row
        return affected._dispatch_relations(conn, db.parent, {1: registry}, [1], {"alpha"}, set(exclude))[2]


def test_concurrent_cold_scans_leave_no_staging_files(dispatch_db, monkeypatch):
    from roam import atomic_io

    sidecar = dispatch_db.with_name("index.db.dispatch.json")
    ready = Barrier(2, timeout=120)
    installing = Lock()
    native_install = atomic_io._native_conditional_install
    replace = os.replace

    def competing_native_install(src, dst, **kwargs):
        ready.wait()
        with installing:
            return native_install(src, dst, **kwargs)

    def competing_replace(src, dst, *args, **kwargs):
        if os.path.normcase(dst) == os.path.normcase(sidecar):
            ready.wait()
            with installing:
                return replace(src, dst, *args, **kwargs)
        return replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(atomic_io, "_native_conditional_install", competing_native_install)
    monkeypatch.setattr(os, "replace", competing_replace)
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(_query, [dispatch_db, dispatch_db])) == [{TEST: "alpha"}] * 2
    assert not list(dispatch_db.parent.glob(f".{sidecar.name}.*"))
    assert json.loads(sidecar.read_bytes())[1]
    assert _query(dispatch_db) == {TEST: "alpha"}


@pytest.mark.parametrize("module", ["changed_files", "file_roles", "test_conventions"])
def test_analyzer_dependency_edit_invalidates_dispatch_cache(indexed_project, monkeypatch, tmp_path, module):
    import sys

    dependency = next(
        sys.modules[name] for name in (f"roam.commands.{module}", f"roam.index.{module}") if name in sys.modules
    )
    original = os.fspath(dependency.__file__)
    copied = tmp_path / f"{module}.py"
    with open(original, "rb") as handle:
        copied.write_bytes(handle.read())
    monkeypatch.setattr(dependency, "__file__", str(copied))
    _, sidecar = indexed_project
    _run("affected-tests")
    old_key = json.loads(sidecar.read_bytes())[0]
    with copied.open("ab") as handle:
        handle.write(b"\n# analyzer source changed\n")
    data = _run("affected-tests")
    _assert_reach("affected-tests", data)
    assert json.loads(sidecar.read_bytes())[0] != old_key


def test_cache_distinguishes_index_import_mapping(dispatch_db):
    duplicate = "roam/cli.py"
    _write(dispatch_db.parent, duplicate, "R = {}\n")
    with closing(sqlite3.connect(dispatch_db)) as conn, conn:
        conn.execute("INSERT INTO files VALUES (3, ?, 'python')", (duplicate,))
    assert _query(dispatch_db) == {TEST: "alpha"}

    # Move the first candidate to the end of the index scan, with the
    # same file paths, bytes, test roots, flood and registry.
    with closing(sqlite3.connect(dispatch_db)) as conn, conn:
        conn.execute("DELETE FROM files WHERE id = 1")
        conn.execute("INSERT INTO files VALUES (1, ?, 'python')", (CLI,))
    assert _query(dispatch_db) == {}


def test_cache_distinguishes_registry_bindings(dispatch_db):
    # Same reached symbol and flood, but the imported binding is no
    # longer the registry in the dispatch scope.
    assert _query(dispatch_db) == {TEST: "alpha"}
    assert _query(dispatch_db, registry=(CLI, "S", {"alpha"})) == {}


def test_cache_distinguishes_excluded_test_roots(dispatch_db):
    # Start with empty rows, which are valid for either set of roots.
    assert _query(dispatch_db, exclude=[TEST]) == {}
    assert _query(dispatch_db) == {TEST: "alpha"}
    assert _query(dispatch_db, exclude=[TEST]) == {}


def test_mcp_queries_report_current_dispatch_state(indexed_project):
    from roam import mcp_server

    _, sidecar = indexed_project
    for tool, command in ((mcp_server.roam_preflight, "preflight"), (mcp_server.affected_tests, "affected-tests")):
        for arguments in ({}, {"symbol": ""}):
            refused = tool(**arguments)
            assert refused["isError"]
            assert refused["error_code"] == "USAGE_ERROR"
            if command == "preflight":
                assert refused["summary"]["target"] == ""
                assert refused["summary"]["risk_level"] == "UNKNOWN"
            assert not sidecar.exists()
        for expected in ("miss", "hit", "unusable", "hit"):
            if expected == "unusable":
                sidecar.write_bytes(b"[" * 10_000)
            payload = tool(symbol="alpha_cmd")
            _assert_reach(command, payload)
            assert payload["dispatch_cache"]["state"] == expected
        sidecar.unlink()


def test_dispatch_cache_rejects_rows_outside_the_selected_roots(indexed_project):
    _, sidecar = indexed_project
    _run("affected-tests")
    key, rows = json.loads(sidecar.read_bytes())
    rows.append(["tests/test_foreign.py", "test_foreign", False, ["alpha"], [], False])
    sidecar.write_text(json.dumps([key, rows]), encoding="utf-8")

    updated = _run("affected-tests")
    _assert_reach("affected-tests", updated)
    assert updated["dispatch_cache"]["read_error"]
    assert all(row["file"] != "tests/test_foreign.py" for row in updated["tests"])


def test_dispatch_cache_discloses_source_change_during_scan(indexed_project, monkeypatch):
    root, sidecar = indexed_project
    dispatch_reach = affected._dispatch_reach

    def edit_after_scan(scope, paths):
        yield from dispatch_reach(scope, paths)
        _write(root, TEST, SOURCE.replace("'alpha'", "'bravo'"))

    with monkeypatch.context() as patch:
        patch.setattr(affected, "_dispatch_reach", edit_after_scan)
        first = _run("affected-tests")
    _assert_reach("affected-tests", first)
    assert first["dispatch_cache"]["state"] == "write_failed"
    assert "source changed" in first["dispatch_cache"]["write_error"]
    assert not sidecar.exists()

    out, rc = index_in_process(root)
    assert rc == 0, out
    fresh = _run("affected-tests")
    assert fresh["summary"]["cli_possible"] == 0
    assert fresh["dispatch_cache"]["state"] == "miss"
