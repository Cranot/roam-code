"""Regression: top_n_ranking dimensions must return REAL names, never `rank_N`
placeholders.

The 2026-06-03 compiler-vs-vanilla tool-call A/B found "5 most-imported files"
emitting `rank_1`/`rank_2` placeholder names — the agent distrusted the envelope
and re-grepped 10× (a compile LOSS). Root cause: per-dimension `name_keys` in
`_probe_top_n_ranking_for_task`'s dispatch table had drifted from the live roam
result shapes. This test pins every dimension to either a real-named ranked list
or the honest `top_n_ranking_unavailable` remediation — never garbage.
"""

from __future__ import annotations

import os

import pytest

from roam.plan.compiler import compile_for_artifact, compile_plan

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_QUERIES = {
    "complexity": "top 5 most complex functions",
    "churn": "top 5 most churned files",
    "importance": "top 5 most important symbols",
    "callers": "top 5 most-called functions",
    "imports": "top 5 most-imported files",
    "coupling": "top 5 most coupled files",
}


@pytest.mark.skipif(
    not os.path.exists(os.path.join(_REPO, ".roam", "index.db")),
    reason="requires .roam/index.db in cwd",
)
@pytest.mark.parametrize("dim,query", list(_QUERIES.items()))
def test_top_n_dimension_returns_real_names_not_placeholders(dim, query):
    plan = compile_plan(query)
    env, _label = compile_for_artifact(plan, cwd=_REPO)
    pre = env["plan"].get("prefetched_facts", {})
    # Honest remediation when a shape can't be ranked is acceptable.
    if "top_n_ranking_unavailable" in pre:
        return
    items = (pre.get("top_n_ranking") or {}).get("items") or []
    assert items, f"{dim}: produced neither items nor a remediation"
    placeholders = [it for it in items if str(it.get("name", "")).startswith("rank_")]
    assert not placeholders, f"{dim}: placeholder names leaked: {placeholders}"
    assert all(it.get("name") for it in items), f"{dim}: empty item name(s)"
