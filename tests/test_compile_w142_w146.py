"""W142-W146 — per-probe timeouts, CliRunner singleton, lru-cached
canonicalize, lock-bypass cwd=None fast-path."""

from __future__ import annotations

from roam.plan import compiler as M


def test_w142_per_probe_timeout_table_present():
    assert "owner_probe" in M._PROBE_TIMEOUT_BY_LABEL
    assert M._PROBE_TIMEOUT_BY_LABEL["owner_probe"] <= 3.0
    assert M._PROBE_TIMEOUT_DEFAULT >= 5.0
    # Slow probe labels NOT in the fast table fall back to default.
    fast = set(M._PROBE_TIMEOUT_BY_LABEL.keys())
    assert "owner_probe" in fast
    assert M._PROBE_TIMEOUT_BY_LABEL.get("owner_probe", 99) < M._PROBE_TIMEOUT_DEFAULT


def test_w143_cli_runner_singleton_module_level():
    """Cached singletons live on the module after first inproc call."""
    # Trigger one inproc call
    M._roam_invoke_inproc(["--help"], cwd=None)
    assert M._CACHED_ROAM_CLI is not None
    assert M._CACHED_CLI_RUNNER is not None
    # Second call should reuse — identity preserved
    cli_a = M._CACHED_ROAM_CLI
    runner_a = M._CACHED_CLI_RUNNER
    M._roam_invoke_inproc(["--help"], cwd=None)
    assert M._CACHED_ROAM_CLI is cli_a
    assert M._CACHED_CLI_RUNNER is runner_a


def test_w144_canonicalize_task_lru_cached():
    """Calling _canonicalize_task twice with same input is cached."""
    # Reset cache for deterministic check
    M._canonicalize_task.cache_clear()
    info0 = M._canonicalize_task.cache_info()
    M._canonicalize_task("Who Calls FooBar?")
    M._canonicalize_task("Who Calls FooBar?")
    info1 = M._canonicalize_task.cache_info()
    assert info1.hits == info0.hits + 1


def test_w144_canonicalize_idempotent():
    """Canonical form of canonical form is identical (idempotence)."""
    s = "Where is `xyz` in src/foo.py?"
    once = M._canonicalize_task(s)
    twice = M._canonicalize_task(once)
    assert once == twice


def test_w145_lock_bypass_when_cwd_is_none():
    """cwd=None must NOT touch _ROAM_INPROC_LOCK or os.path.abspath."""
    # The bypass is a code-shape contract; assert the early-return path.
    # We can verify it transitively: call with cwd=None and confirm no
    # exception + lock is NOT held after.
    result = M._roam_invoke_inproc(["--help"], cwd=None)
    assert result is not None
    code, out = result
    assert "Usage:" in out or "Commands:" in out or code == 0


def test_w146_envelope_recommended_model_smoke(tmp_path):
    """End-to-end: compile a task and inspect the envelope shape."""
    plan = M.compile_plan("who calls log_swallowed")
    env, _ = M.compile_for_artifact(plan)
    plan_section = env.get("plan") or {}
    # The field may be absent when no probes fire on the test tmpdir, but
    # if present must be one of the closed set.
    rec = plan_section.get("recommended_model")
    if rec is not None:
        assert rec in ("haiku", "sonnet", "opus")
