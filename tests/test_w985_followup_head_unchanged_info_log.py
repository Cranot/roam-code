"""W985-followup: INFO-level log when HEAD-unchanged short-circuits git stats.

Same diagnosis-shadowing shape as W985's shallow-history filter: the existing
"skipping git stats pass" message was technically correct but did not name
the previous index's HEAD nor the ``--force`` opt-out. An operator running
``roam health`` (or any consumer that expects fresh git-derived metrics)
got a silent "nothing to refresh" branch and had to cross-reference the
manifest table to confirm the skip was legitimate vs. evidence of a stale
index.

W985-followup closes that loop: when ``_head_unchanged_since_last_run``
returns True, the INFO log MUST name BOTH the truncated recorded HEAD and
the ``--force`` opt-out.

Invariants asserted here:

1. HEAD-unchanged path -> W985-followup INFO log fires, naming the recorded
   7-char SHA AND the ``--force`` hint.
2. HEAD-changed path -> NO W985-followup log (real work being done).
3. First-ever index (no recorded HEAD) -> NO W985-followup log (the skip
   branch never trips; the manifest-read in ``_head_unchanged_since_last_run``
   returns None first).
4. W985-followup is INFO-level, not a Pattern-2 envelope warning (no
   ``partial_success`` flip).
5. Helper-level: ``_recorded_head_for_log`` returns ``"unknown"`` when the
   manifest is missing / empty / lacks a ``git_head``, never raises.

Scope discipline (per the wave description): INFO log ONLY, no
envelope-warning, no Pattern-2 disclosure, no change to ``--force`` flag
behaviour, no change to the unchanged-detection logic, no new logger
namespace. The W985 shallow-history log on the same logger
(``roam.index.git_stats``) stays intact and is not triggered by this path
(``parse_git_log`` is not called when the HEAD-unchanged skip fires).
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from unittest import mock

import pytest

from roam.db.connection import ensure_schema
from roam.index import git_stats
from roam.index.git_stats import (
    _recorded_head_for_log,
    collect_git_stats,
)

# ---------------------------------------------------------------------------
# Two anchors the W985-followup log MUST carry. Asserting both makes the
# test resilient to copy-edits that preserve the diagnostic value AND
# blocks accidental removal of either signal.
# ---------------------------------------------------------------------------
_LOG_TRIGGER_PHRASE = "HEAD unchanged since last index"
_LOG_FORCE_HINT = "--force"


def _fresh_db(tmp_path: Path) -> sqlite3.Connection:
    """Brand-new SQLite connection with the full roam schema applied."""
    db_path = tmp_path / "w985_followup_test.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    return conn


@pytest.fixture
def fake_git_project(tmp_path, monkeypatch):
    """Project dir that ``collect_git_stats`` will treat as a real git repo.

    We monkeypatch ``_is_git_repo`` rather than running real ``git init`` so
    the test runs identically on any host (no dependency on git config / EOL
    handling / repo-detection edge cases).
    """
    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.setattr(git_stats, "_is_git_repo", lambda _path: True)
    return proj


# ---------------------------------------------------------------------------
# Test 1: HEAD unchanged -> W985-followup INFO log fires (with sha + hint).
# ---------------------------------------------------------------------------


def test_head_unchanged_emits_info_log_with_sha_and_force_hint(fake_git_project, tmp_path, monkeypatch, caplog):
    """The skip-branch must surface BOTH the recorded SHA AND the --force hint."""
    conn = _fresh_db(tmp_path)
    full_sha = "abcdef0123456789012345678901234567890abc"
    expected_short = full_sha[:7]  # "abcdef0"

    # Pretend the unchanged-detection returned True (its internals are tested
    # elsewhere; we only need the consume-site behaviour here).
    monkeypatch.setattr(git_stats, "_head_unchanged_since_last_run", lambda _conn, _root: True)
    # Pretend the manifest table records that exact HEAD.
    monkeypatch.setattr(
        "roam.index.manifest.latest_manifest",
        lambda _conn: {"git_head": full_sha},
    )

    with caplog.at_level(logging.INFO, logger="roam.index.git_stats"):
        collect_git_stats(conn, fake_git_project)

    followup_records = [rec for rec in caplog.records if _LOG_TRIGGER_PHRASE in rec.getMessage()]
    assert len(followup_records) == 1, (
        f"Expected exactly 1 W985-followup INFO log when HEAD unchanged; "
        f"got {len(followup_records)} (all messages: "
        f"{[rec.getMessage() for rec in caplog.records]})"
    )

    msg = followup_records[0].getMessage()
    assert expected_short in msg, f"Expected 7-char recorded SHA {expected_short!r} in W985-followup log; got: {msg!r}"
    assert _LOG_FORCE_HINT in msg, f"Expected --force opt-out hint in W985-followup log; got: {msg!r}"
    assert followup_records[0].levelno == logging.INFO, (
        "W985-followup must be INFO-level — not a Pattern-2 envelope warning"
    )


# ---------------------------------------------------------------------------
# Test 2: HEAD changed -> NO W985-followup log (real work running).
# ---------------------------------------------------------------------------


def test_head_changed_no_followup_log(fake_git_project, tmp_path, monkeypatch, caplog):
    """When the unchanged check returns False, the followup log MUST stay silent."""
    conn = _fresh_db(tmp_path)
    monkeypatch.setattr(git_stats, "_head_unchanged_since_last_run", lambda _conn, _root: False)

    # Stub the downstream passes so the test doesn't depend on git/sqlite state.
    with mock.patch.object(git_stats, "parse_git_log", return_value=[]):
        with caplog.at_level(logging.INFO, logger="roam.index.git_stats"):
            collect_git_stats(conn, fake_git_project)

    followup_records = [rec for rec in caplog.records if _LOG_TRIGGER_PHRASE in rec.getMessage()]
    assert followup_records == [], (
        f"W985-followup log must NOT fire on the HEAD-changed path; got "
        f"{[rec.getMessage() for rec in followup_records]}"
    )


# ---------------------------------------------------------------------------
# Test 3: First-ever index -> NO followup log (skip-branch never trips).
# ---------------------------------------------------------------------------


def test_first_ever_index_no_followup_log(fake_git_project, tmp_path, monkeypatch, caplog):
    """No recorded manifest -> ``_head_unchanged_since_last_run`` returns False
    inside its real implementation, so the skip-branch (and the followup log)
    never trips. We exercise the real helper here to lock that invariant in."""
    conn = _fresh_db(tmp_path)
    # Real helper, but force the manifest read to return None (first-ever run).
    monkeypatch.setattr(
        "roam.index.manifest.latest_manifest",
        lambda _conn: None,
    )

    with mock.patch.object(git_stats, "parse_git_log", return_value=[]):
        with caplog.at_level(logging.INFO, logger="roam.index.git_stats"):
            collect_git_stats(conn, fake_git_project)

    followup_records = [rec for rec in caplog.records if _LOG_TRIGGER_PHRASE in rec.getMessage()]
    assert followup_records == [], (
        f"W985-followup log must NOT fire on first-ever index (no recorded "
        f"HEAD); got {[rec.getMessage() for rec in followup_records]}"
    )


# ---------------------------------------------------------------------------
# Test 4: helper-level — _recorded_head_for_log defensive paths.
# ---------------------------------------------------------------------------


def test_recorded_head_for_log_handles_missing_manifest(tmp_path, monkeypatch):
    """``_recorded_head_for_log`` returns ``"unknown"``, never raises, on missing manifest."""
    conn = _fresh_db(tmp_path)
    monkeypatch.setattr("roam.index.manifest.latest_manifest", lambda _conn: None)
    assert _recorded_head_for_log(conn) == "unknown"


def test_recorded_head_for_log_handles_empty_git_head(tmp_path, monkeypatch):
    """Manifest exists but ``git_head`` is empty -> returns ``"unknown"``."""
    conn = _fresh_db(tmp_path)
    monkeypatch.setattr(
        "roam.index.manifest.latest_manifest",
        lambda _conn: {"git_head": ""},
    )
    assert _recorded_head_for_log(conn) == "unknown"


def test_recorded_head_for_log_truncates_to_seven_chars(tmp_path, monkeypatch):
    """Full SHA in manifest -> returns the 7-char prefix."""
    conn = _fresh_db(tmp_path)
    full = "0123456789abcdef0123456789abcdef01234567"
    monkeypatch.setattr(
        "roam.index.manifest.latest_manifest",
        lambda _conn: {"git_head": full},
    )
    assert _recorded_head_for_log(conn) == "0123456"


def test_recorded_head_for_log_swallows_exceptions(tmp_path, monkeypatch):
    """``latest_manifest`` raising -> ``"unknown"``, no propagation.

    Diagnostic logs MUST NOT crash the indexer. This pins the defensive
    discipline so future refactors don't accidentally let a manifest-read
    failure escape this helper.
    """
    conn = _fresh_db(tmp_path)

    def _boom(_conn):
        raise RuntimeError("manifest table corrupted")

    monkeypatch.setattr("roam.index.manifest.latest_manifest", _boom)
    assert _recorded_head_for_log(conn) == "unknown"


# ---------------------------------------------------------------------------
# Test 5: W985 shallow-history log is not triggered on the skip path.
# ---------------------------------------------------------------------------


def test_skip_branch_does_not_trip_w985_shallow_log(fake_git_project, tmp_path, monkeypatch, caplog):
    """Cross-check: the followup INFO and the W985 shallow log are siblings on
    the same logger but live on disjoint branches. When the HEAD-unchanged
    skip fires, ``parse_git_log`` is never called, so the W985 trigger
    phrase (``"parse_git_log returned 0 commits"``) must stay absent. Guards
    against accidental log-line duplication if the skip branch is ever
    relocated below the parse call.
    """
    conn = _fresh_db(tmp_path)
    monkeypatch.setattr(git_stats, "_head_unchanged_since_last_run", lambda _conn, _root: True)
    monkeypatch.setattr(
        "roam.index.manifest.latest_manifest",
        lambda _conn: {"git_head": "f" * 40},
    )

    with caplog.at_level(logging.INFO, logger="roam.index.git_stats"):
        collect_git_stats(conn, fake_git_project)

    w985_shallow_records = [rec for rec in caplog.records if "parse_git_log returned 0 commits" in rec.getMessage()]
    assert w985_shallow_records == [], (
        f"The HEAD-unchanged skip path must not emit the W985 shallow-history "
        f"log; got {[rec.getMessage() for rec in w985_shallow_records]}"
    )
