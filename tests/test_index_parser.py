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

    monkeypatch.setattr(parser_mod, "has_language", lambda _grammar: False)

    def unexpected_parser_acquisition(_grammar: str) -> None:
        raise AssertionError("unsupported grammars must not reach get_parser")

    monkeypatch.setattr(parser_mod, "get_parser", unexpected_parser_acquisition)

    assert parser_mod.parse_file(source, "python") == (None, None, None)
    assert parser_mod.parse_errors["no_grammar"] == before + 1


def test_parse_file_parser_factory_failure_propagates(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "sample.py"
    source.write_text("def f():\n    return 1\n", encoding="utf-8")

    monkeypatch.setattr(parser_mod, "has_language", lambda _grammar: True)

    def broken_parser_factory(_grammar: str) -> None:
        raise RuntimeError("parser factory crashed")

    monkeypatch.setattr(parser_mod, "get_parser", broken_parser_factory)

    with pytest.raises(RuntimeError, match="parser factory crashed"):
        parser_mod.parse_file(source, "python")


def test_parse_file_download_failure_propagates(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from tree_sitter_language_pack import DownloadError

    source = tmp_path / "sample.py"
    source.write_text("def f():\n    return 1\n", encoding="utf-8")
    monkeypatch.setattr(parser_mod, "has_language", lambda _grammar: True)

    def failed_download(_grammar: str) -> None:
        raise DownloadError("parser download unavailable")

    monkeypatch.setattr(parser_mod, "get_parser", failed_download)

    with pytest.raises(DownloadError, match="download unavailable"):
        parser_mod.parse_file(source, "python")
