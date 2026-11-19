"""Regression coverage for mutation of the caller's exact-name symbol index."""

from copy import deepcopy

import pytest

from roam.index.relations import resolve_references


def _symbol(sym_id, name):
    return {
        "id": sym_id,
        "name": name,
        "qualified_name": name,
        "kind": "function",
        "file_id": 1,
        "file_path": "example.prg",
        "line_start": sym_id * 10,
        "line_end": sym_id * 10 + 5,
    }


@pytest.mark.parametrize("names", [("foo", "Foo"), ("Foo", "foo")])
def test_resolving_case_variants_preserves_exact_name_index(names, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    symbols = {name: [_symbol(i, name)] for i, name in enumerate(names, start=1)}
    symbols["caller"] = [_symbol(3, "caller")]
    original = deepcopy(symbols)
    # Retain the lists too: replacing a mapping entry must not hide mutation
    # visible to another consumer holding the original list.
    original_lists = dict(symbols)
    references = [
        {
            "source_name": "caller",
            "target_name": "foo",
            "source_file": "example.prg",
            "line": 31,
            "kind": "call",
        }
    ]

    for _ in range(2):
        edges = resolve_references(references, symbols, {"example.prg": 1}, project_root=str(tmp_path))
        assert symbols == original
        assert original_lists == original
        assert [(edge["source_id"], edge["target_id"]) for edge in edges] == [(3, original["foo"][0]["id"])]


@pytest.mark.parametrize("name", ["foo", "Foo"])
def test_case_insensitive_fallback_still_resolves(name, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    symbols = {name: [_symbol(1, name)], "caller": [_symbol(2, "caller")]}
    references = [
        {
            "source_name": "caller",
            "target_name": "FOO",
            "source_file": "example.prg",
            "line": 21,
            "kind": "call",
        }
    ]

    edges = resolve_references(references, symbols, {"example.prg": 1}, project_root=str(tmp_path))

    assert [(edge["source_id"], edge["target_id"]) for edge in edges] == [(2, 1)]
