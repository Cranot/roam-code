"""Tests for deterministic JSON output (backlog #90).

Verifies that roam's JSON serialization produces byte-identical output
across multiple invocations with the same input — critical for LLM
prompt-caching compatibility (exact prefix matching).
"""

from __future__ import annotations

import json

import pytest


# ============================================================================
# 1. to_json() uses sort_keys=True and is idempotent
# ============================================================================


class TestToJsonDeterministic:
    """Verify to_json() produces deterministic output."""

    def test_sort_keys_enabled(self):
        """to_json() must use sort_keys=True."""
        from roam.output.formatter import to_json

        data = {"zebra": 1, "alpha": 2, "middle": 3}
        result = to_json(data)
        parsed = json.loads(result)
        keys = list(parsed.keys())
        assert keys == sorted(keys), (
            f"Keys should be sorted but got: {keys}"
        )

    def test_idempotent_simple(self):
        """Calling to_json() multiple times with same input gives identical output."""
        from roam.output.formatter import to_json

        data = {"b": 2, "a": 1, "c": [3, 2, 1]}
        results = [to_json(data) for _ in range(10)]
        assert all(r == results[0] for r in results), (
            "to_json() should produce identical output across calls"
        )

    def test_idempotent_nested(self):
        """Nested dicts and lists produce deterministic output."""
        from roam.output.formatter import to_json

        data = {
            "z_outer": {"b_inner": 1, "a_inner": 2},
            "a_outer": [{"y": 1, "x": 2}],
        }
        results = [to_json(data) for _ in range(10)]
        assert all(r == results[0] for r in results)

    def test_nested_dict_keys_sorted(self):
        """Keys in nested dicts must also be sorted."""
        from roam.output.formatter import to_json

        data = {"outer": {"z": 1, "a": 2, "m": 3}}
        result = to_json(data)
        parsed = json.loads(result)
        inner_keys = list(parsed["outer"].keys())
        assert inner_keys == sorted(inner_keys), (
            f"Nested dict keys should be sorted: {inner_keys}"
        )

    def test_sort_keys_in_source_code(self):
        """Verify sort_keys=True is literally in the to_json source."""
        import inspect
        from roam.output.formatter import to_json

        source = inspect.getsource(to_json)
        assert "sort_keys=True" in source, (
            "to_json() must contain sort_keys=True in its source"
        )


# ============================================================================
# 2. json_envelope() deterministic structure
# ============================================================================


class TestJsonEnvelopeDeterministic:
    """Verify json_envelope() produces deterministic output."""

    def test_no_timestamp_in_main_body(self):
        """Timestamps must not appear as top-level keys in the envelope.

        They should be in the _meta sub-dict instead, so the main
        content keys remain stable across invocations.
        """
        from roam.output.formatter import json_envelope

        env = json_envelope("test-cmd", summary={"verdict": "ok"})
        assert "timestamp" not in env, (
            "timestamp should be in _meta, not top-level"
        )
        assert "index_age_s" not in env, (
            "index_age_s should be in _meta, not top-level"
        )

    def test_meta_contains_timestamp(self):
        """The _meta sub-dict must contain timestamp and index_age_s."""
        from roam.output.formatter import json_envelope

        env = json_envelope("test-cmd", summary={"verdict": "ok"})
        assert "_meta" in env, "Envelope must have _meta key"
        meta = env["_meta"]
        assert "timestamp" in meta, "_meta must contain timestamp"
        assert "index_age_s" in meta, "_meta must contain index_age_s"

    def test_required_fields_present(self):
        """Envelope must have all required fields."""
        from roam.output.formatter import json_envelope

        env = json_envelope("test-cmd", summary={"verdict": "ok"})
        for field in ("schema", "schema_version", "command", "version",
                      "project", "summary"):
            assert field in env, f"Missing required field: {field}"

    def test_content_stable_across_calls(self):
        """Content keys (excluding _meta) should be identical across calls.

        Even though _meta values (timestamps) change between calls,
        the non-meta content must be stable.
        """
        from roam.output.formatter import json_envelope

        env1 = json_envelope("test-cmd", summary={"verdict": "ok"},
                            items=[1, 2, 3])
        env2 = json_envelope("test-cmd", summary={"verdict": "ok"},
                            items=[1, 2, 3])

        # Remove _meta for comparison (timestamps differ)
        for env in (env1, env2):
            env.pop("_meta", None)

        # Serialize both — should be identical
        json1 = json.dumps(env1, sort_keys=True)
        json2 = json.dumps(env2, sort_keys=True)
        assert json1 == json2, (
            "Content (excluding _meta) should be identical across calls"
        )

    def test_sort_keys_in_serialized_envelope(self):
        """When serialized via to_json(), envelope keys are sorted."""
        from roam.output.formatter import json_envelope, to_json

        env = json_envelope("test-cmd", summary={"verdict": "ok"},
                            zebra=1, alpha=2)
        serialized = to_json(env)
        parsed = json.loads(serialized)
        keys = list(parsed.keys())
        assert keys == sorted(keys), (
            f"Serialized envelope keys should be sorted: {keys}"
        )


# ============================================================================
# 3. Schema registry updated
# ============================================================================


class TestSchemaRegistry:
    """Verify schema registry reflects the _meta change."""

    def test_timestamp_not_required(self):
        """timestamp should not be a required top-level field."""
        from roam.output.schema_registry import ENVELOPE_SCHEMA

        required = ENVELOPE_SCHEMA["required_fields"]
        assert "timestamp" not in required, (
            "timestamp moved to _meta, should not be in required_fields"
        )

    def test_meta_documented(self):
        """_meta should be documented in optional_fields."""
        from roam.output.schema_registry import ENVELOPE_SCHEMA

        optional = ENVELOPE_SCHEMA.get("optional_fields", {})
        assert "_meta" in optional, (
            "_meta should be documented in optional_fields"
        )


# ============================================================================
# 4. Graph builder determinism
# ============================================================================


class TestGraphBuilderDeterminism:
    """Verify graph builder SQL queries use ORDER BY."""

    def test_build_symbol_graph_has_order_by(self):
        """build_symbol_graph SQL queries should include ORDER BY."""
        import inspect
        from roam.graph.builder import build_symbol_graph

        source = inspect.getsource(build_symbol_graph)
        assert "ORDER BY" in source, (
            "build_symbol_graph should have ORDER BY in SQL queries"
        )

    def test_build_file_graph_has_order_by(self):
        """build_file_graph SQL queries should include ORDER BY."""
        import inspect
        from roam.graph.builder import build_file_graph

        source = inspect.getsource(build_file_graph)
        assert "ORDER BY" in source, (
            "build_file_graph should have ORDER BY in SQL queries"
        )


# ============================================================================
# 5. Propagation cost uses fixed seed
# ============================================================================


class TestPropagationCostDeterminism:
    """Verify sampled propagation cost is deterministic."""

    def test_sampled_uses_fixed_seed(self):
        """_propagation_cost_sampled should use a fixed random seed."""
        import inspect
        from roam.graph.cycles import _propagation_cost_sampled

        source = inspect.getsource(_propagation_cost_sampled)
        # Should use Random(42) or seed(42) for determinism
        assert "Random(42)" in source or "seed(42)" in source, (
            "_propagation_cost_sampled should use a fixed seed for determinism"
        )


# ============================================================================
# 6. Cycle detection sorting
# ============================================================================


class TestCycleDetectionDeterminism:
    """Verify cycle detection output is sorted."""

    def test_find_cycles_sorted(self):
        """find_cycles should return sorted SCCs."""
        import inspect
        from roam.graph.cycles import find_cycles

        source = inspect.getsource(find_cycles)
        assert "sorted" in source, (
            "find_cycles should sort SCCs for deterministic output"
        )


# ============================================================================
# 7. Layer violations sorted
# ============================================================================


class TestLayerViolationsDeterminism:
    """Verify layer violation output is sorted."""

    def test_find_violations_sorted(self):
        """find_violations should sort violations."""
        import inspect
        from roam.graph.layers import find_violations

        source = inspect.getsource(find_violations)
        assert "sort" in source, (
            "find_violations should sort output for determinism"
        )


# ============================================================================
# 8. compact_json_envelope also deterministic
# ============================================================================


class TestCompactEnvelopeDeterministic:
    """Verify compact_json_envelope is deterministic via to_json."""

    def test_compact_envelope_serializes_deterministically(self):
        """compact_json_envelope output should serialize deterministically."""
        from roam.output.formatter import compact_json_envelope, to_json

        env = compact_json_envelope("test", results=[1, 2], alpha="a", zebra="z")
        results = [to_json(env) for _ in range(10)]
        assert all(r == results[0] for r in results), (
            "Compact envelope should serialize identically across calls"
        )


# ============================================================================
# 9. End-to-end: json_envelope + to_json roundtrip
# ============================================================================


class TestEndToEndDeterminism:
    """Full pipeline: json_envelope -> to_json -> parse -> verify."""

    def test_full_roundtrip_sorted(self):
        """Full envelope -> JSON -> parse produces sorted keys at all levels."""
        from roam.output.formatter import json_envelope, to_json

        env = json_envelope(
            "test",
            summary={"verdict": "ok", "zebra": 1, "alpha": 2},
            results=[
                {"z_name": "foo", "a_kind": "fn"},
                {"z_name": "bar", "a_kind": "cls"},
            ],
        )
        serialized = to_json(env)
        parsed = json.loads(serialized)

        # Top-level keys sorted
        top_keys = list(parsed.keys())
        assert top_keys == sorted(top_keys), f"Top keys unsorted: {top_keys}"

        # Summary keys sorted
        summary_keys = list(parsed["summary"].keys())
        assert summary_keys == sorted(summary_keys), (
            f"Summary keys unsorted: {summary_keys}"
        )

        # _meta keys sorted
        if "_meta" in parsed:
            meta_keys = list(parsed["_meta"].keys())
            assert meta_keys == sorted(meta_keys), (
                f"_meta keys unsorted: {meta_keys}"
            )

        # List item dict keys sorted
        for item in parsed.get("results", []):
            item_keys = list(item.keys())
            assert item_keys == sorted(item_keys), (
                f"Result item keys unsorted: {item_keys}"
            )

    def test_no_top_level_timestamp_in_serialized(self):
        """Serialized JSON should not have timestamp at top level."""
        from roam.output.formatter import json_envelope, to_json

        env = json_envelope("test", summary={"verdict": "ok"})
        serialized = to_json(env)
        parsed = json.loads(serialized)

        # timestamp should only exist in _meta
        assert "timestamp" not in parsed, (
            "Serialized envelope should not have top-level timestamp"
        )
        assert "index_age_s" not in parsed, (
            "Serialized envelope should not have top-level index_age_s"
        )
        assert "timestamp" in parsed.get("_meta", {}), (
            "timestamp should be in _meta"
        )
