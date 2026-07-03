"""W57.5 — task canonicalization for cache keys + persistent symbol-resolution cache.

Closes the W56-exposed gap where backticked-symbol tasks only got 1.6× warm-cache
speedup because `compile_plan` ran `roam search-semantic` BEFORE the envelope
cache lookup.

Two layers under test:
  (a) `_canonicalize_task` is strictly conservative — lowercase + whitespace
      collapse + smart-quote normalize + strip terminal `?`/`!`/`.`. Does NOT
      collapse semantically-distinct rephrasings.
  (b) Persistent symbol-resolution cache wrapping `_likely_files_from_search`
      so the second compile of the same canonical query skips the subprocess.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from roam.plan import compiler as M
from roam.plan.compiler import (
    _cache_key,
    _canonicalize_task,
    _likely_files_from_search,
    _plan_persist_key,
    _symbol_resolution_cache_lookup,
    _symbol_resolution_cache_store,
    clear_plan_cache,
    compile_plan,
)

# ---- (a) canonicalizer is conservative ----


def test_w575_canonicalize_lowercase_and_whitespace():
    assert _canonicalize_task("Who calls foo") == "who calls foo"
    assert _canonicalize_task("  who calls   foo  ") == "who calls foo"
    assert _canonicalize_task("WHO\tcalls\nfoo") == "who calls foo"


def test_w575_canonicalize_smart_quotes():
    # Smart quotes → straight; both single and double.
    assert _canonicalize_task("what does “foo” do") == 'what does "foo" do'
    assert _canonicalize_task("show ‘foo’") == "show 'foo'"


def test_w575_canonicalize_strips_terminal_punctuation():
    assert _canonicalize_task("who calls foo?") == "who calls foo"
    assert _canonicalize_task("who calls foo!") == "who calls foo"
    assert _canonicalize_task("who calls foo.") == "who calls foo"
    # Multiple trailing — strip them all.
    assert _canonicalize_task("who calls foo???") == "who calls foo"


def test_w575_canonicalize_preserves_backticks():
    # Backticks are content (probe regexes anchor on them); MUST NOT be stripped.
    assert _canonicalize_task("who calls `foo`") == "who calls `foo`"


def test_w575_canonicalize_does_not_collapse_semantically_distinct():
    # SAFETY: rephrasings with different intent must NOT canonicalize identically.
    assert _canonicalize_task("who calls foo") != _canonicalize_task("what does foo do")
    assert _canonicalize_task("what calls foo") != _canonicalize_task("what does foo do")


def test_w575_canonicalize_empty():
    assert _canonicalize_task("") == ""
    assert _canonicalize_task("   ") == ""


# ---- (b) in-process plan cache hits across canonical variants ----


def test_w575_plan_cache_key_collapses_case_and_whitespace(monkeypatch):
    monkeypatch.setattr(M, "_memoized_head", lambda cwd: "deadbeef")
    a = _cache_key("Who calls foo", cwd="/tmp/r")
    b = _cache_key("  who  calls foo  ", cwd="/tmp/r")
    c = _cache_key("who calls foo?", cwd="/tmp/r")
    assert a == b == c


def test_w575_persist_key_collapses_case_and_whitespace():
    a = _plan_persist_key("Who calls foo", cwd="/tmp/r", repo_head="deadbeef")
    b = _plan_persist_key("  who  calls foo  ", cwd="/tmp/r", repo_head="deadbeef")
    c = _plan_persist_key("who calls foo?", cwd="/tmp/r", repo_head="deadbeef")
    assert a == b == c


def test_w575_persist_key_does_not_collapse_distinct(monkeypatch):
    # Sanity: semantically-different tasks → distinct keys.
    a = _plan_persist_key("who calls foo", cwd="/tmp/r", repo_head="d")
    b = _plan_persist_key("what does foo do", cwd="/tmp/r", repo_head="d")
    assert a != b


# ---- (c) symbol-resolution cache ----


@pytest.fixture
def tmp_repo(tmp_path, monkeypatch):
    """Initialize a tiny git repo so `.roam/` exists and HEAD is resolvable."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "f.py").write_text("x=1\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
    (repo / ".roam").mkdir()
    # Clear per-cwd HEAD memo so the fresh HEAD is picked up.
    M._HEAD_BY_CWD.clear()
    clear_plan_cache()
    return str(repo)


def test_w575_symcache_roundtrip(tmp_repo):
    files = ["src/a.py", "src/b.py"]
    _symbol_resolution_cache_store("who calls foo", tmp_repo, files)
    got = _symbol_resolution_cache_lookup("who calls foo", tmp_repo)
    assert got is not None
    assert got == (files, False)


def test_w575_symcache_canonical_variants_share_row(tmp_repo):
    files = ["src/a.py"]
    _symbol_resolution_cache_store("Who Calls Foo?", tmp_repo, files)
    # Different casing + trailing punctuation → same canonical key → hit.
    got = _symbol_resolution_cache_lookup("who calls foo", tmp_repo)
    assert got == (files, False)


def test_w575_symcache_negative_result_cached(tmp_repo):
    _symbol_resolution_cache_store("nonexistent task", tmp_repo, [])
    got = _symbol_resolution_cache_lookup("nonexistent task", tmp_repo)
    assert got == ([], False)


def test_w575_symcache_invalidates_on_head_change(tmp_repo):
    _symbol_resolution_cache_store("who calls foo", tmp_repo, ["a.py"])
    # New commit → new HEAD → cache row should not match.
    (Path(tmp_repo) / "g.py").write_text("y=2\n")
    subprocess.run(["git", "add", "."], cwd=tmp_repo, check=True)
    subprocess.run(["git", "commit", "-qm", "two"], cwd=tmp_repo, check=True)
    M._HEAD_BY_CWD.clear()
    got = _symbol_resolution_cache_lookup("who calls foo", tmp_repo)
    assert got is None


def test_w575_likely_files_uses_symcache(monkeypatch, tmp_repo):
    """`_likely_files_from_search` should consult the cache BEFORE running
    the `roam search-semantic` subprocess. After one hot run, a second
    call with a canonicalize-equivalent task must NOT invoke the
    subprocess."""
    M._RUN_ROAM_CACHE.clear()
    calls: list[tuple] = []

    def _fake_run_roam(args, cwd=None, timeout=8.0, detail=False):
        calls.append(tuple(args))
        return {"results": [{"file_path": "src/a.py"}, {"file_path": "src/b.py"}]}

    monkeypatch.setattr(M, "_run_roam", _fake_run_roam)
    # First call: no explicit paths, no cache → subprocess fires.
    files1, invoked1 = _likely_files_from_search("who calls foo", cwd=tmp_repo)
    assert invoked1 is True
    assert files1 == ["src/a.py", "src/b.py"]
    assert any(c[0] == "search-semantic" for c in calls)

    # Second call (rephrased to canonical-equivalent) — cache hits.
    calls.clear()
    files2, invoked2 = _likely_files_from_search("Who Calls Foo?", cwd=tmp_repo)
    assert invoked2 is False, "search-semantic should not be invoked on cache hit"
    assert files2 == ["src/a.py", "src/b.py"]
    assert not any(c[0] == "search-semantic" for c in calls)


def test_w575_likely_files_explicit_path_still_skips_cache(monkeypatch, tmp_repo):
    """Explicit-path fast path remains untouched — no cache write, no
    subprocess. (Cache is only consulted when no explicit paths.)"""
    M._RUN_ROAM_CACHE.clear()
    calls: list[tuple] = []
    monkeypatch.setattr(M, "_run_roam", lambda *a, **kw: (calls.append(a), {})[1])
    files, invoked = _likely_files_from_search("what does src/foo.py do", cwd=tmp_repo)
    assert invoked is False
    assert "src/foo.py" in files
    # No cache row written.
    assert _symbol_resolution_cache_lookup("what does src/foo.py do", tmp_repo) is None


@pytest.mark.parametrize(
    ("procedure", "task"),
    [
        ("session_meta", "ultrathink: continue"),
        ("self_contained_task", "You are validating the payload. Output JSON only."),
        ("top_n_ranking", "top 5 most-imported files"),
        ("symbol_defined_where", "where is compile_plan defined"),
    ],
)
def test_w575_task_text_no_repo_procedures_skip_semantic_fallback(monkeypatch, tmp_repo, procedure, task):
    """Classifier-owned task-text procedures already own the answer probe.

    With no explicit path present, they must not consult the symbol-resolution
    cache or fall through to ``roam search-semantic`` just to invent likely
    files.
    """
    M._RUN_ROAM_CACHE.clear()

    def fail_cache_lookup(*args, **kwargs):
        raise AssertionError("symbol-resolution cache should be skipped")

    def fail_run_roam(*args, **kwargs):
        raise AssertionError("search-semantic fallback should be skipped")

    monkeypatch.setattr(M, "_symbol_resolution_cache_lookup", fail_cache_lookup)
    monkeypatch.setattr(M, "_run_roam", fail_run_roam)

    files, invoked = _likely_files_from_search(task, cwd=tmp_repo, procedure=procedure)

    assert files == []
    assert invoked is False


def test_w575_task_text_no_repo_procedures_still_honor_explicit_paths(monkeypatch, tmp_repo):
    """The no-repo skip happens after explicit path extraction."""
    M._RUN_ROAM_CACHE.clear()

    def fail_cache_lookup(*args, **kwargs):
        raise AssertionError("symbol-resolution cache should be skipped")

    def fail_run_roam(*args, **kwargs):
        raise AssertionError("search-semantic fallback should be skipped")

    monkeypatch.setattr(M, "_symbol_resolution_cache_lookup", fail_cache_lookup)
    monkeypatch.setattr(M, "_run_roam", fail_run_roam)

    files, invoked = _likely_files_from_search(
        "where is compile_plan defined in src/roam/plan/compiler.py",
        cwd=tmp_repo,
        procedure="symbol_defined_where",
    )

    assert files == ["src/roam/plan/compiler.py"]
    assert invoked is False


def test_w575_compile_plan_symbol_defined_where_skips_semantic_after_classify(monkeypatch, tmp_repo):
    """``compile_plan`` passes the classifier winner into likely-file search."""
    M._RUN_ROAM_CACHE.clear()
    clear_plan_cache()

    monkeypatch.setattr(M, "_plan_cache_lookup", lambda *args, **kwargs: None)
    monkeypatch.setattr(M, "_plan_cache_store", lambda *args, **kwargs: None)

    def fail_cache_lookup(*args, **kwargs):
        raise AssertionError("symbol-resolution cache should be skipped")

    def fail_run_roam(*args, **kwargs):
        raise AssertionError("search-semantic fallback should be skipped")

    monkeypatch.setattr(M, "_symbol_resolution_cache_lookup", fail_cache_lookup)
    monkeypatch.setattr(M, "_run_roam", fail_run_roam)

    plan = compile_plan("where is compile_plan defined", cwd=tmp_repo)

    assert plan.procedure == "symbol_defined_where"
    assert plan.likely_files == []


# ---- (d) compile_plan integration ----


def test_w575_compile_plan_inprocess_hit_across_canonical_variants(monkeypatch, tmp_repo):
    """compile_plan's in-process _PLAN_CACHE should return the same PlanV0
    for "Who calls foo" and "who calls foo?" — same canonical key."""
    M._RUN_ROAM_CACHE.clear()
    clear_plan_cache()

    # Stub the search subprocess so the first compile is fast and deterministic.
    monkeypatch.setattr(M, "_run_roam", lambda *a, **k: {"results": []})

    p1 = compile_plan("Who calls bar", cwd=tmp_repo)
    p2 = compile_plan("  who  calls bar?  ", cwd=tmp_repo)
    # Same PlanV0 instance returned (cache hit), so procedure/likely_files/etc.
    # are byte-identical.
    assert p1 is p2 or (
        p1.procedure == p2.procedure and p1.likely_files == p2.likely_files and p1.repo_head == p2.repo_head
    )
