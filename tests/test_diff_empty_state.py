"""Tests for ``roam --json diff`` empty-state envelope (Fix A / Pattern 1).

The CLI used to emit empty stdout when there were no uncommitted changes,
which made the MCP wrapper crash with
``Expecting value: line 1 column 1 (char 0)`` on a clean tree. The fix is
to always emit a ``json_envelope("diff", ...)`` with a ``no_changes``
state, even on the empty path.

These tests pin the envelope shape so the regression cannot reappear.
See ``internal/dogfood/SYNTHESIS-2026-05-12.md`` Pattern 1 for context.
"""

from __future__ import annotations

import json as _json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import (  # noqa: E402
    git_commit,
    git_init,
    index_in_process,
    invoke_cli,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    from click.testing import CliRunner

    return CliRunner()


@pytest.fixture
def clean_indexed_project(tmp_path, monkeypatch):
    """Tiny indexed project with a fully committed (clean) working tree."""
    proj = tmp_path / "clean-repo"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "app.py").write_text(
        "def greet(name):\n"
        "    return f'hi {name}'\n"
        "\n"
        "def main():\n"
        "    return greet('world')\n"
    )
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"index failed: {out}"
    return proj


@pytest.fixture
def dirty_indexed_project(clean_indexed_project):
    """Extend ``clean_indexed_project`` with an uncommitted edit on disk."""
    # Edit a source file but DO NOT commit, so `git diff` finds the change.
    (clean_indexed_project / "app.py").write_text(
        "def greet(name):\n"
        "    return f'hello {name}'\n"  # changed greeting
        "\n"
        "def main():\n"
        "    return greet('world')\n"
        "\n"
        "def farewell(name):\n"  # newly-added symbol
        "    return f'bye {name}'\n"
    )
    return clean_indexed_project


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_diff_json_clean_tree_returns_envelope(clean_indexed_project, cli_runner):
    """`roam --json diff` on a clean tree must produce a parseable envelope.

    Pre-fix bug: empty stdout → MCP wrapper crashed on json.loads("").
    Post-fix: structured envelope with verdict=`no changes` /
    state=`no_changes` / partial_success=False.
    """
    result = invoke_cli(cli_runner, ["diff"], json_mode=True)
    assert result.exit_code == 0, f"diff exited {result.exit_code}: {result.output}"

    payload = _json.loads(result.output)
    summary = payload.get("summary") or {}

    assert payload.get("command") == "diff"
    assert summary.get("verdict") == "no changes"
    assert summary.get("state") == "no_changes"
    assert summary.get("partial_success") is False
    # Empty-state defaults must be present so downstream tools never
    # KeyError when they inspect changed/affected counters.
    assert summary.get("changed_files") == 0
    assert summary.get("affected_symbols") == 0
    assert summary.get("affected_files") == 0
    # Top-level data arrays are present (and empty) — no missing fields.
    assert payload.get("per_file") == []
    assert payload.get("blast_radius") == []


def test_diff_json_clean_tree_not_empty_stdout(clean_indexed_project, cli_runner):
    """Explicit assertion: stdout is non-empty and JSON-parseable on clean tree.

    This is the literal regression class — the MCP wrapper used to crash
    because stdout was an empty string. We pin both properties.
    """
    result = invoke_cli(cli_runner, ["diff"], json_mode=True)
    assert result.exit_code == 0
    assert result.output.strip(), "stdout must NOT be empty in --json mode"
    # Must parse as JSON (the original failure mode was JSONDecodeError).
    parsed = _json.loads(result.output)
    assert isinstance(parsed, dict)


def test_diff_json_with_changes_unchanged_behavior(dirty_indexed_project, cli_runner):
    """Happy path: with real uncommitted changes the envelope is unchanged.

    The fix should ONLY add an empty-state branch — the existing
    non-empty path must keep its existing shape.
    """
    result = invoke_cli(cli_runner, ["diff"], json_mode=True)
    assert result.exit_code == 0
    payload = _json.loads(result.output)
    summary = payload.get("summary") or {}

    assert payload.get("command") == "diff"
    # The happy-path verdict is a human sentence about the blast radius —
    # NOT the empty-state literal "no changes".
    verdict = summary.get("verdict", "")
    assert verdict and verdict != "no changes"
    # The pre-existing summary keys must still be present.
    assert "changed_files" in summary
    assert "affected_symbols" in summary
    assert "affected_files" in summary
    # Happy-path must NOT carry the empty-state state field.
    assert summary.get("state") != "no_changes"


def test_diff_json_empty_state_includes_label(clean_indexed_project, cli_runner):
    """Empty-state envelope still carries the diff label (unstaged / staged / range)."""
    result = invoke_cli(cli_runner, ["diff"], json_mode=True)
    assert result.exit_code == 0
    payload = _json.loads(result.output)
    summary = payload.get("summary") or {}
    assert summary.get("label") == "unstaged"


def test_diff_json_empty_state_staged(clean_indexed_project, cli_runner):
    """Empty-state envelope on `--staged` reports label=`staged`."""
    result = invoke_cli(cli_runner, ["diff", "--staged"], json_mode=True)
    assert result.exit_code == 0
    payload = _json.loads(result.output)
    summary = payload.get("summary") or {}
    assert summary.get("state") == "no_changes"
    assert summary.get("label") == "staged"
