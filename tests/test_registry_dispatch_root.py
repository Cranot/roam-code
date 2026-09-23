"""Dispatch refresh is rooted and read failures preserve existing evidence."""

from __future__ import annotations

import builtins
import json

import pytest
from click.testing import CliRunner

from roam.db.connection import open_db
from roam.index.registry_dispatch import resolve_registry_dispatch
from tests.conftest import git_init, index_in_process, invoke_cli
from tests.test_affected_tests_cli_dispatch import _files


@pytest.fixture
def dispatch_project(tmp_path, monkeypatch):
    for rel, content in _files().items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    git_init(tmp_path)
    output, rc = index_in_process(tmp_path)
    assert rc == 0, output
    monkeypatch.chdir(tmp_path)
    return tmp_path


def edges(conn):
    return set(map(tuple, conn.execute("SELECT source_id, target_id FROM edges WHERE kind='dispatch'")))


@pytest.mark.parametrize("subdir", ["tests", "src/roam/commands"])
def test_d5_incremental_from_subdirectory(dispatch_project, monkeypatch, subdir):
    with open_db(readonly=True) as conn:
        before = edges(conn)
    assert len(before) == 15
    path = dispatch_project / "tests/test_alpha_invoke.py"
    path.write_text(path.read_text() + "\n# changed\n")
    monkeypatch.chdir(dispatch_project / subdir)
    result = invoke_cli(CliRunner(), ["index"])
    assert result.exit_code == 0, result.output
    with open_db(readonly=True) as conn:
        assert edges(conn) == before
        steps = json.loads(
            conn.execute("SELECT steps_status FROM index_manifest ORDER BY id DESC LIMIT 1").fetchone()[0]
        )
        assert steps["registry_dispatch_resolver"]["status"] == "ok"


@pytest.mark.parametrize("failure", ["missing", "permission"])
def test_d5_failed_read_preserves_and_reports(dispatch_project, monkeypatch, failure):
    real_open = builtins.open

    def fail_one(path, *args, **kwargs):
        if str(path).endswith("roam/cli.py"):
            error = FileNotFoundError if failure == "missing" else PermissionError
            raise error("dispatch source unavailable")
        return real_open(path, *args, **kwargs)

    with open_db() as conn:
        before = edges(conn)
        assert before
        monkeypatch.setattr(builtins, "open", fail_one)
        error = None
        try:
            resolve_registry_dispatch(conn)
        except OSError as exc:
            error = exc
        assert edges(conn) == before
        assert error is not None and "dispatch source unavailable" in str(error)


def test_d5_index_reports_failed_refresh(dispatch_project, monkeypatch):
    real_open = builtins.open

    def fail_one(path, *args, **kwargs):
        if str(path).endswith("roam/cli.py"):
            raise PermissionError("dispatch source unavailable")
        return real_open(path, *args, **kwargs)

    with open_db(readonly=True) as conn:
        before = edges(conn)
    path = dispatch_project / "tests/test_alpha_invoke.py"
    path.write_text(path.read_text() + "\n# changed\n")
    monkeypatch.setattr(builtins, "open", fail_one)
    result = invoke_cli(CliRunner(), ["index"])
    assert "registry-dispatch resolver skipped: dispatch source unavailable" in result.output
    with open_db(readonly=True) as conn:
        assert edges(conn) == before
