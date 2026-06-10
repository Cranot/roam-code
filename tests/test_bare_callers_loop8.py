"""Loop8 (2026-06-02) — bare 'who calls X' symbol extraction.

The backtick-only callers fallback missed the common un-backticked shape
"who calls open_db", which routed to structural_callers but stayed `full`
(empty envelope). _extract_bare_callers_symbol resolves the bareword so the
probe fires and the L1 envelope carries callers + call_line."""

from __future__ import annotations

import pytest

from roam.plan.compiler import _extract_bare_callers_symbol


@pytest.mark.parametrize(
    "task,expected",
    [
        ("who calls open_db", "open_db"),
        ("what uses _evaluate_mcp_mode_policy", "_evaluate_mcp_mode_policy"),
        ("callers of useThemeClasses", "useThemeClasses"),
        ("who references compile_plan", "compile_plan"),
        ("open_db callers", "open_db"),
    ],
)
def test_extracts_identifier_shaped_symbols(task, expected):
    assert _extract_bare_callers_symbol(task) == expected


@pytest.mark.parametrize(
    "task",
    [
        "who calls the function",  # stopword
        "what calls test",  # stopword
        "who calls foo",  # not identifier-shaped (no _ / no camelCase)
        "who calls bar",  # ditto
        "explain the codebase",  # no callers verb
        "who calls the class",  # stopword
    ],
)
def test_rejects_non_symbols(task):
    assert _extract_bare_callers_symbol(task) is None
