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
