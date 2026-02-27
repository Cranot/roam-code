"""
Test Language Extractors Against Corpus.

This test module validates that language extractors produce the expected
output for all corpus test cases.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# These are optional dependencies — skip the entire module if unavailable
yaml = pytest.importorskip("yaml", reason="PyYAML not installed")

# QueryCursor was added in tree-sitter 0.23+; older versions (e.g. on Python 3.9) lack it
try:
    from tree_sitter import QueryCursor  # noqa: F401
except ImportError:
    pytest.skip("tree-sitter too old (no QueryCursor)", allow_module_level=True)

# Add src to path for imports
SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roam.languages.extractor_schema import LanguageConfig, validate_config
from roam.languages.query_engine import QueryEngine

# Import corpus infrastructure
from tests.fixtures.languages.corpus_runner import (
    CORPUS_ROOT,
    CorpusTestCase,
    compare_extraction,
    discover_corpus_cases,
)

# -------------------------------------------------------------------------
# Fixtures
# -------------------------------------------------------------------------


@pytest.fixture
def kotlin_config() -> LanguageConfig:
    """Load the Kotlin language config."""
    config_path = SRC / "roam" / "languages" / "extractors" / "kotlin.yaml"
    if not config_path.exists():
        pytest.skip("Kotlin YAML extractor not yet available")
    return LanguageConfig.load(config_path)


@pytest.fixture
def kotlin_engine(kotlin_config: LanguageConfig) -> QueryEngine:
    """Create a QueryEngine for Kotlin."""
    return QueryEngine(kotlin_config)


def _load_corpus_case(language: str, name: str) -> CorpusTestCase | None:
    """Load a specific corpus test case by language and name."""
    cases = discover_corpus_cases(language)
    for case in cases:
        if case.name == name:
            return case
    return None


# -------------------------------------------------------------------------
# Corpus Discovery Tests
# -------------------------------------------------------------------------


def test_corpus_directory_exists():
    """Verify the corpus root directory exists."""
    assert CORPUS_ROOT.exists(), f"Corpus root not found: {CORPUS_ROOT}"


def test_kotlin_corpus_exists():
    """Verify Kotlin corpus files exist."""
    kotlin_dir = CORPUS_ROOT / "kotlin"
    assert kotlin_dir.exists(), f"Kotlin corpus directory not found: {kotlin_dir}"

    kt_files = list(kotlin_dir.glob("*.kt"))
    assert len(kt_files) >= 2, f"Expected at least 2 Kotlin test files, found {len(kt_files)}"


def test_corpus_cases_discovered():
    """Verify corpus test cases are discovered."""
    cases = discover_corpus_cases()
    assert len(cases) >= 2, f"Expected at least 2 corpus cases, found {len(cases)}"


# -------------------------------------------------------------------------
# Kotlin Extractor Tests
# -------------------------------------------------------------------------


class TestKotlinCorpus:
    """Test Kotlin extractor against corpus files."""

    def test_basic_symbols(self, kotlin_engine: QueryEngine):
        """Test extraction of basic Kotlin symbols."""
        case = _load_corpus_case("kotlin", "basic")
        if case is None:
            pytest.skip("Kotlin basic.kt corpus case not found")

        result = kotlin_engine.extract(case.source, str(case.source_path))
        discrepancies = compare_extraction(result, case.expected)

        if discrepancies:
            msg = "\n".join(f"  - {d}" for d in discrepancies)
            pytest.fail(f"Kotlin basic.kt extraction discrepancies:\n{msg}")

    def test_inheritance(self, kotlin_engine: QueryEngine):
        """Test extraction of Kotlin inheritance relationships."""
        case = _load_corpus_case("kotlin", "inheritance")
        if case is None:
            pytest.skip("Kotlin inheritance.kt corpus case not found")

        result = kotlin_engine.extract(case.source, str(case.source_path))
        discrepancies = compare_extraction(result, case.expected)

        if discrepancies:
            msg = "\n".join(f"  - {d}" for d in discrepancies)
            pytest.fail(f"Kotlin inheritance.kt extraction discrepancies:\n{msg}")


# -------------------------------------------------------------------------
# Schema Validation Tests
# -------------------------------------------------------------------------


class TestExtractorSchema:
    """Test the extractor schema validation."""

    def test_kotlin_config_valid(self, kotlin_config: LanguageConfig):
        """Verify the Kotlin config is valid."""
        errors = validate_config(kotlin_config)
        assert not errors, f"Kotlin config validation errors: {errors}"

    def test_kotlin_config_has_symbols(self, kotlin_config: LanguageConfig):
        """Verify the Kotlin config has symbol patterns."""
        assert len(kotlin_config.symbols) >= 5, (
            f"Expected at least 5 symbol patterns, found {len(kotlin_config.symbols)}"
        )

    def test_kotlin_config_has_inheritance(self, kotlin_config: LanguageConfig):
        """Verify the Kotlin config has inheritance patterns."""
        assert "extends" in kotlin_config.inheritance or "implements" in kotlin_config.inheritance, (
            "Expected extends or implements inheritance patterns"
        )


# -------------------------------------------------------------------------
# Query Engine Tests
# -------------------------------------------------------------------------


class TestQueryEngine:
    """Test the query engine directly."""

    def test_kotlin_engine_created(self, kotlin_engine: QueryEngine):
        """Verify Kotlin engine can be created."""
        assert kotlin_engine.language_name == "kotlin"
        assert ".kt" in kotlin_engine.file_extensions

    def test_kotlin_simple_extraction(self, kotlin_engine: QueryEngine):
        """Test simple Kotlin extraction."""
        source = """
class User(val name: String) {
    fun greet(): String = "Hello"
}
"""
        result = kotlin_engine.extract(source, "test.kt")

        assert len(result.errors) == 0, f"Extraction errors: {result.errors}"
        assert len(result.symbols) >= 2, f"Expected at least 2 symbols, got {len(result.symbols)}"

        # Check for User class
        user_class = next((s for s in result.symbols if s.name == "User"), None)
        assert user_class is not None, "Expected to find User class"
        assert user_class.kind == "class"

        # Check for greet method
        greet_method = next((s for s in result.symbols if s.name == "greet"), None)
        assert greet_method is not None, "Expected to find greet method"
        assert greet_method.kind == "method"
        assert greet_method.scope == "User"

    def test_kotlin_inheritance_extraction(self, kotlin_engine: QueryEngine):
        """Test Kotlin inheritance extraction."""
        source = """
open class Animal(val name: String)
class Dog(name: String) : Animal(name)
"""
        result = kotlin_engine.extract(source, "test.kt")

        assert len(result.errors) == 0, f"Extraction errors: {result.errors}"

        # Check for inheritance relationship
        assert len(result.inheritance) >= 1, f"Expected at least 1 inheritance, got {len(result.inheritance)}"

        dog_inherits = next(
            (i for i in result.inheritance if i.child == "Dog" and i.parent == "Animal"),
            None,
        )
        assert dog_inherits is not None, "Expected Dog to inherit from Animal"


# -------------------------------------------------------------------------
# Parametrized Corpus Tests (run all discovered cases)
# -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "case",
    discover_corpus_cases(),
    ids=lambda c: f"{c.language}:{c.name}",
)
def test_corpus_case(case: CorpusTestCase):
    """
    Run all corpus test cases.

    This parametrized test will run once for each discovered corpus case.
    Currently skips languages without YAML extractors.
    """
    # Check if we have a YAML extractor for this language
    extractor_path = SRC / "roam" / "languages" / "extractors" / f"{case.language}.yaml"
    if not extractor_path.exists():
        pytest.skip(f"No YAML extractor for {case.language}")

    # Load config and create engine
    config = LanguageConfig.load(extractor_path)
    engine = QueryEngine(config)

    # Run extraction
    result = engine.extract(case.source, str(case.source_path))

    # Compare against expected
    discrepancies = compare_extraction(result, case.expected)

    if discrepancies:
        msg = "\n".join(f"  - {d}" for d in discrepancies)
        pytest.fail(f"Extraction discrepancies for {case.language}:{case.name}:\n{msg}")
