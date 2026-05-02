"""Tests for the pytest fixture dependency resolver and command."""

from __future__ import annotations

import sqlite3

import pytest
from click.testing import CliRunner

from roam.index.pytest_fixtures import (
    _fixture_autouse,
    _fixture_scope,
    _parse_param_names,
    resolve_pytest_fixtures,
)

# ---------------------------------------------------------------------------
# Pure parser tests — no DB
# ---------------------------------------------------------------------------


class TestParseParamNames:
    def test_empty(self):
        assert _parse_param_names("") == []
        assert _parse_param_names(None) == []

    def test_no_params(self):
        assert _parse_param_names("def foo()") == []

    def test_single_param(self):
        assert _parse_param_names("def foo(bar)") == ["bar"]

    def test_multiple_params(self):
        assert _parse_param_names("def foo(a, b, c)") == ["a", "b", "c"]

    def test_typed_params(self):
        assert _parse_param_names("def foo(a: int, b: str)") == ["a", "b"]

    def test_default_values(self):
        assert _parse_param_names("def foo(a=1, b='x')") == ["a", "b"]

    def test_typed_with_defaults(self):
        assert _parse_param_names("def foo(a: int = 1, b: str = 'x')") == ["a", "b"]

    def test_strips_self(self):
        # ``self`` is in NON_FIXTURE_PARAMS but the parser still returns
        # it — filtering happens in the resolver, not the parser.
        assert _parse_param_names("def foo(self, db)") == ["self", "db"]

    def test_strips_star_args(self):
        assert _parse_param_names("def foo(a, *args, **kwargs)") == ["a", "args", "kwargs"]

    def test_decorator_in_signature(self):
        sig = "@pytest.fixture\ndef foo(db, request)"
        assert _parse_param_names(sig) == ["db", "request"]

    def test_async_def(self):
        assert _parse_param_names("async def foo(client)") == ["client"]

    def test_return_annotation(self):
        assert _parse_param_names("def foo(a, b) -> int") == ["a", "b"]


class TestFixtureScope:
    def test_no_decorators_defaults_function(self):
        assert _fixture_scope("") == "function"
        assert _fixture_scope(None) == "function"

    def test_bare_fixture_defaults_function(self):
        assert _fixture_scope("@pytest.fixture") == "function"

    def test_session_scope(self):
        assert _fixture_scope('@pytest.fixture(scope="session")') == "session"

    def test_module_scope(self):
        assert _fixture_scope("@pytest.fixture(scope='module')") == "module"

    def test_invalid_scope_falls_back(self):
        assert _fixture_scope('@pytest.fixture(scope="bogus")') == "function"


class TestFixtureAutouse:
    def test_no_autouse(self):
        assert _fixture_autouse("@pytest.fixture") is False
        assert _fixture_autouse("") is False

    def test_autouse_true(self):
        assert _fixture_autouse("@pytest.fixture(autouse=True)") is True

    def test_autouse_false(self):
        assert _fixture_autouse("@pytest.fixture(autouse=False)") is False

    def test_autouse_with_other_args(self):
        assert _fixture_autouse('@pytest.fixture(scope="session", autouse=True)') is True


# ---------------------------------------------------------------------------
# DB-backed resolver tests with an in-memory schema
# ---------------------------------------------------------------------------


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE files (
            id INTEGER PRIMARY KEY,
            path TEXT NOT NULL,
            language TEXT,
            file_role TEXT DEFAULT 'test'
        );
        CREATE TABLE symbols (
            id INTEGER PRIMARY KEY,
            file_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            qualified_name TEXT,
            kind TEXT NOT NULL,
            signature TEXT,
            decorators TEXT DEFAULT ''
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


def _add_file(conn, file_id: int, path: str) -> None:
    conn.execute(
        "INSERT INTO files (id, path, language) VALUES (?, ?, 'python')",
        (file_id, path),
    )


def _add_fixture(conn, sym_id: int, file_id: int, name: str, params: str = "") -> None:
    conn.execute(
        """
        INSERT INTO symbols (id, file_id, name, qualified_name, kind, signature, decorators)
        VALUES (?, ?, ?, ?, 'function', ?, '@pytest.fixture')
        """,
        (sym_id, file_id, name, name, f"@pytest.fixture\ndef {name}({params})"),
    )


def _add_test(conn, sym_id: int, file_id: int, name: str, params: str = "") -> None:
    conn.execute(
        """
        INSERT INTO symbols (id, file_id, name, qualified_name, kind, signature, decorators)
        VALUES (?, ?, ?, ?, 'function', ?, '')
        """,
        (sym_id, file_id, name, name, f"def {name}({params})"),
    )


class TestResolver:
    def test_no_fixtures_returns_zero(self):
        conn = _make_conn()
        _add_file(conn, 1, "tests/test_a.py")
        _add_test(conn, 10, 1, "test_foo")
        assert resolve_pytest_fixtures(conn) == 0

    def test_simple_fixture_dependency(self):
        conn = _make_conn()
        _add_file(conn, 1, "tests/test_a.py")
        _add_fixture(conn, 10, 1, "db")
        _add_fixture(conn, 11, 1, "user", params="db")
        n = resolve_pytest_fixtures(conn)
        assert n == 1
        edges = conn.execute("SELECT source_id, target_id FROM edges WHERE kind = 'pytest_fixture_dep'").fetchall()
        assert (edges[0]["source_id"], edges[0]["target_id"]) == (11, 10)

    def test_test_function_depends_on_fixture(self):
        conn = _make_conn()
        _add_file(conn, 1, "tests/test_a.py")
        _add_fixture(conn, 10, 1, "db")
        _add_test(conn, 20, 1, "test_login", params="db")
        n = resolve_pytest_fixtures(conn)
        assert n == 1

    def test_builtin_fixtures_skipped(self):
        conn = _make_conn()
        _add_file(conn, 1, "tests/test_a.py")
        _add_test(conn, 20, 1, "test_x", params="tmp_path, monkeypatch, capsys")
        n = resolve_pytest_fixtures(conn)
        # No user-defined fixture matches builtins — zero edges
        assert n == 0

    def test_self_skipped(self):
        conn = _make_conn()
        _add_file(conn, 1, "tests/test_a.py")
        _add_fixture(conn, 10, 1, "self")  # pathological but possible
        _add_fixture(conn, 11, 1, "user", params="self")
        n = resolve_pytest_fixtures(conn)
        # ``self`` is filtered as a non-fixture param even when a
        # fixture with that name exists.
        assert n == 0

    def test_conftest_lookup(self):
        conn = _make_conn()
        _add_file(conn, 1, "tests/conftest.py")
        _add_file(conn, 2, "tests/test_a.py")
        _add_fixture(conn, 10, 1, "db")  # fixture in conftest
        _add_test(conn, 20, 2, "test_login", params="db")
        n = resolve_pytest_fixtures(conn)
        assert n == 1
        edge = conn.execute("SELECT target_id FROM edges WHERE source_id = 20").fetchone()
        assert edge["target_id"] == 10

    def test_root_conftest_lookup(self):
        conn = _make_conn()
        _add_file(conn, 1, "conftest.py")
        _add_file(conn, 2, "tests/sub/test_a.py")
        _add_fixture(conn, 10, 1, "db")
        _add_test(conn, 20, 2, "test_login", params="db")
        n = resolve_pytest_fixtures(conn)
        assert n == 1

    def test_same_file_wins_over_conftest(self):
        conn = _make_conn()
        _add_file(conn, 1, "tests/conftest.py")
        _add_file(conn, 2, "tests/test_a.py")
        _add_fixture(conn, 10, 1, "db")  # conftest version
        _add_fixture(conn, 11, 2, "db")  # same-file shadow
        _add_test(conn, 20, 2, "test_login", params="db")
        n = resolve_pytest_fixtures(conn)
        assert n == 1
        # Test should resolve to the same-file fixture (id=11), not conftest
        edge = conn.execute("SELECT target_id FROM edges WHERE source_id = 20").fetchone()
        assert edge["target_id"] == 11

    def test_self_loop_prevented(self):
        # Edge case: a fixture with a parameter shadowing its own name.
        # The resolver MUST NOT emit a self-loop (would create infinite
        # walk loops in the chain command).
        conn = _make_conn()
        _add_file(conn, 1, "tests/test_a.py")
        _add_fixture(conn, 10, 1, "db", params="db")
        n = resolve_pytest_fixtures(conn)
        assert n == 0

    def test_idempotent_reindex(self):
        conn = _make_conn()
        _add_file(conn, 1, "tests/test_a.py")
        _add_fixture(conn, 10, 1, "db")
        _add_fixture(conn, 11, 1, "user", params="db")
        # First run
        assert resolve_pytest_fixtures(conn) == 1
        # Second run wipes prior edges and re-derives — count stable.
        assert resolve_pytest_fixtures(conn) == 1
        edges = conn.execute("SELECT COUNT(*) FROM edges WHERE kind = 'pytest_fixture_dep'").fetchone()[0]
        assert edges == 1

    def test_chain_three_deep(self):
        conn = _make_conn()
        _add_file(conn, 1, "tests/test_a.py")
        _add_fixture(conn, 10, 1, "settings")
        _add_fixture(conn, 11, 1, "db", params="settings")
        _add_fixture(conn, 12, 1, "user", params="db")
        _add_test(conn, 20, 1, "test_x", params="user")
        n = resolve_pytest_fixtures(conn)
        # 3 pair edges: db->settings, user->db, test_x->user
        assert n == 3

    def test_unknown_fixture_skipped(self):
        conn = _make_conn()
        _add_file(conn, 1, "tests/test_a.py")
        _add_test(conn, 20, 1, "test_x", params="some_undefined_fixture")
        n = resolve_pytest_fixtures(conn)
        assert n == 0


# ---------------------------------------------------------------------------
# CLI command smoke test
# ---------------------------------------------------------------------------


@pytest.fixture
def fixture_project(tmp_path, monkeypatch):
    """A tiny project that uses pytest fixtures, indexed end-to-end."""
    from roam.index.indexer import Indexer

    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "conftest.py").write_text("import pytest\n\n@pytest.fixture\ndef db():\n    return 'db'\n")
    (tmp_path / "tests" / "test_login.py").write_text(
        "import pytest\n"
        "\n"
        "@pytest.fixture\n"
        "def user(db):\n"
        "    return {'id': 1, 'db': db}\n"
        "\n"
        "def test_user_has_id(user):\n"
        "    assert user['id'] == 1\n"
    )
    # Initialise a tiny git repo so discovery works.
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init"],
        cwd=tmp_path,
        check=True,
    )

    monkeypatch.chdir(tmp_path)
    Indexer().run(quiet=True)
    return tmp_path


class TestCommand:
    def test_summary_no_args(self, fixture_project):
        from roam.cli import cli

        runner = CliRunner()
        res = runner.invoke(cli, ["pytest-fixtures"])
        assert res.exit_code == 0, res.output
        assert "fixture(s)" in res.output
        assert "dependency edge(s)" in res.output

    def test_chain_for_test_function(self, fixture_project):
        from roam.cli import cli

        runner = CliRunner()
        res = runner.invoke(cli, ["pytest-fixtures", "test_user_has_id"])
        assert res.exit_code == 0, res.output
        # Chain should mention the user fixture (depth 1) and db (depth 2)
        assert "user" in res.output
        assert "db" in res.output

    def test_json_envelope(self, fixture_project):
        import json

        from roam.cli import cli

        runner = CliRunner()
        res = runner.invoke(cli, ["--json", "pytest-fixtures"])
        assert res.exit_code == 0, res.output
        payload = json.loads(res.output)
        assert payload["command"] == "pytest-fixtures"
        assert "summary" in payload
        assert payload["summary"]["fixtures"] >= 2

    def test_impact_picks_up_fixture_edges(self, fixture_project):
        """Blast radius of a fixture must include the tests that depend
        on it, transitively. The graph builder ingests every edge kind,
        so this should work without any per-edge-kind logic in impact."""
        from roam.cli import cli

        runner = CliRunner()
        res = runner.invoke(cli, ["impact", "db"])
        assert res.exit_code == 0, res.output
        # ``user`` fixture depends on ``db`` (direct), and
        # ``test_user_has_id`` depends on ``user`` (transitive). Both
        # should be in the blast radius — 2 symbols total, 1 affected
        # file (test_login.py).
        assert "user" in res.output
        assert "2 symbols" in res.output
        assert "test_login.py" in res.output
