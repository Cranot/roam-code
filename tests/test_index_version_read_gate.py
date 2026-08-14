"""Task #147 — readonly opens compare the index's stamped schema version.

The index stamps versions at build time (``ensure_schema`` bumps ``PRAGMA
user_version``; the manifest records ``roam_version`` / ``schema_version``)
but NO read path ever compared them — a stale index built by an older roam
was consumed silently. Origin incident: a 13.10-built index consumed under
roam 14 produced wrong verify-imports results with zero staleness
disclosure. The only version comparisons that existed were non-gating
(doctor WARN, index-bundle stderr warning, attest declarative stale_if).

The fix gates at the single chokepoint every requires_index command reads
through: the readonly branch of ``open_db`` (which deliberately skips
``ensure_schema``). Three-valued outcome:

* stamped == ``USER_VERSION``  -> proceed (one PRAGMA read of overhead)
* stamped <  ``USER_VERSION``  -> typed refusal naming ``roam index --force``
  (an unstamped DB, ``user_version=0``, is UNKNOWN and refuses too — an
  absent measurement is never a benign default)
* stamped >  ``USER_VERSION``  -> typed refusal: a downgraded client cannot
  understand the schema

Write-mode opens stay exempt: ``ensure_schema`` migrates + re-stamps there,
and ``roam index --force`` unlinks the DB before rebuilding, so remediation
can never be blocked by the gate it remediates.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from roam.db.connection import USER_VERSION, open_db
from roam.exit_codes import EXIT_ERROR, IndexVersionError


def _project_with_stamp(tmp_path: Path, stamp: int | None) -> Path:
    """A git-rooted project whose ``.roam/index.db`` carries *stamp*.

    ``stamp=None`` leaves the DB unstamped (``PRAGMA user_version`` reads 0),
    which is what every hand-crafted / foreign / pre-stamping DB looks like.
    """
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, capture_output=True)
    roam_dir = tmp_path / ".roam"
    roam_dir.mkdir()
    conn = sqlite3.connect(str(roam_dir / "index.db"))
    conn.execute("CREATE TABLE files (id INTEGER PRIMARY KEY, path TEXT)")
    if stamp is not None:
        conn.execute(f"PRAGMA user_version = {int(stamp)}")
    conn.commit()
    conn.close()
    return tmp_path


# ---------------------------------------------------------------------------
# (1) Older stamp — refuse, name the remediation, carry the version pair
# ---------------------------------------------------------------------------


def test_older_stamp_refuses_with_remediation(tmp_path, monkeypatch):
    proj = _project_with_stamp(tmp_path, USER_VERSION - 1)
    monkeypatch.chdir(proj)

    with pytest.raises(IndexVersionError) as excinfo:
        with open_db(readonly=True):
            pass

    err = excinfo.value
    msg = err.format_message()
    assert "roam index --force" in msg, msg
    assert "index is stale" in msg.lower(), msg
    # The version pair is in the message AND as typed attributes.
    assert f"v{USER_VERSION - 1}" in msg, msg
    assert f"v{USER_VERSION}" in msg, msg
    assert err.found == USER_VERSION - 1
    assert err.expected == USER_VERSION
    assert err.exit_code == EXIT_ERROR


# ---------------------------------------------------------------------------
# (2) Newer stamp — a downgraded client refuses outright
# ---------------------------------------------------------------------------


def test_newer_stamp_refuses_as_downgraded_client(tmp_path, monkeypatch):
    proj = _project_with_stamp(tmp_path, USER_VERSION + 1)
    monkeypatch.chdir(proj)

    with pytest.raises(IndexVersionError) as excinfo:
        with open_db(readonly=True):
            pass

    err = excinfo.value
    msg = err.format_message()
    assert "newer" in msg.lower(), msg
    assert "downgraded" in msg.lower(), msg
    assert "cannot understand" in msg.lower(), msg
    assert f"v{USER_VERSION + 1}" in msg, msg
    assert f"v{USER_VERSION}" in msg, msg
    assert err.found == USER_VERSION + 1
    assert err.expected == USER_VERSION


# ---------------------------------------------------------------------------
# (3) Current stamp — proceeds; the gate costs one PRAGMA read, nothing else
# ---------------------------------------------------------------------------


def test_current_stamp_proceeds(tmp_path, monkeypatch):
    proj = _project_with_stamp(tmp_path, USER_VERSION)
    monkeypatch.chdir(proj)

    with open_db(readonly=True) as conn:
        row = conn.execute("SELECT COUNT(*) FROM files").fetchone()
    assert row[0] == 0


# ---------------------------------------------------------------------------
# (4) Unstamped (user_version=0) — UNKNOWN is a refusal, not a benign default
# ---------------------------------------------------------------------------


def test_unstamped_db_refuses(tmp_path, monkeypatch):
    proj = _project_with_stamp(tmp_path, None)
    monkeypatch.chdir(proj)

    with pytest.raises(IndexVersionError) as excinfo:
        with open_db(readonly=True):
            pass

    err = excinfo.value
    msg = err.format_message()
    assert "user_version=0" in msg, msg
    assert "roam index --force" in msg, msg
    assert err.found == 0
    assert err.expected == USER_VERSION


# ---------------------------------------------------------------------------
# (5) Write-mode opens are exempt — migration/rebuild remediation stays open
# ---------------------------------------------------------------------------


def test_write_mode_migrates_and_restamps_then_readonly_passes(tmp_path, monkeypatch):
    from roam.db.connection import ensure_schema

    # A real (current-schema) index whose stamp says "older roam built me" —
    # the shape of the origin incident, minus the schema drift itself.
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, capture_output=True)
    roam_dir = tmp_path / ".roam"
    roam_dir.mkdir()
    conn = sqlite3.connect(str(roam_dir / "index.db"))
    ensure_schema(conn)
    conn.execute(f"PRAGMA user_version = {USER_VERSION - 1}")
    conn.commit()
    conn.close()
    monkeypatch.chdir(tmp_path)

    # Sanity: the readonly gate refuses this DB.
    with pytest.raises(IndexVersionError):
        with open_db(readonly=True):
            pass

    # Write-mode open runs ensure_schema: migrates + re-stamps. The gate
    # must NOT block this path or `roam index` could never fix the index.
    with open_db(readonly=False) as conn:
        found = conn.execute("PRAGMA user_version").fetchone()[0]
        assert found == USER_VERSION

    # After remediation the readonly gate is satisfied.
    with open_db(readonly=True) as conn:
        conn.execute("SELECT 1").fetchone()


# ---------------------------------------------------------------------------
# (6) CLI-level disclosure — a requires_index command refuses loudly
# ---------------------------------------------------------------------------


def test_cli_command_refuses_with_disclosed_error(tmp_path, monkeypatch):
    """The refusal is a disclosed, typed error through the real CLI — never a
    silent fallthrough (wrong-but-plausible output) or a bare traceback."""
    from roam.cli import cli

    proj = _project_with_stamp(tmp_path, USER_VERSION - 1)
    runner = CliRunner()
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        result = runner.invoke(cli, ["cycles"], catch_exceptions=False)
    finally:
        os.chdir(old_cwd)

    assert result.exit_code == EXIT_ERROR, result.output
    assert "index is stale" in result.output.lower(), result.output
    assert "roam index --force" in result.output, result.output
    # Version pair disclosed to the operator.
    assert f"v{USER_VERSION - 1}" in result.output, result.output
    assert f"v{USER_VERSION}" in result.output, result.output


# ---------------------------------------------------------------------------
# (7) MCP classification — the stderr path reads the refusal as INDEX_STALE
# ---------------------------------------------------------------------------


def test_mcp_classifies_stale_refusal_as_retryable_index_stale(tmp_path, monkeypatch):
    """Exit 1 is deliberately NOT exit 4 (the guard family's needs_review) —
    the MCP layer classifies this refusal from the message text instead,
    which is the documented structured/stderr channel for a schema bump."""
    from roam.mcp_server import _classify_error

    proj = _project_with_stamp(tmp_path, USER_VERSION - 1)
    monkeypatch.chdir(proj)

    with pytest.raises(IndexVersionError) as excinfo:
        with open_db(readonly=True):
            pass

    code, hint, retryable = _classify_error(excinfo.value.format_message(), EXIT_ERROR)
    assert code == "INDEX_STALE"
    assert "roam index" in hint
    assert retryable is True
