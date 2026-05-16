"""Tests for ``roam hover`` — single-line architectural summary."""

from __future__ import annotations

import json
import subprocess

import pytest
from click.testing import CliRunner


@pytest.fixture
def hover_project(tmp_path, monkeypatch):
    """A tiny indexed project with a clear caller → callee chain."""
    from roam.index.indexer import Indexer

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "lib.py").write_text(
        "def low_level():\n"
        "    return 42\n"
        "\n"
        "def mid_level():\n"
        "    return low_level() + 1\n"
        "\n"
        "def top_level():\n"
        "    return mid_level() + 2\n"
    )

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init"],
        cwd=tmp_path,
        check=True,
    )

    monkeypatch.chdir(tmp_path)
    Indexer().run(quiet=True)
    return tmp_path


class TestHover:
    def test_text_output(self, hover_project):
        from roam.cli import cli

        runner = CliRunner()
        res = runner.invoke(cli, ["hover", "mid_level"])
        assert res.exit_code == 0, res.output
        # Verdict line: kind + name + location
        assert "mid_level" in res.output
        assert "src/lib.py" in res.output or "src\\lib.py" in res.output
        # Blast radius line
        assert "blast radius" in res.output

    def test_top_caller_and_callee(self, hover_project):
        from roam.cli import cli

        runner = CliRunner()
        res = runner.invoke(cli, ["hover", "mid_level"])
        assert res.exit_code == 0, res.output
        # mid_level is called by top_level (caller) and calls low_level (callee)
        assert "top_level" in res.output
        assert "low_level" in res.output

    def test_json_envelope(self, hover_project):
        from roam.cli import cli

        runner = CliRunner()
        res = runner.invoke(cli, ["--json", "hover", "low_level"])
        assert res.exit_code == 0, res.output
        payload = json.loads(res.output)
        assert payload["command"] == "hover"
        s = payload["summary"]
        assert s["qualified_name"] == "low_level"
        assert s["blast_bucket"] in {"none", "small", "moderate", "large"}
        assert "in_degree" in s
        assert "out_degree" in s

    def test_unknown_symbol_exits_zero_with_unresolved_disclosure(self, hover_project):
        """W1272 — Pattern-2c Convention (c): unresolved exits 0 with a
        resolution=unresolved disclosure (was: exit non-zero, pre-W1272).

        cmd_hover's text-mode unresolved path still emits the FTS
        suggestion list so the human caller sees what they probably meant,
        but the exit code stays 0 so CI gating distinguishes a typo
        (recoverable) from a tool/IO failure (non-recoverable).
        """
        from roam.cli import cli

        runner = CliRunner()
        res = runner.invoke(cli, ["hover", "definitely_not_a_symbol"])
        assert res.exit_code == 0, res.output
        assert "not found" in res.output.lower()
