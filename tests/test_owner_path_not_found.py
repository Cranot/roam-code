"""W362 — ``roam owner <nonexistent> [--json]`` emits a structured envelope.

The Pattern-1 family audit at
``(internal memo)`` (and W315 codification in
CLAUDE.md) classified the prior ``SystemExit(1)`` + bare ``Path not found``
behaviour as a "vacuous error emit" adjacent to Variant B of Pattern-1.
Pattern-2 always-emit discipline says a "no matches" outcome is a valid
analytical result, not a failure.

This module pins:

* ``--json`` mode emits a full envelope with
  ``summary.state == "path_not_found"`` and exits 0.
* Text mode prints a verdict (not a one-line error) and exits 0.
* A real, indexed path still produces ownership output unchanged.
* The unknown-path verdict remains LAW 4 concrete-noun-anchored (drift
  guard).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))


@pytest.fixture
def owner_project(tmp_path, monkeypatch):
    """A tiny indexed git repo so ``roam owner`` has files to query."""
    from roam.index.indexer import Indexer

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "hello.py").write_text("def hello():\n    return 'hi'\n")
    (tmp_path / "README.md").write_text("# demo\n")

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "add", "."],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init"],
        cwd=tmp_path,
        check=True,
    )

    monkeypatch.chdir(tmp_path)
    Indexer().run(quiet=True)
    return tmp_path


class TestOwnerUnknownPathEmitsStructuredEnvelope:
    """W362: ``roam owner /nonexistent --json`` must emit an envelope."""

    def test_owner_unknown_path_emits_structured_envelope(self, owner_project):
        from roam.cli import cli

        runner = CliRunner()
        res = runner.invoke(cli, ["--json", "owner", "does/not/exist.py"])
        # Exit 0 — "checked, no match" is not a failure (LAW 11).
        assert res.exit_code == 0, res.output
        payload = json.loads(res.output)
        assert payload["command"] == "owner"
        assert payload["state"] == "path_not_found"
        assert payload["target_path"] == "does/not/exist.py"
        assert payload["authors"] == []
        assert payload["file_count"] == 0

        summary = payload["summary"]
        assert summary["state"] == "path_not_found"
        assert summary["partial_success"] is False
        assert summary["target_path"] == "does/not/exist.py"
        assert "does/not/exist.py" in summary["verdict"]

    def test_owner_unknown_path_text_mode_exits_zero(self, owner_project):
        from roam.cli import cli

        runner = CliRunner()
        res = runner.invoke(cli, ["owner", "does/not/exist.py"])
        # Exit 0 even in text mode — "no matches" is not a failure.
        assert res.exit_code == 0, res.output
        # Verdict prints to stdout (not stderr) so agents capturing
        # combined streams still see it. Must NOT be the legacy
        # "Path not found in index" error one-liner.
        assert "VERDICT:" in res.output
        assert "does/not/exist.py" in res.output
        # Helpful next-step hint (CLAUDE.md follow-up command guidance).
        assert "roam search" in res.output

    def test_owner_unknown_path_with_backslashes_normalised(self, owner_project):
        """Windows-style backslashes should still produce a structured
        envelope (the CLI normalises them before lookup, so the
        ``target_path`` echo is the forward-slash form)."""
        from roam.cli import cli

        runner = CliRunner()
        res = runner.invoke(cli, ["--json", "owner", "does\\not\\exist.py"])
        assert res.exit_code == 0, res.output
        payload = json.loads(res.output)
        assert payload["state"] == "path_not_found"
        assert payload["target_path"] == "does/not/exist.py"

    def test_owner_existing_path_unchanged(self, owner_project):
        """Sanity: a real indexed path still resolves and emits a
        non-empty envelope (verdict shape unchanged from pre-W362)."""
        from roam.cli import cli

        runner = CliRunner()
        res = runner.invoke(cli, ["--json", "owner", "src/hello.py"])
        assert res.exit_code == 0, res.output
        payload = json.loads(res.output)
        # No path_not_found state on the happy path.
        assert payload.get("state") != "path_not_found"
        assert payload["summary"].get("state") != "path_not_found"
        # The verdict mentions "top owner" / contributors per the
        # existing envelope shape.
        assert "top owner" in payload["summary"]["verdict"]

    def test_owner_verdict_uses_concrete_noun_terminal(self, owner_project):
        """LAW 4 drift guard: the W362 unknown-path verdict must remain
        concrete-noun-anchored (passes runtime LAW 4 evaluation).

        The verdict is a long sentence (>4 tokens) with a non-numeric
        lead ("No"), which is one of the five anchoring rules the LAW 4
        lint accepts. This test pins that the rule still holds — if the
        verdict ever gets shortened, the lint regresses.
        """
        from test_law4_lint import _is_concrete_anchored

        from roam.cli import cli

        runner = CliRunner()
        res = runner.invoke(cli, ["--json", "owner", "does/not/exist.py"])
        assert res.exit_code == 0, res.output
        payload = json.loads(res.output)
        verdict = payload["summary"]["verdict"]
        assert _is_concrete_anchored(verdict), (
            f"W362 unknown-path verdict regressed LAW 4: {verdict!r}"
        )
