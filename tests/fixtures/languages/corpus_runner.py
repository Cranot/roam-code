"""
Language Extractor Test Corpus Infrastructure.

This module provides infrastructure for testing language extractors
against a corpus of test files with expected outputs.

Directory structure:
    tests/fixtures/languages/
        kotlin/
            basic.kt              # Test source file
            basic.expected.json   # Expected extraction output
            inheritance.kt
            inheritance.expected.json
        python/
            basic.py
            basic.expected.json
            ...

Each .expected.json file has the format:
{
    "symbols": [
        {"name": "User", "kind": "class", "line": 3},
        ...
    ],
    "references": [...],
    "inheritance": [...]
}
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from roam.languages.query_engine import ExtractionResult

CORPUS_ROOT = Path(__file__).parent


@dataclass
class CorpusTestCase:
    """A single test case in the corpus."""

    language: str
    name: str
    source_path: Path
    expected_path: Path
    source: str
    expected: dict[str, Any]

    @classmethod
    def load(cls, source_path: Path) -> "CorpusTestCase | None":
        """Load a test case from a source file."""
        expected_path = source_path.with_suffix(".expected.json")
        if not expected_path.exists():
            return None

        language = source_path.parent.name
        name = source_path.stem

        source = source_path.read_text(encoding="utf-8")
        expected = json.loads(expected_path.read_text(encoding="utf-8"))

        return cls(
            language=language,
            name=name,
            source_path=source_path,
            expected_path=expected_path,
            source=source,
            expected=expected,
        )


def discover_corpus_cases(language: str | None = None) -> list[CorpusTestCase]:
    """
    Discover all test cases in the corpus.

    Args:
        language: If provided, only return cases for this language

    Returns:
        List of CorpusTestCase objects
    """
    cases = []

    for lang_dir in CORPUS_ROOT.iterdir():
        if not lang_dir.is_dir():
            continue
        if lang_dir.name.startswith("_"):
            continue
        if language and lang_dir.name != language:
            continue

        for source_file in lang_dir.iterdir():
            if source_file.suffix in (".expected.json", ".pyc", ".pyo", ".json"):
                continue
            if source_file.name.startswith("_"):
                continue

            case = CorpusTestCase.load(source_file)
            if case:
                cases.append(case)

    return cases


# Alias for backward compatibility
discover_cases = discover_corpus_cases


def compare_extraction(
    result: "ExtractionResult",
    expected: dict[str, Any],
) -> list[str]:
    """
    Compare extraction result against expected output.

    Returns a list of discrepancies (empty if match).
    """
    discrepancies = []

    # Compare symbols
    expected_symbols = expected.get("symbols", [])
    actual_symbols = [
        {
            "name": s.name,
            "kind": s.kind,
            "line": s.line,
            "scope": s.scope,
        }
        for s in result.symbols
    ]

    # Sort for comparison
    expected_symbols_sorted = sorted(expected_symbols, key=lambda s: (s.get("line", 0), s.get("name", "")))
    actual_symbols_sorted = sorted(actual_symbols, key=lambda s: (s["line"], s["name"]))

    if len(expected_symbols_sorted) != len(actual_symbols_sorted):
        discrepancies.append(
            f"Symbol count mismatch: expected {len(expected_symbols_sorted)}, got {len(actual_symbols_sorted)}"
        )

    for exp, act in zip(expected_symbols_sorted, actual_symbols_sorted):
        if exp.get("name") != act["name"]:
            discrepancies.append(
                f"Symbol name mismatch at line {exp.get('line')}: expected '{exp.get('name')}', got '{act['name']}'"
            )
        if exp.get("kind") and exp.get("kind") != act["kind"]:
            discrepancies.append(
                f"Symbol kind mismatch for '{act['name']}': expected '{exp.get('kind')}', got '{act['kind']}'"
            )
        if exp.get("scope") and exp.get("scope") != act["scope"]:
            discrepancies.append(
                f"Symbol scope mismatch for '{act['name']}': expected '{exp.get('scope')}', got '{act['scope']}'"
            )

    # Compare inheritance
    expected_inheritance = expected.get("inheritance", [])
    actual_inheritance = [
        {
            "child": i.child,
            "parent": i.parent,
            "relationship": i.relationship,
        }
        for i in result.inheritance
    ]

    expected_inh_sorted = sorted(expected_inheritance, key=lambda i: (i.get("child", ""), i.get("parent", "")))
    actual_inh_sorted = sorted(actual_inheritance, key=lambda i: (i["child"], i["parent"]))

    if len(expected_inh_sorted) != len(actual_inh_sorted):
        discrepancies.append(
            f"Inheritance count mismatch: expected {len(expected_inh_sorted)}, got {len(actual_inh_sorted)}"
        )

    for exp, act in zip(expected_inh_sorted, actual_inh_sorted):
        if exp.get("child") != act["child"] or exp.get("parent") != act["parent"]:
            discrepancies.append(
                f"Inheritance mismatch: expected '{exp.get('child')}' -> '{exp.get('parent')}', "
                f"got '{act['child']}' -> '{act['parent']}'"
            )

    # Compare references (optional, may be noisy)
    expected_refs = expected.get("references", [])
    if expected_refs:
        actual_refs = [
            {
                "name": r.name,
                "kind": r.kind,
                "line": r.line,
            }
            for r in result.references
        ]
        expected_refs_sorted = sorted(expected_refs, key=lambda r: (r.get("line", 0), r.get("name", "")))
        actual_refs_sorted = sorted(actual_refs, key=lambda r: (r["line"], r["name"]))

        if len(expected_refs_sorted) != len(actual_refs_sorted):
            discrepancies.append(
                f"Reference count mismatch: expected {len(expected_refs_sorted)}, got {len(actual_refs_sorted)}"
            )

    return discrepancies


def generate_expected_json(result: "ExtractionResult") -> str:
    """Generate expected.json content from an extraction result."""
    data = {
        "symbols": [
            {
                "name": s.name,
                "kind": s.kind,
                "line": s.line,
                "scope": s.scope,
            }
            for s in result.symbols
        ],
        "inheritance": [
            {
                "child": i.child,
                "parent": i.parent,
                "relationship": i.relationship,
            }
            for i in result.inheritance
        ],
        "references": [
            {
                "name": r.name,
                "kind": r.kind,
                "line": r.line,
            }
            for r in result.references
        ],
    }
    return json.dumps(data, indent=2, ensure_ascii=False)
