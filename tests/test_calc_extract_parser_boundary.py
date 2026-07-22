"""Regression tests for calc extraction's language-pack boundary."""

from __future__ import annotations

import pytest
import tree_sitter_language_pack as language_pack

from roam.index.calc_extract import extract_calcs


def test_unsupported_grammar_skips_parser_acquisition(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(language_pack, "has_language", lambda _grammar: False)

    def unexpected_parser_acquisition(_grammar: str) -> None:
        raise AssertionError("unsupported grammars must not reach get_parser")

    monkeypatch.setattr(language_pack, "get_parser", unexpected_parser_acquisition)

    assert extract_calcs("no_such_language", b"x = 1 + 2") == []


def test_download_failure_remains_loud(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(language_pack, "has_language", lambda _grammar: True)

    def failed_download(_grammar: str) -> None:
        raise language_pack.DownloadError("parser download unavailable")

    monkeypatch.setattr(language_pack, "get_parser", failed_download)

    with pytest.raises(language_pack.DownloadError, match="download unavailable"):
        extract_calcs("python", b"x = 1 + 2")


def test_language_disappearing_after_probe_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(language_pack, "has_language", lambda _grammar: True)

    def missing_language(_grammar: str) -> None:
        raise language_pack.LanguageNotFoundError("grammar unavailable")

    monkeypatch.setattr(language_pack, "get_parser", missing_language)

    assert extract_calcs("python", b"x = 1 + 2") == []


def test_integrity_failure_remains_loud(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(language_pack, "has_language", lambda _grammar: True)

    def failed_integrity_check(_grammar: str) -> None:
        raise language_pack.ChecksumMismatchError("parser checksum mismatch")

    monkeypatch.setattr(language_pack, "get_parser", failed_integrity_check)

    with pytest.raises(language_pack.ChecksumMismatchError, match="checksum mismatch"):
        extract_calcs("python", b"x = 1 + 2")


def test_source_parse_failure_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(language_pack, "has_language", lambda _grammar: True)

    class InvalidSourceParser:
        def parse(self, _source: bytes) -> None:
            raise ValueError("invalid source")

    monkeypatch.setattr(language_pack, "get_parser", lambda _grammar: InvalidSourceParser())

    assert extract_calcs("python", b"x = 1 + 2") == []
