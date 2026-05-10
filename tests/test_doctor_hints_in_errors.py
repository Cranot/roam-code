"""Verify that environmental error paths emit a "run roam doctor" hint.

The 17-check ``roam doctor`` is a thorough diagnostic, but new users
don't discover it until they hit a confusing error and dig through
docs. Audit recommendation R4: every error path that could be
environmental (DB locked, ImportError on extras, parser load failure,
permission denied, no git, malformed config) should append a one-liner
hint pointing at ``roam doctor``.

These tests pin the contract: the hint string must appear in the
user-facing message for the four environmental error paths roam
actually raises today.
"""

from __future__ import annotations

import sqlite3
from unittest.mock import patch

import click
import pytest

_HINT = "roam doctor"


def test_db_open_error_includes_doctor_hint(tmp_path):
    """Corrupt-DB error in ``open_db`` is the most-hit env error path —
    must point users at the doctor.
    """
    from roam.db import connection as conn_mod

    # Force ``get_connection`` to raise a DatabaseError so we exercise
    # the user-facing message, not a real corruption.
    def _bad(*args, **kwargs):
        raise sqlite3.DatabaseError("simulated corruption")

    with patch.object(conn_mod, "get_connection", _bad):
        with pytest.raises(click.ClickException) as exc_info:
            with conn_mod.open_db(readonly=True, project_root=tmp_path) as _conn:
                pass
    assert _HINT in exc_info.value.message, (
        f"DB-open error message should mention `roam doctor`, got:\n{exc_info.value.message}"
    )


def test_db_schema_error_includes_doctor_hint(tmp_path):
    """Schema-corruption error in ``ensure_schema`` (write path) — same
    contract as the open path.
    """
    from roam.db import connection as conn_mod

    def _bad_schema(_conn):
        raise sqlite3.DatabaseError("simulated schema corruption")

    with patch.object(conn_mod, "ensure_schema", _bad_schema):
        with pytest.raises(click.ClickException) as exc_info:
            with conn_mod.open_db(readonly=False, project_root=tmp_path) as _conn:
                pass
    assert _HINT in exc_info.value.message


def test_config_parse_error_includes_doctor_hint(tmp_path):
    """Malformed ``roam.config`` triggers a ClickException — must surface
    the doctor hint so users know how to diagnose what's wrong with
    their install / config combo.
    """
    from roam import config as cfg_mod

    # Write a config file with broken TOML at the canonical path.
    proj = tmp_path / "proj"
    (proj / ".roam").mkdir(parents=True)
    (proj / ".roam" / "config.toml").write_text("not = valid [[[ toml", encoding="utf-8")

    with pytest.raises(click.ClickException) as exc_info:
        cfg_mod.load_config(proj)
    assert _HINT in exc_info.value.message


def test_bundle_verify_error_includes_doctor_hint(tmp_path):
    """``roam index-import`` on a corrupt/tampered bundle surfaces a
    ClickException; users should see the doctor hint in case the
    failure is environmental (cosign/extras missing) rather than a
    genuine tampering.
    """
    # Create a dummy "bundle" file that won't parse as a valid tar.
    bogus = tmp_path / "bogus.tar.gz"
    bogus.write_bytes(b"not a real bundle")

    from click.testing import CliRunner

    from roam.cli import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["index-import", str(bogus)], catch_exceptions=False)
    assert result.exit_code != 0
    assert _HINT in result.output, f"index-import on corrupt bundle should mention `roam doctor`, got:\n{result.output}"
