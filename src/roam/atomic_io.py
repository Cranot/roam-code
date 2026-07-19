"""Atomic file writes — temp-file + ``os.replace`` pattern.

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
   directory = same filesystem = ``os.replace`` is guaranteed atomic
   on POSIX and Windows.
2. Write all bytes to the tempfile, fsync optional (skipped — most
   callers want speed; durability against a power-cut is a different
   problem from torn-write protection).
3. ``os.replace`` the tempfile onto the target. This is the only
   step that mutates the target.
4. On any failure: best-effort ``os.unlink`` the tempfile and re-raise.

``os.replace`` (not ``os.rename``) is mandatory: ``rename`` raises on
Windows when the target exists, ``replace`` overwrites atomically on
both platforms.

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

import json
import os
import secrets
import tempfile
from pathlib import Path
from typing import Any, Callable, Union

from roam.observability import log_swallowed

# Public surface kept intentionally small. Callers should NOT reach in to
# the private helpers — they may grow os-specific flags (O_TMPFILE,
# FILE_FLAG_WRITE_THROUGH) in future iterations.
__all__ = ["atomic_write_text", "atomic_write_bytes", "atomic_write_json"]


PathLike = Union[str, os.PathLike]


def _cleanup_tmp(tmp_name: str) -> None:
    """Best-effort remove a tempfile path.

    Centralised so both writers share the exact same cleanup policy:
    ``OSError`` is the expected/tolerated failure (e.g. on Windows an
    antivirus or indexer may have the file briefly open). Any other
    exception class (``MemoryError``, ``KeyboardInterrupt`` raised
    re-entrantly) must propagate — we are already inside an outer
    except block raising the original error.
    """
    try:
        os.unlink(tmp_name)
    except OSError as exc:
        # Best-effort cleanup: we run inside the caller's except block,
        # which already re-raises the original error, so re-raising this
        # OSError would mask the real failure. Surface the miss via the
        # opt-in swallow logger (silent unless ROAM_VERBOSE=1) instead of
        # a bare ``pass``. See the docstring for the full OSError rationale.
        log_swallowed("atomic_io._cleanup_tmp", exc)


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


def _fsync_parent_directory(target: Path) -> None:
    """Persist a completed rename on filesystems that support directory fsync.

    A file ``fsync`` makes the tempfile contents durable, but a power loss can
    still forget the directory entry installed by ``os.replace``. POSIX lets us
    close that gap by syncing the containing directory after the rename.
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
) -> None:
    """POSIX atomic write pinned to an already-open parent directory.

    This variant is deliberately private and selected only by the explicit
    ``secure_parent`` option. Relative ``open``/``replace``/``unlink`` calls
    keep cleanup and replacement attached to the directory inode even if its
    lexical pathname is concurrently renamed.
    """

    parent_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    parent_flags |= getattr(os, "O_NOFOLLOW", 0)
    parent_fd = os.open(target.parent, parent_flags)
    fd = -1
    fdopen_succeeded = False
    temp_name: str | None = None
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
        if before_replace is not None:
            before_replace()
        os.replace(
            temp_name,
            target.name,
            src_dir_fd=parent_fd,
            dst_dir_fd=parent_fd,
        )
        temp_name = None
        if durable:
            os.fsync(parent_fd)
    except BaseException:
        if fd >= 0 and not fdopen_succeeded:
            _close_fd_safely(fd, origin="atomic_io._atomic_write_bytes_dirfd.fd_close")
        if temp_name is not None:
            try:
                os.unlink(temp_name, dir_fd=parent_fd)
            except OSError as exc:
                log_swallowed("atomic_io._atomic_write_bytes_dirfd.cleanup", exc)
        raise
    finally:
        os.close(parent_fd)


def atomic_write_text(path: PathLike, content: str, *, encoding: str = "utf-8") -> None:
    """Atomically write *content* to *path*.

    The parent directory is created if missing. The temp file is named
    ``.<target_name>.<rand>.tmp`` in the same directory as the target
    so the eventual ``os.replace`` is intra-filesystem (atomic on
    every POSIX + Windows filesystem we support).

    On any I/O error the temp file is best-effort cleaned up and the
    error is re-raised — the caller decides how to react. Crucially,
    the target file is NEVER left in a half-written state.

    Cleanup-race discipline
    -----------------------
    Three failure stages must each be safe:

    1. ``mkstemp`` raises — no temp file or fd exists yet; nothing to
       clean. The ``except`` block here is never entered.
    2. ``fdopen`` raises (e.g. invalid encoding) — the raw ``fd`` is
       still open and the temp file exists. The ``except`` block must
       both ``os.close(fd)`` AND ``unlink(tmp_name)``; otherwise the
       fd leaks for the process lifetime.
    3. ``fh.write`` / ``os.replace`` raises — the ``with os.fdopen``
       context has already taken ownership of the fd and will close
       it on context exit (even on exception). Only the temp file
       needs cleanup.

    The ``fdopen_succeeded`` flag distinguishes (2) from (3) so we
    don't double-close — calling ``os.close(fd)`` AFTER ``os.fdopen``
    wrapped it is a hard error on POSIX.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=str(target.parent),
    )
    fdopen_succeeded = False
    try:
        fh = os.fdopen(fd, "w", encoding=encoding, newline="")
        fdopen_succeeded = True
        with fh:
            fh.write(content)
        os.replace(tmp_name, str(target))
    except BaseException:
        # Cleanup must run on ANY exception (KeyboardInterrupt, SystemExit,
        # OSError, etc.) — we don't want stray ``.tmp`` debris on disk
        # OR a leaked file descriptor.
        if not fdopen_succeeded:
            _close_fd_safely(fd, origin="atomic_io.atomic_write_text.fd_close")
        _cleanup_tmp(tmp_name)
        raise


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
    synced when ``durable`` is true) but immediately before ``os.replace``.
    Callers can use it for a final compare-and-swap or path-integrity check. A
    callback failure removes the tempfile and leaves the target untouched.

    ``create_parents=False`` is intended for security-sensitive callers that
    validated an existing directory themselves and do not want this helper to
    create or traverse missing ancestors. All callbacks must raise on failure.

    ``secure_parent=True`` additionally pins the existing parent directory by
    file descriptor and performs tempfile creation, replacement, and cleanup
    relative to that descriptor on POSIX. Python has no portable equivalent on
    Windows, where the callback-based validation path remains in force.
    """
    target = Path(path)
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
        )
        return
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=str(target.parent),
    )
    fdopen_succeeded = False
    try:
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
        if before_replace is not None:
            before_replace()
        os.replace(tmp_name, str(target))
        if durable:
            _fsync_parent_directory(target)
    except BaseException:
        if not fdopen_succeeded:
            _close_fd_safely(fd, origin="atomic_io.atomic_write_bytes.fd_close")
        _cleanup_tmp(tmp_name)
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
    :func:`atomic_write_text` under the hood so the same temp-file +
    ``os.replace`` guarantee holds.

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
