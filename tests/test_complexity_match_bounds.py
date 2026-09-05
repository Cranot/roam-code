"""Keep rewritten-function lookup exact while bounding unrelated AST traversal."""

from __future__ import annotations

import sys

import pytest

from roam.commands.changed_files import parse_source_with_grammar
from roam.index import complexity_extract as ce


def _reference_match(tree, original, source):
    """The original exhaustive selection rule, retained as an independent oracle."""
    row = original.start_point[0]
    original_name = original.child_by_field_name("name")
    name = ce._node_text(original_name, source) if original_name is not None else None
    matches = []
    for node in ce._walk_nodes(tree.root_node):
        if node.type not in ce._FUNCTION_NODES or abs(node.start_point[0] - row) > 1:
            continue
        if name:
            candidate_name = node.child_by_field_name("name")
            if candidate_name is not None and ce._node_text(candidate_name, source) != name:
                continue
        matches.append(node)
    return min(matches, key=lambda node: abs(node.start_point[0] - row)) if matches else None


@pytest.mark.parametrize(
    "language,source",
    [
        (
            "python",
            "def outer(x):\n    def inner():\n        return x\n    return inner()\n\ndef after():\n    return 2\n",
        ),
        (
            "python",
            "class A:\n    @staticmethod\n    def first(x):\n        return x\n    def second(self):\n        return 2\n",
        ),
        (
            "javascript",
            "function first(x) { return x; } function second(x) { return () => x; }\nconst third = x => x;\n",
        ),
        (
            "typescript",
            "class Box { first(x: number) { return x; }\nsecond() { const nested = () => 2; return nested(); } }\n",
        ),
        ("go", "package sample\nfunc first(x int) int { return x }\nfunc second() int { return 2 }\n"),
        ("java", "class Box { int first(int x) { return x; }\nint second() { return 2; } }\n"),
        ("rust", "fn first(x: i32) -> i32 { x }\nfn second() -> i32 { 2 }\n"),
        ("c", "int first(int x) { return x; }\nint second(void) { return 2; }\n"),
        ("cpp", "int first(int x) { return x; }\nint second() { auto nested = [] { return 2; }; return nested(); }\n"),
        ("c_sharp", "class Box { int First(int x) { return x; }\nint Second() { return 2; } }\n"),
        ("php", "<?php function first($x) { return $x; }\nfunction second() { return 2; }\n"),
        ("ruby", "def first(x)\n  x\nend\ndef second\n  2\nend\n"),
        ("kotlin", "fun first(x: Int): Int { return x }\nfun second(): Int { return 2 }\n"),
        ("swift", "func first(_ x: Int) -> Int { return x }\nfunc second() -> Int { return 2 }\n"),
    ],
)
@pytest.mark.parametrize("rewrite", ["identity", "leading_newline", "body_token_change", "incomplete_tail"])
def test_position_bounded_lookup_matches_exhaustive_rule(language, source, rewrite):
    original_tree, parsed, _ = parse_source_with_grammar(source.encode(), language)
    assert original_tree is not None and parsed is not None
    candidate = parsed
    if rewrite == "leading_newline":
        candidate = b"\n" + parsed
    elif rewrite == "body_token_change":
        candidate = parsed.replace(b"return", b"yield", 1)
    elif rewrite == "incomplete_tail":
        candidate = parsed[:-4]
    tree, candidate_source, _ = parse_source_with_grammar(candidate, language)
    assert tree is not None and candidate_source is not None
    functions = [node for node in ce._walk_nodes(original_tree.root_node) if node.type in ce._FUNCTION_NODES]
    if language in {"rust", "ruby"}:
        # These grammar-specific kinds are not in this helper's current
        # function registry. Preserve that absence; this optimization does
        # not expand the set of languages receiving extraction suggestions.
        assert functions == []
        kind = "function_item" if language == "rust" else "method"
        original = next(node for node in ce._walk_nodes(original_tree.root_node) if node.type == kind)
        assert ce._matching_function(tree, original, candidate_source) is None
        assert _reference_match(tree, original, candidate_source) is None
        return
    assert len(functions) >= 2, "the control must exercise neighboring or nested functions"
    for original in functions:
        assert ce._matching_function(tree, original, candidate_source) == _reference_match(
            tree, original, candidate_source
        )


class _Node:
    def __init__(self, kind, start, end, visits, children=()):
        self.type = kind
        self.start_point = (start, 0)
        self.end_point = (end, 0)
        self._children = children
        self._visits = visits

    @property
    def children(self):
        self._visits.append(self.type)
        return self._children

    def child_by_field_name(self, name):
        return None


class _Tree:
    def __init__(self, root):
        self.root_node = root


def test_lookup_does_not_descend_into_unrelated_function_bodies():
    visits = []
    unrelated = []
    for row in range(0, 4000, 4):
        leaves = tuple(_Node("identifier", row + 1, row + 1, visits) for _ in range(50))
        unrelated.append(_Node("function_definition", row, row + 2, visits, leaves))
    target = _Node("function_definition", 5000, 5010, visits)
    tree = _Tree(_Node("module", 0, 5010, visits, (*unrelated, target)))

    assert ce._matching_function(tree, target, b"") is target
    assert len(visits) <= 2, "unrelated function bodies must not be traversed for each rewritten candidate"


def test_lookup_handles_ast_nesting_beyond_python_recursion_limit():
    visits = []
    target = _Node("function_definition", 100, 101, visits)
    root = target
    for _ in range(sys.getrecursionlimit() + 100):
        root = _Node("container", 0, 101, visits, (root,))

    assert ce._matching_function(_Tree(root), target, b"") is target


def test_equal_distance_candidates_keep_preorder_tie_break():
    visits = []
    original = _Node("function_definition", 10, 15, visits)
    first = _Node("function_definition", 9, 15, visits)
    second = _Node("function_definition", 11, 15, visits)
    tree = _Tree(_Node("module", 0, 20, visits, (first, second)))
    assert ce._matching_function(tree, original, b"") is first


def test_missing_function_still_returns_none():
    visits = []
    original = _Node("function_definition", 10, 15, visits)
    distant = _Node("function_definition", 20, 25, visits)
    tree = _Tree(_Node("module", 0, 25, visits, (distant,)))
    assert ce._matching_function(tree, original, b"") is None
