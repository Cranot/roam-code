"""Windows regressions for rejected owner-only file creation."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows handle semantics")


def test_rejected_new_file_is_deleted_by_its_open_handle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from roam.security import owner_only

    target = tmp_path / "rejected.key"
    monkeypatch.setattr(
        owner_only,
        "_windows_handle_is_owner_only",
        lambda _handle, *, require_protected: False,
    )

    with pytest.raises(PermissionError, match="not stable and owner-only"):
        owner_only._windows_open_new_owner_only_file(target)

    assert not target.exists()


def test_rejected_new_file_is_retained_when_handle_deletion_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from roam.security import owner_only

    target = tmp_path / "retained.key"
    monkeypatch.setattr(
        owner_only,
        "_windows_handle_is_owner_only",
        lambda _handle, *, require_protected: False,
    )
    monkeypatch.setattr(
        owner_only,
        "_windows_mark_handle_for_delete",
        lambda _handle, _kernel32: False,
    )

    with pytest.raises(PermissionError, match="not stable and owner-only"):
        owner_only._windows_open_new_owner_only_file(target)

    assert target.is_file()


def test_unproven_identity_cleanup_stays_bound_to_creation_handle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from roam.security import owner_only

    target = tmp_path / "unproven.key"
    monkeypatch.setattr(
        owner_only,
        "_windows_handle_identity",
        lambda _handle: None,
    )
    with pytest.raises(PermissionError, match="not stable and owner-only"):
        owner_only._windows_open_new_owner_only_file(target)

    assert not target.exists()


def test_successful_descriptor_blocks_path_replacement_for_its_lifetime(tmp_path: Path) -> None:
    from roam.security import owner_only

    target = tmp_path / "pinned.key"
    original_object = tmp_path / "original-object.key"
    descriptor = owner_only._windows_open_new_owner_only_file(target)
    try:
        with pytest.raises(PermissionError):
            os.replace(target, original_object)
        assert target.exists()
        assert not original_object.exists()
    finally:
        os.close(descriptor)
    os.replace(target, original_object)
    assert original_object.exists()


def test_non_delete_lock_descriptor_allows_peer_open_but_blocks_replacement(tmp_path: Path) -> None:
    from roam.security import owner_only

    target = tmp_path / "shared-lock.key"
    replacement = tmp_path / "replacement.key"
    descriptor = owner_only._windows_open_new_owner_only_file(
        target,
        request_delete_access=False,
    )
    peer = -1
    try:
        peer = os.open(target, os.O_RDWR | getattr(os, "O_BINARY", 0))
        with pytest.raises(PermissionError):
            os.replace(target, replacement)
        assert target.exists()
        assert not replacement.exists()
    finally:
        if peer >= 0:
            os.close(peer)
        os.close(descriptor)


def test_delete_disposition_stays_bound_during_path_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from roam.security import owner_only

    target = tmp_path / "rejected.key"
    original_object = tmp_path / "original-object.key"
    mark_handle_for_delete = owner_only._windows_mark_handle_for_delete

    monkeypatch.setattr(
        owner_only,
        "_windows_handle_is_owner_only",
        lambda _handle, *, require_protected: False,
    )

    def replace_path_then_mark(handle, kernel32) -> bool:
        original_identity = owner_only._windows_path_identity(target)
        assert owner_only._windows_handle_identity(handle) == original_identity
        with pytest.raises(PermissionError):
            os.replace(target, original_object)
        return mark_handle_for_delete(handle, kernel32)

    monkeypatch.setattr(
        owner_only,
        "_windows_mark_handle_for_delete",
        replace_path_then_mark,
    )

    with pytest.raises(PermissionError, match="not stable and owner-only"):
        owner_only._windows_open_new_owner_only_file(target)

    assert not target.exists()
    assert not original_object.exists()


def test_successful_new_file_does_not_block_sqlite_while_descriptor_is_open(
    tmp_path: Path,
) -> None:
    from roam.security import owner_only

    database = tmp_path / "compatible.sqlite3"
    descriptor = owner_only._windows_open_new_owner_only_file(
        database,
        allow_delete_sharing=True,
    )
    try:
        with sqlite3.connect(database) as connection:
            connection.execute("CREATE TABLE proof (value TEXT NOT NULL)")
            connection.execute("INSERT INTO proof VALUES ('sqlite-compatible')")
            connection.commit()
    finally:
        os.close(descriptor)

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT value FROM proof").fetchone() == ("sqlite-compatible",)
