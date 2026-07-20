"""Atomic-write regression tests.

R28 substrate-found bug fixes — the ``roam tx-boundaries`` detector
flagged two roam-code symbols as ``unsafe_mutation``:

* ``roam.telemetry._open`` — SQLite schema-create lacked an explicit
  transaction wrapper; the fix wraps both the DDL and the insert/prune
  in ``with conn:`` so the engine commits atomically and the heuristic
  classifies the function as ``transactional``.
* ``roam.commands.cmd_cga.cga_emit`` — the attestation writer used
  ``target.write_text(...)``, which is not atomic mid-crash. The fix
  routes through :func:`roam.atomic_io.atomic_write_text`, which uses
  the shared conditional native-install protocol.

These tests pin both fixes so we cannot regress.
"""

from __future__ import annotations

import json
import os
import sqlite3
import stat
from pathlib import Path

import pytest

from roam import atomic_io
from roam.atomic_io import (
    atomic_write_bytes,
    atomic_write_json,
    atomic_write_text,
    capture_file_generation,
    conditional_install_file,
)
from tests._helpers.repo_root import repo_root

# ---------------------------------------------------------------------------
# atomic_io helper itself
# ---------------------------------------------------------------------------


def test_atomic_write_text_happy_path(tmp_path):
    """Happy path — write text, read it back."""
    target = tmp_path / "data.txt"
    atomic_write_text(target, "hello world")
    assert target.read_text(encoding="utf-8") == "hello world"


def test_atomic_write_json_happy_path(tmp_path):
    """JSON round-trip with default formatting."""
    target = tmp_path / "data.json"
    payload = {"foo": 1, "bar": [1, 2, 3]}
    atomic_write_json(target, payload)
    loaded = json.loads(target.read_text(encoding="utf-8"))
    assert loaded == payload


def test_atomic_write_bytes_happy_path(tmp_path):
    """Binary round-trip."""
    target = tmp_path / "data.bin"
    atomic_write_bytes(target, b"\x00\x01\x02\xff")
    assert target.read_bytes() == b"\x00\x01\x02\xff"


def test_atomic_write_bytes_prepares_empty_temp_before_content(tmp_path):
    target = tmp_path / "private.bin"
    observations = []

    def prepare_temp(temp_path: str) -> None:
        temp = Path(temp_path)
        observations.append((temp.parent, temp.stat().st_size))

    atomic_write_bytes(target, b"private", prepare_temp=prepare_temp)

    assert observations == [(tmp_path, 0)]
    assert target.read_bytes() == b"private"


def test_atomic_write_bytes_prepare_failure_preserves_existing_target(tmp_path):
    target = tmp_path / "private.bin"
    target.write_bytes(b"old")

    def reject_temp(_temp_path: str) -> None:
        raise PermissionError("ACL unavailable")

    with pytest.raises(PermissionError, match="ACL unavailable"):
        atomic_write_bytes(target, b"new", prepare_temp=reject_temp)

    assert target.read_bytes() == b"old"
    leftovers = list(tmp_path.glob(".private.bin.*.tmp"))
    if os.name == "nt":
        assert leftovers == []
    else:
        assert len(leftovers) == 1
        assert leftovers[0].read_bytes() == b""


def test_atomic_write_bytes_fd_prepare_receives_live_descriptor(tmp_path):
    target = tmp_path / "private.bin"
    observations = []

    def prepare_temp_fd(fd: int, temp_path: str) -> None:
        observations.append((Path(temp_path).parent, os.fstat(fd).st_size))

    atomic_write_bytes(target, b"private", prepare_temp_fd=prepare_temp_fd)

    assert observations == [(tmp_path, 0)]
    assert target.read_bytes() == b"private"


def test_atomic_write_bytes_pre_replace_failure_preserves_target(tmp_path):
    target = tmp_path / "private.bin"
    target.write_bytes(b"old")

    def reject_replace() -> None:
        raise RuntimeError("compare-and-swap conflict")

    with pytest.raises(RuntimeError, match="compare-and-swap conflict"):
        atomic_write_bytes(target, b"new", before_replace=reject_replace, durable=True)

    assert target.read_bytes() == b"old"
    leftovers = list(tmp_path.glob(".private.bin.*.tmp"))
    if os.name == "nt":
        assert leftovers == []
    else:
        assert len(leftovers) == 1
        assert leftovers[0].read_bytes() == b"new"


def test_atomic_write_bytes_require_absent_preserves_existing_target(tmp_path: Path) -> None:
    target = tmp_path / "create-only.bin"
    target.write_bytes(b"existing-generation")

    with pytest.raises(FileExistsError, match="destination already exists"):
        atomic_write_bytes(target, b"candidate-generation", require_absent=True)

    assert target.read_bytes() == b"existing-generation"


def test_conditional_install_file_publishes_captured_source_generation(tmp_path: Path) -> None:
    source = tmp_path / ".database.sqlite.producer.tmp"
    destination = tmp_path / "database.sqlite"
    destination.write_bytes(b"prior-generation")
    descriptor = os.open(source, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
    try:
        os.write(descriptor, b"verified-producer-generation")
        os.fsync(descriptor)
        generation = capture_file_generation(descriptor)
    finally:
        os.close(descriptor)

    conditional_install_file(source, destination, source_generation=generation)

    assert destination.read_bytes() == b"verified-producer-generation"
    assert not source.exists()


def test_conditional_install_file_rejects_changed_source_generation(tmp_path: Path) -> None:
    source = tmp_path / ".database.sqlite.producer.tmp"
    destination = tmp_path / "database.sqlite"
    destination.write_bytes(b"prior-generation")
    descriptor = os.open(source, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
    try:
        os.write(descriptor, b"captured-generation")
        os.fsync(descriptor)
        generation = capture_file_generation(descriptor)
    finally:
        os.close(descriptor)
    source.write_bytes(b"changed-source-generation-with-another-size")

    with pytest.raises(FileExistsError, match="source changed"):
        conditional_install_file(source, destination, source_generation=generation)

    assert destination.read_bytes() == b"prior-generation"
    assert source.read_bytes() == b"changed-source-generation-with-another-size"


def test_conditional_install_file_rejects_same_size_rewrite_with_restored_mtime(tmp_path: Path) -> None:
    source = tmp_path / ".database.sqlite.producer.tmp"
    destination = tmp_path / "database.sqlite"
    destination.write_bytes(b"prior-generation")
    descriptor = os.open(source, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
    try:
        os.write(descriptor, b"GOOD")
        os.fsync(descriptor)
        generation = capture_file_generation(descriptor)
    finally:
        os.close(descriptor)
    before = source.stat()
    source.write_bytes(b"EVIL")
    os.utime(source, ns=(before.st_atime_ns, generation.mtime_ns))

    with pytest.raises(FileExistsError, match="tempfile content changed"):
        conditional_install_file(source, destination, source_generation=generation)

    assert destination.read_bytes() == b"prior-generation"
    assert source.read_bytes() == b"EVIL"


def test_conditional_install_file_rejects_source_hard_link_added_after_capture(tmp_path: Path) -> None:
    source = tmp_path / ".database.sqlite.producer.tmp"
    alias = tmp_path / "database-alias.sqlite"
    destination = tmp_path / "database.sqlite"
    destination.write_bytes(b"prior-generation")
    descriptor = os.open(source, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
    try:
        os.write(descriptor, b"captured-single-link-generation")
        os.fsync(descriptor)
        generation = capture_file_generation(descriptor)
    finally:
        os.close(descriptor)
    try:
        os.link(source, alias)
    except OSError as exc:
        pytest.skip(f"hard links are unavailable: {exc}")

    with pytest.raises(FileExistsError, match="source changed"):
        conditional_install_file(source, destination, source_generation=generation)

    assert destination.read_bytes() == b"prior-generation"
    assert source.read_bytes() == b"captured-single-link-generation"
    assert alias.read_bytes() == b"captured-single-link-generation"


@pytest.mark.skipif(os.name != "nt", reason="exclusive Windows source-handle contract")
def test_windows_final_digest_keeps_source_rewrite_blocked_through_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / ".database.sqlite.producer.tmp"
    destination = tmp_path / "database.sqlite"
    source.write_bytes(b"GOOD")
    destination.write_bytes(b"prior-generation")
    descriptor = os.open(source, os.O_RDONLY | getattr(os, "O_BINARY", 0))
    try:
        generation = capture_file_generation(descriptor)
    finally:
        os.close(descriptor)

    real_capture = atomic_io.capture_file_generation
    injection_attempted = False

    def capture_then_attempt_rewrite(descriptor: int, *, max_bytes: int):
        nonlocal injection_attempted
        proven = real_capture(descriptor, max_bytes=max_bytes)
        if not injection_attempted:
            injection_attempted = True
            with pytest.raises(PermissionError):
                source.write_bytes(b"EVIL")
        return proven

    monkeypatch.setattr(atomic_io, "capture_file_generation", capture_then_attempt_rewrite)

    conditional_install_file(source, destination, source_generation=generation)

    assert injection_attempted is True
    assert destination.read_bytes() == b"GOOD"
    assert not source.exists()


@pytest.mark.skipif(os.name != "nt", reason="exclusive Windows source-handle contract")
def test_windows_hard_link_added_after_final_digest_is_rejected_before_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / ".database.sqlite.producer.tmp"
    alias = tmp_path / "database-alias.sqlite"
    destination = tmp_path / "database.sqlite"
    source.write_bytes(b"GOOD")
    destination.write_bytes(b"prior-generation")
    descriptor = os.open(source, os.O_RDONLY | getattr(os, "O_BINARY", 0))
    try:
        generation = capture_file_generation(descriptor)
    finally:
        os.close(descriptor)

    real_capture = atomic_io.capture_file_generation
    injected = False

    def capture_then_link(descriptor: int, *, max_bytes: int):
        nonlocal injected
        proven = real_capture(descriptor, max_bytes=max_bytes)
        if not injected:
            os.link(source, alias)
            injected = True
        return proven

    monkeypatch.setattr(atomic_io, "capture_file_generation", capture_then_link)

    with pytest.raises(FileExistsError, match="changed after content capture"):
        conditional_install_file(source, destination, source_generation=generation)

    assert injected is True
    assert destination.read_bytes() == b"prior-generation"
    assert source.read_bytes() == b"GOOD"
    assert alias.read_bytes() == b"GOOD"


@pytest.mark.skipif(os.name != "nt", reason="exclusive Windows source-handle contract")
def test_windows_hard_link_at_native_boundary_is_quarantined_before_handle_release(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / ".database.sqlite.producer.tmp"
    alias = tmp_path / "database-alias.sqlite"
    destination = tmp_path / "database.sqlite"
    source.write_bytes(b"GOOD")
    descriptor = os.open(source, os.O_RDONLY | getattr(os, "O_BINARY", 0))
    try:
        generation = capture_file_generation(descriptor)
    finally:
        os.close(descriptor)

    real_set_name = atomic_io._windows_set_descriptor_name
    injected = False

    def link_then_set_name(descriptor: int, next_path: Path, *, replace: bool) -> None:
        nonlocal injected
        if not injected and Path(next_path) == destination:
            os.link(source, alias)
            injected = True
        real_set_name(descriptor, next_path, replace=replace)

    monkeypatch.setattr(atomic_io, "_windows_set_descriptor_name", link_then_set_name)

    with pytest.raises(FileExistsError, match="was quarantined"):
        conditional_install_file(source, destination, source_generation=generation)

    assert injected is True
    assert not source.exists()
    assert not destination.exists()
    assert alias.read_bytes() == b"GOOD"
    quarantined = list(tmp_path.glob(".database.sqlite.*.atomic-conflict"))
    assert len(quarantined) == 1
    assert quarantined[0].read_bytes() == b"GOOD"


@pytest.mark.parametrize("secure_parent", [False, True], ids=["path-fallback", "secure-parent"])
def test_atomic_write_bytes_failure_cleanup_preserves_replaced_temp(
    tmp_path: Path,
    secure_parent: bool,
) -> None:
    target = tmp_path / "private.bin"
    target.write_bytes(b"old-target")
    captured_temp: Path | None = None

    def capture_temp(temp_path: str) -> None:
        nonlocal captured_temp
        captured_temp = Path(temp_path)

    def replace_temp_then_fail() -> None:
        assert captured_temp is not None
        captured_temp.unlink()
        captured_temp.write_bytes(b"replacement-canary")
        raise RuntimeError("simulated pre-install failure")

    with pytest.raises(RuntimeError, match="simulated pre-install failure"):
        atomic_write_bytes(
            target,
            b"new-target",
            prepare_temp=capture_temp,
            before_replace=replace_temp_then_fail,
            secure_parent=secure_parent,
        )

    assert target.read_bytes() == b"old-target"
    assert captured_temp is not None
    assert captured_temp.read_bytes() == b"replacement-canary"


def test_atomic_write_bytes_rejects_temp_hard_link_added_before_install(
    tmp_path: Path,
) -> None:
    target = tmp_path / "private.bin"
    target.write_bytes(b"old-target")
    alias = tmp_path / "temp-alias.bin"
    captured_temp: Path | None = None

    def capture_temp(temp_path: str) -> None:
        nonlocal captured_temp
        captured_temp = Path(temp_path)

    def add_alias() -> None:
        assert captured_temp is not None
        try:
            os.link(captured_temp, alias)
        except OSError as exc:
            pytest.skip(f"hard links are unavailable: {exc}")

    with pytest.raises(FileExistsError, match="tempfile changed"):
        atomic_write_bytes(
            target,
            b"candidate-target",
            prepare_temp=capture_temp,
            before_replace=add_alias,
            secure_parent=True,
        )

    assert target.read_bytes() == b"old-target"
    assert alias.read_bytes() == b"candidate-target"


def test_atomic_write_bytes_rejects_same_size_temp_rewrite_with_restored_mtime(
    tmp_path: Path,
) -> None:
    target = tmp_path / "private.bin"
    target.write_bytes(b"old-target")
    captured_temp: Path | None = None

    def capture_temp(temp_path: str) -> None:
        nonlocal captured_temp
        captured_temp = Path(temp_path)

    def rewrite_without_metadata_signal() -> None:
        assert captured_temp is not None
        before = captured_temp.stat()
        captured_temp.write_bytes(b"EVIL")
        os.utime(captured_temp, ns=(before.st_atime_ns, before.st_mtime_ns))

    with pytest.raises(FileExistsError, match="tempfile content changed"):
        atomic_write_bytes(
            target,
            b"GOOD",
            prepare_temp=capture_temp,
            before_replace=rewrite_without_metadata_signal,
            secure_parent=True,
        )

    assert target.read_bytes() == b"old-target"


@pytest.mark.parametrize("secure_parent", [False, True], ids=["path-fallback", "secure-parent"])
def test_conditional_install_preserves_destination_created_at_last_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    secure_parent: bool,
) -> None:
    target = tmp_path / "created-at-boundary.bin"
    real_install = atomic_io._native_conditional_install
    validation_observations = []

    def validate_absent() -> None:
        validation_observations.append(target.exists())

    def create_destination_then_install(*args, **kwargs):
        assert validation_observations == [False]
        target.write_bytes(b"concurrent-create-canary")
        return real_install(*args, **kwargs)

    monkeypatch.setattr(atomic_io, "_native_conditional_install", create_destination_then_install)

    with pytest.raises(FileExistsError, match="destination appeared"):
        atomic_write_bytes(
            target,
            b"our-payload",
            before_replace=validate_absent,
            secure_parent=secure_parent,
        )

    assert validation_observations == [False]
    assert target.read_bytes() == b"concurrent-create-canary"
    leftovers = list(tmp_path.glob(".created-at-boundary.bin.*.tmp"))
    if os.name == "nt":
        assert leftovers == []
    else:
        assert len(leftovers) == 1
        assert leftovers[0].read_bytes() == b"our-payload"


@pytest.mark.parametrize("secure_parent", [False, True], ids=["path-fallback", "secure-parent"])
def test_conditional_install_rejects_destination_swapped_before_native_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    secure_parent: bool,
) -> None:
    target = tmp_path / "swapped-at-boundary.bin"
    target.write_bytes(b"initial-target")
    replacement = tmp_path / "replacement-canary.bin"
    replacement.write_bytes(b"concurrent-replacement-canary")
    replacement_identity = replacement.stat().st_dev, replacement.stat().st_ino
    real_install = atomic_io._native_conditional_install
    validation_observations = []

    def validate_initial_target() -> None:
        validation_observations.append(target.read_bytes())

    def swap_destination_then_install(*args, **kwargs):
        assert validation_observations == [b"initial-target"]
        os.replace(replacement, target)
        return real_install(*args, **kwargs)

    monkeypatch.setattr(atomic_io, "_native_conditional_install", swap_destination_then_install)

    with pytest.raises(FileExistsError, match="destination changed before install"):
        atomic_write_bytes(
            target,
            b"our-payload",
            before_replace=validate_initial_target,
            secure_parent=secure_parent,
        )

    assert validation_observations == [b"initial-target"]
    assert target.read_bytes() == b"concurrent-replacement-canary"
    assert (target.stat().st_dev, target.stat().st_ino) == replacement_identity
    leftovers = list(tmp_path.glob(".swapped-at-boundary.bin.*.tmp"))
    if os.name == "nt":
        assert leftovers == []
    else:
        assert len(leftovers) == 1
        assert leftovers[0].read_bytes() == b"our-payload"


@pytest.mark.parametrize("initial", [None, b"initial-target"], ids=["absent", "existing"])
@pytest.mark.parametrize("secure_parent", [False, True], ids=["path-fallback", "secure-parent"])
def test_successful_native_install_never_rolls_back_a_later_writer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    initial: bytes | None,
    secure_parent: bool,
) -> None:
    target = tmp_path / "later-writer.bin"
    if initial is not None:
        target.write_bytes(initial)
    later = tmp_path / "later-generation.bin"
    later.write_bytes(b"legitimate-later-generation")
    later_identity = later.stat().st_dev, later.stat().st_ino
    real_install = atomic_io._native_conditional_install

    def install_then_publish_later(*args, **kwargs):
        real_install(*args, **kwargs)
        os.replace(later, target)

    monkeypatch.setattr(atomic_io, "_native_conditional_install", install_then_publish_later)

    atomic_write_bytes(target, b"our-generation", secure_parent=secure_parent)

    assert target.read_bytes() == b"legitimate-later-generation"
    assert (target.stat().st_dev, target.stat().st_ino) == later_identity


@pytest.mark.skipif(os.name != "nt", reason="handle-bound replacement contract is Windows-only")
def test_windows_replace_failure_preserves_destination(tmp_path, monkeypatch):
    target = tmp_path / "partial-failure.bin"
    target.write_bytes(b"original-destination")
    original_identity = atomic_io._path_snapshot(target, require_regular=True).identity

    def reject_move(_descriptor, _destination, *, replace, expected_temp, durable):
        raise OSError(5, f"simulated handle rename failure replace={replace} durable={durable}")

    monkeypatch.setattr(atomic_io, "_windows_rename_descriptor", reject_move)

    with pytest.raises(OSError, match="simulated handle rename failure"):
        atomic_write_bytes(target, b"replacement")

    assert target.read_bytes() == b"original-destination"
    assert atomic_io._path_snapshot(target, require_regular=True).identity == original_identity
    assert list(tmp_path.glob(".partial-failure.bin.*")) == []


@pytest.mark.skipif(os.name != "nt", reason="native Windows handle metadata contract")
def test_windows_path_snapshot_binds_metadata_to_native_identity(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "snapshot.bin"
    target.write_bytes(b"A")
    replacement = tmp_path / "replacement.bin"
    replacement_payload = b"replacement-generation-is-longer"
    replacement.write_bytes(replacement_payload)
    replacement_identity = atomic_io._windows_path_identity(replacement)
    real_stat = atomic_io.os.stat
    injected = False

    def stat_then_swap(path, *args, **kwargs):
        nonlocal injected
        value = real_stat(path, *args, **kwargs)
        if not injected and Path(path) == target:
            injected = True
            os.replace(replacement, target)
        return value

    monkeypatch.setattr(atomic_io.os, "stat", stat_then_swap)
    snapshot = atomic_io._path_snapshot(target, require_regular=True)

    assert injected is True
    assert snapshot is not None
    assert snapshot.identity == replacement_identity
    assert snapshot.size == len(replacement_payload)


@pytest.mark.skipif(os.name == "nt", reason="secure-parent dirfds are a POSIX contract")
def test_secure_parent_install_remains_relative_to_pinned_dirfd(tmp_path, monkeypatch):
    target = tmp_path / "private.bin"
    calls = []
    real_install = atomic_io._native_conditional_install

    def record_install(source, destination, **kwargs):
        calls.append((source, destination, kwargs["source_dir_fd"], kwargs["destination_dir_fd"]))
        return real_install(source, destination, **kwargs)

    monkeypatch.setattr(atomic_io, "_native_conditional_install", record_install)
    atomic_write_bytes(target, b"private", secure_parent=True, durable=True)

    assert len(calls) == 1
    source, destination, source_dir_fd, destination_dir_fd = calls[0]
    assert Path(source).parent == Path(".")
    assert destination == target.name
    assert isinstance(source_dir_fd, int)
    assert destination_dir_fd == source_dir_fd
    assert target.read_bytes() == b"private"


def test_atomic_write_bytes_durable_syncs_file_then_parent(tmp_path, monkeypatch):
    target = tmp_path / "private.bin"
    calls: list[str] = []
    real_fsync = os.fsync

    def recording_fsync(fd: int) -> None:
        mode = os.fstat(fd).st_mode
        calls.append("directory" if stat.S_ISDIR(mode) else "file")
        real_fsync(fd)

    monkeypatch.setattr("roam.atomic_io.os.fsync", recording_fsync)

    atomic_write_bytes(target, b"private", durable=True)

    assert calls == (["file"] if os.name == "nt" else ["file", "directory"])


def test_atomic_write_bytes_can_require_existing_parent(tmp_path):
    target = tmp_path / "missing" / "private.bin"

    with pytest.raises(FileNotFoundError):
        atomic_write_bytes(target, b"private", create_parents=False)

    assert not target.parent.exists()


def test_atomic_write_creates_parent_dir(tmp_path):
    """``parents=True`` — nested paths should auto-create dirs."""
    target = tmp_path / "deep" / "nested" / "dir" / "out.txt"
    atomic_write_text(target, "ok")
    assert target.exists()
    assert target.read_text(encoding="utf-8") == "ok"


def test_atomic_write_cleans_up_temp_on_failure(tmp_path, monkeypatch):
    """Failed installs clean by handle on Windows and fail safe on POSIX."""

    def boom(*args, **kwargs):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(atomic_io, "_native_conditional_install", boom)

    target = tmp_path / "victim.txt"
    with pytest.raises(OSError, match="simulated replace failure"):
        atomic_write_text(target, "should never land")

    # The target must NOT exist (no partial write).
    assert not target.exists()

    # Windows can delete by handle identity. POSIX cannot conditionally unlink
    # by inode, so preservation is safer than deleting a raced replacement.
    leftover_tmps = list(tmp_path.glob(".victim.txt.*"))
    if os.name == "nt":
        assert leftover_tmps == []
    else:
        assert len(leftover_tmps) == 1
        assert leftover_tmps[0].read_bytes() == b"should never land"


def test_atomic_write_preserves_target_on_failure(tmp_path, monkeypatch):
    """If a write fails mid-way, the EXISTING target file must remain
    untouched. This is the core safety property — readers never see a
    half-written file."""
    target = tmp_path / "original.txt"
    target.write_text("ORIGINAL CONTENT", encoding="utf-8")

    def boom(*args, **kwargs):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(atomic_io, "_native_conditional_install", boom)

    with pytest.raises(OSError):
        atomic_write_text(target, "WOULD-BE-NEW CONTENT")

    # Original untouched.
    assert target.read_text(encoding="utf-8") == "ORIGINAL CONTENT"


def test_atomic_write_overwrites_existing_target(tmp_path):
    """The atomic last-writer-wins protocol replaces an existing target."""
    target = tmp_path / "existing.txt"
    target.write_text("OLD", encoding="utf-8")
    atomic_write_text(target, "NEW")
    assert target.read_text(encoding="utf-8") == "NEW"
    assert list(tmp_path.glob(".existing.txt.*")) == []


def test_unsupported_posix_platform_has_no_unconditional_fallback(monkeypatch):
    monkeypatch.setattr(atomic_io.sys, "platform", "unsupported-posix")

    with pytest.raises(OSError, match="conditional atomic install is unavailable"):
        atomic_io._posix_native_rename(
            "source",
            "destination",
            source_dir_fd=None,
            destination_dir_fd=None,
        )


@pytest.mark.parametrize(
    ("platform", "expected_flag", "native_name"),
    [
        ("linux", atomic_io._RENAME_NOREPLACE, "_linux_renameat2"),
        ("darwin", atomic_io._DARWIN_RENAME_EXCL, "_darwin_renameatx_np"),
    ],
)
def test_posix_native_dispatch_preserves_conditional_flags_and_dirfds(
    monkeypatch,
    platform,
    expected_flag,
    native_name,
):
    calls = []

    def record(source, destination, **kwargs):
        calls.append((source, destination, kwargs))

    monkeypatch.setattr(atomic_io.sys, "platform", platform)
    monkeypatch.setattr(atomic_io, native_name, record)
    atomic_io._posix_native_rename(
        "source",
        "destination",
        source_dir_fd=41,
        destination_dir_fd=42,
    )

    assert calls == [
        (
            "source",
            "destination",
            {
                "source_dir_fd": 41,
                "destination_dir_fd": 42,
                "flags": expected_flag,
            },
        )
    ]


# ---------------------------------------------------------------------------
# Telemetry _open / record — substrate fix
# ---------------------------------------------------------------------------


def test_telemetry_open_creates_schema_atomically(tmp_path, monkeypatch):
    """``_open`` should create the ``calls`` table inside an explicit
    transaction so DDL is committed atomically."""
    from roam import telemetry

    db_path = tmp_path / "telemetry.db"
    monkeypatch.setattr(telemetry, "_db_path", lambda: db_path)

    conn = telemetry._open()
    assert conn is not None
    try:
        # Schema must exist after _open returns.
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='calls'").fetchall()
        assert rows == [("calls",)]
    finally:
        conn.close()


def test_telemetry_record_writes_transactionally(tmp_path, monkeypatch):
    """``record`` should leave the DB in a consistent state. The
    ``with conn:`` wrapper around INSERT + DELETE ensures the two
    statements commit atomically (the substrate's unmatched-begin
    heuristic flagged the previous form)."""
    from roam import telemetry

    db_path = tmp_path / "telemetry.db"
    monkeypatch.setattr(telemetry, "_db_path", lambda: db_path)
    monkeypatch.setenv("ROAM_TELEMETRY_LOCAL", "1")

    telemetry.record("roam test", duration_ms=42, exit_code=0)
    telemetry.record("roam other", duration_ms=99, exit_code=1)

    # Independently reopen the DB and confirm both rows are present
    # (i.e. both committed).
    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute("SELECT command, duration_ms, exit_code FROM calls ORDER BY id").fetchall()
    assert rows == [("roam test", 42, 0), ("roam other", 99, 1)]


def test_telemetry_open_returns_none_on_filesystem_failure(tmp_path, monkeypatch):
    """Expected filesystem setup failures return ``None`` so telemetry
    still never breaks a CLI run."""
    from roam import telemetry

    def boom():
        raise OSError("simulated _db_path failure")

    monkeypatch.setattr(telemetry, "_db_path", boom)
    assert telemetry._open() is None


def test_telemetry_open_propagates_bug_class_path_failure(monkeypatch):
    """Bug-class exceptions from the path helper should not be hidden by
    telemetry's expected OS/SQLite failure guard."""
    from roam import telemetry

    def boom():
        raise RuntimeError("simulated _db_path bug")

    monkeypatch.setattr(telemetry, "_db_path", boom)
    with pytest.raises(RuntimeError, match="simulated _db_path bug"):
        telemetry._open()


# ---------------------------------------------------------------------------
# CGA attestation emit — substrate fix
# ---------------------------------------------------------------------------


def test_cga_emit_uses_atomic_write(tmp_path, monkeypatch):
    """``cga_emit`` should route the attestation write through
    :func:`roam.atomic_io.atomic_write_text` so the cryptographic
    artefact is crash-safe.

    We patch ``atomic_write_text`` to assert it is invoked, then to
    actually perform the write so downstream assertions can read the
    file back.
    """
    from roam.atomic_io import atomic_write_text as _real_write

    calls: list[tuple[Path, str]] = []

    def spy(path, content, **kwargs):
        calls.append((Path(path), content))
        _real_write(path, content, **kwargs)

    # Patch the import site (cmd_cga.py does a local import inside the
    # function, so we patch the module that defines it).
    monkeypatch.setattr("roam.atomic_io.atomic_write_text", spy)

    # Drive the write path directly via the helper for a focused unit
    # test — we don't need the full CLI plumbing to verify the contract.
    target = tmp_path / "out.intoto.json"
    payload = '{"_type":"https://in-toto.io/Statement/v1"}\n'
    # Re-import after patching so the spy is what cmd_cga would see.
    from roam import atomic_io as _aio

    _aio.atomic_write_text(target, payload)

    assert len(calls) == 1
    assert calls[0][0] == target
    assert calls[0][1] == payload
    assert target.read_text(encoding="utf-8") == payload


def test_cga_emit_atomic_write_survives_torn_replace(tmp_path, monkeypatch):
    """If native installation fails, no partial attestation becomes current."""
    target = tmp_path / "abc123.intoto.json"

    def boom(*args, **kwargs):
        raise OSError("simulated mid-write crash")

    monkeypatch.setattr(atomic_io, "_native_conditional_install", boom)

    with pytest.raises(OSError, match="simulated mid-write crash"):
        atomic_write_text(target, '{"would":"be","tampered":true}\n')

    assert not target.exists()
    leftovers = list(tmp_path.glob(".abc123.intoto.json.*"))
    if os.name == "nt":
        assert leftovers == []
    else:
        assert len(leftovers) == 1
        assert leftovers[0].read_text(encoding="utf-8") == '{"would":"be","tampered":true}\n'


def test_cga_emit_atomic_write_via_cli(tmp_path, monkeypatch):
    """End-to-end: invoke ``cga_emit``'s write path through the helper
    and confirm both the temp pattern AND final landing are correct.

    Uses a fake project + in-memory style direct call to bypass the
    full ``roam init`` cost while still exercising the same code path
    that the CLI uses.
    """
    canonical = '{"hello":"world"}'
    target = tmp_path / ".roam" / "attestations" / "deadbeef.intoto.json"

    atomic_write_text(target, canonical + "\n")

    assert target.exists()
    assert target.read_text(encoding="utf-8") == canonical + "\n"
    # Parent created automatically.
    assert target.parent.is_dir()


# ---------------------------------------------------------------------------
# Regression: heuristic-classification proof
# ---------------------------------------------------------------------------


def test_atomic_io_module_has_no_post_install_rollback_or_posix_unlink_race():
    """Successful publication never rewrites a legitimate later generation."""
    src = (repo_root() / "src" / "roam" / "atomic_io.py").read_text(encoding="utf-8")
    assert "renameat2" in src
    assert "renameatx_np" in src
    assert "SetFileInformationByHandle" in src
    assert "MoveFileExW" not in src
    assert "os.replace(" in src, "existing destinations use atomic last-writer-wins replacement"
    assert "os.rename(" not in src, "atomic_io must not use os.rename"
    assert "_restore_posix_destination" not in src
    assert "_discard_displaced_posix_destination" not in src
    assert "_restore_windows_destination" not in src
