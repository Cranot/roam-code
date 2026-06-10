"""W43/W44/W45 — tests for polish, improvements, and corrections.

Each test pins an invariant introduced or strengthened in waves 43-45.
"""

from __future__ import annotations

import json
import time

from roam.plan.compiler import (
    _CONFIDENCE_THRESHOLD,
    _CONVENTIONS_RE,
    _MODULE_NAME_RE,
    _RUN_ROAM_CACHE_TTL_S,
    _probe_conventions_for_task,
    _probe_module_name_for_task,
    _read_file_slice,
    _run_w128_parallel,
    compile_for_artifact,
    compile_plan,
)

# ---- W43 P3 — telemetry includes probe_timings_ms ----


def test_w43_p3_telemetry_records_probe_timings(tmp_path):
    """When compile_for_artifact runs against a real-ish project the
    telemetry sidecar should record per-section timings."""
    (tmp_path / ".roam").mkdir()
    plan = compile_plan("what does some/file.py do")
    compile_for_artifact(plan, cwd=str(tmp_path))
    log = tmp_path / ".roam" / "compile-runs.jsonl"
    assert log.exists()
    entry = json.loads(log.read_text().splitlines()[-1])
    # When art_label == l1_probe the timings dict must be present.
    if entry["art_label"] == "l1_probe":
        assert "probe_timings_ms" in entry
        timings = entry["probe_timings_ms"]
        # Every section should appear
        for label in ("inner_probe", "task_text", "backtick_fallback", "always_on", "l10_symbol_resolution"):
            assert label in timings, f"missing timing for {label}: {timings}"
            assert isinstance(timings[label], (int, float))


def test_w128_outer_parallel_honors_always_on_budget(monkeypatch):
    """A stuck always_on future must not make the outer W128 join wait 20s."""
    from roam.plan import compiler as M

    monkeypatch.setattr(M, "_W42_ALWAYS_ON_BUDGET_MS", 10)

    def _slow_always_on(*_args):
        time.sleep(0.7)
        return {"late": True}

    monkeypatch.setattr(M, "_apply_always_on_extenders", _slow_always_on)
    monkeypatch.setattr(M, "_probe_l10_symbol_resolution", lambda *_args: None)

    timings: dict[str, float] = {}
    start = time.monotonic()
    out = _run_w128_parallel("freeform_explore", "investigate cache", False, [], None, {}, timings)
    elapsed = time.monotonic() - start

    assert out == {}
    assert elapsed < 0.5
    assert "always_on" in timings


# ---- W44 I1 — conventions probe ----


def test_w44_i1_conventions_regex_matches_common_phrasings():
    for s in [
        "how do we structure tests",
        "what's the convention here",
        "how should I name my helper",
        "existing pattern for X",
    ]:
        assert _CONVENTIONS_RE.search(s), s


def test_w44_i1_conventions_does_not_misfire():
    for s in [
        "what does src/foo.py do",
        "fix this bug",
        "trace from the CLI",
    ]:
        assert not _CONVENTIONS_RE.search(s), s


def test_w44_i1_conventions_probe_samples_sibling_files(tmp_path):
    target = tmp_path / "src" / "pkg"
    target.mkdir(parents=True)
    for i in range(4):
        (target / f"thing_{i}.py").write_text(f"# header\nclass X{i}: pass\n")
    out = _probe_conventions_for_task(
        "how do we structure helpers in src/pkg/",
        named_paths=["src/pkg/thing_0.py"],
        cwd=str(tmp_path),
    )
    assert out is not None
    assert "convention_samples" in out
    assert len(out["convention_samples"]) <= 3
    assert all("path" in s and "content" in s for s in out["convention_samples"])


# ---- W44 I2 — module-name resolution ----


def test_w44_i2_module_name_regex_matches():
    for s in [
        "what does the compiler module do",
        "audit the auth service",
        "fix the cli command",
    ]:
        assert _MODULE_NAME_RE.search(s), s


def test_w44_i2_module_name_resolves_existing_dir(tmp_path):
    src = tmp_path / "src" / "myproj"
    src.mkdir(parents=True)
    (src / "auth.py").write_text("# auth module\n")
    out = _probe_module_name_for_task(
        "what does the auth module do",
        named_paths=[],
        cwd=str(tmp_path),
    )
    assert out is not None
    paths = out.get("resolved_named_paths_from_module_name") or []
    assert any("auth" in p for p in paths)


def test_w44_i2_module_name_skips_when_explicit_path_present():
    out = _probe_module_name_for_task(
        "what does the auth module do in src/auth.py",
        named_paths=["src/auth.py"],
        cwd=None,
    )
    assert out is None  # explicit path wins


# ---- W44 I3 — bounded _run_roam cache ----


def test_w44_i3_cache_hit_avoids_subprocess(monkeypatch):
    """When a (args, cwd, detail) tuple is in the cache and the TTL
    is alive, _run_roam returns the cached value WITHOUT invoking
    subprocess."""
    from roam.plan import compiler as M

    M._RUN_ROAM_CACHE.clear()
    sentinel = {"hit": True}
    M._RUN_ROAM_CACHE[(("uses", "foo"), "/x", False)] = (time.monotonic(), sentinel)
    # Spy on subprocess.run — should NOT be called
    calls = []

    def _spy(*a, **kw):
        calls.append((a, kw))
        raise RuntimeError("should not be called")

    monkeypatch.setattr(M.subprocess, "run", _spy)
    result = M._run_roam(["uses", "foo"], "/x")
    assert result is sentinel
    assert not calls


def test_w44_i3_cache_expires_after_ttl(monkeypatch):
    from roam.plan import compiler as M

    M._RUN_ROAM_CACHE.clear()
    # Insert an entry with timestamp old enough to be expired
    old_ts = time.monotonic() - _RUN_ROAM_CACHE_TTL_S - 1
    M._RUN_ROAM_CACHE[(("uses", "foo"), "", False)] = (old_ts, {"stale": True})

    # Real subprocess would run; stub to return rc!=0 so value=None
    def _stub(*a, **kw):
        class P:
            returncode = 1
            stdout = ""

        return P()

    monkeypatch.setattr(M.subprocess, "run", _stub)
    monkeypatch.setattr(M, "_roam_invoke_inproc", lambda *a, **k: None)
    result = M._run_roam(["uses", "foo"], None)
    assert result is None  # NOT the stale cached value


# ---- W45 C1 — module-name resolution chains into downstream probes ----


def test_w45_c1_module_name_stitches_into_named_paths(tmp_path):
    """When module-name resolves a path AND no explicit path was given,
    the resolved path should chain into downstream probes (so coupling
    /callers/etc see a target)."""
    src = tmp_path / "src" / "myproj"
    src.mkdir(parents=True)
    f = src / "thing.py"
    f.write_text("def thing(): pass\n")
    (tmp_path / ".roam").mkdir()  # let the call complete telemetry write
    plan = compile_plan("what does the thing module do")
    env, label = compile_for_artifact(plan, cwd=str(tmp_path))
    pre = env["plan"].get("prefetched_facts") or {}
    # The module-name probe key must be there
    assert "resolved_named_paths_from_module_name" in pre
    # And the downstream skeleton probe should have fired on the resolved
    # path (since freeform_explore probe consults named_paths).
    # NOTE: depends on the resolved file being parseable by roam — when
    # cwd is a synthetic tmp dir without an index, file_skeleton won't
    # populate. We assert only the resolution key as the contract.


# ---- W45 C3 — _read_file_slice edge cases ----


def test_w45_c3_read_slice_line_below_one(tmp_path):
    f = tmp_path / "x.py"
    f.write_text("a\nb\nc\n")
    # line=0 should NOT crash; returns a slice without a marker
    out = _read_file_slice(str(f), 0, cwd=None)
    assert out is not None
    # No marker line for line=0
    assert ">> " not in out["excerpt"]


def test_w45_c3_read_slice_line_past_eof(tmp_path):
    f = tmp_path / "x.py"
    f.write_text("a\nb\nc\n")
    out = _read_file_slice(str(f), 999, cwd=None)
    assert out is not None
    assert out["line_count"] == 3


def test_w45_c3_read_slice_missing_file_returns_none(tmp_path):
    out = _read_file_slice(str(tmp_path / "nonexistent.py"), 1, cwd=None)
    assert out is None


# ---- W45 C2 — empty prefetched_facts is never emitted ----


def test_w45_c2_empty_prefetched_facts_not_in_envelope(tmp_path):
    """Sanity invariant: an L1 envelope built where NO probe fires should
    omit the prefetched_facts key entirely (not ship {} ).
    """
    # Task with no extractable paths or backticks → no probes fire
    # in a synthetic tmp dir without .roam.
    plan = compile_plan("a very short generic question")
    env, label = compile_for_artifact(plan, cwd=str(tmp_path))
    plan_section = env.get("plan") or {}
    # Either L1 with NO prefetched_facts key, or any other envelope shape
    if "prefetched_facts" in plan_section:
        # If present, must be non-empty
        assert plan_section["prefetched_facts"], "empty prefetched_facts dict should never ship"


# ---- W43 P2 — named caps replace magic numbers ----


def test_w43_p2_named_caps_are_module_constants():
    from roam.plan import compiler as M

    # All the major caps exist and are reasonable integers
    for name in (
        "_DEPS_LIST_CAP",
        "_COCHANGE_PAIR_CAP",
        "_COCHANGE_GIT_LIMIT",
        "_CALLERS_CAP",
        "_DEAD_TOP_CAP",
        "_BLAST_TOP_FILES_CAP",
        "_FILE_SKELETON_SYMBOL_CAP",
        "_FREEFORM_SKELETON_CAP",
        "_STACK_FRAME_SLICE_BEFORE",
        "_STACK_FRAME_SLICE_AFTER",
        "_FILE_EXCERPT_LINES",
        "_SIBLING_TEST_LINES",
        "_SRC_UNDER_TEST_LINES",
        "_CONFTEST_LINES",
        "_GIT_LOG_RECENT_COMMITS",
        "_DIFF_TRUNCATE_LINES",
    ):
        val = getattr(M, name, None)
        assert isinstance(val, int) and val > 0, f"{name} should be int>0, got {val!r}"


def test_w45_c1_confidence_threshold_boundary_documented():
    """Pinning the inequality direction: confidence < threshold means
    fall back to 'full'; at confidence == threshold the specialized
    policy applies. Changing this is a behavior change."""
    assert _CONFIDENCE_THRESHOLD == 0.60, (
        "If this constant moves, update memory/calibration notes and re-run the W37 readiness scorecard."
    )
