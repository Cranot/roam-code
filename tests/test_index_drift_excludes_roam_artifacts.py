"""Roam output stays outside source freshness and ignore rules affect coverage."""

from __future__ import annotations

import json

import pytest

from roam.cli import cli


@pytest.fixture(autouse=True)
def _pin_index_freshness_budget(monkeypatch):
    monkeypatch.setenv("ROAM_INDEX_FRESHNESS_BUDGET_S", "3600")


def test_roam_artifacts_do_not_count_as_source_drift(project_factory, cli_runner, monkeypatch):
    monkeypatch.setenv("ROAM_INDEX_FRESHNESS_BUDGET_S", "30")
    project = project_factory({"app.py": "def helper(): pass\n"})
    monkeypatch.chdir(project)
    (project / ".roamignore").write_text("# project ignore rules\n")
    (project / ".roamignore-findings").write_text("# no excluded findings\n")
    (project / ".roam" / "generated.py").write_text("def generated(): pass\n")

    result = cli_runner.invoke(cli, ["--json", "health"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["_meta"]["index_changed_files"] == 0

    (project / ".roamignore").write_text("app.py\n")
    result = cli_runner.invoke(cli, ["--json", "health"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["_meta"]["index_changed_files"] == 1
