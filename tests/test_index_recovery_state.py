"""Crash-safe index lifecycle and consumer recovery regressions."""

from __future__ import annotations

import os

import click
import pytest


def test_indexer_marks_successful_run_complete(tmp_path, monkeypatch):
    from roam.index import indexer as indexer_mod

    roam_dir = tmp_path / ".roam"
    roam_dir.mkdir()
    indexer = indexer_mod.Indexer(tmp_path)
    monkeypatch.setattr(indexer_mod, "_claim_index_lock", lambda _path: True)
    monkeypatch.setattr(indexer_mod, "_release_index_lock", lambda _path: None)
    monkeypatch.setattr(indexer, "_do_run", lambda *args, **kwargs: None)

    assert indexer.run(quiet=True) is True
    assert (roam_dir / "index.state").read_text(encoding="utf-8") == "complete\n"


def test_indexer_leaves_in_progress_marker_after_crash(tmp_path, monkeypatch):
    from roam.index import indexer as indexer_mod

    roam_dir = tmp_path / ".roam"
    roam_dir.mkdir()
    indexer = indexer_mod.Indexer(tmp_path)
    monkeypatch.setattr(indexer_mod, "_claim_index_lock", lambda _path: True)
    monkeypatch.setattr(indexer_mod, "_release_index_lock", lambda _path: None)

    def crash(*_args, **_kwargs):
        raise RuntimeError("simulated indexer crash")

    monkeypatch.setattr(indexer, "_do_run", crash)

    with pytest.raises(RuntimeError, match="simulated indexer crash"):
        indexer.run(quiet=True)
    assert (roam_dir / "index.state").read_text(encoding="utf-8") == f"in_progress:{os.getpid()}\n"


def test_ensure_index_force_recovers_pre_marker_stale_lock(tmp_path, monkeypatch):
    from roam.commands import resolve as resolve_mod
    from roam.index import indexer as indexer_mod

    roam_dir = tmp_path / ".roam"
    roam_dir.mkdir()
    (roam_dir / "index.lock").write_text("999999\n", encoding="utf-8")
    calls: list[dict] = []

    class FakeIndexer:
        def run(self, **kwargs):
            calls.append(kwargs)
            return True

    monkeypatch.setattr(resolve_mod, "db_exists", lambda: True)
    monkeypatch.setattr(resolve_mod, "find_project_root", lambda: tmp_path)
    monkeypatch.setattr(indexer_mod, "_pid_is_running", lambda _pid: False)
    monkeypatch.setattr(indexer_mod, "Indexer", FakeIndexer)

    resolve_mod.ensure_index(quiet=True)

    assert calls == [{"force": True, "quiet": True}]


def test_ensure_index_rejects_active_writer(tmp_path, monkeypatch):
    from roam.commands import resolve as resolve_mod
    from roam.index import indexer as indexer_mod

    roam_dir = tmp_path / ".roam"
    roam_dir.mkdir()
    (roam_dir / "index.lock").write_text("12345\n", encoding="utf-8")
    (roam_dir / "index.state").write_text("in_progress:12345\n", encoding="utf-8")

    monkeypatch.setattr(resolve_mod, "db_exists", lambda: True)
    monkeypatch.setattr(resolve_mod, "find_project_root", lambda: tmp_path)
    monkeypatch.setattr(indexer_mod, "_pid_is_running", lambda _pid: True)

    with pytest.raises(click.ClickException, match="currently being built"):
        resolve_mod.ensure_index(quiet=True)


def test_ensure_index_accepts_completed_marker_and_released_lock(tmp_path, monkeypatch):
    from roam.commands import resolve as resolve_mod
    from roam.index import indexer as indexer_mod

    roam_dir = tmp_path / ".roam"
    roam_dir.mkdir()
    (roam_dir / "index.lock").write_text("released\n", encoding="utf-8")
    (roam_dir / "index.state").write_text("complete\n", encoding="utf-8")

    monkeypatch.setattr(resolve_mod, "db_exists", lambda: True)
    monkeypatch.setattr(resolve_mod, "find_project_root", lambda: tmp_path)
    monkeypatch.setattr(
        indexer_mod.Indexer,
        "run",
        lambda *_args, **_kwargs: pytest.fail("completed index must not rebuild"),
    )

    resolve_mod.ensure_index(quiet=True)
