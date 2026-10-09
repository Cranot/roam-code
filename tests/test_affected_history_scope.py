"""Committed-history comparisons disclose that working changes are excluded."""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from roam.cli import cli


@pytest.mark.parametrize("json_mode", [False, True])
def test_base_head_discloses_scope_on_dirty_tree(indexed_project, monkeypatch, json_mode):
    monkeypatch.chdir(indexed_project)
    target = indexed_project / "src" / "models.py"
    target.write_text(target.read_text() + "\ndef pending_fix():\n    return 1\n")
    args = (["--json"] if json_mode else []) + ["affected", "--base", "HEAD"]
    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 0, result.output
    verdict = json.loads(result.output)["summary"]["verdict"] if json_mode else result.output
    assert "committed" in verdict.lower()
    assert "--changed" in verdict
    working = CliRunner().invoke(cli, ["--json", "affected", "--changed"])
    assert working.exit_code == 0, working.output
    assert "src/models.py" in json.loads(working.output)["changed_files"]
