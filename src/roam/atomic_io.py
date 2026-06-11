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
import tempfile
from pathlib import Path
from typing import Any, Union

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
    except OSError:
        pass


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
            try:
                os.close(fd)
            except OSError:
                pass
        _cleanup_tmp(tmp_name)
        raise


def atomic_write_bytes(path: PathLike, content: bytes) -> None:
    """Atomically write *content* (raw bytes) to *path*.

    Same contract as :func:`atomic_write_text` but for binary payloads
    (e.g. cosign signatures, gzip-compressed bundles). Same three-stage
    cleanup discipline — see :func:`atomic_write_text` docstring.
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
        fh = os.fdopen(fd, "wb")
        fdopen_succeeded = True
        with fh:
            fh.write(content)
        os.replace(tmp_name, str(target))
    except BaseException:
        if not fdopen_succeeded:
            try:
                os.close(fd)
            except OSError:
                pass
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
