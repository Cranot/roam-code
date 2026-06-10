"""W147-W150 — persistent _run_roam cache, WAL mode, off-thread telemetry."""

from __future__ import annotations

import os
import sqlite3
import time

from roam.plan import compiler as M


def test_w147_persist_path_resolves_when_dot_roam_exists(tmp_path):
    (tmp_path / ".roam").mkdir()
    path = M._run_roam_persist_path(str(tmp_path))
    assert path is not None
    assert path.endswith("compile-envelope-cache.sqlite")


def test_w147_persist_path_none_without_dot_roam(tmp_path):
    # No .roam dir → no persistence
    assert M._run_roam_persist_path(str(tmp_path)) is None


def test_w147_put_then_get_roundtrip(tmp_path):
    (tmp_path / ".roam").mkdir()
    args = ["--json", "uses", "fooBar"]
    value = {"version": "test", "callers": ["a", "b"]}
    M._run_roam_persist_put(args, str(tmp_path), "abc123", value)
    got = M._run_roam_persist_get(args, str(tmp_path), "abc123")
    assert got == value


def test_w147_get_returns_none_on_head_mismatch(tmp_path):
    (tmp_path / ".roam").mkdir()
    args = ["--json", "uses", "foo"]
    M._run_roam_persist_put(args, str(tmp_path), "old-head", {"x": 1})
    # Different head → miss + evict
    got = M._run_roam_persist_get(args, str(tmp_path), "new-head")
    assert got is None


def test_w148_wal_mode_enabled_on_persist_db(tmp_path):
    (tmp_path / ".roam").mkdir()
    M._run_roam_persist_put(["--json", "uses", "x"], str(tmp_path), "h", {"a": 1})
    path = M._run_roam_persist_path(str(tmp_path))
    conn = sqlite3.connect(path)
    try:
        (mode,) = conn.execute("PRAGMA journal_mode").fetchone()
        assert mode.lower() == "wal"
    finally:
        conn.close()


def test_w149_telemetry_queue_module_objects_present():
    assert M._TELEMETRY_QUEUE is not None
    assert hasattr(M, "_ensure_telemetry_worker")
    assert hasattr(M, "_telemetry_worker")


def test_w149_telemetry_off_thread_writes_eventually(tmp_path):
    """Worker thread eventually drains the queue + writes to file."""
    log_path = str(tmp_path / "compile-runs.jsonl")
    M._ensure_telemetry_worker()
    M._TELEMETRY_QUEUE.put((log_path, '{"x":1}\n'))
    # Worker reads with up to 5s timeout but processes immediately when item available
    for _ in range(50):
        if os.path.exists(log_path):
            break
        time.sleep(0.05)
    assert os.path.exists(log_path)
    with open(log_path) as fh:
        assert fh.read().strip() == '{"x":1}'


def test_w147_cap_enforced_lru(tmp_path):
    """Beyond the cap, oldest rows are evicted by ts."""
    (tmp_path / ".roam").mkdir()
    # Lower cap for the test (don't write 4096 rows!)
    orig_cap = M._RUN_ROAM_PERSIST_CAP
    M._RUN_ROAM_PERSIST_CAP = 3
    try:
        for i in range(5):
            M._run_roam_persist_put(["--json", "uses", f"sym{i}"], str(tmp_path), "h", {"i": i})
        path = M._run_roam_persist_path(str(tmp_path))
        conn = sqlite3.connect(path)
        try:
            (count,) = conn.execute("SELECT COUNT(*) FROM run_roam_cache").fetchone()
            assert count <= 3
        finally:
            conn.close()
    finally:
        M._RUN_ROAM_PERSIST_CAP = orig_cap
