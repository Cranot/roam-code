"""Verify additional environmental error paths emit a "run roam doctor" breadcrumb.

Companion to ``tests/test_doctor_hints_in_errors.py`` — that file covers the
original 4 sites (db corrupt, db schema, config parse, bundle verify).
This file covers the newer sites added when expanding the breadcrumb to
make ``roam doctor`` more discoverable from environmental errors:

* ``IndexMissingError`` (``roam.exit_codes``) — raised by ``require_index()``
  when ``.roam/index.db`` does not exist.
* ``StaleDbDirError`` (``roam.db.connection``) — raised when a configured
  ``db_dir`` cannot be created/written to.
* ``ensure_index`` first-run notice — printed when no index is found.
* ``cmd_init`` no-.git error — raised when invoked outside a git repo.
* ``cmd_index_bundle`` "could not extract index.db from bundle".
"""

from __future__ import annotations

from pathlib import Path

_HINT = "roam doctor"


def test_index_missing_error_includes_doctor_hint():
    """``IndexMissingError`` default message must mention `roam doctor`.

    This is the gate-style "no index found" error raised by
    ``require_index()`` in CI / gate commands.
    """
    from roam.exit_codes import IndexMissingError

    exc = IndexMissingError()
    assert _HINT in exc.message, f"IndexMissingError default message should mention `roam doctor`, got:\n{exc.message}"


def test_stale_db_dir_error_includes_doctor_hint():
    """``StaleDbDirError`` message must mention `roam doctor`.

    This fires when a configured ``db_dir`` (env var or .roam/config.json)
    cannot be created — typically a stale path from another machine.
    """
    from roam.db.connection import StaleDbDirError

    exc = StaleDbDirError(
        "/nonexistent/path",
        "ROAM_DB_DIR env",
        PermissionError("simulated permission denied"),
    )
    assert _HINT in str(exc), f"StaleDbDirError message should mention `roam doctor`, got:\n{exc}"


def test_ensure_index_no_index_notice_includes_doctor_hint(tmp_path, monkeypatch, capsys):
    """``ensure_index`` first-run echo must mention `roam doctor` so users
    who land on the "No roam index found" tip have a diagnostic path.
    """
    from roam.commands import resolve as resolve_mod

    # Pretend the DB doesn't exist, and short-circuit Indexer().run() so
    # we don't actually walk the filesystem.
    monkeypatch.setattr(resolve_mod, "db_exists", lambda: False)

    class _FakeIndexer:
        def run(self, quiet=False):
            return None

    import roam.index.indexer as indexer_mod

    monkeypatch.setattr(indexer_mod, "Indexer", lambda *a, **kw: _FakeIndexer())

    resolve_mod.ensure_index(quiet=False)
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert _HINT in combined, f"ensure_index first-run notice should mention `roam doctor`, got:\n{combined}"


def test_init_outside_git_repo_includes_doctor_hint(tmp_path):
    """``roam init`` outside a git repo raises a UsageError that must
    mention `roam doctor` as a diagnostic option.
    """
    from click.testing import CliRunner

    from roam.cli import cli

    # tmp_path is guaranteed not to be inside a git repo.
    runner = CliRunner()
    result = runner.invoke(cli, ["init", "--root", str(tmp_path)], catch_exceptions=False)
    assert result.exit_code != 0
    assert _HINT in result.output, f"`roam init` outside a git repo should mention `roam doctor`, got:\n{result.output}"


def test_bundle_extract_failure_includes_doctor_hint(monkeypatch):
    """When ``index-import`` is fed a tar bundle whose ``index.db`` member
    cannot be extracted (``tar.extractfile`` returns None — happens for a
    directory entry or a symlink), the resulting ClickException must
    surface the doctor hint.
    """
    from roam.commands import cmd_index_bundle as bundle_mod

    # Bypass _verify_bundle — we only want to exercise the extract path.
    monkeypatch.setattr(bundle_mod, "_verify_bundle", lambda p: {"schema_version": None, "repo_head": None})
    # Target DB path stays inside tmp so the overwrite check is a no-op.
    monkeypatch.setattr(bundle_mod, "_index_db_path", lambda: Path("/tmp/_does_not_exist_roam.db"))

    class _FakeMember:
        pass

    class _FakeTar:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def getmember(self, name):
            return _FakeMember()

        def extractfile(self, member):
            # This is the path under test — None triggers the "could not
            # extract index.db" branch.
            return None

    monkeypatch.setattr(bundle_mod.tarfile, "open", lambda *a, **kw: _FakeTar())

    from click.testing import CliRunner

    from roam.cli import cli

    runner = CliRunner()
    with runner.isolated_filesystem():
        # Make a placeholder bundle file so click's path validation passes.
        Path("bundle.tar.gz").write_bytes(b"placeholder")
        result = runner.invoke(
            cli,
            ["index-import", "bundle.tar.gz"],
            catch_exceptions=False,
        )
    assert result.exit_code != 0
    assert _HINT in result.output, f"index-import extract failure should mention `roam doctor`, got:\n{result.output}"


def test_breadcrumb_wording_is_consistent():
    """All breadcrumb sites use the identical canonical wording.

    The string ``"If this looks unexpected, run `roam doctor` to diagnose
    your install."`` should be byte-identical across error paths so an
    agent can pattern-match on it as a stable signal.
    """
    canonical = "If this looks unexpected, run `roam doctor` to diagnose your install."

    from roam.db.connection import StaleDbDirError
    from roam.exit_codes import IndexMissingError

    assert canonical in str(StaleDbDirError("/x", "test", PermissionError("denied")))
    assert canonical in IndexMissingError().message
