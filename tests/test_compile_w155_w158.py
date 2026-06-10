"""W155-W158 — persistent negative cache + adjacent caller body embed."""

from __future__ import annotations

import sqlite3
import time

from roam.plan import compiler as M


def test_w155_neg_persist_roundtrip(tmp_path):
    (tmp_path / ".roam").mkdir()
    assert M._probe_neg_persist_get("owner_probe", "task A", str(tmp_path)) is False
    M._probe_neg_persist_put("owner_probe", "task A", str(tmp_path))
    assert M._probe_neg_persist_get("owner_probe", "task A", str(tmp_path)) is True
    # Different task → different key → miss
    assert M._probe_neg_persist_get("owner_probe", "task B", str(tmp_path)) is False


def test_w155_neg_persist_ttl_expiry(tmp_path):
    (tmp_path / ".roam").mkdir()
    M._probe_neg_persist_put("lbl", "t", str(tmp_path))
    # Walk the timestamp back to force expiry
    path = M._run_roam_persist_path(str(tmp_path))
    conn = sqlite3.connect(path)
    try:
        ancient = time.time() - M._PROBE_NEG_PERSIST_TTL_S - 100
        conn.execute("UPDATE probe_neg_cache SET ts=?", (ancient,))
        conn.commit()
    finally:
        conn.close()
    assert M._probe_neg_persist_get("lbl", "t", str(tmp_path)) is False


def test_w155_neg_persist_cap_eviction(tmp_path):
    (tmp_path / ".roam").mkdir()
    orig_cap = M._PROBE_NEG_PERSIST_CAP
    M._PROBE_NEG_PERSIST_CAP = 3
    try:
        for i in range(5):
            M._probe_neg_persist_put(f"l{i}", f"t{i}", str(tmp_path))
            time.sleep(0.001)
        path = M._run_roam_persist_path(str(tmp_path))
        conn = sqlite3.connect(path)
        try:
            (count,) = conn.execute("SELECT COUNT(*) FROM probe_neg_cache").fetchone()
            assert count <= 3
        finally:
            conn.close()
    finally:
        M._PROBE_NEG_PERSIST_CAP = orig_cap


def test_w155_neg_persist_wal_mode(tmp_path):
    (tmp_path / ".roam").mkdir()
    M._probe_neg_persist_put("lbl", "t", str(tmp_path))
    path = M._run_roam_persist_path(str(tmp_path))
    conn = sqlite3.connect(path)
    try:
        (mode,) = conn.execute("PRAGMA journal_mode").fetchone()
        assert mode.lower() == "wal"
    finally:
        conn.close()


def test_w156_caller_body_embed_when_few_callers(tmp_path, monkeypatch):
    """When _probe_callers gets <=3 callers, embed their source bodies."""
    # Set up a tiny project
    (tmp_path / "src").mkdir()
    target = tmp_path / "src" / "target.py"
    target.write_text("def my_func():\n    return 42\n")
    caller_a = tmp_path / "src" / "caller_a.py"
    caller_a.write_text("from .target import my_func\n\ndef use_a():\n    return my_func() + 1\n")
    caller_b = tmp_path / "src" / "caller_b.py"
    caller_b.write_text("from .target import my_func\n\ndef use_b():\n    return my_func() * 2\n")

    # Stub _run_roam + _flatten_consumers to return 2 callers (≤3 triggers W156)
    def fake_run_roam(args, cwd, timeout=8.0, detail=False):
        return {"_": "stub"}

    monkeypatch.setattr(M, "_run_roam", fake_run_roam)
    monkeypatch.setattr(
        M,
        "_flatten_consumers",
        lambda d: [
            "src/caller_a.py:3",
            "src/caller_b.py:3",
        ],
    )

    facts = M._probe_callers(["my_func"], str(tmp_path))
    assert "callers" in facts
    assert "caller_bodies" in facts
    assert "src/caller_a.py" in facts["caller_bodies"]
    assert "src/caller_b.py" in facts["caller_bodies"]
    assert "def use_a" in facts["caller_bodies"]["src/caller_a.py"]
    assert "caller_bodies_definition" in facts


def test_w156_no_body_embed_when_too_many_callers(tmp_path, monkeypatch):
    """4+ callers → skip body embedding (would be too noisy)."""
    monkeypatch.setattr(M, "_run_roam", lambda *a, **k: {"_": "stub"})
    monkeypatch.setattr(M, "_flatten_consumers", lambda d: [f"src/f{i}.py:1" for i in range(5)])
    facts = M._probe_callers(["my_func"], str(tmp_path))
    assert "callers" in facts
    assert "caller_bodies" not in facts


def test_w156_no_body_embed_without_cwd(monkeypatch):
    """cwd=None → no filesystem reads → no body embedding."""
    monkeypatch.setattr(M, "_run_roam", lambda *a, **k: {"_": "stub"})
    monkeypatch.setattr(M, "_flatten_consumers", lambda d: ["x.py:1"])
    facts = M._probe_callers(["my_func"], cwd=None)
    assert "callers" in facts
    assert "caller_bodies" not in facts
