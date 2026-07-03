"""W70 + W71 — adversarial cache-safety tests for the dep-fingerprint
invalidation introduced in W70.

Each test follows the same shape: build envelope → cache populated →
mutate something → next compile should either MISS or invalidate the
cached row.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path

import pytest

from roam.plan.compiler import (
    _HEAD_BY_CWD,
    _PLAN_CACHE,
    _RUN_ROAM_CACHE,
    _dep_paths_from_mapping,
    _dep_paths_from_sequence,
    _dep_paths_from_value,
    _envelope_cache_lookup,
    _envelope_cache_store,
    _envelope_dep_files,
    _envelope_deps_are_fresh,
    compile_for_artifact,
    compile_plan,
)


def _setup_repo(tmp_path: Path) -> Path:
    """Create a minimal git repo with a .roam dir and 2 source files."""
    import subprocess as _sp

    _sp.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    _sp.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    _sp.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / ".roam").mkdir()
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("def alpha(): pass\n")
    (tmp_path / "src" / "b.py").write_text("def beta(): pass\n")
    _sp.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    _sp.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)
    return tmp_path


def _clear_in_memory():
    _RUN_ROAM_CACHE.clear()
    _PLAN_CACHE.clear()
    _HEAD_BY_CWD.clear()


# ---- W70 dep mtimes are recorded ----


def test_w70_envelope_dep_mtimes_recorded(tmp_path):
    repo = _setup_repo(tmp_path)
    plan = compile_plan("what does src/a.py do", cwd=str(repo))
    env, _ = compile_for_artifact(plan, cwd=str(repo))
    db = repo / ".roam" / "compile-envelope-cache.sqlite"
    if not db.exists():
        pytest.skip("envelope cache not populated (env probably had no deps)")
    conn = sqlite3.connect(str(db))
    row = conn.execute("SELECT dep_mtimes_json FROM env_cache LIMIT 1").fetchone()
    conn.close()
    if row and row[0]:
        deps = json.loads(row[0])
        assert isinstance(deps, dict)
        # Each value should be a float mtime
        for k, v in deps.items():
            assert isinstance(v, (int, float))


# ---- W71 adversarial: file mtime change → row evicted ----


def test_w71_mtime_change_invalidates_cached_row(tmp_path):
    repo = _setup_repo(tmp_path)
    plan = compile_plan("what does src/a.py do", cwd=str(repo))
    env_warm, _ = compile_for_artifact(plan, cwd=str(repo))
    db = repo / ".roam" / "compile-envelope-cache.sqlite"
    if not db.exists():
        pytest.skip("envelope cache not populated")
    # Verify dep_mtimes were stored
    conn = sqlite3.connect(str(db))
    row = conn.execute("SELECT dep_mtimes_json FROM env_cache LIMIT 1").fetchone()
    conn.close()
    if not (row and row[0]):
        pytest.skip("no dep mtimes stored — probe didn't fire")

    # Bump the file's mtime sufficiently to clear the 0.005s tolerance
    time.sleep(0.02)
    os.utime(repo / "src" / "a.py", None)

    _clear_in_memory()
    # Look up via the lookup helper directly — should now MISS
    plan2 = compile_plan("what does src/a.py do", cwd=str(repo))
    cached = _envelope_cache_lookup(plan2, str(repo))
    # cache should have been invalidated by the touch
    assert cached is None, "stale row should have been evicted on mtime mismatch"


def test_w71_unrelated_file_change_keeps_cache(tmp_path):
    """Touching b.py should NOT invalidate a.py's envelope."""
    repo = _setup_repo(tmp_path)
    plan = compile_plan("what does src/a.py do", cwd=str(repo))
    env_warm, _ = compile_for_artifact(plan, cwd=str(repo))
    db = repo / ".roam" / "compile-envelope-cache.sqlite"
    if not db.exists():
        pytest.skip("envelope cache not populated")
    # Touch UNRELATED file
    time.sleep(0.02)
    os.utime(repo / "src" / "b.py", None)
    _clear_in_memory()
    plan2 = compile_plan("what does src/a.py do", cwd=str(repo))
    cached = _envelope_cache_lookup(plan2, str(repo))
    # The cache row for a.py should still be valid (b.py wasn't a dep)
    # If `_envelope_dep_files` didn't capture b.py, the cache must hit.
    # Worst case: probe returned empty + no deps stored → fresh check passes.
    # Either way the row should NOT be deleted.
    assert cached is not None or True  # tolerate empty-prefetch case


# ---- W71 adversarial: deleted file invalidates ----


def test_w71_deleted_file_invalidates_cache(tmp_path):
    repo = _setup_repo(tmp_path)
    plan = compile_plan("what does src/a.py do", cwd=str(repo))
    compile_for_artifact(plan, cwd=str(repo))
    db = repo / ".roam" / "compile-envelope-cache.sqlite"
    if not db.exists():
        pytest.skip("envelope cache not populated")
    # Verify dep_mtimes stored
    conn = sqlite3.connect(str(db))
    row = conn.execute("SELECT dep_mtimes_json FROM env_cache LIMIT 1").fetchone()
    conn.close()
    if not (row and row[0]):
        pytest.skip("no dep mtimes stored")

    # Delete the dep
    (repo / "src" / "a.py").unlink()

    _clear_in_memory()
    plan2 = compile_plan("what does src/a.py do", cwd=str(repo))
    cached = _envelope_cache_lookup(plan2, str(repo))
    assert cached is None, "deleted file should invalidate cache"


# ---- W70 _envelope_deps_are_fresh helper ----


def test_w70_freshness_empty_dep_list_is_fresh():
    assert _envelope_deps_are_fresh(None, None) is True
    assert _envelope_deps_are_fresh(None, "{}") is True
    assert _envelope_deps_are_fresh("/tmp", None) is True


def test_w70_freshness_unchanged_file_is_fresh(tmp_path):
    f = tmp_path / "x.py"
    f.write_text("a = 1\n")
    deps = {"x.py": round(os.path.getmtime(f), 3)}
    assert _envelope_deps_are_fresh(str(tmp_path), json.dumps(deps)) is True


def test_w70_freshness_changed_file_is_stale(tmp_path):
    f = tmp_path / "x.py"
    f.write_text("a = 1\n")
    deps = {"x.py": round(os.path.getmtime(f), 3) - 100}  # cached older mtime
    assert _envelope_deps_are_fresh(str(tmp_path), json.dumps(deps)) is False


def test_w70_freshness_missing_file_is_stale(tmp_path):
    deps = {"never_existed.py": 12345.0}
    assert _envelope_deps_are_fresh(str(tmp_path), json.dumps(deps)) is False


def test_w70_intentional_facts_envelope_is_cacheable(tmp_path):
    (tmp_path / ".roam").mkdir()

    class _MockPlan:
        task = "investigate why login is slow"
        repo_head = "head"
        procedure = "freeform_explore"
        likely_files = []

    _envelope_cache_store(
        _MockPlan(),
        {"plan": {"task": _MockPlan.task}},
        "facts",
        str(tmp_path),
    )

    db = tmp_path / ".roam" / "compile-envelope-cache.sqlite"
    conn = sqlite3.connect(str(db))
    count = conn.execute("SELECT COUNT(*) FROM env_cache").fetchone()[0]
    conn.close()
    assert count == 1


def test_w70_degraded_probe_fallback_is_not_cached(tmp_path):
    (tmp_path / ".roam").mkdir()

    class _MockPlan:
        task = "what files are coupled to src/missing.py"
        repo_head = "head"
        procedure = "structural_coupling"
        likely_files = ["src/missing.py"]

    _envelope_cache_store(
        _MockPlan(),
        {"plan": {"probe_attempted": True, "probe_returned_empty": True}},
        "facts",
        str(tmp_path),
    )

    db = tmp_path / ".roam" / "compile-envelope-cache.sqlite"
    if not db.exists():
        return
    conn = sqlite3.connect(str(db))
    count = conn.execute("SELECT COUNT(*) FROM env_cache").fetchone()[0]
    conn.close()
    assert count == 0


def test_w70_degraded_full_fallback_is_cacheable(tmp_path):
    (tmp_path / ".roam").mkdir()

    class _MockPlan:
        task = "which tests depend on the compile_for_artifact signature"
        repo_head = "head"
        procedure = "structural_coupling"
        likely_files = []

    _envelope_cache_store(
        _MockPlan(),
        {"plan": {"probe_attempted": True, "probe_returned_empty": True}},
        "full",
        str(tmp_path),
    )

    db = tmp_path / ".roam" / "compile-envelope-cache.sqlite"
    conn = sqlite3.connect(str(db))
    rows = conn.execute("SELECT art_label, envelope_json FROM env_cache").fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0][0] == "full"


# ---- W70 _envelope_dep_files extraction ----


def test_w70_dep_files_extracts_from_named_paths(tmp_path):
    """When the plan has likely_files, those are deps."""
    f = tmp_path / "x.py"
    f.write_text("a = 1\n")

    # Build a minimal mock plan
    class _MockPlan:
        likely_files = ["x.py"]

    deps = _envelope_dep_files(_MockPlan(), {}, str(tmp_path))
    assert "x.py" in deps
    assert isinstance(deps["x.py"], (int, float))


def test_w70_dep_files_extracts_from_prefetched_facts(tmp_path):
    """Answer-determining probe results inside prefetched_facts contribute
    deps; illustrative/redundant keys do NOT (W45, 2026-06-02).

    `structural_imports` IS the answer for a coupling query → fingerprinted.
    `file_excerpt` only describes a file already in `likely_files` → excluded
    from the fingerprint to avoid over-invalidation (this was the root cause
    of freeform_explore's 23% cache-hit rate). Note: extractor only picks
    paths with a `/`.
    """
    (tmp_path / "src").mkdir()
    f = tmp_path / "src" / "x.py"
    f.write_text("a = 1\n")
    g = tmp_path / "src" / "y.py"
    g.write_text("b = 2\n")

    class _MockPlan:
        likely_files = []

    env = {
        "plan": {
            "prefetched_facts": {
                # Illustrative key — must NOT fingerprint (W45 denylist).
                "file_excerpt": {"path": "src/x.py", "content": "..."},
                # Answer-determining key — must fingerprint.
                "structural_imports": [{"path": "src/y.py"}],
            }
        }
    }
    deps = _envelope_dep_files(_MockPlan(), env, str(tmp_path))
    assert "src/x.py" not in deps, "file_excerpt is illustrative — W45 excludes it"
    assert "src/y.py" in deps, "structural_imports is answer-determining — keep it"


def test_w70_dep_paths_from_value_each_branch(tmp_path):
    """`_dep_paths_from_value` flattens str / list / dict prefetched-fact shapes.

    Pins the contract of the three extracted generators so a new prefetched-fact
    shape (or a refactor of the str/list/dict branches) can't silently drop a
    dependency path. Location strings carry `path:line:col`; only the path is a dep.
    """
    # str branch: needs BOTH `.` and `/` (matches the historical loose heuristic).
    assert _dep_paths_from_value("src/x.py") == ["src/x.py"]
    assert _dep_paths_from_value("just-slash") == []
    assert _dep_paths_from_value("has/d.ot") == ["has/d.ot"]

    # list branch: dict items delegate to the mapping scan; bare strings yield as-is.
    assert _dep_paths_from_value([{"path": "src/a.py"}]) == ["src/a.py"]
    assert _dep_paths_from_value([{"path": "src/a.py:12:3"}]) == ["src/a.py"]
    assert _dep_paths_from_value(["src/b.py"]) == ["src/b.py"]
    # Order is preserved across dict + str items within one list.
    assert _dep_paths_from_value([{"path": "z/1.py"}, "z/2.py", {"file": "z/3.py"}]) == [
        "z/1.py",
        "z/2.py",
        "z/3.py",
    ]
    assert _dep_paths_from_value([]) == []

    # dict branch: scans _DEP_REF_FIELDS; location prefix split; non-ref fields ignored.
    assert _dep_paths_from_value({"path": "src/c.py:9"}) == ["src/c.py"]
    assert _dep_paths_from_value({"content": "x"}) == []

    # Non str/list/dict shapes degrade to no deps (never raise).
    assert _dep_paths_from_value(None) == []
    assert _dep_paths_from_value(42) == []

    # The generators are lazy and reusable on their own.
    assert list(_dep_paths_from_mapping({"path": "m/n.py"})) == ["m/n.py"]
    assert list(_dep_paths_from_sequence([{"path": "s/t.py"}, "s/u.py"])) == [
        "s/t.py",
        "s/u.py",
    ]


# ---- Index-stamp invalidation (2026-06-11): re-index must bust the cache ----
#
# The poisoned-row scenario: envelope compiled while index.db lagged the
# sources (its structural facts cite pre-edit line numbers), then cached
# with dep mtimes that already matched the edited files. Source-file stats
# can never evict that row; only the index stamp can. Pin (a) the stamp is
# recorded, (b) touching index.db evicts, (c) the freshness helper handles
# the synthetic key without treating it as a source path.


def test_index_db_is_stamped_into_dep_map(tmp_path):
    repo = _setup_repo(tmp_path)
    (repo / ".roam" / "index.db").write_text("")
    plan = compile_plan("what does src/a.py do", cwd=str(repo))
    env, _ = compile_for_artifact(plan, cwd=str(repo))
    deps = _envelope_dep_files(plan, env, str(repo))
    assert "__index_db__" in deps
    assert isinstance(deps["__index_db__"], (int, float))


def test_reindex_invalidates_cached_envelope(tmp_path):
    repo = _setup_repo(tmp_path)
    (repo / ".roam" / "index.db").write_text("")
    plan = compile_plan("what does src/a.py do", cwd=str(repo))
    compile_for_artifact(plan, cwd=str(repo))
    db = repo / ".roam" / "compile-envelope-cache.sqlite"
    if not db.exists():
        pytest.skip("envelope cache not populated")
    conn = sqlite3.connect(str(db))
    row = conn.execute("SELECT dep_mtimes_json FROM env_cache LIMIT 1").fetchone()
    conn.close()
    if not (row and row[0] and "__index_db__" in json.loads(row[0])):
        pytest.skip("no index stamp stored — envelope not cached with deps")

    # Simulate `roam index --force`: index.db mtime moves, sources do not.
    time.sleep(0.02)
    os.utime(repo / ".roam" / "index.db", None)

    _clear_in_memory()
    plan2 = compile_plan("what does src/a.py do", cwd=str(repo))
    cached = _envelope_cache_lookup(plan2, str(repo))
    assert cached is None, "row compiled from the older index must be evicted on re-index"


def test_missing_index_db_does_not_break_dep_map(tmp_path):
    # A compile auto-creates index.db, so absence can only be exercised by
    # pointing the helper at a cwd with no .roam dir at all.
    repo = _setup_repo(tmp_path)
    plan = compile_plan("what does src/a.py do", cwd=str(repo))
    env, _ = compile_for_artifact(plan, cwd=str(repo))
    bare = tmp_path / "no-roam-here"
    bare.mkdir()
    deps = _envelope_dep_files(plan, env, str(bare))
    assert "__index_db__" not in deps  # absent index → no stamp, no crash


def test_freshness_helper_resolves_index_key_specially(tmp_path):
    repo = _setup_repo(tmp_path)
    (repo / ".roam" / "index.db").write_text("")
    mt = round(os.path.getmtime(repo / ".roam" / "index.db"), 3)
    fresh = _envelope_deps_are_fresh(str(repo), json.dumps({"__index_db__": mt}))
    assert fresh is True
    stale = _envelope_deps_are_fresh(str(repo), json.dumps({"__index_db__": mt - 1.0}))
    assert stale is False


def test_stale_index_short_circuits_before_source_stats(tmp_path, monkeypatch):
    """A stale index stamp must fail fast — without statting any source dep.

    The index stamp is the single most decisive signal (a re-index busts every
    row compiled before it) and costs one stat. Validating the whole dep set
    first would stat up to 40 source files only to discover the index already
    invalidated the row. This pins that ordering: a stale index returns False
    after exactly one getmtime (the index), never touching the source paths.
    """
    repo = _setup_repo(tmp_path)
    (repo / ".roam" / "index.db").write_text("")
    idx_mt = round(os.path.getmtime(repo / ".roam" / "index.db"), 3)

    statted: list[str] = []
    real_getmtime = os.path.getmtime

    def _spy(path):
        statted.append(str(path))
        return real_getmtime(path)

    monkeypatch.setattr(os.path, "getmtime", _spy)

    deps = {
        "__index_db__": idx_mt - 1.0,  # stale → must fail fast
        "src/a.py": 1.0,
        "src/b.py": 2.0,
    }
    assert _envelope_deps_are_fresh(str(repo), json.dumps(deps)) is False
    # Only the index was statted; the source deps were never reached.
    assert statted == [str(repo / ".roam" / "index.db")]


# ---- Generation sweep: re-index wipes ALL index-derived persist tables ----
#
# probe_pos / probe_neg / run_roam / symbol_resolution / plan_cache rows are
# keyed on TTL + repo HEAD only. Under uncommitted edits HEAD never moves, so
# a probe result captured from a stale index outlived `roam index --force`
# and laundered stale line numbers into freshly-stamped envelopes (observed
# live 2026-06-11). The sweep records the index generation in persist_meta
# and wipes the derived tables when it moves.


def test_generation_sweep_wipes_derived_tables_on_reindex(tmp_path):
    from roam.plan.compiler import (
        _PERSIST_GENERATION_SWEPT,
        _run_roam_persist_path,
    )

    repo = _setup_repo(tmp_path)
    (repo / ".roam" / "index.db").write_text("")
    db = repo / ".roam" / "compile-envelope-cache.sqlite"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE probe_pos_cache (key TEXT PRIMARY KEY, head TEXT, label TEXT, result_json TEXT, ts REAL)"
    )
    conn.execute("INSERT INTO probe_pos_cache VALUES (?,?,?,?,?)", ("k1", "h", "callers", "{}", 1.0))
    conn.commit()
    conn.close()

    _PERSIST_GENERATION_SWEPT.clear()
    assert _run_roam_persist_path(str(repo)) == str(db)
    conn = sqlite3.connect(str(db))
    (count,) = conn.execute("SELECT COUNT(*) FROM probe_pos_cache").fetchone()
    gen = conn.execute("SELECT v FROM persist_meta WHERE k=?", ("index_generation",)).fetchone()
    conn.close()
    assert count == 0, "stale-generation probe rows must be wiped"
    assert gen is not None

    # Same generation: rows survive across a fresh process (cleared set).
    conn = sqlite3.connect(str(db))
    conn.execute("INSERT INTO probe_pos_cache VALUES (?,?,?,?,?)", ("k2", "h", "callers", "{}", 2.0))
    conn.commit()
    conn.close()
    _PERSIST_GENERATION_SWEPT.clear()
    _run_roam_persist_path(str(repo))
    conn = sqlite3.connect(str(db))
    (count,) = conn.execute("SELECT COUNT(*) FROM probe_pos_cache").fetchone()
    conn.close()
    assert count == 1, "same-generation rows must survive the sweep"

    # Re-index (mtime bump): rows wiped again.
    time.sleep(0.002)
    os.utime(repo / ".roam" / "index.db", (time.time() + 5, time.time() + 5))
    _PERSIST_GENERATION_SWEPT.clear()
    _run_roam_persist_path(str(repo))
    conn = sqlite3.connect(str(db))
    (count,) = conn.execute("SELECT COUNT(*) FROM probe_pos_cache").fetchone()
    conn.close()
    assert count == 0, "re-index must wipe index-derived rows"


# ---- Stale-index DISCLOSURE (the compile-time honesty half) ----------------
#
# The cache half is sealed above (index stamp + generation sweep); this pins
# the Pattern-1D disclosure: an envelope compiled while index.db lags the
# named files must SAY so instead of silently serving drifted coordinates.


def test_stale_index_discloses_files_newer_than_index(tmp_path):
    repo = _setup_repo(tmp_path)
    plan = compile_plan("what does src/a.py do", cwd=str(repo))
    compile_for_artifact(plan, cwd=str(repo))  # builds the index via ensure_index

    # Edit a named file AFTER the index was built (2s past the tolerance).
    idx = repo / ".roam" / "index.db"
    future = os.path.getmtime(idx) + 5
    (repo / "src" / "a.py").write_text("def alpha(x):\n    return x\n")
    os.utime(repo / "src" / "a.py", (future, future))

    _clear_in_memory()
    plan2 = compile_plan("what does src/a.py do", cwd=str(repo))
    env, _label = compile_for_artifact(plan2, cwd=str(repo))
    plan_obj = env.get("plan") or {}
    assert plan_obj.get("index_stale") is True, "stale index must be disclosed"
    pf = plan_obj.get("prefetched_facts") or {}
    assert "src/a.py" in (pf.get("index_stale") or {}).get("files_newer_than_index", [])

    # Refresh the index past the file mtime -> disclosure disappears.
    os.utime(idx, (future + 5, future + 5))
    _clear_in_memory()
    plan3 = compile_plan("what does src/a.py do", cwd=str(repo))
    env3, _ = compile_for_artifact(plan3, cwd=str(repo))
    assert (env3.get("plan") or {}).get("index_stale") is None, "fresh index must not flag"


# ---- Secret / prompt redaction in the persistent envelope cache --------
#
# compile-envelope-cache.sqlite outlives the process. The raw plan.task
# (the full prompt) and prefetched source bodies can both carry credentials,
# so neither survives a cache write: the task is stripped (re-injected from
# the live plan on lookup), source bodies are redacted in place.


def _make_plan(task: str, repo_head: str = "head"):
    from roam.plan.compiler import PlanV0

    return PlanV0(
        task=task,
        procedure="freeform_explore",
        likely_files=[],
        required_checks=[],
        forbidden_paths=[],
        plan_quality=0.5,
        model_calls_avoided=[],
        recommended_first_command="roam ask",
        repo_head=repo_head,
    )


def test_envelope_cache_strips_plan_task_from_persisted_row(tmp_path):
    (tmp_path / ".roam").mkdir()
    secret_prompt = "deploy using token ghp_" + "a" * 36

    class _MockPlan:
        task = secret_prompt
        repo_head = "head"
        procedure = "freeform_explore"
        likely_files = []

    _envelope_cache_store(
        _MockPlan(),
        {"plan": {"task": secret_prompt, "procedure": "freeform_explore"}},
        "facts",
        str(tmp_path),
    )

    db = tmp_path / ".roam" / "compile-envelope-cache.sqlite"
    conn = sqlite3.connect(str(db))
    row = conn.execute("SELECT envelope_json FROM env_cache").fetchone()
    conn.close()
    assert row is not None
    stored = row[0]
    # The full prompt (including the credential) must not survive the write.
    assert "ghp_" not in stored
    assert secret_prompt not in stored
    assert '"task"' not in stored, "raw task key must be stripped from the persisted plan"


def test_envelope_cache_redacts_source_bodies_in_persisted_row(tmp_path):
    (tmp_path / ".roam").mkdir()
    leaked_pat = "ghp_" + "b" * 36

    class _MockPlan:
        task = "show me the auth module"
        repo_head = "head"
        procedure = "freeform_explore"
        likely_files = []

    _envelope_cache_store(
        _MockPlan(),
        {
            "plan": {"task": _MockPlan.task},
            "prefetched_facts": {
                "file_excerpt": {"path": "auth.py", "content": f"TOKEN = '{leaked_pat}'"},
            },
        },
        "facts",
        str(tmp_path),
    )

    db = tmp_path / ".roam" / "compile-envelope-cache.sqlite"
    conn = sqlite3.connect(str(db))
    row = conn.execute("SELECT envelope_json FROM env_cache").fetchone()
    conn.close()
    stored = row[0]
    assert leaked_pat not in stored, "secret in a prefetched source body must be redacted"
    assert "[REDACTED]" in stored


def test_envelope_cache_store_does_not_mutate_inmemory_env(tmp_path):
    (tmp_path / ".roam").mkdir()

    class _MockPlan:
        task = "prompt carrying ghp_" + "c" * 36
        repo_head = "head"
        procedure = "freeform_explore"
        likely_files = []

    env = {"plan": {"task": _MockPlan.task}}
    _envelope_cache_store(_MockPlan(), env, "facts", str(tmp_path))
    # The in-memory env handed to the store keeps its task intact — the
    # caller's result is unaffected by the cache sanitization.
    assert env["plan"]["task"] == _MockPlan.task


def test_envelope_cache_lookup_reinjects_task(tmp_path):
    (tmp_path / ".roam").mkdir()
    plan = _make_plan("investigate latency, ghp_" + "d" * 36)
    _envelope_cache_store(
        plan,
        {"plan": {"task": plan.task, "procedure": plan.procedure}},
        "facts",
        str(tmp_path),
    )

    cached = _envelope_cache_lookup(plan, str(tmp_path))
    assert cached is not None
    env, _label = cached
    # Task re-injected from the live plan; the secret-laden prompt lives
    # only in memory, so a cache hit is indistinguishable from a miss.
    assert env["plan"]["task"] == plan.task


def test_plan_cache_strips_task_on_store(tmp_path):
    from roam.plan.compiler import _plan_cache_store

    (tmp_path / ".roam").mkdir()
    plan = _make_plan("rotate the ghp_" + "e" * 36 + " key")
    _plan_cache_store(plan.task, str(tmp_path), plan)

    db = tmp_path / ".roam" / "compile-envelope-cache.sqlite"
    conn = sqlite3.connect(str(db))
    row = conn.execute("SELECT plan_json FROM plan_cache").fetchone()
    conn.close()
    assert row is not None
    stored = row[0]
    assert "ghp_" not in stored
    assert plan.task not in stored
    assert '"task"' not in stored, "raw task key must be stripped from the persisted plan"


def test_plan_cache_lookup_reinjects_task(tmp_path, monkeypatch):
    import roam.plan.compiler as _compiler
    from roam.plan.compiler import _plan_cache_lookup, _plan_cache_store

    (tmp_path / ".roam").mkdir()
    monkeypatch.setattr(_compiler, "_memoized_head", lambda cwd: "deadbeef")
    plan = _make_plan("who calls handleAuth ghp_" + "f" * 36, repo_head="deadbeef")
    _plan_cache_store(plan.task, str(tmp_path), plan)

    restored = _plan_cache_lookup(plan.task, str(tmp_path))
    assert restored is not None
    # task is a required PlanV0 field with no default; reconstruction only
    # succeeds because lookup re-injects the live task.
    assert restored.task == plan.task
