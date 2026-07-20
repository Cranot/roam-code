"""POSIX regressions for fail-safe owner-only file cleanup."""

from __future__ import annotations

import errno
import os
import stat
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX unlink semantics")


def test_open_new_owner_only_file_creates_private_regular_file(tmp_path: Path) -> None:
    from roam.security import owner_only

    target = tmp_path / "created.key"
    descriptor = owner_only.open_new_owner_only_file(target)
    try:
        opened = os.fstat(descriptor)
        assert stat.S_ISREG(opened.st_mode)
        assert opened.st_nlink == 1
        assert stat.S_IMODE(opened.st_mode) == 0o600
        assert owner_only.file_descriptor_is_owner_only(descriptor, target)
    finally:
        os.close(descriptor)

    assert target.is_file()


def test_rejected_new_file_is_closed_but_retained(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from roam.security import owner_only

    target = tmp_path / "rejected.key"
    captured_descriptors: list[int] = []
    ensure_owner_only = owner_only.ensure_owner_only_file_descriptor

    def reject_after_validation(descriptor: int, path: owner_only.PathLike) -> bool:
        assert ensure_owner_only(descriptor, path)
        captured_descriptors.append(descriptor)
        return False

    monkeypatch.setattr(
        owner_only,
        "ensure_owner_only_file_descriptor",
        reject_after_validation,
    )

    with pytest.raises(PermissionError, match="not stable and owner-only"):
        owner_only.open_new_owner_only_file(target)

    assert len(captured_descriptors) == 1
    with pytest.raises(OSError) as closed:
        os.fstat(captured_descriptors[0])
    assert closed.value.errno == errno.EBADF
    value = target.lstat()
    assert stat.S_ISREG(value.st_mode)
    assert value.st_nlink == 1
    assert stat.S_IMODE(value.st_mode) == 0o600


def test_rejected_new_file_never_unlinks_a_path_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from roam.security import owner_only

    target = tmp_path / "rejected.key"
    original_object = tmp_path / "original-object.key"
    replacement_bytes = b"replacement-canary"
    ensure_owner_only = owner_only.ensure_owner_only_file_descriptor

    def replace_after_identity_check(descriptor: int, path: owner_only.PathLike) -> bool:
        assert ensure_owner_only(descriptor, path)
        os.replace(target, original_object)
        target.write_bytes(replacement_bytes)
        return False

    monkeypatch.setattr(
        owner_only,
        "ensure_owner_only_file_descriptor",
        replace_after_identity_check,
    )

    with pytest.raises(PermissionError, match="not stable and owner-only"):
        owner_only.open_new_owner_only_file(target)

    assert target.read_bytes() == replacement_bytes
    assert original_object.exists()


def test_validation_exception_closes_and_retains_new_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from roam.security import owner_only

    target = tmp_path / "validation-error.key"
    captured_descriptors: list[int] = []

    def fail_validation(descriptor: int, _path: owner_only.PathLike) -> bool:
        captured_descriptors.append(descriptor)
        raise RuntimeError("validation failed")

    monkeypatch.setattr(
        owner_only,
        "ensure_owner_only_file_descriptor",
        fail_validation,
    )

    with pytest.raises(RuntimeError, match="validation failed"):
        owner_only.open_new_owner_only_file(target)

    assert target.is_file()
    with pytest.raises(OSError) as closed:
        os.fstat(captured_descriptors[0])
    assert closed.value.errno == errno.EBADF
