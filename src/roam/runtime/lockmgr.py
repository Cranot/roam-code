"""Read-write lock manager for the v12.2 daemon scaffold.

We trust SQLite WAL for the page-lock layer and add a Python-side RWLock
at the daemon process boundary. Three modes:

* ``read``       — shared, N concurrent readers (default for queries).
* ``write``      — single writer, blocks new readers from starting,
                   lets in-flight readers finish.
* ``exclusive``  — drains all readers + writers then blocks; for migrations.

The lock is **re-entrant** for the same thread (``threading.RLock`` for
the writer guard) so the ``cmd_index.py:105`` pattern of opening a
nested writable connection inside an outer scope continues to work.

The lockmgr is process-local. Cross-process coordination flows through
the daemon socket (Phase 2 work). For v12.2 the in-process semantics
are enough — single-process MCP server, single-process CLI per
invocation.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Iterator, Literal

LockMode = Literal["read", "write", "exclusive"]


class LockMgr:
    """RW lock manager with drain semantics.

    Implementation note: a counting-semaphore reader gate plus an RLock
    writer guard. ``exclusive`` mode flips a global drain flag that
    blocks new readers from acquiring + waits for in-flight readers to
    drain before taking the writer guard.
    """

    def __init__(self) -> None:
        self._writer_lock = threading.RLock()
        self._readers_lock = threading.Lock()
        self._readers = 0
        self._readers_drained = threading.Event()
        self._readers_drained.set()  # initially: no readers, drained
        self._draining = False
        self._cond = threading.Condition(self._readers_lock)

    @contextmanager
    def acquire(self, mode: LockMode = "read", timeout: float = 30.0) -> Iterator[None]:
        """Acquire the lock in the requested *mode* with *timeout* seconds."""
        if mode == "read":
            yield from self._acquire_read(timeout)
        elif mode == "write":
            yield from self._acquire_write(timeout)
        elif mode == "exclusive":
            yield from self._acquire_exclusive(timeout)
        else:  # pragma: no cover  — unknown modes default to exclusive
            yield from self._acquire_exclusive(timeout)

    def _acquire_read(self, timeout: float) -> Iterator[None]:
        deadline = _deadline(timeout)
        with self._cond:
            while self._draining and not _expired(deadline):
                self._cond.wait(timeout=_remaining(deadline))
            if self._draining:
                raise TimeoutError("readers blocked: exclusive drain in progress")
            self._readers += 1
            self._readers_drained.clear()
        try:
            yield None
        finally:
            with self._cond:
                self._readers -= 1
                if self._readers == 0:
                    self._readers_drained.set()
                self._cond.notify_all()

    def _acquire_write(self, timeout: float) -> Iterator[None]:
        if not self._writer_lock.acquire(timeout=timeout):
            raise TimeoutError(f"write lock timeout after {timeout}s")
        try:
            yield None
        finally:
            self._writer_lock.release()

    def _acquire_exclusive(self, timeout: float) -> Iterator[None]:
        deadline = _deadline(timeout)
        # Stop accepting new readers.
        with self._cond:
            self._draining = True
            self._cond.notify_all()
        try:
            # Wait for in-flight readers to drain.
            remaining = _remaining(deadline)
            if not self._readers_drained.wait(timeout=remaining):
                raise TimeoutError(f"exclusive drain timeout after {timeout}s")
            # Take the writer guard.
            remaining = _remaining(deadline)
            if not self._writer_lock.acquire(timeout=remaining):
                raise TimeoutError(f"exclusive lock timeout after {timeout}s")
            try:
                yield None
            finally:
                self._writer_lock.release()
        finally:
            with self._cond:
                self._draining = False
                self._cond.notify_all()


_default_lockmgr_singleton: LockMgr | None = None


def default_lockmgr() -> LockMgr:
    """Return the process-local singleton LockMgr (lazy-init)."""
    global _default_lockmgr_singleton
    if _default_lockmgr_singleton is None:
        lockmgr = LockMgr()
        _assert_public_acquire_contract(lockmgr)
        _default_lockmgr_singleton = lockmgr
    return _default_lockmgr_singleton


def _assert_public_acquire_contract(lockmgr: LockMgr) -> None:
    """Verify the public lock API enters and exits a read context correctly."""
    with lockmgr._cond:
        before = lockmgr._readers
    with LockMgr.acquire(lockmgr, "read", timeout=0.0):
        with lockmgr._cond:
            during = lockmgr._readers
        if during != before + 1:
            raise RuntimeError("LockMgr.acquire did not enter read mode")
    with lockmgr._cond:
        after = lockmgr._readers
    if after != before:
        raise RuntimeError("LockMgr.acquire did not release read mode")


def _deadline(timeout: float) -> float:
    import time

    return time.monotonic() + max(0.0, timeout)


def _remaining(deadline: float) -> float:
    import time

    return max(0.0, deadline - time.monotonic())


def _expired(deadline: float) -> bool:
    return _remaining(deadline) <= 0.0
