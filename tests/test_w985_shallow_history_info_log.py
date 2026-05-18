"""W985: INFO-level log when shallow-history default shadows an empty corpus.

Per W978's BAIL discovery (first-hypothesis-often-wrong rule), the W405
365-day shallow-history default was the canonical cause of empty-corpus
git-log issues — but the production log only said "No git commits found",
which trained readers to trust the wrong hypothesis (truly empty corpus
vs. shallowed-out history).

W985 closes that loop: when ``parse_git_log`` returns an empty list AND a
shallow-history filter was active (env var or default), emit an INFO log
naming the active filter and the opt-out (``ROAM_GIT_SINCE=0``).

Invariants asserted here:

1. Shallow active (env=365d) + empty result -> W985 INFO log emitted, with
   the active window AND the ``ROAM_GIT_SINCE=0`` opt-out hint named.
2. Shallow disabled (env=0) + empty result -> NO W985 INFO log (the corpus
   really is empty; don't train readers to ignore the message).
3. Shallow inactive (since=None returned by ``_resolve_default_since``) +
   empty result -> NO W985 INFO log (no filter to blame).
4. Shallow active + NON-empty result -> NO W985 INFO log (no false
   positives on the hot path).

Scope discipline (per the W985 wave description): INFO log ONLY, no
envelope-warning, no Pattern-2 disclosure, no change to the
``ROAM_GIT_SINCE`` default value, no change to ``parse_git_log``'s return
contract. The W978 BAIL seal + W984 autouse conftest fixture are
preserved.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from unittest import mock

import pytest

from roam.db.connection import ensure_schema
from roam.index import git_stats
from roam.index.git_stats import collect_git_stats

# ---------------------------------------------------------------------------
# The W985 log line must name BOTH the active filter AND the opt-out.
# Asserting both anchors makes the test resilient to copy-edits that keep
# the diagnostic value AND blocks accidental removal of either signal.
# ---------------------------------------------------------------------------
_LOG_TRIGGER_PHRASE = "parse_git_log returned 0 commits"
_LOG_OPT_OUT_PHRASE = "ROAM_GIT_SINCE=0"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _fresh_db(tmp_path: Path) -> sqlite3.Connection:
    """Brand-new SQLite connection with the full roam schema applied."""
    db_path = tmp_path / "w985_test.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    return conn


@pytest.fixture
def fake_git_project(tmp_path, monkeypatch):
    """Project directory that *looks* like a git repo to ``collect_git_stats``.

    We monkeypatch ``_is_git_repo`` rather than running real ``git init`` so
    the test runs identically on any host (no dependency on git config / EOL
    handling / repo-detection edge cases).
    """
    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.setattr(git_stats, "_is_git_repo", lambda _path: True)
    # Also short-circuit the manifest-skip path: pretend the manifest cannot
    # be loaded so ``_head_unchanged_since_last_run`` returns False and we
    # actually exercise the parse_git_log + log-emission path.
    monkeypatch.setattr(git_stats, "_head_unchanged_since_last_run", lambda _conn, _root: False)
    return proj


# ---------------------------------------------------------------------------
# Test 1: shallow active via explicit env value + empty result -> log fires
# ---------------------------------------------------------------------------


def test_shallow_env_set_empty_corpus_emits_info_log(fake_git_project, tmp_path, monkeypatch, caplog):
    """ROAM_GIT_SINCE set to a window + empty parse_git_log -> W985 INFO."""
    conn = _fresh_db(tmp_path)
    monkeypatch.setenv("ROAM_GIT_SINCE", "30d")

    with mock.patch.object(git_stats, "parse_git_log", return_value=[]):
        with caplog.at_level(logging.INFO, logger="roam.index.git_stats"):
            collect_git_stats(conn, fake_git_project)

    w985_records = [rec for rec in caplog.records if _LOG_TRIGGER_PHRASE in rec.getMessage()]
    assert len(w985_records) == 1, (
        f"Expected exactly 1 W985 INFO log when shallow active + empty result; "
        f"got {len(w985_records)} (all messages: "
        f"{[rec.getMessage() for rec in caplog.records]})"
    )

    msg = w985_records[0].getMessage()
    assert "30d" in msg, f"Expected active window '30d' in W985 log; got: {msg!r}"
    assert _LOG_OPT_OUT_PHRASE in msg, f"Expected opt-out hint '{_LOG_OPT_OUT_PHRASE}' in W985 log; got: {msg!r}"
    assert w985_records[0].levelno == logging.INFO, "W985 must be INFO-level — not a Pattern-2 envelope warning"


# ---------------------------------------------------------------------------
# Test 2: shallow disabled (env=0) + empty result -> NO W985 log
# ---------------------------------------------------------------------------


def test_shallow_env_disabled_empty_corpus_no_w985_log(fake_git_project, tmp_path, monkeypatch, caplog):
    """ROAM_GIT_SINCE=0 + empty result -> truly empty corpus; suppress W985."""
    conn = _fresh_db(tmp_path)
    monkeypatch.setenv("ROAM_GIT_SINCE", "0")

    with mock.patch.object(git_stats, "parse_git_log", return_value=[]):
        with caplog.at_level(logging.INFO, logger="roam.index.git_stats"):
            collect_git_stats(conn, fake_git_project)

    w985_records = [rec for rec in caplog.records if _LOG_TRIGGER_PHRASE in rec.getMessage()]
    assert w985_records == [], (
        f"W985 log must NOT fire when shallow truncation is explicitly "
        f"disabled; got {[rec.getMessage() for rec in w985_records]}"
    )

    # Sanity: the legacy "No git commits found" path should still emit.
    legacy_records = [rec for rec in caplog.records if "No git commits found" in rec.getMessage()]
    assert legacy_records, "Expected legacy 'No git commits found' INFO on truly-empty path"


# ---------------------------------------------------------------------------
# Test 3: shallow inactive (resolved since=None) + empty result -> NO W985 log
# ---------------------------------------------------------------------------


def test_shallow_resolved_to_none_empty_corpus_no_w985_log(fake_git_project, tmp_path, monkeypatch, caplog):
    """``_resolve_default_since`` returning None -> no filter to blame.

    Simulates the "warm index with shallow disabled" branch (which would
    happen if a user permanently opts out via env and re-runs against an
    already-populated DB). When ``since`` is None at the call site the
    W985 log must stay silent.
    """
    conn = _fresh_db(tmp_path)
    # Pretend the env var was unset AND the resolver decided no shallow
    # applies — direct stub of the resolver gives a deterministic state
    # without depending on _first_index / manifest interactions.
    monkeypatch.delenv("ROAM_GIT_SINCE", raising=False)
    monkeypatch.setattr(git_stats, "_resolve_default_since", lambda _conn: None)

    with mock.patch.object(git_stats, "parse_git_log", return_value=[]):
        with caplog.at_level(logging.INFO, logger="roam.index.git_stats"):
            collect_git_stats(conn, fake_git_project)

    w985_records = [rec for rec in caplog.records if _LOG_TRIGGER_PHRASE in rec.getMessage()]
    assert w985_records == [], (
        f"W985 log must NOT fire when no shallow filter was active; got {[rec.getMessage() for rec in w985_records]}"
    )


# ---------------------------------------------------------------------------
# Test 4: shallow active + NON-empty result -> NO W985 log (no false positives)
# ---------------------------------------------------------------------------


def test_shallow_active_non_empty_result_no_w985_log(fake_git_project, tmp_path, monkeypatch, caplog):
    """Non-empty parse_git_log result must NOT trip the W985 INFO log.

    Guards against the lazy-log anti-pattern (CP46: loud fixes over silent
    fallbacks): we only want this signal on the empty-corpus path, never
    on the hot path of a healthy index.
    """
    conn = _fresh_db(tmp_path)
    monkeypatch.setenv("ROAM_GIT_SINCE", "365d")

    fake_commits = [
        {
            "hash": "abc" * 13 + "x",
            "author": "Test",
            "timestamp": 1700000000,
            "message": "feat: thing",
            "files": [],
        }
    ]

    # Stub out the heavy downstream passes — they require populated files
    # tables we don't have. We're only asserting the log behaviour here.
    with (
        mock.patch.object(git_stats, "parse_git_log", return_value=fake_commits),
        mock.patch.object(git_stats, "store_commits"),
        mock.patch.object(git_stats, "compute_cochange"),
        mock.patch.object(git_stats, "compute_file_stats"),
        mock.patch.object(git_stats, "compute_complexity"),
    ):
        with caplog.at_level(logging.INFO, logger="roam.index.git_stats"):
            collect_git_stats(conn, fake_git_project)

    w985_records = [rec for rec in caplog.records if _LOG_TRIGGER_PHRASE in rec.getMessage()]
    assert w985_records == [], (
        f"W985 log must NOT fire when parse_git_log returned commits; got {[rec.getMessage() for rec in w985_records]}"
    )


# ---------------------------------------------------------------------------
# Test 5: env-unset first-index default ("365d") names the *default* in the log
# ---------------------------------------------------------------------------


def test_shallow_default_first_index_logs_default_window(fake_git_project, tmp_path, monkeypatch, caplog):
    """When env is unset and the resolver returns the W405 365d default,
    the W985 log must name the resolved window (so readers can copy-paste
    the right value into ``ROAM_GIT_SINCE=0`` advice without guessing)."""
    conn = _fresh_db(tmp_path)
    monkeypatch.delenv("ROAM_GIT_SINCE", raising=False)

    with mock.patch.object(git_stats, "parse_git_log", return_value=[]):
        with caplog.at_level(logging.INFO, logger="roam.index.git_stats"):
            collect_git_stats(conn, fake_git_project)

    w985_records = [rec for rec in caplog.records if _LOG_TRIGGER_PHRASE in rec.getMessage()]
    assert len(w985_records) == 1, (
        f"Expected exactly 1 W985 INFO log on first-index default + empty corpus; got {len(w985_records)}"
    )
    msg = w985_records[0].getMessage()
    # The resolver returns ``_DEFAULT_SINCE`` ("365d") when env is unset on a
    # fresh DB. The log surfaces the *raw* default token so the operator can
    # copy it back into ``ROAM_GIT_SINCE`` if they want to extend it.
    assert "365d" in msg, f"Expected default window '365d' in W985 log; got: {msg!r}"
    assert _LOG_OPT_OUT_PHRASE in msg, f"Expected opt-out hint '{_LOG_OPT_OUT_PHRASE}' in W985 log; got: {msg!r}"
