"""Mechanical fix hints for complexity and structural-clone findings."""

from __future__ import annotations

import json

from roam.commands.changed_files import parse_source_with_grammar
from roam.commands.cmd_verify import (
    _check_clones,
    _check_complexity,
    _compact_fix_hint,
    _standard_complexity_violation,
)
from roam.graph.clone_detect import ClonePair, build_clone_fix_hint
from roam.index.complexity import _find_function_node, _walk_complexity
from roam.index.complexity_extract import build_complexity_fix_hint

_COMPLEX = b"""def dispatch(items, strict):
    accepted = []
    for item in items:
        if item.active:
            for child in item.children:
                if child.ready:
                    if strict and child.valid:
                        accepted.append(child)
                    else:
                        audit(child)
    if accepted:
        publish(accepted)
    return len(accepted)
"""

_MANUALLY_EXTRACTED = b"""def dispatch(items, strict):
    accepted = []
    for item in items:
        process_active_items()
    if accepted:
        publish(accepted)
    return len(accepted)
"""

_UNDER_THRESHOLD = b"""def simple(value):
    if value:
        return normalize(value)
    return None
"""


def _function(source: bytes):
    tree, parsed, _ = parse_source_with_grammar(source, "python")
    assert tree is not None
    func = _find_function_node(tree, 1, source.count(b"\n"))
    assert func is not None
    return func, parsed


def test_complexity_fix_hint_delta_matches_manual_extraction():
    """The promised residual is the score of a real replacement-call AST."""
    func, source = _function(_COMPLEX)
    original_score = _walk_complexity(func, source, 0)["cognitive"]

    hint = build_complexity_fix_hint(func, source, threshold=15)

    assert hint is not None
    assert hint["kind"] == "extract"
    assert hint["span"] == {"start_line": 4, "end_line": 10}
    assert hint["suggested_name"]
    assert hint["expected_delta"] == original_score - hint["residual_score"]
    assert hint["auto_fixable"] is False
    assert hint["reason"]

    extracted_func, extracted_source = _function(_MANUALLY_EXTRACTED)
    extracted_score = _walk_complexity(extracted_func, extracted_source, 0)["cognitive"]
    assert extracted_score == hint["residual_score"]


def test_complexity_fix_hint_marks_two_candidates_as_iterative():
    source = (
        _COMPLEX
        + b"""
def dispatch_again(items, strict):
    accepted = []
    for item in items:
        if item.active:
            for child in item.children:
                if child.ready:
                    if strict and child.valid:
                        accepted.append(child)
    for item in items:
        if item.pending:
            for child in item.children:
                if child.ready:
                    if strict and child.valid:
                        audit(child)
    return accepted
"""
    )
    tree, parsed, _ = parse_source_with_grammar(source, "python")
    assert tree is not None
    func = _find_function_node(tree, 15, source.count(b"\n"))
    assert func is not None

    hint = build_complexity_fix_hint(func, parsed, threshold=15)

    assert hint is not None
    assert hint["iterative"] is True
    assert "must be iterative" in hint["iteration_reason"]
    assert len(hint["candidate_spans"]) == 2
    assert hint["residual_score"] >= 15


def test_under_threshold_function_has_no_complexity_fix_hint():
    func, source = _function(_UNDER_THRESHOLD)
    assert build_complexity_fix_hint(func, source, threshold=15) is None


def test_provably_local_extraction_is_auto_fixable():
    source = b"""def notify(items):
    for item in items:
        if item.active:
            for child in item.children:
                if child.ready:
                    if child.valid:
                        notify_child(child)
                    else:
                        audit_child(child)
    if items:
        audit_items(items)
"""
    func, parsed = _function(source)
    hint = build_complexity_fix_hint(func, parsed, threshold=15)
    assert hint is not None
    assert hint["auto_fixable"] is True
    assert "reason" not in hint


def test_clone_family_fix_hint_identifies_varying_token_slots(tmp_path):
    left = tmp_path / "orders.py"
    right = tmp_path / "invoices.py"
    left.write_text(
        """def process_orders(items):
    results = []
    for item in items:
        if item.is_valid():
            value = item.calculate()
            results.append(value)
    return results
""",
        encoding="utf-8",
    )
    right.write_text(
        """def handle_invoices(entries):
    output = []
    for entry in entries:
        if entry.is_valid():
            amount = entry.calculate()
            output.append(amount)
    return output
""",
        encoding="utf-8",
    )
    members = [
        {"file": str(left), "line_start": 1, "line_end": 7},
        {"file": str(right), "line_start": 1, "line_end": 7},
    ]

    hint = build_clone_fix_hint(members, similarity=0.96)

    assert hint is not None
    assert hint["kind"] == "parameterize"
    assert hint["similarity"] == 0.96
    assert hint["members"] == [
        {"file": str(left), "span": {"start_line": 1, "end_line": 7}},
        {"file": str(right), "span": {"start_line": 1, "end_line": 7}},
    ]
    assert hint["varying_slots"]
    differing_values = {value for slot in hint["varying_slots"] for value in slot["values"]}
    assert {"items", "entries"} <= differing_values
    assert {"results", "output"} <= differing_values
    assert hint["auto_fixable"] is False
    assert hint["reason"]


def test_absent_hints_preserve_verify_finding_bytes():
    """Conservation snapshots captured before fix_hint enrichment."""
    row = {
        "name": "simple",
        "file_path": "src/simple.py",
        "line_start": 3,
        "line_end": 5,
    }
    finding = _standard_complexity_violation(row, 16, 15)
    assert json.dumps(finding, sort_keys=True, separators=(",", ":")) == (
        '{"category":"complexity","cognitive_complexity":16,"file":"src/simple.py",'
        '"fix":"Decompose `simple` \\u2014 extract helpers / flatten nesting to lower cognitive load",'
        '"line":3,"line_end":5,"message":"fn `simple` cognitive complexity 16 (threshold 15)",'
        '"severity":"WARN","symbol":"simple"}'
    )
    assert json.dumps(_check_complexity(None, []), sort_keys=True, separators=(",", ":")) == (
        '{"score":100,"violations":[]}'
    )
    assert json.dumps(_check_clones(None, []), sort_keys=True, separators=(",", ":")) == (
        '{"score":100,"violations":[]}'
    )


def test_verify_complexity_finding_emits_and_renders_fix_hint(tmp_path):
    path = tmp_path / "complex.py"
    path.write_bytes(_COMPLEX)
    func, source = _function(_COMPLEX)
    score = _walk_complexity(func, source, 0)["cognitive"]
    row = {
        "name": "dispatch",
        "file_path": str(path),
        "line_start": 1,
        "line_end": _COMPLEX.count(b"\n"),
    }

    finding = _standard_complexity_violation(row, score, 15)

    assert finding["fix_hint"]["kind"] == "extract"
    rendered = _compact_fix_hint(finding["fix_hint"])
    assert rendered is not None
    assert "delta 26" in rendered
    assert "residual 2" in rendered


def test_verify_clone_finding_emits_family_hint(tmp_path, monkeypatch):
    left = tmp_path / "left.py"
    right = tmp_path / "right.py"
    left.write_text("def left(x):\n    return normalize(x)\n", encoding="utf-8")
    right.write_text("def right(y):\n    return normalize(y)\n", encoding="utf-8")
    members = [
        {"file": str(left), "line_start": 1, "line_end": 2},
        {"file": str(right), "line_start": 1, "line_end": 2},
    ]
    hint = build_clone_fix_hint(members, 0.9)
    pair = ClonePair(
        str(left),
        "left",
        f"{left}:left",
        1,
        2,
        str(right),
        "right",
        f"{right}:right",
        1,
        2,
        0.9,
        fix_hint=hint,
    )
    from roam.graph import clone_detect

    monkeypatch.setattr(clone_detect, "detect_clones", lambda conn: ([pair], []))

    result = _check_clones(None, [str(left)])

    finding = result["violations"][0]
    assert finding["fix_hint"] == hint
    assert "parameterize 2 members" in (_compact_fix_hint(hint) or "")
