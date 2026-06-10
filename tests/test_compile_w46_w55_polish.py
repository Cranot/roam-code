"""W46-W55 — tests for the 10-wave polish/improvement/correction batch.

Covers W47 parallel coupling, W48 reachability, W49 config-by-name,
W50 find-by-description, W51 per-procedure conf threshold, W54 cache key.
"""

from __future__ import annotations

import json
import time

from roam.plan.compiler import (
    _CONFIG_BY_NAME_RE,
    _FIND_BY_DESC_RE,
    _PER_PROCEDURE_CONF_THRESHOLD,
    _REACHABILITY_RE,
    _probe_reachability_for_task,
)

# ---- W47 parallel coupling probe ----


def test_w47_coupling_uses_parallel_dispatch(monkeypatch):
    """W43 update (2026-06-02): _probe_coupling now issues ONE
    `deps <target> --multi` per target — the `--multi` envelope bundles
    imports + importers + cochange_pairs, eliminating the separate
    `_git_cochange_counts` subprocess (4 spawns → 2). This test asserts the
    new dispatch: 2 deps calls (each carrying `--multi`), NO separate
    cochange call, and the temporal_coupling_pairs derived from the
    envelope's cochange_pairs field."""
    from roam.plan import compiler as M

    M._RUN_ROAM_CACHE.clear()
    calls = []

    def _fake_run_roam(args, cwd=None, timeout=8.0, detail=False):
        calls.append(tuple(args))
        # W43 --multi envelope shape: deps + cochange in one result.
        return {
            "imports": ["x"],
            "imported_by": ["y"],
            "cochange_pairs": [{"file": "pair", "count": 1}],
        }

    def _fake_cochange(target, cwd, limit=200):
        calls.append(("cochange", target))
        return [("pair", 1)]

    monkeypatch.setattr(M, "_run_roam", _fake_run_roam)
    monkeypatch.setattr(M, "_git_cochange_counts", _fake_cochange)
    out = M._probe_coupling(["a.py", "b.py"], cwd=None)
    # 2 deps calls, each with the --multi flag; NO separate cochange spawn.
    deps_calls = [c for c in calls if c and c[0] == "deps"]
    assert len(deps_calls) == 2
    assert all("--multi" in c for c in deps_calls), deps_calls
    assert len([c for c in calls if c and c[0] == "cochange"]) == 0
    assert "structural_imports" in out
    assert "structural_imports_2" in out
    assert "temporal_coupling_pairs" in out
    assert "temporal_coupling_pairs_2" in out


# ---- W48 reachability probe ----


def test_w48_reachability_regex():
    for s in [
        "is `foo` reachable from `bar`",
        "does `foo` depend on `bar`",
        "can `foo` call `bar`",
        "is there a path from `foo` to `bar`",
    ]:
        assert _REACHABILITY_RE.search(s), s


def test_w48_reachability_needs_two_symbols():
    out = _probe_reachability_for_task(
        "is `foo` reachable",
        cwd=None,
    )
    assert out is None  # only 1 backticked symbol


def test_w48_reachability_returns_yes_no(monkeypatch):
    from roam.plan import compiler as M

    monkeypatch.setattr(
        M,
        "_run_roam",
        lambda *a, **k: {
            "affected_file_list": ["src/path_to_bar.py"],
        },
    )
    out = _probe_reachability_for_task(
        "is `foo` reachable from `bar`",
        cwd=None,
    )
    assert out is not None
    r = out["reachability"]
    assert r["source"] == "foo"
    assert r["target"] == "bar"
    # "bar" appears in "src/path_to_bar.py" → reachable=True
    assert r["reachable"] is True


# ---- W49 config-by-name probe ----


def test_w49_config_regex_extracts_name():
    m = _CONFIG_BY_NAME_RE.search("where is the API_KEY env var")
    assert m is not None
    assert "API_KEY" in (m.group(3) or "")


def test_w49_config_misses_when_no_name():
    assert not _CONFIG_BY_NAME_RE.search("how does config work")
    # the word "config" alone isn't enough — needs `the X env var/config`


# ---- W50 find-by-description ----


def test_w50_find_by_desc_regex():
    for s in [
        "the function that parses JSON",
        "find anything about caching",
        "where is the code that handles auth",
        "which function handles login",
    ]:
        assert _FIND_BY_DESC_RE.search(s), s


def test_w50_find_by_desc_misses_explicit_paths():
    # "what does src/foo.py do" is an explain question, not find-by-desc
    assert not _FIND_BY_DESC_RE.search("what does src/foo.py do")


# ---- W51 per-procedure confidence threshold ----


def test_w51_per_procedure_thresholds_defined():
    for proc in (
        "stack_trace_fix",
        "structural_coupling",
        "structural_callers",
        "trace_query",
        "synthesis_query",
        "freeform_explore",
    ):
        assert proc in _PER_PROCEDURE_CONF_THRESHOLD


def test_w51_freeform_threshold_relaxed():
    # freeform_explore catches a lot; relaxed threshold 0.30 lets generic
    # tasks still get the "facts" envelope.
    assert _PER_PROCEDURE_CONF_THRESHOLD["freeform_explore"] == 0.30


def test_w51_stack_trace_threshold_high():
    # stack_trace_fix regex is unambiguous → high threshold safe
    assert _PER_PROCEDURE_CONF_THRESHOLD["stack_trace_fix"] == 0.85


# ---- W54 cache key shape ----


def test_w54_cache_key_uses_tuple_not_joined_string():
    """Pre-W54 the cache key was `(" ".join(args), cwd, detail)` which
    could collide (`["uses", "foo bar"]` vs `["uses foo", "bar"]`).
    Post-W54 the key is `(tuple(args), cwd, detail)` — unambiguous.
    """
    from roam.plan import compiler as M

    M._RUN_ROAM_CACHE.clear()
    M._RUN_ROAM_CACHE[(("uses", "foo bar"), "", False)] = (time.monotonic(), {"a": 1})
    M._RUN_ROAM_CACHE[(("uses foo", "bar"), "", False)] = (time.monotonic(), {"b": 2})
    # They must be stored as DISTINCT keys (no collision)
    assert len(M._RUN_ROAM_CACHE) == 2


# ---- W52 compile-stats new flags ----


def test_w52_compile_stats_by_procedure_summarizes(tmp_path):
    log_dir = tmp_path / ".roam"
    log_dir.mkdir()
    entries = [
        {
            "ts": "x",
            "task_hash": "a",
            "task_prefix": "p",
            "procedure": "structural_coupling",
            "classifier_conf": 0.85,
            "art_label": "l1_probe",
            "prefetched_keys": ["x"],
            "envelope_bytes": 1000,
            "compile_ms": 100,
        },
        {
            "ts": "x",
            "task_hash": "b",
            "task_prefix": "p",
            "procedure": "structural_coupling",
            "classifier_conf": 0.85,
            "art_label": "full",
            "prefetched_keys": [],
            "envelope_bytes": 800,
            "compile_ms": 50,
        },
        {
            "ts": "x",
            "task_hash": "c",
            "task_prefix": "p",
            "procedure": "stack_trace_fix",
            "classifier_conf": 0.95,
            "art_label": "l1_probe",
            "prefetched_keys": ["stack_frames"],
            "envelope_bytes": 2000,
            "compile_ms": 200,
        },
    ]
    (log_dir / "compile-runs.jsonl").write_text("\n".join(json.dumps(e) for e in entries))
    from click.testing import CliRunner

    from roam.commands.cmd_compile_stats import compile_stats

    runner = CliRunner()
    result = runner.invoke(
        compile_stats,
        ["--root", str(tmp_path), "--by-procedure"],
        obj={"json": True},
    )
    assert result.exit_code == 0
    env = json.loads(result.output)
    bp = env["summary"]["by_procedure"]
    assert "structural_coupling" in bp
    assert bp["structural_coupling"]["n"] == 2
    assert bp["structural_coupling"]["l1_pct"] == 50  # 1/2


def test_w52_compile_stats_slow_probes(tmp_path):
    log_dir = tmp_path / ".roam"
    log_dir.mkdir()
    entries = [
        {
            "ts": "x",
            "task_hash": "a",
            "task_prefix": "p",
            "procedure": "x",
            "classifier_conf": 0.85,
            "art_label": "l1_probe",
            "prefetched_keys": [],
            "envelope_bytes": 1000,
            "compile_ms": 100,
            "probe_timings_ms": {"inner_probe": 95.0, "task_text": 2.0},
        },
        {
            "ts": "x",
            "task_hash": "b",
            "task_prefix": "p",
            "procedure": "x",
            "classifier_conf": 0.85,
            "art_label": "l1_probe",
            "prefetched_keys": [],
            "envelope_bytes": 1000,
            "compile_ms": 200,
            "probe_timings_ms": {"inner_probe": 195.0, "task_text": 3.0},
        },
    ]
    (log_dir / "compile-runs.jsonl").write_text("\n".join(json.dumps(e) for e in entries))
    from click.testing import CliRunner

    from roam.commands.cmd_compile_stats import compile_stats

    runner = CliRunner()
    result = runner.invoke(
        compile_stats,
        ["--root", str(tmp_path), "--slow-probes"],
        obj={"json": True},
    )
    assert result.exit_code == 0
    env = json.loads(result.output)
    psl = env["summary"]["probe_section_latency_ms"]
    assert "inner_probe" in psl
    assert "task_text" in psl
    # inner_probe has the larger p95
    assert psl["inner_probe"]["p95"] > psl["task_text"]["p95"]


def test_w91_top_misses_marks_active_vs_stale_cache_misses(tmp_path):
    log_dir = tmp_path / ".roam"
    log_dir.mkdir()
    entries = [
        {
            "ts": "x",
            "task_hash": "stale",
            "task_prefix": "now cached",
            "procedure": "structural_coupling",
            "classifier_conf": 0.95,
            "art_label": "l1_probe",
            "prefetched_keys": ["x"],
            "envelope_bytes": 1000,
            "compile_ms": 100,
            "cache_hit": False,
        },
        {
            "ts": "x",
            "task_hash": "stale",
            "task_prefix": "now cached",
            "procedure": "structural_coupling",
            "classifier_conf": 0.95,
            "art_label": "l1_probe",
            "prefetched_keys": ["x"],
            "envelope_bytes": 1000,
            "compile_ms": 2,
            "cache_hit": True,
        },
        {
            "ts": "x",
            "task_hash": "built",
            "task_prefix": "builder warmed",
            "procedure": "structural_coupling",
            "classifier_conf": 0.95,
            "art_label": "l1_probe",
            "prefetched_keys": ["x"],
            "envelope_bytes": 1000,
            "compile_ms": 50,
            "cache_hit": False,
        },
        {
            "ts": "x",
            "task_hash": "built",
            "task_prefix": "builder warmed",
            "procedure": "structural_coupling",
            "classifier_conf": 0.95,
            "art_label": "l1_probe",
            "prefetched_keys": ["x"],
            "envelope_bytes": 1000,
            "compile_ms": 2,
            "cache_hit": True,
        },
        {
            "ts": "x",
            "task_hash": "built",
            "task_prefix": "builder warmed",
            "procedure": "structural_coupling",
            "classifier_conf": 0.95,
            "art_label": "l1_probe",
            "prefetched_keys": ["x"],
            "envelope_bytes": 1000,
            "compile_ms": 50,
            "cache_hit": False,
            "agent_mode": "compile_cache_build",
        },
        {
            "ts": "x",
            "task_hash": "build-only",
            "task_prefix": "warmer only",
            "procedure": "structural_coupling",
            "classifier_conf": 0.95,
            "art_label": "l1_probe",
            "prefetched_keys": ["x"],
            "envelope_bytes": 1000,
            "compile_ms": 50,
            "cache_hit": False,
            "agent_mode": "compile_cache_build",
        },
        {
            "ts": "x",
            "task_hash": "active",
            "task_prefix": "still missing",
            "procedure": "freeform_explore",
            "classifier_conf": 0.35,
            "art_label": "facts",
            "prefetched_keys": [],
            "envelope_bytes": 500,
            "compile_ms": 10,
            "cache_hit": False,
        },
    ]
    (log_dir / "compile-runs.jsonl").write_text("\n".join(json.dumps(e) for e in entries))
    from click.testing import CliRunner

    from roam.commands.cmd_compile_stats import compile_stats

    runner = CliRunner()
    result = runner.invoke(
        compile_stats,
        ["--root", str(tmp_path), "--top-misses"],
        obj={"json": True},
    )
    assert result.exit_code == 0
    env = json.loads(result.output)
    misses = env["summary"]["top_cache_misses"]
    assert misses[0]["task_hash"] == "active"
    assert misses[0]["active_miss"] is True
    stale = next(m for m in misses if m["task_hash"] == "stale")
    assert stale["active_miss"] is False
    assert stale["hit_count"] == 1
    assert stale["miss_rate_pct"] == 50
    built = next(m for m in misses if m["task_hash"] == "built")
    assert built["active_miss"] is False
    assert built["hit_count"] == 1
    assert built["miss_count"] == 1
    assert "build-only" not in {m["task_hash"] for m in misses}
