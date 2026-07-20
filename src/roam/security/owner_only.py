"""Cross-platform owner-only filesystem primitives.

POSIX mode bits do not describe Windows ACLs. Security-sensitive local
artifacts use these helpers so ``owner-only`` means that only the current
token user receives access. Windows mutations and checks are performed on one
open handle, and owner-only directories carry inheritable ACEs so children are
private from the instant they are created.
"""

from __future__ import annotations

import os
import stat
from contextlib import contextmanager
from pathlib import Path
from typing import TypeAlias

PathLike: TypeAlias = str | os.PathLike[str]

_SE_FILE_OBJECT = 1
_OWNER_SECURITY_INFORMATION = 0x00000001
_DACL_SECURITY_INFORMATION = 0x00000004
_PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000
_SE_DACL_PROTECTED = 0x1000
_FILE_ALL_ACCESS = 0x001F01FF
_FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400


def _windows_sid_to_text(advapi32, kernel32, sid) -> str | None:
    import ctypes
    from ctypes import wintypes

    rendered = wintypes.LPWSTR()
    if not sid or not advapi32.ConvertSidToStringSidW(sid, ctypes.byref(rendered)):
        return None
    try:
        return str(rendered.value or "") or None
    finally:
        kernel32.LocalFree(rendered)


def _windows_current_user_sid() -> str | None:
    import ctypes
    from ctypes import wintypes

    token_query = 0x0008
    token_user_class = 1

    class _SidAndAttributes(ctypes.Structure):
        _fields_ = [("sid", wintypes.LPVOID), ("attributes", wintypes.DWORD)]

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    token = wintypes.HANDLE()

    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    advapi32.OpenProcessToken.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    )
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    )
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    advapi32.ConvertSidToStringSidW.argtypes = (
        wintypes.LPVOID,
        ctypes.POINTER(wintypes.LPWSTR),
    )
    advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.LocalFree.argtypes = (wintypes.LPVOID,)

    if not advapi32.OpenProcessToken(
        kernel32.GetCurrentProcess(),
        token_query,
        ctypes.byref(token),
    ):
        return None
    try:
        needed = wintypes.DWORD()
        advapi32.GetTokenInformation(token, token_user_class, None, 0, ctypes.byref(needed))
        if not needed.value:
            return None
        token_info = ctypes.create_string_buffer(needed.value)
        if not advapi32.GetTokenInformation(
            token,
            token_user_class,
            token_info,
            needed,
            ctypes.byref(needed),
        ):
            return None
        token_user = ctypes.cast(token_info, ctypes.POINTER(_SidAndAttributes)).contents
        return _windows_sid_to_text(advapi32, kernel32, token_user.sid)
    finally:
        if token:
            kernel32.CloseHandle(token)


def _windows_descriptor_for_current_user(*, directory: bool):
    """Return ``(descriptor, dacl, kernel32)`` allocated by LocalAlloc."""

    import ctypes
    from ctypes import wintypes

    sid = _windows_current_user_sid()
    if not sid:
        return None
    sddl_revision_1 = 1
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    descriptor = wintypes.LPVOID()
    dacl = wintypes.LPVOID()
    present = wintypes.BOOL()
    defaulted = wintypes.BOOL()
    inheritance = "OICI" if directory else ""

    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.DWORD),
    )
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    advapi32.GetSecurityDescriptorDacl.argtypes = (
        wintypes.LPVOID,
        ctypes.POINTER(wintypes.BOOL),
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.BOOL),
    )
    advapi32.GetSecurityDescriptorDacl.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = (wintypes.LPVOID,)

    if not advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        f"O:{sid}D:P(A;{inheritance};FA;;;{sid})",
        sddl_revision_1,
        ctypes.byref(descriptor),
        None,
    ):
        return None
    if (
        not advapi32.GetSecurityDescriptorDacl(
            descriptor,
            ctypes.byref(present),
            ctypes.byref(dacl),
            ctypes.byref(defaulted),
        )
        or not present.value
        or not dacl
    ):
        kernel32.LocalFree(descriptor)
        return None
    return descriptor, dacl, kernel32


def _windows_open_path_handle(
    path: PathLike,
    *,
    write_dacl: bool,
    share_delete: bool = True,
    delete_access: bool = False,
):
    import ctypes
    from ctypes import wintypes

    read_control = 0x00020000
    write_dac = 0x00040000
    delete = 0x00010000
    share_read_write = 0x00000001 | 0x00000002
    open_existing = 3
    file_flag_backup_semantics = 0x02000000
    file_flag_open_reparse_point = 0x00200000

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
    desired_access = read_control
    desired_access |= write_dac if write_dacl else 0
    desired_access |= delete if delete_access else 0
    handle = kernel32.CreateFileW(
        str(Path(path)),
        desired_access,
        share_read_write | (0x00000004 if share_delete else 0),
        None,
        open_existing,
        file_flag_backup_semantics | file_flag_open_reparse_point,
        None,
    )
    invalid_handle = ctypes.c_void_p(-1).value
    if not handle or handle == invalid_handle:
        return None
    return handle, kernel32


def _windows_handle_identity(handle) -> tuple[int, int] | None:
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
    if info.file_attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
        return None
    file_index = (int(info.file_index_high) << 32) | int(info.file_index_low)
    return int(info.volume_serial_number), file_index


def _windows_path_identity(path: PathLike) -> tuple[int, int] | None:
    opened = _windows_open_path_handle(path, write_dacl=False)
    if opened is None:
        return None
    handle, kernel32 = opened
    try:
        return _windows_handle_identity(handle)
    finally:
        kernel32.CloseHandle(handle)


def _windows_handle_is_owner_only(handle, *, require_protected: bool) -> bool:
    import ctypes
    from ctypes import wintypes

    access_allowed_ace_type = 0

    class _Acl(ctypes.Structure):
        _fields_ = [
            ("revision", wintypes.BYTE),
            ("sbz1", wintypes.BYTE),
            ("size", wintypes.WORD),
            ("ace_count", wintypes.WORD),
            ("sbz2", wintypes.WORD),
        ]

    class _AceHeader(ctypes.Structure):
        _fields_ = [
            ("ace_type", wintypes.BYTE),
            ("ace_flags", wintypes.BYTE),
            ("ace_size", wintypes.WORD),
        ]

    class _AccessAllowedAce(ctypes.Structure):
        _fields_ = [
            ("header", _AceHeader),
            ("mask", wintypes.DWORD),
            ("sid_start", wintypes.DWORD),
        ]

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    owner = wintypes.LPVOID()
    dacl = wintypes.LPVOID()
    descriptor = wintypes.LPVOID()
    control = wintypes.WORD()
    revision = wintypes.DWORD()
    ace_pointer = wintypes.LPVOID()

    advapi32.GetSecurityInfo.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.LPVOID),
    )
    advapi32.GetSecurityInfo.restype = wintypes.DWORD
    advapi32.GetSecurityDescriptorControl.argtypes = (
        wintypes.LPVOID,
        ctypes.POINTER(wintypes.WORD),
        ctypes.POINTER(wintypes.DWORD),
    )
    advapi32.GetSecurityDescriptorControl.restype = wintypes.BOOL
    advapi32.GetAce.argtypes = (
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.LPVOID),
    )
    advapi32.GetAce.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = (wintypes.LPVOID,)

    information = _OWNER_SECURITY_INFORMATION | _DACL_SECURITY_INFORMATION
    result = advapi32.GetSecurityInfo(
        handle,
        _SE_FILE_OBJECT,
        information,
        ctypes.byref(owner),
        None,
        ctypes.byref(dacl),
        None,
        ctypes.byref(descriptor),
    )
    if result != 0 or not descriptor or not owner or not dacl:
        return False
    try:
        current_sid = _windows_current_user_sid()
        owner_sid = _windows_sid_to_text(advapi32, kernel32, owner)
        if not current_sid or owner_sid != current_sid:
            return False
        if not advapi32.GetSecurityDescriptorControl(
            descriptor,
            ctypes.byref(control),
            ctypes.byref(revision),
        ):
            return False
        if require_protected and not control.value & _SE_DACL_PROTECTED:
            return False
        acl = ctypes.cast(dacl, ctypes.POINTER(_Acl)).contents
        if acl.ace_count != 1 or acl.size < ctypes.sizeof(_Acl):
            return False
        if not advapi32.GetAce(dacl, 0, ctypes.byref(ace_pointer)) or not ace_pointer:
            return False
        ace = ctypes.cast(ace_pointer, ctypes.POINTER(_AccessAllowedAce)).contents
        if (
            ace.header.ace_type != access_allowed_ace_type
            or ace.header.ace_size < ctypes.sizeof(_AccessAllowedAce)
            or int(ace.mask) & _FILE_ALL_ACCESS != _FILE_ALL_ACCESS
        ):
            return False
        sid_address = int(ace_pointer.value) + _AccessAllowedAce.sid_start.offset
        ace_sid = _windows_sid_to_text(
            advapi32,
            kernel32,
            wintypes.LPVOID(sid_address),
        )
        return ace_sid == current_sid
    finally:
        kernel32.LocalFree(descriptor)


def _windows_restrict_handle_to_current_user(handle, *, directory: bool) -> bool:
    import ctypes
    from ctypes import wintypes

    converted = _windows_descriptor_for_current_user(directory=directory)
    if converted is None:
        return False
    descriptor, dacl, kernel32 = converted
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    advapi32.SetSecurityInfo.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.LPVOID,
    )
    advapi32.SetSecurityInfo.restype = wintypes.DWORD
    try:
        information = _DACL_SECURITY_INFORMATION | _PROTECTED_DACL_SECURITY_INFORMATION
        return (
            advapi32.SetSecurityInfo(
                handle,
                _SE_FILE_OBJECT,
                information,
                None,
                None,
                dacl,
                None,
            )
            == 0
        )
    finally:
        kernel32.LocalFree(descriptor)


def _windows_path_is_owner_only(path: PathLike) -> bool:
    opened = _windows_open_path_handle(path, write_dacl=False)
    if opened is None:
        return False
    handle, kernel32 = opened
    try:
        identity = _windows_handle_identity(handle)
        return bool(
            identity is not None
            and _windows_handle_is_owner_only(handle, require_protected=False)
            and _windows_path_identity(path) == identity
        )
    finally:
        kernel32.CloseHandle(handle)


def _windows_restrict_path_to_current_user(path: PathLike) -> bool:
    opened = _windows_open_path_handle(path, write_dacl=True)
    if opened is None:
        return False
    handle, kernel32 = opened
    try:
        identity = _windows_handle_identity(handle)
        if identity is None:
            return False
        try:
            directory = Path(path).is_dir()
        except OSError:
            return False
        return bool(
            _windows_restrict_handle_to_current_user(handle, directory=directory)
            and _windows_handle_is_owner_only(handle, require_protected=True)
            and _windows_path_identity(path) == identity
        )
    finally:
        kernel32.CloseHandle(handle)


def _windows_create_owner_only_directory(path: PathLike) -> bool:
    import ctypes
    from ctypes import wintypes

    class _SecurityAttributes(ctypes.Structure):
        _fields_ = [
            ("length", wintypes.DWORD),
            ("security_descriptor", wintypes.LPVOID),
            ("inherit_handle", wintypes.BOOL),
        ]

    converted = _windows_descriptor_for_current_user(directory=True)
    if converted is None:
        return False
    descriptor, _dacl, kernel32 = converted
    kernel32.CreateDirectoryW.argtypes = (
        wintypes.LPCWSTR,
        ctypes.POINTER(_SecurityAttributes),
    )
    kernel32.CreateDirectoryW.restype = wintypes.BOOL
    attributes = _SecurityAttributes(
        ctypes.sizeof(_SecurityAttributes),
        descriptor,
        False,
    )
    try:
        if not kernel32.CreateDirectoryW(str(Path(path)), ctypes.byref(attributes)):
            return False
    finally:
        kernel32.LocalFree(descriptor)
    return _windows_path_is_owner_only(path)


def _windows_mark_handle_for_delete(handle, kernel32) -> bool:
    """Mark the file object referenced by *handle* for deletion on close."""

    import ctypes
    from ctypes import wintypes

    class _FileDispositionInfo(ctypes.Structure):
        _fields_ = [("delete_file", wintypes.BOOLEAN)]

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


def file_descriptor_identity(descriptor: int) -> tuple[int, int] | None:
    """Return the native identity of one open descriptor, if available."""

    try:
        if os.name == "nt":
            import msvcrt

            return _windows_handle_identity(msvcrt.get_osfhandle(descriptor))
        opened = os.fstat(descriptor)
        return int(opened.st_dev), int(opened.st_ino)
    except (OSError, TypeError, ValueError):
        return None


def delete_file_if_matches_identity(path: PathLike, identity: tuple[int, int]) -> bool:
    """Delete exactly the expected Windows file object; retain on POSIX.

    POSIX has no portable unlink-by-inode primitive, so it deliberately
    returns ``False`` without issuing a pathname unlink. Windows opens a
    dedicated DELETE handle, proves its native identity, and marks that exact
    object for deletion. A replacement installed before or after the handle
    is opened is never selected by pathname after the proof.
    """

    if os.name != "nt":
        return False
    opened = _windows_open_path_handle(
        path,
        write_dacl=False,
        delete_access=True,
    )
    if opened is None:
        return False
    handle, kernel32 = opened
    try:
        return bool(_windows_handle_identity(handle) == identity and _windows_mark_handle_for_delete(handle, kernel32))
    finally:
        kernel32.CloseHandle(handle)


def delete_file_if_matches_descriptor(path: PathLike, descriptor: int) -> bool:
    """Delete *path* only when it still names the descriptor's file object."""

    if os.name == "nt":
        try:
            import ctypes
            import msvcrt

            handle = msvcrt.get_osfhandle(descriptor)
            identity = _windows_handle_identity(handle)
            if identity is None or _windows_path_identity(path) != identity:
                return False
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            return _windows_mark_handle_for_delete(handle, kernel32)
        except (OSError, TypeError, ValueError):
            return False
    identity = file_descriptor_identity(descriptor)
    return bool(identity is not None and delete_file_if_matches_identity(path, identity))


def _windows_open_new_owner_only_file(
    path: PathLike,
    *,
    allow_delete_sharing: bool = False,
    request_delete_access: bool = True,
) -> int:
    """Create a file with its protected DACL supplied to ``CreateFileW``."""

    import ctypes
    import msvcrt
    from ctypes import wintypes

    class _SecurityAttributes(ctypes.Structure):
        _fields_ = [
            ("length", wintypes.DWORD),
            ("security_descriptor", wintypes.LPVOID),
            ("inherit_handle", wintypes.BOOL),
        ]

    converted = _windows_descriptor_for_current_user(directory=False)
    if converted is None:
        raise PermissionError(f"cannot build owner-only file descriptor: {path}")
    descriptor, _dacl, kernel32 = converted
    generic_read = 0x80000000
    generic_write = 0x40000000
    delete = 0x00010000
    share_read_write = 0x00000001 | 0x00000002
    share_delete = 0x00000004
    has_delete_access = request_delete_access and not allow_delete_sharing
    create_new = 1
    file_attribute_normal = 0x00000080
    kernel32.CreateFileW.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(_SecurityAttributes),
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    kernel32.CreateFileW.restype = wintypes.HANDLE
    attributes = _SecurityAttributes(
        ctypes.sizeof(_SecurityAttributes),
        descriptor,
        False,
    )
    try:
        handle = kernel32.CreateFileW(
            str(Path(path)),
            generic_read | generic_write | (delete if has_delete_access else 0),
            share_read_write | (share_delete if allow_delete_sharing else 0),
            ctypes.byref(attributes),
            create_new,
            file_attribute_normal,
            None,
        )
        error = ctypes.get_last_error()
    finally:
        kernel32.LocalFree(descriptor)
    invalid_handle = ctypes.c_void_p(-1).value
    if not handle or handle == invalid_handle:
        if error in {80, 183}:
            raise FileExistsError(error, "file already exists", str(path))
        raise ctypes.WinError(error)

    cleanup_handle = None
    cleanup_kernel32 = kernel32
    cleanup_is_bound = False
    try:
        identity = _windows_handle_identity(handle)
        if allow_delete_sharing:
            cleanup_opened = _windows_open_path_handle(
                path,
                write_dacl=False,
                delete_access=True,
            )
            if cleanup_opened is None:
                raise PermissionError(f"cannot bind new file cleanup handle: {path}")
            cleanup_handle, cleanup_kernel32 = cleanup_opened
            cleanup_is_bound = identity is not None and _windows_handle_identity(cleanup_handle) == identity
            if not cleanup_is_bound:
                raise PermissionError(f"cannot bind new file cleanup handle: {path}")
        if (
            identity is None
            or _windows_path_identity(path) != identity
            or not _windows_handle_is_owner_only(handle, require_protected=True)
        ):
            raise PermissionError(f"new file is not stable and owner-only: {path}")
        file_descriptor = msvcrt.open_osfhandle(
            int(handle),
            os.O_RDWR | getattr(os, "O_BINARY", 0),
        )
    except BaseException:
        try:
            if allow_delete_sharing:
                if cleanup_handle is not None and cleanup_is_bound:
                    _windows_mark_handle_for_delete(cleanup_handle, cleanup_kernel32)
            elif has_delete_access:
                _windows_mark_handle_for_delete(handle, kernel32)
        finally:
            if cleanup_handle is not None:
                cleanup_kernel32.CloseHandle(cleanup_handle)
            kernel32.CloseHandle(handle)
        raise
    if cleanup_handle is not None:
        cleanup_kernel32.CloseHandle(cleanup_handle)
    return file_descriptor


def _same_path_and_descriptor(path: PathLike, descriptor: int) -> bool:
    try:
        by_path = os.lstat(path)
        by_descriptor = os.fstat(descriptor)
    except OSError:
        return False
    return (by_path.st_dev, by_path.st_ino) == (
        by_descriptor.st_dev,
        by_descriptor.st_ino,
    )


def path_is_owner_only(path: PathLike) -> bool:
    """Return whether only the current OS user can access *path*."""

    try:
        if os.name == "nt":
            return _windows_path_is_owner_only(path)
        value = os.stat(path, follow_symlinks=False)
        return value.st_uid == os.geteuid() and not bool(stat.S_IMODE(value.st_mode) & (stat.S_IRWXG | stat.S_IRWXO))
    except (AttributeError, OSError, TypeError, ValueError):
        return False


def restrict_path_to_current_user(path: PathLike) -> bool:
    """Install owner-only permissions on an existing file or directory."""

    try:
        if os.name == "nt":
            return _windows_restrict_path_to_current_user(path)
        os.chmod(
            path,
            0o700 if Path(path).is_dir() else 0o600,
            follow_symlinks=False,
        )
        return True
    except (AttributeError, OSError, TypeError, ValueError):
        return False


def ensure_owner_only_path(path: PathLike) -> bool:
    """Restrict *path* and verify the resulting owner-only policy."""

    return restrict_path_to_current_user(path) and path_is_owner_only(path)


def file_descriptor_is_owner_only(descriptor: int, path: PathLike) -> bool:
    """Non-mutating proof that an open regular file is private and stable.

    Read-only commands must not repair ACLs as a side effect.  This companion
    to :func:`ensure_owner_only_file_descriptor` verifies the same path,
    identity, link-count, and owner-only policy while never calling chmod or
    changing a Windows DACL.
    """

    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1 or not _same_path_and_descriptor(path, descriptor):
            return False
        if os.name == "nt":
            import msvcrt

            handle = msvcrt.get_osfhandle(descriptor)
            identity = _windows_handle_identity(handle)
            if (
                identity is None
                or _windows_path_identity(path) != identity
                or not _windows_handle_is_owner_only(handle, require_protected=True)
            ):
                return False
        elif opened.st_uid != os.geteuid() or stat.S_IMODE(opened.st_mode) & 0o077:
            return False
        return os.fstat(descriptor).st_nlink == 1 and _same_path_and_descriptor(path, descriptor)
    except (AttributeError, OSError, TypeError, ValueError):
        return False


def ensure_owner_only_file_descriptor(descriptor: int, path: PathLike) -> bool:
    """Restrict one open file and prove *path* still names that same object."""

    try:
        if not _same_path_and_descriptor(path, descriptor):
            return False
        opened = os.fstat(descriptor)
        # A hard link lets a caller redirect chmod/DACL mutation and later
        # writes to an unrelated file owned by the same account.  Sensitive
        # state files must have exactly one name before any mutation occurs.
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
            return False
        if os.name == "nt":
            import msvcrt

            handle = msvcrt.get_osfhandle(descriptor)
            identity = _windows_handle_identity(handle)
            if identity is None or _windows_path_identity(path) != identity:
                return False
            # CRT descriptors opened for ordinary writes do not carry
            # WRITE_DAC. Open a dedicated ACL handle, bind it to the same
            # native volume/file index, and perform mutation + validation on
            # that one handle. This avoids pathname-based restrict/check races
            # without pretending DuplicateHandle can elevate access rights.
            acl_opened = _windows_open_path_handle(path, write_dacl=True)
            if acl_opened is None:
                return False
            acl_handle, kernel32 = acl_opened
            try:
                if _windows_handle_identity(acl_handle) != identity:
                    return False
                if not _windows_restrict_handle_to_current_user(
                    acl_handle,
                    directory=False,
                ):
                    return False
                if not _windows_handle_is_owner_only(
                    acl_handle,
                    require_protected=True,
                ):
                    return False
                if _windows_handle_identity(acl_handle) != identity:
                    return False
            finally:
                kernel32.CloseHandle(acl_handle)
            if _windows_path_identity(path) != identity:
                return False
        else:
            os.fchmod(descriptor, 0o600)
            secured = os.fstat(descriptor)
            if secured.st_uid != os.geteuid() or stat.S_IMODE(secured.st_mode) & 0o077:
                return False
        return os.fstat(descriptor).st_nlink == 1 and _same_path_and_descriptor(path, descriptor)
    except (AttributeError, OSError, TypeError, ValueError):
        return False


def create_owner_only_directory(path: PathLike) -> bool:
    """Create one directory with owner-only security applied at creation."""

    try:
        if os.name == "nt":
            return _windows_create_owner_only_directory(path)
        os.mkdir(path, 0o700)
        return path_is_owner_only(path)
    except (AttributeError, OSError, TypeError, ValueError):
        return False


def open_new_owner_only_file(
    path: PathLike,
    *,
    allow_delete_sharing: bool = False,
    request_delete_access: bool = True,
) -> int:
    """Exclusively create one empty owner-only file and return its descriptor.

    Windows denies path replacement for the descriptor lifetime by default.
    SQLite producers may opt into delete sharing because its VFS requires it;
    those callers must re-prove descriptor/path identity after SQLite closes.
    Long-lived lock descriptors may set ``request_delete_access=False`` so
    ordinary peer opens remain compatible while every live descriptor still
    denies delete sharing. A rejected creation in that mode is retained because
    the primary handle intentionally cannot perform identity-bound cleanup.
    """

    if os.name == "nt":
        return _windows_open_new_owner_only_file(
            path,
            allow_delete_sharing=allow_delete_sharing,
            request_delete_access=request_delete_access,
        )
    flags = os.O_CREAT | os.O_EXCL | os.O_RDWR | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        accepted = ensure_owner_only_file_descriptor(descriptor, path)
    except BaseException:
        os.close(descriptor)
        raise
    if not accepted:
        os.close(descriptor)
        # POSIX has no portable conditional-unlink operation bound to this
        # descriptor.  A stat-then-unlink cleanup can delete a replacement
        # installed between those calls, so fail safe by retaining the random
        # owner-only file.  Callers receive an error and may inspect/reap it
        # through a separately serialized, identity-safe lifecycle.
        raise PermissionError(f"new file is not stable and owner-only: {path}")
    return descriptor


@contextmanager
def pinned_owner_only_directory(path: PathLike):
    """Hold an owner-only directory stable for a sensitive operation.

    Windows opens the directory without ``FILE_SHARE_DELETE``. That prevents a
    writable ancestor from renaming or replacing the directory between an ACL
    check and child creation. POSIX pins the directory inode and verifies the
    lexical path before and after the operation; callers that write atomically
    should additionally use dir-fd-relative operations where available.
    """

    target = Path(path)
    completed = False
    if os.name == "nt":
        opened = _windows_open_path_handle(
            target,
            write_dacl=False,
            share_delete=False,
        )
        if opened is None:
            raise PermissionError(f"cannot pin owner-only directory: {target}")
        handle, kernel32 = opened
        identity = _windows_handle_identity(handle)
        try:
            try:
                value = os.lstat(target)
            except OSError as exc:
                raise PermissionError(f"cannot inspect owner-only directory: {target}") from exc
            if (
                identity is None
                or not stat.S_ISDIR(value.st_mode)
                or stat.S_ISLNK(value.st_mode)
                or _windows_path_identity(target) != identity
                or not _windows_handle_is_owner_only(handle, require_protected=True)
            ):
                raise PermissionError(f"directory is not stable and owner-only: {target}")
            yield
            completed = True
        finally:
            if completed and (
                _windows_handle_identity(handle) != identity or _windows_path_identity(target) != identity
            ):
                kernel32.CloseHandle(handle)
                raise PermissionError(f"owner-only directory changed during operation: {target}")
            kernel32.CloseHandle(handle)
        return

    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(target, flags)
    try:
        opened = os.fstat(descriptor)
        lexical = os.stat(target, follow_symlinks=False)
        identity = (opened.st_dev, opened.st_ino)
        if (
            not stat.S_ISDIR(opened.st_mode)
            or opened.st_uid != os.geteuid()
            or stat.S_IMODE(opened.st_mode) & 0o077
            or (lexical.st_dev, lexical.st_ino) != identity
        ):
            raise PermissionError(f"directory is not stable and owner-only: {target}")
        yield
        completed = True
    finally:
        if completed:
            try:
                lexical_after = os.stat(target, follow_symlinks=False)
            except OSError as exc:
                os.close(descriptor)
                raise PermissionError(f"owner-only directory changed during operation: {target}") from exc
            if (lexical_after.st_dev, lexical_after.st_ino) != identity:
                os.close(descriptor)
                raise PermissionError(f"owner-only directory changed during operation: {target}")
        os.close(descriptor)
