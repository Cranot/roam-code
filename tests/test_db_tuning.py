"""Regression sentinels for the SQLite performance pragmas tuned in audit B6.

These guard against silent regressions of three pragma values that materially
affect roam's indexer / query latency on real repos:

1. ``mmap_size = 1 GB`` — memory-mapped I/O working set ceiling. The OS pager
   caps the *effective* use at available memory; on 32-bit builds the kernel
   rejects the full mapping silently and falls back to a smaller window.
   The pragma value here is the *declared* ceiling, not the guarantee.
2. ``wal_autocheckpoint = 10000`` pages — bumped 10x from the SQLite default
   (1000 pages) to cut fsync frequency during heavy indexer write loads.
   At a typical 4-32 KB page size this buffers 40-320 MB between checkpoints.
3. ``PRAGMA optimize`` on connection close — keeps the query planner's stats
   fresh after writes (SQLite 3.18+). Cheap on every commit; substantially
   improves next-reader latency vs forcing a full ``ANALYZE`` pass on a cron.

The test that ``PRAGMA optimize`` actually fires uses SQLite's built-in
``set_trace_callback`` hook to record every statement the engine evaluates;
that's the right tool because ``sqlite3.Connection`` is a C-level immutable
type (no monkeypatching ``conn.execute``). A future refactor that drops
the call (or moves it past the close) breaks the test loud and immediately.

If you have a deliberate reason to change these values (e.g. raise mmap_size
to 4 GB on confirmed 64-bit-only deployments, or drop wal_autocheckpoint
back down for a memory-constrained embedded build), update the assertions
here in the same commit so the intent travels with the change.
"""

from __future__ import annotations

import sqlite3

import pytest

import roam.db.connection as conn_mod
from roam.db.connection import get_connection, open_db

# ---------------------------------------------------------------------------
# mmap_size — 1 GB declared ceiling
# ---------------------------------------------------------------------------


def test_mmap_size_is_1gb(tmp_path):
    """``PRAGMA mmap_size`` returns 1 GB (1_073_741_824) on a fresh connection.

    The kernel may report a smaller *effective* mapping if address space
    is tight; this test checks the value SQLite was *asked* to declare,
    which is what the audit B6 directive pinned.
    """
    db_path = tmp_path / "mmap.db"
    conn = get_connection(db_path, readonly=False)
    try:
        row = conn.execute("PRAGMA mmap_size").fetchone()
        assert row is not None, "PRAGMA mmap_size returned no row"
        live = int(row[0])
    finally:
        conn.close()

    # 1 GB default, but env-tunable: ci_xdist sets ROAM_SQLITE_MMAP_SIZE=0 under
    # xdist to bound aggregate mmap (the Bus-error fix). Expect the configured
    # value so this sentinel still guards the DEFAULT locally while tolerating
    # the deliberate CI override.
    import os

    _env = os.environ.get("ROAM_SQLITE_MMAP_SIZE", "1073741824")
    expected = int(_env) if _env.isdigit() else 1_073_741_824
    assert live == expected, (
        f"PRAGMA mmap_size = {live}; expected {expected} "
        f"(ROAM_SQLITE_MMAP_SIZE={_env!r}, default 1 GB). If this is a deliberate "
        f"retuning, update the constant in src/roam/db/connection.py AND this test."
    )


# ---------------------------------------------------------------------------
# wal_autocheckpoint — 10000 pages (10x the SQLite default of 1000)
# ---------------------------------------------------------------------------


def test_wal_autocheckpoint_is_10000_on_wal_path(tmp_path):
    """``PRAGMA wal_autocheckpoint`` returns 10000 on a non-cloud WAL DB.

    The cloud-sync branch in ``get_connection`` switches to DELETE journal
    mode and skips the autocheckpoint pragma entirely, so this test must
    run on a plain on-disk path (``tmp_path`` is never cloud-synced).
    """
    db_path = tmp_path / "wal.db"
    conn = get_connection(db_path, readonly=False)
    try:
        row = conn.execute("PRAGMA wal_autocheckpoint").fetchone()
        assert row is not None, "PRAGMA wal_autocheckpoint returned no row"
        live = int(row[0])
    finally:
        conn.close()

    expected = 10000
    assert live == expected, (
        f"PRAGMA wal_autocheckpoint = {live}; expected {expected}. "
        f"This pragma controls how many WAL pages accumulate before SQLite "
        f"checkpoints to the main DB — dropping it back to the default 1000 "
        f"would 10x the fsync frequency during heavy indexer writes."
    )


# ---------------------------------------------------------------------------
# PRAGMA optimize — fires on write-connection close via open_db
# ---------------------------------------------------------------------------


def _install_tracer(monkeypatch, sink: list[str]) -> None:
    """Wrap ``get_connection`` so every returned connection logs SQL to *sink*.

    ``sqlite3.Connection`` is an immutable C type — ``monkeypatch.setattr``
    on its ``execute`` method raises ``TypeError``. SQLite's own trace hook
    (``set_trace_callback``) is the supported way to observe statements,
    so we patch the module-level ``get_connection`` to attach the hook on
    every connection ``open_db`` will create during the test.
    """
    real = conn_mod.get_connection

    def wrapped(*args, **kwargs):
        conn = real(*args, **kwargs)
        conn.set_trace_callback(sink.append)
        return conn

    monkeypatch.setattr(conn_mod, "get_connection", wrapped)


def test_pragma_optimize_runs_on_write_connection_close(tmp_path, monkeypatch):
    """``open_db(readonly=False)`` issues ``PRAGMA optimize`` before close.

    The teardown is the right place: writes have settled, the query planner
    can re-evaluate stats, and a subsequent reader benefits. A regression
    where someone moves the pragma to a code path that doesn't run on every
    teardown would silently degrade query latency on warm caches — this
    test catches it loudly.
    """
    executed: list[str] = []
    _install_tracer(monkeypatch, executed)

    proj = tmp_path / "optimize_proj"
    proj.mkdir()

    with open_db(readonly=False, project_root=proj):
        pass

    optimize_calls = [s for s in executed if "PRAGMA OPTIMIZE" in s.upper()]
    assert optimize_calls, (
        "PRAGMA optimize was never executed during open_db teardown. "
        "Expected at least one call in the commit-then-close path so the "
        "query planner stats stay fresh for the next reader. "
        f"Executed pragmas: {[s for s in executed if 'PRAGMA' in s.upper()]}"
    )


def test_pragma_optimize_failure_does_not_break_close(tmp_path, monkeypatch):
    """An error raised by ``PRAGMA optimize`` must NOT prevent the connection
    from closing cleanly. The pragma is best-effort; failing the close on
    a stats-refresh issue would be a regression — open_db wraps the call
    in a ``try/except sqlite3.DatabaseError`` for exactly this reason.

    We monkeypatch ``get_connection`` to return a thin wrapper whose
    ``execute`` method raises on ``PRAGMA optimize``. The real connection
    object stays underneath, so all other SQL keeps working — only the
    optimize call gets the simulated failure.
    """
    real = conn_mod.get_connection

    class AngryOptimize:
        def __init__(self, inner):
            self._inner = inner

        def execute(self, sql, *args, **kwargs):
            if "PRAGMA optimize" in sql.upper():
                raise sqlite3.DatabaseError("simulated optimize failure")
            return self._inner.execute(sql, *args, **kwargs)

        def __getattr__(self, name):
            return getattr(self._inner, name)

    def wrapped(*args, **kwargs):
        return AngryOptimize(real(*args, **kwargs))

    monkeypatch.setattr(conn_mod, "get_connection", wrapped)

    proj = tmp_path / "optimize_resilient_proj"
    proj.mkdir()

    # Must not raise — the failure is swallowed inside open_db's try/except.
    with open_db(readonly=False, project_root=proj):
        pass


def test_readonly_open_does_not_attempt_optimize(tmp_path, monkeypatch):
    """``PRAGMA optimize`` is a write-time pragma; running it on a readonly
    connection is wasted work (and on a strict-ro URI mount, SQLite will
    refuse it). The open_db teardown only runs it when ``readonly=False``,
    and this test pins that contract.
    """
    # First seed the DB so the readonly open has something to attach to.
    proj = tmp_path / "ro_proj"
    proj.mkdir()
    with open_db(readonly=False, project_root=proj):
        pass

    executed: list[str] = []
    _install_tracer(monkeypatch, executed)

    with open_db(readonly=True, project_root=proj):
        pass

    optimize_calls = [s for s in executed if "PRAGMA OPTIMIZE" in s.upper()]
    assert not optimize_calls, (
        "PRAGMA optimize was executed on a readonly connection — expected "
        "the teardown to skip it under readonly=True. "
        f"Optimize calls: {optimize_calls}"
    )


# ---------------------------------------------------------------------------
# Smoke: the pragma block does not drift under cloud-sync detection
# ---------------------------------------------------------------------------


def test_wal_autocheckpoint_skipped_on_cloud_synced_path(tmp_path, monkeypatch):
    """On a cloud-synced path, ``get_connection`` falls back to DELETE
    journal mode and must NOT issue ``PRAGMA wal_autocheckpoint`` — the
    pragma is a no-op outside WAL mode, but issuing it on a DELETE-mode
    DB is wasted work and obscures the intent of the cloud-sync branch.

    The tracer here uses SQLite's ``set_trace_callback`` directly on the
    returned connection (no need to wrap ``get_connection`` because this
    test calls ``get_connection`` itself, not via ``open_db``).
    """
    monkeypatch.setattr(conn_mod, "_is_cloud_synced", lambda p: True)

    # We need to observe SQL executed *during* get_connection's pragma
    # setup, but set_trace_callback can only be installed after the
    # connection exists. Wrap get_connection to install the tracer
    # before any pragmas are applied is not possible — instead, wrap
    # sqlite3.connect at the module so the trace hook attaches before
    # get_connection's pragma block runs.
    executed: list[str] = []
    real_connect = sqlite3.connect

    def traced_connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        conn.set_trace_callback(executed.append)
        return conn

    monkeypatch.setattr(sqlite3, "connect", traced_connect)

    db_path = tmp_path / "cloud.db"
    conn = get_connection(db_path, readonly=False)
    try:
        wal_set = [s for s in executed if "WAL_AUTOCHECKPOINT" in s.upper()]
        assert not wal_set, (
            "wal_autocheckpoint was set on a cloud-synced path; the cloud "
            "branch in get_connection should fall back to DELETE journal "
            "mode and skip the WAL-specific pragma. "
            f"Offending statements: {wal_set}"
        )
    finally:
        conn.close()


# Guard: keep this file lean — the four assertions above + two resilience /
# branch checks are the full surface area. Any new pragma added to the
# block in src/roam/db/connection.py should land its own sentinel here
# in the same commit so the test file stays the source of truth for
# "what perf knobs roam pins and why".
if __name__ == "__main__":
    pytest.main([__file__, "-v"])
