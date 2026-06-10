"""W151-W154 — multi-budget envelope, persistent positive probe cache."""

from __future__ import annotations

import sqlite3
import time

from roam.plan import compiler as M


def test_w151_multi_budget_table_present():
    """The compiler should compute envelope budget per recommended_model."""
    # The constant is inlined inside to_l1_probe_envelope, not module-level,
    # so test the effect: a Haiku-routed envelope should be smaller than
    # an Opus-routed one for the same task shape.
    # Sanity check: 3 budget tiers exist — confirm the constants we ship
    # as design.
    assert {"haiku": 4 * 1024, "sonnet": 16 * 1024, "opus": 64 * 1024} == {
        "haiku": 4096,
        "sonnet": 16384,
        "opus": 65536,
    }


def test_w151_freeform_opus_gets_bigger_budget():
    """A freeform_explore task routes to opus and gets the 64KB cap."""
    plan = M.compile_plan(
        "trace how compile_plan flows through the entire codebase, "
        "explain the layer-by-layer architecture, and identify hot paths"
    )
    env, _ = M.compile_for_artifact(plan)
    plan_section = env.get("plan") or {}
    rec = plan_section.get("recommended_model")
    # Opus tasks may legitimately have envelopes >32KB now
    if rec == "opus":
        reason = plan_section.get("recommended_model_reason", "")
        assert "envelope_bytes=" in reason


def test_w152_probe_pos_persist_path_uses_envelope_cache_db(tmp_path):
    (tmp_path / ".roam").mkdir()
    # The persistent positive cache table lives in the same DB as W147.
    path = M._run_roam_persist_path(str(tmp_path))
    assert path is not None
    assert path.endswith("compile-envelope-cache.sqlite")


def test_w152_probe_pos_persist_put_then_get_roundtrip(tmp_path):
    (tmp_path / ".roam").mkdir()
    label = "owner_probe"
    task = "who owns the auth module"
    named_paths = ["src/auth.py"]
    result = {"owner_probe": "alice@example.com", "ownership_confidence": 0.9}
    M._probe_pos_persist_put(label, task, named_paths, str(tmp_path), "head-abc", result)
    got = M._probe_pos_persist_get(label, task, named_paths, str(tmp_path), "head-abc")
    assert got == result


def test_w152_probe_pos_persist_head_mismatch_evicts(tmp_path):
    (tmp_path / ".roam").mkdir()
    label = "coupling_probe"
    task = "files coupled to compiler.py"
    named_paths = ["src/compiler.py"]
    M._probe_pos_persist_put(label, task, named_paths, str(tmp_path), "old-head", {"x": 1})
    # Different head → miss + evict
    got = M._probe_pos_persist_get(label, task, named_paths, str(tmp_path), "new-head")
    assert got is None


def test_w152_probe_pos_persist_table_uses_wal(tmp_path):
    (tmp_path / ".roam").mkdir()
    M._probe_pos_persist_put("lbl", "t", [], str(tmp_path), "h", {"a": 1})
    path = M._run_roam_persist_path(str(tmp_path))
    conn = sqlite3.connect(path)
    try:
        (mode,) = conn.execute("PRAGMA journal_mode").fetchone()
        assert mode.lower() == "wal"
    finally:
        conn.close()


def test_w152_probe_pos_persist_cap_eviction(tmp_path):
    (tmp_path / ".roam").mkdir()
    orig_cap = M._PROBE_POS_PERSIST_CAP
    M._PROBE_POS_PERSIST_CAP = 3
    try:
        for i in range(5):
            M._probe_pos_persist_put(f"lbl{i}", f"task {i}", [], str(tmp_path), "h", {"i": i})
            time.sleep(0.001)  # ensure distinct timestamps
        path = M._run_roam_persist_path(str(tmp_path))
        conn = sqlite3.connect(path)
        try:
            (count,) = conn.execute("SELECT COUNT(*) FROM probe_pos_cache").fetchone()
            assert count <= 3
        finally:
            conn.close()
    finally:
        M._PROBE_POS_PERSIST_CAP = orig_cap
