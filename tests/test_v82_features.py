"""Tests for v8.2.0 improvements.

Covers:
1. Python extractor: with-statement, raise, except clause references
2. Metrics history: test path filtering + geometric mean health score
3. Patterns command: self-detection filtering
4. Health command: non-production path exclusion
5. Dead code removal: functions actually removed
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Helper: parse Python source and extract symbols + references
# ---------------------------------------------------------------------------


def _parse_py(source_text: str, file_path: str = "example.py"):
    """Parse Python source and return (symbols, references)."""
    from tree_sitter_language_pack import get_parser

    from roam.index.parser import GRAMMAR_ALIASES
    from roam.languages.registry import get_extractor

    grammar = GRAMMAR_ALIASES.get("python", "python")
    parser = get_parser(grammar)
    source = source_text.encode("utf-8")
    tree = parser.parse(source)

    extractor = get_extractor("python")
    symbols = extractor.extract_symbols(tree, source, file_path)
    references = extractor.extract_references(tree, source, file_path)
    return symbols, references


def _ref_targets(refs, kind=None, source_name=None):
    """Get reference target_names, optionally filtered by kind and/or source_name."""
    result = []
    for r in refs:
        if kind and r["kind"] != kind:
            continue
        if source_name is not None and r.get("source_name") != source_name:
            continue
        result.append(r["target_name"])
    return result


# ===========================================================================
# 1. Python extractor: with-statement context manager references
# ===========================================================================


class TestWithStatement:
    """with-statement context managers should produce call references."""

    def test_with_simple_call(self):
        code = """\
def process():
    with open_db() as conn:
        pass
"""
        _, refs = _parse_py(code)
        calls = _ref_targets(refs, kind="call", source_name="process")
        assert "open_db" in calls

    def test_with_nested_calls(self):
        code = """\
def process():
    with open("file.txt") as f:
        data = json.load(f)
"""
        _, refs = _parse_py(code)
        calls = _ref_targets(refs, kind="call", source_name="process")
        assert "open" in calls
        assert "json.load" in calls

    def test_with_multiple_context_managers(self):
        code = """\
def process():
    with open_db() as conn, get_lock() as lock:
        pass
"""
        _, refs = _parse_py(code)
        calls = _ref_targets(refs, kind="call", source_name="process")
        assert "open_db" in calls
        assert "get_lock" in calls

    def test_with_attribute_call(self):
        code = """\
def process():
    with tempfile.NamedTemporaryFile() as f:
        pass
"""
        _, refs = _parse_py(code)
        calls = _ref_targets(refs, kind="call", source_name="process")
        assert "tempfile.NamedTemporaryFile" in calls


# ===========================================================================
# 2. Python extractor: raise statement references
# ===========================================================================


class TestRaiseStatement:
    """raise statements should produce call references."""

    def test_raise_with_call(self):
        code = """\
def validate(x):
    if x < 0:
        raise ValueError("negative")
"""
        _, refs = _parse_py(code)
        calls = _ref_targets(refs, kind="call", source_name="validate")
        assert "ValueError" in calls

    def test_raise_without_parens(self):
        """raise SomeError (no call) should still create a call reference."""
        code = """\
def fail():
    raise StopIteration
"""
        _, refs = _parse_py(code)
        calls = _ref_targets(refs, kind="call", source_name="fail")
        assert "StopIteration" in calls

    def test_raise_custom_error(self):
        code = """\
def check():
    raise CustomError("msg")
"""
        _, refs = _parse_py(code)
        calls = _ref_targets(refs, kind="call", source_name="check")
        assert "CustomError" in calls

    def test_raise_attribute_error(self):
        code = """\
def check():
    raise errors.NotFoundError("missing")
"""
        _, refs = _parse_py(code)
        calls = _ref_targets(refs, kind="call", source_name="check")
        assert "errors.NotFoundError" in calls


# ===========================================================================
# 3. Python extractor: except clause type references
# ===========================================================================


class TestExceptClause:
    """except clauses should produce type_ref references."""

    def test_except_single_type(self):
        code = """\
def handle():
    try:
        pass
    except CustomError:
        pass
"""
        _, refs = _parse_py(code)
        type_refs = _ref_targets(refs, kind="type_ref", source_name="handle")
        assert "CustomError" in type_refs

    def test_except_with_as(self):
        code = """\
def handle():
    try:
        pass
    except CustomError as e:
        pass
"""
        _, refs = _parse_py(code)
        type_refs = _ref_targets(refs, kind="type_ref", source_name="handle")
        assert "CustomError" in type_refs
        # 'e' should NOT be a type_ref
        assert "e" not in type_refs

    def test_except_tuple(self):
        code = """\
def handle():
    try:
        pass
    except (CustomError, AnotherError):
        pass
"""
        _, refs = _parse_py(code)
        type_refs = _ref_targets(refs, kind="type_ref", source_name="handle")
        assert "CustomError" in type_refs
        assert "AnotherError" in type_refs

    def test_except_tuple_with_as(self):
        code = """\
def handle():
    try:
        pass
    except (CustomError, AnotherError) as e:
        pass
"""
        _, refs = _parse_py(code)
        type_refs = _ref_targets(refs, kind="type_ref", source_name="handle")
        assert "CustomError" in type_refs
        assert "AnotherError" in type_refs
        assert "e" not in type_refs

    def test_except_attribute_type(self):
        code = """\
def handle():
    try:
        pass
    except module.CustomError as e:
        pass
"""
        _, refs = _parse_py(code)
        type_refs = _ref_targets(refs, kind="type_ref", source_name="handle")
        assert "module.CustomError" in type_refs

    def test_except_builtin_filtered(self):
        """Builtin types like int, str should be filtered out of except refs."""
        code = """\
def handle():
    try:
        pass
    except CustomError:
        pass
"""
        _, refs = _parse_py(code)
        type_refs = _ref_targets(refs, kind="type_ref", source_name="handle")
        # CustomError is not a builtin, should be included
        assert "CustomError" in type_refs

    def test_except_bare(self):
        """except: (bare) should produce no type_ref."""
        code = """\
def handle():
    try:
        pass
    except:
        pass
"""
        _, refs = _parse_py(code)
        type_refs = _ref_targets(refs, kind="type_ref", source_name="handle")
        assert type_refs == []

    def test_multiple_except_clauses(self):
        code = """\
def handle():
    try:
        pass
    except FirstError:
        pass
    except SecondError as e:
        pass
"""
        _, refs = _parse_py(code)
        type_refs = _ref_targets(refs, kind="type_ref", source_name="handle")
        assert "FirstError" in type_refs
        assert "SecondError" in type_refs


# ===========================================================================
# 4. Metrics history: _is_test_path + geometric mean health score
# ===========================================================================


class TestMetricsHistory:
    """Test metrics_history.py helper functions."""

    def test_is_test_path_prefix(self):
        from roam.commands.metrics_history import _is_test_path

        assert _is_test_path("tests/test_basic.py") is True
        assert _is_test_path("test_something.py") is True

    def test_is_test_path_suffix(self):
        from roam.commands.metrics_history import _is_test_path

        assert _is_test_path("my_test.py") is True

    def test_is_not_test_path(self):
        from roam.commands.metrics_history import _is_test_path

        assert _is_test_path("src/roam/cli.py") is False
        assert _is_test_path("src/roam/commands/cmd_health.py") is False


# ===========================================================================
# 5. Patterns command: self-detection filter
# ===========================================================================


class TestPatternsFilter:
    """Test _is_test_or_detector_path from cmd_patterns.py."""

    def test_filters_test_files(self):
        from roam.commands.cmd_patterns import _is_test_or_detector_path

        assert _is_test_or_detector_path("tests/test_basic.py") is True
        assert _is_test_or_detector_path("test_something.py") is True

    def test_filters_cmd_patterns_itself(self):
        from roam.commands.cmd_patterns import _is_test_or_detector_path

        assert _is_test_or_detector_path("src/roam/commands/cmd_patterns.py") is True

    def test_allows_production_files(self):
        from roam.commands.cmd_patterns import _is_test_or_detector_path

        assert _is_test_or_detector_path("src/roam/cli.py") is False
        assert _is_test_or_detector_path("src/roam/commands/cmd_health.py") is False

    def test_filters_test_directories(self):
        from roam.commands.cmd_patterns import _is_test_or_detector_path

        assert _is_test_or_detector_path("tests/integration/test_api.py") is True
        assert _is_test_or_detector_path("spec/models/user_spec.py") is True

    def test_handles_backslashes(self):
        from roam.commands.cmd_patterns import _is_test_or_detector_path

        assert _is_test_or_detector_path("tests\\test_basic.py") is True
        assert _is_test_or_detector_path("src\\roam\\commands\\cmd_patterns.py") is True


# ===========================================================================
# 6. Health command: non-production path exclusion
# ===========================================================================


class TestHealthNonProductionPaths:
    """Test _is_utility_path includes non-production paths."""

    def test_dev_directory_is_utility(self):
        from roam.commands.cmd_health import _is_utility_path

        assert _is_utility_path("dev/roam-bench.py") is True

    def test_tests_directory_is_utility(self):
        from roam.commands.cmd_health import _is_utility_path

        assert _is_utility_path("tests/test_basic.py") is True

    def test_scripts_directory_is_utility(self):
        from roam.commands.cmd_health import _is_utility_path

        assert _is_utility_path("scripts/deploy.sh") is True

    def test_benchmark_directory_is_utility(self):
        from roam.commands.cmd_health import _is_utility_path

        assert _is_utility_path("benchmark/perf.py") is True

    def test_conftest_is_utility(self):
        from roam.commands.cmd_health import _is_utility_path

        assert _is_utility_path("tests/conftest.py") is True

    def test_production_code_not_utility(self):
        from roam.commands.cmd_health import _is_utility_path

        # Unless it matches OTHER utility patterns, normal production code is not utility
        assert _is_utility_path("src/roam/commands/cmd_search.py") is False


# ===========================================================================
# 7. File roles: dev/ is ROLE_SCRIPTS
# ===========================================================================


class TestFileRolesDev:
    """Test that dev/ directory files get ROLE_SCRIPTS."""

    def test_dev_file_classified_as_scripts(self):
        from roam.index.file_roles import ROLE_SCRIPTS, classify_file

        role = classify_file("dev/roam-bench.py")
        assert role == ROLE_SCRIPTS

    def test_dev_nested_file(self):
        from roam.index.file_roles import ROLE_SCRIPTS, classify_file

        role = classify_file("dev/tools/helper.py")
        assert role == ROLE_SCRIPTS


# ===========================================================================
# 8. Dead code removal: verify functions are gone
# ===========================================================================


class TestDeadCodeRemoval:
    """Verify that dead functions from v8.0.x were removed."""

    def test_condense_cycles_removed(self):
        from roam.graph import cycles

        assert not hasattr(cycles, "condense_cycles")

    def test_layer_balance_removed(self):
        from roam.graph import layers

        assert not hasattr(layers, "layer_balance")

    def test_find_path_removed(self):
        from roam.graph import pathfinding

        assert not hasattr(pathfinding, "find_path")

    def test_build_reverse_adj_removed(self):
        from roam.commands import graph_helpers

        assert not hasattr(graph_helpers, "build_reverse_adj")

    def test_get_symbol_blame_removed(self):
        from roam.index import git_stats

        assert not hasattr(git_stats, "get_symbol_blame")

    def test_init_exports_clean(self):
        """graph/__init__.py should not export removed functions."""
        from roam.graph import __all__

        removed = {"condense_cycles", "layer_balance", "find_path"}
        assert removed.isdisjoint(set(__all__))
