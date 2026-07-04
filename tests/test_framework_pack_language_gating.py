"""Framework packs must be gated to the repo's actual languages.

Regression for the cross-language contamination bug: a TypeScript/Bun repo's
semantic search returned Python ``@pack/django`` / ``@pack/pytest`` symbols
(because ``search_stored(packs=None)`` searched every pack), which then leaked
into the compiler envelope's ``named_paths``. Packs are now gated by the
languages present in the index.
"""

from __future__ import annotations

import sqlite3

from roam.search import index_embeddings as ie
from roam.search.framework_packs import (
    available_packs,
    packs_for_languages,
)


def test_typescript_excludes_python_packs():
    eligible = set(packs_for_languages({"typescript"}))
    assert "react" in eligible
    assert "express" in eligible
    assert "django" not in eligible
    assert "pytest" not in eligible
    assert "flask" not in eligible
    assert "sqlalchemy" not in eligible


def test_python_excludes_js_packs():
    eligible = set(packs_for_languages({"python"}))
    assert "django" in eligible
    assert "pytest" in eligible
    assert "react" not in eligible
    assert "express" not in eligible


def test_unknown_languages_returns_all_packs():
    # Empty/unknown -> conservative legacy behaviour (all packs eligible).
    assert set(packs_for_languages(set())) == set(available_packs())
    assert set(packs_for_languages(None)) == set(available_packs())


def test_unmatched_language_returns_empty():
    # A language with no pack (e.g. Go) yields no eligible packs.
    assert packs_for_languages({"go"}) == []


def _conn_with_languages(langs: list[str]) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE files (id INTEGER PRIMARY KEY, language TEXT)")
    conn.executemany("INSERT INTO files (language) VALUES (?)", [(lang,) for lang in langs])
    conn.commit()
    return conn


def test_repo_languages_reads_distinct():
    conn = _conn_with_languages(["typescript", "typescript", "json"])
    assert ie._repo_languages(conn) == {"typescript", "json"}


def test_search_stored_gates_packs_to_repo_language(monkeypatch):
    captured: dict = {}

    def _fake_pack(query, top_k=10, packs=None):
        captured["packs"] = packs
        return []

    monkeypatch.setattr(ie, "search_pack_symbols", _fake_pack)

    conn = _conn_with_languages(["typescript"])
    ie.search_stored(conn, "test coverage", ie.SearchOptions(include_packs=True, packs=None))

    assert captured, "pack search should have run for a known language"
    eff = set(captured["packs"])
    assert "django" not in eff and "pytest" not in eff
    assert "react" in eff and "express" in eff


def test_search_stored_skips_packs_when_no_eligible(monkeypatch):
    called = {"n": 0}

    def _fake_pack(query, top_k=10, packs=None):
        called["n"] += 1
        return []

    monkeypatch.setattr(ie, "search_pack_symbols", _fake_pack)

    conn = _conn_with_languages(["go"])
    ie.search_stored(conn, "anything", ie.SearchOptions(include_packs=True, packs=None))

    assert called["n"] == 0, "no language-appropriate packs -> pack search skipped"


def test_search_stored_respects_explicit_packs(monkeypatch):
    captured: dict = {}

    def _fake_pack(query, top_k=10, packs=None):
        captured["packs"] = packs
        return []

    monkeypatch.setattr(ie, "search_pack_symbols", _fake_pack)

    conn = _conn_with_languages(["typescript"])
    # An explicit packs list must NOT be overridden by language gating.
    ie.search_stored(conn, "q", ie.SearchOptions(include_packs=True, packs=["django"]))

    assert captured["packs"] == ["django"]
