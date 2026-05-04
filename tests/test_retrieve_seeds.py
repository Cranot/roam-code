"""Tests for A.0.3 — seed inference for `roam retrieve`.

Two test surfaces:

* :class:`TestExtractTokens` — pure-Python token extraction (no DB).
* :class:`TestInferSeedsIntegration` — end-to-end against an indexed
  fixture project so the FTS5 path is exercised.
"""

from __future__ import annotations

import os

import pytest
from click.testing import CliRunner

from roam.cli import cli
from roam.retrieve.seeds import extract_tokens, infer_seeds
from tests.conftest import make_src_project as _make_project

# ---------------------------------------------------------------------------
# Token extraction (offline)
# ---------------------------------------------------------------------------


class TestExtractTokens:
    def test_empty_query_returns_empty(self):
        assert extract_tokens("") == []
        assert extract_tokens("   ") == []

    def test_pascal_case(self):
        tokens = extract_tokens("is it safe to delete UserSession?")
        assert "UserSession" in tokens

    def test_camel_case(self):
        tokens = extract_tokens("trace getUserById")
        assert "getUserById" in tokens

    def test_snake_case_multiword(self):
        tokens = extract_tokens("look at handle_http_request please")
        assert "handle_http_request" in tokens

    def test_snake_extractor_requires_underscore(self):
        """The snake_case extractor (`_SNAKE_RE`) only matches multi-word
        identifiers (with at least one underscore). Bare words go through
        the DOG.7 NL fallback instead — see TestExtractTokens.dog7 below.
        """
        from roam.retrieve.seeds import _SNAKE_RE

        # `_SNAKE_RE` must NOT match a single word. The fallback may, but
        # that's a different code path tested separately.
        assert not _SNAKE_RE.findall("session please")
        assert _SNAKE_RE.findall("user_session please") == ["user_session"]

    def test_dotted_path(self):
        tokens = extract_tokens("inspect user.session.token")
        assert "user.session.token" in tokens

    def test_file_path(self):
        tokens = extract_tokens("the bug is in api/handler.py around line 50")
        assert "api/handler.py" in tokens

    def test_file_path_with_dotted_extension_not_double_counted(self):
        tokens = extract_tokens("frontend/component.tsx broke")
        # File pattern wins; dotted-attribute pattern shouldn't add a duplicate.
        assert "frontend/component.tsx" in tokens

    def test_initialisms_dropped(self):
        tokens = extract_tokens("the API URL ID is wrong")
        assert "API" not in tokens
        assert "URL" not in tokens
        assert "ID" not in tokens

    def test_stopwords_dropped(self):
        tokens = extract_tokens("the and for from")
        assert tokens == []

    def test_short_tokens_dropped(self):
        tokens = extract_tokens("ab cd ef")
        assert tokens == []

    def test_dedupes(self):
        tokens = extract_tokens("UserSession UserSession UserSession")
        assert tokens.count("UserSession") == 1

    def test_mixed_query(self):
        q = "trace UserSession.refresh through handle_login and check api/auth.py for race conditions"
        tokens = extract_tokens(q)
        # We expect at minimum these strong-shape tokens
        for expected in ("UserSession.refresh", "handle_login", "api/auth.py"):
            assert expected in tokens, f"missing {expected} in {tokens}"

    # DOG.7 — natural-language fallback

    def test_natural_language_query_falls_back_to_lowercase_nouns(self):
        """Pure NL query yields lowercase-noun seeds when no identifiers present."""
        tokens = extract_tokens("where does critique decide finding severity")
        # 'critique', 'finding', 'severity' are real domain words ≥5 chars.
        # Stopwords (where, decide) must be filtered.
        assert "critique" in tokens
        assert "finding" in tokens
        assert "severity" in tokens
        for noise in ("where", "decide"):
            assert noise not in tokens, f"{noise} should be filtered"

    def test_lowercase_supplement_runs_alongside_strong_tokens(self):
        """Lowercase domain nouns supplement (not replace) identifier-shaped tokens.

        R.1 (2026-05-01): the original DOG.7 contract suppressed
        lowercase nouns whenever any PascalCase/snake/dotted token was
        present. The 30-task self-bench showed this discarding
        informative domain words ("language", "extractor") and tanking
        recall — e.g. "Ruby Tier 1 language extractor" extracted only
        ``[Ruby, Tier]`` and lost to ``TestDecayTier`` rows under BM25.
        Both classes of tokens now coexist.
        """
        tokens = extract_tokens("where is UserSession used in critique")
        assert "UserSession" in tokens
        assert "critique" in tokens, "lowercase supplement must run alongside strong tokens"

    def test_short_lowercase_words_dropped(self):
        """Non-domain 4-letter words never enter via the NL fallback.

        v12.12.6 added a curated 4-letter domain-noun pass (``file``,
        ``code``, ``dead``, etc.), so this test now picks 4-letter
        noise words that are deliberately *not* in the allow-list.
        """
        tokens = extract_tokens("look back some each more thing find")
        for noise in ("look", "back", "some", "each", "more", "thing", "find"):
            assert noise not in tokens, f"non-domain word leaked through: {noise!r}"

    def test_curated_four_letter_domain_nouns_extracted(self):
        """v12.12.6 — curated 4-letter programming-domain nouns ARE
        captured even though they're below the lowercase-noun fallback
        floor of 5 characters. Without these, queries like 'where is
        dead code detection' or 'find file role classifier' returned
        only the 5+ char words and missed the actual answer."""
        tokens = extract_tokens("where is dead code detection")
        assert "dead" in tokens
        assert "code" in tokens
        tokens = extract_tokens("find file role classifier")
        assert "file" in tokens
        assert "role" in tokens

    def test_extended_stopwords_filtered(self):
        """Extended NL stopwords are dropped from the fallback path."""
        tokens = extract_tokens("where would the things actually decide between modules")
        # All should be filtered (stopwords + NL_EXTRA + len<5)
        assert tokens == [] or all(
            t.lower() not in {"where", "would", "things", "actually", "decide", "between"} for t in tokens
        )


# ---------------------------------------------------------------------------
# End-to-end integration with a real indexed project
# ---------------------------------------------------------------------------


class TestInferSeedsIntegration:
    @pytest.fixture
    def indexed_project(self, tmp_path):
        proj = _make_project(
            tmp_path,
            {
                "auth.py": """
                class UserSession:
                    def refresh(self):
                        return self.token

                    def revoke(self):
                        return None

                def handle_login(user):
                    return UserSession()
                """,
                "billing.py": """
                class Invoice:
                    def total(self):
                        return self.amount

                def calculate_tax(invoice):
                    return invoice.total() * 0.07
                """,
            },
        )
        old_cwd = os.getcwd()
        try:
            os.chdir(str(proj))
            runner = CliRunner()
            result = runner.invoke(cli, ["index"])
            assert result.exit_code == 0, result.output
            yield proj
        finally:
            os.chdir(old_cwd)

    def _open(self):
        from roam.db.connection import open_db

        return open_db(readonly=True)

    def test_pascal_token_finds_class(self, indexed_project):
        with self._open() as conn:
            seeds = infer_seeds(conn, "is it safe to delete UserSession?")
        assert seeds, "expected at least one seed"
        # The class is in auth.py — at least one seed must come from there.
        with self._open() as conn:
            files = conn.execute(
                "SELECT f.path FROM symbols s JOIN files f ON s.file_id = f.id "
                f"WHERE s.id IN ({','.join(str(k) for k in seeds.keys())})"
            ).fetchall()
        paths = {row["path"] for row in files}
        assert any("auth" in p for p in paths), f"no auth.py seed in {paths}"

    def test_snake_token_finds_function(self, indexed_project):
        with self._open() as conn:
            seeds = infer_seeds(conn, "trace handle_login flow please")
        assert seeds
        with self._open() as conn:
            names = conn.execute(
                f"SELECT name FROM symbols WHERE id IN ({','.join(str(k) for k in seeds.keys())})"
            ).fetchall()
        all_names = {row["name"] for row in names}
        assert "handle_login" in all_names, f"missing in {all_names}"

    def test_file_path_finds_symbols_in_file(self, indexed_project):
        with self._open() as conn:
            seeds = infer_seeds(conn, "the bug is in src/auth.py")
        # At least one seed should resolve to a symbol in auth.py
        with self._open() as conn:
            paths = conn.execute(
                "SELECT f.path FROM symbols s JOIN files f ON s.file_id = f.id "
                f"WHERE s.id IN ({','.join(str(k) for k in seeds.keys())})"
            ).fetchall()
        assert any("auth" in row["path"] for row in paths), seeds

    def test_unknown_tokens_return_empty(self, indexed_project):
        with self._open() as conn:
            seeds = infer_seeds(conn, "ZzZz_no_such_thing AnotherMissing")
        assert seeds == {}

    def test_pure_english_returns_empty(self, indexed_project):
        with self._open() as conn:
            seeds = infer_seeds(conn, "is it safe to delete the thing")
        assert seeds == {}

    def test_max_seeds_caps_result(self, indexed_project):
        """If many tokens match, max_seeds caps the dict size."""
        # Use a query whose tokens individually match multiple symbols.
        with self._open() as conn:
            seeds = infer_seeds(
                conn,
                "UserSession Invoice calculate_tax handle_login refresh revoke",
                max_seeds=2,
            )
        assert len(seeds) <= 2

    def test_empty_query_returns_empty(self, indexed_project):
        with self._open() as conn:
            assert infer_seeds(conn, "") == {}
            assert infer_seeds(conn, "   ") == {}

    def test_max_seeds_zero_returns_empty(self, indexed_project):
        with self._open() as conn:
            assert infer_seeds(conn, "UserSession", max_seeds=0) == {}

    def test_weights_are_positive_floats(self, indexed_project):
        with self._open() as conn:
            seeds = infer_seeds(conn, "UserSession refresh")
        for sym_id, weight in seeds.items():
            assert isinstance(sym_id, int)
            assert isinstance(weight, float)
            assert weight > 0

    def test_max_seeds_one_returns_at_most_one(self, indexed_project):
        with self._open() as conn:
            seeds = infer_seeds(conn, "UserSession Invoice handle_login refresh", max_seeds=1)
        assert len(seeds) <= 1

    def test_dog7_natural_language_query_resolves(self, indexed_project):
        """DOG.7: a pure-NL query with no identifier-shaped tokens still
        resolves seeds via the lowercase-noun fallback.
        """
        with self._open() as conn:
            seeds = infer_seeds(conn, "where does the session refresh and revoke happen")
        assert seeds, "expected NL fallback to surface seeds"
