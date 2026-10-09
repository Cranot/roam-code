"""Completion timing includes the automatic snapshot tail."""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from roam.cli import cli
from roam.commands import cmd_index, metrics_history
from roam.index.indexer import Indexer


@pytest.mark.parametrize("json_mode", [False, True])
def test_completion_includes_snapshot_work(indexed_project, monkeypatch, json_mode):
    monkeypatch.chdir(indexed_project)
    clock = [0.0]
    monkeypatch.setattr(cmd_index.time, "monotonic", lambda: clock[0])

    def no_change_run(self, **kwargs):
        clock[0] += 2.0
        self.summary = {"up_to_date": True}
        return True

    def slow_snapshot(conn, **kwargs):
        clock[0] += 120.0
        return {"health_score": 80, "stored": True}

    monkeypatch.setattr(Indexer, "run", no_change_run)
    monkeypatch.setattr(metrics_history, "append_snapshot", slow_snapshot)
    args = (["--json"] if json_mode else []) + ["index"]
    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 0, result.output
    if json_mode:
        assert json.loads(result.output)["elapsed_s"] == 122.0
    else:
        assert "Completed in 122.0s" in result.output
        assert "snapshot saved" in result.output
