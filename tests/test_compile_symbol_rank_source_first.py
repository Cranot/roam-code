"""Source-first / exact-match-first ranking of `roam search` rows.

`roam search` substring-matches AND interleaves tests with source, so
`roam search _foo` can return a test (`test_x_foo`, substring) or a `tests/`
hit ABOVE the canonical `_foo` in `src/`. The compile envelope embeds the
top-5 as `symbol_definitions`; if a test ranks first, the agent reading
`symbol_definitions[0]` describes the wrong symbol.

These pin `_rank_symbol_search_rows` + `_build_symbol_definition_hits`:
exact name before substring, source path before test path, stable otherwise.
"""

from __future__ import annotations

from roam.plan.compiler import (
    _build_symbol_definition_hits,
    _rank_symbol_search_rows,
)

# Mirrors the live `roam search _canonicalize_task` order that exposed the bug:
# the substring test match came back BEFORE the exact source definition.
_RAW = [
    {
        "name": "test_w144_canonicalize_task_lru_cached",
        "location": "tests/test_compile_w142_w146.py:33",
        "kind": "function",
    },
    {
        "name": "_canonicalize_task",
        "location": "src/roam/plan/compiler.py:7530",
        "kind": "function",
    },
]


def test_exact_source_match_ranks_above_substring_test_match() -> None:
    ranked = _rank_symbol_search_rows(_RAW, "_canonicalize_task")
    assert ranked[0]["name"] == "_canonicalize_task"
    assert ranked[0]["location"].startswith("src/")
    assert ranked[1]["name"].startswith("test_")


def test_build_hits_leads_with_source_definition() -> None:
    hits = _build_symbol_definition_hits(_RAW, "_canonicalize_task")
    assert hits, "expected non-empty symbol_definitions"
    assert hits[0]["file"] == "src/roam/plan/compiler.py"
    assert hits[0]["line"] == 7530


def test_build_hits_strips_enrichment_for_forbidden_definition_path() -> None:
    raw = [
        {
            "name": "_secret",
            "location": "internal/private.py:7",
            "kind": "function",
            "references": ["src/public.py:9"],
            "body_preview": "def _secret():\n    return TOKEN",
        }
    ]
    hit = _build_symbol_definition_hits(raw, "_secret")[0]
    assert hit["file"] == "internal/private.py"
    assert "references" not in hit
    assert "body_preview" not in hit


def test_build_hits_strips_enrichment_for_unnormalizable_definition_path() -> None:
    raw = [
        {
            "name": "_secret",
            "location": "../private.py:7",
            "kind": "function",
            "references": ["src/public.py:9"],
            "body_preview": "def _secret():\n    return TOKEN",
        }
    ]
    hit = _build_symbol_definition_hits(raw, "_secret")[0]
    assert hit["file"] == "../private.py"
    assert "references" not in hit
    assert "body_preview" not in hit


def test_build_hits_strips_enrichment_when_location_keeps_line_suffix() -> None:
    raw = [
        {
            "name": "package",
            "location": "package.json:1",
            "line": 1,
            "kind": "file",
            "references": ["src/public.py:9"],
            "body_preview": '{"scripts": {"postinstall": "TOKEN"}}',
        }
    ]
    hit = _build_symbol_definition_hits(raw, "package")[0]
    assert hit["file"] == "package.json:1"
    assert "references" not in hit
    assert "body_preview" not in hit


def test_source_ranked_above_test_even_when_both_exact() -> None:
    """Two exact-name matches (e.g. a test helper sharing the real name):
    the source path still wins the tie over the test path."""
    raw = [
        {"name": "_run_roam", "location": "tests/test_basic.py:35", "kind": "function"},
        {"name": "_run_roam", "location": "src/roam/plan/compiler.py:1638", "kind": "function"},
    ]
    ranked = _rank_symbol_search_rows(raw, "_run_roam")
    assert ranked[0]["location"].startswith("src/")


def test_stable_order_preserved_among_equal_source_matches() -> None:
    """Multiple source defs of the same exact name keep `roam search`'s own
    relevance order (stable sort) — we only float source/exact up, never
    reshuffle equally-ranked rows."""
    raw = [
        {"name": "_run_roam", "location": "src/roam/mcp_server.py:4851"},
        {"name": "_run_roam", "location": "src/roam/plan/compiler.py:1638"},
    ]
    ranked = _rank_symbol_search_rows(raw, "_run_roam")
    assert [r["location"] for r in ranked] == [
        "src/roam/mcp_server.py:4851",
        "src/roam/plan/compiler.py:1638",
    ]


def test_exact_test_query_still_leads_when_user_names_the_test() -> None:
    """If the user names a `test_` symbol exactly, the exact match beats the
    source-preference — exactness is the primary key, source/test the tiebreak."""
    raw = [
        {"name": "helper_foo", "location": "src/roam/x.py:10"},
        {"name": "test_foo", "location": "tests/test_x.py:5"},
    ]
    ranked = _rank_symbol_search_rows(raw, "test_foo")
    assert ranked[0]["name"] == "test_foo"


def test_non_dict_rows_filtered() -> None:
    raw = ["junk", None, {"name": "_canonicalize_task", "location": "src/roam/plan/compiler.py:7530"}]
    ranked = _rank_symbol_search_rows(raw, "_canonicalize_task")
    assert len(ranked) == 1
    assert ranked[0]["name"] == "_canonicalize_task"
