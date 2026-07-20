"""Atomic file writes with conditional native destination installation.

Centralised home for the "write-to-sibling-tempfile, then rename onto
target" idiom used by :mod:`roam.runs` (the run ledger), the response
cache writer, the W6.5 attestation emitter, and several command-level
writers (``cmd_stale_refs._atomic_write_text``,
``cmd_pr_bundle._atomic_write_bundle``).

Why this matters
================

Critical artefacts must survive a crash mid-write without leaving a
torn file on disk:

* Run ledger entries — the source-of-truth for ``roam replay`` and
  ``roam runs``. A torn ledger breaks the timeline.
* CGA attestation files — cryptographically chained. A torn statement
  invalidates the signature.
* Telemetry pings, response caches, suppression lists — small files
  that consumers ``json.loads`` on read. A torn JSON crashes the
  reader (Pattern 1 from `the dogfood synthesis notes`).

Strategy
========

1. Create a tempfile **in the same directory** as the target. Same
   directory keeps every native rename on one filesystem.
2. Write all bytes to the tempfile, fsync optional (skipped — most
   callers want speed; durability against a power-cut is a different
   problem from torn-write protection).
3. Content-bind the completed tempfile, snapshot the destination, and run the
   caller's final validator. Absent destinations use a native exclusive move.
   Existing destinations receive one final generation check followed by an
   ordinary atomic last-writer-wins replacement.
4. On failure, Windows removes only the identity-bound tempfile. POSIX retains
   the tempfile because it has no conditional unlink-by-inode operation; a
   recoverable private artifact is safer than deleting a raced replacement.

There is deliberately no post-install inspect-and-rollback sequence: once a
native move succeeds, a different current pathname is a legitimate later
writer and is never moved or restored. Linux uses
``renameat2(RENAME_NOREPLACE)`` and Darwin uses
``renameatx_np(RENAME_EXCL)`` for create-only publication. Existing paths use
``os.replace`` on POSIX after the final precheck. Windows keeps an exclusive,
    no-follow source handle from its final content proof through a handle-bound
    ``SetFileInformationByHandle(FileRenameInfo)`` operation, so publication names
    the proved object rather than re-resolving its pathname. A generation that
    changes at the final native boundary is moved off the consumer pathname
    before that exclusive handle is released. Unsupported native primitives
    fail closed.

R28 follow-up — substrate self-application
==========================================

The ``roam tx-boundaries`` substrate (Phase 2 sub-feature 4) flagged
two roam-code symbols as ``unsafe_mutation`` because they performed
mutations outside any transaction wrapper. ``_open`` in
:mod:`roam.telemetry` and the attestation writer at
``cmd_cga.py:cga_emit`` were both addressed by routing their writes
through this helper. Eating our own dogfood: the substrate caught
real bugs, we fixed them.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import secrets
import stat
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Union

from roam.observability import log_swallowed

# Public surface kept intentionally small. Callers should NOT reach in to
# the private helpers — they may grow os-specific flags (O_TMPFILE,
# FILE_FLAG_WRITE_THROUGH) in future iterations.
__all__ = [
    "atomic_write_text",
    "atomic_write_bytes",
    "atomic_write_json",
    "capture_file_generation",
    "conditional_install_file",
    "FileGeneration",
]


PathLike = Union[str, os.PathLike]


class _AtomicInstallConflict(FileExistsError):
    """The destination changed after its final pre-install snapshot."""


class _AtomicInstallUnavailable(OSError):
    """The platform/filesystem cannot provide conditional atomic install."""


@dataclass(frozen=True)
class _DestinationSnapshot:
    """Filesystem generation token used by the conditional installer."""

    identity: tuple[int, int]
    size: int
    mtime_ns: int
    ctime_ns: int
    nlink: int


@dataclass(frozen=True)
class FileGeneration:
    """Content-bound producer generation for an already-written file."""

    identity: tuple[int, int]
    size: int
    mtime_ns: int
    ctime_ns: int
    nlink: int
    sha256: str


_AT_FDCWD = -100
_RENAME_NOREPLACE = 1
_DARWIN_RENAME_EXCL = 0x00000004
_NATIVE_INSTALL_UNSUPPORTED = {
    errno.ENOSYS,
    errno.EINVAL,
    getattr(errno, "ENOTSUP", errno.EINVAL),
    getattr(errno, "EOPNOTSUPP", errno.EINVAL),
}


def _atomic_conflict(message: str, path: str | os.PathLike[str]) -> _AtomicInstallConflict:
    return _AtomicInstallConflict(errno.EEXIST, message, str(path))


def _windows_handle_snapshot(handle) -> tuple[_DestinationSnapshot, int] | None:
    """Read identity and generation metadata from one native Windows handle."""

    import ctypes
    from ctypes import wintypes

    class _FileTime(ctypes.Structure):
        _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]

    class _ByHandleFileInformation(ctypes.Structure):
        _fields_ = [
            ("file_attributes", wintypes.DWORD),
            ("creation_time", _FileTime),
            ("last_access_time", _FileTime),
            ("last_write_time", _FileTime),
            ("volume_serial_number", wintypes.DWORD),
            ("file_size_high", wintypes.DWORD),
            ("file_size_low", wintypes.DWORD),
            ("number_of_links", wintypes.DWORD),
            ("file_index_high", wintypes.DWORD),
            ("file_index_low", wintypes.DWORD),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetFileInformationByHandle.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(_ByHandleFileInformation),
    )
    kernel32.GetFileInformationByHandle.restype = wintypes.BOOL
    info = _ByHandleFileInformation()
    if not kernel32.GetFileInformationByHandle(handle, ctypes.byref(info)):
        return None
    if info.file_attributes & 0x00000400:  # FILE_ATTRIBUTE_REPARSE_POINT
        return None
    file_index = (int(info.file_index_high) << 32) | int(info.file_index_low)
    filetime_ticks = (int(info.last_write_time.high) << 32) | int(info.last_write_time.low)
    creation_ticks = (int(info.creation_time.high) << 32) | int(info.creation_time.low)
    windows_to_unix_epoch_ticks = 116_444_736_000_000_000
    snapshot = _DestinationSnapshot(
        identity=(int(info.volume_serial_number), file_index),
        size=(int(info.file_size_high) << 32) | int(info.file_size_low),
        mtime_ns=(filetime_ticks - windows_to_unix_epoch_ticks) * 100,
        ctime_ns=(creation_ticks - windows_to_unix_epoch_ticks) * 100,
        nlink=int(info.number_of_links),
    )
    return snapshot, int(info.file_attributes)


def _windows_handle_identity(handle) -> tuple[int, int] | None:
    """Return a stable Windows volume/file ID, rejecting reparse points."""

    captured = _windows_handle_snapshot(handle)
    return None if captured is None else captured[0].identity


def _windows_open_identity_handle(
    path: str | os.PathLike[str],
    *,
    delete_access: bool,
    share_delete: bool = True,
):
    """Open one path without following its final reparse point."""

    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    kernel32.CreateFileW.restype = wintypes.HANDLE
    file_read_attributes = 0x00000080
    delete = 0x00010000
    share_all = 0x00000001 | 0x00000002
    if share_delete:
        share_all |= 0x00000004
    open_existing = 3
    file_flag_backup_semantics = 0x02000000
    file_flag_open_reparse_point = 0x00200000
    handle = kernel32.CreateFileW(
        str(Path(path)),
        file_read_attributes | (delete if delete_access else 0),
        share_all,
        None,
        open_existing,
        file_flag_backup_semantics | file_flag_open_reparse_point,
        None,
    )
    invalid_handle = ctypes.c_void_p(-1).value
    if not handle or handle == invalid_handle:
        return None
    return handle, kernel32


def _windows_path_identity(path: str | os.PathLike[str]) -> tuple[int, int] | None:
    """Read one no-follow Windows pathname identity through a native handle."""

    opened = _windows_open_identity_handle(path, delete_access=False)
    if opened is None:
        return None
    handle, kernel32 = opened
    try:
        return _windows_handle_identity(handle)
    finally:
        kernel32.CloseHandle(handle)


def _windows_delete_identity(path: Path, identity: tuple[int, int]) -> bool:
    """Delete exactly one Windows object through its identity-bound handle."""

    import ctypes
    from ctypes import wintypes

    class _FileDispositionInfo(ctypes.Structure):
        _fields_ = [("delete_file", wintypes.BOOLEAN)]

    opened = _windows_open_identity_handle(path, delete_access=True)
    if opened is None:
        return False
    handle, kernel32 = opened
    try:
        if _windows_handle_identity(handle) != identity:
            return False
        file_disposition_info = 4
        kernel32.SetFileInformationByHandle.argtypes = (
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
        )
        kernel32.SetFileInformationByHandle.restype = wintypes.BOOL
        disposition = _FileDispositionInfo(True)
        return bool(
            kernel32.SetFileInformationByHandle(
                handle,
                file_disposition_info,
                ctypes.byref(disposition),
                ctypes.sizeof(disposition),
            )
        )
    finally:
        kernel32.CloseHandle(handle)


def _path_snapshot(
    path: str | os.PathLike[str],
    *,
    dir_fd: int | None = None,
    require_regular: bool,
) -> _DestinationSnapshot | None:
    """Capture one no-follow pathname snapshot or return ``None`` if absent."""

    try:
        opened = os.stat(path, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    if os.name == "nt":
        if dir_fd is not None:  # pragma: no cover - Windows rejects dir_fd earlier
            raise _AtomicInstallUnavailable(errno.ENOTSUP, "Windows has no dirfd conditional installer")
        native = _windows_open_identity_handle(path, delete_access=False)
        if native is None:
            raise OSError(errno.EINVAL, "atomic-write destination identity is unavailable", str(path))
        handle, kernel32 = native
        try:
            captured = _windows_handle_snapshot(handle)
        finally:
            kernel32.CloseHandle(handle)
        if captured is None:
            raise OSError(errno.EINVAL, "atomic-write destination identity is unavailable", str(path))
        snapshot, attributes = captured
        if require_regular and attributes & 0x00000010:  # FILE_ATTRIBUTE_DIRECTORY
            raise OSError(errno.EINVAL, "atomic-write destination must be a regular file", str(path))
        if _windows_path_identity(path) != snapshot.identity:
            raise _atomic_conflict("atomic-write destination changed while it was inspected", path)
        return snapshot

    if require_regular and not stat.S_ISREG(opened.st_mode):
        raise OSError(errno.EINVAL, "atomic-write destination must be a regular file", str(path))

    return _DestinationSnapshot(
        identity=(int(opened.st_dev), int(opened.st_ino)),
        size=int(opened.st_size),
        mtime_ns=int(opened.st_mtime_ns),
        ctime_ns=int(opened.st_ctime_ns),
        nlink=int(opened.st_nlink),
    )


def _snapshot_matches(expected: _DestinationSnapshot, actual: _DestinationSnapshot | None) -> bool:
    """Match identity plus observable content-generation metadata."""

    return bool(
        actual is not None
        and actual.identity == expected.identity
        and actual.size == expected.size
        and actual.mtime_ns == expected.mtime_ns
        and actual.ctime_ns == expected.ctime_ns
        and actual.nlink == expected.nlink
    )


def _raise_native_error(result: int, *, operation: str, path: str | os.PathLike[str]) -> None:
    """Translate one errno-reporting native rename failure."""

    if result == 0:
        return
    import ctypes

    error = ctypes.get_errno()
    if error in _NATIVE_INSTALL_UNSUPPORTED:
        raise _AtomicInstallUnavailable(
            error,
            f"{operation} is unavailable on this filesystem; atomic install refused",
            str(path),
        )
    raise OSError(error, os.strerror(error), str(path))


def _linux_renameat2(
    source: str | os.PathLike[str],
    destination: str | os.PathLike[str],
    *,
    source_dir_fd: int | None,
    destination_dir_fd: int | None,
    flags: int,
) -> None:
    """Invoke Linux ``renameat2`` without an unsafe compatibility fallback."""

    import ctypes

    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise _AtomicInstallUnavailable(errno.ENOSYS, "renameat2 is unavailable; atomic install refused")
    renameat2.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    renameat2.restype = ctypes.c_int
    ctypes.set_errno(0)
    result = renameat2(
        _AT_FDCWD if source_dir_fd is None else source_dir_fd,
        os.fsencode(source),
        _AT_FDCWD if destination_dir_fd is None else destination_dir_fd,
        os.fsencode(destination),
        flags,
    )
    _raise_native_error(result, operation="renameat2", path=destination)


def _darwin_renameatx_np(
    source: str | os.PathLike[str],
    destination: str | os.PathLike[str],
    *,
    source_dir_fd: int | None,
    destination_dir_fd: int | None,
    flags: int,
) -> None:
    """Invoke Darwin ``renameatx_np`` with exclusive-create semantics."""

    import ctypes

    libc = ctypes.CDLL(None, use_errno=True)
    renameatx_np = getattr(libc, "renameatx_np", None)
    if renameatx_np is None:
        raise _AtomicInstallUnavailable(errno.ENOSYS, "renameatx_np is unavailable; atomic install refused")
    renameatx_np.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    renameatx_np.restype = ctypes.c_int
    ctypes.set_errno(0)
    result = renameatx_np(
        _AT_FDCWD if source_dir_fd is None else source_dir_fd,
        os.fsencode(source),
        _AT_FDCWD if destination_dir_fd is None else destination_dir_fd,
        os.fsencode(destination),
        flags,
    )
    _raise_native_error(result, operation="renameatx_np", path=destination)


def _posix_native_rename(
    source: str | os.PathLike[str],
    destination: str | os.PathLike[str],
    *,
    source_dir_fd: int | None,
    destination_dir_fd: int | None,
) -> None:
    """Run the platform's conditional rename or fail closed."""

    if sys.platform.startswith("linux"):
        _linux_renameat2(
            source,
            destination,
            source_dir_fd=source_dir_fd,
            destination_dir_fd=destination_dir_fd,
            flags=_RENAME_NOREPLACE,
        )
        return
    if sys.platform == "darwin":
        _darwin_renameatx_np(
            source,
            destination,
            source_dir_fd=source_dir_fd,
            destination_dir_fd=destination_dir_fd,
            flags=_DARWIN_RENAME_EXCL,
        )
        return
    raise _AtomicInstallUnavailable(
        errno.ENOTSUP,
        f"conditional atomic install is unavailable on {sys.platform}",
        str(destination),
    )


def _windows_open_pinned_source_descriptor(
    source: str | os.PathLike[str],
    *,
    durable: bool,
) -> int:
    """Open one source object exclusively for proof and handle-bound rename."""

    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    kernel32.CreateFileW.restype = wintypes.HANDLE
    generic_read = 0x80000000
    generic_write = 0x40000000
    delete = 0x00010000
    open_existing = 3
    file_attribute_normal = 0x00000080
    file_flag_open_reparse_point = 0x00200000
    desired_access = generic_read | delete | (generic_write if durable else 0)
    handle = kernel32.CreateFileW(
        str(Path(source)),
        desired_access,
        0,  # No sharing: pathname writes, replacement, and consumer opens wait.
        None,
        open_existing,
        file_attribute_normal | file_flag_open_reparse_point,
        None,
    )
    invalid_handle = ctypes.c_void_p(-1).value
    if not handle or handle == invalid_handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return msvcrt.open_osfhandle(
            int(handle),
            (os.O_RDWR if durable else os.O_RDONLY) | getattr(os, "O_BINARY", 0),
        )
    except BaseException:
        kernel32.CloseHandle(handle)
        raise


def _windows_set_descriptor_name(
    descriptor: int,
    destination: Path,
    *,
    replace: bool,
) -> None:
    """Rename the exact object behind *descriptor* via ``FileRenameInfo``."""

    import ctypes
    import msvcrt
    from ctypes import wintypes

    class _FileRenameInfo(ctypes.Structure):
        _fields_ = [
            ("replace_if_exists", ctypes.c_ubyte),
            ("root_directory", wintypes.HANDLE),
            ("file_name_length", wintypes.DWORD),
            ("file_name", wintypes.WCHAR * 1),
        ]

    destination = Path(os.path.abspath(os.fspath(destination)))
    encoded = str(destination).encode("utf-16-le")
    name_offset = _FileRenameInfo.file_name.offset
    buffer = ctypes.create_string_buffer(name_offset + len(encoded) + 2)
    info = ctypes.cast(buffer, ctypes.POINTER(_FileRenameInfo)).contents
    info.replace_if_exists = bool(replace)
    info.root_directory = None
    info.file_name_length = len(encoded)
    ctypes.memmove(ctypes.addressof(buffer) + name_offset, encoded, len(encoded))

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.SetFileInformationByHandle.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    )
    kernel32.SetFileInformationByHandle.restype = wintypes.BOOL
    handle = msvcrt.get_osfhandle(descriptor)
    if kernel32.SetFileInformationByHandle(
        wintypes.HANDLE(handle),
        3,  # FileRenameInfo
        buffer,
        name_offset + len(encoded),
    ):
        return
    error = ctypes.get_last_error()
    if not replace and error in {80, 183}:  # ERROR_FILE_EXISTS / ERROR_ALREADY_EXISTS
        raise _atomic_conflict("atomic-write destination appeared before install", destination)
    raise ctypes.WinError(error)


def _windows_flush_descriptor(descriptor: int) -> None:
    """Flush the exact source/current object while its exclusive handle is live."""

    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.FlushFileBuffers.argtypes = (wintypes.HANDLE,)
    kernel32.FlushFileBuffers.restype = wintypes.BOOL
    if not kernel32.FlushFileBuffers(wintypes.HANDLE(msvcrt.get_osfhandle(descriptor))):
        raise ctypes.WinError(ctypes.get_last_error())


def _windows_quarantine_published_descriptor(descriptor: int, destination: Path) -> Path:
    """Remove a committed but unproven object from its consumer pathname."""

    last_error: OSError | None = None
    for _attempt in range(128):
        quarantine = destination.parent / f".{destination.name}.{secrets.token_hex(8)}.atomic-conflict"
        try:
            _windows_set_descriptor_name(descriptor, quarantine, replace=False)
            return quarantine
        except FileExistsError:
            continue
        except OSError as exc:
            last_error = exc
            break
    raise OSError(
        errno.EIO,
        "atomic-write committed source could not be quarantined",
        str(destination),
    ) from last_error


def _windows_rename_descriptor(
    descriptor: int,
    destination: Path,
    *,
    replace: bool,
    expected_temp: FileGeneration,
    durable: bool,
) -> None:
    """Prove and publish the exact exclusively-held Windows file object."""

    _prove_descriptor_generation(
        descriptor,
        expected_temp,
        source=None,
        source_dir_fd=None,
    )
    if durable:
        _windows_flush_descriptor(descriptor)
    _windows_set_descriptor_name(descriptor, destination, replace=replace)
    try:
        published = capture_file_generation(descriptor, max_bytes=expected_temp.size)
        if published != expected_temp:
            raise _atomic_conflict("atomic-write source changed during publication", destination)
    except BaseException as exc:
        quarantine = _windows_quarantine_published_descriptor(descriptor, destination)
        raise _atomic_conflict(
            f"atomic-write source changed during publication and was quarantined at {quarantine.name}",
            destination,
        ) from exc
    if durable:
        _windows_flush_descriptor(descriptor)


def _install_windows_conditionally(
    source_descriptor: int,
    destination: Path,
    *,
    expected: _DestinationSnapshot | None,
    expected_temp: FileGeneration,
    durable: bool,
) -> None:
    """Install with exclusive-create or atomic last-writer-wins semantics."""

    if expected is None:
        _windows_rename_descriptor(
            source_descriptor,
            destination,
            replace=False,
            expected_temp=expected_temp,
            durable=durable,
        )
        return

    current = _path_snapshot(destination, require_regular=True)
    if not _snapshot_matches(expected, current):
        raise _atomic_conflict("atomic-write destination changed before install", destination)
    _windows_rename_descriptor(
        source_descriptor,
        destination,
        replace=True,
        expected_temp=expected_temp,
        durable=durable,
    )


def _install_posix_conditionally(
    source: str | os.PathLike[str],
    destination: str | os.PathLike[str],
    *,
    source_dir_fd: int | None,
    destination_dir_fd: int | None,
    expected: _DestinationSnapshot | None,
    source_descriptor: int,
    expected_temp: FileGeneration,
) -> None:
    """Install with exclusive-create or atomic last-writer-wins semantics."""

    if expected is None:
        _prove_descriptor_generation(
            source_descriptor,
            expected_temp,
            source=source,
            source_dir_fd=source_dir_fd,
        )
        try:
            _posix_native_rename(
                source,
                destination,
                source_dir_fd=source_dir_fd,
                destination_dir_fd=destination_dir_fd,
            )
        except FileExistsError as exc:
            raise _atomic_conflict("atomic-write destination appeared before install", destination) from exc
        return

    current = _path_snapshot(destination, dir_fd=destination_dir_fd, require_regular=True)
    if not _snapshot_matches(expected, current):
        raise _atomic_conflict("atomic-write destination changed before install", destination)
    _prove_descriptor_generation(
        source_descriptor,
        expected_temp,
        source=source,
        source_dir_fd=source_dir_fd,
    )
    os.replace(
        source,
        destination,
        src_dir_fd=source_dir_fd,
        dst_dir_fd=destination_dir_fd,
    )


def _native_conditional_install(
    source: str | os.PathLike[str],
    destination: str | os.PathLike[str],
    *,
    source_dir_fd: int | None,
    destination_dir_fd: int | None,
    expected: _DestinationSnapshot | None,
    temp_identity: tuple[int, int],
    durable: bool,
    expected_temp: FileGeneration | None = None,
) -> None:
    """Shared last-boundary conditional install used by every writer."""

    temp_snapshot = _path_snapshot(source, dir_fd=source_dir_fd, require_regular=True)
    if (
        temp_snapshot is None
        or temp_snapshot.identity != temp_identity
        or temp_snapshot.nlink != 1
        or (expected_temp is not None and not _generation_matches_snapshot(expected_temp, temp_snapshot))
    ):
        raise _atomic_conflict("atomic-write tempfile changed before install", source)
    if os.name == "nt" and (source_dir_fd is not None or destination_dir_fd is not None):
        raise _AtomicInstallUnavailable(errno.ENOTSUP, "Windows has no dirfd conditional installer")

    if os.name == "nt":
        source_descriptor = _windows_open_pinned_source_descriptor(source, durable=durable)
    else:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        source_descriptor = os.open(source, flags, dir_fd=source_dir_fd)
    try:
        pinned_snapshot = _descriptor_snapshot(source_descriptor)
        if not _snapshot_matches(temp_snapshot, pinned_snapshot):
            raise _atomic_conflict("atomic-write tempfile changed while it was pinned", source)
        pinned_generation = expected_temp
        if pinned_generation is None:
            pinned_generation = capture_file_generation(source_descriptor, max_bytes=temp_snapshot.size)
        if not _generation_matches_snapshot(pinned_generation, pinned_snapshot):
            raise _atomic_conflict("atomic-write tempfile changed while it was pinned", source)

        if os.name == "nt":
            _install_windows_conditionally(
                source_descriptor,
                Path(destination),
                expected=expected,
                expected_temp=pinned_generation,
                durable=durable,
            )
        else:
            _install_posix_conditionally(
                source,
                destination,
                source_dir_fd=source_dir_fd,
                destination_dir_fd=destination_dir_fd,
                expected=expected,
                source_descriptor=source_descriptor,
                expected_temp=pinned_generation,
            )
    finally:
        os.close(source_descriptor)


def _descriptor_identity(descriptor: int) -> tuple[int, int] | None:
    """Capture the platform-stable identity of one open tempfile."""

    if os.name == "nt":
        import msvcrt

        return _windows_handle_identity(msvcrt.get_osfhandle(descriptor))
    opened = os.fstat(descriptor)
    return int(opened.st_dev), int(opened.st_ino)


def _descriptor_snapshot(descriptor: int) -> _DestinationSnapshot:
    """Capture a regular, single-name file through its open descriptor."""

    opened = os.fstat(descriptor)
    if os.name == "nt":
        import msvcrt

        captured = _windows_handle_snapshot(msvcrt.get_osfhandle(descriptor))
        if captured is None:
            raise OSError(errno.EIO, "atomic-install source identity is unavailable")
        snapshot, attributes = captured
        if attributes & 0x00000010 or snapshot.nlink != 1:  # FILE_ATTRIBUTE_DIRECTORY
            raise OSError(errno.EINVAL, "atomic-install source must be a single-link regular file")
        return snapshot
    identity = _descriptor_identity(descriptor)
    if identity is None:
        raise OSError(errno.EIO, "atomic-install source identity is unavailable")
    if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
        raise OSError(errno.EINVAL, "atomic-install source must be a single-link regular file")
    return _DestinationSnapshot(
        identity=identity,
        size=int(opened.st_size),
        mtime_ns=int(opened.st_mtime_ns),
        ctime_ns=int(opened.st_ctime_ns),
        nlink=int(opened.st_nlink),
    )


def _generation_matches_snapshot(expected: FileGeneration, actual: _DestinationSnapshot | None) -> bool:
    return bool(
        actual is not None
        and actual.identity == expected.identity
        and actual.size == expected.size
        and actual.mtime_ns == expected.mtime_ns
        and actual.ctime_ns == expected.ctime_ns
        and actual.nlink == expected.nlink
    )


def capture_file_generation(
    descriptor: int,
    *,
    max_bytes: int = 256 * 1024 * 1024,
) -> FileGeneration:
    """Capture the exact regular, single-link file generation behind *descriptor*.

    Producers should flush and fsync the descriptor first, capture this token,
    close every producer handle to the source, and pass the token to
    :func:`conditional_install_file`. The native installer reopens the source,
    re-proves the metadata and SHA-256 token, and retains that descriptor
    through publication. Windows makes the retained handle exclusive and
    renames that exact object rather than resolving the source pathname again.
    """

    expected = _descriptor_snapshot(descriptor)
    if expected.size > max_bytes:
        raise OSError(errno.EFBIG, f"atomic-install source exceeds the {max_bytes}-byte capture limit")
    try:
        original_offset = os.lseek(descriptor, 0, os.SEEK_CUR)
        os.lseek(descriptor, 0, os.SEEK_SET)
    except OSError as exc:
        raise OSError(errno.EINVAL, "atomic-install source descriptor must be seekable and readable") from exc
    digest = hashlib.sha256()
    total = 0
    try:
        while total <= expected.size:
            chunk = os.read(descriptor, min(64 * 1024, expected.size + 1 - total))
            if not chunk:
                break
            digest.update(chunk)
            total += len(chunk)
    finally:
        os.lseek(descriptor, original_offset, os.SEEK_SET)
    after = _descriptor_snapshot(descriptor)
    if total != expected.size or not _snapshot_matches(expected, after):
        raise _atomic_conflict("atomic-install source changed while its content was captured", "<descriptor>")
    return FileGeneration(
        identity=expected.identity,
        size=expected.size,
        mtime_ns=expected.mtime_ns,
        ctime_ns=expected.ctime_ns,
        nlink=expected.nlink,
        sha256=digest.hexdigest(),
    )


def _prove_descriptor_generation(
    descriptor: int,
    expected: FileGeneration,
    *,
    source: str | os.PathLike[str] | None,
    source_dir_fd: int | None,
) -> FileGeneration:
    """Re-prove one pinned producer immediately before native publication."""

    try:
        generation = capture_file_generation(descriptor, max_bytes=expected.size)
    except OSError as exc:
        raise _atomic_conflict("atomic-write tempfile changed before install", source or "<descriptor>") from exc
    if generation != expected:
        raise _atomic_conflict("atomic-write tempfile content changed before install", source or "<descriptor>")
    try:
        after_capture = _descriptor_snapshot(descriptor)
    except OSError as exc:
        raise _atomic_conflict("atomic-write tempfile changed after content capture", source or "<descriptor>") from exc
    if not _generation_matches_snapshot(generation, after_capture):
        raise _atomic_conflict("atomic-write tempfile changed after content capture", source or "<descriptor>")
    if source is not None:
        current = _path_snapshot(source, dir_fd=source_dir_fd, require_regular=True)
        if not _generation_matches_snapshot(generation, current):
            raise _atomic_conflict("atomic-install source changed after content capture", source)
    return generation


def _generation_for_content(snapshot: _DestinationSnapshot, content: bytes) -> FileGeneration:
    return FileGeneration(
        identity=snapshot.identity,
        size=snapshot.size,
        mtime_ns=snapshot.mtime_ns,
        ctime_ns=snapshot.ctime_ns,
        nlink=snapshot.nlink,
        sha256=hashlib.sha256(content).hexdigest(),
    )


def _cleanup_tmp(
    tmp_name: str,
    original_identity: tuple[int, int] | None,
    *,
    dir_fd: int | None = None,
) -> bool:
    """Best-effort remove only the tempfile object created by this write.

    Centralised so both writers share the exact same cleanup policy:
    ``OSError`` is the expected/tolerated failure (e.g. on Windows an
    antivirus or indexer may have the file briefly open). Any other
    exception class (``MemoryError``, ``KeyboardInterrupt`` raised
    re-entrantly) must propagate — we are already inside an outer
    except block raising the original error.
    A callback or concurrent process may replace the random pathname after the
    write stream closes. Cleanup must preserve that replacement. On Windows
    deletion is issued through a second handle after matching the native
    volume/file identity. POSIX has no conditional unlink-by-inode primitive:
    check-then-``unlink`` is itself racy, so failed-write tempfiles are retained
    for explicit recovery instead of risking deletion of a replacement object.
    """
    if original_identity is None:
        return False
    try:
        if os.name == "nt":
            return _windows_delete_identity(Path(tmp_name), original_identity)
        return False
    except OSError as exc:
        # Best-effort cleanup: we run inside the caller's except block,
        # which already re-raises the original error, so re-raising this
        # OSError would mask the real failure. Surface the miss via the
        # opt-in swallow logger (silent unless ROAM_VERBOSE=1) instead of
        # a bare ``pass``. See the docstring for the full OSError rationale.
        log_swallowed("atomic_io._cleanup_tmp", exc)
        return False


def _close_fd_safely(fd: int, *, origin: str) -> None:
    """Best-effort close a raw OS file descriptor.

    Centralised so both writers share the same fd-close policy inside
    their ``except BaseException`` cleanup blocks (see
    :func:`atomic_write_text` stage 2: ``fdopen`` raised, so the raw
    ``fd`` is still open). ``OSError`` is the expected/tolerated failure
    (the fd may already be closed, or an antivirus/indexer may hold it
    briefly on Windows); any other exception class must propagate — we
    are already inside an outer except block raising the original error,
    so re-raising here would mask the real failure. Surface the miss via
    the opt-in swallow logger (silent unless ``ROAM_VERBOSE=1``) instead
    of a bare ``pass``. Mirrors :func:`_cleanup_tmp` for the close path.
    """
    try:
        os.close(fd)
    except OSError as exc:
        log_swallowed(origin, exc)


def _owner_only_sibling_temp(target: Path) -> tuple[int, str]:
    """Create a random sibling tempfile with owner-only security at byte zero."""

    # Keep the dependency lazy: owner_only depends only on the stdlib, but
    # atomic_io is a low-level module used by most of the package.
    from roam.security.owner_only import open_new_owner_only_file

    for _attempt in range(128):
        temporary = target.parent / f".{target.name}.{secrets.token_hex(8)}.tmp"
        try:
            return open_new_owner_only_file(temporary), str(temporary)
        except FileExistsError:
            continue
    raise FileExistsError("could not allocate a unique owner-only atomic-write tempfile")


def _fsync_parent_directory(target: Path) -> None:
    """Persist a completed rename on filesystems that support directory fsync.

    A file ``fsync`` makes the tempfile contents durable, but a power loss can
    still forget the directory entry installed by a rename. POSIX lets us close
    that gap by syncing the containing directory after the native install.
    Windows does not expose a portable directory-fsync primitive through
    Python, so its file flush remains the strongest cross-version guarantee.
    """

    if os.name == "nt":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(target.parent, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_write_bytes_dirfd(
    target: Path,
    content: bytes,
    *,
    prepare_temp: Callable[[str], None] | None,
    prepare_temp_fd: Callable[[int, str], None] | None,
    before_replace: Callable[[], None] | None,
    durable: bool,
    require_absent: bool,
) -> None:
    """POSIX atomic write pinned to an already-open parent directory.

    This variant is deliberately private and selected only by the explicit
    ``secure_parent`` option. Relative ``open``/conditional-rename/``unlink``
    calls keep installation attached to the directory inode even if its lexical
    pathname is concurrently renamed.
    """

    parent_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    parent_flags |= getattr(os, "O_NOFOLLOW", 0)
    parent_fd = os.open(target.parent, parent_flags)
    fd = -1
    fdopen_succeeded = False
    temp_name: str | None = None
    temp_identity: tuple[int, int] | None = None
    try:
        lexical_parent = os.stat(target.parent, follow_symlinks=False)
        opened_parent = os.fstat(parent_fd)
        if (lexical_parent.st_dev, lexical_parent.st_ino) != (opened_parent.st_dev, opened_parent.st_ino):
            raise RuntimeError("atomic-write parent changed while opening its directory handle")

        open_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
        open_flags |= getattr(os, "O_NOFOLLOW", 0)
        for _attempt in range(128):
            candidate = f".{target.name}.{secrets.token_hex(8)}.tmp"
            try:
                fd = os.open(candidate, open_flags, 0o600, dir_fd=parent_fd)
            except FileExistsError:
                continue
            temp_name = candidate
            temp_identity = _descriptor_identity(fd)
            if temp_identity is None:
                raise OSError(errno.EIO, "atomic-write tempfile identity is unavailable", candidate)
            break
        if temp_name is None:
            raise FileExistsError("could not allocate a unique atomic-write tempfile")

        temp_path = str(target.parent / temp_name)
        if prepare_temp_fd is not None:
            prepare_temp_fd(fd, temp_path)
        if prepare_temp is not None:
            prepare_temp(temp_path)
        fh = os.fdopen(fd, "wb")
        fdopen_succeeded = True
        with fh:
            fh.write(content)
            if durable:
                fh.flush()
                os.fsync(fh.fileno())
        written_snapshot = _path_snapshot(temp_name, dir_fd=parent_fd, require_regular=True)
        if written_snapshot is None or written_snapshot.identity != temp_identity:
            raise _atomic_conflict("atomic-write tempfile changed after writing", temp_name)
        expected_temp = _generation_for_content(written_snapshot, content)
        destination_snapshot = _path_snapshot(
            target.name,
            dir_fd=parent_fd,
            require_regular=True,
        )
        if require_absent and destination_snapshot is not None:
            raise _atomic_conflict("atomic-write destination already exists", target)
        if before_replace is not None:
            before_replace()
        assert temp_identity is not None
        _native_conditional_install(
            temp_name,
            target.name,
            source_dir_fd=parent_fd,
            destination_dir_fd=parent_fd,
            expected=destination_snapshot,
            temp_identity=temp_identity,
            durable=durable,
            expected_temp=expected_temp,
        )
        temp_name = None
        if durable:
            os.fsync(parent_fd)
    except BaseException:
        if fd >= 0 and not fdopen_succeeded:
            _close_fd_safely(fd, origin="atomic_io._atomic_write_bytes_dirfd.fd_close")
        if temp_name is not None:
            _cleanup_tmp(temp_name, temp_identity, dir_fd=parent_fd)
        raise
    finally:
        os.close(parent_fd)


def conditional_install_file(
    source: PathLike,
    destination: PathLike,
    *,
    source_generation: FileGeneration,
    before_install: Callable[[], None] | None = None,
    durable: bool = True,
) -> None:
    """Conditionally install an already-written sibling file.

    This is the transfer counterpart to :func:`atomic_write_bytes` for
    producers such as SQLite that must build their artifact through another
    API. ``source_generation`` must come from
    :func:`capture_file_generation` after the producer flushes and fsyncs its
    single-link regular file, and after every producer handle has been closed.
    The source and destination must be siblings.

    The destination generation is captured immediately before
    ``before_install`` runs and checked once more at the native boundary.
    Absent destinations are true create-only operations. Existing destinations
    use atomic last-writer-wins replacement after that check: a writer racing
    after the check is ordered normally, and no successful move is ever rolled
    back based on a later pathname observation. The source is retained on
    failure unless native publication already consumed it. Ownership and
    cleanup therefore remain with the caller. With ``durable=True`` (the
    default), POSIX syncs the pinned parent directory and Windows flushes the
    exclusively pinned file handle before and after its handle-bound rename.
    """

    source_path = Path(os.path.abspath(os.fspath(source)))
    target = Path(os.path.abspath(os.fspath(destination)))
    source_parent = os.path.normcase(os.path.abspath(os.fspath(source_path.parent)))
    target_parent = os.path.normcase(os.path.abspath(os.fspath(target.parent)))
    if source_parent != target_parent or source_path.name == target.name:
        raise ValueError("atomic-install source and destination must be distinct siblings")

    expected_source = source_generation

    if os.name == "nt":
        parent_opened = _windows_open_identity_handle(
            target.parent,
            delete_access=False,
            share_delete=False,
        )
        if parent_opened is None:
            raise OSError(errno.EACCES, "cannot pin atomic-install parent", str(target.parent))
        parent_handle, kernel32 = parent_opened
        parent_identity = _windows_handle_identity(parent_handle)
        try:
            if parent_identity is None or _windows_path_identity(target.parent) != parent_identity:
                raise _atomic_conflict("atomic-install parent changed while it was pinned", target.parent)
            source_snapshot = _path_snapshot(source_path, require_regular=True)
            if not _generation_matches_snapshot(expected_source, source_snapshot):
                raise _atomic_conflict("atomic-install source changed while it was inspected", source_path)
            destination_snapshot = _path_snapshot(target, require_regular=True)
            if before_install is not None:
                before_install()
            if (
                _windows_handle_identity(parent_handle) != parent_identity
                or _windows_path_identity(target.parent) != parent_identity
            ):
                raise _atomic_conflict("atomic-install state changed before installation", target)
            _native_conditional_install(
                source_path,
                target,
                source_dir_fd=None,
                destination_dir_fd=None,
                expected=destination_snapshot,
                temp_identity=expected_source.identity,
                durable=durable,
                expected_temp=expected_source,
            )
        finally:
            kernel32.CloseHandle(parent_handle)
        return

    parent_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    parent_flags |= getattr(os, "O_NOFOLLOW", 0)
    parent_fd = os.open(target.parent, parent_flags)
    try:
        lexical_parent = os.stat(target.parent, follow_symlinks=False)
        opened_parent = os.fstat(parent_fd)
        parent_identity = int(opened_parent.st_dev), int(opened_parent.st_ino)
        if (int(lexical_parent.st_dev), int(lexical_parent.st_ino)) != parent_identity:
            raise _atomic_conflict("atomic-install parent changed while it was pinned", target.parent)
        source_snapshot = _path_snapshot(source_path.name, dir_fd=parent_fd, require_regular=True)
        if not _generation_matches_snapshot(expected_source, source_snapshot):
            raise _atomic_conflict("atomic-install source changed while it was inspected", source_path)
        destination_snapshot = _path_snapshot(target.name, dir_fd=parent_fd, require_regular=True)
        if before_install is not None:
            before_install()
        lexical_parent = os.stat(target.parent, follow_symlinks=False)
        if (int(lexical_parent.st_dev), int(lexical_parent.st_ino)) != parent_identity:
            raise _atomic_conflict("atomic-install state changed before installation", target)
        _native_conditional_install(
            source_path.name,
            target.name,
            source_dir_fd=parent_fd,
            destination_dir_fd=parent_fd,
            expected=destination_snapshot,
            temp_identity=expected_source.identity,
            durable=durable,
            expected_temp=expected_source,
        )
        if durable:
            os.fsync(parent_fd)
    finally:
        os.close(parent_fd)


def atomic_write_text(path: PathLike, content: str, *, encoding: str = "utf-8") -> None:
    """Encode *content* and use the shared conditional byte installer."""
    atomic_write_bytes(path, content.encode(encoding))


def atomic_write_bytes(
    path: PathLike,
    content: bytes,
    *,
    prepare_temp: Callable[[str], None] | None = None,
    prepare_temp_fd: Callable[[int, str], None] | None = None,
    before_replace: Callable[[], None] | None = None,
    durable: bool = False,
    create_parents: bool = True,
    secure_parent: bool = False,
    require_absent: bool = False,
) -> None:
    """Atomically write *content* (raw bytes) to *path*.

    Same contract as :func:`atomic_write_text` but for binary payloads
    (e.g. cosign signatures, gzip-compressed bundles). Same three-stage
    cleanup discipline — see :func:`atomic_write_text` docstring.

    ``prepare_temp`` and ``prepare_temp_fd`` run after the same-directory
    tempfile is created but before it receives any bytes. The latter receives
    the still-open descriptor and is preferred for race-resistant permission
    changes. Security-sensitive callers can use either callback to install and
    verify owner-only ACLs without reimplementing the atomic-write protocol.

    ``before_replace`` runs after the complete payload has been written (and
    synced when ``durable`` is true), after the destination generation has
    been captured, and immediately before native conditional installation.
    Callers can use it for a final content or path-integrity check. The native
    installer checks the captured destination once more before publication.
    A replacement visible at that check is preserved and reported; a writer
    racing afterward is ordered by ordinary atomic last-writer-wins semantics.

    ``create_parents=False`` is intended for security-sensitive callers that
    validated an existing directory themselves and do not want this helper to
    create or traverse missing ancestors. All callbacks must raise on failure.

    ``secure_parent=True`` additionally pins the existing parent directory by
    file descriptor and performs tempfile creation, replacement, and cleanup
    relative to that descriptor on POSIX. Python has no portable equivalent on
    Windows. On Windows it instead creates the sibling tempfile with a
    protected current-user DACL supplied to ``CreateFileW``, before byte one;
    callbacks then validate that exact open file before content is written.

    ``require_absent=True`` provides create-only publication. If the target
    already exists, or appears at the native installation boundary, its exact
    generation is preserved and :class:`FileExistsError` is raised.
    """
    target = Path(os.path.abspath(os.fspath(path)))
    if create_parents:
        target.parent.mkdir(parents=True, exist_ok=True)
    if secure_parent and os.name != "nt":
        _atomic_write_bytes_dirfd(
            target,
            content,
            prepare_temp=prepare_temp,
            prepare_temp_fd=prepare_temp_fd,
            before_replace=before_replace,
            durable=durable,
            require_absent=require_absent,
        )
        return
    if secure_parent:
        fd, tmp_name = _owner_only_sibling_temp(target)
    else:
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{target.name}.",
            suffix=".tmp",
            dir=str(target.parent),
        )
    fdopen_succeeded = False
    temp_identity: tuple[int, int] | None = None
    try:
        temp_identity = _descriptor_identity(fd)
        if temp_identity is None:
            raise OSError(errno.EIO, "atomic-write tempfile identity is unavailable", tmp_name)
        if prepare_temp_fd is not None:
            prepare_temp_fd(fd, tmp_name)
        if prepare_temp is not None:
            prepare_temp(tmp_name)
        fh = os.fdopen(fd, "wb")
        fdopen_succeeded = True
        with fh:
            fh.write(content)
            if durable:
                fh.flush()
                os.fsync(fh.fileno())
        written_snapshot = _path_snapshot(tmp_name, require_regular=True)
        if written_snapshot is None or written_snapshot.identity != temp_identity:
            raise _atomic_conflict("atomic-write tempfile changed after writing", tmp_name)
        expected_temp = _generation_for_content(written_snapshot, content)
        destination_snapshot = _path_snapshot(target, require_regular=True)
        if require_absent and destination_snapshot is not None:
            raise _atomic_conflict("atomic-write destination already exists", target)
        if before_replace is not None:
            before_replace()
        assert temp_identity is not None
        _native_conditional_install(
            tmp_name,
            target,
            source_dir_fd=None,
            destination_dir_fd=None,
            expected=destination_snapshot,
            temp_identity=temp_identity,
            durable=durable,
            expected_temp=expected_temp,
        )
        if durable:
            _fsync_parent_directory(target)
    except BaseException:
        if not fdopen_succeeded:
            _close_fd_safely(fd, origin="atomic_io.atomic_write_bytes.fd_close")
        _cleanup_tmp(tmp_name, temp_identity)
        raise


def atomic_write_json(
    path: PathLike,
    data: Any,
    *,
    indent: int | None = 2,
    sort_keys: bool = False,
) -> None:
    """Atomically write *data* as JSON to *path*.

    Trailing newline included for POSIX-friendly ``cat``-ability. Uses
    :func:`atomic_write_text` under the hood so the same conditional native
    install guarantee holds.

    Policy note
    -----------
    This function deliberately does NOT expose a ``default=`` parameter
    to :func:`json.dumps`. Callers that need non-JSON-native types like
    :class:`datetime.datetime` must pre-serialize, OR wrap this function
    in a thin helper that encapsulates the serialization policy (see
    ``cmd_pr_bundle._atomic_write_bundle`` for the bundle-specific
    policy that injects ``updated_at`` and uses ``default=str`` to
    round-trip datetime fields).

    Adding ``default=`` to the canonical helper would silently weaken
    type discipline across all callers. Keep this policy.
    """
    payload = json.dumps(data, indent=indent, sort_keys=sort_keys, ensure_ascii=False)
    atomic_write_text(path, payload + "\n")
