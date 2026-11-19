"""Recency suppression must recognize historical words, not substrings."""

import sqlite3
import time

import pytest

from roam.retrieve.rerank import _recency_boost


@pytest.fixture
def recent_candidate(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    now = 1_800_000_000
    monkeypatch.setattr(time, "time", lambda: now)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        "CREATE TABLE files (id INTEGER, path TEXT);"
        "CREATE TABLE git_commits (id INTEGER, timestamp INTEGER);"
        "CREATE TABLE git_file_changes (file_id INTEGER, commit_id INTEGER);"
        "INSERT INTO files VALUES (1, 'src/watcher.py');"
        "INSERT INTO git_file_changes VALUES (1, 1);"
    )
    conn.execute("INSERT INTO git_commits VALUES (1, ?)", (now - 86400,))
    try:
        yield conn, [{"symbol_id": 7, "file_path": "src/watcher.py"}]
    finally:
        conn.close()


@pytest.mark.parametrize("word", ["folder", "holder", "bold", "cold"])
def test_nonhistorical_words_keep_recency_boost(recent_candidate, word):
    conn, candidates = recent_candidate
    baseline = _recency_boost(conn, candidates, "where is the watcher")
    assert baseline.get(7, 0) > 0
    assert _recency_boost(conn, candidates, f"where is the {word} watcher") == baseline


@pytest.mark.parametrize(
    "query",
    ["where was the old watcher", "where is the LEGACY watcher?", "find deprecated routes"],
)
def test_historical_queries_still_suppress_recency(recent_candidate, query):
    conn, candidates = recent_candidate
    assert _recency_boost(conn, candidates, query) == {}
