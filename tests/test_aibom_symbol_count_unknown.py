"""AIBOM symbol binding: an uncomputable count is UNKNOWN, never zero.

``build_aibom_block`` counts the indexed symbols in the files an AI
contributor touched. That count is embedded in the ``aibom`` predicate of a
cosign-signed in-toto CodeGraph-AIBOM statement and in the CycloneDX AIBOM
emitted by ``roam sbom`` — i.e. it is a supply-chain claim about how much of
the codebase an AI authored.

The defect these tests pin: a ``sqlite3.Error`` on the count query (a
concurrent writer holding the lock, a half-built or corrupt index) was
floored to ``0``, which is the SAME value the honest path publishes for "this
contributor touched files containing no indexed symbols". The floored
component kept a non-empty ``files`` list beside its zero — an internally
contradictory claim — and its key set was identical to the honest one, so no
consumer could tell the two apart. The outer disclosure siblings
(``predicate['aibom_error']`` in attest, ``build_aibom_block_failed`` in
sbom) exist but were routed around, because the inner floor guaranteed no
exception ever escaped.
"""

from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path

import pytest

from roam.attest.cga import PREDICATE_TYPE_AIBOM, build_cga_statement, serialize_statement
from roam.security.aibom_extension import aibom_block_incomplete_reason, build_aibom_block

_AIBOM_SQL_MARK = "JOIN files f ON f.id = s.file_id"


class _BusyOnAibomQuery:
    """Live connection whose AIBOM count statement alone hits the write lock.

    Models the real trigger: a concurrent ``roam index`` takes the write lock
    between two reads, so ``build_cga_predicate`` succeeds and only the AIBOM
    count raises SQLITE_BUSY. That timing is exactly what keeps the outer
    ``except Exception`` handler from ever seeing anything.
    """

    def __init__(self, real):
        self._real = real
        self.blocked = 0

    def execute(self, sql, *args, **kwargs):
        if _AIBOM_SQL_MARK in sql:
            self.blocked += 1
            raise sqlite3.OperationalError("database is locked")
        return self._real.execute(sql, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._real, name)


def _git(repo: Path, *args: str, **env_extra: str) -> None:
    env = {
        "GIT_AUTHOR_NAME": "Claude",
        "GIT_AUTHOR_EMAIL": "noreply@anthropic.com",
        "GIT_COMMITTER_NAME": "Claude",
        "GIT_COMMITTER_EMAIL": "noreply@anthropic.com",
        "GIT_CONFIG_GLOBAL": str(repo / ".gitconfig-none"),
        "GIT_CONFIG_SYSTEM": str(repo / ".gitconfig-none"),
        "PATH": __import__("os").environ.get("PATH", ""),
    }
    env.update(env_extra)
    subprocess.run(["git", *args], cwd=repo, env=env, check=True, capture_output=True)


@pytest.fixture
def ai_repo(tmp_path: Path) -> Path:
    """A git repo with one AI-authored commit touching one symbol-bearing file."""
    repo = tmp_path / "ai_repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "d.py").write_text("def touched_by_ai():\n    return 1\n", encoding="utf-8")
    (repo / "notes.md").write_text("# no symbols here\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "feat: add delta")
    return repo


def _index(rows_symbols: bool = True) -> sqlite3.Connection:
    """A real roam index (roam.db.schema) with ``d.py`` holding one symbol."""
    from roam.db.schema import SCHEMA_SQL

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    conn.execute("INSERT INTO files (id, path, language) VALUES (1, 'd.py', 'python')")
    conn.execute("INSERT INTO files (id, path, language) VALUES (2, 'notes.md', 'markdown')")
    if rows_symbols:
        conn.execute(
            "INSERT INTO symbols (id, file_id, name, qualified_name, kind, signature) "
            "VALUES (1, 1, 'touched_by_ai', 'd.touched_by_ai', 'function', '()')"
        )
    conn.commit()
    return conn


@pytest.fixture
def ai_conn() -> sqlite3.Connection:
    return _index()


def _binding(block: dict) -> dict:
    components = block["ai-components"]
    assert components, "fixture must mine at least one AI contributor"
    return components[0]["binding"]


# --------------------------------------------------------------------------
# Positive control — the healthy path still counts, and still says zero when
# zero is the true answer. A guard that only ever reports UNKNOWN fails here.
# --------------------------------------------------------------------------


def test_healthy_conn_counts_symbols_and_reports_complete(ai_repo, ai_conn):
    block = build_aibom_block(ai_repo, ai_conn)
    binding = _binding(block)

    assert binding["symbol_count"] == 1
    assert "d.py" in binding["files"]
    assert block["summary"]["symbol_binding_complete"] is True
    assert block["summary"]["partial_success"] is False
    assert "symbol_binding_errors" not in block
    assert "symbol_count_status" not in binding
    assert aibom_block_incomplete_reason(block) is None


def test_true_zero_is_still_reported_as_zero(ai_repo):
    """A file with no indexed symbols keeps its honest, provable 0."""
    block = build_aibom_block(ai_repo, _index(rows_symbols=False))
    binding = _binding(block)

    assert binding["symbol_count"] == 0
    assert block["summary"]["symbol_binding_complete"] is True
    assert aibom_block_incomplete_reason(block) is None


# --------------------------------------------------------------------------
# The defect.
# --------------------------------------------------------------------------


def test_uncomputable_count_is_unknown_not_zero(ai_repo, ai_conn):
    block = build_aibom_block(ai_repo, _BusyOnAibomQuery(ai_conn))
    binding = _binding(block)

    # The whole point: absent must not resolve to the honest zero.
    assert binding["symbol_count"] is None
    assert binding["symbol_count"] != 0
    assert binding["symbol_count_status"] == "unavailable"
    assert "database is locked" in binding["symbol_count_error"]


def test_uncomputable_count_never_contradicts_its_own_file_list(ai_repo, ai_conn):
    """A binding may not assert 'these files, zero symbols' without disclosure."""
    block = build_aibom_block(ai_repo, _BusyOnAibomQuery(ai_conn))
    binding = _binding(block)

    if binding["files"] and binding.get("symbol_count") == 0:
        pytest.fail("non-empty file list published beside an unverified zero symbol count")


def test_degraded_block_carries_a_disclosure_sibling(ai_repo, ai_conn):
    honest = build_aibom_block(ai_repo, ai_conn)
    degraded = build_aibom_block(ai_repo, _BusyOnAibomQuery(ai_conn))

    assert set(_binding(honest)) != set(_binding(degraded)), (
        "degraded binding is key-identical to the honest one — indistinguishable to a consumer"
    )
    assert degraded["summary"]["partial_success"] is True
    assert degraded["summary"]["symbol_binding_complete"] is False
    assert degraded["symbol_binding_errors"], "the failing component must be named"
    assert degraded["symbol_binding_errors"][0]["email"] == "noreply@anthropic.com"
    assert aibom_block_incomplete_reason(degraded)


def test_summary_only_consumer_can_see_the_gap(ai_repo, ai_conn):
    """A policy engine that reads only ``summary`` still learns it is partial."""
    degraded = build_aibom_block(ai_repo, _BusyOnAibomQuery(ai_conn))
    summary = degraded["summary"]

    assert summary["partial_success"] is True
    assert summary["symbol_binding_complete"] is False


# --------------------------------------------------------------------------
# The sink: the cosign-signed in-toto statement.
# --------------------------------------------------------------------------


def test_signed_statement_discloses_incomplete_aibom(ai_repo, ai_conn):
    real = _BusyOnAibomQuery(ai_conn)
    statement = build_cga_statement(real, project_root=ai_repo, tool_version="test", include_aibom=True)

    assert real.blocked == 1, "fault must land on the AIBOM query, not earlier"
    # The statement is still an AIBOM statement, but it must not be signed as
    # a complete one with no error sibling.
    assert statement["predicateType"] == PREDICATE_TYPE_AIBOM
    assert "aibom_error" in statement["predicate"], (
        "predicate upgraded to CodeGraph-AIBOM while silently understating AI-authored volume"
    )
    signed_bytes = serialize_statement(statement)
    assert "aibom_error" in signed_bytes
    assert '"symbol_count":0' not in signed_bytes


def test_healthy_signed_statement_has_no_error_sibling(ai_repo, ai_conn):
    """Negative control at the sink: a clean build must not be flagged."""
    statement = build_cga_statement(ai_conn, project_root=ai_repo, tool_version="test", include_aibom=True)

    assert statement["predicateType"] == PREDICATE_TYPE_AIBOM
    assert "aibom_error" not in statement["predicate"]
    assert statement["predicate"]["aibom"]["ai-components"][0]["binding"]["symbol_count"] == 1
