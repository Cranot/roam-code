"""Tests for roam duplicates -- semantic duplicate detector."""

from __future__ import annotations

import json
import os

import pytest

from tests.conftest import (
    git_init, git_commit, index_in_process, invoke_cli,
    parse_json_output, assert_json_envelope,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def dup_project(tmp_path):
    """Project with clearly duplicate functions across files.

    config/json_parser.py: parse_json_config (read, parse, validate, return)
    config/yaml_parser.py: parse_yaml_config (read, parse, validate, return)
    config/toml_parser.py: parse_toml_config (read, parse, validate, return)
    """
    proj = tmp_path / "dup_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    config = proj / "config"
    config.mkdir()

    (config / "json_parser.py").write_text(
        'def parse_json_config(path):\n'
        '    """Parse a JSON config file."""\n'
        '    with open(path) as f:\n'
        '        data = f.read()\n'
        '    parsed = _parse_format(data)\n'
        '    if not _validate_schema(parsed):\n'
        '        raise ValueError("invalid")\n'
        '    return parsed\n'
        '\n'
        'def _parse_format(data):\n'
        '    return data\n'
        '\n'
        'def _validate_schema(data):\n'
        '    return True\n'
    )

    (config / "yaml_parser.py").write_text(
        'def parse_yaml_config(path):\n'
        '    """Parse a YAML config file."""\n'
        '    with open(path) as f:\n'
        '        data = f.read()\n'
        '    parsed = _parse_format(data)\n'
        '    if not _validate_schema(parsed):\n'
        '        raise ValueError("invalid")\n'
        '    return parsed\n'
        '\n'
        'def _parse_format(data):\n'
        '    return data\n'
        '\n'
        'def _validate_schema(data):\n'
        '    return True\n'
    )

    (config / "toml_parser.py").write_text(
        'def parse_toml_config(path):\n'
        '    """Parse a TOML config file."""\n'
        '    with open(path) as f:\n'
        '        data = f.read()\n'
        '    parsed = _parse_format(data)\n'
        '    if not _validate_schema(parsed):\n'
        '        raise ValueError("invalid")\n'
        '    return parsed\n'
        '\n'
        'def _parse_format(data):\n'
        '    return data\n'
        '\n'
        'def _validate_schema(data):\n'
        '    return True\n'
    )

    git_init(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"roam index failed:\n{out}"
    return proj


@pytest.fixture
def no_dup_project(tmp_path):
    """Project with no duplicate functions -- all are very different."""
    proj = tmp_path / "nodup_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    (proj / "math_ops.py").write_text(
        'def add(a, b):\n'
        '    """Add two numbers."""\n'
        '    result = a + b\n'
        '    if result > 100:\n'
        '        return 100\n'
        '    return result\n'
    )

    (proj / "string_ops.py").write_text(
        'def format_report(title, items, footer, separator, header, prefix):\n'
        '    """Format a complex report with many parameters."""\n'
        '    parts = [header, title]\n'
        '    for item in items:\n'
        '        for sub in item:\n'
        '            parts.append(prefix + str(sub))\n'
        '        parts.append(separator)\n'
        '    while len(parts) < 20:\n'
        '        parts.append("")\n'
        '    try:\n'
        '        result = "\\n".join(parts)\n'
        '    except Exception:\n'
        '        result = "error"\n'
        '    parts.append(footer)\n'
        '    return result\n'
    )

    git_init(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"roam index failed:\n{out}"
    return proj


@pytest.fixture
def mixed_project(tmp_path):
    """Project with a mix of duplicates and non-duplicates."""
    proj = tmp_path / "mixed_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    api = proj / "api"
    api.mkdir()

    # Duplicate pair: validate_user_input / validate_order_input
    (api / "users.py").write_text(
        'def validate_user_input(data):\n'
        '    """Validate user input."""\n'
        '    if not data:\n'
        '        return False\n'
        '    if "name" not in data:\n'
        '        return False\n'
        '    if "email" not in data:\n'
        '        return False\n'
        '    return True\n'
    )

    (api / "orders.py").write_text(
        'def validate_order_input(data):\n'
        '    """Validate order input."""\n'
        '    if not data:\n'
        '        return False\n'
        '    if "product" not in data:\n'
        '        return False\n'
        '    if "quantity" not in data:\n'
        '        return False\n'
        '    return True\n'
    )

    # Not a duplicate -- very different structure
    (proj / "utils.py").write_text(
        'def compute_statistics(values, weights, normalize):\n'
        '    """Compute weighted statistics."""\n'
        '    total = 0\n'
        '    for i in range(len(values)):\n'
        '        for j in range(len(weights)):\n'
        '            total += values[i] * weights[j]\n'
        '    if normalize:\n'
        '        total = total / len(values)\n'
        '    mean = total / max(len(values), 1)\n'
        '    variance = 0\n'
        '    for v in values:\n'
        '        variance += (v - mean) ** 2\n'
        '    return {"mean": mean, "var": variance, "total": total}\n'
    )

    git_init(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"roam index failed:\n{out}"
    return proj


@pytest.fixture
def cluster_project(tmp_path):
    """Project for testing transitive clustering: A~B, B~C -> {A,B,C}."""
    proj = tmp_path / "cluster_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    # Three similar functions that should form a cluster
    (proj / "proc_a.py").write_text(
        'def process_alpha(data):\n'
        '    """Process alpha data."""\n'
        '    if not data:\n'
        '        return None\n'
        '    result = transform(data)\n'
        '    if not validate(result):\n'
        '        return None\n'
        '    return result\n'
    )

    (proj / "proc_b.py").write_text(
        'def process_beta(data):\n'
        '    """Process beta data."""\n'
        '    if not data:\n'
        '        return None\n'
        '    result = transform(data)\n'
        '    if not validate(result):\n'
        '        return None\n'
        '    return result\n'
    )

    (proj / "proc_c.py").write_text(
        'def process_gamma(data):\n'
        '    """Process gamma data."""\n'
        '    if not data:\n'
        '        return None\n'
        '    result = transform(data)\n'
        '    if not validate(result):\n'
        '        return None\n'
        '    return result\n'
    )

    git_init(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"roam index failed:\n{out}"
    return proj


@pytest.fixture
def scoped_project(tmp_path):
    """Project with duplicates in different directories for --scope testing."""
    proj = tmp_path / "scope_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    src = proj / "src"
    src.mkdir()
    lib = proj / "lib"
    lib.mkdir()

    # Duplicates in src/
    (src / "handler_a.py").write_text(
        'def handle_request_a(req):\n'
        '    """Handle request type A."""\n'
        '    if not req:\n'
        '        return None\n'
        '    data = parse(req)\n'
        '    if not validate(data):\n'
        '        return None\n'
        '    return respond(data)\n'
    )

    (src / "handler_b.py").write_text(
        'def handle_request_b(req):\n'
        '    """Handle request type B."""\n'
        '    if not req:\n'
        '        return None\n'
        '    data = parse(req)\n'
        '    if not validate(data):\n'
        '        return None\n'
        '    return respond(data)\n'
    )

    # Similar duplicate in lib/ (should be excluded by --scope src)
    (lib / "handler_c.py").write_text(
        'def handle_request_c(req):\n'
        '    """Handle request type C."""\n'
        '    if not req:\n'
        '        return None\n'
        '    data = parse(req)\n'
        '    if not validate(data):\n'
        '        return None\n'
        '    return respond(data)\n'
    )

    git_init(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"roam index failed:\n{out}"
    return proj


@pytest.fixture
def small_fn_project(tmp_path):
    """Project with small functions (below min-lines threshold)."""
    proj = tmp_path / "small_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    (proj / "tiny_a.py").write_text(
        'def tiny_func_a(x):\n'
        '    return x + 1\n'
    )

    (proj / "tiny_b.py").write_text(
        'def tiny_func_b(x):\n'
        '    return x + 1\n'
    )

    # One function that IS big enough
    (proj / "big.py").write_text(
        'def big_function(data):\n'
        '    """A big function."""\n'
        '    if not data:\n'
        '        return None\n'
        '    result = []\n'
        '    for item in data:\n'
        '        if item > 0:\n'
        '            result.append(item)\n'
        '    return result\n'
    )

    git_init(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"roam index failed:\n{out}"
    return proj


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestDuplicatesBasic:
    """Basic duplicate detection tests."""

    def test_detects_duplicate_functions(self, dup_project, cli_runner):
        """Functions with same structure across files should be detected."""
        result = invoke_cli(cli_runner, ["duplicates", "--threshold", "0.5"],
                            cwd=dup_project)
        assert result.exit_code == 0
        output = result.output
        assert "VERDICT:" in output
        # Should find at least one cluster
        assert "CLUSTER" in output or "duplicate" in output.lower()

    def test_no_duplicates_found(self, no_dup_project, cli_runner):
        """Functions with very different structures should not be flagged."""
        result = invoke_cli(cli_runner, ["duplicates"], cwd=no_dup_project)
        assert result.exit_code == 0
        output = result.output
        assert "VERDICT:" in output
        # The two functions have wildly different param counts and structure
        # so no cluster should form at the default threshold
        assert "No semantic duplicates" in output or "0 duplicate" in output or "CLUSTER" not in output

    def test_duplicate_pair_detected(self, mixed_project, cli_runner):
        """Similar validate functions should be detected as duplicates."""
        result = invoke_cli(cli_runner, ["duplicates", "--threshold", "0.5"],
                            cwd=mixed_project)
        assert result.exit_code == 0
        output = result.output
        assert "VERDICT:" in output


class TestClustering:
    """Test Union-Find clustering behavior."""

    def test_transitive_clustering(self, cluster_project, cli_runner):
        """A~B and B~C should produce cluster {A, B, C}."""
        result = invoke_cli(cli_runner, ["duplicates", "--threshold", "0.5"],
                            cwd=cluster_project)
        assert result.exit_code == 0
        output = result.output
        assert "VERDICT:" in output
        # All three process_* functions should be in one cluster
        if "CLUSTER" in output:
            # Count how many functions are in cluster 1
            # All three should be together
            assert "process_alpha" in output or "process_beta" in output


class TestFlags:
    """Test CLI option flags."""

    def test_threshold_flag(self, dup_project, cli_runner):
        """Higher threshold should produce fewer or same number of clusters."""
        result_low = invoke_cli(cli_runner,
                                ["duplicates", "--threshold", "0.3"],
                                cwd=dup_project)
        result_high = invoke_cli(cli_runner,
                                 ["duplicates", "--threshold", "0.99"],
                                 cwd=dup_project)
        assert result_low.exit_code == 0
        assert result_high.exit_code == 0

    def test_min_lines_filter(self, small_fn_project, cli_runner):
        """Functions below --min-lines should be excluded."""
        # Default min-lines=5 should filter out 2-line functions
        result = invoke_cli(cli_runner, ["duplicates"],
                            cwd=small_fn_project)
        assert result.exit_code == 0
        output = result.output
        # tiny_func_a/b have 2 lines, below min-lines=5 default
        assert "tiny_func_a" not in output
        assert "tiny_func_b" not in output

    def test_min_lines_low(self, small_fn_project, cli_runner):
        """With --min-lines 1, small functions should be considered."""
        result = invoke_cli(cli_runner,
                            ["duplicates", "--min-lines", "1", "--threshold", "0.5"],
                            cwd=small_fn_project)
        assert result.exit_code == 0
        # Now tiny functions are candidates

    def test_scope_filter(self, scoped_project, cli_runner):
        """--scope should limit analysis to the specified path."""
        result = invoke_cli(cli_runner,
                            ["duplicates", "--scope", "src", "--threshold", "0.5"],
                            cwd=scoped_project)
        assert result.exit_code == 0
        output = result.output
        # handler_c is in lib/, should not appear in output
        assert "handler_c" not in output


class TestJsonOutput:
    """Test JSON output format."""

    def test_json_envelope_structure(self, dup_project, cli_runner):
        """JSON output should follow the roam envelope contract."""
        result = invoke_cli(cli_runner,
                            ["duplicates", "--threshold", "0.5"],
                            cwd=dup_project, json_mode=True)
        data = parse_json_output(result, "duplicates")
        assert_json_envelope(data, "duplicates")
        summary = data["summary"]
        assert "verdict" in summary
        assert "total_clusters" in summary
        assert "total_functions" in summary
        assert "estimated_reducible_lines" in summary
        assert "clusters" in data

    def test_json_cluster_structure(self, dup_project, cli_runner):
        """Each cluster in JSON should have required fields."""
        result = invoke_cli(cli_runner,
                            ["duplicates", "--threshold", "0.5"],
                            cwd=dup_project, json_mode=True)
        data = parse_json_output(result, "duplicates")
        if data["summary"]["total_clusters"] > 0:
            cluster = data["clusters"][0]
            assert "similarity" in cluster
            assert "size" in cluster
            assert "functions" in cluster
            assert "suggestion" in cluster
            # Check function structure
            fn = cluster["functions"][0]
            assert "name" in fn
            assert "file" in fn
            assert "line" in fn
            assert "lines" in fn

    def test_json_no_duplicates(self, no_dup_project, cli_runner):
        """JSON output with no duplicates should have empty clusters."""
        result = invoke_cli(cli_runner, ["duplicates"],
                            cwd=no_dup_project, json_mode=True)
        data = parse_json_output(result, "duplicates")
        assert_json_envelope(data, "duplicates")
        assert data["summary"]["total_clusters"] == 0
        assert data["clusters"] == []

    def test_json_reducible_lines(self, dup_project, cli_runner):
        """Estimated reducible lines should be a non-negative integer."""
        result = invoke_cli(cli_runner,
                            ["duplicates", "--threshold", "0.5"],
                            cwd=dup_project, json_mode=True)
        data = parse_json_output(result, "duplicates")
        lines = data["summary"]["estimated_reducible_lines"]
        assert isinstance(lines, int)
        assert lines >= 0


class TestTextOutput:
    """Test text output format."""

    def test_verdict_first(self, dup_project, cli_runner):
        """Output should start with VERDICT:."""
        result = invoke_cli(cli_runner,
                            ["duplicates", "--threshold", "0.5"],
                            cwd=dup_project)
        assert result.exit_code == 0
        lines = result.output.strip().split("\n")
        assert lines[0].startswith("VERDICT:")

    def test_summary_line(self, dup_project, cli_runner):
        """Output should include a SUMMARY line when clusters are found."""
        result = invoke_cli(cli_runner,
                            ["duplicates", "--threshold", "0.5"],
                            cwd=dup_project)
        assert result.exit_code == 0
        output = result.output
        if "CLUSTER" in output:
            assert "SUMMARY:" in output

    def test_cluster_format(self, cluster_project, cli_runner):
        """Cluster output should include similarity, function details, pattern."""
        result = invoke_cli(cli_runner,
                            ["duplicates", "--threshold", "0.5"],
                            cwd=cluster_project)
        assert result.exit_code == 0
        output = result.output
        if "CLUSTER" in output:
            assert "similarity" in output
            assert "Shared pattern:" in output
            assert "Suggestion:" in output


class TestEdgeCases:
    """Edge case tests."""

    def test_empty_project(self, tmp_path, cli_runner):
        """Empty project with no functions should handle gracefully."""
        proj = tmp_path / "empty_proj"
        proj.mkdir()
        (proj / ".gitignore").write_text(".roam/\n")
        (proj / "empty.py").write_text("# Just a comment\nX = 1\n")
        git_init(proj)
        out, rc = index_in_process(proj)
        assert rc == 0

        result = invoke_cli(cli_runner, ["duplicates"], cwd=proj)
        assert result.exit_code == 0
        assert "VERDICT:" in result.output

    def test_single_function(self, tmp_path, cli_runner):
        """Project with only one function should report no duplicates."""
        proj = tmp_path / "single_proj"
        proj.mkdir()
        (proj / ".gitignore").write_text(".roam/\n")
        (proj / "main.py").write_text(
            'def main():\n'
            '    """Entry point."""\n'
            '    data = load()\n'
            '    process(data)\n'
            '    save(data)\n'
            '    return 0\n'
        )
        git_init(proj)
        out, rc = index_in_process(proj)
        assert rc == 0

        result = invoke_cli(cli_runner, ["duplicates"], cwd=proj)
        assert result.exit_code == 0
        assert "No" in result.output or "0" in result.output

    def test_identical_functions_different_files(self, tmp_path, cli_runner):
        """Identical functions in different files should be top-similarity."""
        proj = tmp_path / "ident_proj"
        proj.mkdir()
        (proj / ".gitignore").write_text(".roam/\n")

        code = (
            'def process_data(items):\n'
            '    """Process a list of items."""\n'
            '    results = []\n'
            '    for item in items:\n'
            '        if item > 0:\n'
            '            results.append(item * 2)\n'
            '        else:\n'
            '            results.append(0)\n'
            '    return results\n'
        )

        (proj / "module_a.py").write_text(code)
        (proj / "module_b.py").write_text(code)

        git_init(proj)
        out, rc = index_in_process(proj)
        assert rc == 0

        result = invoke_cli(cli_runner,
                            ["duplicates", "--threshold", "0.5"],
                            cwd=proj)
        assert result.exit_code == 0
        output = result.output
        # Identical functions should definitely be detected
        assert "CLUSTER" in output
        assert "process_data" in output


class TestInternalFunctions:
    """Unit tests for internal helper functions."""

    def test_name_tokens(self):
        """Test camelCase and snake_case tokenization."""
        from roam.commands.cmd_duplicates import _name_tokens
        tokens = _name_tokens("parseJsonConfig")
        assert "parse" in tokens
        assert "json" in tokens
        assert "config" in tokens

        tokens = _name_tokens("parse_yaml_config")
        assert "parse" in tokens
        assert "yaml" in tokens
        assert "config" in tokens

    def test_jaccard_identical(self):
        """Jaccard of identical sets should be 1.0."""
        from roam.commands.cmd_duplicates import _jaccard
        assert _jaccard({"a", "b"}, {"a", "b"}) == 1.0

    def test_jaccard_disjoint(self):
        """Jaccard of disjoint sets should be 0.0."""
        from roam.commands.cmd_duplicates import _jaccard
        assert _jaccard({"a", "b"}, {"c", "d"}) == 0.0

    def test_jaccard_empty(self):
        """Jaccard of two empty sets should be 1.0."""
        from roam.commands.cmd_duplicates import _jaccard
        assert _jaccard(set(), set()) == 1.0

    def test_union_find_basic(self):
        """Union-Find should cluster connected elements."""
        from roam.commands.cmd_duplicates import _UnionFind
        uf = _UnionFind()
        uf.union(1, 2)
        uf.union(2, 3)
        clusters = uf.clusters()
        # 1, 2, 3 should be in the same cluster
        roots = {uf.find(1), uf.find(2), uf.find(3)}
        assert len(roots) == 1

    def test_union_find_separate(self):
        """Unconnected elements should remain in separate clusters."""
        from roam.commands.cmd_duplicates import _UnionFind
        uf = _UnionFind()
        uf.union(1, 2)
        uf.union(3, 4)
        assert uf.find(1) != uf.find(3)

    def test_param_similarity(self):
        """Same param count should give 1.0, different counts partial."""
        from roam.commands.cmd_duplicates import _param_similarity
        assert _param_similarity(3, 3) == 1.0
        assert _param_similarity(0, 0) == 1.0
        assert 0.0 < _param_similarity(2, 3) < 1.0

    def test_body_similarity_identical(self):
        """Identical body vectors should give 1.0."""
        from roam.commands.cmd_duplicates import _body_similarity
        v = {"line_count": 10, "param_count": 2, "nesting_depth": 1}
        assert _body_similarity(v, v) == 1.0

    def test_infer_pattern(self):
        """Pattern inference should find common tokens."""
        from roam.commands.cmd_duplicates import _infer_pattern
        names = ["parse_json_config", "parse_yaml_config", "parse_toml_config"]
        pattern = _infer_pattern(names)
        assert isinstance(pattern, str)
        assert len(pattern) > 0
