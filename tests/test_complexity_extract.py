"""Tests for the deterministic helper-extraction analyzer.

Covers :mod:`roam.index.complexity_extract` — the engine behind
``roam complexity --suggest``. The analyzer answers "where do I cut this
function to reduce its cognitive complexity, and by how much?" purely from the
AST, so every assertion here is exact and deterministic (no LLM, no fixtures
beyond source strings).
"""

from __future__ import annotations

from roam.commands.changed_files import parse_source_with_grammar
from roam.index.complexity import _walk_complexity
from roam.index.complexity_extract import (
    ExtractionHint,
    hints_for_symbol,
    suggest_extractions,
)

# A function whose complexity is concentrated in a deeply-nested block — the
# case the mined signal is about. Cognitive complexity ≈ 29.
_NESTED = b"""def handler(items, cfg):
    total = 0
    for it in items:
        if it.active:
            for tag in it.tags:
                if tag in cfg.allow:
                    if tag.startswith("x"):
                        total += 1
                    elif tag.startswith("y"):
                        total += 2
                    else:
                        total += 3
    if total > 10 and cfg.strict:
        return "high"
    return "low"
"""

# Complexity that is *diffuse* — flat boolean expressions, no nesting. No single
# block extraction helps here; the analyzer should say so by returning nothing.
_FLAT = b"""def flat(a, b, c, d):
    x = a or b or c or d
    y = a and b and c
    return x, y
"""


def _first_function(tree):
    stack = [tree.root_node]
    while stack:
        node = stack.pop(0)
        if node.type == "function_definition":
            return node
        stack.extend(node.children)
    return None


def _parse_func(source: bytes):
    tree, parsed, _ = parse_source_with_grammar(source, "python")
    assert tree is not None
    func = _first_function(tree)
    assert func is not None
    return func, parsed


def test_nested_function_yields_ranked_hints():
    func, source = _parse_func(_NESTED)
    total = _walk_complexity(func, source, 0)["cognitive"]
    hints = suggest_extractions(func, source)

    assert hints, "a deeply-nested function must produce extraction hints"
    assert all(isinstance(h, ExtractionHint) for h in hints)

    # Every hint is internally consistent with the same model that scored the
    # function: the parent sheds exactly the block's in-place contribution.
    for h in hints:
        assert h.parent_after == round(total - h.reduction, 1)
        assert h.parent_after < total  # extraction always reduces the parent
        assert h.reduction >= 3.0  # only meaningful reductions surface
        assert h.line_count >= 3

    # The top hint "solves" the finding (drops below roam's high-severity floor
    # of 15) with the smallest helper — exactly the mined intent: the SMALLEST
    # helper that reduces complexity meaningfully.
    top = hints[0]
    assert top.parent_after < 15.0
    assert top.label == "if block"
    # The deepest nesting is where the triangular penalty concentrates, so the
    # winning cut is a deep block whose helper is far cheaper than its in-place
    # cost.
    assert top.helper_cc < top.reduction


def test_diffuse_complexity_yields_no_hints():
    func, source = _parse_func(_FLAT)
    # Sanity: it does have some complexity, just not the extractable kind.
    assert _walk_complexity(func, source, 0)["cognitive"] > 0
    assert suggest_extractions(func, source) == []


def test_hints_are_deterministic():
    func, source = _parse_func(_NESTED)
    first = suggest_extractions(func, source)
    second = suggest_extractions(func, source)
    assert [(h.line_start, h.reduction, h.parent_after) for h in first] == [
        (h.line_start, h.reduction, h.parent_after) for h in second
    ]


def test_max_hints_is_respected():
    func, source = _parse_func(_NESTED)
    assert len(suggest_extractions(func, source, max_hints=1)) == 1


def test_hints_for_symbol_end_to_end(tmp_path):
    target = tmp_path / "sample.py"
    target.write_bytes(_NESTED)
    # The function spans the whole file (line 1 to EOF); hints_for_symbol locates
    # the node and returns the same ranked hints.
    line_end = _NESTED.decode().count("\n")
    hints = hints_for_symbol(str(target), 1, line_end)
    assert hints
    assert hints[0].parent_after < 15.0


def test_hints_for_symbol_missing_file_is_safe():
    assert hints_for_symbol("/no/such/file.py", 1, 10) == []


def test_hints_for_symbol_unparseable_language_is_safe(tmp_path):
    target = tmp_path / "notes.unknownext"
    target.write_bytes(_NESTED)
    # Unknown extension → no language → empty, never raises.
    assert hints_for_symbol(str(target), 1, 10) == []
