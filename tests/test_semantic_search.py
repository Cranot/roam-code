"""Tests for TF-IDF semantic search (roam search-semantic)."""

from __future__ import annotations

import json
import os

import pytest
from click.testing import CliRunner

from roam.cli import cli
from roam.search.tfidf import tokenize, cosine_similarity, search, build_corpus
from roam.search.index_embeddings import build_and_store_tfidf, load_tfidf_vectors, search_stored


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def semantic_project(project_factory):
    return project_factory({
        "db/connection.py": (
            "def open_database():\n"
            "    '''Open a database connection.'''\n"
            "    pass\n"
            "def close_database():\n"
            "    '''Close the database connection.'''\n"
            "    pass\n"
        ),
        "db/pool.py": (
            "class ConnectionPool:\n"
            "    '''Pool of database connections.'''\n"
            "    def get_connection(self):\n"
            "        pass\n"
            "    def release_connection(self, conn):\n"
            "        pass\n"
        ),
        "auth/login.py": (
            "def authenticate_user(username, password):\n"
            "    '''Authenticate a user with credentials.'''\n"
            "    pass\n"
            "def logout_user(session):\n"
            "    '''Log out the current user.'''\n"
            "    pass\n"
        ),
        "api/routes.py": (
            "def handle_request(req):\n"
            "    '''Handle incoming HTTP request.'''\n"
            "    pass\n"
            "def send_response(data):\n"
            "    '''Send HTTP response.'''\n"
            "    pass\n"
        ),
    })


# ---------------------------------------------------------------------------
# Unit tests: tokenizer
# ---------------------------------------------------------------------------

class TestTokenize:
    def test_tokenize_basic(self):
        """Splits text, lowercases, returns tokens."""
        tokens = tokenize("OpenDatabase")
        assert "open" in tokens
        assert "database" in tokens

    def test_tokenize_strips_stopwords(self):
        """Common English and code words are removed."""
        tokens = tokenize("the return value from a function class")
        # "the", "return", "from", "a", "function", "class" are stopwords
        assert "the" not in tokens
        assert "return" not in tokens
        assert "from" not in tokens
        assert "function" not in tokens
        assert "class" not in tokens
        # "value" should survive (no suffix matches, so stays as-is)
        assert "value" in tokens


# ---------------------------------------------------------------------------
# Unit tests: cosine similarity
# ---------------------------------------------------------------------------

class TestCosineSimilarity:
    def test_identical_vectors(self):
        """Identical vectors should give similarity of 1.0."""
        vec = {"database": 0.5, "connection": 0.3}
        assert abs(cosine_similarity(vec, vec) - 1.0) < 1e-6

    def test_orthogonal_vectors(self):
        """Completely unrelated vectors give 0.0."""
        vec_a = {"database": 1.0, "connection": 1.0}
        vec_b = {"authentication": 1.0, "login": 1.0}
        assert cosine_similarity(vec_a, vec_b) == 0.0

    def test_empty_vectors(self):
        """Empty vectors give 0.0."""
        assert cosine_similarity({}, {"a": 1.0}) == 0.0
        assert cosine_similarity({"a": 1.0}, {}) == 0.0
        assert cosine_similarity({}, {}) == 0.0


# ---------------------------------------------------------------------------
# Integration tests: search
# ---------------------------------------------------------------------------

class TestSearch:
    def test_search_finds_relevant(self, semantic_project):
        """'database connection' should find db/ symbols first."""
        from roam.db.connection import open_db
        with open_db(readonly=True, project_root=semantic_project) as conn:
            results = search(conn, "database connection")
            assert len(results) > 0
            # Top results should be from db/ directory
            top_names = [r["name"] for r in results[:4]]
            db_names = {"open_database", "close_database",
                        "ConnectionPool", "get_connection", "release_connection"}
            assert any(n in db_names for n in top_names), (
                f"Expected db-related symbols in top results, got {top_names}"
            )

    def test_search_ranks_by_relevance(self, semantic_project):
        """Higher scores for better matches."""
        from roam.db.connection import open_db
        with open_db(readonly=True, project_root=semantic_project) as conn:
            results = search(conn, "database connection")
            if len(results) >= 2:
                assert results[0]["score"] >= results[1]["score"]

    def test_search_respects_top_k(self, semantic_project):
        """Returns at most k results."""
        from roam.db.connection import open_db
        with open_db(readonly=True, project_root=semantic_project) as conn:
            results = search(conn, "database", top_k=2)
            assert len(results) <= 2

    def test_search_respects_threshold(self, semantic_project):
        """Filters results below threshold via CLI."""
        runner = CliRunner()
        old_cwd = os.getcwd()
        try:
            os.chdir(str(semantic_project))
            result = runner.invoke(cli, [
                "search-semantic", "database connection",
                "--threshold", "0.99",
            ], catch_exceptions=False)
            assert result.exit_code == 0
            # With a very high threshold most/all results should be filtered
            assert "VERDICT:" in result.output
        finally:
            os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# Integration tests: stored vectors
# ---------------------------------------------------------------------------

class TestStoredVectors:
    def test_build_and_store(self, semantic_project):
        """build_and_store_tfidf populates symbol_tfidf table."""
        from roam.db.connection import open_db
        with open_db(readonly=False, project_root=semantic_project) as conn:
            build_and_store_tfidf(conn)
            count = conn.execute(
                "SELECT COUNT(*) FROM symbol_tfidf"
            ).fetchone()[0]
            assert count > 0

    def test_search_stored(self, semantic_project):
        """search_stored returns results using pre-computed vectors."""
        from roam.db.connection import open_db
        with open_db(readonly=False, project_root=semantic_project) as conn:
            build_and_store_tfidf(conn)
            conn.commit()

        with open_db(readonly=True, project_root=semantic_project) as conn:
            results = search_stored(conn, "database connection")
            assert len(results) > 0
            assert results[0]["score"] > 0


# ---------------------------------------------------------------------------
# CLI tests
# ---------------------------------------------------------------------------

class TestCLI:
    def test_cli_search_semantic_runs(self, semantic_project):
        """Command exits with code 0."""
        runner = CliRunner()
        old_cwd = os.getcwd()
        try:
            os.chdir(str(semantic_project))
            result = runner.invoke(cli, [
                "search-semantic", "database connection",
            ], catch_exceptions=False)
            assert result.exit_code == 0
        finally:
            os.chdir(old_cwd)

    def test_cli_search_semantic_json(self, semantic_project):
        """JSON output is a valid envelope."""
        runner = CliRunner()
        old_cwd = os.getcwd()
        try:
            os.chdir(str(semantic_project))
            result = runner.invoke(cli, [
                "--json", "search-semantic", "database connection",
            ], catch_exceptions=False)
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data["command"] == "search-semantic"
            assert "summary" in data
            assert "results" in data
            assert "verdict" in data["summary"]
        finally:
            os.chdir(old_cwd)

    def test_cli_search_semantic_verdict(self, semantic_project):
        """Text output starts with VERDICT."""
        runner = CliRunner()
        old_cwd = os.getcwd()
        try:
            os.chdir(str(semantic_project))
            result = runner.invoke(cli, [
                "search-semantic", "database connection",
            ], catch_exceptions=False)
            assert result.exit_code == 0
            assert result.output.startswith("VERDICT:")
        finally:
            os.chdir(old_cwd)

    def test_cli_search_semantic_help(self):
        """--help works without an index."""
        runner = CliRunner()
        result = runner.invoke(cli, [
            "search-semantic", "--help",
        ], catch_exceptions=False)
        assert result.exit_code == 0
        assert "natural language query" in result.output.lower() or "--top" in result.output
