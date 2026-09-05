"""Do not suggest set membership or hoisting from disproven loop invariance."""

from __future__ import annotations

import json
import sqlite3

import pytest

from roam.catalog.detectors import detect_loop_invariant_call, detect_loop_lookup
from roam.commands.changed_files import parse_source_with_grammar
from roam.index.complexity import _extract_math_signals


def _signals(body, language="javascript"):
    source = f"function sample(items, allowed, positions) {{ for (const item of items) {{ {body} }} }}".encode()
    tree, parsed, _ = parse_source_with_grammar(source, language)
    assert tree is not None and parsed is not None
    return _extract_math_signals(tree.root_node.named_children[0], parsed, "sample")


@pytest.mark.parametrize("language", ["javascript", "typescript"])
@pytest.mark.parametrize(
    "expression", ["allowed.includes(item)", "allowed.indexOf(item) >= 0", "allowed.indexOf(item) !== -1"]
)
def test_stable_boolean_membership_is_still_detected(language, expression):
    assert _signals(f"if ({expression}) emit(item);", language)["loop_lookup_calls"]


@pytest.mark.parametrize(
    "body",
    [
        "if (allowed.includes(item)) emit(item); allowed.push(item);",
        "const at = allowed.indexOf(item); allowed.splice(at, 1);",
        "positions.push(allowed.indexOf(item));",
        "const at = allowed.indexOf(item); positions.push(at);",
        "if (allowed.lastIndexOf(item) > 4) emit(item);",
        "allowed[item] = item; if (allowed.includes(item)) emit(item);",
    ],
)
def test_mutated_collections_and_positional_results_are_not_set_candidates(body):
    assert _signals(body)["loop_lookup_calls"] == []


@pytest.mark.parametrize(
    "mutation", ["push(item)", "pop()", "splice(0, 1)", "sort()", "reverse()", "fill(item)", "copyWithin(0, 1)"]
)
def test_mutating_receiver_prevents_hoisting(mutation):
    signals = _signals(f"emit(allowed.slice()); allowed.{mutation};")
    assert "allowed.slice" not in signals["loop_invariant_calls"]
    assert "emit" not in signals["loop_invariant_calls"]


def test_stable_receiver_still_has_invariant_signal():
    assert "allowed.slice" in _signals("emit(allowed.slice());")["loop_invariant_calls"]


@pytest.mark.parametrize(
    "expression",
    [
        "allowed.includes(item, 3)",
        "allowed.indexOf(item, 3) !== -1",
        "allowed.lastIndexOf(item, 3) >= 0",
        "getAllowed().includes(item)",
    ],
)
def test_partial_search_and_dynamic_receiver_are_not_whole_collection_lookups(expression):
    assert _signals(f"if ({expression}) emit(item);")["loop_lookup_calls"] == []


def test_mutation_through_this_invalidates_receiver_dependent_calls():
    signals = _signals(
        "if (this.allowed.includes(item)) emit(item); emit(this.allowed.slice()); this.allowed.push(item);"
    )
    assert signals["loop_lookup_calls"] == []
    assert signals["loop_invariant_calls"] == []


def test_stable_this_receiver_remains_a_membership_candidate():
    assert _signals("if (this.allowed.includes(item)) emit(item);")["loop_lookup_calls"]


@pytest.mark.parametrize(
    "body", ["emit(Math.random().toString().slice(2));", "emit(format(Date.now()));", "emit(performance.now());"]
)
def test_volatile_call_results_do_not_make_invariant_receivers_or_arguments(body):
    assert _signals(body)["loop_invariant_calls"] == []


def _detector_db(*, legacy=False, detailed="[]"):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE files (id INTEGER, path TEXT);
        CREATE TABLE symbols (id INTEGER, file_id INTEGER, name TEXT, qualified_name TEXT, kind TEXT, line_start INTEGER);
        INSERT INTO files VALUES (1, 'src/example.js');
        INSERT INTO symbols VALUES (1, 1, 'sample', 'sample', 'function', 1);
    """)
    if legacy:
        conn.execute("CREATE TABLE math_signals (symbol_id INTEGER, loop_depth INTEGER, calls_in_loops TEXT)")
        conn.execute("INSERT INTO math_signals VALUES (1, 1, ?)", (json.dumps(["indexOf"]),))
    else:
        conn.execute(
            "CREATE TABLE math_signals (symbol_id INTEGER, loop_depth INTEGER, calls_in_loops TEXT, loop_lookup_calls TEXT, calls_in_loops_qualified TEXT)"
        )
        conn.execute(
            "INSERT INTO math_signals VALUES (1, 1, ?, ?, ?)",
            (json.dumps(["indexOf"]), detailed, json.dumps(["allowed.indexOf"])),
        )
    return conn


def test_modern_empty_detailed_signal_is_not_overridden_by_legacy_fallback():
    with _detector_db() as conn:
        assert detect_loop_lookup(conn) == []


def test_legacy_index_keeps_low_confidence_fallback():
    with _detector_db(legacy=True) as conn:
        findings = detect_loop_lookup(conn)
        assert len(findings) == 1
        assert findings[0]["confidence"] == "low"


def test_modern_positive_signal_still_yields_finding():
    with _detector_db(detailed=json.dumps(["allowed.indexOf"])) as conn:
        findings = detect_loop_lookup(conn)
        assert len(findings) == 1
        assert findings[0]["detector_version"] == "1.0.1"


@pytest.mark.parametrize(
    "call",
    [
        "Date.now",
        "performance.now",
        "Math.random",
        "crypto.randomUUID",
        "crypto.getRandomValues",
        "process.hrtime",
        "process.hrtime.bigint",
    ],
)
def test_known_js_clock_and_random_calls_are_not_hoisting_candidates(project_factory, monkeypatch, call):
    from roam.db.connection import open_db

    project = project_factory(
        {"src/sample.js": f"function sample(items) {{ for (const item of items) {{ emit({call}()); }} }}"}
    )
    monkeypatch.chdir(project)
    with open_db(readonly=True, project_root=project) as conn:
        assert detect_loop_invariant_call(conn) == []


def test_unknown_callee_requires_purity_review(project_factory, monkeypatch):
    from roam.db.connection import open_db

    project = project_factory(
        {
            "src/sample.js": "function sample(items) { for (const item of items) { const x = loadConfig(); emit(item, x); } }"
        }
    )
    monkeypatch.chdir(project)
    with open_db(readonly=True, project_root=project) as conn:
        findings = detect_loop_invariant_call(conn)
        assert len(findings) == 1
        assert "purity" in findings[0]["reason"]
        assert findings[0]["detector_version"] == "2.0.1"


def test_builtin_detector_metadata_uses_the_canonical_finding_versions():
    from roam.catalog import detectors
    from roam.catalog.versions import detector_version

    entries = [
        entry for entry in detectors._DETECTOR_REGISTRY.values() if entry["function"].__module__ == detectors.__name__
    ]
    assert entries
    for entry in entries:
        assert entry["version"] == detector_version(entry["task_id"]), entry["task_id"]


def test_explicit_external_detector_version_is_preserved(monkeypatch):
    from roam.catalog import detectors

    monkeypatch.setattr(detectors, "_DETECTOR_REGISTRY", {})

    @detectors.algorithm_detector(task_id="custom-task", version="3.2.1")
    def custom(conn):
        return []

    assert detectors.list_registered_detectors()[0]["version"] == "3.2.1"
