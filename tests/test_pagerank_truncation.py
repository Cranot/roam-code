"""Tests for PageRank-weighted truncation in budget_truncate_json().

Backlog item #91: when token budget is hit, drop lowest-importance nodes
first instead of naively keeping the first N items.
"""

from __future__ import annotations

import json

import pytest


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _make_envelope(items_key="items", items=None, summary=None):
    """Build a minimal JSON envelope dict for testing."""
    return {
        "command": "test",
        "summary": summary or {"verdict": "ok"},
        items_key: items or [],
    }


# ---------------------------------------------------------------------------
# _sort_by_importance unit tests
# ---------------------------------------------------------------------------


class TestSortByImportance:
    """Tests for the _sort_by_importance helper."""

    def test_empty_list(self):
        from roam.output.formatter import _sort_by_importance

        result, was_sorted = _sort_by_importance([])
        assert result == []
        assert was_sorted is False

    def test_non_dict_items(self):
        """Lists of non-dict items are not sorted."""
        from roam.output.formatter import _sort_by_importance

        items = [3, 1, 2]
        result, was_sorted = _sort_by_importance(items)
        assert result == [3, 1, 2]
        assert was_sorted is False

    def test_pagerank_key(self):
        """Items with 'pagerank' key are sorted descending."""
        from roam.output.formatter import _sort_by_importance

        items = [
            {"name": "low", "pagerank": 0.01},
            {"name": "high", "pagerank": 0.99},
            {"name": "mid", "pagerank": 0.50},
        ]
        result, was_sorted = _sort_by_importance(items)
        assert was_sorted is True
        assert [d["name"] for d in result] == ["high", "mid", "low"]

    def test_importance_key(self):
        """Items with 'importance' key are sorted descending."""
        from roam.output.formatter import _sort_by_importance

        items = [
            {"name": "c", "importance": 1},
            {"name": "a", "importance": 10},
            {"name": "b", "importance": 5},
        ]
        result, was_sorted = _sort_by_importance(items)
        assert was_sorted is True
        assert [d["name"] for d in result] == ["a", "b", "c"]

    def test_score_key(self):
        """Items with 'score' key are sorted descending."""
        from roam.output.formatter import _sort_by_importance

        items = [
            {"name": "z", "score": 100},
            {"name": "a", "score": 200},
        ]
        result, was_sorted = _sort_by_importance(items)
        assert was_sorted is True
        assert result[0]["name"] == "a"

    def test_rank_key(self):
        """Items with 'rank' key are sorted descending (higher rank = more important)."""
        from roam.output.formatter import _sort_by_importance

        items = [
            {"name": "low", "rank": 1},
            {"name": "high", "rank": 10},
        ]
        result, was_sorted = _sort_by_importance(items)
        assert was_sorted is True
        assert result[0]["name"] == "high"

    def test_no_importance_key_fallback(self):
        """Items without any importance key are not sorted."""
        from roam.output.formatter import _sort_by_importance

        items = [
            {"name": "first", "value": 1},
            {"name": "second", "value": 2},
        ]
        result, was_sorted = _sort_by_importance(items)
        assert was_sorted is False
        assert [d["name"] for d in result] == ["first", "second"]

    def test_priority_pagerank_over_score(self):
        """'pagerank' takes priority over 'score' when both are present."""
        from roam.output.formatter import _sort_by_importance

        items = [
            {"name": "a", "pagerank": 0.1, "score": 100},
            {"name": "b", "pagerank": 0.9, "score": 1},
        ]
        result, was_sorted = _sort_by_importance(items)
        assert was_sorted is True
        # Sorted by pagerank (priority), not score
        assert result[0]["name"] == "b"

    def test_missing_key_in_some_items(self):
        """Items missing the importance key get default 0."""
        from roam.output.formatter import _sort_by_importance

        items = [
            {"name": "has", "pagerank": 0.5},
            {"name": "missing"},
            {"name": "also_has", "pagerank": 0.9},
        ]
        result, was_sorted = _sort_by_importance(items)
        assert was_sorted is True
        assert result[0]["name"] == "also_has"
        assert result[1]["name"] == "has"
        assert result[2]["name"] == "missing"


# ---------------------------------------------------------------------------
# budget_truncate_json importance-aware tests
# ---------------------------------------------------------------------------


class TestBudgetTruncateJsonImportance:
    """Test that budget_truncate_json sorts by importance before truncating."""

    def test_highest_importance_items_kept(self):
        """When truncating, highest-importance items survive."""
        from roam.output.formatter import budget_truncate_json

        items = [
            {"name": f"item_{i}", "pagerank": i * 0.01, "data": "x" * 50}
            for i in range(50)
        ]
        # item_49 has highest pagerank (0.49), item_0 has lowest (0.00)
        data = _make_envelope(items=items)

        # Tight budget forces truncation
        result = budget_truncate_json(data, 100)

        assert "items" in result
        kept = result["items"]
        assert len(kept) < 50

        # The kept items should be the highest-pagerank ones
        kept_names = {d["name"] for d in kept}
        # item_49 (highest) must be kept
        assert "item_49" in kept_names
        # item_0 (lowest) should be dropped
        assert "item_0" not in kept_names

    def test_metadata_omitted_low_importance_nodes(self):
        """Truncation metadata includes omitted_low_importance_nodes count."""
        from roam.output.formatter import budget_truncate_json

        items = [
            {"name": f"item_{i}", "pagerank": i * 0.01, "data": "x" * 50}
            for i in range(50)
        ]
        data = _make_envelope(items=items)

        result = budget_truncate_json(data, 100)

        summary = result["summary"]
        assert summary["truncated"] is True
        assert "omitted_low_importance_nodes" in summary
        assert summary["omitted_low_importance_nodes"] > 0
        # omitted = original_len - kept_len
        kept_len = len(result.get("items", []))
        assert summary["omitted_low_importance_nodes"] == 50 - kept_len

    def test_metadata_kept_highest_importance(self):
        """When importance sorting is applied, metadata flags it."""
        from roam.output.formatter import budget_truncate_json

        items = [
            {"name": f"item_{i}", "importance": i, "data": "x" * 50}
            for i in range(50)
        ]
        data = _make_envelope(items=items)

        result = budget_truncate_json(data, 100)

        assert result["summary"]["kept_highest_importance"] is True

    def test_no_importance_key_falls_back_to_positional(self):
        """Items without importance key are truncated positionally."""
        from roam.output.formatter import budget_truncate_json

        items = [
            {"name": f"item_{i}", "data": "x" * 50}
            for i in range(50)
        ]
        data = _make_envelope(items=items)

        result = budget_truncate_json(data, 100)

        assert "items" in result
        kept = result["items"]
        assert len(kept) < 50
        # Should keep the first items (positional fallback)
        assert kept[0]["name"] == "item_0"
        # No kept_highest_importance flag
        assert "kept_highest_importance" not in result["summary"]

    def test_budget_zero_returns_unchanged(self):
        """budget=0 returns data unchanged (no sorting or truncation)."""
        from roam.output.formatter import budget_truncate_json

        items = [
            {"name": f"item_{i}", "pagerank": i * 0.01}
            for i in range(10)
        ]
        data = _make_envelope(items=items)

        result = budget_truncate_json(data, 0)
        assert result is data
        assert result["items"][0]["name"] == "item_0"  # original order

    def test_within_budget_returns_unchanged(self):
        """Data that fits within budget is returned unchanged (no sorting)."""
        from roam.output.formatter import budget_truncate_json

        items = [
            {"name": f"item_{i}", "pagerank": i * 0.01}
            for i in range(3)
        ]
        data = _make_envelope(items=items)

        result = budget_truncate_json(data, 100000)
        assert result is data

    def test_multiple_list_fields_sorted_independently(self):
        """Each list field is sorted by its own importance key."""
        from roam.output.formatter import budget_truncate_json

        data = {
            "command": "test",
            "summary": {"verdict": "ok"},
            "symbols": [
                {"name": f"low_sym_{i}", "pagerank": 0.001 * i, "data": "x" * 80}
                for i in range(30)
            ] + [
                {"name": "high_sym", "pagerank": 0.99, "data": "x" * 80},
            ],
            "files": [
                {"path": f"unimportant_{i}.py", "score": i, "data": "y" * 80}
                for i in range(30)
            ] + [
                {"path": "critical.py", "score": 100, "data": "y" * 80},
            ],
        }

        result = budget_truncate_json(data, 200)

        # If symbols survived, high_sym should be first (highest pagerank)
        if "symbols" in result and result["symbols"]:
            assert result["symbols"][0]["name"] == "high_sym"
        # If files survived, critical.py should be first (highest score)
        if "files" in result and result["files"]:
            assert result["files"][0]["path"] == "critical.py"

    def test_does_not_mutate_original(self):
        """Original data is not mutated by importance sorting."""
        from roam.output.formatter import budget_truncate_json

        items = [
            {"name": f"item_{i}", "pagerank": (50 - i) * 0.01, "data": "x" * 50}
            for i in range(50)
        ]
        data = _make_envelope(items=items)
        # Save original first item name
        original_first = data["items"][0]["name"]

        budget_truncate_json(data, 100)

        # Original should be unchanged
        assert data["items"][0]["name"] == original_first
        assert len(data["items"]) == 50
        assert "truncated" not in data["summary"]

    def test_score_key_items_sorted(self):
        """Items with 'score' key are sorted by score descending."""
        from roam.output.formatter import budget_truncate_json

        items = [
            {"name": f"item_{i}", "score": i, "data": "x" * 50}
            for i in range(50)
        ]
        data = _make_envelope(items=items)

        result = budget_truncate_json(data, 100)

        kept = result["items"]
        # Highest score items should be kept
        assert kept[0]["name"] == "item_49"

    def test_rank_key_items_sorted(self):
        """Items with 'rank' key are sorted by rank descending."""
        from roam.output.formatter import budget_truncate_json

        items = [
            {"name": f"item_{i}", "rank": i, "data": "x" * 50}
            for i in range(50)
        ]
        data = _make_envelope(items=items)

        result = budget_truncate_json(data, 100)

        kept = result["items"]
        assert kept[0]["name"] == "item_49"

    def test_importance_key_items_sorted(self):
        """Items with 'importance' key are sorted by importance descending."""
        from roam.output.formatter import budget_truncate_json

        items = [
            {"name": f"item_{i}", "importance": i * 0.001, "data": "x" * 50}
            for i in range(50)
        ]
        data = _make_envelope(items=items)

        result = budget_truncate_json(data, 100)

        kept = result["items"]
        assert kept[0]["name"] == "item_49"

    def test_summary_budget_tokens_correct(self):
        """Summary budget_tokens matches the provided budget."""
        from roam.output.formatter import budget_truncate_json

        items = [
            {"name": f"item_{i}", "pagerank": i * 0.01, "data": "x" * 50}
            for i in range(50)
        ]
        data = _make_envelope(items=items)

        result = budget_truncate_json(data, 200)

        assert result["summary"]["budget_tokens"] == 200

    def test_full_output_tokens_in_summary(self):
        """Summary includes full_output_tokens for agent awareness."""
        from roam.output.formatter import budget_truncate_json

        items = [
            {"name": f"item_{i}", "pagerank": i * 0.01, "data": "x" * 50}
            for i in range(50)
        ]
        data = _make_envelope(items=items)

        result = budget_truncate_json(data, 100)

        assert "full_output_tokens" in result["summary"]
        assert result["summary"]["full_output_tokens"] > 0


# ---------------------------------------------------------------------------
# json_envelope integration
# ---------------------------------------------------------------------------


class TestJsonEnvelopeImportanceTruncation:
    """Test that json_envelope with budget applies importance-aware truncation."""

    def test_envelope_importance_sorting(self):
        """json_envelope with budget sorts by importance before truncating."""
        from roam.output.formatter import json_envelope

        items = [
            {"name": f"item_{i}", "pagerank": i * 0.01, "data": "x" * 50}
            for i in range(50)
        ]

        result = json_envelope(
            "test",
            summary={"verdict": "ok"},
            budget=150,
            items=items,
        )

        # If items survived truncation, highest-pagerank should be first
        if "items" in result and result["items"]:
            assert result["items"][0]["pagerank"] >= result["items"][-1]["pagerank"]

    def test_envelope_no_importance_positional(self):
        """json_envelope with budget falls back to positional for items without importance."""
        from roam.output.formatter import json_envelope

        items = [
            {"name": f"item_{i}", "data": "x" * 50}
            for i in range(50)
        ]

        result = json_envelope(
            "test",
            summary={"verdict": "ok"},
            budget=150,
            items=items,
        )

        if "items" in result and result["items"]:
            # Positional: first items kept
            assert result["items"][0]["name"] == "item_0"


# ---------------------------------------------------------------------------
# _IMPORTANCE_KEYS constant
# ---------------------------------------------------------------------------


class TestImportanceKeys:
    """Verify the priority order of importance keys."""

    def test_keys_defined(self):
        """_IMPORTANCE_KEYS is a tuple with expected entries."""
        from roam.output.formatter import _IMPORTANCE_KEYS

        assert isinstance(_IMPORTANCE_KEYS, tuple)
        assert "pagerank" in _IMPORTANCE_KEYS
        assert "importance" in _IMPORTANCE_KEYS
        assert "score" in _IMPORTANCE_KEYS
        assert "rank" in _IMPORTANCE_KEYS

    def test_pagerank_is_first_priority(self):
        """pagerank is the first (highest priority) key."""
        from roam.output.formatter import _IMPORTANCE_KEYS

        assert _IMPORTANCE_KEYS[0] == "pagerank"
