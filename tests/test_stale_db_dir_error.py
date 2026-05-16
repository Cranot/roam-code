"""Tests for ``StaleDbDirError`` + ``_safe_mkdir`` in :mod:`roam.db.connection`.

Background: ``.roam/config.json`` may carry a ``db_dir`` pointing at a
path that is no longer writable (e.g. a path from another user's
machine that was committed by mistake). The previous behaviour was to
let the raw ``OSError`` / ``PermissionError`` (``[WinError 5] Access
denied``) propagate up the call stack — the MCP subprocess wrapper
then saw empty stdout + opaque stderr and reported a useless
``COMMAND_FAILED``. ``StaleDbDirError`` makes that case structured and
re-catchable at the MCP boundary with a remediation hint.

See ``internal/dogfood/IMPLEMENTATION-2026-05-12.md`` Task 2 for the full
root-cause analysis.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from roam.db.connection import (
    StaleDbDirError,
    _safe_mkdir,
    get_db_path,
)

# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_db_env(monkeypatch):
    """Make sure tests don't pick up a developer's ROAM_DB_DIR."""
    monkeypatch.delenv("ROAM_DB_DIR", raising=False)


def _make_project(tmp_path: Path, config: dict | None = None) -> Path:
    """Build a minimal project root (``.git/`` so ``find_project_root``
    stops walking) and optionally write ``.roam/config.json``.
    """
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".git").mkdir()
    if config is not None:
        roam_dir = proj / ".roam"
        roam_dir.mkdir()
        (roam_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")
    return proj


# ---------------------------------------------------------------------------
# _safe_mkdir
# ---------------------------------------------------------------------------


class TestSafeMkdir:
    def test_safe_mkdir_happy_path(self, tmp_path):
        target = tmp_path / "newdir"
        assert not target.exists()
        result = _safe_mkdir(target)
        assert result == target
        assert target.exists()
        assert target.is_dir()

    def test_safe_mkdir_idempotent(self, tmp_path):
        """Re-calling on an existing dir must succeed (exist_ok=True)."""
        target = tmp_path / "existing"
        target.mkdir()
        result = _safe_mkdir(target)
        assert result == target
        assert target.is_dir()

    def test_safe_mkdir_accepts_string_paths(self, tmp_path):
        target_str = str(tmp_path / "from-string")
        result = _safe_mkdir(target_str)
        assert isinstance(result, Path)
        assert result.exists()

    def test_safe_mkdir_raises_stale_db_dir_error_on_permission_error(self, tmp_path, monkeypatch):
        """Simulate a Windows ACL denial: monkeypatch Path.mkdir to raise.

        We don't rely on actual ACLs because they're not portable to the
        CI runners (Linux/macOS) and the regression we're guarding
        against is the Windows ``[WinError 5]`` shape specifically.
        """
        target = tmp_path / "unwritable"

        def _boom(self, *args, **kwargs):
            raise PermissionError("[WinError 5] Access denied")

        monkeypatch.setattr(Path, "mkdir", _boom)

        with pytest.raises(StaleDbDirError) as excinfo:
            _safe_mkdir(target, source="ROAM_DB_DIR env")

        err = excinfo.value
        assert err.db_dir == str(target)
        assert err.source == "ROAM_DB_DIR env"
        assert isinstance(err.original_error, PermissionError)
        # Message must carry a remediation hint so MCP error envelopes
        # can surface something actionable.
        msg = str(err)
        assert "ROAM_DB_DIR env" in msg
        assert "roam config db-dir --reset" in msg
        assert "[WinError 5] Access denied" in msg

    def test_safe_mkdir_raises_stale_db_dir_error_on_os_error(self, tmp_path, monkeypatch):
        """Non-permission OSError (e.g. invalid path) must also wrap."""
        target = tmp_path / "bogus"

        def _boom(self, *args, **kwargs):
            raise OSError(22, "Invalid argument")

        monkeypatch.setattr(Path, "mkdir", _boom)

        with pytest.raises(StaleDbDirError) as excinfo:
            _safe_mkdir(target, source=".roam/config.json db_dir")

        err = excinfo.value
        assert err.source == ".roam/config.json db_dir"
        assert isinstance(err.original_error, OSError)

    def test_safe_mkdir_default_source_label(self, tmp_path, monkeypatch):
        """When the caller omits source, the label falls back to <unknown>."""
        target = tmp_path / "no-source"

        def _boom(self, *args, **kwargs):
            raise PermissionError("denied")

        monkeypatch.setattr(Path, "mkdir", _boom)

        with pytest.raises(StaleDbDirError) as excinfo:
            _safe_mkdir(target)

        assert excinfo.value.source == "<unknown>"


# ---------------------------------------------------------------------------
# get_db_path — integration with the source-tracking wiring
# ---------------------------------------------------------------------------


class TestGetDbPath:
    def test_get_db_path_no_config_falls_back_to_project_default(self, tmp_path, monkeypatch):
        """No ``.roam/config.json`` → db lives under ``<project>/.roam/``."""
        proj = _make_project(tmp_path)
        # Don't write any config — exercise the bare fallback branch.
        monkeypatch.chdir(proj)
        db_path = get_db_path(project_root=proj)
        assert db_path == proj / ".roam" / "index.db"
        assert (proj / ".roam").exists()

    def test_get_db_path_empty_config_falls_back_to_project_default(self, tmp_path, monkeypatch):
        """Empty ``{}`` config (no ``db_dir`` key) → project default."""
        proj = _make_project(tmp_path, config={})
        monkeypatch.chdir(proj)
        db_path = get_db_path(project_root=proj)
        assert db_path == proj / ".roam" / "index.db"

    def test_get_db_path_raises_stale_db_dir_error_on_stale_config(self, tmp_path, monkeypatch):
        """Stale ``db_dir`` in ``.roam/config.json`` → StaleDbDirError."""
        stale = tmp_path / "stale-other-user-path"
        proj = _make_project(tmp_path, config={"db_dir": str(stale)})

        def _boom(self, *args, **kwargs):
            # Only fail on the stale path so the .roam/ scaffolding
            # (which uses mkdir up front in _make_project) is unaffected.
            # By the time get_db_path runs, _make_project's mkdirs have
            # already completed, so unconditional failure is safe.
            raise PermissionError("[WinError 5] Access denied")

        monkeypatch.setattr(Path, "mkdir", _boom)

        with pytest.raises(StaleDbDirError) as excinfo:
            get_db_path(project_root=proj)

        err = excinfo.value
        assert err.source == ".roam/config.json db_dir"
        assert err.db_dir == str(stale)
        assert "roam config db-dir --reset" in str(err)

    def test_get_db_path_raises_stale_db_dir_error_on_stale_env(self, tmp_path, monkeypatch):
        """Stale ``ROAM_DB_DIR`` env override → StaleDbDirError with env
        as the source label (so the remediation hint points the user at
        the env var, not the config file)."""
        stale = tmp_path / "stale-env-path"
        monkeypatch.setenv("ROAM_DB_DIR", str(stale))

        def _boom(self, *args, **kwargs):
            raise PermissionError("[WinError 5] Access denied")

        monkeypatch.setattr(Path, "mkdir", _boom)

        with pytest.raises(StaleDbDirError) as excinfo:
            get_db_path()

        err = excinfo.value
        assert err.source == "ROAM_DB_DIR env"
        assert err.db_dir == str(stale)
