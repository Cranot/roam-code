"""Batched persistent probe-cache reads (`_probe_persist_lookup_batch`).

The always-on compile path used to call `_probe_pos_persist_get` +
`probe_neg_persist_get` once per candidate label — up to 2·N SQLite opens per
compile. These tests pin the replacement: one connection serves both tables for
every label, with the same freshness / cleanup semantics as the per-label
getters, and `_filter_runnable_probes` drives it end-to-end.
"""

from __future__ import annotations

import sqlite3
import time

import pytest

from roam.plan import compiler as M


@pytest.fixture(autouse=True)
def _clear_inmem_probe_caches():
    """The in-mem pos/neg probe caches are process-global; clear them around
    every test so a `_filter_runnable_probes` run can't pollute the next."""
    M._PROBE_POSITIVE_CACHE.clear()
    M._PROBE_NEGATIVE_CACHE.clear()
    yield
    M._PROBE_POSITIVE_CACHE.clear()
    M._PROBE_NEGATIVE_CACHE.clear()


def _counting_connect(monkeypatch):
    """Wrap sqlite3.connect with a call counter; returns the (calls, real) pair."""
    real = sqlite3.connect
    calls: list = []

    def _connect(*args, **kwargs):
        calls.append(args)
        return real(*args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", _connect)
    return calls


def test_batch_returns_pos_and_neg_hits(tmp_path):
    """A fresh positive row for one label, a fresh negative row for another."""
    (tmp_path / ".roam").mkdir()
    cwd = str(tmp_path)
    M._probe_pos_persist_put("owner_probe", "t", [], cwd, "h", {"a": 1})
    M._probe_neg_persist_put("todo_audit", "t", cwd)

    pos_hits, neg_hits = M._probe_persist_lookup_batch(
        ["owner_probe", "todo_audit", "deprecation_audit"], "t", [], cwd, "h"
    )
    assert pos_hits == {"owner_probe": {"a": 1}}
    assert neg_hits == {"todo_audit"}
    # Uncached label is in neither.
    assert "deprecation_audit" not in pos_hits
    assert "deprecation_audit" not in neg_hits


def test_batch_uses_a_single_connection(tmp_path, monkeypatch):
    """N labels, both tables → exactly ONE sqlite3.connect (the point of the fix)."""
    (tmp_path / ".roam").mkdir()
    cwd = str(tmp_path)
    M._probe_pos_persist_put("owner_probe", "t", [], cwd, "h", {"a": 1})
    M._probe_neg_persist_put("todo_audit", "t", cwd)

    calls = _counting_connect(monkeypatch)
    pos_hits, neg_hits = M._probe_persist_lookup_batch(
        ["owner_probe", "todo_audit", "env_vars_audit", "subprocess_audit"],
        "t",
        [],
        cwd,
        "h",
    )
    assert len(calls) == 1, f"expected 1 connection, got {len(calls)}"
    assert "owner_probe" in pos_hits
    assert "todo_audit" in neg_hits


def test_batch_no_cwd_returns_empty(tmp_path, monkeypatch):
    """cwd=None or missing .roam → empty results, no connection opened."""
    calls = _counting_connect(monkeypatch)
    pos_hits, neg_hits = M._probe_persist_lookup_batch(["owner_probe", "todo_audit"], "t", [], None, "h")
    assert pos_hits == {}
    assert neg_hits == set()
    assert calls == []


def test_batch_empty_labels_returns_empty(tmp_path, monkeypatch):
    (tmp_path / ".roam").mkdir()
    calls = _counting_connect(monkeypatch)
    pos_hits, neg_hits = M._probe_persist_lookup_batch([], "t", [], str(tmp_path), "h")
    assert pos_hits == {}
    assert neg_hits == set()
    assert calls == []


def test_batch_pos_hit_skips_neg_lookup(tmp_path):
    """A positive hit short-circuits the negative check, matching per-label behavior."""
    (tmp_path / ".roam").mkdir()
    cwd = str(tmp_path)
    # Seed BOTH a positive and a negative row for the same label.
    M._probe_pos_persist_put("owner_probe", "t", [], cwd, "h", {"a": 1})
    M._probe_neg_persist_put("owner_probe", "t", cwd)

    pos_hits, neg_hits = M._probe_persist_lookup_batch(["owner_probe"], "t", [], cwd, "h")
    assert "owner_probe" in pos_hits
    assert "owner_probe" not in neg_hits


def test_batch_stale_pos_expired_is_deleted_and_missed(tmp_path):
    (tmp_path / ".roam").mkdir()
    cwd = str(tmp_path)
    M._probe_pos_persist_put("owner_probe", "t", [], cwd, "h", {"a": 1})
    # Walk the timestamp back past TTL.
    path = M._run_roam_persist_path(cwd)
    conn = sqlite3.connect(path)
    try:
        ancient = time.time() - M._PROBE_POS_PERSIST_TTL_S - 100
        conn.execute("UPDATE probe_pos_cache SET ts=?", (ancient,))
        conn.commit()
    finally:
        conn.close()

    pos_hits, neg_hits = M._probe_persist_lookup_batch(["owner_probe"], "t", [], cwd, "h")
    assert pos_hits == {}
    # Stale row was cleaned up in the batch's single commit.
    conn = sqlite3.connect(path)
    try:
        (count,) = conn.execute("SELECT COUNT(*) FROM probe_pos_cache").fetchone()
    finally:
        conn.close()
    assert count == 0


def test_batch_pos_head_mismatch_is_deleted_and_missed(tmp_path):
    (tmp_path / ".roam").mkdir()
    cwd = str(tmp_path)
    M._probe_pos_persist_put("owner_probe", "t", [], cwd, "old-head", {"a": 1})

    pos_hits, neg_hits = M._probe_persist_lookup_batch(["owner_probe"], "t", [], cwd, "new-head")
    assert pos_hits == {}
    path = M._run_roam_persist_path(cwd)
    conn = sqlite3.connect(path)
    try:
        (count,) = conn.execute("SELECT COUNT(*) FROM probe_pos_cache").fetchone()
    finally:
        conn.close()
    assert count == 0


def test_batch_stale_neg_expired_is_deleted_and_missed(tmp_path):
    (tmp_path / ".roam").mkdir()
    cwd = str(tmp_path)
    M._probe_neg_persist_put("todo_audit", "t", cwd)
    path = M._run_roam_persist_path(cwd)
    conn = sqlite3.connect(path)
    try:
        ancient = time.time() - M._PROBE_NEG_PERSIST_TTL_S - 100
        conn.execute("UPDATE probe_neg_cache SET ts=?", (ancient,))
        conn.commit()
    finally:
        conn.close()

    pos_hits, neg_hits = M._probe_persist_lookup_batch(["todo_audit"], "t", [], cwd, "h")
    assert neg_hits == set()
    conn = sqlite3.connect(path)
    try:
        (count,) = conn.execute("SELECT COUNT(*) FROM probe_neg_cache").fetchone()
    finally:
        conn.close()
    assert count == 0


def test_batch_matches_per_label_getters(tmp_path):
    """For every label the batch result equals the per-label getter result."""
    (tmp_path / ".roam").mkdir()
    cwd = str(tmp_path)
    labels = ["owner_probe", "todo_audit", "deprecation_audit", "env_vars_audit"]
    M._probe_pos_persist_put("owner_probe", "t", [], cwd, "h", {"a": 1})
    M._probe_pos_persist_put("env_vars_audit", "t", [], cwd, "h", {"b": 2})
    M._probe_neg_persist_put("deprecation_audit", "t", cwd)

    pos_hits, neg_hits = M._probe_persist_lookup_batch(labels, "t", [], cwd, "h")
    for label in labels:
        per_label_pos = M._probe_pos_persist_get(label, "t", [], cwd, "h")
        assert pos_hits.get(label) == per_label_pos, label
        per_label_neg = M._probe_neg_persist_get(label, "t", cwd)
        assert (label in neg_hits) == bool(per_label_neg), label


def test_filter_runnable_probes_batches_into_one_connection(tmp_path, monkeypatch):
    """End-to-end: _filter_runnable_probes opens ONE connection for many cached
    labels (was O(labels) connections) and merges positive data in label order."""
    (tmp_path / ".roam").mkdir()
    cwd = str(tmp_path)
    # Small fake registry; all labels seeded with persistent hits so none run.
    fake = tuple((lbl, None) for lbl in ["p1", "p2", "p3", "p4", "p5"])
    monkeypatch.setattr(M, "_L1_ALWAYS_ON_PROBES", fake)
    M._probe_pos_persist_put("p1", "t", [], cwd, "h", {"k": 1})
    M._probe_pos_persist_put("p2", "t", [], cwd, "h", {"k": 2})
    for lbl in ("p3", "p4", "p5"):
        M._probe_neg_persist_put(lbl, "t", cwd)

    calls = _counting_connect(monkeypatch)
    runnable, prefetched = M._filter_runnable_probes("", "t", [], cwd, "h", {})
    assert len(calls) == 1, f"expected 1 connection, got {len(calls)}"
    assert runnable == []  # every label hit a cache → nothing to run
    # p2 merged after p1 in label order → its value wins.
    assert prefetched == {"k": 2}


def test_filter_runnable_probes_uncached_label_becomes_runnable(tmp_path, monkeypatch):
    (tmp_path / ".roam").mkdir()
    cwd = str(tmp_path)
    sentinel = object()
    fake = (("cached_pos", None), ("cached_neg", None), ("fresh", sentinel))
    monkeypatch.setattr(M, "_L1_ALWAYS_ON_PROBES", fake)
    M._probe_pos_persist_put("cached_pos", "t", [], cwd, "h", {"x": 9})
    M._probe_neg_persist_put("cached_neg", "t", cwd)

    runnable, prefetched = M._filter_runnable_probes("", "t", [], cwd, "h", {})
    assert [lbl for lbl, _ in runnable] == ["fresh"]
    assert prefetched == {"x": 9}


def test_filter_runnable_probes_skips_seeded_module_name(monkeypatch):
    """A module-name result seeded before always_on satisfies that probe label."""
    sentinel = object()
    fake = (("module_name", sentinel),)
    monkeypatch.setattr(M, "_L1_ALWAYS_ON_PROBES", fake)
    prefetched = {
        "resolved_named_paths_from_module_name": ["src/auth.py"],
        "module_name_resolution_definition": "Globbed 1 matching file.",
    }

    runnable, got = M._filter_runnable_probes(
        "",
        "what does the auth module do",
        ["src/auth.py"],
        None,
        "",
        prefetched,
    )

    assert runnable == []
    assert got == prefetched


def test_filter_runnable_probes_no_cwd_makes_everything_runnable(tmp_path, monkeypatch):
    """Without cwd there is no persistent layer; uncached (non-skipped) labels run."""
    sentinel = object()
    fake = (("p1", sentinel), ("p2", sentinel))
    monkeypatch.setattr(M, "_L1_ALWAYS_ON_PROBES", fake)
    runnable, prefetched = M._filter_runnable_probes("", "t", [], None, "", {})
    assert [lbl for lbl, _ in runnable] == ["p1", "p2"]
    assert prefetched == {}
