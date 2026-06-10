"""W32 — parallel sub-probe dispatcher tests.

Covers `_parallel_probe_dispatch` plus the parallelization shipped in
`_probe_freeform_skeleton` / `_probe_synthesis_skeleton`. The contract:

  * Independent sub-probes run concurrently — N×100ms work completes
    in ~100ms wall-time, not 300ms+.
  * Exception in one sub-probe is isolated (logged) and does not
    poison the other results.
  * Timeout marks the offending sub-probe with `_w32_timeout`; siblings
    still return their real results.
  * Result-dict iteration order is sorted-by-key (deterministic, so
    envelope-cache keys stay stable).
"""

from __future__ import annotations

import time

from roam.plan.compiler import (
    _W32_ERROR_KEY,
    _W32_TIMEOUT_SENTINEL,
    _parallel_probe_dispatch,
)


def test_parallel_dispatch_runs_concurrently():
    """3 sub-probes × 100ms each should finish in well under 350ms.

    Sequential would be ~300ms; parallel ~100ms + thread overhead.
    The 350ms bound includes a generous overhead margin so the test
    is stable on heavily loaded CI without hiding the parallel-vs-
    sequential signal (would be ~300ms+ if broken)."""

    def _sleeper():
        time.sleep(0.1)
        return {"ok": True}

    tasks = [
        ("alpha", _sleeper),
        ("beta", _sleeper),
        ("gamma", _sleeper),
    ]
    t0 = time.monotonic()
    out = _parallel_probe_dispatch(tasks, max_workers=4, per_task_timeout=3.0)
    elapsed = time.monotonic() - t0
    assert elapsed < 0.35, f"parallel dispatch took {elapsed:.3f}s (expected <0.35)"
    # Each sub-probe returned its dict.
    assert out["alpha"] == {"ok": True}
    assert out["beta"] == {"ok": True}
    assert out["gamma"] == {"ok": True}
    # Per-probe timings recorded.
    timings = out["_w32_subprobe_timings_ms"]
    assert set(timings) == {"alpha", "beta", "gamma"}
    for k, v in timings.items():
        assert 80 <= v <= 350, f"{k} timing {v}ms out of expected range"


def test_parallel_dispatch_isolates_exception():
    """One sub-probe raising does not poison sibling results."""

    def _good():
        return {"value": 42}

    def _bad():
        raise RuntimeError("intentional")

    def _other():
        return {"value": "ok"}

    out = _parallel_probe_dispatch(
        [("a_good", _good), ("b_bad", _bad), ("c_other", _other)],
        max_workers=4,
        per_task_timeout=3.0,
    )
    assert out["a_good"] == {"value": 42}
    assert out["c_other"] == {"value": "ok"}
    # The failing probe is marked with the error sentinel key.
    assert _W32_ERROR_KEY in out["b_bad"]
    assert out["b_bad"][_W32_ERROR_KEY] == "RuntimeError"


def test_parallel_dispatch_timeout_marks_sentinel():
    """Timed-out sub-probe gets `_w32_timeout`; siblings unaffected."""

    def _quick():
        return {"value": "fast"}

    def _slow():
        time.sleep(0.6)
        return {"value": "should-not-arrive"}

    def _also_quick():
        return {"value": "also-fast"}

    out = _parallel_probe_dispatch(
        [("a_fast", _quick), ("b_slow", _slow), ("c_fast", _also_quick)],
        max_workers=4,
        per_task_timeout=0.15,
    )
    assert out["a_fast"] == {"value": "fast"}
    assert out["c_fast"] == {"value": "also-fast"}
    assert out["b_slow"].get("_w32_timeout") is True
    # Sentinel-marker constant exposed for callers to filter results.
    assert _W32_TIMEOUT_SENTINEL == {"_w32_timeout": True}


def test_parallel_dispatch_deterministic_key_order():
    """Result-dict iteration order must be sorted by key — envelope-cache
    hashes depend on stable JSON serialization order."""

    def _r(payload):
        def _fn():
            return {"v": payload}

        return _fn

    # Submit in non-sorted order on purpose.
    tasks = [
        ("zebra", _r("z")),
        ("alpha", _r("a")),
        ("mango", _r("m")),
    ]
    out = _parallel_probe_dispatch(tasks, max_workers=4, per_task_timeout=3.0)
    keys = [k for k in out.keys() if not k.startswith("_w32_")]
    assert keys == sorted(keys), f"keys not sorted: {keys}"
    # Timings dict also sorted.
    t_keys = list(out["_w32_subprobe_timings_ms"].keys())
    assert t_keys == sorted(t_keys)


def test_parallel_dispatch_empty():
    """Empty task list returns an empty dict (no crash)."""
    out = _parallel_probe_dispatch([])
    assert out == {}


def test_freeform_skeleton_records_w32_timings(tmp_path, monkeypatch):
    """End-to-end: `_probe_freeform_skeleton` exposes the W32 timings."""
    from roam.plan import compiler as compiler_mod

    target = tmp_path / "demo.py"
    target.write_text("def hello():\n    return 'world'\n", encoding="utf-8")

    fake_d = {
        "symbols": [
            {"name": "hello", "kind": "fn", "depth": 0, "signature": "def hello()", "line_start": 1, "line_end": 2},
        ],
        "summary": {"line_count": 2, "symbols": 1, "verdict": "ok"},
    }

    def _fake_run_roam(argv, cwd, **kwargs):  # noqa: ARG001
        return fake_d

    monkeypatch.setattr(compiler_mod, "_run_roam", _fake_run_roam)
    facts = compiler_mod._probe_freeform_skeleton(
        ["demo.py"],
        str(tmp_path),
        task="what does `hello` do",
    )
    assert "_w32_subprobe_timings_ms" in facts
    timings = facts["_w32_subprobe_timings_ms"]
    assert set(timings) == {"roam_file", "full_file"}
    # Both probes ran and contributed to the result.
    assert facts.get("full_file_body", "").startswith("def hello")
    assert "file_skeleton" in facts


def test_synthesis_skeleton_records_w32_timings(tmp_path, monkeypatch):
    """End-to-end: `_probe_synthesis_skeleton` exposes the W32 timings."""
    from roam.plan import compiler as compiler_mod

    target = tmp_path / "demo.py"
    target.write_text("def hello():\n    return 'world'\n", encoding="utf-8")

    fake_d = {
        "symbols": [
            {"name": "hello", "kind": "fn", "depth": 0, "signature": "def hello()", "line_start": 1, "line_end": 2},
        ],
    }

    def _fake_run_roam(argv, cwd, **kwargs):  # noqa: ARG001
        return fake_d

    monkeypatch.setattr(compiler_mod, "_run_roam", _fake_run_roam)
    facts = compiler_mod._probe_synthesis_skeleton(
        ["demo.py"],
        str(tmp_path),
        task="what does `hello` do",
    )
    assert "_w32_subprobe_timings_ms" in facts
    timings = facts["_w32_subprobe_timings_ms"]
    assert set(timings) == {"roam_file", "target_body"}
    assert "file_skeleton" in facts
    # W172 body-embed: should pick up `hello`'s source.
    assert "target_symbol_body" in facts
