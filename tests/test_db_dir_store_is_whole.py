"""The index store is one directory: database, lock and lifecycle marker.

``ROAM_DB_DIR`` exists so a caller can analyse a repository without writing
into it -- a tool inspecting a checkout it does not own, a sandbox with no
write budget, a project on a cloud-synced drive. The database honoured that
redirect; the indexer did not. It hard-coded ``<root>/.roam/index.lock`` and
``<root>/.roam/index.state``, so a redirected run still created a ``.roam/``
directory in the analysed repository. Because the read-only commands
auto-index on a cold start, that was the DEFAULT outcome of pointing roam at
a fresh repository, not a corner case.

Falsification record. Each assertion below was run against the unfixed tree
first and observed to fail; the recorded text is verbatim.

* ``test_redirected_store_leaves_the_repository_untouched``::

      AssertionError: redirected run wrote roam files into the analysed repo:
      ['.roam', '.roam/index.lock', '.roam/index.state']

* ``test_refuses_instead_of_indexing_when_auto_index_disabled`` first failed
  with ``assert 0 == 3`` -- ``ROAM_NO_AUTO_INDEX`` did not exist, so the
  command indexed the repository and exited 0 instead of refusing. With the
  refusal in place it failed a second time, on the residue::

      AssertionError: a refused run still wrote into the repo: ['.roam']

  because ``db_exists`` resolved the store path with the directory-creating
  helper: merely ASKING whether an index existed created ``.roam/``.

* ``test_one_resolver_places_database_lock_and_marker_together`` and
  ``test_auto_index_disabled_reads_only_truthy_values`` failed on import --
  neither ``get_db_dir`` nor ``auto_index_disabled`` existed.

Two tests are controls and passed before the fix as well as after:
``test_default_store_keeps_every_file_in_the_project_roam_dir`` (no redirect
configured, so all three files must still land in ``<root>/.roam`` exactly as
they always have) and ``test_explicit_init_still_builds_when_auto_index_
disabled`` (the opt-out must not disable the command the refusal recommends).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

# Names that belong to the roam store. ``.git`` is excluded by the walk below
# because git keeps its own unrelated ``index.lock``.
_STORE_NAMES = frozenset({".roam", "index.db", "index.lock", "index.state"})


@pytest.fixture(autouse=True)
def _isolate_store_env(monkeypatch):
    """A developer's own ``ROAM_DB_DIR`` must not decide these outcomes."""
    monkeypatch.delenv("ROAM_DB_DIR", raising=False)
    monkeypatch.delenv("ROAM_NO_AUTO_INDEX", raising=False)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A tiny committed git repository with one resolvable symbol."""
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.py").write_text(
        "def helper():\n    return 1\n\n\ndef caller():\n    return helper()\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init"], cwd=project, capture_output=True, check=False)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=project, capture_output=True, check=False)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=project, capture_output=True, check=False)
    subprocess.run(["git", "add", "."], cwd=project, capture_output=True, check=False)
    subprocess.run(["git", "commit", "-m", "init"], cwd=project, capture_output=True, check=False)
    return project


def _store_entries(root: Path) -> list[str]:
    """Every roam-store path under *root*, ignoring git's own bookkeeping."""
    return sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.name in _STORE_NAMES and ".git" not in path.relative_to(root).parts
    )


def _run_cli(project: Path, argv: list[str]):
    from roam.cli import cli

    runner = CliRunner()
    previous = os.getcwd()
    try:
        os.chdir(str(project))
        return runner.invoke(cli, argv, catch_exceptions=False)
    finally:
        os.chdir(previous)


def test_redirected_store_leaves_the_repository_untouched(repo, tmp_path, monkeypatch):
    """With ``ROAM_DB_DIR`` set, a cold-start analysis writes nothing into the repo."""
    store = tmp_path / "store"
    monkeypatch.setenv("ROAM_DB_DIR", str(store))

    result = _run_cli(repo, ["preflight", "helper"])
    assert result.exit_code == 0, result.output

    strays = _store_entries(repo)
    assert strays == [], f"redirected run wrote roam files into the analysed repo: {strays}"

    # The whole store, not just the database, moved to the redirect target.
    for name in ("index.db", "index.lock", "index.state"):
        assert (store / name).exists(), (
            f"{name} missing from the redirected store: {sorted(p.name for p in store.iterdir())}"
        )


def test_default_store_keeps_every_file_in_the_project_roam_dir(repo):
    """Control: with no redirect, the store stays where it always was."""
    result = _run_cli(repo, ["preflight", "helper"])
    assert result.exit_code == 0, result.output

    roam_dir = repo / ".roam"
    for name in ("index.db", "index.lock", "index.state"):
        assert (roam_dir / name).exists(), f"{name} missing from {roam_dir}"


def test_one_resolver_places_database_lock_and_marker_together(repo, tmp_path, monkeypatch):
    """All three paths derive from one directory, so a fourth file would too."""
    from roam.db.connection import (
        get_db_dir,
        get_db_path,
        get_index_lock_path,
        get_index_state_path,
    )

    def parents() -> set[Path]:
        return {
            get_db_path(repo).parent,
            get_index_lock_path(repo).parent,
            get_index_state_path(repo).parent,
        }

    assert parents() == {get_db_dir(repo)} == {repo / ".roam"}

    store = tmp_path / "elsewhere"
    monkeypatch.setenv("ROAM_DB_DIR", str(store))
    assert parents() == {get_db_dir(repo)} == {store}


def test_refuses_instead_of_indexing_when_auto_index_disabled(repo, monkeypatch):
    """``ROAM_NO_AUTO_INDEX`` turns a cold start into a typed refusal, not a build."""
    from roam.exit_codes import EXIT_INDEX_MISSING

    monkeypatch.setenv("ROAM_NO_AUTO_INDEX", "1")

    result = _run_cli(repo, ["preflight", "helper"])
    assert result.exit_code == EXIT_INDEX_MISSING, result.output
    assert "ROAM_NO_AUTO_INDEX" in result.output, result.output

    strays = _store_entries(repo)
    assert strays == [], f"a refused run still wrote into the repo: {strays}"


def test_explicit_init_still_builds_when_auto_index_disabled(repo, monkeypatch):
    """The refusal recommends ``roam init``; the opt-out must not block it."""
    monkeypatch.setenv("ROAM_NO_AUTO_INDEX", "1")

    result = _run_cli(repo, ["init"])
    assert result.exit_code == 0, result.output
    assert (repo / ".roam" / "index.db").exists(), result.output


def test_auto_index_disabled_reads_only_truthy_values(monkeypatch):
    """An unset or falsy value keeps the historical auto-index behaviour."""
    from roam.commands.resolve import auto_index_disabled

    monkeypatch.delenv("ROAM_NO_AUTO_INDEX", raising=False)
    assert auto_index_disabled() is False
    for falsy in ("", "0", "false", "no", "off"):
        monkeypatch.setenv("ROAM_NO_AUTO_INDEX", falsy)
        assert auto_index_disabled() is False, falsy
    for truthy in ("1", "true", "TRUE", "yes", "on", " 1 "):
        monkeypatch.setenv("ROAM_NO_AUTO_INDEX", truthy)
        assert auto_index_disabled() is True, truthy
