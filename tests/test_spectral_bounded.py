"""Tests for the bounded-variant spectral envelope.

Closes the Rank-19 finding: ``cmd_spectral`` used to print a vague
"Graph too large" line.  The bounded variant emits a structured envelope
with ``state: "graph_too_large_for_spectral_dense"``, ``partial_success:
true``, and a copy-paste-executable ``Run roam clusters or roam
partition`` verdict — without adding scipy as a dependency.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest
from click.testing import CliRunner

from roam.commands.cmd_spectral import _MAX_GRAPH_SYMBOLS, spectral
from tests.conftest import git_init, index_in_process


def _bulk_insert_symbols(db_path: Path, n_symbols: int) -> None:
    """Inflate the indexed DB with ``n_symbols`` synthetic function rows.

    The spectral bailout fires off ``SELECT COUNT(*) FROM symbols`` so we
    only need rows in that table to trigger it — no edges or metrics
    required.
    """
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("INSERT OR IGNORE INTO files (id, path) VALUES (7777, 'src/synth_spec.py')")
        rows = [
            (
                700000 + i,
                7777,
                f"node_{i:06d}",
                f"node_{i:06d}",
                "function",
                "",
                10 + i,
                10 + i + 5,
            )
            for i in range(n_symbols)
        ]
        conn.executemany(
            "INSERT OR IGNORE INTO symbols "
            "(id, file_id, name, qualified_name, kind, signature, line_start, line_end) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def small_spectral_project(tmp_path, monkeypatch):
    """A small indexed project — well under the dense-spectral threshold."""
    proj = tmp_path / "spec_small"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "a.py").write_text(
        "class Alpha:\n"
        "    def step1(self):\n"
        "        return Beta().step2()\n"
        "\n"
        "class Beta:\n"
        "    def step2(self):\n"
        "        return 1\n"
    )
    (proj / "b.py").write_text("class Gamma:\n    def run(self):\n        return 2\n")
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"index failed: {out}"
    return proj


@pytest.fixture
def large_spectral_project(tmp_path, monkeypatch):
    """An indexed project blown up beyond the dense-spectral threshold."""
    proj = tmp_path / "spec_large"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "seed.py").write_text("def seed():\n    return 1\n")
    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"index failed: {out}"
    db_path = proj / ".roam" / "index.db"
    # Push past the 5000-symbol bailout threshold.
    _bulk_insert_symbols(db_path, _MAX_GRAPH_SYMBOLS + 500)
    return proj


def _invoke_spectral(args, cwd, json_mode=True):
    runner = CliRunner()
    old_cwd = os.getcwd()
    try:
        os.chdir(str(cwd))
        return runner.invoke(spectral, args, obj={"json": json_mode, "budget": 0}, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)


class TestSpectralBounded:
    """Happy path on small graphs + structured envelope on large graphs."""

    def test_spectral_handles_small_graph(self, small_spectral_project):
        """Under the threshold spectral runs to completion (no bailout)."""
        result = _invoke_spectral([], small_spectral_project, json_mode=True)
        assert result.exit_code == 0, f"exit {result.exit_code}: {result.output[:400]}"
        data = json.loads(result.output)
        assert data["command"] == "spectral"
        summary = data["summary"]
        # Must NOT be the bounded-bailout envelope.
        assert summary.get("state") != "graph_too_large_for_spectral_dense"
        # Should have a real verdict from verdict_from_gap().
        assert summary["verdict"] in {
            "Well-modularized",
            "Moderately modular",
            "Poorly modularized",
        }
        assert "spectral_gap" in summary

    def test_spectral_emits_scoped_envelope_on_large_graph(self, large_spectral_project):
        """Beyond the threshold the command must emit a structured
        envelope naming the sparse alternatives (clusters / partition)
        rather than the old free-text bailout."""
        result = _invoke_spectral([], large_spectral_project, json_mode=True)
        assert result.exit_code == 0, f"exit {result.exit_code}: {result.output[:400]}"
        data = json.loads(result.output)
        assert data["command"] == "spectral"
        summary = data["summary"]
        assert summary.get("state") == "graph_too_large_for_spectral_dense"
        assert summary.get("partial_success") is True
        # Verdict must be a literally executable command per CONSTRAINT 12.
        assert "roam clusters" in summary["verdict"] or "roam partition" in summary["verdict"]
        suggested = summary.get("suggested_commands") or []
        assert "clusters" in suggested or "partition" in suggested
        assert summary.get("symbol_count", 0) > _MAX_GRAPH_SYMBOLS

    def test_spectral_text_output_on_large_graph(self, large_spectral_project):
        """Text-mode VERDICT line must also name the follow-up command."""
        result = _invoke_spectral([], large_spectral_project, json_mode=False)
        assert result.exit_code == 0, f"exit {result.exit_code}: {result.output[:400]}"
        assert result.output.startswith("VERDICT:")
        line = result.output.splitlines()[0]
        assert "roam clusters" in line or "roam partition" in line
