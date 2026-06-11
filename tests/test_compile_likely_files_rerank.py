"""Freeform likely-files rerank — graph/math signals over flat text scores.

search-semantic text scores on conceptual tasks are nearly flat (~0.03
spread observed live), so ordering by them alone is noise: a comprehension
task naming "the compiler and verify" surfaced six unrelated test files as
named_paths. The rerank blends four offline signals — percentile-ranked
text score (band scaled by observed spread), path-token match (mini-IDF
filtered), file role, and summed symbol PageRank from graph_metrics — and
``_path_token_recall`` widens the pool with source files whose basename
matches a task token. Pure local SQLite math: no subprocess, no model call.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, index_in_process  # noqa: E402

from roam.plan.compiler import (  # noqa: E402
    _likely_files_from_search,
    _path_token_recall,
    _rerank_likely_files,
    _task_path_tokens,
)

SOURCE_BODY = "def resolve_backoff(n):\n    return n * 2\n\n\ndef sync_cursor(c):\n    return c + 1\n"
TEST_BODY = "def test_resolve_backoff():\n    assert True\n"


def _repo(tmp_path: Path) -> Path:
    proj = tmp_path / "rerank_repo"
    (proj / "src").mkdir(parents=True)
    (proj / "tests").mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "src" / "scheduler.py").write_text(SOURCE_BODY)
    (proj / "src" / "billing.py").write_text(SOURCE_BODY.replace("resolve_backoff", "compute_total"))
    (proj / "tests" / "test_scheduler.py").write_text(TEST_BODY)
    git_init(proj)
    index_in_process(proj)
    return proj


def test_role_demotes_tests_on_flat_scores(tmp_path, monkeypatch):
    proj = _repo(tmp_path)
    # Flat text scores: ordering must come from role (source > test).
    scored = [("tests/test_scheduler.py", 0.30), ("src/billing.py", 0.29)]
    ranked = _rerank_likely_files("how does billing work", scored, str(proj))
    assert ranked[0] == "src/billing.py", ranked


def test_basename_token_beats_flat_text(tmp_path, monkeypatch):
    proj = _repo(tmp_path)
    # The task names "scheduler"; a slightly-higher flat text score on an
    # unrelated source file must not outrank the named module.
    scored = [("src/billing.py", 0.31), ("src/scheduler.py", 0.29)]
    ranked = _rerank_likely_files("explain the scheduler retry policy", scored, str(proj))
    assert ranked[0] == "src/scheduler.py", ranked


def test_strong_text_hit_stays_on_top(tmp_path, monkeypatch):
    proj = _repo(tmp_path)
    # A real symbol hit (wide spread) keeps its raw dominance even when the
    # other candidate carries a path-token match.
    scored = [("src/billing.py", 0.85), ("src/scheduler.py", 0.30)]
    ranked = _rerank_likely_files("where is compute_total in the scheduler", scored, str(proj))
    assert ranked[0] == "src/billing.py", ranked


def test_token_recall_pulls_named_module(tmp_path, monkeypatch):
    proj = _repo(tmp_path)
    out = _path_token_recall("why does the scheduler double the delay", str(proj), known=set())
    assert any(p.endswith("src/scheduler.py") or p == "src/scheduler.py" for p, _ in out), out
    # Test-role files are never recalled.
    assert not any("test_scheduler" in p for p, _ in out), out


def test_universal_token_carries_no_boost():
    # Mini-IDF: "src" appears in every candidate → discriminates nothing.
    scored = [("src/a.py", 0.30), ("src/b.py", 0.30), ("src/c.py", 0.30)]
    ranked = _rerank_likely_files("look at src stuff", scored, None)
    assert set(ranked) == {"src/a.py", "src/b.py", "src/c.py"}


def test_fail_open_without_index():
    # No cwd / no index.db: text order is preserved, nothing raises.
    scored = [("a.py", 0.9), ("b.py", 0.3)]
    assert _rerank_likely_files("anything", scored, None) == ["a.py", "b.py"]


def test_stop_tokens_excluded():
    tokens = _task_path_tokens("check if the compiler can improve this command")
    assert "compiler" in tokens
    assert "check" not in tokens and "command" not in tokens and "improve" not in tokens


def test_end_to_end_named_module_in_top(tmp_path, monkeypatch):
    proj = _repo(tmp_path)
    monkeypatch.chdir(proj)
    files, _ = _likely_files_from_search("explain how the scheduler backoff works", str(proj))
    assert any("scheduler.py" in f and "test" not in f for f in files), files
