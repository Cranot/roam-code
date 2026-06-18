from __future__ import annotations

from pathlib import Path

import pytest

from roam.index import parser as parser_mod


def test_parse_file_missing_grammar_is_expected_skip(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "sample.py"
    source.write_text("def f():\n    return 1\n", encoding="utf-8")
    before = parser_mod.parse_errors["no_grammar"]

    def missing_grammar(_grammar: str) -> None:
        raise LookupError("missing grammar")

    monkeypatch.setattr(parser_mod, "get_parser", missing_grammar)

    assert parser_mod.parse_file(source, "python") == (None, None, None)
    assert parser_mod.parse_errors["no_grammar"] == before + 1


def test_parse_file_parser_factory_failure_propagates(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "sample.py"
    source.write_text("def f():\n    return 1\n", encoding="utf-8")

    def broken_parser_factory(_grammar: str) -> None:
        raise RuntimeError("parser factory crashed")

    monkeypatch.setattr(parser_mod, "get_parser", broken_parser_factory)

    with pytest.raises(RuntimeError, match="parser factory crashed"):
        parser_mod.parse_file(source, "python")
